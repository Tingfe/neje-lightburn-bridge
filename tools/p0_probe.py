"""NEJE P0 evidence probe. Defaults to scan only; --query enables two official queries."""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
import time
from neje_protocol import FrameDecoder, describe, VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS

BASE = '0000{}-0000-1000-8000-00805f9b34fb'


async def probe(args, log):
    from bleak import BleakScanner, BleakClient
    found = await BleakScanner.discover(timeout=args.scan_seconds, return_adv=True)
    matches = [d for d, a in found.values() if
               (d.address == args.device if args.device else (d.name or a.local_name) == 'NEJE 5 Plus')]
    log('discovery', matches=[{'name':d.name, 'address':d.address,
        'rssi':found[d.address][1].rssi} for d in matches])
    if not args.query:
        return
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one target. Use --device with a current scan UUID.')
    for trial in range(1, args.repeat + 1):
        decoder = FrameDecoder()
        recognized = []
        def notification(characteristic, data):
            log('notify', trial=trial, uuid=characteristic.uuid, hex=bytes(data).hex(' '),
                ascii=bytes(data).decode('ascii', errors='replace'))
            for frame in decoder.feed(data):
                details = describe(frame)
                log('frame', trial=trial, **details)
                if details['kind'] != 'unmapped':
                    recognized.append(details['kind'])
        async with BleakClient(matches[0], timeout=20,
                               disconnected_callback=lambda _:log('disconnected', trial=trial)) as client:
            log('connected', trial=trial, value=client.is_connected)
            for service in client.services:
                log('service', trial=trial, uuid=service.uuid, characteristics=[
                    {'uuid':c.uuid,'properties':c.properties} for c in service.characteristics])
            await client.start_notify(BASE.format('ffea'), notification)
            log('subscribed', trial=trial, uuid=BASE.format('ffea'))
            # Same order and write type as ConnBle.js; no speculative data.
            queries = [('verify', VERIFY), ('system_read_all', SYSTEM_READ_ALL)]
            if args.grbl_status:
                queries.append(('grbl_comm_status', GRBL_COMM_STATUS))
                queries.append(('system_read_after_status', SYSTEM_READ_ALL))
            for label, data in queries:
                log('write_attempt', trial=trial, label=label, uuid=BASE.format('ffe9'),
                    hex=data.hex(' '), response=False)
                await client.write_gatt_char(BASE.format('ffe9'), data, response=False)
                log('write_submitted', trial=trial, label=label)
                await asyncio.sleep(0.1)
            await asyncio.sleep(args.observe_seconds)
            await client.stop_notify(BASE.format('ffea'))
        success = {'verify', 'system_info', 'app_info'}.issubset(recognized) and not decoder.buffer
        if args.grbl_status:
            success = success and 'grbl_comm_status' in recognized
        log('trial_result', trial=trial, recognized=recognized, pending_hex=decoder.buffer.hex(' '), success=success)
        if not success:
            raise RuntimeError('No complete verified system response; BLE write submission is not success.')
        if trial < args.repeat:
            await asyncio.sleep(2)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--query', action='store_true', help='Send official verify + SystemRead(0xFF)')
    p.add_argument('--grbl-status', action='store_true', help='Query GRBL bridge status; no explicit mode command, firmware side effects unresolved')
    p.add_argument('--laser-power-isolated', action='store_true', help='User confirmed physical isolation; required for GRBL group queries after unexplained app change')
    p.add_argument('--device', help='Current macOS BLE UUID; default selects exact NEJE 5 Plus name')
    p.add_argument('--scan-seconds', type=float, default=10)
    p.add_argument('--observe-seconds', type=float, default=10)
    p.add_argument('--repeat', type=int, choices=[1,2], default=1)
    p.add_argument('--log', type=Path, default=Path(__file__).resolve().parents[1] / 'logs' /
                   ('p0-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '.jsonl'))
    args = p.parse_args()
    if args.grbl_status and not args.query:
        p.error('--grbl-status requires --query')
    if args.grbl_status and not args.laser_power_isolated:
        p.error('GRBL group queries require physical laser power isolation: application changed between prior observations; firmware side effects remain unresolved')
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open('x') as output:
        start = time.monotonic()
        def log(event, **fields):
            row = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       elapsed_s=round(time.monotonic()-start, 4), event=event, **fields)
            text = json.dumps(row, ensure_ascii=False)
            output.write(text+'\n'); output.flush(); print(text, flush=True)
        log('start', query=args.query, grbl_status=args.grbl_status, repeat=args.repeat,
            laser_power_isolated_user_attestation=args.laser_power_isolated)
        try:
            asyncio.run(probe(args, log))
        except Exception as error:
            log('error', type=type(error).__name__, message=str(error))
            return 1
        log('complete')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
