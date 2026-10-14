# evidence/ is deliberately not part of the repository; tests that read real captures
# skip themselves explicitly rather than failing or pretending to pass.
import os as _os, unittest as _unittest
if not _os.path.isdir(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'evidence')):
    raise _unittest.SkipTest('evidence/ is not part of this repository')
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from reviewed_job import JOB, ReviewedJobParser, review_export

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / 'evidence/stream-transport/lightburn-zero-power-reviewed-v2.gc'


class ReviewedJobTests(unittest.TestCase):
    def test_native_export_and_all_tcp_split_positions_preserve_wire(self):
        self.assertFalse(review_export(EXPORT)['hardware_executed'])
        # Export contains Chinese human-readable comments. They are reviewed
        # offline, never forwarded as possible extended realtime bytes.
        wire = b';ASCII review comment\r\n' + b'\r\n'.join(JOB) + b'\r\n'
        for split in range(len(wire) + 1):
            parser = ReviewedJobParser()
            rows = parser.feed(wire[:split]) + parser.feed(wire[split:])
            parser.eof()
            self.assertTrue(parser.complete)
            self.assertEqual(b''.join(value for _, value in rows), wire)
            self.assertEqual(sum(kind == 'job' for kind, _ in rows), len(JOB))

    def test_status_interleaves_partial_line_without_changing_job(self):
        parser = ReviewedJobParser()
        rows = parser.feed(b'$I\r\nG0\nM5\nS?0\n')
        self.assertIn(('status', b'?'), rows)
        self.assertEqual(parser.index, 2)
        self.assertIn(('job', b'S0\n'), rows)

    def test_motion_power_air_reset_and_reordering_fail_closed(self):
        for wire in (b'G1 X1\n', b'M4 S1\n', b'M8\n', b'$H\n', b'\x18', b'!', b'~',
                     b'M5\nG90\n', b'M5\nM5\n', b'A' * 513, b'M5\n$I\n'):
            parser = ReviewedJobParser()
            with self.assertRaises(ValueError):
                parser.feed(wire)
            with self.assertRaises(ValueError):
                parser.feed(b'?')

    def test_settings_read_is_admitted_only_outside_the_job(self):
        # '$$' dumps settings (read-only). It is admitted before the job starts
        # and after it completes, but never inside the reviewed sequence, and
        # no settings write ($n=value) is admitted at all.
        parser = ReviewedJobParser()
        self.assertEqual(parser.feed(b'$$\n'), [('initialization', b'$$\n')])
        parser.feed(b'\n'.join(JOB) + b'\n')
        self.assertTrue(parser.complete)
        self.assertEqual(parser.feed(b'$$\r\n'), [('initialization', b'$$\r\n')])
        for wire in (b'M5\n$$\n', b'$$\nM5\n$$\n', b'$30=500\n', b'M5\n$30=500\n'):
            with self.subTest(wire=wire):
                parser = ReviewedJobParser()
                with self.assertRaises(ValueError):
                    parser.feed(wire)
        parser = ReviewedJobParser()
        parser.feed(b'M5\n')
        with self.assertRaises(ValueError):
            parser.feed(b'$$\n')

    def test_second_job_and_incomplete_eof_are_rejected(self):
        parser = ReviewedJobParser()
        parser.feed(b'\n'.join(JOB) + b'\n')
        with self.assertRaises(ValueError):
            parser.feed(b'M5\n')
        for wire in (b'M', b'M5\n', b'M5\nS0'):
            parser = ReviewedJobParser()
            parser.feed(wire)
            with self.assertRaises(ValueError):
                parser.eof()

    def test_export_mutation_requires_new_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'changed.gc'
            path.write_bytes(EXPORT.read_bytes().replace(b'X1S0F60', b'X2S0F60'))
            with self.assertRaises(ValueError):
                review_export(path)


if __name__ == '__main__':
    unittest.main()
