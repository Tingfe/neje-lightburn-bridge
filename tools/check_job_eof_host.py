"""Read-only real BLE/TCP EOF race probe; default offline, no job execution."""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
from reviewed_job_bridge import ReviewedJobBridge


async def probe(log, rows):
    from bleak import BleakScanner
    targets = [d for d in await BleakScanner.discover(timeout=8) if d.name == 'NEJE 5 Plus']
    if len(targets) != 1:
        raise RuntimeError('Expected exactly one NEJE 5 Plus')
    baseline, started = asyncio.Event(), asyncio.Event()

    def guarded_log(event, **fields):
        # JobChannel logs before any business write. Independently constrain
        # this host probe even though it exercises the reviewed bridge itself.
        if event == 'job_request_attempt' and fields['hex'] != '3f':
            raise RuntimeError('EOF probe permits only the fixed status query')
        log(event, **fields)
        if event == 'job_request_verified' and fields['name'] == 'connection_baseline':
            baseline.set()
        if event == 'job_request_attempt' and fields['name'] == 'tcp_status':
            started.set()

    bridge = ReviewedJobBridge(targets[0], guarded_log, timeout=15)
    server = await asyncio.start_server(bridge.handle, '127.0.0.1', 0)
    reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
    try:
        await asyncio.wait_for(baseline.wait(), 10)
        writer.write(b'?\n')
        await writer.drain()
        await asyncio.wait_for(started.wait(), 5)
        writer.write_eof()
        received = await asyncio.wait_for(reader.read(), 8)
        if received:
            raise RuntimeError('Response forwarded after probe EOF')
        if bridge.active:
            await asyncio.wait_for(asyncio.shield(bridge.active), 5)
        events = [row['event'] for row in rows]
        if 'job_eof_query_drain_finished' not in events or bridge.cleanup_unknown:
            raise RuntimeError('Expected bounded query drain and verified cleanup')
        if any(event in events for event in ('stream_write_uncertain', 'job_session_error', 'job_task_error')):
            raise RuntimeError('Unexpected uncertain write/session failure')
        log('readonly_eof_probe_passed', business_payloads=['?', '?'], motion_executed=False)
    finally:
        writer.close()
        await writer.wait_closed()
        if bridge.active:
            bridge.active.cancel()
            await asyncio.gather(bridge.active, return_exceptions=True)
        server.close()
        await server.wait_closed()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(dict(offline_only=True, payloads=['?', '?'], motion=False)))
        return 0
    if not args.laser_power_isolated or not args.log:
        parser.error('Requires existing physical isolation and a new evidence log')
    rows = []
    with args.log.open('x') as output:
        def log(event, **fields):
            row = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), event=event, **fields)
            rows.append(row)
            output.write(json.dumps(row) + '\n')
            output.flush()
        try:
            asyncio.run(probe(log, rows))
        except Exception as error:
            log('error', message=repr(error))
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
