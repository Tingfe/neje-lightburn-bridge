"""Offline audit of authorized $$/$G reads and USB/BLE profile parity."""
import json
from pathlib import Path
from grbl_readonly import ENABLE_BLE_RELIABLE, DISABLE_RELIABLE, accept_data
from grbl_transport import encode_write
from machine_profile_probe import PROFILE_QUERIES
from neje_protocol import VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS, FrameDecoder, crc32_mpeg2

ROOT = Path(__file__).resolve().parents[1]
folder = ROOT / 'evidence/lightburn-init-profile'
usb = [json.loads(x) for x in (folder / 'profile-usb-01.jsonl').read_text().splitlines()]
ble = [json.loads(x) for x in (folder / 'profile-mac-ble-01.jsonl').read_text().splitlines()]
expected = {r['name']: r['text'] for r in usb if r['event'] == 'profile_result'}
actual = {r['name']: r['text'] for r in ble if r['event'] == 'profile_result'}
assert expected == actual and set(actual) == set(PROFILE_QUERIES)
assert usb[-1]['event'] == ble[-1]['event'] == 'profile_complete'
fixed = {VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS, ENABLE_BLE_RELIABLE, DISABLE_RELIABLE}
requests = dict(PROFILE_QUERIES, status=b'?')
decoder, decoded, frames = FrameDecoder(), [], []
pending, data = None, bytearray()
for row in ble:
    event = row['event']
    if event == 'session_ready':
        w, r = row['write_check'], row['read_check']
    elif event == 'notify':
        decoded.extend(decoder.feed(bytes.fromhex(row['hex'])))
    elif event == 'frame':
        packet = bytes.fromhex(row['hex']); frames.append(packet)
        if row['kind'] == 'grbl_data':
            assert packet[-1] == crc32_mpeg2(packet[1:-1]) & 255
            payload = bytes.fromhex(row['payload_hex'])
            r = accept_data(r, row['read_check'], payload); data.extend(payload)
        if row['kind'] == 'grbl_comm_status':
            assert row['device_tx_error'] == row['device_rx_error'] == 0
    elif event == 'write_attempt' and bytes.fromhex(row['hex']) not in fixed:
        assert pending is None
        name = row['label'][len('init_'):]
        assert bytes.fromhex(row['hex']) == encode_write(requests[name], w)
        pending = name; data.clear()
    elif event == 'init_verified':
        assert row['name'] == pending and row['text'].encode('ascii') == bytes(data)
        w = (w + len(requests[pending])) & 65535
        assert (w, r) == (row['write_check'], row['read_check'])
        pending = None
    assert event != 'error'
assert pending is None and decoded == frames
last = next(row for row in reversed(ble) if row['event'] == 'cleanup_verified')
assert (last['way'], last['final_app'], last['write_check'], last['read_check']) == (0, 3, w, r)
settings = dict(line[1:].split('=', 1) for line in actual['settings'].splitlines() if line.startswith('$'))
print(json.dumps({'passed': True, 'usb_ble_profile_byte_exact': True,
                  'settings': settings, 'modal': actual['modal'],
                  'cleanup': last, 'decoded_frames': len(frames),
                  'unconsumed_notification_tail': len(decoder.buffer)}, indent=2))
