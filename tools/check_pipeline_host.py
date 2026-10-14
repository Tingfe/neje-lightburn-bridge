"""Fixed readonly pipeline probe: one baseline ?, 16 $I, one final ?.

Default offline. No TCP listener, motion, settings writes or control experiment.
"""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
import re
import time

from grbl_pipeline import GrblPipeline
from lightburn_init_bridge import reported_idle
from neje_byte_stream import ByteStreamSession
from reviewed_job_bridge import JobChannel


async def probe(log):
    from bleak import BleakClient, BleakScanner
    devices = [d for d in await BleakScanner.discover(timeout=8) if d.name == 'NEJE 5 Plus']
    if len(devices) != 1:
        raise RuntimeError('Expected exactly one NEJE 5 Plus')
    async with BleakClient(devices[0], timeout=20) as client:
        def guard(event, **fields):
            if event == 'stream_write_reserved':
                wire = bytes.fromhex(fields['hex'])
                if wire != b'?' and not (wire and wire.replace(b'$I\n', b'') == b''):
                    raise RuntimeError('Probe guard allows only build/status queries')
            log(event, **fields)
        session = ByteStreamSession(client, guard)
        received = bytearray()
        try:
            await session.start()
            async def push(data):
                received.extend(data)
                log('pipeline_response_chunk', text=data.decode('ascii'))
            channel = JobChannel(session, guard, push)
            baseline = await channel.status('pipeline_baseline')
            # JobChannel deliberately hides its internal status from deliver.
            log('pipeline_response_chunk', text=baseline+'\n')
            buffer = re.search(r'\|Bf:(\d+),(\d+)(?:\||>)', baseline)
            if not reported_idle(baseline) or not buffer:
                raise RuntimeError('Requires Idle/FS0 and a real reported receive buffer')
            rx_budget = int(buffer[2]) - 8
            char = client.services.get_characteristic('0000ffe9-0000-1000-8000-00805f9b34fb')
            capacity = min(256, char.max_write_without_response_size - 9)
            pipeline = GrblPipeline(session, push, guard, rx_budget=rx_budget, payload_capacity=capacity)
            # Preserve a possible partial asynchronous line crossing baseline.
            pipeline.output = channel.lines
            log('pipeline_configuration', idle_report=baseline, rx_budget=rx_budget,
                gatt_payload_capacity=capacity, queries=16, motion=False)
            for _ in range(16):
                pipeline.enqueue(b'$I\n')
            await pipeline.step()
            pipeline.realtime(b'?')
            deadline = time.monotonic()+10
            while not pipeline.drained:
                if time.monotonic()>deadline:
                    raise TimeoutError('Readonly pipeline response deadline; no replay')
                await pipeline.step()
                await asyncio.sleep(.01)
            if received.splitlines().count(b'ok') != 16:
                raise RuntimeError('Missing real terminal responses')
            log('readonly_pipeline_passed', build_queries=16, real_ok=16, motion=False)
        finally:
            await session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(dict(offline_only=True, build_queries=16, status_queries=2, motion=False)))
        return 0
    if not args.laser_power_isolated or not args.log:
        parser.error('Requires existing physical isolation and new log')
    with args.log.open('x') as output:
        def log(event, **fields):
            output.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                        event=event, **fields))+'\n')
            output.flush()
        try:
            asyncio.run(probe(log))
        except Exception as error:
            log('error', message=repr(error))
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
