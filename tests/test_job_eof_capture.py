# evidence/ is deliberately not part of the repository; tests that read real captures
# skip themselves explicitly rather than failing or pretending to pass.
import os as _os, unittest as _unittest
if not _os.path.isdir(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'evidence')):
    raise _unittest.SkipTest('evidence/ is not part of this repository')
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from check_job_eof_capture import audit

CAPTURE = Path(__file__).resolve().parents[1] / 'evidence/eof-query-drain/mac-readonly-eof-01.jsonl'


class EOFSignalAuditTests(unittest.TestCase):
    def test_actual_capture_passes(self):
        self.assertTrue(audit(CAPTURE)['inflight_query_verified_after_eof'])

    def test_forged_forwarded_ack_rejected(self):
        rows = [json.loads(line) for line in CAPTURE.read_text().splitlines()]
        rows.insert(-1, dict(event='job_tcp_delivered', hex=b'ok\n'.hex()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'forged.jsonl'
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            with self.assertRaises(AssertionError):
                audit(path)

    def test_uncertain_write_cannot_be_declared_verified(self):
        rows = [json.loads(line) for line in CAPTURE.read_text().splitlines()]
        rows.insert(-1, dict(event='stream_write_uncertain', hex='3f'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'uncertain.jsonl'
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            with self.assertRaises(AssertionError):
                audit(path)
