"""Read-only USB/BLE preflight, or a separately approved fixed USB 10 mm job.

BLE job execution remains exclusively in real LightBurn and the TCP bridge.
No retries, user-supplied GCode, automatic return, settings writes or emission.
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
from readonly_debugger import USBBackend, USB_PORT
from reviewed_job import ReviewedJobParser
from reviewed_job_bridge import JobChannel
from reviewed_pipeline_job import ReviewedPipelineJob
from reviewed_ten_mm import TEN_MM_JOB, review, wire_lines

READS = (b'?', b'$$\n', b'$G\n', b'$#\n')


class TenMMUSBSession:
    def __init__(self, log, *, allow_job=False, reviewed_file=None):
        if reviewed_file is not None and not allow_job:
            raise ValueError('USB file requires the separately reviewed job scope')
        self.backend, self.log = USBBackend(USB_PORT, log), log
        self.allowed_job = b''.join(reviewed_file.wires if reviewed_file is not None
                                   else wire_lines()) if allow_job else b''
        self.attempted = 0
        self.failed = False

    async def start(self):
        await self.backend.open()

    async def verify(self):
        data = self.backend.port.read(4096)
        if data: self.log('usb_ten_mm_rx', hex=data.hex())
        return data

    async def write(self, wire):
        if self.failed:
            raise RuntimeError('Previous USB write uncertain; no replay')
        if wire in READS:
            pass
        elif wire == b'G0\n' and self.allowed_job and self.attempted == len(self.allowed_job):
            pass
        elif self.allowed_job and wire and self.allowed_job[self.attempted:self.attempted+len(wire)] == wire:
            self.attempted += len(wire)  # Reserve before attempting a potentially partial write.
        else:
            raise ValueError('USB fixed job/read policy refused input')
        self.log('usb_ten_mm_write_attempt', hex=wire.hex())
        self.failed = True
        if self.backend.port.write(wire) != len(wire):
            raise RuntimeError('Partial USB write; execution unknown, no retry')
        self.failed = False
        return await self.verify()

    async def close(self):
        await self.backend.close()


async def inspect(session, log, *, execute_job=False):
    async def visible(data):
        log('ten_mm_probe_visible', hex=data.hex())
    try:
        await session.start()
        channel = JobChannel(session, log, visible, max_x=10, completion_timeout=45)
        if not execute_job:
            await channel.prepare()
        else:
            baseline = await channel.status('ten_mm_usb_baseline')
            buffer = re.search(r'\|Bf:(\d+),(\d+)(?:\||>)', baseline)
            if not reported_idle(baseline) or not buffer:
                raise RuntimeError('Requires Idle/FS0 and real receive capacity')
            pipeline = GrblPipeline(session, visible, log, rx_budget=int(buffer[2])-8,
                                    payload_capacity=173, deliver_lines=True)
            pipeline.output = channel.lines
            job = ReviewedPipelineJob(pipeline, channel, log)
            pipeline.check_status = job.check_status
            parser = ReviewedJobParser(job=TEN_MM_JOB)
            for kind, wire in parser.feed(b''.join(wire_lines())):
                job.enqueue(kind, wire)
            parser.eof()
            deadline = time.monotonic()+45
            try:
                while not job.completed:
                    if time.monotonic() > deadline:
                        raise TimeoutError('USB fixed job deadline; no return or replay')
                    await job.advance()
                    if not job.completed:
                        await pipeline.step()
                        await asyncio.sleep(.01)
            finally:
                log('usb_ten_mm_stopped', job_completed=job.completed, job_submitted=job.submitted,
                    endpoint_unknown=bool(job.submitted and not job.completed),
                    pending_lines=len(pipeline.pending), queued_bytes=pipeline.queued_bytes,
                    staged_bytes=job.staged_bytes, no_replay=True)
        log('ten_mm_host_passed', preflight_only=not execute_job,
            motion_executed=execute_job, physical_displacement_independently_measured=False)
    finally:
        await session.close()


async def run(args, log):
    log('ten_mm_fixed_review', **review())
    if args.backend == 'usb':
        await inspect(TenMMUSBSession(log, allow_job=args.execute_reviewed_ten_mm_job), log,
                      execute_job=args.execute_reviewed_ten_mm_job)
    else:
        from bleak import BleakClient, BleakScanner
        targets = [d for d in await BleakScanner.discover(timeout=8) if d.name == 'NEJE 5 Plus']
        if len(targets) != 1: raise RuntimeError('Expected exactly one NEJE 5 Plus')
        async with BleakClient(targets[0], timeout=20) as client:
            await inspect(ByteStreamSession(client, log), log)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('usb', 'ble'), required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--execute-reviewed-ten-mm-job', action='store_true')
    parser.add_argument('--allow-reviewed-ten-mm-job', action='store_true')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if args.execute_reviewed_ten_mm_job and args.backend != 'usb':
        parser.error('BLE jobs must originate in real LightBurn')
    if not args.execute:
        print(json.dumps(dict(review=review(), preflight_only=not args.execute_reviewed_ten_mm_job)))
        return 0
    if not (args.laser_power_isolated and args.log):
        parser.error('Requires previously authorized isolated reads and a new log')
    if args.execute_reviewed_ten_mm_job and not args.allow_reviewed_ten_mm_job:
        parser.error('Requires separate onsite fixed 10 mm job authorization')
    with args.log.open('x') as output:
        def log(event, **fields):
            output.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                        event=event, **fields))+'\n')
            output.flush()
        try:
            asyncio.run(run(args, log))
        except Exception as error:
            log('error', message=repr(error))
            return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
