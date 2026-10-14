# evidence/ is deliberately not part of the repository; tests that read real captures
# skip themselves explicitly rather than failing or pretending to pass.
import os as _os, unittest as _unittest
if not _os.path.isdir(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'evidence')):
    raise _unittest.SkipTest('evidence/ is not part of this repository')
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import check_reviewed_job_capture as capture


class CaptureTests(unittest.TestCase):
    def test_real_usb_and_lightburn_capture_pass_complete_audit(self):
        self.assertTrue(capture.usb_audit()['passed'])
        result = capture.ble_audit()
        self.assertTrue(result['passed'])
        self.assertEqual(result['job_lines'], 14)

    def test_invented_tcp_ack_is_detected(self):
        rows = copy.deepcopy(capture.load('lightburn-ble-job-01.jsonl'))
        delivery = next(row for row in rows if row['event'] == 'job_tcp_delivered')
        delivery['hex'] = (bytes.fromhex(delivery['hex']) + b'ok\n').hex()
        with patch.object(capture, 'load', return_value=rows), self.assertRaises(AssertionError):
            capture.ble_audit()

    def test_repeated_job_and_unlogged_write_are_detected(self):
        original = capture.load('lightburn-ble-job-01.jsonl')
        rows = copy.deepcopy(original)
        incoming = next(row for row in rows if row['event'] == 'job_tcp_receive' and bytes.fromhex(row['hex']).startswith(b'M5\nS0'))
        incoming['hex'] += b'M5\n'.hex()
        with patch.object(capture, 'load', return_value=rows), self.assertRaises(ValueError):
            capture.ble_audit()
        rows = copy.deepcopy(original)
        index = next(i for i, row in enumerate(rows) if row['event'] == 'job_request_verified')
        rows.insert(index + 1, dict(event='write_attempt', hex='010203'))
        with patch.object(capture, 'load', return_value=rows), self.assertRaises(AssertionError):
            capture.ble_audit()


if __name__ == '__main__':
    unittest.main()
