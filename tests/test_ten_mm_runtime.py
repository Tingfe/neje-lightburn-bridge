# evidence/ is deliberately not part of the repository; tests that read real captures
# skip themselves explicitly rather than failing or pretending to pass.
import os as _os, unittest as _unittest
if not _os.path.isdir(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'evidence')):
    raise _unittest.SkipTest('evidence/ is not part of this repository')
import asyncio
import datetime
import json
from pathlib import Path
import sys
import types
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from check_ten_mm_host import TenMMUSBSession, inspect
from grbl_pipeline import GrblPipeline
from pipeline_tcp_bridge import PipelineTCPBridge
from reviewed_job import ReviewedJobParser
from reviewed_job_bridge import JobChannel
from reviewed_pipeline_job import ReviewedPipelineJob
from reviewed_ten_mm import TEN_MM_JOB, wire_lines
from reviewed_ten_mm import review
from check_ten_mm_capture import audit
from test_byte_stream import FakeClient

IDLE = b'<Idle|MPos:0,400,0,0|Bf:100,1500|FS:0,0>\n'
SETTINGS = b'$13=0\n$30=1000\n$32=1\n$130=260\n$131=400\nok\n'
MODAL = b'[GC:G0 G54 G17 G21 G90 G94 M5 S0 F60]\nok\n'


class Session:
    def __init__(self, *args, bad_offset=False, bad_status=None, split_message=False,
                 disconnect_forward=False, slow_settings=False):
        self.writes=[];self.partial=bytearray();self.job_count=0;self.closed=False
        self.bad_offset, self.bad_status = bad_offset, bad_status
        self.split_message, self.message_tail = split_message, b''
        self.disconnect_forward, self.disconnected = disconnect_forward, False
        self.slow_settings=slow_settings
        self.settings_started, self.settings_release = asyncio.Event(), asyncio.Event()
    async def start(self): pass
    async def verify(self): return b''
    async def close(self):
        self.closed=True
        if self.disconnected: raise ConnectionError('Injected disconnected cleanup unknown')
    async def write(self, wire):
        self.writes.append(wire)
        if self.disconnect_forward and b'G1 X10' in self.partial+wire:
            self.disconnected=True
            raise ConnectionError('Injected acceptance then disconnect; never retry')
        if wire==b'?':
            if self.job_count:
                return self.bad_status or IDLE
            if self.split_message and b'$$\n' in self.writes:
                self.message_tail=b' boundary]\n'
                return IDLE+b'[MSG:internal'
            return IDLE
        if wire==b'$$\n':
            self.settings_started.set()
            if self.slow_settings: await self.settings_release.wait()
            return SETTINGS
        if wire==b'$G\n':return MODAL
        if wire==b'$#\n':
            x=b'1' if self.bad_offset else b'0'
            return b'[G54:'+x+b',0,0,0]\n[G92:0,0,0,0]\nok\n'
        output=bytearray(self.message_tail);self.message_tail=b''
        self.partial.extend(wire)
        while b'\n' in self.partial:
            index=self.partial.index(10)
            command=bytes(self.partial[:index]).split(b';',1)[0].strip()
            del self.partial[:index+1]
            if command!=b'G0':self.job_count+=1
            output.extend(b'ok\n')
        return bytes(output)


class JobGateTests(unittest.IsolatedAsyncioTestCase):
    async def setup_gate(self, session):
        rows=[];visible=[]
        def log(event,**kw):rows.append(dict(event=event,**kw))
        async def deliver(data):visible.append(data)
        channel=JobChannel(session,log,deliver,max_x=10,completion_timeout=45)
        pipeline=GrblPipeline(session,deliver,log,rx_budget=1492,payload_capacity=173,
                              deliver_lines=True)
        pipeline.output=channel.lines
        gate=ReviewedPipelineJob(pipeline,channel,log)
        pipeline.check_status=gate.check_status
        parser=ReviewedJobParser(job=TEN_MM_JOB)
        for kind,wire in parser.feed(b''.join(wire_lines())):gate.enqueue(kind,wire)
        parser.eof()
        return gate,pipeline,rows,visible

    async def test_complete_fixed_job_hides_internal_acks_and_fragment_suffixes(self):
        session=Session(split_message=True)
        gate,pipeline,rows,visible=await self.setup_gate(session)
        for _ in range(20):
            await gate.advance()
            if gate.completed:break
            await pipeline.step()
        self.assertTrue(gate.completed)
        self.assertEqual(gate.submitted,14)
        public=b''.join(visible)
        self.assertEqual(public.count(b'ok\n'),14)
        self.assertEqual(public.count(b'[MSG:internal boundary]\n'),1)
        self.assertNotIn(b'$130=',public)
        self.assertNotIn(b'[GC:',public)
        self.assertTrue(session.writes[-2:]==[b'G0\n',b'?'])

    async def test_preflight_waits_for_previous_real_terminal(self):
        session=Session()
        gate,pipeline,rows,visible=await self.setup_gate(session)
        pipeline.enqueue(b'G0\n')
        await gate.advance()
        self.assertFalse(gate.prepared)
        self.assertEqual(session.writes,[])
        await pipeline.step()
        await gate.advance()
        self.assertTrue(gate.prepared)
        self.assertEqual(session.writes[:2],[b'G0\n',b'$$\n'])

    async def test_changed_offset_prevents_any_job_write(self):
        session=Session(bad_offset=True)
        gate,pipeline,rows,visible=await self.setup_gate(session)
        with self.assertRaises(RuntimeError):await gate.advance()
        self.assertEqual(session.writes,[b'$$\n',b'$G\n',b'$#\n',b'?'])
        self.assertEqual(gate.submitted,0)

    async def test_nonzero_power_nonfinite_or_wrong_axes_stop_without_restore(self):
        for status in (b'<Run|MPos:10.1,400,0,0|FS:60,0>\n',
                       b'<Run|MPos:5,399,0,0|FS:60,0>\n',
                       b'<Run|MPos:5,400,0,0|FS:60,1>\n',
                       b'<Run|MPos:5,nan,0,0|FS:60,0>\n',
                       b'<Idle|MPos:1,400,0,0|FS:0,0>\n'):
            session=Session(bad_status=status)
            gate,pipeline,rows,visible=await self.setup_gate(session)
            with self.assertRaises(RuntimeError):
                for _ in range(20):
                    await gate.advance()
                    await pipeline.step()
            self.assertFalse(gate.completed)
            self.assertNotIn(b'G0\n',session.writes)

    async def test_public_status_envelope_is_checked_and_fault_latched(self):
        session=Session(bad_status=b'<Run|MPos:5,400,0,1|FS:60,0>\n')
        gate,pipeline,rows,visible=await self.setup_gate(session)
        await gate.advance();await pipeline.step()
        pipeline.realtime(b'?')
        with self.assertRaises(RuntimeError):await pipeline.step()
        self.assertIsNotNone(pipeline.fault)
        before=list(session.writes)
        with self.assertRaises(RuntimeError):await pipeline.step()
        self.assertEqual(session.writes,before)

    async def test_completion_waits_run_to_idle_and_times_out_without_return(self):
        session=Session(bad_status=b'<Run|MPos:5,400,0,0|FS:60,0>\n')
        gate,pipeline,rows,visible=await self.setup_gate(session)
        gate.channel.completion_timeout=.01
        with self.assertRaises(TimeoutError):
            for _ in range(20):
                await gate.advance();await pipeline.step()
        self.assertNotIn(b'G0\n',session.writes)
        self.assertFalse(gate.completed)

    async def test_completion_accepts_real_run_then_idle_within_ten_mm(self):
        class Moving(Session):
            reports=0
            async def write(self, wire):
                if wire==b'?' and self.job_count and self.reports<2:
                    self.reports+=1
                    self.writes.append(wire)
                    return b'<Run|MPos:5,400,0,0|FS:60,0>\n'
                return await super().write(wire)
        session=Moving();gate,pipeline,rows,visible=await self.setup_gate(session)
        for _ in range(20):
            await gate.advance()
            if gate.completed:break
            await pipeline.step()
        self.assertTrue(gate.completed)
        self.assertEqual(session.reports,2)

    async def test_post_job_init_and_status_wait_behind_completion_barrier(self):
        session=Session();gate,pipeline,rows,visible=await self.setup_gate(session)
        gate.enqueue('initialization',b'G0\n')
        await gate.advance()
        self.assertEqual(len(gate.staged),1)
        for _ in range(10):
            await pipeline.step();await gate.advance()
            if gate.completed:break
        self.assertTrue(gate.completed)
        self.assertEqual(len(gate.staged),1)
        await gate.advance();await pipeline.step()
        self.assertEqual(session.writes.count(b'G0\n'),2) # Internal restore + real public init.

    async def test_usb_default_is_preflight_only_and_guard_latches_partial_write(self):
        usb=TenMMUSBSession(lambda *a,**kw:None)
        usb.backend.port=types.SimpleNamespace(write=lambda data:len(data),read=lambda n:b'')
        with self.assertRaises(ValueError):await usb.write(b'M5\n')
        usb=TenMMUSBSession(lambda *a,**kw:None,allow_job=True)
        usb.backend.port=types.SimpleNamespace(write=lambda data:len(data)-1,read=lambda n:b'')
        with self.assertRaises(RuntimeError):await usb.write(b'M5\n')
        self.assertEqual(usb.attempted,3)
        with self.assertRaises(RuntimeError):await usb.write(b'?')

    async def test_usb_and_ble_probe_default_never_execute_job(self):
        session=Session();rows=[]
        await inspect(session,lambda event,**kw:rows.append(dict(event=event,**kw)))
        self.assertEqual(session.writes,[b'$$\n',b'$G\n',b'$#\n',b'?'])
        self.assertTrue(session.closed)
        self.assertFalse(next(r for r in rows if r['event']=='ten_mm_host_passed')['motion_executed'])

    async def test_usb_pipeline_uses_same_file_once_and_closes(self):
        session=Session();rows=[]
        await inspect(session,lambda event,**kw:rows.append(dict(event=event,**kw)),execute_job=True)
        ordinary=b''.join(w for w in session.writes if w not in (b'?',b'$$\n',b'$G\n',b'$#\n',b'G0\n'))
        self.assertEqual(ordinary,b''.join(wire_lines()))
        self.assertEqual(session.job_count,14)
        self.assertTrue(session.closed)
        self.assertTrue(next(r for r in rows if r['event']=='usb_ten_mm_stopped')['job_completed'])


class TCPJobTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, scenario, *, session_factory=Session, **session_args):
        rows=[];session=session_factory(**session_args)
        class Client:
            def __init__(self,*a,**kw):
                self.services=types.SimpleNamespace(get_characteristic=lambda uuid:
                    types.SimpleNamespace(max_write_without_response_size=20 if session.disconnect_forward else 182))
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
        bridge=PipelineTCPBridge('fake',lambda event,**kw:rows.append(dict(event=event,**kw)),
                                 timeout=5,reviewed_ten_mm=True)
        with patch.dict(sys.modules,{'bleak':types.SimpleNamespace(BleakClient=Client)}), \
             patch('pipeline_tcp_bridge.ByteStreamSession',return_value=session):
            server=await asyncio.start_server(bridge.handle,'127.0.0.1',0)
            reader,writer=await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1])
            try:await scenario(reader,writer,session,rows)
            finally:
                writer.close();await writer.wait_closed()
                await asyncio.wait_for(bridge.finished.wait(),4)
                server.close();await server.wait_closed()
        return session,rows,bridge

    async def test_actual_tcp_fixed_job_and_second_job_rejection(self):
        async def scenario(reader,writer,session,rows):
            writer.write(b''.join(wire_lines()));await writer.drain()
            for _ in range(14):await asyncio.wait_for(reader.readuntil(b'ok\n'),2)
            while not any(r['event']=='pipeline_job_completed' for r in rows):await asyncio.sleep(.01)
            writer.write(b'M5\n');await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(),2),b'')
        session,rows,bridge=await self.exercise(scenario)
        self.assertTrue(any(r['event']=='pipeline_session_error' for r in rows))
        self.assertEqual(sum(r['event']=='pipeline_job_completed' for r in rows),1)
        self.assertEqual(len([r for r in rows if r['event']=='pipeline_job_submitted']),14)
        self.assertTrue(session.closed)

    async def test_eof_during_preflight_does_not_send_first_job_or_next_query(self):
        async def scenario(reader,writer,session,rows):
            writer.write(b''.join(wire_lines()));await writer.drain()
            await asyncio.wait_for(session.settings_started.wait(),2)
            writer.write_eof()
            while not any(r['event']=='pipeline_tcp_eof' for r in rows):await asyncio.sleep(.01)
            session.settings_release.set()
            self.assertEqual(await asyncio.wait_for(reader.read(),2),b'')
        session,rows,bridge=await self.exercise(scenario,slow_settings=True)
        self.assertEqual(session.writes,[b'?',b'$$\n'])
        self.assertFalse(any(r['event']=='pipeline_job_completed' for r in rows))
        self.assertTrue(session.closed)

    async def test_fragment_write_disconnect_does_not_append_return_or_replay(self):
        async def scenario(reader,writer,session,rows):
            writer.write(b''.join(wire_lines()));await writer.drain()
            await asyncio.wait_for(reader.read(),3)
        session,rows,bridge=await self.exercise(scenario,disconnect_forward=True)
        attempted=b''.join(w for w in session.writes if w not in (b'?',b'$$\n',b'$G\n',b'$#\n'))
        self.assertEqual(attempted.count(b'G1 X10'),1)
        self.assertNotIn(b'G1 X0\n',attempted)
        self.assertTrue(bridge.cleanup_unknown)
        stopped=next(r for r in rows if r['event']=='pipeline_transmission_stopped')
        self.assertTrue(stopped['endpoint_unknown'])
        self.assertFalse(stopped['job_completed'])

    async def test_real_m2_ack_after_eighteen_simulated_seconds_uses_job_timeout(self):
        clock=types.SimpleNamespace(now=0)
        class SlowM2(Session):
            deferred=False;polls=0
            async def write(self,wire):
                output=await super().write(wire)
                if self.job_count==14 and wire.endswith(b'M2\n'):
                    self.deferred=True
                    return output[:-3] # Withhold only the final real M2 terminal.
                return output
            async def verify(self):
                if self.deferred:
                    self.polls+=1;clock.now+=9
                    if self.polls==2:
                        self.deferred=False
                        return b'ok\n'
                return b''
        async def scenario(reader,writer,session,rows):
            writer.write(b''.join(wire_lines()));await writer.drain()
            for _ in range(14):await asyncio.wait_for(reader.readuntil(b'ok\n'),2)
            while not any(r['event']=='pipeline_job_completed' for r in rows):await asyncio.sleep(.01)
        with patch('pipeline_tcp_bridge.time',types.SimpleNamespace(monotonic=lambda:clock.now)):
            session,rows,bridge=await self.exercise(scenario,session_factory=SlowM2)
        self.assertEqual(clock.now,18)
        self.assertFalse(any(r['event']=='pipeline_session_error' for r in rows))


class CaptureAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_usb_and_lightburn_ble_captures_pass_same_job_audit(self):
        root=Path(__file__).resolve().parents[1]/'evidence/ten-mm-runtime'
        for backend,name,tcp in (('usb','usb-job-01.jsonl',False),
                                  ('ble','lightburn-ble-job-01.jsonl',True)):
            result=audit(root/name,backend=backend,tcp_job=tcp)
            self.assertTrue(result['passed'])
            self.assertTrue(result['motion_executed'])
            self.assertFalse(result['physical_displacement_independently_measured'])

    async def test_raw_frames_fifo_public_output_and_internal_queries_audit_independently(self):
        rows=[]
        def log(event,**kw):
            rows.append(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),event=event,**kw))
        class Client(FakeClient):
            def __init__(self,*a,**kw):
                super().__init__();self.controller=Session(split_message=True)
                self.services=types.SimpleNamespace(get_characteristic=lambda uuid:
                    types.SimpleNamespace(max_write_without_response_size=182))
            async def __aenter__(self):return self
            async def __aexit__(self,*a):self.is_connected=False
            async def write_gatt_char(self,uuid,packet,response):
                if packet[3:5]!=b'\x40\x03':
                    return await super().write_gatt_char(uuid,packet,response)
                self.writes.append(packet);wire=packet[8:-1]
                assert int.from_bytes(packet[6:8],'little')==self.w
                self.w=(self.w+len(wire))&65535
                reply=await self.controller.write(wire)
                for i in range(0,len(reply),31):self.data(reply[i:i+31])
        log('pipeline_fixed_job_review',**review())
        bridge=PipelineTCPBridge('fake',log,timeout=5,reviewed_ten_mm=True)
        with patch.dict(sys.modules,{'bleak':types.SimpleNamespace(BleakClient=Client)}):
            server=await asyncio.start_server(bridge.handle,'127.0.0.1',0)
            reader,writer=await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1])
            writer.write(b''.join(wire_lines()));await writer.drain()
            for _ in range(14):await asyncio.wait_for(reader.readuntil(b'ok\n'),2)
            while not any(r['event']=='pipeline_job_completed' for r in rows):await asyncio.sleep(.01)
            writer.write_eof();await reader.read();writer.close();await writer.wait_closed()
            await asyncio.wait_for(bridge.finished.wait(),2)
            server.close();await server.wait_closed()
        log('pipeline_server_stopped',cleanup_unknown=bridge.cleanup_unknown)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'simulated.jsonl'
            def save():path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            save();result=audit(path,backend='ble',tcp_job=True)
            self.assertTrue(result['passed'])
            public=next(r for r in rows if r['event']=='pipeline_tcp_delivered' and '6f6b0a' in r['hex'])
            old=public['hex'];public['hex']+='6f6b0a';save()
            with self.assertRaises(AssertionError):audit(path,backend='ble',tcp_job=True)
            public['hex']=old
            internal=next(r for r in rows if r['event']=='job_request_attempt' and r['name']=='restore_rapid_mode')
            internal['hex']=b'G0 X20\n'.hex();save()
            with self.assertRaises(AssertionError):audit(path,backend='ble',tcp_job=True)


if __name__=='__main__':unittest.main()
