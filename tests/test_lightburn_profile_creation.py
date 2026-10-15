import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import device_setup


class NativeProfileTests(unittest.TestCase):
    def test_empty_or_unrelated_device_list_needs_no_template(self):
        for templates in ([], [{'Name': 'Ruida', 'Settings': {'StartGCode': 'M3 S1000'},
                                'MirrorX': True, 'Macros': ['G1 X500']}],
                          [{'Name': 'No Machine'}]):
            device = device_setup.build_device(templates)
            self.assertEqual((device['Name'], device['ProfilePath'], device['Type']), ('GRBL', 'GRBL', 'TCP'))
            self.assertEqual((device['Width'], device['Height']), (260, 400))
            self.assertFalse(device['HomeOnStartup'])
            self.assertFalse(device['MirrorX'])
            self.assertEqual(device['Macros'], [])
            self.assertEqual(device['Settings']['StartGCode'], '')
            self.assertEqual(device['Settings']['S_Scale'], 1000)
            self.assertFalse(device['Settings']['LaserFire_Enable'])

    def test_creation_backup_preservation_readback_and_idempotence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'prefs.ini'
            original = {'Name': 'Other', 'Checklist': 'check [square] and "quote"'}
            text = '{"Before": 42, "DeviceList": '+json.dumps([original])+', "After": "keep"}'
            path.write_text(text)
            with patch.object(device_setup, 'PREFS', path), \
                 patch.object(device_setup, 'lightburn_running', return_value=False):
                changed, message = device_setup.add_tcp_device(apply=True)
                self.assertTrue(changed, message)
                data = json.loads(path.read_text())
                self.assertEqual(data['DeviceList'][0], original)
                self.assertEqual(data['Before'], 42)
                self.assertEqual(data['After'], 'keep')
                self.assertEqual(device_setup.lightburn_tcp_device(), ['LaserLink NEJE5 (蓝牙桥接)'])
                saved = path.read_text()
                self.assertFalse(device_setup.add_tcp_device(apply=True)[0])
                self.assertEqual(path.read_text(), saved)
            backups = list(Path(folder).glob('prefs.ini.neje-backup-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), text)

    def test_failed_readback_rolls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'prefs.ini'
            text = '{"DeviceList": []}'
            path.write_text(text)
            with patch.object(device_setup, 'PREFS', path), \
                 patch.object(device_setup, 'lightburn_running', return_value=False), \
                 patch.object(device_setup, 'lightburn_tcp_device', return_value=[]):
                changed, message = device_setup.add_tcp_device(apply=True)
            self.assertFalse(changed)
            self.assertIn('回滚', message)
            self.assertEqual(path.read_text(), text)

    def test_malformed_or_non_array_config_is_preserved(self):
        for text in ('{"Other": []}', '{"DeviceList": "oops", "Other": []}'):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder)/'prefs.ini'
                path.write_text(text)
                with patch.object(device_setup, 'PREFS', path), \
                     patch.object(device_setup, 'lightburn_running', return_value=False):
                    changed, _ = device_setup.add_tcp_device(apply=True)
                self.assertFalse(changed)
                self.assertEqual(path.read_text(), text)

    def test_unknown_process_state_refuses_to_modify_configuration(self):
        with patch.object(device_setup, '_devices', return_value=[]), \
             patch.object(device_setup.subprocess, 'run', return_value=types.SimpleNamespace(returncode=3)):
            changed, message = device_setup.add_tcp_device(apply=True)
        self.assertFalse(changed)
        self.assertIn('无法确认', message)
