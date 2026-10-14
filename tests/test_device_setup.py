import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import device_setup


class DeviceSetupTests(unittest.TestCase):
    def test_candidates_pick_the_machine_out_of_a_crowd(self):
        found = [('iPhone', 'A'), ('NEJE 5 Plus', 'B'), ('HID-Device', 'C'), ('neje 5', 'D')]
        self.assertEqual(device_setup.candidates(found), [('NEJE 5 Plus', 'B'), ('neje 5', 'D')])

    def test_a_missing_lightburn_config_is_not_the_same_as_no_device(self):
        original = device_setup.PREFS
        try:
            device_setup.PREFS = Path('/nonexistent/prefs.ini')
            self.assertIsNone(device_setup.lightburn_tcp_device())
        finally:
            device_setup.PREFS = original

    def test_a_tcp_device_on_the_bridge_port_is_reported(self):
        original = device_setup.PREFS
        import tempfile
        handle = tempfile.NamedTemporaryFile('w', suffix='.ini', delete=False)
        handle.write('"DeviceList": ' + json.dumps([
            {'DisplayName': 'USB one', 'Type': 'Serial'},
            {'DisplayName': 'Bridge', 'Type': 'TCP', 'NetworkPort': 4002},
            {'DisplayName': 'Elsewhere', 'Type': 'TCP', 'NetworkPort': 9999},
        ]))
        handle.close()
        try:
            device_setup.PREFS = Path(handle.name)
            self.assertEqual(device_setup.lightburn_tcp_device(), ['Bridge'])
        finally:
            device_setup.PREFS = original

    def test_the_instructions_name_the_bridge_endpoint(self):
        text = device_setup.add_instructions()
        self.assertIn('127.0.0.1', text)
        self.assertIn(str(device_setup.BRIDGE_PORT), text)


if __name__ == '__main__':
    unittest.main()


class CreateLightBurnDeviceTests(unittest.TestCase):
    """Synthesising the device must be reversible and must never lose an entry."""

    DEVICES = [{'DisplayName': 'No Machine', 'Type': None},
               {'DisplayName': 'GRBL', 'Type': 'Serial', 'Name': 'GRBL',
                'ProfilePath': 'GRBL', 'CommPort': '(Choose)', 'Settings': {}}]

    def test_a_serial_grbl_device_becomes_a_tcp_device_on_the_bridge_port(self):
        device = device_setup.build_device(self.DEVICES)
        self.assertEqual(device['Type'], 'TCP')
        self.assertEqual(device['NetworkPort'], device_setup.BRIDGE_PORT)
        self.assertEqual(device['Info'], '127.0.0.1')
        self.assertEqual(device['ProfilePath'], 'GRBL')
        self.assertNotIn('CommPort', device)
        self.assertNotEqual(device['GUID'], self.DEVICES[1].get('GUID'))

    def test_the_template_is_not_mutated(self):
        device_setup.build_device(self.DEVICES)
        self.assertEqual(self.DEVICES[1]['Type'], 'Serial')

    def test_an_existing_device_on_the_same_port_is_never_duplicated(self):
        original_read, original_running = device_setup._devices, device_setup.lightburn_running
        try:
            device_setup._devices = lambda: self.DEVICES + [
                {'DisplayName': 'Already', 'Type': 'TCP', 'NetworkPort': device_setup.BRIDGE_PORT}]
            device_setup.lightburn_running = lambda: False
            changed, message = device_setup.add_tcp_device(apply=True)
            self.assertFalse(changed)
            self.assertIn('已存在', message)
        finally:
            device_setup._devices, device_setup.lightburn_running = original_read, original_running

    def test_it_refuses_to_write_while_lightburn_runs(self):
        original_read, original_running = device_setup._devices, device_setup.lightburn_running
        try:
            device_setup._devices = lambda: self.DEVICES
            device_setup.lightburn_running = lambda: True
            changed, message = device_setup.add_tcp_device(apply=True)
            self.assertFalse(changed)
            self.assertIn('正在运行', message)
        finally:
            device_setup._devices, device_setup.lightburn_running = original_read, original_running

    def test_dry_run_changes_nothing(self):
        original_read = device_setup._devices
        try:
            device_setup._devices = lambda: self.DEVICES
            changed, message = device_setup.add_tcp_device(apply=False)
            self.assertTrue(changed)
            self.assertIn('干跑', message)
        finally:
            device_setup._devices = original_read
