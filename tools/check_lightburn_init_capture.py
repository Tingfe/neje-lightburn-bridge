"""Offline USB/BLE initialization parity audit; never accesses hardware."""
import collections
import json
from pathlib import Path

from grbl_readonly import ENABLE_BLE_RELIABLE, DISABLE_RELIABLE, accept_data
from grbl_transport import encode_write
from lightburn_init_bridge import REQUESTS
from neje_protocol import FrameDecoder, VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS, crc32_mpeg2

ROOT = Path(__file__).resolve().parents[1]
folder = ROOT / 'evidence/lightburn-init-profile'
usb = [json.loads(line) for line in (folder / 'usb-init-01.jsonl').read_text().splitlines()]
rows = [json.loads(line) for line in (folder / 'lightburn-mac-ble-01.jsonl').read_text().splitlines()]
baseline = {r['name']: r['text'] for r in usb if r['event'] == 'usb_init_result'}
assert set(baseline) == {'rapid_mode', 'build', 'parameters'}
assert all(r['terminal'] == 'ok' for r in usb if r['event'] == 'usb_init_result')
fixed = {VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS, ENABLE_BLE_RELIABLE, DISABLE_RELIABLE}
decoder = FrameDecoder()
decoded, expected, verified, delivered = [], [], [], []
pending = None
output = bytearray()
sent = collections.Counter()
for row in rows:
    event = row['event']
    if event == 'session_ready':
        wcheck, rcheck = row['write_check'], row['read_check']
        start = (wcheck, rcheck)
    elif event == 'notify':
        decoded.extend(decoder.feed(bytes.fromhex(row['hex'])))
    elif event == 'frame':
        frame = bytes.fromhex(row['hex']); expected.append(frame)
        if row['kind'] == 'grbl_data':
            assert pending is not None
            assert frame[-1] == crc32_mpeg2(frame[1:-1]) & 255
            payload = bytes.fromhex(row['payload_hex'])
            rcheck = accept_data(rcheck, row['read_check'], payload)
            output.extend(payload)
        if row['kind'] == 'grbl_comm_status':
            assert row['device_tx_error'] == row['device_rx_error'] == 0
    elif event == 'write_attempt':
        frame = bytes.fromhex(row['hex'])
        if frame not in fixed:
            assert row['label'].startswith('init_')
            name = row['label'][len('init_'):]
            assert frame == encode_write(REQUESTS[name], wcheck)
            assert pending is None
            pending = name; output.clear(); sent[name] += 1
    elif event == 'init_verified':
        assert pending == row['name']
        assert bytes(output).decode('ascii') == row['text']
        wcheck = (wcheck + len(REQUESTS[pending])) & 65535
        assert (wcheck, rcheck) == (row['write_check'], row['read_check'])
        if pending in baseline:
            assert row['text'] == baseline[pending] and row['terminal'] == 'ok'
        else:
            assert '<Idle|' in row['text'] and '|MPos:0.000,400.000,0.000,0.000|' in row['text']
            assert '|FS:0,0' in row['text']
        verified.append((pending, bytes(output))); pending = None
    elif event == 'tcp_delivered':
        delivered.append((row['name'], bytes.fromhex(row['hex'])))
    assert event not in ('tcp_blocked', 'session_error', 'task_error', 'error', 'cleanup_unconfirmed')
assert decoded == expected and pending is None
# One startup Idle check is internal and is not sent as a reply to LightBurn G0.
assert verified[0][0] == 'status' and delivered == verified[1:]
cleanup = [r for r in rows if r['event'] == 'cleanup_verified']
assert len(cleanup) == 1
assert (cleanup[0]['way'], cleanup[0]['final_app']) == (0, 3)
assert (cleanup[0]['write_check'], cleanup[0]['read_check']) == (wcheck, rcheck)
assert any(r['event'] == 'ble_disconnected' and not r['is_connected'] for r in rows)
assert any(r['event'] == 'tcp_closed' and not r['cleanup_unconfirmed'] for r in rows)
prefs_before = json.loads((folder / 'prefs-before.ini').read_text())
prefs_after = json.loads((folder / 'prefs-after.ini').read_text())
changes = sorted(k for k in prefs_before.keys() | prefs_after.keys() if prefs_before.get(k) != prefs_after.get(k))
assert set(changes) <= {'WindowState', 'WindowPosY', 'WindowPosX'}
print(json.dumps({'passed': True, 'usb_ble_byte_exact_parity': sorted(baseline),
                  'requests_sent': dict(sent), 'tcp_results': len(delivered),
                  'initial_counters': start, 'final_counters': [wcheck, rcheck],
                  'decoded_frames': len(decoded), 'unconsumed_notification_tail': len(decoder.buffer),
                  'no_local_rejections': True, 'all_device_terminals_ok': True,
                  'cleanup': cleanup[0], 'preference_changes': changes}, indent=2))
