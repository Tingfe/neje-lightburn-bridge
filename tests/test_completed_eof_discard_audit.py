import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from check_controlled_capture import audit
from reviewed_job_file import load

CAPTURE=ROOT/'evidence/ble-arc-rapid-parity-20261013/rapid/ble-rapid-01.jsonl'
JOB=ROOT/'evidence/ble-arc-rapid-parity-20261013/rapid/job.gc'


class CompletedEOFDiscardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=[json.loads(line) for line in CAPTURE.read_text().splitlines()]
        cls.reviewed=load(JOB,expected_sha256=
            'aa273f6ee1c9317e442e856d9e6c17f69f8a9998850ebaf57d45142f9a20445d',
            profile='xy-job-zero')

    def check(self,rows):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'capture.jsonl'
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            return audit(path,backend='ble',reviewed_file=self.reviewed,expected_controls=())

    def test_actual_completed_capture_accounts_for_unsent_query_and_blank(self):
        result=self.check(self.rows)
        self.assertTrue(result['passed'])
        self.assertEqual(result['discarded_unsent_status_queries'],1)
        self.assertEqual(result['discarded_unsent_whitespace_bytes'],1)
        self.assertFalse(result['all_received_tcp_queries_forwarded'])
        self.assertEqual(result['discard_cause'],'post_completion_client_eof')
        self.assertTrue(result['no_synthetic_ack'])

    def test_forwarding_flag_follows_the_discard_count(self):
        # The flag is derived from the capture instead of being hardcoded, so it
        # must agree with the recorded discard count. This helper is only reached
        # when at least one discard exists (caller guard), so real captures report
        # False; the point here is that the two fields cannot disagree.
        result=self.check(self.rows)
        self.assertEqual(result['all_received_tcp_queries_forwarded'],
                         result['discarded_unsent_status_queries']==0)
        self.assertEqual(result['discard_cause'],'post_completion_client_eof')

    def test_missing_or_uncleared_local_generation_rejected(self):
        for mode in ('missing','uncleared','incomplete'):
            rows=copy.deepcopy(self.rows)
            index=next(i for i,row in enumerate(rows) if row['event']=='control_generation_abandoned')
            if mode=='missing':rows.pop(index)
            elif mode=='uncleared':rows[index]['after']['queued_controls']=1
            else:rows[index]['after'].pop('queued_controls')
            with self.subTest(mode=mode),self.assertRaises(AssertionError):self.check(rows)

    def test_pending_attempted_line_or_status_is_never_allowed(self):
        for field in ('pending_lines','pending_statuses','queued_bytes'):
            rows=copy.deepcopy(self.rows)
            next(row for row in rows if row['event']=='pipeline_transmission_stopped')[field]=1
            with self.subTest(field=field),self.assertRaises(AssertionError):self.check(rows)

    def test_unsent_bytes_must_match_discarded_whitespace(self):
        rows=copy.deepcopy(self.rows)
        next(row for row in rows if row['event']=='pipeline_transmission_stopped')['staged_bytes']=2
        with self.assertRaises(AssertionError):self.check(rows)

    def test_discard_cannot_precede_confirmed_job_completion(self):
        rows=copy.deepcopy(self.rows)
        completed=next(row for row in rows if row['event']=='controlled_job_completed')
        rows.remove(completed)
        index=next(i for i,row in enumerate(rows) if row['event']=='pipeline_tcp_eof')
        rows.insert(index+1,completed)
        with self.assertRaises(AssertionError):self.check(rows)

    def test_forwarding_after_eof_or_fabricated_ack_rejected(self):
        for row in (dict(event='pipeline_write_attempt',hex='3f'),
                    dict(event='pipeline_tcp_delivered',hex='6f6b0a')):
            rows=copy.deepcopy(self.rows)
            index=next(i for i,item in enumerate(rows) if item['event']=='pipeline_tcp_eof')
            rows.insert(index+1,row)
            with self.subTest(event=row['event']),self.assertRaises(AssertionError):self.check(rows)


if __name__=='__main__':unittest.main()
