import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import desktop_settings


class DesktopSettingsTests(unittest.TestCase):
    def test_only_device_choice_round_trips_no_run_authorization(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'nested/settings.json'
            desktop_settings.save('NEJE 5 Plus', 'device-A', path)
            self.assertEqual(desktop_settings.load(path), {'name': 'NEJE 5 Plus', 'address': 'device-A'})
            self.assertEqual(set(json.loads(path.read_text())), {'name', 'address'})
            self.assertFalse(path.with_suffix('.tmp').exists())
            path.write_text('{"address":"device-A", "laser_authorized":true}')
            self.assertNotIn('laser_authorized', desktop_settings.load(path))

    def test_missing_corrupt_and_wrong_type_are_not_a_device(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'settings.json'
            self.assertIsNone(desktop_settings.load(path))
            for text in ('broken', '[]', '{}', '{"address":3}', '{"address":""}'):
                path.write_text(text)
                self.assertIsNone(desktop_settings.load(path))
