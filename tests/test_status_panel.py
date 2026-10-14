import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from status_panel import summarize, render, INTERESTING


def make_session(root, name='20260101T000000Z-abcdef01', state='completed'):
    directory = Path(root) / name
    directory.mkdir(parents=True)
    (directory / 'session.json').write_text(json.dumps(dict(
        schema=1, state=state, session_id=name, directory=str(directory),
        started_utc='2026-01-01T00:00:00+00:00', finished_utc='2026-01-01T00:01:00+00:00',
        error=None, requires_explicit_recovery=state == 'requires_recovery',
        automatic_reconnect=False,
        plan=dict(job=dict(geometry_profile='xy-linear-zero', sha256='a' * 64,
                           command_count=18, power=0, power_ceiling=0,
                           path_distance_mm='40', nominal_motion_seconds='40',
                           x_range=[0.0, 10.0], y_range=[390.0, 400.0])),
        audit=dict(passed=True, verified_bytes=1234, decoded_frames=99,
                   no_synthetic_ack=True, no_replay=True,
                   cleanup=dict(way=0, final_app=3)))))
    rows = [
        dict(event='pipeline_job_submitted', utc='2026-01-01T00:00:10+00:00', hex='4d350a'),
        dict(event='noise_not_interesting', utc='2026-01-01T00:00:11+00:00'),
        dict(event='controlled_job_completed', utc='2026-01-01T00:00:50+00:00', commands=18),
        dict(event='cleanup_verified', utc='2026-01-01T00:01:00+00:00', way=0, final_app=3),
    ]
    (directory / 'bridge.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    return directory


class PanelTests(unittest.TestCase):
    def test_summary_reports_record_without_touching_the_device(self):
        with tempfile.TemporaryDirectory() as root:
            make_session(root)
            summary = summarize(root)
            self.assertTrue(summary['found'])
            self.assertEqual(summary['state'], 'completed')
            self.assertEqual(summary['state_zh'], '已完成')
            self.assertEqual(summary['job']['commands'], 18)
            self.assertEqual(summary['job']['power'], 0)
            self.assertTrue(summary['audit']['passed'])
            self.assertEqual(summary['audit']['cleanup']['way'], 0)
            # The panel must never claim device access or a bypassed gate.
            self.assertFalse(summary['device_accessed_by_this_panel'])
            self.assertFalse(summary['execution_gates_bypassed'])

    def test_only_interesting_events_are_surfaced(self):
        with tempfile.TemporaryDirectory() as root:
            make_session(root)
            summary = summarize(root)
            names = [e['event'] for e in summary['recent_events']]
            self.assertIn('pipeline_job_submitted', names)
            self.assertIn('cleanup_verified', names)
            self.assertNotIn('noise_not_interesting', names)
            self.assertTrue(all(n in INTERESTING for n in names))

    def test_empty_root_and_missing_root_are_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as root:
            summary = summarize(root)
            self.assertFalse(summary['found'])
            self.assertIn('不连接设备', summary['note'])
        self.assertFalse(summarize('/definitely/not/here')['found'])

    def test_render_marks_recovery_and_audit_state(self):
        with tempfile.TemporaryDirectory() as root:
            make_session(root, state='requires_recovery')
            page = render(summarize(root))
            self.assertIn('状态面板', page)
            self.assertIn('需要显式恢复', page)
            self.assertIn('passed', page)
            self.assertIn('location.reload', page)

    def test_panel_module_pulls_in_no_device_transport(self):
        for name in ('bleak', 'serial'):
            self.assertNotIn(name, sys.modules,
                             f'{name} must not be imported by the status panel')


if __name__ == '__main__':
    unittest.main()
