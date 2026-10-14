"""Offline verification of the real host-requested BLE disconnect capture."""
import datetime
import argparse
import json
from pathlib import Path
from neje_protocol import FrameDecoder, VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS
from grbl_readonly import ENABLE_BLE_RELIABLE, DISABLE_RELIABLE, encode_query, accept_data

root = Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('capture', nargs='?', type=Path, default=root/'logs/ble-disconnect-host-01.jsonl')
path = p.parse_args().capture.resolve()
rows = [json.loads(line) for line in path.read_text().splitlines()]
pending=rows[0].get('mode')=='pending'
def one(event):
    matches = [r for r in rows if r['event']==event]
    assert len(matches)==1, event
    return matches[0]
def delta(a,b):
    return (datetime.datetime.fromisoformat(one(b)['utc'])-datetime.datetime.fromisoformat(one(a)['utc'])).total_seconds()
assert rows[-1]['event']=='experiment_passed'
assert one('link_disconnect_requested')['tcp_peer_still_open']
assert one('link_disconnect_requested')['is_connected']
assert not one('ble_disconnected')['is_connected']
assert not one('link_disconnect_returned')['is_connected']
assert one('tcp_closed')['cleanup_unconfirmed']
assert one('bridge_fail_closed_verified')['ble_connections']==1
assert one('bridge_fail_closed_verified')['new_peer_rejected']
assert one('tcp_rejected')['reason']=='busy_or_cleanup_unconfirmed'
one('cleanup_unconfirmed')
assert not any(r['event']=='cleanup_verified' for r in rows)
ready=one('session_ready');verified=one('query_verified')
w,rcheck = ready['write_check'], ready['read_check']
assert verified['name']=='build'
data=bytearray()
baseline_data=bytearray()
for row in rows:
    if row['event']=='frame' and row.get('kind')=='grbl_data':
        payload=bytes.fromhex(row['payload_hex'])
        rcheck=accept_data(rcheck,row['read_check'],payload);data.extend(payload)
    if row is verified:
        baseline_data.extend(data)
assert baseline_data.decode('ascii')==verified['text']
assert verified['read_check']==(ready['read_check']+len(baseline_data))&65535
assert verified['write_check']==(w+3)&65535
for prefix in ('','inspection_'):
    decoder=FrameDecoder();decoded=[]
    for row in rows:
        if row['event']==prefix+'notify':decoded.extend(decoder.feed(bytes.fromhex(row['hex'])))
    assert decoded==[bytes.fromhex(row['hex']) for row in rows if row['event']==prefix+'frame']
    for row in rows:
        if row['event']==prefix+'frame' and row.get('kind')=='grbl_comm_status':
            assert row['device_tx_error']==row['device_rx_error']==0
allowed={VERIFY,SYSTEM_READ_ALL,GRBL_COMM_STATUS,ENABLE_BLE_RELIABLE,DISABLE_RELIABLE}
query_count=0;disabled=0;lost=False
for row in rows:
    if row['event']=='link_disconnect_requested':lost=True
    if row['event'] in ('write_attempt','inspection_write_attempt'):
        b=bytes.fromhex(row['hex'])
        if row['event']=='write_attempt' and lost:raise AssertionError('Bridge wrote after disconnect request')
        if b not in allowed:
            assert row['event']=='write_attempt' and b==encode_query('build',(w+3*query_count)&65535)
            query_count+=1
        if b==DISABLE_RELIABLE:
            assert row['event']=='inspection_write_attempt';disabled+=1
assert query_count==(2 if pending else 1) and disabled==1
delivered=one('tcp_delivered')
assert bytes.fromhex(delivered['hex'])==baseline_data
after=one('device_after_link_loss')
assert after['way']==2
dw=(after['write_check']-verified['write_check'])&65535
dr=(after['read_check']-verified['read_check'])&65535
if pending:
    one('pending_query_write_returned')
    assert one('pending_query_armed')['baseline_write_check']==verified['write_check']
    assert dw==3 and dr==len(baseline_data)==62
    assert one('link_disconnect_requested')['received_grbl_bytes']==len(baseline_data)
    arm_index=next(i for i,r in enumerate(rows) if r['event']=='pending_query_armed')
    inspection_index=next(i for i,r in enumerate(rows) if r['event']=='explicit_inspection_start')
    assert not any(r['event'] in ('query_verified','tcp_delivered') for r in rows[arm_index:inspection_index])
    # BlueZ can deliver the in-flight response before disconnect completes.
    # Receiving bytes alone does not validate the post-query device counters.
    assert len(data)-len(baseline_data) in (0, len(baseline_data))
    assert data[len(baseline_data):] in (b'', baseline_data)
    repeats=[r for r in rows if r['event']=='device_after_link_loss_repeat']
    assert len(repeats)==2
    for r in repeats:
        for k in ('way','write_check','read_check','device_tx_error','device_rx_error'):assert r[k]==after[k]
else:
    assert dw==dr==0
final=one('explicit_cleanup_verified')
assert final['way']==0 and final['final_app']==3
assert (final['write_check'],final['read_check'])==(after['write_check'],after['read_check'])
print(json.dumps(dict(offline_only=True, file=str(path.relative_to(root)), passed=True,
    fault='real host-requested BLE disconnect; not radio fade or device power loss',
    query_count=query_count, no_bridge_writes_after_disconnect=True,
    mode='pending' if pending else 'idle', device_write_delta_after_baseline=dw,
    device_read_delta_after_baseline=dr, tcp_delivered_queries=1,
    pending_response_bytes_received=len(data)-len(baseline_data),
    callback_to_tcp_close_seconds=delta('ble_disconnected','tcp_closed'),
    request_to_tcp_close_seconds=delta('link_disconnect_requested','tcp_closed'),
    device_before_explicit_cleanup=after, explicit_cleanup=final),ensure_ascii=False,indent=2))
