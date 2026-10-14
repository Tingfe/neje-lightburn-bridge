"""Pipelined BLE/TCP bridge; initialization only unless fixed job enabled.

Preserves original TCP line bytes and real responses. Reviewed file execution
requires a matching digest and separate motion/control/isolation authorization.
No unreviewed job CLI, retries, reconnect, synthetic ok or homing.
"""
import argparse
import os
import asyncio
import contextlib
import datetime
import json
from pathlib import Path
import re
import signal
import time

from grbl_pipeline import GrblPipeline
from controlled_grbl_pipeline import ControlledGrblPipeline
from controlled_job_policy import ControlledJobPolicy
from controlled_reviewed_job import ControlledReviewedJob, require_empty_startup
from lightburn_init_bridge import reported_idle
from neje_byte_stream import ByteStreamSession
from reviewed_job import ReviewedJobParser
from reviewed_job_bridge import JobChannel
from reviewed_pipeline_job import ReviewedPipelineJob
from reviewed_ten_mm import TEN_MM_JOB, review
from control_capture_policy import ControlCapturePolicy
from passthrough_policy import PassthroughPolicy
from reviewed_job_file import load as load_reviewed_file, PROFILES, MAX_ACTIVE_SECONDS


class PipelineTCPBridge:
    def __init__(self, device, log, timeout=90, *, reviewed_ten_mm=False,
                 capture_controls=False, control_review=False, reviewed_file=None,
                 transparent=False,
                 clock=time.monotonic):
        self.device, self.log, self.timeout = device, log, timeout
        self.active = None
        self.used = False
        self.cleanup_unknown = False
        self.failure_reason = None
        self.finished = asyncio.Event()
        self.reviewed_ten_mm = reviewed_ten_mm
        self.reviewed_file = reviewed_file
        self.clock = clock
        if reviewed_file is not None and (reviewed_ten_mm or not control_review):
            raise ValueError('File review requires its own exclusive controlled profile')
        self.motion_profile = reviewed_ten_mm or reviewed_file is not None
        self.job_commands = reviewed_file.commands if reviewed_file is not None else TEN_MM_JOB
        self.job_timeout = reviewed_file.execution_budget() if reviewed_file is not None else 45
        self.profile = 'controlled_file_once' if reviewed_file is not None else \
                       'controlled_ten_mm_once' if control_review else \
                       'reviewed_ten_mm_once' if reviewed_ten_mm else 'initialization_only'
        if self.motion_profile and capture_controls:
            raise ValueError('Control capture is initialization-only; cannot combine with motion')
        self.capture_controls = capture_controls
        if control_review and (not self.motion_profile or capture_controls):
            raise ValueError('Control review requires an explicitly reviewed job profile')
        self.control_review = control_review
        self.transparent = transparent

    async def handle(self, reader, writer):
        if self.used:
            # Single-use session: a client that disconnects cannot reconnect here.
            # Recorded so operators can see that a new session is required rather
            # than retrying in the LightBurn UI.
            self.log('pipeline_tcp_rejected', reason='connection_consumed_no_reconnect',
                     new_session_required=True)
            writer.close()
            await writer.wait_closed()
            return
        self.used = True
        self.active = asyncio.current_task()
        ready, eof, space = asyncio.Event(), asyncio.Event(), asyncio.Event()
        parser = ControlCapturePolicy() if self.capture_controls else ReviewedJobParser(
            job=self.job_commands if self.motion_profile else ())
        if self.control_review:
            parser = ControlledJobPolicy(self.job_commands)
        if self.transparent:
            # Opt-in USB-equivalent mode: nothing is matched against a reviewed job and
            # no whitelist applies. See docs/transparent-bridge-plan.md.
            parser = PassthroughPolicy()
        pipeline = None
        job = None
        self.log('pipeline_tcp_connected', peer=writer.get_extra_info('peername'))

        async def deliver(data):
            if eof.is_set():
                self.log('pipeline_tcp_delivery_suppressed', hex=data.hex(), reason='peer_eof')
            else:
                writer.write(data)
                await asyncio.wait_for(writer.drain(), 2)
                self.log('pipeline_tcp_delivered', hex=data.hex())

        async def receive():
            await ready.wait()
            while True:
                data = await reader.read(512)
                if eof.is_set():
                    self.log('pipeline_input_after_close_discarded', hex=data.hex(), no_replay=True)
                    return
                if not data:
                    self.log('pipeline_tcp_eof', pending_hex=parser.pending.hex())
                    eof.set()
                    parser.eof()
                    return
                self.log('pipeline_tcp_received', hex=data.hex())
                for kind, wire in parser.feed(data):
                    if self.control_review and kind in ('hold', 'resume', 'cancel'):
                        if kind == 'cancel':
                            job.cancel()
                        pipeline.realtime(wire)
                        self.log('pipeline_ingress', kind=kind, hex=wire.hex())
                        continue
                    if kind == 'blocked_control':
                        self.log('lightburn_control_captured', hex=wire.hex(),
                                 forwarded=False, controller_success_claimed=False)
                        continue
                    while True:
                        try:
                            if kind in ('hold', 'resume', 'cancel'):
                                # Realtime bytes must go out of band; queueing them as
                                # ordinary data would leave them sitting in a line buffer.
                                pipeline.realtime(wire)
                            elif self.control_review and kind == 'status': pipeline.realtime(wire)
                            elif job is not None: job.enqueue(kind, wire)
                            elif kind == 'status': pipeline.realtime(wire)
                            else: pipeline.enqueue(wire)
                            break
                        except asyncio.QueueFull:
                            if self.control_review:
                                raise RuntimeError('Control ingress overflow; fail closed instead of blocking Stop')
                            space.clear()
                            await space.wait()
                    self.log('pipeline_ingress', kind=kind, hex=wire.hex())

        async def operate():
            nonlocal pipeline, job
            from bleak import BleakClient
            async with BleakClient(self.device, timeout=20,
                    disconnected_callback=lambda client: self.log('pipeline_ble_disconnected')) as client:
                session = ByteStreamSession(client, self.log)
                try:
                    await session.start()
                    channel = JobChannel(session, self.log, deliver,
                        max_x=10 if self.motion_profile else 1,
                        completion_timeout=self.job_timeout if self.motion_profile else 8,
                        can_write=lambda: not eof.is_set())
                    baseline = await channel.status('pipeline_tcp_baseline')
                    buffer = re.search(r'\|Bf:(\d+),(\d+)(?:\||>)', baseline)
                    if not reported_idle(baseline) or not buffer:
                        raise RuntimeError('Requires Idle/FS0 and real Bf receive capacity')
                    char = client.services.get_characteristic('0000ffe9-0000-1000-8000-00805f9b34fb')
                    pipeline_class = ControlledGrblPipeline if self.control_review else GrblPipeline
                    pipeline = pipeline_class(session, deliver, self.log,
                        rx_budget=int(buffer[2])-8,
                        payload_capacity=min(256, char.max_write_without_response_size-9),
                        deliver_lines=self.motion_profile)
                    pipeline.output = channel.lines
                    if self.motion_profile:
                        job_class = ControlledReviewedJob if self.control_review else ReviewedPipelineJob
                        job = job_class(pipeline, channel, self.log,
                            can_write=lambda: not eof.is_set(), job_commands=self.job_commands,
                            **dict(clock=self.clock,active_timeout=self.job_timeout,
                                   position_bounds=self.reviewed_file.position_bounds if self.reviewed_file
                                       else ((0,10),(400,400),(0,0),(0,0)),
                                   power_ceiling=self.reviewed_file.summary()['power_ceiling']
                                       if self.reviewed_file else 0,
                                   end_position=self.reviewed_file.end_position
                                       if self.reviewed_file else (0,400,0,0))
                              if self.control_review else {})
                        pipeline.check_status = job.check_status
                        if self.control_review:
                            settings=await channel.prepare()
                            if self.reviewed_file is not None:self.reviewed_file.require_rapid_settings(settings)
                            startup = await channel.request(b'$N\n', 'control_review_startup_blocks')
                            require_empty_startup(startup)
                            job.prepared = True
                            self.log('control_review_preflight_passed', startup_blocks_empty=True)
                    self.log('pipeline_tcp_ready', baseline=baseline, rx_budget=pipeline.rx_budget,
                             payload_capacity=pipeline.capacity,
                             query_barriers=True, max_pending_lines=pipeline.max_pending_lines,
                             profile=self.profile, job_timeout_seconds=self.job_timeout,
                             file_geometry_profile=self.reviewed_file.geometry_profile if self.reviewed_file else None,
                             reviewed_position_bounds=self.reviewed_file.position_bounds if self.reviewed_file else None,
                             reviewed_source_sha256=self.reviewed_file.sha256 if self.reviewed_file else None)
                    ready.set()
                    previous, deadline = None, self.clock()+8
                    next_file_poll = self.clock()
                    while not eof.is_set():
                        if job is not None:
                            await job.advance()
                            if eof.is_set(): break
                        if (self.control_review and job.submitted and not job.completed
                                and pipeline.ordinary_allowed and not pipeline.statuses
                                and not pipeline.controls and pipeline.internal is None
                                and self.clock()>=next_file_poll):
                            pipeline.realtime(b'?')
                            next_file_poll = self.clock()+.5
                        wrote = await pipeline.step()
                        space.set()
                        if self.control_review and pipeline.cancelled:
                            self.log('controlled_session_cancelled', status=pipeline.last_status,
                                     requires_explicit_recovery=True, no_replay=True)
                            break
                        key = (pipeline.pending[0].sequence if pipeline.pending else None,
                               pipeline.statuses if not pipeline.pending else None,
                               bool(pipeline.output.pending))
                        if key != previous or pipeline.drained:
                            response_timeout = self.job_timeout if job and job.submitted and not job.completed else 8
                            previous, deadline = key, self.clock()+response_timeout
                        suspended = self.control_review and pipeline.ordinary_timeout_suspended
                        if suspended:
                            # The core retains bounded transition/status timers.
                            # Ordinary ACKs may legitimately stop during Hold.
                            previous, deadline = None, self.clock()+self.job_timeout
                        if not suspended and self.clock()>deadline:
                            raise TimeoutError('Missing real response; stop without replay')
                        if not wrote: await asyncio.sleep(.02)
                    # EOF must not dequeue new lines or realtime bytes. Receive
                    # only what was already attempted, with the outer 2s bound.
                    while pipeline.pending or pipeline.statuses or pipeline.output.pending:
                        await pipeline.step(receive_only=True)
                        await asyncio.sleep(.01)
                    if job is not None and job.submitted and not job.completed and not (
                            self.control_review and pipeline.cancelled):
                        self.failure_reason = self.failure_reason or 'peer_eof_with_unfinished_job'
                        self.log('pipeline_job_incomplete_at_eof', submitted=job.submitted,
                                 expected=job.job_length, endpoint_unknown=True, no_replay=True)
                    self.log('controlled_cancel_responses_drained' if self.control_review and
                             pipeline.cancelled else 'pipeline_eof_responses_drained', no_replay=True)
                except Exception as error:
                    # A subsequent route-close error must not hide the original
                    # acceptance/transport failure. Cleanup remains separately unknown.
                    self.failure_reason = self.failure_reason or repr(error)
                    raise
                finally:
                    # Freeze ingress before awaiting route shutdown. A live TCP
                    # sender must not refill staging while operate is closing.
                    eof.set()
                    self.log('pipeline_transmission_stopped',
                        queued_bytes=pipeline.queued_bytes if pipeline else 0,
                        queued_controls=len(pipeline.controls) if pipeline else 0,
                        pending_lines=len(pipeline.pending) if pipeline else 0,
                        pending_statuses=pipeline.statuses if pipeline else 0,
                        staged_bytes=job.staged_bytes if job else 0,
                        job_submitted=job.submitted if job else 0,
                        job_completed=job.completed if job else False,
                        control_phase=pipeline.phase if self.control_review and pipeline else None,
                        unsent_urgent_hex=pipeline.urgent.hex() if self.control_review and
                            pipeline and pipeline.urgent else None,
                        discarded_source_bytes=parser.discarded_bytes if self.control_review else 0,
                        endpoint_unknown=bool(job and job.submitted and not job.completed),
                        control_position_trusted=False if self.control_review and pipeline and
                            pipeline.cancelling else None,
                        no_replay=True)
                    if self.control_review and pipeline is not None:
                        if job is not None and not job.aborted:
                            job.cancel()
                        self.log('pipeline_parser_discarded', pending_hex=parser.pending.hex(),
                                 no_replay=True)
                        parser.pending.clear()
                        parser.cancelled = True
                        pipeline.abandon('session_closed')
                    try:
                        # Receive may observe the latched pipeline fault while
                        # operate is already closing. Its completion must not
                        # cancel an owned-route shutdown in progress.
                        cleanup = asyncio.create_task(asyncio.wait_for(session.close(), 12))
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            await asyncio.shield(cleanup)
                            raise
                    except asyncio.CancelledError:
                        raise
                    except BaseException:
                        self.cleanup_unknown = True
                        raise

        tasks = [asyncio.create_task(receive()), asyncio.create_task(operate())]
        try:
            done, _ = await asyncio.wait(tasks, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                self.failure_reason = 'pipeline_session_timeout'
                self.log('pipeline_session_timeout')
            for task in done: task.result()
            if eof.is_set() and not tasks[1].done():
                self.log('pipeline_eof_drain_started', timeout_seconds=2)
                try:
                    await asyncio.wait_for(asyncio.shield(tasks[1]), 2)
                except TimeoutError:
                    self.failure_reason = 'pipeline_eof_drain_timeout'
                    self.log('pipeline_eof_drain_timeout', no_replay=True)
        except asyncio.CancelledError:
            if job is not None and job.submitted and not job.completed:
                self.failure_reason = 'session_cancelled_with_unfinished_job'
            self.log('pipeline_session_cancelled')
            raise
        except Exception as error:
            self.failure_reason = self.failure_reason or repr(error)
            self.log('pipeline_session_error', message=repr(error))
        finally:
            for task in tasks:
                if not task.done(): task.cancel()
            for result in await asyncio.gather(*tasks, return_exceptions=True):
                if isinstance(result, Exception):
                    self.failure_reason = self.failure_reason or repr(result)
                    self.log('pipeline_task_error', message=repr(result))
            writer.close()
            with contextlib.suppress(ConnectionError): await writer.wait_closed()
            self.active = None
            self.log('pipeline_tcp_closed', cleanup_unknown=self.cleanup_unknown)
            self.finished.set()


class _KnownTarget(str):
    """A BLE address that also answers .name / .address.

    Subclassing str keeps BleakClient happy (it accepts a plain address), while the
    rest of the code can keep reading .name and .address.
    """

    @property
    def address(self):
        return str(self)

    @property
    def name(self):
        return 'NEJE 5 Plus'


async def run(args, log):
    from bleak import BleakScanner
    known = getattr(args, 'device', None) or os.environ.get('NEJE_DEVICE')
    if known:
        # BleakScanner.discover(timeout=8) costs eight seconds on EVERY start, which is
        # the entire gap between two sessions. When the address is already known there
        # is nothing to discover, so connect straight to it.
        targets = [_KnownTarget(known)]
        log('pipeline_ble_target', name='NEJE 5 Plus', address=known, via='direct')
    else:
        targets = [d for d in await BleakScanner.discover(timeout=8) if d.name == 'NEJE 5 Plus']
        if len(targets)!=1: raise RuntimeError('Expected exactly one NEJE 5 Plus')
        log('pipeline_ble_target', name=targets[0].name, address=targets[0].address, via='scan')
    ten_mm = getattr(args, 'allow_reviewed_ten_mm_job', False)
    capture = getattr(args, 'capture_controls_without_forwarding', False)
    controls = getattr(args, 'allow_reviewed_job_controls', False)
    file_review = getattr(args, 'file_review', None)
    if getattr(args, 'transparent', False):
        # Opt-in USB-equivalent mode. The pre-review gate does not apply here; the
        # accepted trade-offs are recorded in docs/transparent-bridge-plan.md.
        log('pipeline_transparent_mode', usb_equivalent=True, reviewed_job_required=False,
            whitelist=False, plan='docs/transparent-bridge-plan.md', authorization_required=True)
        bridge = PipelineTCPBridge(targets[0], log, args.seconds, transparent=True)
    elif file_review is not None:
        log('pipeline_file_review', source_sha256=file_review.sha256,
            review=file_review.summary(), execution_requested=True,
            authorization_required=True)
        bridge = PipelineTCPBridge(targets[0], log, args.seconds,
                                   control_review=True, reviewed_file=file_review)
    elif ten_mm:
        log('pipeline_fixed_job_review', **review())
        bridge = PipelineTCPBridge(targets[0], log, args.seconds, reviewed_ten_mm=True,
                                   control_review=controls)
    else:
        bridge = PipelineTCPBridge(targets[0], log, args.seconds, capture_controls=True) if capture else \
                 PipelineTCPBridge(targets[0], log, args.seconds)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT): loop.add_signal_handler(sig, stop.set)
    server = await asyncio.start_server(bridge.handle, '127.0.0.1', 4002)
    log('pipeline_listening', bind='127.0.0.1:4002',
        profile=bridge.profile,
        reviewed_job_controls=controls,
        control_capture_without_forwarding=capture)
    waits = [asyncio.create_task(stop.wait()), asyncio.create_task(bridge.finished.wait())]
    try:
        done, _ = await asyncio.wait(waits, timeout=args.seconds, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            # Wall-clock limit, not a fault. Record it explicitly so the managed
            # layer can tell an expired session apart from a failed one instead of
            # having to infer it from a cancellation.
            log('pipeline_wall_limit_reached', seconds=args.seconds,
                client_connected=bridge.used)
    finally:
        for task in waits:
            if not task.done(): task.cancel()
        await asyncio.gather(*waits, return_exceptions=True)
        server.close()
        if bridge.active:
            bridge.active.cancel()
            await asyncio.gather(bridge.active, return_exceptions=True)
        await server.wait_closed()
        log('pipeline_server_stopped', cleanup_unknown=bridge.cleanup_unknown,
            failure_reason=bridge.failure_reason)
    if bridge.cleanup_unknown: raise RuntimeError('Cleanup unknown; explicit recovery required')
    if bridge.failure_reason: raise RuntimeError('Session failed: '+bridge.failure_reason)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--allow-initialization', action='store_true')
    parser.add_argument('--allow-reviewed-ten-mm-job', action='store_true',
                        help='Only after separate onsite authorization of the fixed 10 mm job')
    parser.add_argument('--job-file', type=Path,
                        help='Review one bounded zero-power file; default remains offline')
    parser.add_argument('--expected-job-sha256',
                        help='Required for file execution; pin the separately reviewed source')
    parser.add_argument('--file-profile',choices=PROFILES,default='x-linear-zero')
    parser.add_argument('--device',
                        help='BLE address of the machine; skips the 8 s scan. Can also be set '
                             'with NEJE_DEVICE')
    parser.add_argument('--transparent',action='store_true',
                        help='USB-equivalent mode: forward client GRBL bytes verbatim, with no '
                             'reviewed job and no whitelist; see docs/transparent-bridge-plan.md')
    parser.add_argument('--allow-transparent-bridge',action='store_true',
                        help='Explicit authorization for transparent mode, which removes the '
                             'pre-review gate')
    parser.add_argument('--power-ceiling',type=int,
                        help='Declared S ceiling for a powered profile; there is no default')
    parser.add_argument('--feed-ceiling',type=int,
                        help='Declared feed ceiling in mm/min for a powered profile; no default')
    parser.add_argument('--allow-powered-marking',action='store_true',
                        help='Explicitly authorize nonzero laser output for a powered review')
    parser.add_argument('--laser-output-authorized',action='store_true',
                        help='Attest laser power is restored and onsite safety is confirmed: goggles, '
                             'ventilation, extinguisher, independent power cut, clear area, nobody touching')
    parser.add_argument('--allow-reviewed-xy-file',action='store_true',
                        help='Separate onsite authorization of exact XY file/envelope; never implied by X-only scope')
    parser.add_argument('--allow-reviewed-arc-file',action='store_true',
                        help='Separate onsite authorization of this exact arc file; old XY authorization is insufficient')
    parser.add_argument('--allow-reviewed-rapid-file',action='store_true',
                        help='Separate authorization of G0 at actual machine rapid rates, not F60')
    parser.add_argument('--allow-reviewed-file', action='store_true',
                        help='Separate onsite authorization of this exact file and controls')
    parser.add_argument('--capture-controls-without-forwarding', action='store_true',
                        help='Initialization-only observation; never forwards !/~ or reset')
    parser.add_argument('--allow-reviewed-job-controls', action='store_true',
                        help='Requires separate onsite control/motion authorization; reads $N, forwards !/~ and one reset')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--seconds', type=int,
                        help='Wall session limit, including holds; default derived for reviewed files')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args(argv)
    if args.job_file and (args.allow_reviewed_ten_mm_job or args.capture_controls_without_forwarding):
        parser.error('Reviewed file is exclusive with fixed-job/control-capture profiles')
    if (args.allow_reviewed_file or args.expected_job_sha256 or args.allow_reviewed_xy_file or args.allow_reviewed_arc_file or args.allow_reviewed_rapid_file
            or args.file_profile!='x-linear-zero') and not args.job_file:
        parser.error('File authorization/digest requires --job-file')
    if args.allow_reviewed_ten_mm_job and args.capture_controls_without_forwarding:
        parser.error('Control capture cannot be combined with a motion job')
    if args.allow_reviewed_job_controls and not (args.allow_reviewed_ten_mm_job or args.job_file):
        parser.error('Control review requires an explicitly reviewed job')
    args.file_review = None
    if args.job_file:
        if args.expected_job_sha256 and not re.fullmatch(r'[0-9a-fA-F]{64}',args.expected_job_sha256):
            parser.error('Expected source digest must contain 64 hexadecimal digits')
        try:
            args.file_review = load_reviewed_file(args.job_file,
                expected_sha256=args.expected_job_sha256.lower() if args.expected_job_sha256 else None,
                profile=args.file_profile,
                power_ceiling=args.power_ceiling,
                feed_ceiling=args.feed_ceiling)
            job_budget = args.file_review.execution_budget()
        except (OSError,ValueError) as error:
            parser.error(str(error))
        if args.seconds is None: args.seconds = job_budget+60
        if not job_budget+30<=args.seconds<=MAX_ACTIVE_SECONDS+300:
            parser.error('File wall limit must cover active budget + 30s and be at most '
                         f'{MAX_ACTIVE_SECONDS+300}s')
    elif args.seconds is None:
        # A transparent session is the daily-use path: it must outlast a working day
        # without the operator thinking about a wall clock.
        args.seconds = MAX_ACTIVE_SECONDS if args.transparent else 90
    if args.execute:
        powered=bool(args.file_review and args.file_review.summary()['powered'])
        if args.transparent:
            if args.job_file or args.allow_reviewed_ten_mm_job:
                parser.error('Transparent mode is exclusive with reviewed/fixed jobs')
            if not args.allow_transparent_bridge:
                parser.error('Transparent mode requires --allow-transparent-bridge')
            if not (args.allow_initialization and args.log
                    and (args.laser_power_isolated or args.laser_output_authorized)):
                parser.error('Transparent mode requires --allow-initialization, exactly one laser '
                             'state attestation and a new log')
            if args.laser_power_isolated and args.laser_output_authorized:
                parser.error('Transparent mode takes exactly one laser state attestation')
        elif powered:
            # A powered run must never carry the physical-isolation attestation: the
            # laser is deliberately enabled, so that claim would be false.
            if not (args.allow_initialization and args.laser_output_authorized and args.log):
                parser.error('Powered marking requires --allow-initialization, '
                             '--laser-output-authorized and a new log')
            if args.laser_power_isolated:
                parser.error('Powered marking contradicts the physical-isolation attestation; '
                             'use --laser-output-authorized instead')
        elif not (args.allow_initialization and args.laser_power_isolated and args.log):
            parser.error('Requires existing initialization/isolation authorization and new log')
        if args.file_review and not (args.allow_reviewed_file and args.allow_reviewed_job_controls
                                    and args.expected_job_sha256):
            parser.error('File execution requires digest and separate file/control authorization')
        if args.file_profile!='x-linear-zero' and not args.allow_reviewed_xy_file:
            parser.error('XY execution requires separate --allow-reviewed-xy-file authorization')
        if (args.file_profile=='xy-arc-zero' or args.file_review and args.file_review.arcs) and not args.allow_reviewed_arc_file:
            parser.error('Arc execution requires separate --allow-reviewed-arc-file authorization')
        if args.file_review and args.file_review.rapid_segments and not args.allow_reviewed_rapid_file:
            parser.error('Rapid execution requires separate --allow-reviewed-rapid-file authorization; G0 ignores F60')
        if args.file_review and args.file_review.summary()['powered'] and not args.allow_powered_marking:
            parser.error('Powered review requires --allow-powered-marking')
        if not args.file_review and (args.power_ceiling is not None or args.feed_ceiling is not None):
            parser.error('Declared power/feed ceilings only apply to a reviewed file')
        if args.transparent:
            if not 1<=args.seconds<=MAX_ACTIVE_SECONDS:
                parser.error(f'transparent mode: seconds must be 1..{MAX_ACTIVE_SECONDS}')
        elif not args.file_review and not 1<=args.seconds<=300:
            parser.error('seconds must be 1..300')
    return args


def main(argv=None):
    args = parse_args(argv)
    if not args.execute:
        print(json.dumps(dict(offline_only=True, allowed=['$I','$#','G0','?'],motion=False,
                              ten_mm_candidate=review() if args.allow_reviewed_ten_mm_job else None,
                              file_candidate=args.file_review.summary() if args.file_review else None,
                              active_job_budget_seconds=args.file_review.execution_budget() if args.file_review else None,
                              wall_session_limit_seconds=args.seconds,
                              control_review_candidate=args.allow_reviewed_job_controls,
                              hardware_controls_executed=False)))
        return 0
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


if __name__=='__main__': raise SystemExit(main())
