"""Offline audit of the authorized one-query capture; never accesses BLE."""
import hashlib
import json
from pathlib import Path
from neje_protocol import FrameDecoder, describe, crc32_mpeg2
from grbl_readonly import encode_query

root = Path(__file__).resolve().parents[1]
path = root / 'logs/grbl-build-authorized-host-20261012-01.jsonl'
rows = [json.loads(line) for line in path.read_text().splitlines()]
labels = [r['label'] for r in rows if r['event'] == 'write_attempt']
assert labels == ['verify', 'system_before', 'bridge_before', 'enable_ble_reliable',
                  'bridge_enabled', 'grbl_build_query', 'bridge_after_query',
                  'disable_reliable', 'bridge_cleanup', 'system_after']
assert [r['event'] for r in rows][-1] == 'complete'
raw = b''.join(bytes.fromhex(r['hex']) for r in rows if r['event'] == 'notify')
expected = [bytes.fromhex(r['hex']) for r in rows if r['event'] == 'frame']
for split in range(len(raw) + 1):
    decoder = FrameDecoder()
    assert decoder.feed(raw[:split]) + decoder.feed(raw[split:]) == expected
    assert not decoder.buffer
statuses = [describe(f) for f in expected if describe(f)['kind'] == 'grbl_comm_status']
assert [(s['way'], s['write_check'], s['read_check']) for s in statuses] == [
    (0, 0, 0), (2, 0, 0), (2, 3, 62), (0, 3, 62)]
assert all(s['device_tx_error'] == s['device_rx_error'] == 0 for s in statuses)
data = [f for f in expected if describe(f)['kind'] == 'grbl_data']
assert len(data) == 1
assert data[0][-1] == crc32_mpeg2(data[0][1:-1]) & 255 == 0x34
assert data[0][8:-1] == b'[VER:1.1f.NEJE build version:v2.3.2:]\n[OPT:VMZHL,100,1500]\nok\n'
query = next(r for r in rows if r.get('label') == 'grbl_build_query' and r['event'] == 'write_attempt')
assert bytes.fromhex(query['hex']) == encode_query('build', 0)
cleanup = next(r for r in rows if r['event'] == 'cleanup_verified')
assert cleanup['way'] == 0 and cleanup['initial_app'] == cleanup['final_app'] == 3
print(json.dumps({'capture': str(path.relative_to(root)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  'notification_bytes': len(raw), 'frames': len(expected), 'split_positions_checked': len(raw)+1,
                  'write_labels': labels, 'payload_ascii': data[0][8:-1].decode('ascii'),
                  'write_check': 3, 'read_check': 62, 'cleanup_way': 0, 'passed': True}, indent=2))
