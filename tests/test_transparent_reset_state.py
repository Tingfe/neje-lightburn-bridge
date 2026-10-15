"""Deferred replies expose reset boundaries that an immediate fake ACK hides."""
import unittest

from test_grbl_pipeline import DeferredSession
from transparent_grbl_pipeline import TransparentGrblPipeline


class NativeResetStateTests(unittest.IsolatedAsyncioTestCase):
    def make(self, **kwargs):
        self.now = 0.
        self.session = DeferredSession()
        self.rows, self.visible = [], bytearray()
        async def deliver(data): self.visible.extend(data)
        return TransparentGrblPipeline(self.session, deliver,
            lambda event, **kw: self.rows.append(dict(event=event, **kw)),
            rx_budget=24, payload_capacity=3, queue_bytes=128,
            clock=lambda: self.now, **kwargs)

    async def test_real_homing_defers_status_until_terminal_then_requires_report(self):
        p = self.make(homing_timeout=20)
        p.enqueue(b'$H\n'); await p.step()
        self.assertEqual(self.session.writes, [b'$H\n'])
        self.assertEqual(p.homing_deadline, 20.)
        p.realtime(b'?'); await p.step()
        self.assertEqual(list(p.status_deadlines), [20.])
        self.now = 9.
        await p.step(receive_only=True)  # The real controller may be silent during Home.
        self.assertFalse(self.visible)
        self.now = 12.
        self.session.response = b'ok\n'; await p.step(receive_only=True)
        self.assertIsNone(p.homing_deadline)
        self.assertEqual(list(p.status_deadlines), [20.])
        self.now = 19.
        self.session.response = b'<Idle|MPos:0,400,0|FS:0,0>\n'
        await p.step(receive_only=True)
        self.assertTrue(p.drained)
        self.assertEqual(self.visible, b'ok\n<Idle|MPos:0,400,0|FS:0,0>\n')
        self.assertTrue(any(row['event'] == 'pipeline_homing_status_grace' for row in self.rows))

    async def test_homing_grace_is_bounded_and_reset_does_not_inherit_it(self):
        p = self.make(homing_timeout=20)
        p.enqueue(b'$H\n'); await p.step()
        p.realtime(b'?'); await p.step()
        self.now = 20.1
        with self.assertRaisesRegex(TimeoutError, 'homing exceeded'):
            await p.step(receive_only=True)

        p = self.make(homing_timeout=20)
        p.enqueue(b'$H\n'); await p.step()
        p.realtime(b'\x18'); await p.step()
        self.assertIsNone(p.homing_deadline)
        self.now = 8.1
        with self.assertRaisesRegex(TimeoutError, 'Control transition unconfirmed'):
            await p.step(receive_only=True)

    async def test_partial_old_job_is_aborted_and_new_query_waits_for_status(self):
        p = self.make()
        p.enqueue(b'G1 X1\n'); p.enqueue(b'G1 X2\n')
        await p.step()
        self.assertEqual(p.partial.attempted, 3)
        p.realtime(b'\x18')
        self.session.response = b'Grbl 1.1f [reset]\n'
        await p.step()
        self.assertEqual(p.occupied, 0)
        self.assertEqual(p.phase, 'cancel_wait_status')
        p.enqueue(b'$I\n')
        await p.step()
        self.assertEqual(self.session.writes, [b'G1 ', b'\x18', b'?'])
        self.session.response = b'<Idle|MPos:0.5,400,0|FS:0,0>\n'
        await p.step()
        self.assertEqual(p.phase, 'running')
        await p.step()
        self.session.response = b'[VER:1.1f.test]\nok\n'
        await p.step(receive_only=True)
        self.assertEqual(self.session.writes, [b'G1 ', b'\x18', b'?', b'$I\n'])
        self.assertEqual(self.visible.count(b'ok\n'), 1)  # Only the new real query.
        self.assertTrue(p.drained)
        aborted = next(row for row in self.rows if row['event'] == 'control_reset_banner')
        self.assertEqual(aborted['aborted_sequences'], [1])
        discarded = next(row for row in self.rows if row['event'] == 'control_unsent_discarded')
        self.assertEqual(discarded['lines'][0]['hex'], b'G1 X2\n'.hex())

    async def test_reset_without_banner_remains_a_fault(self):
        p = self.make(); p.realtime(b'\x18'); await p.step()
        self.now = 8.1
        with self.assertRaises(TimeoutError): await p.step()
        self.assertFalse(any(row['event'] == 'pipeline_native_reset_completed' for row in self.rows))
        self.assertEqual(self.session.writes, [b'\x18'])

    async def test_real_banner_without_idle_or_alarm_never_releases_new_input(self):
        p = self.make(); p.realtime(b'\x18')
        self.session.response = b'Grbl 1.1f [reset]\n'; await p.step()
        p.enqueue(b'$I\n')
        self.session.response = b'<Run|FS:60,0>\n'; await p.step()
        self.now = 8.1
        with self.assertRaises(TimeoutError): await p.step()
        self.assertNotIn(b'$I\n', self.session.writes)

    async def test_coalescing_does_not_extend_the_missing_report_deadline(self):
        p = self.make(); p.realtime(b'?'); await p.step()
        for second in (1., 3., 7.):
            self.now = second; p.realtime(b'?'); await p.step()
            self.assertEqual(list(p.status_deadlines), [8.])
            self.assertEqual(p.statuses, 1)
        self.now = 8.1
        with self.assertRaises(TimeoutError): await p.step()
        self.assertEqual(self.session.writes, [b'?'] * 4)
        self.assertFalse(self.visible)

    async def test_query_burst_can_also_return_multiple_real_reports(self):
        p = self.make()
        for _ in range(3): p.realtime(b'?'); await p.step()
        reports = b'<Idle|FS:0,0>\n<Run|FS:60,0>\n<Idle|FS:0,0>\n'
        self.session.response = reports; await p.step(receive_only=True)
        self.assertEqual(self.visible, reports)
        self.assertTrue(p.drained)
        observed = [row for row in self.rows if row['event'] == 'control_status_observed']
        self.assertEqual([row['matched_query'] for row in observed], [True, False, False])

    async def test_old_query_burst_during_reset_does_not_confirm_or_break_reset(self):
        p = self.make()
        for _ in range(3): p.realtime(b'?'); await p.step()
        p.realtime(b'\x18')
        old = b'<Idle|FS:0,0>\n' * 3
        self.session.response = old; await p.step()
        self.assertEqual(p.phase, 'reset_wait')
        self.assertFalse(any(row['event'] == 'pipeline_native_reset_completed' for row in self.rows))
        self.session.response = b'Grbl 1.1f [reset]\n'; await p.step(receive_only=True)
        self.assertEqual(p.phase, 'cancel_wait_status')
        self.session.response = b'<Alarm|FS:0,0>\n'; await p.step()
        self.assertEqual(p.phase, 'running')
        self.assertEqual(self.visible, old + b'Grbl 1.1f [reset]\n<Alarm|FS:0,0>\n')
