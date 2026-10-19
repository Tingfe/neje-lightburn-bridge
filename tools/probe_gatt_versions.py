"""Read standard BLE Device Information version strings without protocol writes."""

import argparse
import asyncio
import datetime
import json
from pathlib import Path


BASE = '0000{}-0000-1000-8000-00805f9b34fb'
FIELDS = {
    '2a24': 'model_number',
    '2a27': 'hardware_revision',
    '2a28': 'firmware_revision',
    '2a29': 'manufacturer_name',
}


async def read_versions(log):
    from bleak import BleakClient, BleakScanner

    found = await BleakScanner.discover(timeout=10, return_adv=True)
    matches = [device for device, advert in found.values()
               if (device.name or advert.local_name) == 'NEJE 5 Plus']
    log('discovery', match_count=len(matches))
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one NEJE 5 Plus')
    async with BleakClient(matches[0], timeout=20) as client:
        log('connected', value=client.is_connected)
        for suffix, name in FIELDS.items():
            value = bytes(await client.read_gatt_char(BASE.format(suffix)))
            log('gatt_read', field=name, hex=value.hex(' '),
                text=value.decode('utf-8', errors='replace').rstrip('\0'))
    log('disconnected')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'offline_only': True, 'reads': list(FIELDS.values()),
                          'protocol_writes': 0}))
        return 0
    if not args.log:
        parser.error('--execute requires a new --log path')
    with args.log.open('x') as output:
        def log(event, **fields):
            row = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'event': event, **fields}
            line = json.dumps(row, ensure_ascii=False)
            output.write(line + '\n')
            output.flush()
            print(line, flush=True)
        try:
            asyncio.run(read_versions(log))
        except BaseException as exc:
            log('error', type=type(exc).__name__, message=str(exc))
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
