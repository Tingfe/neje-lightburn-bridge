"""Exactly one authorized 0->1->0 mm X motion comparison per execution."""
import argparse
import asyncio
import datetime
import json
import math
from pathlib import Path
import re
import time

from lightburn_init_bridge import InitSession, REQUESTS
from machine_profile_probe import PROFILE_QUERIES
from readonly_debugger import USBBackend, USB_PORT

MOVES = {'x_out': b'G53 G1 X1.000 F60.000\n', 'x_back': b'G53 G1 X0.000 F60.000\n'}
QUERIES = dict(REQUESTS, **PROFILE_QUERIES, **MOVES)


def require_ok(text):
    """Ignore unsolicited messages, but require exactly one real success terminal."""
    lines = text.replace('\r', '').split('\n')[:-1]
    terminals = [line for line in lines if line == 'ok' or line.startswith(('error:', 'ALARM:'))]
    if terminals != ['ok']:
        raise RuntimeError('Missing, ambiguous or failed terminal response; no retry')


def parse_status(text, *, power_ceiling=None):
    lines = text.strip().splitlines()
    if len(lines) != 1 or not lines[0].startswith('<') or not lines[0].endswith('>'):
        raise RuntimeError('Not a single complete status report')
    parts = lines[0][1:-1].split('|')
    fields = dict(part.split(':', 1) for part in parts[1:] if ':' in part)
    position = tuple(float(x) for x in fields['MPos'].split(','))
    power = float(fields['FS'].split(',')[1])
    # A powered review declares its own ceiling; without one the zero-power rule
    # (reported spindle must be exactly 0) is preserved unchanged.
    limit = 0 if power_ceiling is None else power_ceiling
    if (len(position) != 4 or not all(math.isfinite(x) for x in position)
            or not 0 <= power <= limit):
        raise RuntimeError('Unexpected axes or reported power; stop')
    return parts[0], position


def check_position(text, x, require_idle=True, *, max_x=1):
    state, position = parse_status(text)
    if state not in ('Idle', 'Run') or (require_idle and state != 'Idle'):
        raise RuntimeError('Unexpected motion state')
    target = (x, 400.0, 0.0, 0.0)
    if require_idle and any(abs(a - b) > .001 for a, b in zip(position, target)):
        raise RuntimeError('Position differs from authorized target')
    if not (0 <= position[0] <= max_x+.001) or any(abs(a - b) > .001 for a, b in zip(position[1:], target[1:])):
        raise RuntimeError(f'Position outside authorized {max_x} mm X-only envelope')
    return state, position


def preflight_environment(settings, modal, parameters):
    values = dict(line[1:].split('=', 1) for line in settings.splitlines() if line.startswith('$'))
    if not (float(values['130']) == 260 and float(values['131']) == 400 and values['13'] == '0'):
        raise RuntimeError('Machine travel/units differ from approved baseline')
    match = re.search(r'^\[GC:(.*)\]$', modal, re.MULTILINE)
    if not match:
        raise RuntimeError('No modal report')
    modes = set(match[1].split())
    if not {'G21', 'G90', 'G94', 'G54', 'M5', 'S0'} <= modes:
        raise RuntimeError('Requires millimeters, absolute/feed-per-minute, G54, M5/S0')
    for coord in ('G54', 'G92'):
        match = re.search(r'^\[' + coord + r':([^]]+)\]$', parameters, re.MULTILINE)
        if not match or any(float(value) != 0 for value in match[1].split(',')):
            raise RuntimeError('Coordinate offsets differ from baseline')


def preflight(settings, modal, parameters, status, start_x=0):
    preflight_environment(settings, modal, parameters)
    check_position(status, start_x)


class MotionSession(InitSession):
    request_table = QUERIES


async def usb_query(backend, name, log):
    if name in ('build', 'status'):
        return await backend.query(name)
    payload = QUERIES[name]
    log('usb_motion_tx', name=name, hex=payload.hex())
    if backend.port.write(payload) != len(payload):
        raise RuntimeError('Partial write; no retry')
    data = bytearray()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        part = backend.port.read(4096)
        if part:
            data.extend(part)
            log('usb_motion_rx', name=name, hex=part.hex())
            if len(data) > 8192:
                raise RuntimeError('Response overflow')
            lines = data.decode('ascii').replace('\r', '').split('\n')[:-1]
            if any(line == 'ok' or line.startswith(('error:', 'ALARM:')) for line in lines):
                return bytes(data)
        await asyncio.sleep(.01)
    raise TimeoutError('Incomplete response; no retry')


async def exercise(query, log, return_only=False):
    async def request(name):
        data = await query(name)
        text = data.decode('ascii')
        log('motion_result', name=name, text=text)
        if name != 'status':
            require_ok(text)
        return text
    settings = await request('settings')
    modal = await request('modal')
    parameters = await request('parameters')
    before = await request('status')
    start_x = 1 if return_only else 0
    preflight(settings, modal, parameters, before, start_x)
    log('motion_preflight_passed', start_position=[start_x, 400, 0, 0], x_range=[0, 1], feed_mm_min=60,
        separately_authorized_return_only=return_only)
    moves = (('x_back', 0, 1),) if return_only else (('x_out', 1, 0), ('x_back', 0, 1))
    for name, target_x, start_x in moves:
        check_position(await request('status'), start_x)
        current_modal = await request('modal')
        modes = re.search(r'^\[GC:(.*)\]$', current_modal, re.MULTILINE)
        if not modes or not {'G21', 'G90', 'G94', 'M5', 'S0'} <= set(modes[1].split()):
            raise RuntimeError('Modal state changed before move')
        await request(name)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            report = await request('status')
            state, position = check_position(report, target_x, require_idle=False)
            if state == 'Idle':
                check_position(report, target_x)
                log('motion_arrival_verified', name=name, position=position,
                    evidence='controller reports Idle and MPos; physical observation is separate')
                break
            await asyncio.sleep(.1)
        else:
            raise TimeoutError('Motion completion unconfirmed; do not append commands')
    await request('rapid_mode')
    final = await request('status')
    check_position(final, 0)
    final_modal = await request('modal')
    log('motion_comparison_passed', final_status=final, final_modal=final_modal,
        moves=len(moves), no_emission_commands=True, physical_motion_not_independently_measured=True)


async def run(args, log):
    if args.backend == 'usb':
        backend = USBBackend(USB_PORT, log)
        try:
            await backend.open()
            await exercise(lambda name: usb_query(backend, name, log), log, args.usb_return_only)
        finally:
            await backend.close()
    else:
        from bleak import BleakClient, BleakScanner
        found = await BleakScanner.discover(timeout=8)
        targets = [d for d in found if d.name == 'NEJE 5 Plus']
        if len(targets) != 1:
            raise RuntimeError('Expected exactly one NEJE 5 Plus')
        async with BleakClient(targets[0], timeout=20) as client:
            session = MotionSession(client, log)
            try:
                await session.start()
                await exercise(session.query, log)
            finally:
                await session.close()
    log('motion_probe_complete', backend=args.backend)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('usb', 'ble'), required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--allow-one-mm-motion', action='store_true')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--usb-return-only', action='store_true', help='Separately approved remaining USB 1->0 move only')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if args.usb_return_only and args.backend != 'usb':
        parser.error('Return-only scope is USB only')
    if not args.execute:
        print(json.dumps({'offline_only': True, 'exact_motion': {k: v.decode() for k, v in MOVES.items()},
                          'prerequisites': 'user confirmed onsite clearance and independent power cutoff'}))
        return 0
    if not args.allow_one_mm_motion or not args.laser_power_isolated or not args.log:
        parser.error('Requires approved onsite 1mm motion, isolation and new log path')
    with args.log.open('x') as stream:
        def log(event, **fields):
            row = json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'event': event, **fields})
            print(row, flush=True); stream.write(row + '\n'); stream.flush()
        try:
            asyncio.run(run(args, log))
        except Exception as exc:
            log('error', error=repr(exc))
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
