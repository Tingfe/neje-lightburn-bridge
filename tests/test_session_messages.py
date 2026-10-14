"""User-facing messages must preserve real failure and audit boundaries."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import lightburn_session as launcher


class MessageTests(unittest.TestCase):
    def test_real_motion_disconnect_remains_unknown_even_after_route_cleanup(self):
        capture=ROOT/'evidence/job-disconnect-20261013/verified-run/ble-job-disconnect-02.jsonl'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'fault';folder.mkdir()
            (folder/'bridge.jsonl').write_bytes(capture.read_bytes())
            (root/'state.json').write_text(json.dumps(dict(state='requires_recovery',directory=str(folder))))
            original=(root/'state.json').read_bytes()
            view=launcher.status(root);message=launcher.status_message(view)
            self.assertIn('尚未确认设备停止',message)
            self.assertIn('勿直接重发',message)
            self.assertNotIn('审计通过',message)
            self.assertEqual((root/'state.json').read_bytes(),original)

    def test_job_end_and_exit_zero_without_audit_never_claim_success(self):
        for view in [dict(state='running',observed_stage='job_completed_waiting_for_disconnect'),
                     dict(state='running',observed_stage='bridge_stopped'),
                     dict(state='completed',job_completed=True,bridge_returncode=0)]:
            message=launcher.status_message(view)
            self.assertNotIn('已通过',message)
            self.assertIn('审计',message)
        for view in [dict(state='running',observed_stage='ready',requires_explicit_recovery=True),
                     dict(state='completed',job_completed=True,audit=dict(passed=True),
                          observed_fault=dict(event='receive_fault'))]:
            self.assertIn('会话异常',launcher.status_message(view))

    def test_success_distinguishes_initialization_from_audited_job(self):
        initial=launcher.status_message(dict(state='completed',audit=dict(passed=True),job_completed=False))
        self.assertIn('没有执行作业',initial)
        self.assertIn('作业和关闭',launcher.status_message(dict(state='completed',
            audit=dict(passed=True),job_completed=True)))
        self.assertIn('不能据此确认',launcher.status_message(dict(state='running',
            observed_stage='ready',incomplete_capture_tail=True)))

    def test_cli_message_is_read_only_and_default_status_creates_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'absent';output=io.StringIO()
            with contextlib.redirect_stdout(output):
                code=launcher.main(['status','--session-root',str(root)])
            self.assertEqual(code,0);self.assertFalse(root.exists())
            self.assertEqual(json.loads(output.getvalue())['message'],'尚未建立管理会话。')


if __name__=='__main__':unittest.main()
