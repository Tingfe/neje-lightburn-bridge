import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from check_transparent_capture import audit
from transparent_fixture import faithful_rows


class TransparentAuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rows = await faithful_rows()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def check(self, rows):
        path = Path(self.folder.name)/'capture.jsonl'
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        return audit(path)

    async def test_real_codec_loopback_capture_passes(self):
        result = self.check(self.rows)
        self.assertTrue(result['passed'])
        self.assertTrue(result['clean_shutdown'])
        self.assertGreater(result['raw_audit']['decoded_frames'], 0)
        self.assertTrue(result['restart_safe'])

    async def test_all_delivery_removed_is_rejected(self):
        with self.assertRaises(AssertionError):
            self.check([row for row in self.rows if row['event'] != 'pipeline_tcp_delivered'])

    async def test_locally_valid_but_discontinuous_write_counters_are_rejected(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            if row['event'] == 'stream_write_reserved':
                row['before'] = (row['before']+100)&65535
                row['after'] = (row['after']+100)&65535
        with self.assertRaises(AssertionError): self.check(rows)

    async def test_fabricated_reply_is_rejected(self):
        rows = copy.deepcopy(self.rows)
        next(row for row in rows if row['event'] == 'pipeline_tcp_delivered')['hex'] = b'ALARM:1\n'.hex()
        with self.assertRaises(AssertionError): self.check(rows)

    async def test_notify_bytes_cannot_be_replaced_by_trusted_frame_metadata(self):
        rows = copy.deepcopy(self.rows)
        row = next(row for row in rows if row['event'] == 'notify')
        row['hex'] = row['hex'][:-2] + '00'
        with self.assertRaises((AssertionError, ValueError)): self.check(rows)

    async def test_staged_unconfirmed_bytes_do_not_prove_delivery(self):
        with self.assertRaises(AssertionError):
            self.check([row for row in self.rows if row['event'] != 'stream_verified'])

    async def test_unknown_cleanup_and_failure_do_not_pass(self):
        for key, value in (('cleanup_unknown', True), ('failure_reason', 'BLE loss')):
            rows = copy.deepcopy(self.rows)
            next(row for row in rows if row['event'] == 'pipeline_server_stopped')[key] = value
            with self.assertRaises(AssertionError): self.check(rows)

    async def test_open_capture_is_pending_not_green(self):
        rows = self.rows[:next(i for i, row in enumerate(self.rows) if row['event'] == 'pipeline_transmission_stopped')]
        path = Path(self.folder.name)/'open.jsonl'
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        result = audit(path, in_progress=True)
        self.assertFalse(result['passed'])
        self.assertFalse(result['shutdown_verified'])

    async def test_forged_post_eof_idle_verdict_is_rejected(self):
        rows = copy.deepcopy(self.rows)
        probe = next(row for row in rows if row['event'] == 'pipeline_eof_idle_probe')
        probe['status'] = '<Idle|MPos:0,400,0,0|FS:0,0>'
        with self.assertRaises(AssertionError): self.check(rows)


if __name__ == '__main__': unittest.main()
