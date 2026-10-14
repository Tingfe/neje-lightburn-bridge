import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from grbl_readonly import encode_query,encode_read_from,accept_data
from neje_protocol import FrameDecoder,describe,GRBL_COMM_STATUS

class GrblProtocolTests(unittest.TestCase):
    def test_independent_official_crc_vectors(self):
        vectors=json.loads((ROOT/'evidence/grbl-research/offline-packet-vectors.json').read_text())
        self.assertEqual(encode_query('build',0).hex(),vectors['build_at_0'])
        self.assertEqual(encode_query('build',65535).hex(),vectors['build_at_65535'])
        self.assertEqual(encode_query('status',3).hex(),vectors['status_at_3'])
        self.assertEqual(encode_read_from(4660).hex(),vectors['read_from_4660'])

    def test_query_whitelist_and_counter_bounds(self):
        for query in ['G0 X1','M3','reset','home','$I','$$']:
            with self.assertRaises(ValueError):encode_query(query,0)
        for counter in [-1,65536]:
            with self.assertRaises(ValueError):encode_query('build',counter)
            with self.assertRaises(ValueError):encode_read_from(counter)

    def test_no_gap_or_duplicate_acceptance_and_wrap(self):
        self.assertEqual(accept_data(65535,65535,b'ab'),1)
        with self.assertRaises(ValueError):accept_data(10,12,b'a')
        with self.assertRaises(ValueError):accept_data(10,8,b'a')

    def test_real_status_replay(self):
        rows=[json.loads(s) for s in (ROOT/'logs/grbl-status-readonly-host.jsonl').read_text().splitlines()]
        decoder=FrameDecoder()
        frames=[f for r in rows if r['event']=='notify' for f in decoder.feed(bytes.fromhex(r['hex']))]
        statuses=[describe(f) for f in frames if describe(f)['kind']=='grbl_comm_status']
        self.assertEqual(len(statuses),1)
        self.assertEqual({k:statuses[0][k] for k in ['way','write_check','read_check','device_tx_error','device_rx_error']},
                         dict(way=0,write_check=0,read_check=0,device_tx_error=0,device_rx_error=0))
        writes=[r['hex'] for r in rows if r['event']=='write_attempt']
        self.assertIn(GRBL_COMM_STATUS.hex(' '),writes)

    def test_truncated_status_does_not_decode(self):
        self.assertEqual(describe(bytes.fromhex('ff aa 0c 41 01 00 00 00 00 00 00 55'))['kind'],'unmapped')

if __name__=='__main__':unittest.main()
