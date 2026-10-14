import asyncio
from pathlib import Path
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from controlled_grbl_pipeline import ControlledGrblPipeline
from controlled_reviewed_job import ControlledReviewedJob
from controlled_usb_probe import ControlledUSBSession, run_review
from reviewed_ten_mm import wire_lines, TEN_MM_JOB
from test_grbl_pipeline import DeferredSession
from test_ten_mm_runtime import Session, MODAL


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    def make(self, session=None):
        self.session, self.visible, self.rows = session or DeferredSession(), bytearray(), []
        async def deliver(data): self.visible.extend(data)
        return ControlledGrblPipeline(self.session, deliver,
            lambda event, **kw: self.rows.append(dict(event=event, **kw)),
            rx_budget=1492, payload_capacity=173, deliver_lines=True)

    async def test_private_split_modal_and_ack_preserve_async_public_output(self):
        p = self.make()
        seq = p.enqueue_internal(b'$G\n', 'modal')
        self.session.response = b'[GC:G1 G54 G21 G90 G94 M5 S0]\no'
        await p.step()
        self.assertEqual(self.visible, b'')
        p.realtime(b'?')
        self.session.response = b'k\n[MSG:temperature]\n<Idle|FS:0,0>\n'
        await p.step()
        self.assertEqual(self.visible, b'[MSG:temperature]\n<Idle|FS:0,0>\n')
        result = p.internal_results.popleft()
        self.assertEqual(result['sequence'], seq)
        self.assertEqual(bytes.fromhex(result['hex']), b'[GC:G1 G54 G21 G90 G94 M5 S0]\nok\n')
        self.assertTrue(p.drained)

    async def test_public_line_cannot_batch_across_private_restore_barrier(self):
        p = self.make()
        p.enqueue_internal(b'G0\n', 'restore')
        p.enqueue(b'G0\n')
        self.session.response = b'ok\n'; await p.step()
        self.assertEqual(self.session.writes, [b'G0\n'])
        self.assertEqual(self.visible, b'')
        p.internal_results.popleft()
        self.session.response = b'ok\n'; await p.step()
        self.assertEqual(self.visible, b'ok\n')
        self.assertEqual(len([row for row in self.rows if row['event']=='pipeline_terminal']), 2)

    async def test_error_from_internal_query_is_real_visible_failure(self):
        p = self.make(); p.enqueue_internal(b'$G\n', 'modal')
        self.session.response = b'error:3\n'
        with self.assertRaises(RuntimeError): await p.step()
        self.assertEqual(self.visible, b'error:3\n')
        self.assertFalse(p.internal_results)
        self.assertIsNotNone(p.fault)

    async def test_cancel_during_internal_query_does_not_restore_or_publish_fake_ok(self):
        p = self.make(); p.enqueue_internal(b'$G\n', 'modal')
        self.session.response = b'[GC:G1 G54 G21 G90 G94 M5 S0]\n'; await p.step()
        p.realtime(b'\x18')
        self.session.response = b'Grbl 1.1f\n'; await p.step()
        self.session.response = b'<Idle|FS:0,0>\n'; await p.step()
        self.assertTrue(p.cancelled)
        self.assertIsNone(p.internal)
        self.assertFalse(p.internal_results)
        self.assertEqual(self.session.writes, [b'$G\n', b'\x18', b'?'])
        self.assertNotIn(b'ok\n', self.visible)
        self.assertTrue(any(row['event']=='pipeline_internal_aborted' for row in self.rows))

    async def test_hold_status_stays_public_while_internal_modal_response_is_pending(self):
        p = self.make(); p.enqueue_internal(b'$G\n', 'modal')
        self.session.response = b'[GC:G1 G54 G21 G90 G94 M5 S0]\n'; await p.step()
        p.realtime(b'!'); await p.step()
        self.session.response = b'<Hold:0|FS:0,0>\n'; await p.step()
        self.assertEqual(p.phase, 'holding')
        self.assertEqual(self.visible, b'<Hold:0|FS:0,0>\n')
        self.assertEqual(len(p.pending), 1)
        self.assertFalse(p.internal_results)
        self.assertEqual(self.session.writes, [b'$G\n', b'!', b'?'])

    async def test_eof_during_private_modal_never_sends_restore(self):
        p = self.make(); p.enqueue_internal(b'$G\n', 'modal')
        self.session.response = b'[GC:G1 G54 G21 G90 G94 M5 S0]\n'; await p.step()
        self.session.response = b'ok\n'; await p.step(receive_only=True)
        self.assertEqual(self.session.writes, [b'$G\n'])
        self.assertFalse(p.pending)
        self.assertEqual(self.visible, b'')
        self.assertEqual(len(p.internal_results), 1)

    async def test_internal_policy_rejects_motion_and_nonexclusive_or_fragment_output(self):
        p = self.make()
        with self.assertRaises(ValueError): p.enqueue_internal(b'G1 X1\n', 'invalid')
        p.enqueue(b'G0\n')
        with self.assertRaises(RuntimeError): p.enqueue_internal(b'$G\n', 'not-drained')
        p = self.make(); p.deliver_lines = False
        with self.assertRaises(ValueError): p.enqueue_internal(b'$G\n', 'unframed')

    async def test_full_completion_verifies_modal_restore_and_final_state_with_only_job_acks_visible(self):
        p = self.make(Session())
        job = ControlledReviewedJob(p, None, lambda event, **kw:
            self.rows.append(dict(event=event, **kw)))
        job.prepared = True; p.check_status = job.check_status
        for wire in wire_lines(): job.enqueue('job', wire)
        for _ in range(30):
            await job.advance(); await p.step()
            if job.completed: break
        self.assertTrue(job.completed)
        self.assertEqual(job.completion_stage, 'complete')
        self.assertEqual(self.visible.count(b'ok\n'), 14)
        self.assertNotIn(b'[GC:', self.visible)
        self.assertEqual(self.session.writes[-5:], [b'?', b'$G\n', b'G0\n', b'$G\n', b'?'])
        self.assertTrue(any(row['event']=='controlled_job_completed' and
                            row['modal_verified'] and row['rapid_mode_restored'] for row in self.rows))

    async def test_bad_modal_never_reaches_restore_or_completed(self):
        class WrongModal(Session):
            async def write(self, wire):
                if wire == b'$G\n':
                    self.writes.append(wire)
                    return b'[GC:G1 G21 G90 G94 G54 M4 S10]\nok\n'
                return await super().write(wire)
        p = self.make(WrongModal())
        job = ControlledReviewedJob(p, None, lambda *a, **kw: None)
        job.prepared = True; p.check_status = job.check_status
        for wire in wire_lines(): job.enqueue('job', wire)
        with self.assertRaises(RuntimeError):
            for _ in range(20): await job.advance(); await p.step()
        self.assertFalse(job.completed)
        self.assertNotIn(b'G0\n', self.session.writes)

    async def test_restore_requires_actual_g0_modal_not_only_real_ok(self):
        with self.assertRaises(RuntimeError):
            ControlledReviewedJob.require_modal('[GC:G1 G54 G21 G90 G94 M5 S0]\nok\n', rapid=True)
        for text in ('ok\n', MODAL.decode()+MODAL.decode()):
            with self.assertRaises(RuntimeError): ControlledReviewedJob.require_modal(text)


class USBPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_usb_default_refuses_controls_startup_and_motion(self):
        session = ControlledUSBSession(lambda *a, **kw: None)
        session.backend.port = types.SimpleNamespace(write=lambda data: len(data), read=lambda n: b'')
        for wire in (b'!', b'~', b'\x18', b'$N\n', b'M5\n'):
            with self.assertRaises(ValueError): await session.write(wire)
        with self.assertRaises(ValueError): ControlledUSBSession(print, allow_controls=True)

    async def test_usb_exact_job_with_interleaved_controls_then_permanent_reset_gate(self):
        writes = []
        session = ControlledUSBSession(lambda *a, **kw: None, allow_job=True, allow_controls=True)
        def write(data): writes.append(data); return len(data)
        session.backend.port = types.SimpleNamespace(write=write, read=lambda n: b'')
        await session.write(b'$N\n')
        job = b''.join(wire_lines())
        await session.write(job[:7]); await session.write(b'!'); await session.write(b'~')
        await session.write(job[7:]); await session.write(b'$G\n'); await session.write(b'G0\n')
        await session.write(b'\x18'); await session.write(b'?')
        for wire in (b'~', b'!', b'\x18', b'G0\n', b'$G\n'):
            with self.assertRaises(RuntimeError): await session.write(wire)
        self.assertEqual(session.attempted, len(job))
        self.assertEqual(writes.count(b'\x18'), 1)

    async def test_usb_control_acceptance_failure_never_retries(self):
        session = ControlledUSBSession(lambda *a, **kw: None, allow_job=True, allow_controls=True)
        writes = []
        def write(data): writes.append(data); raise ConnectionError('Device accepted then port disappeared')
        session.backend.port = types.SimpleNamespace(write=write, read=lambda n: b'')
        with self.assertRaises(ConnectionError): await session.write(b'\x18')
        with self.assertRaises(RuntimeError): await session.write(b'\x18')
        self.assertEqual(writes, [b'\x18'])


class USBRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_control_core_completes_pause_resume_full_finish_and_idle_reset(self):
        from test_controlled_tcp import Controller
        rows, now = [], types.SimpleNamespace(value=0.)
        class Device(Controller):
            def __init__(self):
                super().__init__(); self.closed = False
            async def write(self, wire):
                if wire == b'\x18':
                    self.writes.append(wire); self.state = 'Idle'
                    return b'Grbl 1.1f\n'
                return await super().write(wire)
            async def verify(self):
                if self.deferred_m2 and b'~' in self.writes and self.state == 'Run':
                    self.deferred_m2 = False; self.state, self.x = 'Idle', 0
                    return b'ok\n'
                return b''
        device = Device()
        async def sleep(seconds): now.value += max(.1, seconds)
        await run_review(device, lambda event, **kw: rows.append(dict(event=event, **kw)),
                         clock=lambda: now.value, sleep=sleep)
        self.assertTrue(device.closed)
        self.assertEqual(device.writes.count(b'!'), 1)
        self.assertEqual(device.writes.count(b'~'), 1)
        self.assertEqual(device.writes.count(b'\x18'), 1)
        complete = next(i for i, row in enumerate(rows) if row['event']=='controlled_job_completed')
        reset = next(i for i, row in enumerate(rows) if row['event']=='usb_review_idle_reset_requested')
        self.assertLess(complete, reset)
        self.assertTrue(any(row['event']=='usb_control_review_passed' for row in rows))
        public = b''.join(bytes.fromhex(row['hex']) for row in rows if row['event']=='usb_control_visible')
        self.assertEqual(public.count(b'ok\n'), 14)

    async def test_nonempty_startup_closes_without_job_or_reset(self):
        from test_controlled_tcp import Controller
        device = Controller(startup=b'$N0=G1X10\n$N1=\nok\n')
        with self.assertRaises(RuntimeError): await run_review(device, lambda *a, **kw: None)
        self.assertTrue(device.closed)
        self.assertEqual(device.job_count, 0)
        self.assertNotIn(b'\x18', device.writes)


if __name__ == '__main__': unittest.main()
