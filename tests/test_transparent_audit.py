import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from check_transparent_capture import audit


def capture(rows):
    handle = tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False)
    for row in rows:
        handle.write(json.dumps(row) + '\n')
    handle.close()
    return handle.name


BASE = [
    dict(event='pipeline_transparent_mode', usb_equivalent=True,
         reviewed_job_required=False, whitelist=False),
    dict(event='pipeline_tcp_received', hex=b'?\n'.hex()),
    dict(event='pipeline_ingress', kind='status', hex=b'?'.hex()),
    dict(event='pipeline_ingress', kind='ordinary', hex=b'\n'.hex()),
    dict(event='pipeline_write_attempt', hex=b'?'.hex()),
    dict(event='pipeline_write_attempt', hex=b'\n'.hex()),
    dict(event='stream_write_reserved', hex=b'?\n'.hex(), before=10, after=12),
    dict(event='stream_rx_staged', hex=b'<Idle|MPos:0,400,0,0>\nok\n'.hex(), read_check=5),
    dict(event='pipeline_tcp_delivered', hex=b'ok\n'.hex()),
    dict(event='pipeline_transmission_stopped', queued_bytes=0, staged_bytes=0, no_replay=True),
    dict(event='cleanup_verified', way=0, final_app=3),
    dict(event='pipeline_server_stopped', cleanup_unknown=False, failure_reason=None),
]


class TransparentAuditTests(unittest.TestCase):
    def test_a_faithful_capture_passes(self):
        result = audit(capture(BASE))
        self.assertTrue(result['passed'])
        self.assertFalse(result['whitelist'])
        self.assertTrue(result['clean_shutdown'])
        self.assertTrue(result['no_fabricated_bytes'])

    def test_a_dropped_client_byte_is_caught(self):
        rows = [dict(r) for r in BASE]
        rows[4] = dict(event='pipeline_write_attempt', hex=b'\n'.hex())   # '?' never forwarded
        with self.assertRaises(AssertionError):
            audit(capture(rows))

    def test_an_invented_ingress_byte_is_caught(self):
        rows = [dict(r) for r in BASE]
        rows[2] = dict(event='pipeline_ingress', kind='status', hex=b'$H\n'.hex())
        with self.assertRaises(AssertionError):
            audit(capture(rows))

    def test_a_fabricated_delivered_byte_is_caught(self):
        rows = [dict(r) for r in BASE]
        rows[8] = dict(event='pipeline_tcp_delivered', hex=b'ALARM:1\n'.hex())
        with self.assertRaises(AssertionError):
            audit(capture(rows))

    def test_a_counter_that_does_not_advance_is_caught(self):
        rows = [dict(r) for r in BASE]
        rows[6] = dict(event='stream_write_reserved', hex=b'?\n'.hex(), before=10, after=99)
        with self.assertRaises(AssertionError):
            audit(capture(rows))

    def test_a_declared_reviewed_mode_is_not_accepted(self):
        rows = [dict(r) for r in BASE]
        rows[0] = dict(event='pipeline_transparent_mode', usb_equivalent=True,
                       reviewed_job_required=True, whitelist=True)
        with self.assertRaises(AssertionError):
            audit(capture(rows))


if __name__ == '__main__':
    unittest.main()
