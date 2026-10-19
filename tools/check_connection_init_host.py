"""Check BLE transport initialization and cleanup without sending GRBL bytes.

Offline by default. Requires an explicit stationary/no-other-client attestation
because this probe briefly owns the same BLE transport used by the GUI.
"""
import argparse
import asyncio
import datetime
import json
from pathlib import Path

from neje_byte_stream import ByteStreamSession
from neje_protocol import GRBL_COMM_STATUS, SYSTEM_READ_INFO, VERIFY
from tcp_readonly_bridge import BASE


async def run(device, log, status_only=False, settle_seconds=0,
              status_write_response=False):
    from bleak import BleakClient

    async with BleakClient(device, timeout=20) as client:
        log('ble_connected', connected=client.is_connected)
        session = ByteStreamSession(client, log)
        if status_only:
            await client.start_notify(BASE.format('ffea'), session.notify)
            try:
                identity = await session.exchange('verify', VERIFY, 'verify')
                if identity['model'] != 55:
                    raise RuntimeError('Unexpected model')
                mode = await session.exchange('system_before', SYSTEM_READ_INFO, 'system_info')
                if mode['current_app'] != 3:
                    raise RuntimeError('Requires existing GRBL app 3')
                await asyncio.sleep(settle_seconds)
                if status_write_response:
                    session.frames.clear()
                    log('write_attempt', label='bridge_before_response',
                        hex=GRBL_COMM_STATUS.hex(' '), response=True)
                    await client.write_gatt_char(BASE.format('ffe9'), GRBL_COMM_STATUS,
                                                 response=True)
                    status = await session.wait_kind('grbl_comm_status')
                else:
                    status = await session.exchange('bridge_before', GRBL_COMM_STATUS,
                                                    'grbl_comm_status')
                log('status_only_complete', way=status['way'],
                    write_check=status['write_check'], read_check=status['read_check'],
                    grbl_bytes_sent=0, route_changed=False,
                    settle_seconds=settle_seconds,
                    status_write_response=status_write_response)
            finally:
                await client.stop_notify(BASE.format('ffea'))
            return
        try:
            await session.start()
            log('init_probe_ready', write_check=session.wcheck,
                read_check=session.rcheck, grbl_bytes_sent=0)
        finally:
            await session.close()
    log('init_probe_complete', grbl_bytes_sent=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--device')
    parser.add_argument('--device-stationary-confirmed', action='store_true')
    parser.add_argument('--no-other-client-confirmed', action='store_true')
    parser.add_argument('--status-only', action='store_true')
    parser.add_argument('--settle-seconds', type=float, default=0)
    parser.add_argument('--status-write-response', action='store_true')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'offline_only': True, 'grbl_bytes_sent': 0,
                          'transport': 'unchanged' if args.status_only else 'briefly enabled then disabled'}))
        return 0
    if not (args.device and args.device_stationary_confirmed
            and args.no_other_client_confirmed and args.log):
        parser.error('Requires target, both current-site confirmations, and new log path')
    if not 0 <= args.settle_seconds <= 5:
        parser.error('Settle time must be between 0 and 5 seconds')
    if args.settle_seconds and not args.status_only:
        parser.error('Settle time applies only to status-only probe')
    if args.status_write_response and not args.status_only:
        parser.error('Write-with-response applies only to status-only probe')
    with args.log.open('x') as stream:
        def log(event, **fields):
            row = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'event': event, **fields}
            line = json.dumps(row, ensure_ascii=False)
            stream.write(line + '\n')
            stream.flush()
            print(line, flush=True)
        try:
            asyncio.run(run(args.device, log, args.status_only, args.settle_seconds,
                            args.status_write_response))
        except BaseException as exc:
            log('init_probe_error', error=repr(exc))
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
