import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from neje import build_command, DEFAULT_SECONDS

BRIDGE_FLAGS = ('--transparent', '--allow-transparent-bridge', '--execute',
                '--allow-initialization')


class OneCommandLauncherTests(unittest.TestCase):
    """One command, no configuration: every setting must already be chosen."""

    def test_default_command_starts_full_functionality(self):
        command, log = build_command()
        for flag in BRIDGE_FLAGS:
            self.assertIn(flag, command)
        self.assertIn('--laser-output-authorized', command)
        self.assertNotIn('--laser-power-isolated', command)
        self.assertIn('--seconds', command)
        self.assertEqual(command[command.index('--seconds') + 1], str(DEFAULT_SECONDS))
        self.assertTrue(str(log).endswith('.jsonl'))

    def test_test_mode_isolates_the_laser(self):
        command, _ = build_command(test=True)
        self.assertIn('--laser-power-isolated', command)
        self.assertNotIn('--laser-output-authorized', command)

    def test_exactly_one_laser_attestation_is_produced(self):
        for test in (False, True):
            command, _ = build_command(test=test)
            attestations = [f for f in command
                            if f in ('--laser-power-isolated', '--laser-output-authorized')]
            self.assertEqual(len(attestations), 1)

    def test_the_session_outlasts_a_working_day(self):
        self.assertGreaterEqual(DEFAULT_SECONDS, 8 * 3600 - 3600)

    def test_generated_command_is_accepted_by_the_bridge_parser(self):
        import pipeline_tcp_bridge as bridge
        command, _ = build_command()
        parsed = bridge.parse_args(command[2:])
        self.assertTrue(parsed.transparent)
        self.assertEqual(parsed.seconds, DEFAULT_SECONDS)
        self.assertTrue(parsed.laser_output_authorized)

    def test_launcher_pulls_in_no_device_transport(self):
        for name in ('bleak', 'serial'):
            self.assertNotIn(name, sys.modules)

    def test_double_click_entry_exists_and_is_executable(self):
        entry = Path(__file__).resolve().parents[1] / 'neje.command'
        self.assertTrue(entry.exists())
        self.assertTrue(entry.stat().st_mode & 0o111)
        self.assertIn('tools/neje.py', entry.read_text())


class SelfAuditTests(unittest.TestCase):
    """One-command use must also verify itself; the operator should not have to."""

    def write(self, rows):
        import json as _json
        import tempfile
        handle = tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False)
        for row in rows:
            handle.write(_json.dumps(row) + '\n')
        handle.close()
        return handle.name

    def test_a_session_with_no_client_says_so_instead_of_passing_silently(self):
        from neje import audit_after
        import io
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = audit_after(self.write([dict(event='pipeline_listening')]))
        self.assertEqual(code, 0)
        self.assertIn('没有客户端连入过', out.getvalue())

    def test_a_faithful_session_audits_clean(self):
        from neje import audit_after
        import io
        import contextlib
        rows = [
            dict(event='pipeline_tcp_connected'),
            dict(event='pipeline_transparent_mode', usb_equivalent=True,
                 reviewed_job_required=False, whitelist=False),
            dict(event='pipeline_tcp_received', hex=b'?\n'.hex()),
            dict(event='pipeline_ingress', kind='status', hex=b'?'.hex()),
            dict(event='pipeline_ingress', kind='ordinary', hex=b'\n'.hex()),
            dict(event='pipeline_write_attempt', hex=b'?\n'.hex()),
            dict(event='stream_write_reserved', hex=b'?\n'.hex(), before=0, after=2),
            dict(event='stream_rx_staged', hex=b'ok\n'.hex()),
            dict(event='pipeline_tcp_delivered', hex=b'ok\n'.hex()),
            dict(event='pipeline_transmission_stopped', queued_bytes=0, staged_bytes=0,
                 no_replay=True),
            dict(event='cleanup_verified', way=0, final_app=3),
        ]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = audit_after(self.write(rows))
        self.assertEqual(code, 0)
        self.assertIn('审计通过', out.getvalue())

    def test_a_fabricated_reply_fails_the_session(self):
        from neje import audit_after
        import io
        import contextlib
        rows = [
            dict(event='pipeline_tcp_connected'),
            dict(event='pipeline_transparent_mode', usb_equivalent=True,
                 reviewed_job_required=False, whitelist=False),
            dict(event='pipeline_tcp_received', hex=b'?\n'.hex()),
            dict(event='pipeline_ingress', kind='status', hex=b'?'.hex()),
            dict(event='pipeline_ingress', kind='ordinary', hex=b'\n'.hex()),
            dict(event='pipeline_write_attempt', hex=b'?\n'.hex()),
            dict(event='stream_write_reserved', hex=b'?\n'.hex(), before=0, after=2),
            dict(event='stream_rx_staged', hex=b'ok\n'.hex()),
            dict(event='pipeline_tcp_delivered', hex=b'ALARM:1\n'.hex()),
            dict(event='pipeline_transmission_stopped', queued_bytes=0, staged_bytes=0,
                 no_replay=True),
        ]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = audit_after(self.write(rows))
        self.assertEqual(code, 1)
        self.assertIn('审计未通过', out.getvalue())


class RepeatLaunchTests(unittest.TestCase):
    """Double-clicking twice must not produce a raw bind traceback."""

    def test_port_probe_reports_a_bound_port(self):
        import socket
        from neje import port_in_use
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        port = server.getsockname()[1]
        server.listen(1)
        try:
            self.assertTrue(port_in_use(port))
        finally:
            server.close()
        self.assertFalse(port_in_use(port))

    def test_second_launch_tells_the_operator_the_link_is_already_up(self):
        # Never bind the real port: a live bridge may be using it. Inject the probe.
        import io
        import contextlib
        import neje
        original = neje.port_in_use
        neje.port_in_use = lambda port=4002: True
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                neje.main.__wrapped__ if False else None
                import sys as _sys
                saved = _sys.argv
                _sys.argv = ['neje.py']
                try:
                    code = neje.main()
                finally:
                    _sys.argv = saved
            self.assertEqual(code, 0)
            self.assertIn('已有桥接在运行', out.getvalue())
        finally:
            neje.port_in_use = original

if __name__ == '__main__':
    unittest.main()
