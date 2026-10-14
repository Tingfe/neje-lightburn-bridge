"""Offline audit of Pi BLE captures; no device or network access."""
import collections
import json
from pathlib import Path

from grbl_readonly import READ_ONLY_QUERIES, encode_query
from neje_protocol import FrameDecoder, crc32_mpeg2

ROOT = Path(__file__).resolve().parents[1]
FIXED = {
    'verify': 'ffaa0801015aa555',
    'system_before': 'ffaa081001ff0055',
    'system_after': 'ffaa081001ff0055',
    'bridge_before': 'ffaa084001000055',
    'bridge_enabled': 'ffaa084001000055',
    'query_counters': 'ffaa084001000055',
    'bridge_cleanup': 'ffaa084001000055',
    'enable_ble_reliable': 'ffaa084002020055',
    'disable_reliable': 'ffaa084002000055',
}


def audit(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[-1]['event'] == 'complete'
    decoder = FrameDecoder()
    decoded, expected = [], []
    pending = None
    wcheck = rcheck = None
    queries = collections.Counter()
    blocked, cleanup = [], []
    verified, delivered = [], []
    for row in rows:
        event = row['event']
        if event == 'notify':
            decoded.extend(decoder.feed(bytes.fromhex(row['hex'])))
        elif event == 'frame':
            frame = bytes.fromhex(row['hex'])
            expected.append(frame)
            if row['kind'] == 'grbl_data':
                assert frame[-1] == crc32_mpeg2(frame[1:-1]) & 255
            if row['kind'] == 'grbl_comm_status':
                assert row['device_tx_error'] == row['device_rx_error'] == 0
        elif event == 'session_ready':
            assert pending is None
            wcheck, rcheck = row['write_check'], row['read_check']
        elif event == 'write_attempt':
            label, frame = row['label'], bytes.fromhex(row['hex'])
            if label.startswith('query_') and label != 'query_counters':
                name = label[len('query_'):]
                assert pending is None and frame == encode_query(name, wcheck)
                pending = name
            else:
                assert frame.hex() == FIXED[label]
        elif event == 'query_verified':
            name = row['name']
            assert pending == name
            wcheck = (wcheck + len(READ_ONLY_QUERIES[name])) & 65535
            rcheck = (rcheck + len(row['text'].encode('ascii'))) & 65535
            assert (row['write_check'], row['read_check']) == (wcheck, rcheck)
            verified.append((name, row['text'].encode('ascii')))
            queries[name] += 1
            pending = None
        elif event == 'tcp_delivered':
            delivered.append((row['name'], bytes.fromhex(row['hex'])))
        elif event == 'tcp_blocked':
            blocked.append(bytes.fromhex(row['hex']).decode('ascii'))
        elif event == 'cleanup_verified':
            assert pending is None
            assert (row['way'], row['final_app']) == (0, 3)
            assert (row['write_check'], row['read_check']) == (wcheck, rcheck)
            cleanup.append(row)
        elif event == 'ble_disconnected':
            assert row['is_connected'] is False
        elif event in ('tcp_closed', 'server_stopped'):
            assert row['cleanup_unconfirmed'] is False
        assert event not in ('error', 'session_error', 'cleanup_unconfirmed')
    assert decoded == expected
    assert cleanup and pending is None
    if any(row['event'] == 'listening' for row in rows):
        assert delivered == verified
    return {'capture': str(path.relative_to(ROOT)), 'passed': True,
            'queries': dict(queries), 'blocked': blocked,
            'decoded_frames': len(decoded), 'trailing_notification_bytes': len(decoder.buffer),
            'cleanup': cleanup, 'events': dict(collections.Counter(r['event'] for r in rows)),
            'verified_bytes': sum(len(data) for _, data in verified),
            'tcp_delivered_matches_verified': delivered == verified if delivered else None}


if __name__ == '__main__':
    print(json.dumps([audit(ROOT / 'evidence/pi-ble' / name) for name in
                      ('linux-ble-readonly-01.jsonl', 'lightburn-ble-01.jsonl')], indent=2))
