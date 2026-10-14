"""Offline by default: reviewed USB file or fixed controlled-job experiment.

Uses the BLE bridge's control/completion core. Hardware execution requires new
onsite authorization; motion reset requires its own fixed-job scope. No automatic
return, unlock, homing or replay. Variable files currently allow normal completion.
"""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
import re
import time

from check_ten_mm_host import TenMMUSBSession
from controlled_grbl_pipeline import ControlledGrblPipeline
from controlled_job_policy import ControlledJobPolicy
from controlled_reviewed_job import ControlledReviewedJob, require_empty_startup
from lightburn_init_bridge import reported_idle
from motion_parity_probe import parse_status
from reviewed_job_bridge import JobChannel
from reviewed_ten_mm import TEN_MM_JOB, review, wire_lines
from reviewed_job_file import load as load_reviewed_file, PROFILES


class ControlledUSBSession(TenMMUSBSession):
    def __init__(self, log, *, allow_job=False, allow_controls=False, reviewed_file=None):
        if reviewed_file is not None and not allow_job:
            raise ValueError('USB file requires the separately reviewed job scope')
        if allow_controls and not allow_job:
            raise ValueError('USB controls require the reviewed job scope')
        super().__init__(log, allow_job=allow_job, reviewed_file=reviewed_file)
        self.reviewed_file = reviewed_file
        self.allow_controls = allow_controls
        self.reset_attempted = False

    async def write(self, wire):
        if self.failed:
            raise RuntimeError('USB write uncertain; no further write or replay')
        if self.reset_attempted and wire != b'?':
            raise RuntimeError('Reset generation closed; only final status query allowed')
        if wire not in (b'!', b'~', b'\x18', b'$N\n'):
            return await super().write(wire)
        if self.reviewed_file is not None and wire in (b'!', b'~', b'\x18'):
            raise ValueError('USB file profile only permits normal completion, no physical controls')
        if not self.allow_controls or (wire == b'$N\n' and self.attempted):
            raise ValueError('USB control/startup read is outside explicit scope')
        if wire == b'\x18':
            self.reset_attempted = True
        self.log('usb_control_write_attempt', hex=wire.hex())
        self.failed = True  # Acceptance may precede an exception or partial write.
        if self.backend.port.write(wire) != len(wire):
            raise RuntimeError('Partial USB control write; result unknown, no retry')
        self.failed = False
        return await self.verify()


async def run_review(session, log, *, clock=time.monotonic, sleep=asyncio.sleep,
                     scenario='pause_resume_complete_idle_reset', reviewed_file=None):
    if scenario not in ('pause_resume_complete_idle_reset', 'motion_cancel', 'complete'):
        raise ValueError('Unknown controlled USB scenario')
    if reviewed_file is not None and scenario != 'complete':
        raise ValueError('Variable USB files require normal-completion scope')
    job_commands = reviewed_file.commands if reviewed_file is not None else TEN_MM_JOB
    source_wires = reviewed_file.wires if reviewed_file is not None else wire_lines()
    active_budget = reviewed_file.execution_budget() if reviewed_file is not None else 45
    pipeline = job = None
    async def deliver(data):
        log('usb_control_visible', hex=data.hex())
    try:
        await session.start()
        channel = JobChannel(session, log, deliver, max_x=10, completion_timeout=active_budget)
        baseline = await channel.status('usb_control_baseline')
        buffer = re.search(r'\|Bf:(\d+),(\d+)(?:\||>)', baseline)
        if not reported_idle(baseline) or not buffer:
            raise RuntimeError('Requires Idle/FS0 and real Bf before controlled review')
        settings=await channel.prepare()
        if reviewed_file is not None:reviewed_file.require_rapid_settings(settings)
        require_empty_startup(await channel.request(b'$N\n', 'usb_control_startup_blocks'))
        pipeline = ControlledGrblPipeline(session, deliver, log,
            rx_budget=int(buffer[2])-8, payload_capacity=173, deliver_lines=True, clock=clock)
        pipeline.output = channel.lines
        job = ControlledReviewedJob(pipeline, channel, log, clock=clock,
                                    job_commands=job_commands, active_timeout=active_budget,
                                    position_bounds=reviewed_file.position_bounds if reviewed_file
                                        else ((0,10),(400,400),(0,0),(0,0)),
                                    power_ceiling=reviewed_file.summary()['power_ceiling']
                                        if reviewed_file else 0,
                                    end_position=reviewed_file.end_position
                                        if reviewed_file else (0,400,0,0))
        job.prepared = True
        pipeline.check_status = job.check_status
        parser = ControlledJobPolicy(job_commands)
        source_index = 0
        log('usb_control_ready', scenario=scenario,
            x_range=list(reviewed_file.position_bounds[0]) if reviewed_file else [0,10],
            y_range=list(reviewed_file.position_bounds[1]) if reviewed_file else [400,400],
            feed_mm_min=None if reviewed_file and reviewed_file.rapid_segments else 60,
            rapid_settings=reviewed_file.summary()['required_rapid_settings'] if reviewed_file else None,
            power=0, startup_blocks_empty=True,
            reviewed_source_sha256=reviewed_file.sha256 if reviewed_file else None,
            file_geometry_profile=reviewed_file.geometry_profile if reviewed_file else None,
            reviewed_position_bounds=reviewed_file.position_bounds if reviewed_file else None,
            job_timeout_seconds=active_budget)
        deadline, next_poll = clock()+(active_budget+60 if reviewed_file is not None else 90), clock()
        paused = resumed = reset = False
        held_since = None
        while True:
            if clock() > deadline:
                raise TimeoutError('USB control review deadline; no return or replay')
            while not job.aborted and source_index < len(source_wires):
                wire = source_wires[source_index]
                if job.staged_bytes+len(wire)>16384:
                    break
                for kind, accepted_wire in parser.feed(wire):
                    job.enqueue(kind, accepted_wire)
                source_index += 1
                if source_index == len(source_wires):
                    parser.eof()
            await job.advance()
            if scenario != 'complete' and not paused and not reset and pipeline.last_status:
                state, position = parse_status(pipeline.last_status)
                if state == 'Run' and 2 <= position[0] <= 8:
                    if scenario == 'motion_cancel':
                        job.cancel()
                        pipeline.realtime(b'\x18')
                        reset = True
                        log('usb_review_motion_cancel_requested', position=position,
                            pending_sequences=[line.sequence for line in pipeline.pending],
                            no_return=True, no_replay=True)
                    else:
                        pipeline.realtime(b'!')
                        paused = True
                        log('usb_review_pause_requested', position=position)
            if pipeline.phase == 'holding' and not resumed:
                state, position = parse_status(pipeline.last_status)
                if state != 'Hold:0':
                    raise RuntimeError('Pause slot missed; Idle is not running-job hold evidence')
                if held_since is None:
                    held_since = clock()
                    log('usb_review_hold_confirmed', position=position, observe_seconds=2)
                if clock()-held_since >= 2:
                    pipeline.realtime(b'~')
                    resumed = True
                    log('usb_review_resume_requested')
            if job.completed and pipeline.drained and not reset:
                if scenario == 'complete':
                    log('usb_file_review_passed', job_completed=True,
                        reviewed_source_sha256=reviewed_file.sha256 if reviewed_file else None,
                        command_count=job.submitted, physical_displacement_independently_measured=False)
                    return
                if not resumed:
                    raise RuntimeError('Job ended before pause/resume could be reviewed')
                if not reported_idle(pipeline.last_status):
                    raise RuntimeError('Idle reset requires the final real Idle/FS0 report')
                pipeline.realtime(b'\x18')
                reset = True
                log('usb_review_idle_reset_requested', no_in_motion_reset=True)
            if (not job.completed and pipeline.ordinary_allowed and not pipeline.statuses
                    and not pipeline.controls and pipeline.internal is None and clock() >= next_poll):
                pipeline.realtime(b'?')
                next_poll = clock()+.2
            await pipeline.step()
            if pipeline.cancelled:
                state, position = parse_status(pipeline.last_status)
                if scenario == 'motion_cancel':
                    if not reset or job.completed or state not in ('Idle', 'Alarm'):
                        raise RuntimeError('Motion cancel not confirmed; no recovery or replay')
                    log('usb_control_cancel_observed', state=state, reported_position=position,
                        job_completed=False, endpoint_unknown=True, position_trusted=False,
                        reset_confirmed=True, physical_stop_independently_measured=False,
                        no_return=True, no_replay=True)
                    return
                if state != 'Idle' or any(abs(a-b) > .001 for a, b in zip(position, (0, 400, 0, 0))):
                    raise RuntimeError('Idle reset returned changed state/position; explicit recovery required')
                log('usb_control_review_passed', pause_confirmed=paused, resume_requested=resumed,
                    job_completed=job.completed, idle_reset_confirmed=True,
                    reported_position=position, physical_displacement_independently_measured=False)
                return
            await sleep(.01)
    finally:
        log('usb_control_transmission_stopped',
            phase=pipeline.phase if pipeline else None,
            pending_lines=len(pipeline.pending) if pipeline else 0,
            pending_statuses=pipeline.statuses if pipeline else 0,
            queued_bytes=pipeline.queued_bytes if pipeline else 0,
            staged_bytes=job.staged_bytes if job else 0,
            job_completed=job.completed if job else False,
            endpoint_unknown=bool(job and job.submitted and not job.completed), no_replay=True)
        if pipeline is not None:
            if job is not None and not job.aborted:
                job.cancel()
            pipeline.abandon('usb_session_closed')
        await session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--allow-reviewed-ten-mm-job', action='store_true')
    parser.add_argument('--allow-reviewed-job-controls', action='store_true')
    parser.add_argument('--job-file', type=Path)
    parser.add_argument('--expected-job-sha256')
    parser.add_argument('--file-profile',choices=PROFILES,default='x-linear-zero')
    parser.add_argument('--power-ceiling',type=int,
                        help='Declared S ceiling for a powered profile; there is no default')
    parser.add_argument('--feed-ceiling',type=int,
                        help='Declared feed ceiling in mm/min for a powered profile; no default')
    parser.add_argument('--allow-powered-marking',action='store_true',
                        help='Explicitly authorize nonzero laser output for a powered review')
    parser.add_argument('--laser-output-authorized',action='store_true',
                        help='Attest laser power is restored and onsite safety is confirmed')
    parser.add_argument('--allow-reviewed-xy-file',action='store_true')
    parser.add_argument('--allow-reviewed-arc-file',action='store_true',
                        help='Separate onsite authorization of this exact arc file; old XY authorization is insufficient')
    parser.add_argument('--allow-reviewed-rapid-file',action='store_true',
                        help='Separate authorization of G0 at actual machine rapid rates, not F60')
    parser.add_argument('--allow-reviewed-file', action='store_true')
    parser.add_argument('--scenario', choices=('pause_resume_complete_idle_reset', 'motion_cancel', 'complete'))
    parser.add_argument('--allow-motion-cancel', action='store_true',
                        help='Separate onsite authorization; reset while moving, no return or unlock')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if (args.expected_job_sha256 or args.allow_reviewed_file or args.allow_reviewed_xy_file or args.allow_reviewed_arc_file or args.allow_reviewed_rapid_file
            or args.file_profile!='x-linear-zero') and not args.job_file:
        parser.error('File digest/authorization requires --job-file')
    if args.job_file and (args.allow_reviewed_ten_mm_job or args.allow_motion_cancel):
        parser.error('Variable file is exclusive with fixed-job/motion-cancel profiles')
    args.scenario = args.scenario or ('complete' if args.job_file else 'pause_resume_complete_idle_reset')
    file_review = None
    if args.job_file:
        if args.scenario != 'complete':
            parser.error('Variable USB file only supports normal completion')
        if args.expected_job_sha256 and not re.fullmatch(r'[0-9a-fA-F]{64}',args.expected_job_sha256):
            parser.error('Expected source digest must contain 64 hexadecimal digits')
        try:
            file_review = load_reviewed_file(args.job_file,
                expected_sha256=args.expected_job_sha256.lower() if args.expected_job_sha256 else None,
                profile=args.file_profile,
                power_ceiling=args.power_ceiling,
                feed_ceiling=args.feed_ceiling)
            file_review.execution_budget()
        except (OSError, ValueError) as error:
            parser.error(str(error))
    candidate = dict(offline_only=True, review=file_review.summary() if file_review else review(),
        scenario=args.scenario, controls=['0x18 once during Run; no return']
            if args.scenario == 'motion_cancel' else [] if args.scenario == 'complete'
            else ['!', '~', '0x18 at verified Idle'],
        startup_read='$N (both blocks must be empty)', hardware_executed=False,
        authorization_required=True)
    if not args.execute:
        print(json.dumps(candidate, ensure_ascii=False, indent=2))
        return 0
    job_allowed = (args.allow_reviewed_file and args.expected_job_sha256) if file_review else args.allow_reviewed_ten_mm_job
    powered = bool(file_review and file_review.summary()['powered'])
    laser_ok = args.laser_output_authorized if powered else args.laser_power_isolated
    if not (job_allowed and args.allow_reviewed_job_controls and laser_ok and args.log):
        parser.error('Requires separate onsite motion/control authorization, a laser state attestation and a new log')
    if powered:
        if not args.allow_powered_marking:
            parser.error('Powered review requires --allow-powered-marking')
        if args.laser_power_isolated:
            parser.error('Powered marking contradicts the physical-isolation attestation; '
                         'use --laser-output-authorized instead')
    if args.scenario == 'motion_cancel' and not args.allow_motion_cancel:
        parser.error('Motion cancel requires separate --allow-motion-cancel authorization')
    if args.file_profile!='x-linear-zero' and not args.allow_reviewed_xy_file:
        parser.error('XY execution requires separate --allow-reviewed-xy-file authorization')
    if (args.file_profile=='xy-arc-zero' or file_review and file_review.arcs) and not args.allow_reviewed_arc_file:
        parser.error('Arc execution requires separate --allow-reviewed-arc-file authorization')
    if file_review and file_review.rapid_segments and not args.allow_reviewed_rapid_file:
        parser.error('Rapid execution requires separate --allow-reviewed-rapid-file authorization; G0 ignores F60')
    with args.log.open('x') as output:
        def log(event, **fields):
            output.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                        event=event, **fields), ensure_ascii=False)+'\n')
            output.flush()
        try:
            log('usb_control_file_review' if file_review else 'usb_control_fixed_review',
                execution_requested=True, **candidate)
            asyncio.run(run_review(ControlledUSBSession(log, allow_job=True, allow_controls=True,
                                                       reviewed_file=file_review), log,
                                   scenario=args.scenario, reviewed_file=file_review))
        except Exception as error:
            log('usb_control_error', message=repr(error))
            return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
