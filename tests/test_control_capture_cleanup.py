import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from cleanup_control_capture import owned_counter

ROOT=Path(__file__).resolve().parents[1]/'evidence/control-capture'


class CaptureCleanupTests(unittest.TestCase):
    def test_actual_owned_fault_counter(self):
        self.assertEqual(owned_counter(ROOT/'lightburn-idle-controls-01.jsonl'),2530)

    def test_unknown_extra_write_or_wrong_counter_refuses_ownership(self):
        original=[json.loads(line) for line in (ROOT/'lightburn-idle-controls-01.jsonl').read_text().splitlines()]
        for field,value in (('wire',b'G1 X1\n'.hex()),('counter',0)):
            rows=json.loads(json.dumps(original))
            if field=='wire':
                next(r for r in rows if r['event']=='pipeline_write_attempt')['hex']=value
            else:
                next(r for r in rows if r['event']=='stream_write_reserved')['after']=value
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'bad.jsonl'
                path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                with self.assertRaises(AssertionError): owned_counter(path)
