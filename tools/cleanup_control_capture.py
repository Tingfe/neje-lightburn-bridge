"""Explicit inspection/closure of this read-only capture's owned BLE route.

Never enables a route or sends GRBL text. Requires the terminated capture's
write counter, matching identity, and three stable status reads before disable.
"""
import argparse
import asyncio
import datetime
import json
from pathlib import Path

from grbl_readonly import DISABLE_RELIABLE
from neje_protocol import VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS
from tcp_readonly_bridge import ReliableSession, BASE
from control_capture_policy import ControlCapturePolicy


def owned_counter(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[-2]['event'] == 'pipeline_server_stopped'
    assert next(r for r in rows if r['event'] == 'pipeline_listening')[
        'control_capture_without_forwarding'] is True
    assert not any(r.get('forwarded') for r in rows if r['event'] == 'lightburn_control_captured')
    parser = ControlCapturePolicy()
    normal, attempted = bytearray(), bytearray()
    for row in rows:
        if row['event'] == 'pipeline_tcp_received':
            for kind, wire in parser.feed(bytes.fromhex(row['hex'])):
                if kind not in ('blocked_control', 'status'):
                    normal.extend(wire)
        elif row['event'] == 'pipeline_write_attempt':
            wire = bytes.fromhex(row['hex'])
            if wire != b'?':
                attempted.extend(wire)
    # The failed session may end mid-line; it must remain a prefix of the
    # initialization/read-only source, never an unreviewed business command.
    assert normal.startswith(attempted)
    current = next(r['write_check'] for r in rows if r['event'] == 'session_ready')
    for row in rows:
        if row['event'] == 'stream_write_reserved':
            assert row['before'] == current
            current = (current + len(bytes.fromhex(row['hex']))) & 65535
            assert row['after'] == current
    return current


async def inspect(args, log):
    from bleak import BleakClient, BleakScanner
    expected = owned_counter(args.capture)
    targets = [d for d in await BleakScanner.discover(timeout=8) if d.name == 'NEJE 5 Plus']
    if len(targets) != 1:
        raise RuntimeError('Expected one exact device')
    async with BleakClient(targets[0], timeout=20) as client:
        session = ReliableSession(client, log)
        await client.start_notify(BASE.format('ffea'), session.notify)
        identity = await session.exchange('inspection_verify', VERIFY, 'verify')
        system = await session.exchange('inspection_system', SYSTEM_READ_ALL, 'system_info')
        if identity['model'] != 55 or system['current_app'] != 3:
            raise RuntimeError('Identity/application changed; refusing closure')
        statuses = []
        for _ in range(3):
            status = await session.exchange('inspection_status', GRBL_COMM_STATUS, 'grbl_comm_status')
            session.clean(status)
            statuses.append({k: status[k] for k in
                ('way', 'write_check', 'read_check', 'device_tx_error', 'device_rx_error')})
            await asyncio.sleep(.2)
        log('owned_route_inspection', expected_write_check=expected, statuses=statuses)
        if not all(s == statuses[0] for s in statuses):
            raise RuntimeError('Counters changing; refusing closure')
        before = statuses[0]
        if before['write_check'] != expected or before['way'] not in (0, 2):
            raise RuntimeError('Owned counter/route mismatch; refusing takeover')
        if before['way'] == 2:
            await session.send('disable_owned_capture_route', DISABLE_RELIABLE)
            await asyncio.sleep(.2)
        final = await session.exchange('inspection_final_status', GRBL_COMM_STATUS, 'grbl_comm_status')
        session.clean(final)
        system = await session.exchange('inspection_final_system', SYSTEM_READ_ALL, 'system_info')
        if final['way'] != 0 or system['current_app'] != 3 or any(
                final[k] != before[k] for k in ('write_check', 'read_check')):
            raise RuntimeError('Closure not confirmed')
        log('explicit_cleanup_verified', way=0, final_app=3,
            write_check=final['write_check'], read_check=final['read_check'],
            grbl_writes=0, no_replay=True)
        await client.stop_notify(BASE.format('ffea'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture', required=True, type=Path)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--log', type=Path)
    args = p.parse_args()
    if not args.execute:
        print(json.dumps({'offline': True, 'owned_write_check': owned_counter(args.capture)}))
        return
    if not args.log:
        p.error('A new log is required')
    with args.log.open('x') as output:
        def log(event, **fields):
            output.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                event=event, **fields))+'\n')
            output.flush()
        asyncio.run(inspect(args, log))


if __name__ == '__main__':
    main()
