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
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from check_pipeline_capture import audit, audit_ble
ROOT=Path(__file__).resolve().parents[1]/'evidence/grbl-pipeline'


class PipelineCaptureTests(unittest.TestCase):
    def test_real_ble_usb_comparison(self):
        self.assertTrue(audit(ROOT/'mac-readonly-01.jsonl',ROOT/'usb-readonly-01.jsonl')['build_responses_byte_identical'])

    def mutate(self,change):
        rows=[json.loads(line) for line in (ROOT/'mac-readonly-01.jsonl').read_text().splitlines()]
        change(rows)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'mutated.jsonl'
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            with self.assertRaises(AssertionError): audit_ble(path)

    def test_invented_ack_rejected(self):
        self.mutate(lambda rows: rows.insert(-1,dict(event='pipeline_response_chunk',text='ok\n')))

    def test_wrong_terminal_mapping_rejected(self):
        def change(rows):
            row=next(row for row in rows if row['event']=='pipeline_terminal')
            row['sequence']=2
        self.mutate(change)
