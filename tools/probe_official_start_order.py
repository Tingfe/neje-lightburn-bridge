"""Probe the official Windows reliable-BLE start order without GRBL bytes.

Offline by default. The official worker sends SetStatus(BLE) before GetStatus.
This probe records that exact ordering, then attempts one owned-route shutdown.
It must never be used while another client owns the device.
"""
import argparse
import asyncio
import datetime
import json
from pathlib import Path

from grbl_readonly import DISABLE_RELIABLE, ENABLE_BLE_RELIABLE
from neje_protocol import GRBL_COMM_STATUS, SYSTEM_READ_INFO, VERIFY
from tcp_readonly_bridge import BASE, ReliableSession


async def run(device, log):
    from bleak import BleakClient

    async with BleakClient(device, timeout=20) as client:
        session = ReliableSession(client, log)
        enabled_attempted = False
        await client.start_notify(BASE.format('ffea'), session.notify)
        try:
            identity = await session.exchange('verify', VERIFY, 'verify')
            if identity['model'] != 55:
                raise RuntimeError('Unexpected model')
            mode = await session.exchange('system_before', SYSTEM_READ_INFO, 'system_info')
            if mode['current_app'] != 3:
                raise RuntimeError('Requires existing GRBL app 3')

            # The official v6.8.5 GrblCommWorker.Start sends SetStatus first.
            # The write may reach the device even if the call fails.
            enabled_attempted = True
            log('official_order_enable_attempted', grbl_bytes_sent=0)
            await session.send('official_order_enable', ENABLE_BLE_RELIABLE)
            await asyncio.sleep(.2)
            status = await session.exchange('official_order_status', GRBL_COMM_STATUS,
                                            'grbl_comm_status')
            session.clean(status)
            log('official_order_status_observed', way=status['way'],
                write_check=status['write_check'], read_check=status['read_check'],
                grbl_bytes_sent=0)
            if status['way'] != 2:
                raise RuntimeError('BLE route did not report way=2')
        finally:
            if enabled_attempted:
                if not client.is_connected:
                    log('official_order_cleanup_unconfirmed',
                        reason='BLE disconnected before disable; route unknown')
                else:
                    try:
                        await session.send('official_order_disable', DISABLE_RELIABLE)
                        await asyncio.sleep(.2)
                        final = await session.exchange('official_order_final_status',
                                                       GRBL_COMM_STATUS,
                                                       'grbl_comm_status')
                        session.clean(final)
                        if final['way'] != 0:
                            raise RuntimeError('Final route is not way=0')
                        mode_after = await session.exchange('system_after', SYSTEM_READ_INFO,
                                                            'system_info')
                        if mode_after['current_app'] != 3:
                            raise RuntimeError('Final app is not GRBL 3')
                        log('official_order_cleanup_verified', way=0, app=3,
                            grbl_bytes_sent=0)
                    except BaseException as exc:
                        log('official_order_cleanup_unconfirmed', error=repr(exc),
                            route_unknown=True)
                        raise
            if client.is_connected:
                await client.stop_notify(BASE.format('ffea'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--device')
    parser.add_argument('--device-stationary-confirmed', action='store_true')
    parser.add_argument('--laser-isolated-confirmed', action='store_true')
    parser.add_argument('--no-other-client-confirmed', action='store_true')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'offline_only': True, 'order': ['verify', 'system_info',
              'enable_ble', 'get_status', 'disable_ble', 'get_status', 'system_info'],
              'grbl_bytes_sent': 0, 'motion_commands_sent': 0}))
        return 0
    if not (args.device and args.device_stationary_confirmed
            and args.laser_isolated_confirmed and args.no_other_client_confirmed
            and args.log):
        parser.error('Requires device, current-site confirmations, and a new log path')
    with args.log.open('x') as stream:
        def log(event, **fields):
            row = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'event': event, **fields}
            line = json.dumps(row, ensure_ascii=False)
            stream.write(line + '\n')
            stream.flush()
            print(line, flush=True)
        try:
            asyncio.run(run(args.device, log))
        except BaseException as exc:
            log('official_order_probe_error', error=repr(exc))
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
