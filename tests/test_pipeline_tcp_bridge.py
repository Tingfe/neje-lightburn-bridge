import asyncio
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from pipeline_tcp_bridge import PipelineTCPBridge
from test_byte_stream import FakeClient


class PipelineTCPTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, scenario, *, delay=0, disconnect=False, defer_first=False,
                       capture_controls=False, receive_gap=False, cleanup_delay=0):
        rows,clients=[],[]
        class Client(FakeClient):
            def __init__(self,*args,**kwargs):
                super().__init__(); self.payloads=[];self.partial=bytearray(); self.status_count=0
                self.normal_count=0; self.deferred=b'';clients.append(self)
            async def __aenter__(self):return self
            async def __aexit__(self,*args):self.is_connected=False
            async def write_gatt_char(self,uuid,packet,response):
                from grbl_readonly import DISABLE_RELIABLE
                if packet == DISABLE_RELIABLE:
                    await asyncio.sleep(cleanup_delay)
                if packet[3:5]!=b'\x40\x03':return await super().write_gatt_char(uuid,packet,response)
                self.writes.append(packet)
                wire=packet[8:-1]
                assert int.from_bytes(packet[6:8],'little')==self.w
                if wire==b'?':
                    self.status_count+=1
                    if self.status_count>1:await asyncio.sleep(delay)
                self.payloads.append(wire);self.w=(self.w+len(wire))&65535
                if disconnect and self.status_count>1:
                    self.is_connected=False
                    raise ConnectionError('Injected acceptance then BLE disconnect')
                if wire==b'?':
                    answer=b'<Idle|MPos:0,400,0,0|Bf:100,1500|FS:0,0>\n'
                else:
                    self.partial.extend(wire);answer=bytearray()
                    while b'\n' in self.partial:
                        index=self.partial.index(10);line=bytes(self.partial[:index]).strip();del self.partial[:index+1]
                        if line==b'$I':answer.extend(b'[VER:1.1f]\n[OPT:VMZHL,100,1500]\n')
                        if line==b'$#':answer.extend(b'[G54:0,0,0,0]\n')
                        answer.extend(b'ok\n[MSG:temp]\n')
                    self.normal_count+=1
                    if defer_first and self.normal_count==1:
                        self.deferred=bytes(answer);return
                    answer=self.deferred+answer;self.deferred=b''
                    if receive_gap:
                        self.r=(self.r+1400)&65535
                for i in range(0,len(answer),60):self.data(bytes(answer[i:i+60]))
        bridge=PipelineTCPBridge('fake',lambda event,**kw:rows.append(dict(event=event,**kw)),
                                 timeout=5,capture_controls=capture_controls)
        with patch.dict(sys.modules,{'bleak':types.SimpleNamespace(BleakClient=Client)}):
            server=await asyncio.start_server(bridge.handle,'127.0.0.1',0)
            port=server.sockets[0].getsockname()[1]
            reader,writer=await asyncio.open_connection('127.0.0.1',port)
            try:
                await scenario(reader,writer,rows,bridge,port)
            finally:
                writer.close();await writer.wait_closed()
                for _ in range(400):
                    if bridge.active is None:break
                    await asyncio.sleep(.01)
                if bridge.active:
                    bridge.active.cancel();await asyncio.gather(bridge.active,return_exceptions=True)
                server.close();await server.wait_closed()
        return clients[0],rows,bridge

    async def wait_event(self,rows,event):
        for _ in range(300):
            if any(r['event']==event for r in rows):return
            await asyncio.sleep(.01)
        self.fail('Missing '+event)

    async def test_init_exact_wire_real_acks_and_single_connection(self):
        async def scenario(reader,writer,rows,bridge,port):
            writer.write(b'$I\r\nG0\n$#\n?\n');await writer.drain()
            output=bytearray()
            for _ in range(4):output.extend(await asyncio.wait_for(reader.readuntil(b'ok\n'),3))
            self.assertEqual(output.count(b'ok\n'),4)
            self.assertEqual(output.count(b'<Idle|'),1)
            second_r,second_w=await asyncio.open_connection('127.0.0.1',port)
            self.assertEqual(await second_r.read(),b'');second_w.close();await second_w.wait_closed()
        client,rows,bridge=await self.exercise(scenario)
        ordinary=b''.join(w for w in client.payloads if w!=b'?')
        self.assertEqual(ordinary,b'$I\r\nG0\n$#\n\n')
        self.assertFalse(bridge.cleanup_unknown)
        # A reconnect after the single-use session is refused, and the record says
        # a new session is required so operators do not retry in the client UI.
        rejected=[r for r in rows if r['event']=='pipeline_tcp_rejected']
        self.assertEqual(len(rejected),1)
        self.assertEqual(rejected[0]['reason'],'connection_consumed_no_reconnect')
        self.assertTrue(rejected[0]['new_session_required'])
        self.assertTrue(any(r['event']=='cleanup_verified' for r in rows))
        self.assertTrue(bridge.finished.is_set())

    async def test_second_command_sent_before_first_ack(self):
        async def scenario(reader,writer,rows,*args):
            writer.write(b'G0\n');await writer.drain()
            await self.wait_event(rows,'pipeline_line_reserved')
            writer.write(b'G0\n');await writer.drain()
            for _ in range(2):await asyncio.wait_for(reader.readuntil(b'ok\n'),3)
        client,rows,bridge=await self.exercise(scenario,defer_first=True)
        reserved=[i for i,r in enumerate(rows) if r['event']=='pipeline_line_reserved']
        first_ack=next(i for i,r in enumerate(rows) if r['event']=='pipeline_terminal')
        self.assertEqual(len(reserved),2)
        self.assertLess(reserved[1],first_ack)
        self.assertFalse(bridge.cleanup_unknown)

    async def test_capture_records_controls_without_hardware_forward_or_fake_success(self):
        async def scenario(reader,writer,rows,*args):
            writer.write(b'!~\x18$I\n');await writer.drain()
            result=await asyncio.wait_for(reader.readuntil(b'ok\n'),2)
            self.assertEqual(result.count(b'ok\n'),1)
        client,rows,bridge=await self.exercise(scenario,capture_controls=True)
        self.assertEqual(client.payloads,[b'?',b'$I\n'])
        captured=[r for r in rows if r['event']=='lightburn_control_captured']
        self.assertEqual([r['hex'] for r in captured],['21','7e','18'])
        self.assertTrue(all(not r['forwarded'] and not r['controller_success_claimed'] for r in captured))
        with self.assertRaises(ValueError):
            PipelineTCPBridge('fake',lambda *a,**kw:None,reviewed_ten_mm=True,capture_controls=True)

    async def test_receive_fault_during_slow_cleanup_cannot_cancel_owned_shutdown(self):
        async def scenario(reader,writer,rows,*args):
            writer.write(b'G0\n'); await writer.drain()
            await self.wait_event(rows,'stream_stopped')
            # Reproduce the actual race: ingress notices the already-latched
            # fault while operate's owned-route close is awaiting GATT.
            writer.write(b'?\n'); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(),3),b'')
        client,rows,bridge=await self.exercise(scenario,receive_gap=True,cleanup_delay=.4)
        self.assertFalse(bridge.cleanup_unknown)
        self.assertEqual(client.way,0)
        self.assertEqual(client.payloads,[b'?',b'G0\n'])
        events=[r['event'] for r in rows]
        self.assertLess(events.index('cleanup_verified'),events.index('pipeline_tcp_closed'))
        self.assertTrue(any(r['event']=='pipeline_stopped' for r in rows))

    async def test_policy_blocks_motion_power_reset_and_incomplete_eof(self):
        for wire in (b'G1 X1\n',b'M3 S1\n',b'!',b'\x18',b'G0 X'):
            async def scenario(reader,writer,*args):
                writer.write(wire);await writer.drain();writer.write_eof()
                self.assertEqual(await asyncio.wait_for(reader.read(),3),b'')
            client,rows,bridge=await self.exercise(scenario)
            self.assertEqual(client.payloads,[b'?'])
            self.assertTrue(any(r['event']=='pipeline_session_error' for r in rows))
            self.assertFalse(bridge.cleanup_unknown)

    async def eof_inflight(self,reader,writer,rows,*args):
        writer.write(b'?\n$I\n');await writer.drain()
        await self.wait_event(rows,'pipeline_write_attempt')
        writer.write_eof()
        self.assertEqual(await asyncio.wait_for(reader.read(),4),b'')

    async def test_eof_drain_never_sends_queued_normal_commands(self):
        client,rows,bridge=await self.exercise(self.eof_inflight,delay=.15)
        self.assertEqual(client.payloads,[b'?',b'?'])
        self.assertTrue(any(r['event']=='pipeline_tcp_delivery_suppressed' for r in rows))
        self.assertTrue(any(r['event']=='pipeline_eof_responses_drained' for r in rows))
        stop=next(r for r in rows if r['event']=='pipeline_transmission_stopped')
        self.assertEqual(stop['queued_bytes'],4)
        self.assertFalse(bridge.cleanup_unknown)

    async def test_eof_drain_timeout_records_uncertain_write_without_retry(self):
        client,rows,bridge=await self.exercise(self.eof_inflight,delay=3)
        self.assertTrue(any(r['event']=='pipeline_eof_drain_timeout' for r in rows))
        self.assertTrue(any(r['event']=='stream_write_uncertain' for r in rows))
        self.assertNotIn(b'$I\n',client.payloads)

    async def test_disconnect_during_drain_reports_cleanup_unknown(self):
        client,rows,bridge=await self.exercise(self.eof_inflight,delay=.15,disconnect=True)
        self.assertEqual(client.payloads,[b'?',b'?'])
        self.assertTrue(bridge.cleanup_unknown)
        self.assertFalse(any(r['event']=='cleanup_verified' for r in rows))

class ListenerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_completion_closes_listener_before_global_timeout(self):
        await self.check_listener(None)

    async def test_clean_route_shutdown_does_not_hide_a_failed_session_exit(self):
        await self.check_listener('controller_error')

    async def check_listener(self, failure_reason):
        from pipeline_tcp_bridge import run
        log_rows=[]; listening=asyncio.Event(); port=None
        original=asyncio.start_server
        class FinishedBridge:
            def __init__(self,*args):
                self.finished=asyncio.Event();self.cleanup_unknown=False;self.active=None
                self.profile='initialization_only';self.failure_reason=failure_reason
            async def handle(self,reader,writer):
                await reader.read()
                writer.close();await writer.wait_closed()
                self.finished.set()
        async def ephemeral(handler,host,ignored_port):
            nonlocal port
            server=await original(handler,host,0)
            port=server.sockets[0].getsockname()[1]
            return server
        async def discover(**kwargs):return [types.SimpleNamespace(name='NEJE 5 Plus',address='test-device')]
        def log(event,**fields):
            log_rows.append(event)
            if event=='pipeline_listening':listening.set()
        module=types.SimpleNamespace(BleakScanner=types.SimpleNamespace(discover=discover))
        with patch.dict(sys.modules,{'bleak':module}), patch('pipeline_tcp_bridge.PipelineTCPBridge',FinishedBridge), \
             patch('pipeline_tcp_bridge.asyncio.start_server',ephemeral), \
             patch.object(asyncio.get_running_loop(),'add_signal_handler'):
            task=asyncio.create_task(run(types.SimpleNamespace(seconds=30),log))
            await asyncio.wait_for(listening.wait(),1)
            reader,writer=await asyncio.open_connection('127.0.0.1',port)
            writer.write_eof();await reader.read();writer.close();await writer.wait_closed()
            if failure_reason:
                with self.assertRaisesRegex(RuntimeError,'Session failed'):
                    await asyncio.wait_for(task,1)
            else:
                await asyncio.wait_for(task,1)
        self.assertIn('pipeline_server_stopped',log_rows)
        with self.assertRaises(OSError):await asyncio.open_connection('127.0.0.1',port)
