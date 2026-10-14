"""Offline raw-frame, exact-command and controller endpoint audit of 1mm trials."""
import json
from pathlib import Path
from grbl_readonly import ENABLE_BLE_RELIABLE, DISABLE_RELIABLE, accept_data
from grbl_transport import encode_write
from motion_parity_probe import QUERIES, MOVES, check_position, require_ok
from neje_protocol import VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS, FrameDecoder, crc32_mpeg2
folder = Path(__file__).resolve().parents[1] / 'evidence/lightburn-init-profile'
def rows(name): return [json.loads(x) for x in (folder / name).read_text().splitlines()]
first, returning, ble = [rows(n) for n in ('motion-usb-01.jsonl','motion-usb-return-01.jsonl','motion-mac-ble-01.jsonl')]
usb_moves = [(r['name'],bytes.fromhex(r['hex'])) for r in first+returning if r['event']=='usb_motion_tx' and r['name'] in MOVES]
assert usb_moves == list(MOVES.items()), usb_moves
assert first[-1]['event']=='error' and 'no retry' in first[-1]['error']
first_result = next(r for r in first if r['event']=='motion_result' and r['name']=='x_out')
require_ok(first_result['text'])
assert '[MSG: Laser temperature:' in first_result['text']
for row in rows('motion-usb-post-stop-readonly.jsonl'):
    if row['event']=='post_stop_readonly_result' and row['name']=='status': check_position(row['text'],1)
assert returning[-1]['event']==ble[-1]['event']=='motion_probe_complete'
fixed={VERIFY,SYSTEM_READ_ALL,GRBL_COMM_STATUS,ENABLE_BLE_RELIABLE,DISABLE_RELIABLE}
decoder,decoded,frames=FrameDecoder(),[],[]
pending,data=None,bytearray(); sent=[]
for row in ble:
    event=row['event']
    if event=='session_ready': w,r=row['write_check'],row['read_check']
    elif event=='notify': decoded.extend(decoder.feed(bytes.fromhex(row['hex'])))
    elif event=='frame':
        packet=bytes.fromhex(row['hex']); frames.append(packet)
        if row['kind']=='grbl_data':
            assert packet[-1]==crc32_mpeg2(packet[1:-1]) & 255
            payload=bytes.fromhex(row['payload_hex']); r=accept_data(r,row['read_check'],payload); data.extend(payload)
        if row['kind']=='grbl_comm_status': assert row['device_tx_error']==row['device_rx_error']==0
    elif event=='write_attempt' and bytes.fromhex(row['hex']) not in fixed:
        assert pending is None
        name=row['label'][len('init_'):]; sent.append(name)
        assert bytes.fromhex(row['hex'])==encode_write(QUERIES[name],w)
        pending=name; data.clear()
    elif event=='init_verified':
        assert row['name']==pending and row['text'].encode()==bytes(data)
        w=(w+len(QUERIES[pending])) & 65535
        assert (w,r)==(row['write_check'],row['read_check'])
        pending=None
    assert event!='error'
assert pending is None and decoded==frames
assert [n for n in sent if n in MOVES]==['x_out','x_back']
for capture in (returning,ble):
    final=next(row for row in capture if row['event']=='motion_comparison_passed')
    check_position(final['final_status'],0)
    assert {'G0','M5','S0','F60'} <= set(final['final_modal'].split(']')[0][4:].split())
arrivals=[row['name'] for row in ble if row['event']=='motion_arrival_verified']
assert arrivals==['x_out','x_back']
cleanup=next(row for row in reversed(ble) if row['event']=='cleanup_verified')
assert (cleanup['way'],cleanup['final_app'],cleanup['write_check'],cleanup['read_check'])==(0,3,w,r)
print(json.dumps(dict(passed=True,usb_moves=[n for n,_ in usb_moves],ble_moves=arrivals,
                     usb_split_run_reason='unsolicited temperature after real ok; no duplicate movement',
                     final_position=[0,400,0,0],final_modal='G0 M5 S0 F60',cleanup=cleanup,
                     decoded_frames=len(frames),unconsumed_tail=len(decoder.buffer),
                     physical_displacement_independently_measured=False),indent=2))
