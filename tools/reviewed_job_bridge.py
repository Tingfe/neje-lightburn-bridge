"""Single-use bridge for one reviewed LightBurn zero-power job; default offline.

An experimental small-job gate, not a general machining or throughput profile.
No retries, automatic return, reconnect, synthetic ok, reset or unlock.
"""
import argparse
import asyncio
import contextlib
import datetime
import json
from pathlib import Path
import signal
import time

from machine_profile_probe import PROFILE_QUERIES
from lightburn_init_bridge import reported_idle
from motion_parity_probe import preflight, check_position, require_ok
from neje_byte_stream import ByteStreamSession
from reviewed_job import JOB, ReviewedJobParser, review_export
from stream_tcp_bridge import OutputLines

EXPORT = Path(__file__).resolve().parents[1] / 'evidence/stream-transport/lightburn-zero-power-reviewed-v2.gc'


class JobChannel:
    """One outstanding command, real terminals only, continuous byte reception.

    This deliberately serializes the first tiny job. It does not claim the
    throughput needed for arbitrary vector/raster work.
    """
    def __init__(self, session, log, deliver, *, max_x=1, completion_timeout=8,
                 can_write=None, request_timeout=8):
        self.session, self.log, self.deliver = session, log, deliver
        self.lines = OutputLines()
        self.max_x, self.completion_timeout = max_x, completion_timeout
        self.can_write = can_write
        self.request_timeout = request_timeout

    @staticmethod
    def inspect(lines):
        if any(line.startswith((b'error:', b'ALARM:', b'Grbl ')) for line in lines):
            raise RuntimeError('Controller error/alarm/reset; discard unsent work')

    async def idle(self):
        data = await self.session.verify()
        if not data:
            return
        lines = self.lines.feed(data)
        if lines:
            self.log('job_async_lines', hex=b''.join(lines).hex())
            await self.deliver(b''.join(lines))
        self.inspect(lines)
        if any(line.strip() == b'ok' or line.startswith(b'<') for line in lines):
            raise RuntimeError('Unexpected terminal/status without outstanding request')

    async def request(self, payload, name, public=False):
        # Drain already-confirmed asynchronous data before a new request. A late
        # terminal is not silently assigned to the next command.
        await self.idle()
        if self.can_write is not None and not self.can_write():
            raise RuntimeError('Peer closed; no new internal command or automatic return')
        self.log('job_request_attempt', name=name, hex=payload.hex(), public=public)
        data = await self.session.write(payload)
        complete = []
        deadline = time.monotonic() + self.request_timeout
        total = 0
        while True:
            if data:
                total += len(data)
                if total > 8192:
                    raise RuntimeError('Response exceeds job review limit')
                lines = self.lines.feed(data)
                if lines:
                    self.log('job_response_lines', name=name, public=public,
                             hex=b''.join(lines).hex())
                # Keep fragments until newline, including a MSG crossing from
                # public to internal requests. Never forward a suffix alone or
                # expose an internal query's ok as a LightBurn acknowledgement.
                visible = lines if public else [line for line in lines if line.startswith(b'[MSG:')]
                if visible:
                    await self.deliver(b''.join(visible))
                complete.extend(lines)
                self.inspect(complete)
                terminals = [line for line in complete if line.strip() == b'ok']
                statuses = [line for line in complete if line.startswith(b'<') and line.rstrip().endswith(b'>')]
                if len(terminals) > 1 or len(statuses) > 1:
                    raise RuntimeError('Ambiguous response; no retry')
                if (payload == b'?' and statuses) or (payload != b'?' and terminals):
                    if (payload == b'?' and terminals) or (payload != b'?' and statuses):
                        raise RuntimeError('Response belongs to an unexpected request')
                    text = b''.join(complete).decode('ascii')
                    self.log('job_request_verified', name=name, text=text, public=public)
                    return text
            if time.monotonic() > deadline:
                raise TimeoutError('Missing real response; execution unknown, never replay')
            await asyncio.sleep(.01)
            data = await self.session.verify()

    async def status(self, name):
        text = await self.request(b'?', name)
        reports = [line for line in text.splitlines() if line.startswith('<')]
        if len(reports) != 1:
            raise RuntimeError('Missing unique status report')
        return reports[0]

    async def prepare(self):
        settings = await self.request(PROFILE_QUERIES['settings'], 'preflight_settings')
        modal = await self.request(PROFILE_QUERIES['modal'], 'preflight_modal')
        parameters = await self.request(b'$#\n', 'preflight_parameters')
        for text in (settings, modal, parameters):
            require_ok(text)
        status = await self.status('preflight_status')
        preflight(settings, modal, parameters, status, start_x=0)
        if not reported_idle(status):
            raise RuntimeError('Preflight requires reported Idle and FS:0,0')
        values = dict(line[1:].split('=', 1) for line in settings.splitlines() if line.startswith('$'))
        if values.get('30') != '1000' or values.get('32') != '1':
            raise RuntimeError('Laser scale/mode differs from reviewed baseline')
        self.log('job_preflight_passed', position=[0, 400, 0, 0], power=0, feed_mm_min=60)
        return settings

    async def finish(self):
        deadline = time.monotonic() + self.completion_timeout
        while True:
            report = await self.status('completion_status')
            state, position = check_position(report, 0, require_idle=False, max_x=self.max_x)
            if state == 'Idle':
                check_position(report, 0, max_x=self.max_x)
                if not reported_idle(report):
                    raise RuntimeError('Endpoint requires reported Idle and FS:0,0')
                break
            if time.monotonic() > deadline:
                raise TimeoutError('Job endpoint unknown; no return or replay')
            await asyncio.sleep(.1)
        modal = await self.request(b'$G\n', 'completion_modal')
        require_ok(modal)
        import re
        match = re.search(r'^\[GC:(.*)\]$', modal, re.MULTILINE)
        if not match or not {'G21', 'G90', 'G94', 'G54', 'M5', 'S0'} <= set(match[1].split()):
            raise RuntimeError('Final modal state differs from review')
        require_ok(await self.request(b'G0\n', 'restore_rapid_mode'))
        check_position(await self.status('restored_status'), 0, max_x=self.max_x)
        self.log('job_completion_verified', position=position, modal_before_restore=modal,
                 physical_displacement_independently_measured=False, no_replay=True)


class ReviewedJobBridge:
    def __init__(self, device, log, timeout=120):
        self.device, self.log, self.timeout = device, log, timeout
        self.active = None
        self.used = False
        self.cleanup_unknown = False

    async def handle(self, reader, writer):
        if self.used:
            self.log('job_tcp_rejected', reason='single_connection_consumed_no_reconnect')
            writer.close()
            await writer.wait_closed()
            return
        self.used = True
        self.active = asyncio.current_task()
        parser = ReviewedJobParser()
        queue = asyncio.Queue(maxsize=16)
        eof = asyncio.Event()
        current_kind = None
        self.log('job_tcp_connected', peer=writer.get_extra_info('peername'))

        async def deliver(data):
            if eof.is_set():
                self.log('job_tcp_delivery_suppressed', hex=data.hex(), reason='peer_eof')
                return
            writer.write(data)
            await asyncio.wait_for(writer.drain(), 2)
            self.log('job_tcp_delivered', hex=data.hex())

        async def receive():
            while True:
                data = await reader.read(512)
                if not data:
                    self.log('job_tcp_eof', accepted_commands=parser.index, pending_hex=parser.pending.hex())
                    parser.eof()
                    eof.set()
                    return
                self.log('job_tcp_receive', hex=data.hex())
                for kind, wire in parser.feed(data):
                    await queue.put((kind, wire))

        async def operate():
            nonlocal current_kind
            from bleak import BleakClient
            async with BleakClient(self.device, timeout=20,
                    disconnected_callback=lambda client: self.log('job_ble_disconnected')) as client:
                session = ByteStreamSession(client, self.log)
                sent = 0
                job_started = job_completed = False
                try:
                    await session.start()
                    channel = JobChannel(session, self.log, deliver)
                    check_position(await channel.status('connection_baseline'), 0)
                    while not eof.is_set():
                        try:
                            kind, wire = await asyncio.wait_for(queue.get(), .15)
                        except TimeoutError:
                            if eof.is_set():
                                break
                            await channel.idle()
                            continue
                        if eof.is_set():
                            # Leave the dequeued item accounted as unsent.
                            queue.put_nowait((kind, wire))
                            break
                        if kind == 'job' and sent == 0:
                            await channel.prepare()
                            job_started = True
                        # The reviewed job has tiny lines; reject oversize instead
                        # of sending an unreviewed frame segmentation here.
                        self.log('job_line_sending', kind=kind, hex=wire.hex(), job_index=sent)
                        current_kind = kind
                        try:
                            await channel.request(wire, 'tcp_' + kind, public=True)
                        finally:
                            current_kind = None
                        if kind == 'job':
                            sent += 1
                            if sent == len(JOB):
                                await channel.finish()
                                job_completed = True
                finally:
                    self.log('job_transmission_stopped', sent_commands=sent,
                             endpoint_unknown=job_started and not job_completed,
                             job_completed=job_completed, no_automatic_return=True, no_replay=True)
                    try:
                        await session.close()
                    except BaseException:
                        self.cleanup_unknown = True
                        raise

        tasks = [asyncio.create_task(receive()), asyncio.create_task(operate())]
        try:
            done, _ = await asyncio.wait(tasks, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                self.log('job_session_timeout')
            for task in done:
                task.result()
            # A clean EOF may race a GATT write of '?'. Finish only this already
            # started read, within a fixed bound; do not dequeue another line or
            # extend an in-flight job/initialization command after disconnection.
            if eof.is_set() and not tasks[1].done() and current_kind == 'status':
                self.log('job_eof_query_drain_started', timeout_seconds=2)
                try:
                    await asyncio.wait_for(asyncio.shield(tasks[1]), 2)
                except TimeoutError:
                    self.log('job_eof_query_drain_timeout', no_replay=True)
                else:
                    self.log('job_eof_query_drain_finished', no_replay=True)
        except asyncio.CancelledError:
            self.log('job_session_cancelled')
            raise
        except Exception as error:
            self.log('job_session_error', type=type(error).__name__, message=str(error))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            for result in await asyncio.gather(*tasks, return_exceptions=True):
                if isinstance(result, Exception):
                    self.log('job_task_error', message=str(result))
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            self.log('job_tcp_closed', discarded_unsent=queue.qsize(), cleanup_unknown=self.cleanup_unknown)
            self.active = None


async def run(args, log):
    from bleak import BleakScanner
    targets = [d for d in await BleakScanner.discover(timeout=8) if d.name == 'NEJE 5 Plus']
    if len(targets) != 1:
        raise RuntimeError('Expected exactly one NEJE 5 Plus')
    bridge = ReviewedJobBridge(targets[0], log, args.seconds)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    server = await asyncio.start_server(bridge.handle, '127.0.0.1', 4002)
    log('job_listening', bind='127.0.0.1:4002', review=review_export(EXPORT))
    try:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), args.seconds)
    finally:
        server.close()
        if bridge.active:
            bridge.active.cancel()
            await asyncio.gather(bridge.active, return_exceptions=True)
        await server.wait_closed()
        log('job_server_stopped', cleanup_unknown=bridge.cleanup_unknown)
    if bridge.cleanup_unknown:
        raise RuntimeError('Cleanup unknown; independent explicit recovery required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--allow-reviewed-zero-power-job', action='store_true')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--seconds', type=int, default=120)
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    review = review_export(EXPORT)
    if not args.execute:
        print(json.dumps(review, ensure_ascii=False, indent=2))
        return 0
    if not (args.allow_reviewed_zero_power_job and args.laser_power_isolated and args.log):
        parser.error('Requires separately approved reviewed job, physical isolation and new log')
    if not 1 <= args.seconds <= 300:
        parser.error('seconds must be 1..300')
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
