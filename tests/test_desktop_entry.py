import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import neje
import neje_desktop
import neje_app


class DesktopEntryTests(unittest.TestCase):
    def test_bridge_child_dispatches_without_constructing_a_gui(self):
        with patch.object(sys, 'argv', ['NEJE Bridge', neje.INTERNAL_BRIDGE]), \
             patch.object(neje, '_run_bridge_in_process', return_value=7) as bridge, \
             patch.object(neje_app, 'main') as gui:
            self.assertEqual(neje_desktop.main(), 7)
        bridge.assert_called_once()
        gui.assert_not_called()

    def test_probe_child_dispatches_without_constructing_a_gui(self):
        with patch.object(sys, 'argv', ['NEJE Bridge', neje.INTERNAL_MODE_CHECK]), \
             patch.object(neje, '_run_mode_check_in_process', return_value=3) as probe, \
             patch.object(neje_app, 'main') as gui:
            self.assertEqual(neje_desktop.main(), 3)
        probe.assert_called_once()
        gui.assert_not_called()

    def test_normal_entry_opens_gui_without_starting_bridge(self):
        with patch.object(sys, 'argv', ['NEJE Bridge']), \
             patch.object(neje_app, 'main', return_value=0) as gui, \
             patch.object(neje, '_run_bridge_in_process') as bridge:
            self.assertEqual(neje_desktop.main(), 0)
        gui.assert_called_once()
        bridge.assert_not_called()

    def test_diagnostics_route_is_offline(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'report.json'
            with patch.object(sys, 'argv', ['NEJE Bridge', '--diagnostics', '--report', str(path)]), \
                 patch.object(neje_app, 'main') as gui:
                self.assertEqual(neje_desktop.main(), 0)
            row = json.loads(path.read_text())
        self.assertTrue(row['offline_only'])
        self.assertFalse(row['device_connected'])
        self.assertTrue(row['tcl'])
        gui.assert_not_called()


class DesktopLifecycleTests(unittest.TestCase):
    def app(self):
        app = neje_app.App.__new__(neje_app.App)
        app.closing = False
        app.closed = Mock()
        app.root = Mock()
        app.supervisor = Mock()
        app.log = Mock()
        app.supervisor.running.return_value = False
        app.supervisor.device = 'known-device'
        app.supervisor.recovery_required = False
        app.confirmed_mode = None
        app.pending_start = None
        return app

    def test_close_waits_for_owned_child_cleanup_before_destroying_window(self):
        app = self.app()
        app.supervisor.running.return_value = True
        app.on_close()
        app.supervisor.stop.assert_called_once()
        app.root.destroy.assert_not_called()
        app.root.after.assert_called_once_with(100, app._finish_close)
        app.supervisor.running.return_value = False
        app._finish_close()
        app.closed.set.assert_called_once()
        app.root.destroy.assert_called_once()

    def test_duplicate_close_does_not_terminate_twice(self):
        app = self.app()
        app.supervisor.running.return_value = True
        app.on_close(); app.on_close()
        app.supervisor.stop.assert_called_once()

    def test_default_start_has_no_hour_limit_and_confirmation_is_not_persisted(self):
        app = self.app()
        with patch.object(neje_app.messagebox, 'askokcancel', return_value=True) as confirm:
            app.on_test()
            app.on_test()
        self.assertEqual(confirm.call_count, 1)
        app.supervisor.start.assert_called_with(test=True)
        self.assertTrue(app.confirmed_mode)

    def test_isolation_test_cancellation_does_not_assert_isolation_or_spawn(self):
        app = self.app()
        with patch.object(neje_app.messagebox, 'askokcancel', return_value=False):
            app.on_test()
        app.supervisor.start.assert_not_called()

    def test_first_rearm_cannot_bypass_initial_full_mode_confirmation(self):
        app = self.app()
        app.supervisor.sessions = 0
        app.on_start = Mock()
        app.on_rearm()
        app.on_start.assert_called_once()
        app.supervisor.rearm.assert_not_called()

    def test_start_without_a_device_waits_for_scan_not_a_blind_connection(self):
        app = self.app()
        app.supervisor.device = None
        app.on_scan = Mock()
        app.on_start()
        app.on_scan.assert_called_once()
        app.supervisor.start.assert_not_called()
        self.assertIs(app.pending_start, False)

    def test_multiple_devices_require_explicit_selection(self):
        app = self.app()
        app.supervisor.device = None
        app.pending_start = False
        app.picker = Mock(); app.selected = Mock(); app.show_advanced = Mock()
        app.toggle_panels = Mock(); app._start_mode = Mock()
        app._scan_finished([('NEJE one', 'A'), ('NEJE two', 'B')])
        self.assertIsNone(app.supervisor.device)
        self.assertIsNone(app.pending_start)
        app._start_mode.assert_not_called()
        app.show_advanced.set.assert_called_once_with(True)

    def test_unique_device_continues_the_users_pending_start(self):
        app = self.app()
        app.supervisor.device = None
        app.pending_start = False
        app.picker = Mock(); app.pick = Mock(); app.on_pick = Mock(); app._start_mode = Mock()
        app._scan_finished([('NEJE one', 'A'), ('Phone', 'B')])
        app.pick.set.assert_called_once_with('NEJE one  A')
        app.on_pick.assert_called_once()
        app._start_mode.assert_called_once_with(False)

    def test_stop_cancels_a_pending_start_during_scan(self):
        app = self.app()
        app.pending_start = False
        app.on_stop()
        self.assertIsNone(app.pending_start)

    def test_failed_scan_clears_pending_start_and_shows_retry(self):
        app = self.app()
        app.supervisor.device = None
        app.pending_start = False; app.scanning = True
        app.selected = Mock(); app.show_advanced = Mock(); app.toggle_panels = Mock()
        app._scan_failed('蓝牙初始化或扫描超时（14 秒）')
        self.assertFalse(app.scanning)
        self.assertIsNone(app.pending_start)
        app.selected.set.assert_called_once_with('设备：扫描未完成')
        app.show_advanced.set.assert_called_once_with(True)
        app.supervisor.start.assert_not_called()

    def test_fault_reconnect_declined_never_starts(self):
        app = self.app()
        app.supervisor.recovery_required = True
        with patch.object(neje_app.messagebox, 'askokcancel', return_value=False):
            app.on_start()
        app.supervisor.start.assert_not_called()

    def test_creation_never_closes_a_running_lightburn_document(self):
        app = self.app()
        with patch.object(neje_app.device_setup, 'add_tcp_device', return_value=(True, 'dry')), \
             patch.object(neje_app.device_setup, 'lightburn_running', return_value=True), \
             patch.object(neje_app.device_setup, 'quit_lightburn') as quit_app, \
             patch.object(neje_app.messagebox, 'showinfo'):
            app.on_create_device()
        quit_app.assert_not_called()

    def test_help_uses_existing_configuration_without_claiming_ready(self):
        app = self.app()
        with patch.object(neje_app.device_setup, 'lightburn_tcp_device', return_value=['Bridge']):
            app.on_steps()
        text = app.log.call_args.args[0]
        self.assertIn('配置文件已有', text)
        self.assertIn('真实设备回应', text)
        self.assertNotIn('还没有', text)


class DesktopProcessTests(unittest.TestCase):
    def test_occupied_endpoint_does_not_spawn_or_consume_a_session(self):
        supervisor = neje_app.Supervisor.__new__(neje_app.Supervisor)
        supervisor.log = Mock()
        supervisor.deadline = 1
        with patch.object(neje_app, 'port_in_use', return_value=True), \
             patch.object(neje_app.subprocess, 'Popen') as spawn:
            supervisor._spawn()
        spawn.assert_not_called()
        self.assertEqual(supervisor.deadline, 0)

    def test_bridge_diagnostics_are_persisted_outside_bundle(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(neje_app, 'LOG_ROOT', Path(folder)), \
             patch.object(neje_app, 'port_in_use', return_value=False), \
             patch.object(neje_app.subprocess, 'Popen') as spawn:
            supervisor = neje_app.Supervisor(lambda message: None)
            supervisor._spawn()
            diagnostic = supervisor.last_log.with_suffix('.process.log')
            self.assertTrue(diagnostic.exists())
            self.assertEqual(spawn.call_args.kwargs['stderr'], neje_app.subprocess.STDOUT)
            self.assertEqual(Path(spawn.call_args.kwargs['stdout'].name), diagnostic)
