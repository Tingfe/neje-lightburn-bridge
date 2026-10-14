"""Fixed $$/$G read-only comparison after explicit scope authorization."""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
import time

from lightburn_init_bridge import InitSession, REQUESTS, reported_idle
from readonly_debugger import USBBackend, USB_PORT

PROFILE_QUERIES = {'settings': b'$$\n', 'modal': b'$G\n'}


class ProfileSession(InitSession):
    request_table = dict(REQUESTS, **PROFILE_QUERIES)


async def run(args, log):
    async def inspect(query):
        for name in PROFILE_QUERIES:
            data = await query(name)
            text = data.decode('ascii')
            if text.replace('\r', '').splitlines()[-1] != 'ok':
                raise RuntimeError('Profile query returned a device error')
            log('profile_result', name=name, text=text)
    if args.backend == 'ble':
        from bleak import BleakClient, BleakScanner
        found = await BleakScanner.discover(timeout=8)
        targets = [d for d in found if (d.address == args.device if args.device else d.name == 'NEJE 5 Plus')]
        if len(targets) != 1:
            raise RuntimeError('Expected exactly one NEJE 5 Plus')
        async with BleakClient(targets[0], timeout=20) as client:
            session = ProfileSession(client, log)
            try:
                await session.start()
                await inspect(session.query)
                if not reported_idle((await session.query('status')).decode('ascii')):
                    raise RuntimeError('Final reported state not Idle / FS:0,0')
            finally:
                await session.close()
    else:
        backend = USBBackend(USB_PORT, log)
        try:
            await backend.open()
            if not reported_idle((await backend.query('status')).decode('ascii')):
                raise RuntimeError('Requires Idle / FS:0,0')
            async def query(name):
                payload = PROFILE_QUERIES[name]
                log('usb_profile_tx', name=name, hex=payload.hex())
                if backend.port.write(payload) != len(payload):
                    raise RuntimeError('Partial write; no retry')
                data = bytearray()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    chunk = backend.port.read(4096)
                    if chunk:
                        log('usb_profile_rx', name=name, hex=chunk.hex())
                        data.extend(chunk)
                        if len(data) > 8192:
                            raise RuntimeError('Profile response overflow')
                        lines = data.decode('ascii').replace('\r', '').split('\n')[:-1]
                        if any(line == 'ok' or line.startswith(('error:', 'ALARM:')) for line in lines):
                            return bytes(data)
                    await asyncio.sleep(.01)
                raise TimeoutError('Incomplete profile response; no retry')
            await inspect(query)
            if not reported_idle((await backend.query('status')).decode('ascii')):
                raise RuntimeError('Final reported state not Idle / FS:0,0')
        finally:
            await backend.close()
    log('profile_complete', backend=args.backend)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('usb', 'ble'), required=True)
    parser.add_argument('--device')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--allow-settings-read', action='store_true')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'offline_only': True, 'queries': ['$$', '$G'], 'writes_settings': False}))
        return 0
    if not args.allow_settings_read or not args.laser_power_isolated or not args.log:
        parser.error('Requires settings-read authorization, isolation, and new log path')
    with args.log.open('x') as stream:
        def log(event, **fields):
            row = json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'event': event, **fields})
            stream.write(row + '\n'); stream.flush(); print(row, flush=True)
        try:
            asyncio.run(run(args, log))
        except Exception as exc:
            log('error', error=repr(exc))
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
