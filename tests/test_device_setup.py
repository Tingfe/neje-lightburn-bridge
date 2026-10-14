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
