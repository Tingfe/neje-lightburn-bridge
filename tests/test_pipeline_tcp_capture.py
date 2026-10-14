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
from check_pipeline_tcp_capture import audit
ROOT=Path(__file__).resolve().parents[1]/'evidence/pipeline-lightburn'
CONTROL=ROOT.parent/'control-capture/lightburn-controls-03.jsonl'


class PipelineTCPCaptureTests(unittest.TestCase):
    def test_actual_control_capture_and_512_query_file_after_fix(self):
        result=audit(CONTROL)
        self.assertTrue(result['passed'])
        self.assertEqual(result['commands']['$I'],513)
        self.assertEqual(result['controls_captured_without_forwarding'],['18'])
        self.assertEqual(result['raw_bytes_received'],45305)
        self.assertEqual(result['cleanup']['way'],0)

    def test_control_success_or_forwarding_claim_cannot_pass_capture_audit(self):
        original=[json.loads(line) for line in CONTROL.read_text().splitlines()]
        for field in ('forwarded','controller_success_claimed'):
            rows=json.loads(json.dumps(original))
            next(r for r in rows if r['event']=='lightburn_control_captured')[field]=True
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'false-control.jsonl'
                path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                with self.assertRaises(AssertionError):audit(path)

    def test_forged_extra_ack_in_control_capture_is_rejected(self):
        rows=[json.loads(line) for line in CONTROL.read_text().splitlines()]
        rows.insert(-1,dict(event='pipeline_tcp_delivered',hex='6f6b0a'))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fake-ack.jsonl'
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            with self.assertRaises(AssertionError):audit(path)

    def test_actual_captures_and_listener_fix_evidence(self):
        before=audit(ROOT/'lightburn-init-01.jsonl');after=audit(ROOT/'lightburn-init-02.jsonl')
        self.assertEqual(before['rejected_connections'],2158)
        self.assertEqual(after['rejected_connections'],0)
        self.assertTrue(after['no_synthetic_ack'])

    def test_forged_tcp_ack_rejected(self):
        rows=[json.loads(line) for line in (ROOT/'lightburn-init-02.jsonl').read_text().splitlines()]
        rows.insert(-1,dict(event='pipeline_tcp_delivered',hex='6f6b0a'))
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'forged.jsonl';p.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            with self.assertRaises(AssertionError):audit(p)

    def test_motion_input_cannot_be_reported_as_initialization(self):
        rows=[json.loads(line) for line in (ROOT/'lightburn-init-02.jsonl').read_text().splitlines()]
        row=next(row for row in rows if row['event']=='pipeline_tcp_received')
        row['hex']=b'G1 X1\n'.hex()
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'motion.jsonl';p.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            with self.assertRaises(ValueError):audit(p)
