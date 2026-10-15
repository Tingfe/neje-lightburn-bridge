import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import neje_app


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        with patch.object(neje_app, 'LOG_ROOT', Path(self.folder.name)):
            self.messages = []
            self.supervisor = neje_app.Supervisor(self.messages.append)
        self.supervisor.last_log = Path(self.folder.name)/'capture.jsonl'
        self.supervisor.deadline = time.time()+3600
        self.supervisor._session_start = time.time()-10
        self.supervisor._remember_address = Mock(return_value=None)
        self.supervisor._log_has = Mock(return_value=False)
        self.supervisor._spawn = Mock()

    def finish(self, verdict, *, exit_code=0, restart_safe=False, cancelled=False):
        s = self.supervisor
        s.process = Mock()
        s.process.poll.return_value = exit_code
        s._log_has.return_value = cancelled
        def result(path):
            s.audit_details = dict(restart_safe=restart_safe)
            return verdict
        s._audit = Mock(side_effect=result)
        s.tick()

    def test_failed_pending_or_missing_audit_never_restarts(self):
        for verdict in (None, ('fail', 'bad', ''), ('pending', 'unknown', '')):
            with self.subTest(verdict=verdict):
                self.supervisor.deadline = time.time()+3600
                self.finish(verdict, restart_safe=True)
                self.assertIsNone(self.supervisor.restart_at)
                self.assertTrue(self.supervisor.recovery_required)
                self.supervisor._spawn.assert_not_called()

    def test_process_failure_even_with_audit_pass_never_restarts(self):
        self.finish(('pass', 'ok', ''), exit_code=1, restart_safe=True)
        self.assertIsNone(self.supervisor.restart_at)

    def test_route_cleanup_without_recent_idle_does_not_rearm(self):
        self.finish(('pass', 'ok', ''), restart_safe=False)
        self.assertIsNone(self.supervisor.restart_at)

    def test_confirmed_reset_requires_explicit_recovery(self):
        self.finish(('pass', 'ok', ''), restart_safe=True, cancelled=True)
        self.assertIsNone(self.supervisor.restart_at)

    def test_only_normal_verified_idle_session_can_restart(self):
        self.finish(('pass', 'ok', ''), restart_safe=True)
        self.assertIsNotNone(self.supervisor.restart_at)
        self.supervisor.restart_at = time.time()-1
        with patch.object(neje_app, 'port_in_use', return_value=False):
            self.supervisor.tick()
        self.supervisor._spawn.assert_called_once()

    def test_short_clean_session_rearms_without_fault_backoff(self):
        self.supervisor._session_start = time.time() - 1
        self.finish(('pass', 'ok', ''), restart_safe=True)
        self.assertLess(self.supervisor.restart_at - time.time(), 2)
        self.assertFalse(self.supervisor.recovery_required)

    def test_stop_targets_owned_process_and_waits_for_confirmation(self):
        s = self.supervisor
        s.process = Mock()
        s.process.poll.return_value = None
        s._log_has.return_value = True
        with patch.object(neje_app.subprocess, 'Popen') as spawn:
            self.assertTrue(s.request_stop())
            spawn.assert_not_called()
        s.process.send_signal.assert_called_once_with(signal.SIGUSR1)
        self.assertTrue(s.stop_confirmation_pending)
        self.assertFalse(any('已发送急停' in message for message in self.messages))
        self.assertEqual(s.deadline, 0)
        s._log_has.return_value = True
        s.tick()
        self.assertFalse(s.stop_confirmation_pending)
        self.assertIn('真实 Idle/Alarm', self.messages[-1])

    def test_no_link_and_signal_failure_never_claim_device_stopped(self):
        self.assertFalse(self.supervisor.request_stop())
        self.supervisor.process = Mock()
        self.supervisor.process.poll.return_value = None
        self.supervisor._log_has.return_value = True
        self.supervisor.process.send_signal.side_effect = ProcessLookupError('gone')
        self.assertFalse(self.supervisor.request_stop())
        self.assertTrue(all('停止未确认' in message for message in self.messages))

    def test_stop_during_discovery_never_sends_an_unhandled_signal(self):
        s = self.supervisor
        s.process = Mock()
        s.process.poll.return_value = None
        self.assertFalse(s.request_stop())
        s.process.send_signal.assert_not_called()
