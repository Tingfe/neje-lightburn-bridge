"""Explicit owned-route cleanup and read-only controller inspection, never replay.

Keep the original failed journal unchanged. A complete input boundary, unchanged
identity, real stationary zero-power reports, and independent audits are needed
before the manager can allow a separately requested new session. No reset/home.
"""
import asyncio
import hashlib
import json
from pathlib import Path

from check_byte_stream_capture import audit as byte_audit
from controlled_reviewed_job import require_empty_startup
from grbl_readonly import ENABLE_BLE_RELIABLE, DISABLE_RELIABLE, accept_data
from grbl_transport import encode_write
from inspect_job_disconnect_route import run as inspect_route, require_owned
from motion_parity_probe import preflight, require_ok
from neje_byte_stream import ByteStreamSession
from neje_protocol import (FrameDecoder, describe, VERIFY, SYSTEM_READ_ALL,
                           SYSTEM_READ_INFO, GRBL_COMM_STATUS)
from reviewed_job_bridge import JobChannel

READS=(('recovery_status_before',b'?'),('recovery_settings',b'$$\n'),
       ('recovery_modal',b'$G\n'),('recovery_parameters',b'$#\n'),
       ('recovery_startup',b'$N\n'),('recovery_status_after',b'?'))


def raw_frames(rows):
    decoder=FrameDecoder();decoded=[];logged=[]
    for row in rows:
        if row['event']=='notify':decoded.extend(decoder.feed(bytes.fromhex(row['hex'])))
        elif row['event']=='frame':
            packet=bytes.fromhex(row['hex']);logged.append(packet)
            if any(row.get(k)!=v for k,v in describe(packet).items()):
                raise ValueError('Decoded frame differs from raw notification')
    if decoded!=logged or decoder.buffer:
        raise ValueError('Incomplete or mismatched raw frame journal')


def input_boundary(payload):
    pending=bytearray()
    for byte in payload:
        if byte==0x18:raise ValueError('Reset invalidated position; separate positioning recovery required')
        if byte in (ord('?'),ord('!'),ord('~')):continue
        if byte in (10,13):pending.clear()
        elif 32<=byte<127:pending.append(byte)
        else:raise ValueError('Unsupported controller input in recovery proof')
    return not pending


def ownership_proof(path):
    raw=Path(path).read_bytes()
    if not raw.endswith(b'\n'):raise ValueError('Incomplete capture tail; do not infer ownership')
    rows=[json.loads(line) for line in raw.splitlines()]
    if any(row.get('simulated') for row in rows):raise ValueError('Recovery requires a real journal')
    raw_frames(rows)
    targets=[r for r in rows if r['event']=='pipeline_ble_target']
    ready=[r for r in rows if r['event']=='session_ready']
    if len(targets)!=1 or not targets[0]['address'] or len(ready)!=1:
        raise ValueError('Requires one recorded BLE identity and owned stream')
    w=confirmed_w=ready[0]['write_check'];r=confirmed_r=ready[0]['read_check']
    written=bytearray();pending=bytearray();verified=bytearray();reserved=None
    confirmed_write_size=0;last_status=None
    fixed={VERIFY,SYSTEM_READ_ALL,SYSTEM_READ_INFO,GRBL_COMM_STATUS,
           ENABLE_BLE_RELIABLE,DISABLE_RELIABLE}
    for row in rows:
        event=row['event']
        if event=='frame' and row['kind']=='grbl_data':
            payload=bytes.fromhex(row['payload_hex']);r=accept_data(r,row['read_check'],payload)
            pending.extend(payload)
        elif event=='frame' and row['kind']=='grbl_comm_status':last_status=row
        elif event=='stream_write_reserved':
            if reserved is not None or row['before']!=w:raise ValueError('Ambiguous reserved write')
            payload=bytes.fromhex(row['hex']);reserved=encode_write(payload,w)
            w=(w+len(payload))&65535
            if row['after']!=w:raise ValueError('Reserved counter differs from raw payload')
        elif event=='write_attempt' and bytes.fromhex(row['hex']) not in fixed:
            packet=bytes.fromhex(row['hex'])
            if packet!=reserved or row['label']!='stream_data':raise ValueError('Unowned GATT write')
            written.extend(packet[8:-1]);reserved=None
        elif event=='stream_verified':
            payload=bytes.fromhex(row['hex']);delta=(row['read_check']-confirmed_r)&65535
            if (len(payload)!=delta or payload!=bytes(pending[:delta]) or row['write_check']!=w
                    or not last_status or (last_status['way'],last_status['write_check'],last_status['read_check'])!=(2,w,row['read_check'])
                    or last_status['device_tx_error'] or last_status['device_rx_error']):
                raise ValueError('Stream verification is not supported by raw bytes/counters')
            del pending[:delta];verified.extend(payload);confirmed_r=row['read_check'];confirmed_w=w
            confirmed_write_size=len(written)
            if row['confirmed_total']!=len(verified):raise ValueError('Verified receive total differs')
    if reserved is not None or not written:raise ValueError('Incomplete attempted-write journal')
    unverified=bytes(written[confirmed_write_size:])
    if len(unverified)>256:raise ValueError('Unverified writes exceed one bounded GATT payload')
    # Preserve both possible acceptance boundaries. Never complete a partial line
    # by sending a query; a separate reset/positioning workflow is required.
    boundaries={str(0):input_boundary(bytes(written[:confirmed_write_size]))}
    if unverified:boundaries[str(len(unverified))]=input_boundary(bytes(written))
    return dict(device_address=targets[0]['address'],capture_sha256=hashlib.sha256(raw).hexdigest(),
        last_verified_write_counter=confirmed_w,last_verified_read_counter=confirmed_r,
        unverified_write_bytes=len(unverified),attempted_job_bytes=len(unverified),
        input_boundaries=boundaries,job_execution_or_completion_proven=False)


def audit_route(path,proof):
    rows=[json.loads(line) for line in Path(path).read_text().splitlines()];raw_frames(rows)
    allowed={VERIFY,SYSTEM_READ_INFO,GRBL_COMM_STATUS,DISABLE_RELIABLE}
    writes=[bytes.fromhex(r['hex']) for r in rows if r['event']=='write_attempt']
    if any(w not in allowed for w in writes):raise ValueError('Recovery route inspection wrote a stream command')
    def one(event):
        values=[r for r in rows if r['event']==event]
        if len(values)!=1:raise ValueError('Missing/ambiguous '+event)
        return values[0]
    target=one('inspection_target')
    if target['address']!=proof['device_address']:raise ValueError('Wrong inspected BLE identity')
    frames=[r for r in rows if r['event']=='frame']
    identities=[r for r in frames if r['kind']=='verify']
    systems=[r for r in frames if r['kind']=='system_info']
    statuses=[r for r in frames if r['kind']=='grbl_comm_status']
    if len(identities)!=1 or identities[0]['model']!=55 or len(systems)!=2 or any(r['current_app']!=3 for r in systems) or len(statuses)!=4:
        raise ValueError('Missing raw identity/status/cleanup replies')
    before=statuses[0];owned=require_owned(before,proof)
    for current in statuses[1:3]:
        if bytes.fromhex(current['hex'])!=bytes.fromhex(before['hex']):raise ValueError('Unstable inspection counters')
    final=statuses[-1]
    if final['way']!=0 or (final['write_check'],final['read_check'])!=(before['write_check'],before['read_check']):
        raise ValueError('Route closure not proved')
    if writes!=[VERIFY,SYSTEM_READ_INFO,GRBL_COMM_STATUS,GRBL_COMM_STATUS,GRBL_COMM_STATUS]+([DISABLE_RELIABLE] if before['way']==2 else [])+[GRBL_COMM_STATUS,SYSTEM_READ_INFO]:
        raise ValueError('Unexpected route inspection write sequence')
    done=one('owned_fault_route_cleanup_verified')
    if done['way']!=0 or done['final_app']!=3:raise ValueError('Missing cleanup result')
    boundary=proof['input_boundaries'][str(owned['device_write_delta'])]
    return dict(passed=True,route_closed=True,input_boundary_complete=boundary,**owned)


async def inspect_controller(proof,log):
    from bleak import BleakClient,BleakScanner
    targets=[d for d in await BleakScanner.discover(timeout=8)
             if d.name=='NEJE 5 Plus' and d.address==proof['device_address']]
    if len(targets)!=1:raise RuntimeError('Recorded device not uniquely available')
    async with BleakClient(targets[0],timeout=20) as client:
        session=ByteStreamSession(client,log)
        try:
            await session.start()
            async def visible(data):log('recovery_async_visible',hex=data.hex())
            channel=JobChannel(session,log,visible)
            results={}
            for name,wire in READS:results[name]=await channel.request(wire,name)
            validate_controller(results)
            log('recovery_controller_inspected',new_motion_commands=0,no_replay=True)
        finally:await session.close()


def validate_controller(results):
    for name,wire in READS:
        if wire!=b'?':require_ok(results[name])
    require_empty_startup(results['recovery_startup'])
    for name in ('recovery_status_before','recovery_status_after'):
        reports=[s for s in results[name].splitlines() if s.startswith('<')]
        if len(reports)!=1:raise ValueError('Missing unique stationary status')
        preflight(results['recovery_settings'],results['recovery_modal'],results['recovery_parameters'],reports[0])
        if '|FS:0,0' not in reports[0]:raise ValueError('Requires zero feed/power')


def audit_controller(path):
    rows=[json.loads(line) for line in Path(path).read_text().splitlines()]
    requests=[r for r in rows if r['event']=='job_request_attempt']
    if [(r['name'],bytes.fromhex(r['hex'])) for r in requests]!=list(READS):
        raise ValueError('Recovery controller inspection is not the exact read-only sequence')
    verified=[r for r in rows if r['event']=='job_request_verified']
    if [r['name'] for r in verified]!=[name for name,_ in READS]:raise ValueError('Missing real query completion')
    results={r['name']:r['text'] for r in verified}
    for name,_ in READS:
        raw=b''.join(bytes.fromhex(r['hex']) for r in rows if r['event']=='job_response_lines' and r['name']==name)
        if raw!=results[name].encode():raise ValueError('Controller result differs from real response lines')
    validate_controller(results)
    result=byte_audit(path,allowed=dict(READS),response_event='job_request_verified',
        completion_event='recovery_controller_inspected')
    return dict(passed=True,controller_idle=True,reported_position=[0,400,0,0],power=0,
                no_motion=True,no_reset=True,no_replay=True,stream_audit=result)
