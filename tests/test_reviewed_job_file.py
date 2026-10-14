import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from controlled_grbl_pipeline import ControlledGrblPipeline
from controlled_job_policy import ControlledJobPolicy
from controlled_reviewed_job import ControlledReviewedJob
from reviewed_job_file import load
from reviewed_ten_mm import EXPORT
from test_ten_mm_runtime import Session


class FileTests(unittest.TestCase):
    def review(self,raw,**kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'job.gc';path.write_bytes(raw)
            return load(path,**kwargs)

    def test_existing_native_export_review_retains_inline_bytes_and_omits_full_comments(self):
        result=load(EXPORT)
        self.assertEqual(len(result.commands),14)
        self.assertEqual(str(result.distance_mm),'20')
        self.assertEqual(result.nominal_seconds,20)
        self.assertIn(b';Restore metric mode\n',b''.join(result.wires))
        self.assertNotIn('毫米'.encode(),b''.join(result.wires))
        self.assertFalse(result.summary()['motion_authorized'])
        self.assertEqual(result.sha256,hashlib.sha256(EXPORT.read_bytes()).hexdigest())

    def test_changed_source_rejects_stale_digest_even_for_an_unchanged_path(self):
        raw=b'M5\nS0\nG1 X1 F60\nG1 X0\nM5\nS0\nM2\n'
        result=self.review(raw)
        with self.assertRaises(ValueError):self.review(raw+b';changed\n',expected_sha256=result.sha256)
        self.assertEqual(self.review(raw,expected_sha256=result.sha256),result)

    def test_nonzero_power_motion_modes_axis_feed_and_realtime_in_comments_are_rejected(self):
        for command in (b'M3 S0',b'S1',b'G91',b'G20',b'G2 X1 I1',b'G28',b'$X',
                        b'G1 X11 F60',b'G1 X-1 F60',b'G1 Y399',b'G1 Z0',b'G1 A0',
                        b'G0 X1',b'G1 X1 F0',b'G1 X1 F61',b'G1 X1 X2 F60',
                        b'G0 G1 X1 F60',b'M4 M5 S0',b'G1 X1e3 F60',b'G1 Xnan F60',
                        b'G1 X1 F60 ;!',b'G1 X1 F60 ;?',b'G1 X1 F60 ;~',b'M2',b'G1 X1\x18'):
            raw=b'M5\nS0\n'+command+b'\nG1 X1 F60\nG1 X0\nM5\nS0\nM2\n'
            with self.subTest(command=command),self.assertRaises(ValueError):self.review(raw)

    def test_native_grbl_modal_power_requires_zero_prefix_and_every_power_word_zero(self):
        source=Path(__file__).resolve().parents[1]/'evidence/native-start-20261013/native-reviewed.gc'
        raw=source.read_bytes()
        result=self.review(raw,profile='xy-linear-zero')
        self.assertIn(b'M4',result.commands)
        self.assertEqual(result.distance_mm,40)
        self.assertEqual(result.commands[:2],(b'M5',b'S0'))
        self.assertEqual(b''.join(result.wires),b''.join(line for line in raw.splitlines(keepends=True)
            if line.split(b';',1)[0].strip()))
        for changed in (raw.replace(b'M5\nS0\n',b'M5\n',1),
                        raw.replace(b'M4\n',b'S1\nM4\n'),
                        raw.replace(b'M4\n',b'M4 S1\n'),
                        raw.replace(b'X10S0F60',b'X10S.1F60'),
                        raw.replace(b'M4\n',b'M8\nM4\n')):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                self.review(changed,profile='xy-linear-zero')

    def test_nonzero_endpoint_missing_final_newline_and_missing_safety_suffix_are_rejected(self):
        for raw in (b'M5\nS0\nG1 X1 F60\nM5\nS0\nM2\n',
                    b'M5\nS0\nG1 X1 F60\nG1 X0\nM5\nS0\nM2',
                    b'M5\nS0\nG1 X1 F60\nG1 X0\nM2\n'):
            with self.assertRaises(ValueError):self.review(raw)


class FileCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_varied_file_length_runs_through_same_control_core_and_private_completion(self):
        for segments in (1,80):
            raw=b'M5\nS0\nM4 S0\n'+b'G1 X1 F60\nG1 X0\n'*segments+b'M5\nS0\nM2\n'
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'varied.gc';path.write_bytes(raw);profile=load(path)
            rows,visible=[],bytearray()
            async def deliver(data):visible.extend(data)
            def log(event,**fields):rows.append(dict(event=event,**fields))
            session=Session()
            pipeline=ControlledGrblPipeline(session,deliver,log,rx_budget=1492,
                payload_capacity=17,deliver_lines=True)
            job=ControlledReviewedJob(pipeline,None,log,job_commands=profile.commands)
            pipeline.check_status=job.check_status;job.prepared=True
            parser=ControlledJobPolicy(profile.commands)
            for wire in profile.wires:
                for kind,source in parser.feed(wire):job.enqueue(kind,source)
            parser.eof()
            for _ in range(500):
                await job.advance();await pipeline.step()
                if job.completed:break
            self.assertTrue(job.completed)
            self.assertEqual(job.submitted,len(profile.commands))
            self.assertEqual(visible.splitlines().count(b'ok'),len(profile.commands))
            self.assertNotIn(b'[GC:',visible)
            self.assertEqual(b''.join(bytes.fromhex(r['hex']) for r in rows
                             if r['event']=='pipeline_job_submitted'),b''.join(profile.wires))
            self.assertEqual(session.writes[-5:],[b'?',b'$G\n',b'G0\n',b'$G\n',b'?'])
            with self.assertRaises(ValueError):parser.feed(profile.wires[0])


class PoweredProfileTests(unittest.TestCase):
    """The powered review is the only place a nonzero S can be admitted."""

    BASE=(b'M5\nS0\nG00 G17 G40 G21 G54\nG54\nG90\nM4\nG1 Y390S50F300\n'
          b'M4\nG1 Y400S0F300\nM5\nS0\nM2\n')

    def review(self,raw,**kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'job.gc';path.write_bytes(raw)
            return load(path,**kwargs)

    def powered(self,raw,ceiling=50,feed=2000):
        return self.review(raw,profile='xy-linear-power',power_ceiling=ceiling,feed_ceiling=feed)

    def test_declared_ceilings_are_required_and_bounded(self):
        for kwargs in (dict(),dict(power_ceiling=50),dict(feed_ceiling=2000),
                       dict(power_ceiling=0,feed_ceiling=2000),
                       dict(power_ceiling=1001,feed_ceiling=2000),
                       dict(power_ceiling=50,feed_ceiling=0),
                       dict(power_ceiling=50,feed_ceiling=6001)):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.review(self.BASE,profile='xy-linear-power',**kwargs)

    def test_accepted_file_reports_declared_and_actual_power(self):
        result=self.powered(self.BASE)
        summary=result.summary()
        self.assertEqual(summary['profile'],'powered_marking_xy')
        self.assertTrue(summary['powered'])
        self.assertEqual(summary['power_ceiling'],50)
        self.assertEqual(summary['power'],50)
        self.assertEqual(summary['feed_ceiling_mm_min'],2000)
        self.assertEqual(summary['start'],[0,400,0,0])
        self.assertEqual(summary['end'],[0,400,0,0])
        self.assertFalse(summary['motion_authorized'])
        self.assertEqual(result.distance_mm,20)

    def test_power_feed_and_spindle_boundaries_fail_closed(self):
        cases={
            'above ceiling':self.BASE.replace(b'S50F300',b'S51F300'),
            'missing explicit S':self.BASE.replace(b'G1 Y390S50F300',b'G1 Y390F300'),
            'above feed ceiling':self.BASE.replace(b'F300',b'F2500'),
            'no spindle-on':self.BASE.replace(b'M4\n',b''),
            'powered rapid':self.BASE.replace(b'G1 Y390S50F300',b'G0 Y390S50F300'),
        }
        for label,raw in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    self.powered(raw)

    def test_zero_power_profiles_still_refuse_any_nonzero_power(self):
        for profile in ('xy-linear-zero','xy-arc-zero','xy-job-zero'):
            with self.subTest(profile=profile):
                with self.assertRaises(ValueError):
                    self.review(self.BASE,profile=profile)


class PoweredCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_powered_file_completes_through_the_same_controlled_core(self):
        # The controlled completion checks are labelled "zero power" but really
        # require a final M5/S0 modal, FS 0,0, the X0/Y400 endpoint and a position
        # inside the reviewed envelope. Prove a powered file satisfies them.
        root=Path(__file__).resolve().parents[1]
        path=root/'evidence/machining-phase1-20261013/phase1-single-segment.gc'
        profile=load(path,expected_sha256=
            '092493f87cefdabcceb9412c352f8d64db7535a77a6ab317dc2f3de68a9a2c51',
            profile='xy-linear-power',power_ceiling=50,feed_ceiling=2000)
        rows,visible=[],bytearray()
        async def deliver(data):visible.extend(data)
        def log(event,**fields):rows.append(dict(event=event,**fields))
        session=Session()
        pipeline=ControlledGrblPipeline(session,deliver,log,rx_budget=1492,
            payload_capacity=17,deliver_lines=True)
        job=ControlledReviewedJob(pipeline,None,log,job_commands=profile.commands)
        pipeline.check_status=job.check_status;job.prepared=True
        parser=ControlledJobPolicy(profile.commands)
        for wire in profile.wires:
            for kind,source in parser.feed(wire):job.enqueue(kind,source)
        parser.eof()
        for _ in range(600):
            await job.advance();await pipeline.step()
            if job.completed:break
        self.assertTrue(job.completed)
        self.assertEqual(job.submitted,len(profile.commands))
        submitted=b''.join(bytes.fromhex(r['hex']) for r in rows
                           if r['event']=='pipeline_job_submitted')
        self.assertEqual(submitted,b''.join(profile.wires))
        self.assertTrue(any(r['event']=='controlled_job_endpoint_verified' for r in rows))
        self.assertTrue(any(r['event']=='controlled_job_completed' for r in rows))
        self.assertFalse(any(r['event'] in ('pipeline_stopped','pipeline_session_error','error')
                             for r in rows))

    async def test_reported_power_is_accepted_only_within_the_declared_ceiling(self):
        # Regression: a real Run report with FS:...,50 aborted the first Phase 1
        # attempt because parse_status rejected any nonzero spindle. The offline
        # simulation had missed it by always reporting FS:0,0.
        from motion_parity_probe import parse_status
        report='<Run|MPos:0.000,397.675,0.000,0.000|Bf:98,1494|FS:300,50|Pn:P|Ov:100,100,100>'
        with self.assertRaises(RuntimeError):
            parse_status(report)
        with self.assertRaises(RuntimeError):
            parse_status(report,power_ceiling=49)
        with self.assertRaises(RuntimeError):
            parse_status(report,power_ceiling=0)
        state,position=parse_status(report,power_ceiling=50)
        self.assertEqual(state,'Run')
        self.assertEqual(position,(0.0,397.675,0.0,0.0))
        idle='<Idle|MPos:0.000,400.000,0.000,0.000|Bf:100,1500|FS:0,0|Pn:P|Ov:100,100,100>'
        self.assertEqual(parse_status(idle,power_ceiling=50)[0],'Idle')
