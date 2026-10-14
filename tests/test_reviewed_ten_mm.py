# evidence/ is deliberately not part of the repository; tests that read real captures
# skip themselves explicitly rather than failing or pretending to pass.
import os as _os, unittest as _unittest
if not _os.path.isdir(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'evidence')):
    raise _unittest.SkipTest('evidence/ is not part of this repository')
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from reviewed_ten_mm import EXPORT,TEN_MM_JOB,review
from reviewed_job import ReviewedJobParser


class TenMMReviewTests(unittest.TestCase):
    def test_native_export_all_splits_and_no_runtime_authorization(self):
        result=review()
        self.assertFalse(result['motion_authorized'])
        self.assertTrue(result['job_bridge_ready'])
        wire=b'\r\n'.join(TEN_MM_JOB)+b'\r\n'
        for i in range(len(wire)+1):
            parser=ReviewedJobParser(job=TEN_MM_JOB)
            rows=parser.feed(wire[:i])+parser.feed(wire[i:]);parser.eof()
            self.assertTrue(parser.complete)
            self.assertEqual(b''.join(row[1] for row in rows),wire)
        parser=ReviewedJobParser(job=())
        with self.assertRaises(ValueError):parser.feed(wire)

    def test_changed_power_requires_new_review(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'changed.gc';p.write_bytes(EXPORT.read_bytes().replace(b'M4 S0',b'M4 S1'))
            with self.assertRaises(ValueError):review(p)
