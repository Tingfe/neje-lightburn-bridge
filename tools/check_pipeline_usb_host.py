"""Pi USB counterpart of the fixed readonly BLE pipeline probe; default offline."""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
import re
import time

from grbl_pipeline import GrblPipeline
from lightburn_init_bridge import reported_idle
from readonly_debugger import USBBackend, USB_PORT
from reviewed_job_bridge import JobChannel


class USBReadonlyPipeline:
    def __init__(self, log):
        self.backend = USBBackend(USB_PORT, log)
        self.log = log
        self.failed = False
    async def write(self, wire):
        if self.failed:
            raise RuntimeError('Previous write uncertain; no replay')
        if wire != b'?' and wire != b'$I\n'*16:
            raise ValueError('Only the fixed read-only batch/status is allowed')
        self.log('usb_pipeline_write_attempt', hex=wire.hex())
        self.failed = True
        if self.backend.port.write(wire) != len(wire):
            raise RuntimeError('Partial USB write; no retry')
        self.failed = False
        return await self.verify()
    async def verify(self):
        data = self.backend.port.read(4096)
        if data:
            self.log('usb_pipeline_rx', hex=data.hex())
        return data


async def probe(log):
    session = USBReadonlyPipeline(log)
    try:
        await session.backend.open()
        received = bytearray()
        async def push(data):
            received.extend(data)
            log('pipeline_response_chunk', text=data.decode('ascii'))
        channel = JobChannel(session, log, push)
        baseline = await channel.status('pipeline_baseline')
        log('pipeline_response_chunk', text=baseline+'\n')
        buffer = re.search(r'\|Bf:(\d+),(\d+)(?:\||>)', baseline)
        if not reported_idle(baseline) or not buffer:
            raise RuntimeError('Requires Idle/FS0 and a real reported receive buffer')
        pipeline = GrblPipeline(session, push, log, rx_budget=int(buffer[2])-8, payload_capacity=173)
        pipeline.output = channel.lines
        log('pipeline_configuration', idle_report=baseline, rx_budget=pipeline.rx_budget,
            transport_chunk_capacity=173, queries=16, motion=False)
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
        await session.backend.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(dict(offline_only=True, backend='usb', build_queries=16, status_queries=2, motion=False)))
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
