"""Moving G0 is a separate offline candidate, never covered by feed-only scope."""
import asyncio
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from check_controlled_capture import audit
from reviewed_job_file import load
from test_reviewed_file_usb import simulated_usb_file
from test_xy_linear import XYController
import test_controlled_tcp as fixtures

ROOT=Path(__file__).resolve().parents[1]
RAPID=b'M5\nS0\nG17 G21 G90 G94 G54\nG0 X10 Y390\nM4 S0\nG1 X20 Y390 F60\nG0 X0 Y400\nM5\nS0\nM2\n'
MIXED=RAPID.replace(b'G1 X20 Y390 F60',b'G3 X20 Y390 I5 J0 F60')
SETTINGS=b'$110=18000.000\n$111=18000.000\n$120=250.000\n$121=250.000\n'


def review(raw):
    with tempfile.TemporaryDirectory() as d:
        p=Path(d)/'job.gc';p.write_bytes(raw)
        return load(p,profile='xy-job-zero')


class RapidController(XYController):
    rapid_settings=SETTINGS
    async def write(self,wire):
        reply=await super().write(wire)
        return reply[:-3]+self.rapid_settings+b'ok\n' if wire==b'$$\n' else reply


async def simulated_rapid_ble(profile,controller_class=RapidController, *, fail=False):
    case=fixtures.ControlTCPTests()
    async def scenario(reader,writer,controller,clients,rows):
        if not fail:await case.wait_event(rows,'pipeline_tcp_ready')
        writer.write(b''.join(profile.wires));await writer.drain()
        if not fail:
            await case.wait_event(rows,'controlled_job_completed');writer.write_eof();await writer.drain()
        await asyncio.wait_for(reader.read(),3)
    device,client,rows,bridge=await case.exercise(scenario,reviewed_file=profile,
        controller_class=controller_class,gatt_limit=17,defer_m2=False)
    rows.append(dict(event='pipeline_server_stopped',cleanup_unknown=bridge.cleanup_unknown,
                     failure_reason=bridge.failure_reason))
    for row in rows:row['simulated']=True
    return device,client,rows,bridge


class RapidReviewTests(unittest.TestCase):
    def test_moving_rapid_is_separate_and_does_not_claim_F60_limit_or_nominal_time(self):
        profile=review(RAPID)
        distance=math.hypot(10,10)+math.hypot(20,10)
        self.assertEqual(profile.rapid_segments,2)
        self.assertAlmostEqual(float(profile.rapid_distance_mm),distance)
        allowance=distance+2*math.sqrt(math.hypot(10,10)/250)+2*math.sqrt(math.hypot(20,10)/250)+10
        self.assertAlmostEqual(float(profile.nominal_seconds),allowance)
        summary=profile.summary()
        self.assertIsNone(summary['feed_ceiling_mm_min']);self.assertIsNone(summary['nominal_motion_seconds'])
        self.assertTrue(summary['rapid_actual_speed_is_not_limited_by_F60'])
        self.assertEqual(profile.position_bounds,((0,20),(390,400),(0,0),(0,0)))
        for old in ('x-linear-zero','xy-linear-zero','xy-arc-zero'):
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'job.gc';p.write_bytes(RAPID)
                with self.assertRaises(ValueError):load(p,profile=old)
        only=review(b'M5\nS0\nG0 X1 Y399\nX0 Y400\nM5\nS0\nM2\n')
        self.assertEqual(only.rapid_segments,2)  # Modal G0 without an F word.
        self.assertEqual(len(review(MIXED).arcs),1)

    def test_settings_missing_duplicate_nonfinite_changed_rate_or_acceleration_fail_closed(self):
        profile=review(RAPID);profile.require_rapid_settings(SETTINGS.decode())
        for data in (b'',SETTINGS+SETTINGS,SETTINGS.replace(b'18000',b'9000'),
                     SETTINGS.replace(b'250',b'100'),SETTINGS.replace(b'18000.000',b'nan'),
                     SETTINGS.replace(b'250.000',b'inf'),SETTINGS.replace(b'250.000',b'invalid')):
            with self.subTest(data=data),self.assertRaises(RuntimeError):profile.require_rapid_settings(data.decode())
        for raw in (RAPID.replace(b'X10 Y390',b'X261 Y390'),RAPID.replace(b'M4 S0',b'M4 S1'),
                    RAPID.replace(b'G0 X0 Y400',b'G91 G0 X0 Y400')):
            with self.assertRaises(ValueError):review(raw)

    def test_native_cncjs_line_lengths_vertices_and_mixed_arc_center_match(self):
        node=shutil.which('node')
        if node is None:self.skipTest('Node required for independent geometry oracle')
        with tempfile.TemporaryDirectory() as d:
            for raw in (RAPID,MIXED):
                p=Path(d)/'job.gc';p.write_bytes(raw);profile=review(raw)
                other=json.loads(subprocess.run([node,str(ROOT/'tools/check_xy_geometry_oracle.cjs'),str(p)],
                                 capture_output=True,text=True,check=True).stdout)[0]
                self.assertEqual([s['motion'] for s in other['segments']],['G0','G1','G0'] if raw==RAPID else ['G0','G0'])
                if raw==RAPID:
                    self.assertAlmostEqual(float(profile.distance_mm),other['line_distance_mm'])
                    self.assertEqual([(float(x),float(y)) for x,y in profile.vertices],[(v['x'],v['y']) for v in other['vertices']])
                else:
                    self.assertEqual((other['arcs'][0]['center']['x'],other['arcs'][0]['center']['y']),profile.arcs[0].center)


class RapidRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_usb_ble_rapid_and_mixed_keep_original_bytes_and_independent_audit(self):
        for raw in (RAPID,MIXED,RAPID.replace(b'\n',b'\r\n')):
            profile=review(raw)
            usb,usb_rows,error=await simulated_usb_file(profile,controller_class=RapidController)
            self.assertIsNone(error)
            ready=next(row for row in usb_rows if row['event']=='usb_control_ready')
            self.assertIsNone(ready['feed_mm_min']);self.assertEqual(ready['rapid_settings']['110'],18000)
            ble,client,rows,bridge=await simulated_rapid_ble(profile)
            self.assertIsNone(bridge.failure_reason);self.assertFalse(bridge.cleanup_unknown);self.assertEqual(client.way,0)
            self.assertEqual(usb.vertices,ble.vertices)
            with tempfile.TemporaryDirectory() as d:
                for backend,data in (('usb',usb_rows),('ble',rows)):
                    p=Path(d)/(backend+'.jsonl');p.write_text(''.join(json.dumps(r)+'\n' for r in data))
                    self.assertTrue(audit(p,backend=backend,simulated=True,reviewed_file=profile,expected_controls=())['passed'])

    async def test_bad_live_rapid_settings_block_all_job_submission_in_both_backends(self):
        profile=review(RAPID)
        for bad in (b'',SETTINGS.replace(b'18000',b'9000'),SETTINGS+SETTINGS):
            class BadController(RapidController):rapid_settings=bad
            usb,usb_rows,error=await simulated_usb_file(profile,controller_class=BadController)
            self.assertIsInstance(error,RuntimeError);self.assertEqual(usb.job_count,0);self.assertTrue(usb.closed)
            ble,client,rows,bridge=await simulated_rapid_ble(profile,BadController,fail=True)
            self.assertEqual(ble.job_count,0);self.assertIsNotNone(bridge.failure_reason);self.assertEqual(client.way,0)
            for data in (usb_rows,rows):
                self.assertFalse(any(r['event'] in ('pipeline_job_submitted','controlled_job_completed') for r in data))


class RapidGateTests(unittest.TestCase):
    def test_arc_flag_and_rapid_flag_are_independently_required_before_hardware(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for module in ('serial','bleak'):(root/(module+'.py')).write_text('raise RuntimeError("No hardware import")\n')
            env=dict(os.environ,PYTHONPATH=d)
            for script in ('pipeline_tcp_bridge.py','controlled_usb_probe.py'):
                for raw,extra,missing in ((RAPID,[], '--allow-reviewed-rapid-file'),
                    (MIXED,['--allow-reviewed-arc-file'],'--allow-reviewed-rapid-file'),
                    (MIXED,['--allow-reviewed-rapid-file'],'--allow-reviewed-arc-file')):
                    p=root/'job.gc';p.write_bytes(raw);profile=review(raw);log=root/'not-created.jsonl'
                    base=[sys.executable,str(ROOT/'tools'/script),'--job-file',str(p),'--file-profile','xy-job-zero']
                    result=subprocess.run(base,capture_output=True,text=True,env=env)
                    self.assertEqual(result.returncode,0,result.stderr)
                    result=subprocess.run(base+extra+['--execute','--expected-job-sha256',profile.sha256,
                        '--allow-reviewed-file','--allow-reviewed-xy-file','--allow-reviewed-job-controls',
                        '--laser-power-isolated','--log',str(log)]+(['--allow-initialization'] if script.startswith('pipeline') else []),
                        capture_output=True,text=True,env=env)
                    self.assertEqual(result.returncode,2,result.stderr);self.assertIn(missing,result.stderr)
                    self.assertNotIn('No hardware import',result.stderr);self.assertFalse(log.exists())
