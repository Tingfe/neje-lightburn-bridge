import asyncio
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from grbl_pipeline import GrblPipeline
from neje_byte_stream import ByteStreamSession
from fake_ble import FakeClient


class DeferredSession:
    def __init__(self):
        self.writes = []
        self.response = b''
        self.fail = False
    async def write(self, payload):
        self.writes.append(payload)
        if self.fail:
            raise ConnectionError('Accepted bytes then disconnected')
        return await self.verify()
    async def verify(self):
        data, self.response = self.response, b''
        return data


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def make(self, session=None, rx=24, capacity=10, queue_bytes=48, max_pending_lines=64):
        self.session = session or DeferredSession()
        self.visible, self.events = bytearray(), []
        async def deliver(data): self.visible.extend(data)
        return GrblPipeline(self.session, deliver,
            lambda event, **kw: self.events.append(dict(event=event, **kw)),
            rx_budget=rx, payload_capacity=capacity, queue_bytes=queue_bytes,
            max_pending_lines=max_pending_lines)

    async def test_large_response_queries_are_barriers_but_realtime_bypasses(self):
        p = self.make(capacity=24)
        for line in (b'G0\n', b'$I\n', b'$#\n', b'G0\n'): p.enqueue(line)
        await p.step()
        self.assertEqual(self.session.writes, [b'G0\n'])
        self.session.response = b'ok\n'; await p.step(); await p.step()
        self.assertEqual(self.session.writes[-1], b'$I\n')
        self.session.response = b'[VER:example]\n'; await p.step()
        self.assertEqual(self.session.writes[-1], b'$I\n')
        p.realtime(b'?'); self.session.response = b'<Idle|FS:0,0>\n'; await p.step()
        self.assertEqual(self.session.writes[-1], b'?')
        self.session.response = b'ok\n'; await p.step(); await p.step()
        self.assertEqual(self.session.writes[-1], b'$#\n')
        self.session.response = b'ok\n'; await p.step(); await p.step()
        self.assertEqual(self.session.writes[-1], b'G0\n')
        self.session.response = b'ok\n'; await p.step()
        self.assertTrue(p.drained)

    async def test_blank_line_response_burst_has_independent_line_bound(self):
        p = self.make(rx=24, capacity=24, max_pending_lines=4)
        for _ in range(12): p.enqueue(b'\n')
        await p.step(); await p.step()
        self.assertEqual(self.session.writes, [b'\n'*4])
        self.session.response = b'ok\n'*4; await p.step(); await p.step()
        self.assertEqual(self.session.writes, [b'\n'*4, b'\n'*4])

    async def test_batches_without_waiting_for_ack_and_splits_lines(self):
        p = self.make()
        for line in (b'G1 X1\r\n', b'G1 X2\n', b'G1 X3\n'): p.enqueue(line)
        await p.step(); await p.step()
        self.assertEqual(b''.join(self.session.writes), b'G1 X1\r\nG1 X2\nG1 X3\n')
        self.assertEqual(len(p.pending), 3)
        self.session.response = b'ok\n[MSG:temp]\nok\nok\n'
        await p.step()
        self.assertTrue(p.drained)
        self.assertEqual(self.visible, b'ok\n[MSG:temp]\nok\nok\n')

    async def test_credit_blocks_next_line_but_status_bypasses_full_window(self):
        p = self.make(rx=12, capacity=12)
        p.enqueue(b'G1 X1\n'); p.enqueue(b'G1 X2\n'); p.enqueue(b'G1 X3\n')
        await p.step(); await p.step()
        self.assertEqual(self.session.writes, [b'G1 X1\nG1 X2\n'])
        p.realtime(b'?'); self.session.response = b'<Run|FS:60,0>\n'
        await p.step()
        self.assertEqual(self.session.writes[-1], b'?')
        self.assertEqual(p.occupied, 12)
        self.session.response = b'ok\n'; await p.step(); await p.step()
        self.assertEqual(self.session.writes[-1], b'G1 X3\n')

    async def test_split_ack_and_push_never_release_extra_credit(self):
        p = self.make(rx=6, capacity=6)
        p.enqueue(b'G1 X1\n'); p.enqueue(b'G1 X2\n')
        self.session.response = b'o'; await p.step()
        self.assertFalse(p.drained)
        self.session.response = b'k\n[MSG:ok in message]\n'; await p.step()
        self.assertEqual(len(self.session.writes), 1)
        await p.step()
        self.assertEqual(self.session.writes[-1], b'G1 X2\n')
        self.assertEqual(sum(e['event']=='pipeline_terminal' for e in self.events), 1)

    async def test_status_newline_is_separate_real_blank_line_ack(self):
        p = self.make()
        p.realtime(b'?'); p.enqueue(b'\n')
        self.session.response = b'<Idle|FS:0,0>\n'; await p.step()
        self.session.response = b'ok\n'; await p.step()
        self.assertEqual(self.session.writes, [b'?', b'\n'])
        self.assertTrue(p.drained)

    async def test_receive_only_never_sends_queued_lines_or_controls(self):
        p = self.make()
        p.enqueue(b'G1 X1\n'); await p.step()
        p.enqueue(b'G1 X2\n'); p.realtime(b'?')
        self.session.response = b'ok\n'
        await p.step(receive_only=True)
        self.assertEqual(self.session.writes, [b'G1 X1\n'])
        self.assertEqual(p.queued_bytes, 6)
        self.assertEqual(list(p.controls), [b'?'])
        self.assertFalse(p.pending)

    async def test_real_error_is_forwarded_then_stops_queued_lines(self):
        p = self.make(rx=6, capacity=6)
        p.enqueue(b'G1 X1\n'); p.enqueue(b'G1 X2\n')
        self.session.response = b'error:20\n'
        with self.assertRaises(RuntimeError): await p.step()
        self.assertEqual(self.visible, b'error:20\n')
        with self.assertRaises(RuntimeError): await p.step()
        self.assertEqual(self.session.writes, [b'G1 X1\n'])

    async def test_uncertain_write_and_cancellation_never_retry(self):
        for cancel in (False, True):
            p = self.make()
            p.enqueue(b'G1 X1\n')
            if cancel:
                async def write(payload):
                    self.session.writes.append(payload)
                    raise asyncio.CancelledError()
                self.session.write = write
            else: self.session.fail = True
            with self.assertRaises((ConnectionError, asyncio.CancelledError)): await p.step()
            with self.assertRaises(RuntimeError): await p.step()
            self.assertEqual(self.session.writes, [b'G1 X1\n'])
            self.assertFalse(p.drained)

    async def test_terminal_before_partial_line_complete_stops(self):
        p = self.make(capacity=3); p.enqueue(b'G1 X1\n')
        self.session.response = b'ok\n'
        with self.assertRaises(RuntimeError): await p.step()
        self.assertEqual(self.session.writes, [b'G1 '])

    async def test_alarm_reset_and_unexpected_ok_stop_without_synthetic_reply(self):
        for response in (b'ALARM:1\n', b'Grbl 1.1f\n', b'ok\n'):
            p = self.make(); self.session.response = response
            with self.assertRaises(RuntimeError): await p.step()
            self.assertEqual(self.visible, response)
            with self.assertRaises(RuntimeError): p.enqueue(b'M5\n')

    async def test_queue_backpressure_and_realtime_bound(self):
        p = self.make(rx=6, queue_bytes=12)
        p.enqueue(b'G1 X1\n'); p.enqueue(b'G1 X2\n')
        with self.assertRaises(asyncio.QueueFull): p.enqueue(b'G1 X3\n')
        for _ in range(8): p.realtime(b'?')
        with self.assertRaises(asyncio.QueueFull): p.realtime(b'?')
        self.assertEqual(p.queued_bytes, 12)
        for bad in (b'G1 X1\nG1 X2\n', b'G1 X1?\n', b'G1 X1\rG1 X2\n', b'\x18\n'):
            with self.assertRaises(ValueError): p.enqueue(bad)

    async def test_realtime_can_interrupt_a_fragment_without_changing_line_bytes(self):
        p = self.make(capacity=3)
        p.enqueue(b'G1 X1\n'); await p.step()
        p.realtime(b'!'); await p.step()
        p.realtime(b'~'); await p.step()
        self.session.response = b'ok\n'; await p.step()
        self.assertEqual(self.session.writes, [b'G1 ', b'!', b'~', b'X1\n'])
        self.assertTrue(p.drained)

    async def test_long_job_over_real_byte_layer_with_counter_wraps_and_173_byte_capacity(self):
        class Device(FakeClient):
            def __init__(self):
                super().__init__(); self.partial = bytearray(); self.input = bytearray(); self.answer = bytearray()
            async def write_gatt_char(self, uuid, packet, response):
                if packet[3:5] != b'\x40\x03': return await super().write_gatt_char(uuid, packet, response)
                self.writes.append(packet)
                assert len(packet) <= 182
                assert int.from_bytes(packet[6:8], 'little') == self.w
                payload = packet[8:-1]; self.w = (self.w + len(payload)) & 65535
                self.input.extend(payload); self.partial.extend(payload)
                answer = bytearray()
                while b'\n' in self.partial:
                    index = self.partial.index(10); del self.partial[:index+1]
                    answer.extend(b'ok\n[MSG:temperature]\n')
                if answer:
                    self.data(bytes(answer)); self.answer.extend(answer)
        c=Device(); session=ByteStreamSession(c, lambda *a, **kw:None); await session.start()
        p=self.make(session, rx=1500, capacity=173, queue_bytes=16384)
        job=[b'G1 X0.123 Y0.456 F1000 S0\n']*6000
        submitted=0
        for _ in range(10000):
            while submitted<len(job):
                try: p.enqueue(job[submitted])
                except asyncio.QueueFull: break
                submitted+=1
            await p.step()
            if submitted==len(job) and p.drained: break
        self.assertTrue(p.drained)
        self.assertEqual(c.input, b''.join(job))
        self.assertEqual(self.visible, c.answer)
        self.assertEqual(self.visible.count(b'ok\n'), 6000)
        self.assertEqual(session.wcheck, (65534+len(c.input)) & 65535)
        self.assertEqual(session.rcheck, (65530+len(c.answer)) & 65535)
        self.assertEqual(sum(e['event']=='pipeline_terminal' for e in self.events),6000)
        await session.close()
