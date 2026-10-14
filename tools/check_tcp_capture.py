"""Offline audit of TCP/BLE traces; compares each query with the whitelist encoder."""
import json
from pathlib import Path
from collections import Counter
from neje_protocol import FrameDecoder
from grbl_readonly import encode_query
root=Path(__file__).resolve().parents[1]
reports=[]
for path in sorted(list((root/'logs').glob('tcp-bridge-host-*.jsonl')) + list((root/'logs').glob('lightburn-custom-host-*.jsonl'))):
    rows=[json.loads(x) for x in path.read_text().splitlines()]
    if rows[-1]['event']!='complete':raise AssertionError('Incomplete capture: '+str(path))
    decoder=FrameDecoder();decoded=[];trailing_partial=[]
    for row in rows:
        if row['event']=='tcp_connected':decoder=FrameDecoder()
        if row['event']=='notify':decoded.extend(decoder.feed(bytes.fromhex(row['hex'])))
        if row['event']=='tcp_closed':
            trailing_partial.append(len(decoder.buffer))
            decoder=FrameDecoder()
    assert decoded==[bytes.fromhex(x['hex']) for x in rows if x['event']=='frame']
    wcheck=None;queries=[];cleanup=[]
    for row in rows:
        if row['event']=='session_ready':wcheck=row['write_check']
        if row['event']=='write_attempt' and row['label'] in ('query_build','query_status'):
            name=row['label'][6:]
            assert bytes.fromhex(row['hex'])==encode_query(name,wcheck)
            wcheck=(wcheck+(3 if name=='build' else 1))&65535
            queries.append(name)
        if row['event']=='query_verified':assert row['write_check']==wcheck
        if row['event']=='frame' and row.get('kind')=='grbl_comm_status':
            assert row['device_tx_error']==row['device_rx_error']==0
        if row['event']=='cleanup_verified':
            assert row['way']==0 and row['final_app']==3
            cleanup.append(row)
    assert len(cleanup)==sum(x['event']=='session_ready' for x in rows)
    assert not any(x['event']=='cleanup_unconfirmed' for x in rows)
    assert not any(x['event']=='tcp_closed' and x['cleanup_unconfirmed'] for x in rows)
    reports.append(dict(file=str(path.relative_to(root)),queries=dict(Counter(queries)),
                        blocked_hex=dict(Counter(x['hex'] for x in rows if x['event']=='tcp_blocked')),
                        frames=len(decoded),trailing_partial_bytes_by_session=trailing_partial,cleanup=cleanup,passed=True))
print(json.dumps({'offline_only':True,'reports':reports,'passed':True},ensure_ascii=False,indent=2))
