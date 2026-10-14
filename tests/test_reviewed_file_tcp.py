import asyncio
from collections import deque
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from check_controlled_capture import audit
from controlled_reviewed_job import ControlledReviewedJob
from reviewed_job_file import load
from pipeline_tcp_bridge import PipelineTCPBridge
import test_controlled_tcp as fixtures


def raw_file(pairs):
    return b'M5\nS0\nM4 S0\n'+b'G1 X.001 F60\nG1 X0\n'*pairs+b'M5\nS0\nM2\n'


def review_bytes(raw):
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'job.gc';path.write_bytes(raw)
        return load(path)


class FileTCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_uses_real_tcp_framing_private_finish_and_more_than_old_45_second_budget(self):
        profile=review_bytes(b'M5\nS0\nM4 S0\n'+b'G1 X1 F60\nG1 X0\n'*80+b'M5\nS0\nM2\n')
        case=fixtures.ControlTCPTests();clock=types.SimpleNamespace(value=0.)
        async def scenario(reader,writer,controller,clients,rows):
            await case.wait_event(rows,'pipeline_tcp_ready')
            writer.write(b''.join(profile.wires));await writer.drain()
            await case.wait_event(rows,'pipeline_terminal',sequence=len(profile.commands)-1)
            clock.value=100 # Virtual active time, no hardware and no 100-second sleep.
            await asyncio.sleep(.05)
            self.assertFalse(any(r['event']=='pipeline_session_error' for r in rows))
            self.assertFalse(any(r['event']=='controlled_job_completed' for r in rows))
            controller.state,controller.x='Idle',0;clients[0].data(b'ok\n')
            await case.wait_event(rows,'controlled_job_completed')
            writer.write_eof();await writer.drain()
            output=await asyncio.wait_for(reader.read(),3)
            self.assertEqual(output.splitlines().count(b'ok'),166)
        controller,client,rows,bridge=await case.exercise(scenario,reviewed_file=profile,
                                                        bridge_clock=lambda:clock.value)
        rows.append(dict(event='pipeline_server_stopped',cleanup_unknown=bridge.cleanup_unknown))
        for row in rows:row['simulated']=True
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'capture.jsonl'
            def check(data):
                path.write_text(''.join(json.dumps(row)+'\n' for row in data))
                return audit(path,backend='ble',simulated=True,reviewed_file=profile,expected_controls=())
            result=check(rows)
            self.assertEqual(result['complete_job_commands'],166)
            self.assertTrue(result['no_synthetic_ack'])
            for event,field,change in (
                ('pipeline_tcp_delivered','hex',lambda value:value+'6f6b0a'),
                ('pipeline_tcp_ready','reviewed_source_sha256',lambda value:'0'*64),
                ('pipeline_job_submitted','hex',lambda value:b'G1 X9\n'.hex())):
                altered=copy.deepcopy(rows)
                row=next(row for row in altered if row['event']==event)
                row[field]=change(row[field])
                with self.assertRaises(AssertionError):check(altered)
        self.assertEqual(controller.job_count,166)
        self.assertEqual(client.way,0)
        self.assertIsNone(bridge.failure_reason)

    async def test_credit_limited_sender_crosses_second_write_counter_wrap_without_staging_overflow(self):
        profile=review_bytes(raw_file(4000));case=fixtures.ControlTCPTests()
        self.assertGreater(len(b''.join(profile.wires)),65536)
        async def scenario(reader,writer,controller,clients,rows):
            await case.wait_event(rows,'pipeline_tcp_ready')
            index,used,pending=0,0,deque()
            while index<len(profile.wires) or pending:
                while index<len(profile.wires) and used+len(profile.wires[index])<=1024:
                    wire=profile.wires[index];writer.write(wire)
                    pending.append(len(wire));used+=len(wire);index+=1
                await writer.drain()
                line=await asyncio.wait_for(reader.readline(),3)
                self.assertTrue(line)
                self.assertFalse(line.startswith((b'error:',b'ALARM:')))
                if line.strip()==b'ok':used-=pending.popleft()
            await case.wait_event(rows,'controlled_job_completed')
        controller,client,rows,bridge=await case.exercise(scenario,reviewed_file=profile,
            defer_m2=False,bridge_timeout=25)
        rows.append(dict(event='pipeline_server_stopped',cleanup_unknown=bridge.cleanup_unknown))
        for row in rows:row['simulated']=True
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'large.jsonl'
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            result=audit(path,backend='ble',simulated=True,reviewed_file=profile,expected_controls=())
        self.assertEqual(result['complete_job_commands'],8006)
        self.assertEqual(controller.job_count,8006)
        self.assertFalse(bridge.cleanup_unknown)

    async def test_changed_tcp_source_fails_before_unauthorized_line_and_never_replays(self):
        profile=review_bytes(raw_file(1));case=fixtures.ControlTCPTests()
        async def scenario(reader,writer,controller,clients,rows):
            await case.wait_event(rows,'pipeline_tcp_ready')
            writer.write(b'M5\nS0\nM3 S1000\n');await writer.drain()
            await asyncio.wait_for(reader.read(),3)
        controller,client,rows,bridge=await case.exercise(scenario,reviewed_file=profile)
        self.assertEqual(controller.job_count,0)
        self.assertNotIn(b'M3 S1000\n',controller.writes)
        self.assertFalse(any(r['event']=='controlled_job_completed' for r in rows))
        self.assertTrue(any(r['event']=='pipeline_session_error' for r in rows))
        self.assertIsNotNone(bridge.failure_reason)

    async def test_host_cancellation_of_unfinished_file_preserves_failure_even_with_clean_route(self):
        profile=review_bytes(raw_file(1));case=fixtures.ControlTCPTests();box=types.SimpleNamespace()
        async def scenario(reader,writer,controller,clients,rows):
            await case.wait_event(rows,'pipeline_tcp_ready')
            writer.write(b''.join(profile.wires));await writer.drain()
            await case.wait_event(rows,'pipeline_terminal',sequence=len(profile.commands)-1)
            box.bridge.active.cancel()
            await asyncio.wait_for(reader.read(),3)
        _,_,rows,bridge=await case.exercise(scenario,reviewed_file=profile,bridge_instance=box)
        self.assertEqual(bridge.failure_reason,'session_cancelled_with_unfinished_job')
        self.assertFalse(bridge.cleanup_unknown)
        stopped=next(r for r in rows if r['event']=='pipeline_transmission_stopped')
        self.assertFalse(stopped['job_completed']);self.assertTrue(stopped['endpoint_unknown'])
        abandoned=next(r for r in rows if r['event']=='control_generation_abandoned')
        self.assertEqual(len(abandoned['before']['pending']),1)
        self.assertFalse(any(abandoned['after'].values()))

    async def test_peer_eof_before_endpoint_confirmation_is_failed_even_with_clean_route(self):
        profile=review_bytes(raw_file(1));case=fixtures.ControlTCPTests()
        async def scenario(reader,writer,controller,clients,rows):
            await case.wait_event(rows,'pipeline_tcp_ready')
            writer.write(b''.join(profile.wires));await writer.drain()
            await case.wait_event(rows,'pipeline_terminal',sequence=len(profile.commands)-1)
            clients[0].data(b'ok\n') # M2 acknowledged while controller still reports Run/X2.
            writer.write_eof();await writer.drain();await asyncio.wait_for(reader.read(),3)
        controller,_,rows,bridge=await case.exercise(scenario,reviewed_file=profile)
        self.assertEqual(controller.job_count,len(profile.commands))
        self.assertEqual(bridge.failure_reason,'peer_eof_with_unfinished_job')
        self.assertFalse(bridge.cleanup_unknown)
        self.assertTrue(any(r['event']=='pipeline_job_incomplete_at_eof' for r in rows))
        self.assertFalse(any(r['event']=='controlled_job_completed' for r in rows))


class FileTimerTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_budget_includes_slow_source_and_excludes_confirmed_hold(self):
        now=types.SimpleNamespace(value=0.)
        pipeline=types.SimpleNamespace(ordinary_allowed=True,drained=False,internal_results=deque())
        job=ControlledReviewedJob(pipeline,None,lambda *a,**k:None,
            job_commands=(b'M5',)*166,active_timeout=80,clock=lambda:now.value)
        job.prepared=True;job.submitted=1 # Source not fully submitted yet.
        await job.advance();now.value=40;await job.advance()
        pipeline.ordinary_allowed=False;now.value=1000;await job.advance()
        pipeline.ordinary_allowed=True;now.value=1100;await job.advance()
        now.value=1140;await job.advance();self.assertEqual(job.active_elapsed,80)
        now.value=1141
        with self.assertRaises(TimeoutError):await job.advance()

    async def test_invalid_active_budget_and_oversized_estimate_are_rejected(self):
        for timeout in (0,-1,float('nan'),float('inf'),25201):
            with self.assertRaises(ValueError):ControlledReviewedJob(None,None,print,active_timeout=timeout)
        profile=review_bytes(b'M5\nS0\nG1 X10 F.001\nG1 X0\nM5\nS0\nM2\n')
        with self.assertRaises(ValueError):profile.execution_budget()
        with self.assertRaises(ValueError):PipelineTCPBridge('unused',print,
            reviewed_file=review_bytes(raw_file(1)))


class FileCLITests(unittest.TestCase):
    def test_default_file_review_never_imports_bleak_and_missing_or_stale_gates_refuse_before_log(self):
        script=Path(__file__).resolve().parents[1]/'tools/pipeline_tcp_bridge.py'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'job.gc';source.write_bytes(raw_file(1));profile=load(source)
            (root/'bleak.py').write_text('raise RuntimeError("Hardware import must not occur")\n')
            environment=dict(os.environ,PYTHONPATH=directory)
            command=[sys.executable,str(script),'--job-file',str(source)]
            result=subprocess.run(command,capture_output=True,text=True,env=environment)
            self.assertEqual(result.returncode,0)
            self.assertEqual(json.loads(result.stdout)['file_candidate']['command_count'],8)
            for extra in ([],['--expected-job-sha256',profile.sha256],
                    ['--allow-reviewed-file','--allow-reviewed-job-controls'],
                    ['--allow-reviewed-file','--allow-reviewed-job-controls','--expected-job-sha256','0'*64]):
                log=root/'not-created.jsonl'
                result=subprocess.run(command+['--execute','--allow-initialization',
                    '--laser-power-isolated','--log',str(log)]+extra,capture_output=True,text=True,env=environment)
                self.assertEqual(result.returncode,2,result.stderr)
                self.assertFalse(log.exists())
                self.assertNotIn('Hardware import must not occur',result.stderr)
