"""Fixed preflight-only reads or separately approved USB reviewed job comparison.

BLE job execution is exclusively through reviewed_job_bridge and real LightBurn.
"""
import argparse
import asyncio
import datetime
import json
from pathlib import Path

from neje_byte_stream import ByteStreamSession
from readonly_debugger import USBBackend, USB_PORT
from reviewed_job import JOB, review_export
from reviewed_job_bridge import EXPORT, JobChannel


class USBJobSession:
    def __init__(self, log):
        self.backend = USBBackend(USB_PORT, log)
        self.log = log

    async def start(self):
        await self.backend.open()

    async def verify(self):
        data = self.backend.port.read(4096)
        if data:
            self.log('usb_job_rx', hex=data.hex())
        return data

    async def write(self, payload):
        allowed = tuple(line + b'\n' for line in JOB) + (b'$$\n', b'$G\n', b'$#\n', b'?', b'G0\n')
        if payload not in allowed:
            raise ValueError('USB comparison fixed payload policy refused input')
        self.log('usb_job_write_attempt', hex=payload.hex())
        if self.backend.port.write(payload) != len(payload):
            raise RuntimeError('Partial USB write; execution unknown, never retry')
        return await self.verify()

    async def close(self):
        await self.backend.close()


async def inspect(session, log, execute_job=False):
    async def visible(data):
        log('probe_async_visible', hex=data.hex())
    try:
        await session.start()
        channel = JobChannel(session, log, visible)
        await channel.prepare()
        if execute_job:
            for index, line in enumerate(JOB):
                await channel.request(line + b'\n', f'usb_job_{index}')
            await channel.finish()
        log('reviewed_job_probe_passed', preflight_only=not execute_job,
            new_motion_commands_sent=execute_job)
    finally:
        await session.close()


async def run(args, log):
    if args.backend == 'usb':
        await inspect(USBJobSession(log), log, args.execute_reviewed_job)
    else:
        from bleak import BleakClient, BleakScanner
        targets = [d for d in await BleakScanner.discover(timeout=8) if d.name == 'NEJE 5 Plus']
        if len(targets) != 1:
            raise RuntimeError('Expected exactly one NEJE 5 Plus')
        async with BleakClient(targets[0], timeout=20) as client:
            await inspect(ByteStreamSession(client, log), log)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', choices=('usb', 'ble'), required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--execute-reviewed-job', action='store_true')
    p.add_argument('--allow-reviewed-zero-power-job', action='store_true')
    p.add_argument('--laser-power-isolated', action='store_true')
    p.add_argument('--log', type=Path)
    args = p.parse_args()
    if args.execute_reviewed_job and args.backend != 'usb':
        p.error('BLE jobs must originate in real LightBurn, not this probe')
    review = review_export(EXPORT)
    if not args.execute:
        print(json.dumps(dict(review=review, preflight_only=not args.execute_reviewed_job), ensure_ascii=False, indent=2))
        return 0
    if not (args.laser_power_isolated and args.log):
        p.error('Requires previously authorized isolated reads and new log')
    if args.execute_reviewed_job and not args.allow_reviewed_zero_power_job:
        p.error('New USB job requires separate onsite authorization')
    with args.log.open('x') as stream:
        def log(event, **fields):
            row = json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), event=event, **fields))
            stream.write(row + '\n')
            stream.flush()
            print(row, flush=True)
        try:
            asyncio.run(run(args, log))
        except Exception as error:
            log('error', message=repr(error))
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
