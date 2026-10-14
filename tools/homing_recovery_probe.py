"""USB-only, fixed XY homing after the captured motion-reset alarm.

Offline by default. Uses existing USB transport and real-response channel;
no unlock, general GCode, settings writes, retries or job continuation.
NEJE documents axis-selective $H; upstream GRBL strips spaces and accepts $HX/Y
when single-axis homing is enabled. Unsupported firmware ends the experiment.
"""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
import re

from check_ten_mm_host import TenMMUSBSession, READS
from controlled_reviewed_job import require_empty_startup
from motion_parity_probe import parse_status, preflight, preflight_environment, require_ok
from reviewed_job_bridge import JobChannel

HOMES = (b'$H\n',)
HOME_LABELS = ('all',)
# The axis-selective NEJE forms above were measured to be accepted (`ok`) without
# establishing any position, so the plain whole-axis form is offered as a separate,
# explicitly authorized variant rather than silently replacing them.
HOMES_AXIS_SELECTIVE = (b'$HX\n', b'$HY\n')
HOME_LABELS_AXIS_SELECTIVE = ('X', 'Y')
EXPECTED_SETTINGS = {'13': 0, '20': 0, '21': 0, '22': 1, '23': 1,
                     '24': 250, '25': 2500, '26': 250, '27': 1,
                     '30': 1000, '32': 1, '130': 260, '131': 400}


def settings_values(text):
    values = {}
    for line in text.splitlines():
        if line.startswith('$'):
            key, value = line[1:].split('=', 1)
            if key in values:
                raise ValueError('Duplicate setting')
            values[key] = float(value)
    if any(values.get(key) != value for key, value in EXPECTED_SETTINGS.items()):
        raise ValueError('Homing/travel/units differ from the captured baseline')
    return values


def require_stopped(first, second, allow_idle=False):
    """The whole precondition, factored out so it can be tested without a device."""
    if parse_status(first) != parse_status(second):
        raise ValueError('Reported position/state changed before homing')
    state, position = parse_status(second)
    zero_feed = all(re.search(r'\|FS:0(?:\.0+)?,0(?:\.0+)?(?:\||>)', r) for r in (first, second))
    if state == 'Alarm':
        if not zero_feed:
            raise ValueError('Requires stopped alarm / zero feed and power')
        if not (0 <= position[0] <= 10) or position[1:] != (400.0, 0.0, 0.0):
            raise ValueError('Not the recorded X-only stop neighborhood')
    elif state == 'Idle' and allow_idle:
        # A full power-down clears the reference, so the reported position is a
        # meaningless (0,0,0,0): requiring the recorded neighborhood would be
        # asserting on a value that carries no information. Idle plus zero
        # feed/power is the whole precondition that can honestly be checked, and
        # homing itself is what re-establishes the reference.
        if not zero_feed:
            raise ValueError('Requires zero feed and power before idle homing')
    else:
        raise ValueError('Requires stopped alarm / zero feed and power')
    return state, position


def require_before(settings, modal, parameters, startup, first, second, allow_idle=False):
    values = settings_values(settings)
    require_empty_startup(startup)
    preflight_environment(settings, modal, parameters)
    require_stopped(first, second, allow_idle=allow_idle)
    return values


class HomingSession(TenMMUSBSession):
    def __init__(self, log, *, allow_homing=False, allow_idle_homing=False, axis_selective=False):
        super().__init__(log)
        self.allow_homing = allow_homing
        self.allow_idle_homing = allow_idle_homing
        self.homes = HOMES_AXIS_SELECTIVE if axis_selective else HOMES
        self.home_labels = HOME_LABELS_AXIS_SELECTIVE if axis_selective else HOME_LABELS
        self.prepared = False
        self.homing_attempts = 0

    async def write(self, wire):
        if self.failed:
            raise RuntimeError('Previous USB write uncertain; no retry')
        if wire not in (*READS, b'$N\n'):
            if not (self.allow_homing and self.prepared
                    and self.homing_attempts < len(self.homes)
                    and wire == self.homes[self.homing_attempts]):
                raise ValueError('Only prepared, ordered, single-use XY homing allowed')
            self.homing_attempts += 1  # Reserve before potentially accepted write.
        self.log('homing_usb_write_attempt', hex=wire.hex())
        self.failed = True
        count = self.backend.port.write(wire)
        self.log('homing_usb_write_returned', count=count, expected=len(wire))
        if count != len(wire):
            raise RuntimeError('Partial USB write; no retry or fallback')
        self.failed = False
        return await self.verify()


async def run(session, log, *, inspect_only=False):
    async def visible(data):
        log('homing_visible', hex=data.hex())
    try:
        await session.start()
        channel = JobChannel(session, log, visible, request_timeout=60)
        first = await channel.status('homing_alarm_before')
        settings = await channel.request(b'$$\n', 'homing_settings_before')
        modal = await channel.request(b'$G\n', 'homing_modal_before')
        parameters = await channel.request(b'$#\n', 'homing_parameters_before')
        startup = await channel.request(b'$N\n', 'homing_startup_before')
        second = await channel.status('homing_alarm_stable')
        # Bf/Pn/Ov/WCO may be intermittent; compare state/position/feed only.
        values = require_before(settings, modal, parameters, startup, first, second,
                                allow_idle=getattr(session, 'allow_idle_homing', False))
        log('homing_preflight_passed', reported_position=parse_status(second)[1],
            position_trusted=False, power=0, expected_axes=['X', 'Y'])
        if inspect_only:
            log('homing_inspection_complete', homing_executed=False)
            return
        session.prepared = True
        for axis, wire in zip(getattr(session, 'home_labels', HOME_LABELS),
                              getattr(session, 'homes', HOMES)):
            require_ok(await channel.request(wire, 'home_' + axis))
            report = await channel.status('homing_' + axis + '_after')
            state, position = parse_status(report)
            if state != 'Idle' or position != (0.0, 400.0, 0.0, 0.0) or '|FS:0,0' not in report:
                raise RuntimeError('Unexpected homing state/position; no next axis or fallback')
            log('homing_axis_confirmed', axis=axis, report=report,
                controller_homing_acknowledged=True, physical_precision_measured=False)
        final_settings = await channel.request(b'$$\n', 'homing_settings_after')
        final_modal = await channel.request(b'$G\n', 'homing_modal_after')
        final_parameters = await channel.request(b'$#\n', 'homing_parameters_after')
        final_report = await channel.status('homing_final')
        if settings_values(final_settings) != values:
            raise RuntimeError('Settings changed during homing')
        preflight(final_settings, final_modal, final_parameters, final_report)
        log('homing_recovery_passed', position=[0, 400, 0, 0],
            position_basis='real XY homing completion, not unlock or old coordinates',
            no_job_replayed=True, physical_precision_measured=False)
    finally:
        await session.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--inspect-only', action='store_true')
    p.add_argument('--allow-xy-homing', action='store_true')
    p.add_argument('--allow-idle-homing', action='store_true',
                   help='Separate authorization for homing from Idle with an unknown reference, '
                        'the state a full power-down leaves behind; the reported position is a '
                        'meaningless (0,0,0,0), so Idle plus zero feed/power is all that is checked')
    p.add_argument('--axis-selective-home', action='store_true',
                   help='Use the axis-selective $HX/$HY forms. Measured on this firmware to '
                        'return ok WITHOUT establishing a position, so the plain $H is the '
                        'default; this flag exists only to reproduce the earlier experiment')
    p.add_argument('--laser-power-isolated', action='store_true')
    p.add_argument('--log', type=Path)
    args = p.parse_args()
    if not args.execute:
        print(json.dumps(dict(offline_only=True, usb_only=True,
                              homing=[] if args.inspect_only else
                                  [x.decode().strip() for x in
                                   (HOMES_AXIS_SELECTIVE if args.axis_selective_home else HOMES)],
                              no_unlock=True, no_replay=True)))
        return 0
    if not args.log or not args.laser_power_isolated or not (args.inspect_only or args.allow_xy_homing):
        p.error('Requires current movement/isolation scope and a new log')
    with args.log.open('x') as f:
        def log(event, **kw):
            f.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                   event=event, **kw)) + '\n')
            f.flush()
        try:
            asyncio.run(run(HomingSession(log, allow_homing=args.allow_xy_homing,
                                           allow_idle_homing=args.allow_idle_homing,
                                           axis_selective=args.axis_selective_home), log,
                            inspect_only=args.inspect_only))
        except Exception as error:
            log('homing_error', message=repr(error), no_retry=True)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
