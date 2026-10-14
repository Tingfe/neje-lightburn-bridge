import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from controlled_grbl_pipeline import ControlledGrblPipeline
from controlled_job_policy import ControlledJobPolicy
from reviewed_job import ReviewedJobParser
from test_grbl_pipeline import DeferredSession


def status(state, x=1):
    return f'<{state}|MPos:{x},400,0,0|FS:0,0>\n'.encode()


class ControlledTests(unittest.IsolatedAsyncioTestCase):
    def make(self, *, rx=6, capacity=6):
        self.session = DeferredSession()
        self.visible, self.rows = bytearray(), []
        self.now = 0.
        async def deliver(data): self.visible.extend(data)
        return ControlledGrblPipeline(self.session, deliver,
            lambda event, **kw: self.rows.append(dict(event=event, **kw)),
            rx_budget=rx, payload_capacity=capacity, queue_bytes=48,
            clock=lambda: self.now)

    async def response(self, pipeline, data):
        self.session.response = data
        await pipeline.step(receive_only=True)

    async def held(self, pipeline):
        pipeline.realtime(b'!')
        await pipeline.step()
        self.session.response = status('Hold:0')
        await pipeline.step()
        self.assertEqual(pipeline.phase, 'holding')

    async def test_pause_bypasses_full_credit_and_stops_new_lines(self):
        p = self.make()
        p.enqueue(b'G1 X1\n'); p.enqueue(b'G1 X2\n')
        await p.step()
        for _ in range(8): p.realtime(b'?')
        p.realtime(b'!')
        self.assertFalse(p.ordinary_allowed)
        self.assertFalse(p.drained)
        await p.step()
        self.assertEqual(self.session.writes, [b'G1 X1\n', b'!'])
        self.assertEqual(p.occupied, 6)
        # Even if the old command now ACKs, no new ordinary line is forwarded.
        await self.response(p, b'ok\n')
        self.session.response = status('Hold:0'); await p.step()
        self.assertEqual(p.phase, 'holding')
        self.assertNotIn(b'G1 X2\n', self.session.writes)
        self.assertEqual(self.visible.count(b'ok\n'), 1)

    async def test_stale_status_and_hold1_do_not_confirm_hold(self):
        p = self.make()
        p.realtime(b'?'); await p.step()
        p.realtime(b'!'); self.session.response = status('Hold:0'); await p.step()
        self.assertEqual(p.phase, 'hold_requested')
        self.session.response = status('Hold:1'); await p.step()
        self.assertEqual(p.phase, 'hold_requested')
        self.now += .21
        self.session.response = status('Hold:0'); await p.step()
        self.assertEqual(p.phase, 'holding')
        self.assertEqual(self.session.writes, [b'?', b'!', b'?', b'?'])

    async def test_early_resume_waits_for_hold0_and_fresh_running_status(self):
        p = self.make()
        p.enqueue(b'G1 X1\n')
        p.realtime(b'!'); p.realtime(b'~')
        await p.step()
        self.session.response = status('Hold:1'); await p.step()
        self.assertNotIn(b'~', self.session.writes)
        self.now += .21
        self.session.response = status('Hold:0'); await p.step()
        self.assertEqual(p.phase, 'resume_requested')
        await p.step()
        self.assertEqual(self.session.writes[-1], b'~')
        self.assertNotIn(b'G1 X1\n', self.session.writes)
        self.session.response = status('Run'); await p.step()
        self.assertTrue(p.ordinary_allowed)
        await p.step()
        self.assertEqual(self.session.writes[-1], b'G1 X1\n')
        self.assertNotIn(b'ok\n', self.visible)

    async def test_pause_freezes_a_partially_attempted_line(self):
        p = self.make(capacity=3)
        p.enqueue(b'G1 X1\n'); await p.step()
        await self.held(p)
        self.assertEqual(p.partial.attempted, 3)
        self.assertEqual(p.occupied, 6)
        p.realtime(b'~'); await p.step()
        self.session.response = status('Run'); await p.step()
        self.session.response = b'ok\n'; await p.step()
        self.assertEqual(self.session.writes, [b'G1 ', b'!', b'?', b'~', b'?', b'X1\n'])
        self.assertTrue(p.drained)

    async def test_pause_during_inflight_write_allows_only_already_reserved_payload(self):
        p = self.make()
        entered, release = asyncio.Event(), asyncio.Event()
        async def write(wire):
            self.session.writes.append(wire)
            entered.set(); await release.wait()
            return b'ok\n'
        self.session.write = write
        p.enqueue(b'G1 X1\n'); p.enqueue(b'G1 X2\n')
        task = asyncio.create_task(p.step())
        await entered.wait()
        p.realtime(b'!')
        release.set(); await task
        self.assertFalse(p.ordinary_allowed)
        self.session.write = DeferredSession.write.__get__(self.session)
        await p.step()
        self.assertEqual(self.session.writes, [b'G1 X1\n', b'!'])
        self.assertEqual(len(p.queued), 1)

    async def test_healthy_hold_is_not_an_ordinary_ack_timeout(self):
        p = self.make()
        p.enqueue(b'G1 X1\n'); await p.step()
        await self.held(p)
        for _ in range(30):
            self.now += 2
            self.session.response = status('Hold:0')
            await p.step()
        self.assertGreater(self.now, 45)
        self.assertIsNone(p.fault)
        self.assertTrue(p.ordinary_timeout_suspended)
        self.assertEqual(len(p.pending), 1)
        self.assertEqual(self.visible.count(b'ok\n'), 0)

    async def test_missing_status_in_hold_is_bounded_and_no_replay(self):
        p = self.make(); await self.held(p)
        self.now += .3; await p.step()
        self.now += 8.1
        with self.assertRaises(TimeoutError): await p.step()
        self.assertIsNotNone(p.fault)
        with self.assertRaises(RuntimeError): p.realtime(b'~')
        self.assertNotIn(b'~', self.session.writes)

    async def test_control_transition_needs_more_than_ack_or_temperature(self):
        p = self.make(); p.enqueue(b'G1 X1\n'); await p.step()
        p.realtime(b'!'); self.session.response = b'o'; await p.step()
        self.session.response = b'k\n[MSG:temp]\n'; await p.step()
        self.assertEqual(p.phase, 'hold_requested')
        self.now += 8.1
        with self.assertRaises(TimeoutError): await p.step()
        self.assertFalse(p.ordinary_allowed)

    async def test_cancel_discards_unsent_and_banner_invalidates_pending_without_success(self):
        p = self.make(capacity=3)
        p.enqueue(b'G1 X1\n'); p.enqueue(b'G1 X2\n'); await p.step()
        p.realtime(b'!'); p.realtime(b'?'); p.realtime(b'\x18')
        self.assertFalse(p.queued)
        self.assertEqual(p.queued_bytes, 0)
        self.assertFalse(p.controls)
        self.assertFalse(p.ordinary_allowed)
        self.assertEqual(len(p.pending), 1)
        with self.assertRaises(RuntimeError): p.enqueue(b'G1 X3\n')
        p.realtime(b'~'); p.realtime(b'\x18')
        self.session.response = b"Grbl 1.1f ['$' for help]\n"
        await p.step()
        self.assertEqual(self.session.writes, [b'G1 ', b'\x18'])
        self.assertEqual(p.phase, 'cancel_wait_status')
        self.assertFalse(p.pending)
        self.assertIsNone(p.partial)
        self.assertEqual(p.occupied, 0)
        self.assertFalse(p.cancelled)
        self.session.response = status('Idle', x=.5); await p.step()
        self.assertTrue(p.cancelled)
        self.assertFalse(p.drained)  # No new job generation can reuse this instance.
        self.assertEqual(self.visible.count(b'ok\n'), 0)
        self.assertTrue(any(r['event']=='control_reset_banner' and
                            r['aborted_sequences']==[1] for r in self.rows))
        self.assertFalse(self.rows[-1]['position_trusted'])
        await p.step()
        self.assertEqual(self.session.writes.count(b'\x18'), 1)

    async def test_old_real_terminals_before_reset_are_accounted_as_old_commands(self):
        p = self.make(); p.enqueue(b'G1 X1\n'); p.enqueue(b'G1 X2\n'); await p.step()
        p.realtime(b'\x18')
        await self.response(p, b'error:20\n')
        self.assertEqual(p.phase, 'cancel_requested')
        self.assertFalse(p.pending)
        self.session.response = b'Grbl 1.1f\n'; await p.step()
        self.assertEqual(self.session.writes[-1], b'\x18')
        self.assertEqual(self.visible.count(b'error:20\n'), 1)
        self.assertEqual(sum(r['event']=='pipeline_terminal' for r in self.rows), 1)

    async def test_reset_alarm_is_visible_and_no_unlock_or_return(self):
        p = self.make(); p.realtime(b'\x18')
        self.session.response = b'ALARM:3\nGrbl 1.1f\n'; await p.step()
        self.session.response = status('Alarm', x=.6); await p.step()
        self.assertTrue(p.cancelled)
        self.assertEqual(self.session.writes, [b'\x18', b'?'])
        self.assertIn(b'ALARM:3\n', self.visible)
        self.assertIn(b'<Alarm|', self.visible)
        self.assertFalse(self.rows[-1]['position_trusted'])

    async def test_missing_reset_banner_cannot_be_success(self):
        p = self.make(); p.realtime(b'\x18'); await p.step()
        self.now += 8.1
        with self.assertRaises(TimeoutError): await p.step()
        self.assertFalse(p.cancelled)
        self.assertEqual(self.session.writes, [b'\x18'])

    async def test_reset_transport_failure_is_uncertain_and_not_retried(self):
        p = self.make(); p.realtime(b'\x18'); self.session.fail = True
        with self.assertRaises(ConnectionError): await p.step()
        with self.assertRaises(RuntimeError): await p.step()
        self.assertEqual(self.session.writes, [b'\x18'])
        self.assertFalse(p.cancelled)
        self.assertTrue(any(r['event']=='pipeline_stopped' and
                            r['uncertain_payload_hex']=='18' for r in self.rows))

    async def test_receive_only_never_forwards_a_queued_control(self):
        p = self.make(); p.realtime(b'!')
        await p.step(receive_only=True)
        self.assertEqual(self.session.writes, [])
        self.assertEqual(p.urgent, b'!')

    async def test_duplicate_controls_coalesce_and_new_pause_supersedes_unsent_resume(self):
        p = self.make(); await self.held(p)
        p.realtime(b'!'); p.realtime(b'~'); p.realtime(b'~'); p.realtime(b'!')
        await p.step()
        self.assertEqual(self.session.writes, [b'!', b'?', b'!'])
        self.assertNotIn(b'~', self.session.writes)

    async def test_unsolicited_reset_status_ack_and_resume_are_not_invented_success(self):
        for response in (b'Grbl 1.1f\n', b'ok\n', status('Idle')):
            p = self.make(); self.session.response = response
            with self.assertRaises(RuntimeError): await p.step()
            self.assertEqual(self.visible, response)
        p = self.make(); p.realtime(b'~'); await p.step()
        self.assertEqual(self.session.writes, [])

    async def test_late_ok_after_reset_banner_cannot_ack_new_generation(self):
        p = self.make(); p.enqueue(b'G1 X1\n'); await p.step()
        p.realtime(b'\x18'); self.session.response = b'Grbl 1.1f\nok\n'
        with self.assertRaises(RuntimeError): await p.step()
        self.assertFalse(p.cancelled)
        self.assertEqual(self.session.writes, [b'G1 X1\n', b'\x18'])

    async def test_unrequested_resume_from_confirmed_hold_fails_closed(self):
        p = self.make(); await self.held(p); self.now += .3
        self.session.response = status('Run')
        with self.assertRaises(RuntimeError): await p.step()
        self.assertFalse(p.ordinary_allowed)


class ControlledPolicyTests(unittest.TestCase):
    def test_preserves_partial_lines_around_controls(self):
        p = ControlledJobPolicy((b'M5', b'G1 X1'))
        self.assertEqual(p.feed(b'M5\r\nG1 !X'), [('job', b'M5\r\n'), ('hold', b'!')])
        self.assertEqual(p.feed(b'1~\n?'), [('resume', b'~'), ('job', b'G1 X1\n'), ('status', b'?')])
        p.eof()

    def test_reset_preempts_malformed_bytes_and_discards_partial_and_future_job(self):
        p = ControlledJobPolicy((b'M5', b'G1 X1'))
        p.feed(b'M5\nG1 ')
        self.assertEqual(p.feed(b'bad\x00\x18M3 S1000\n!~'), [('cancel', b'\x18')])
        self.assertFalse(p.pending)
        self.assertEqual(p.index, 1)
        self.assertEqual(p.feed(b'\x18G1 X1\n'), [])
        self.assertGreater(p.discarded_bytes, 0)
        p.eof()

    def test_original_policy_still_rejects_controls_and_repetition(self):
        for byte in (b'!', b'~', b'\x18'):
            with self.assertRaises(ValueError): ReviewedJobParser(job=()).feed(byte)
        p = ControlledJobPolicy((b'M5',))
        p.feed(b'M5\n')
        with self.assertRaises(ValueError): p.feed(b'M5\n')

    def test_non_cancel_incomplete_job_still_fails(self):
        p = ControlledJobPolicy((b'M5', b'G1 X1'))
        p.feed(b'M5\n!')
        with self.assertRaises(ValueError): p.eof()


class ControlledJobStatusTests(unittest.IsolatedAsyncioTestCase):
    def make(self):
        import types
        from collections import deque
        from controlled_reviewed_job import ControlledReviewedJob
        self.now = 0.
        self.pipeline = types.SimpleNamespace(cancelling=False, ordinary_allowed=True, drained=False,
                                              internal_results=deque())
        return ControlledReviewedJob(self.pipeline, None, lambda *a, **kw: None,
                                     clock=lambda: self.now)

    async def test_rest_speed_and_envelope_checked_even_before_job_submission(self):
        job = self.make()
        for report in ('<Hold:0|MPos:1,400,0,0|FS:1,0>',
                       '<Idle|MPos:0,400,0,0|FS:nan,0>',
                       '<Hold:0|MPos:11,400,0,0|FS:0,0>',
                       '<Idle|MPos:0,399,0,0|FS:0,0>'):
            with self.assertRaises(RuntimeError): job.check_status(report)
        job.check_status('<Hold:0|MPos:0,400,0,0|FS:0,0>')
        self.assertFalse(job.completed)

    async def test_completion_deadline_counts_active_time_and_excludes_long_hold(self):
        job = self.make()
        job.prepared, job.submitted = True, 14
        await job.advance()
        self.now = 20; await job.advance()
        self.pipeline.ordinary_allowed = False
        self.now = 100; await job.advance()
        self.pipeline.ordinary_allowed = True
        self.now = 120; await job.advance()
        self.now = 140; await job.advance()
        self.assertEqual(job.active_elapsed, 40)
        self.now = 146
        with self.assertRaises(TimeoutError): await job.advance()


if __name__ == '__main__': unittest.main()
