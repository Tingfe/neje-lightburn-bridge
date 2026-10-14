"""Daily-use entry: one command from a LightBurn export to a BLE bridge session.

The managed entry needs a pinned digest, a geometry profile and a specific set of
authorization flags. Making a user assemble those by hand for every design is what
kept this from being usable daily. This tool does the mechanical parts itself:

  * computes the SHA-256 instead of asking the user to,
  * picks the profile from the file's own content and prints why,
  * assembles exactly the flags the audited entry requires,

and it still refuses to run anything that fails review or lacks an attestation.
Dry-run by default: it prints the command it would run. Add --run to execute it.

It never opens BLE or a serial port itself; the audited entry owns the device.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reviewed_job_file import load, PROFILES, POWER_PROFILES
from status_panel import summarize

# Uses the interpreter that runs this file, so the tool is portable.
PYTHON = sys.executable
ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / 'tools' / 'lightburn_session.py'


def choose_profile(path, requested=None, power_ceiling=None, feed_ceiling=None):
    """Ask the reviewer which profile admits the file, strictest first.

    Heuristics on the text were over-granting: a G0 that does not actually move still
    looked like a rapid. Trying the profiles in order costs a few offline reviews and
    is exact, and it always lands on the least permissive profile that still accepts
    the bytes.
    """
    if requested:
        return requested, 'explicitly requested'
    zero_error = None
    for candidate in ('x-linear-zero', 'xy-linear-zero', 'xy-arc-zero', 'xy-job-zero'):
        try:
            load(Path(path), profile=candidate)
            return candidate, f'{candidate} accepts the file and is the strictest that does'
        except (OSError, ValueError) as error:
            zero_error = str(error)
    if power_ceiling is None or feed_ceiling is None:
        return None, (f'no zero-power profile accepts the file ({zero_error}); a powered '
                      'profile would need explicit --power-ceiling and --feed-ceiling')
    powered_error = None
    for candidate in POWER_PROFILES:
        try:
            load(Path(path), profile=candidate,
                 power_ceiling=power_ceiling, feed_ceiling=feed_ceiling)
            return candidate, f'{candidate} accepts the file with the declared ceilings'
        except (OSError, ValueError) as error:
            powered_error = str(error)
    return None, (f'no zero-power profile accepts the file ({zero_error}); '
                  f'no powered profile either ({powered_error})')


def review_file(path, profile, power_ceiling=None, feed_ceiling=None):
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    reviewed = load(Path(path), expected_sha256=digest, profile=profile,
                    power_ceiling=power_ceiling, feed_ceiling=feed_ceiling)
    return digest, reviewed


def build_start_command(path, profile, digest, reviewed, *, attestation, seconds=None):
    summary = reviewed.summary()
    command = [PYTHON, str(SESSION), 'start', '--execute',
               '--allow-initialization', attestation,
               '--allow-reviewed-file', '--allow-reviewed-xy-file',
               '--allow-reviewed-job-controls',
               '--job-file', str(Path(path).resolve()),
               '--expected-job-sha256', digest,
               '--file-profile', profile]
    if summary['arc_count']:
        command.append('--allow-reviewed-arc-file')
    if summary['rapid_segments']:
        command.append('--allow-reviewed-rapid-file')
    if summary['powered']:
        command += ['--allow-powered-marking',
                    '--power-ceiling', str(summary['power_ceiling']),
                    # g1_g2_g3_feed_ceiling_mm_min keeps the declared value even when a
                    # rapid is present and feed_ceiling_mm_min is reported as None.
                    '--feed-ceiling', str(summary['g1_g2_g3_feed_ceiling_mm_min'])]
    if seconds is not None:
        command += ['--seconds', str(seconds)]
    return command


def doctor():
    """Offline checks for the whole daily chain. Never opens BLE or a serial port."""
    checks = []

    def add(name, ok, detail):
        checks.append(dict(check=name, ok=bool(ok), detail=detail))

    try:
        import subprocess as sp
        out = sp.run(['lsof', '-nP', '-iTCP:4002', '-sTCP:LISTEN'],
                     capture_output=True, text=True, timeout=10).stdout
        add('bridge listening on 127.0.0.1:4002', 'LISTEN' in out,
            'accepting LightBurn connections' if 'LISTEN' in out
            else 'nothing is listening: start a session before connecting LightBurn')
    except Exception as error:                                  # noqa: BLE001
        add('bridge listening on 127.0.0.1:4002', False, repr(error))

    sessions = ROOT / 'sessions'
    newest = None
    for root in sorted(ROOT.glob('sessions*')):
        summary = summarize(str(root))
        if summary.get('found'):
            stamp = (summary.get('started_utc') or '')
            if newest is None or stamp > newest[1]:
                newest = (summary, stamp, root.name)
    if newest is None:
        add('managed session record', False, 'no session record yet; nothing to recover')
    else:
        summary, _, root = newest
        state = summary.get('state')
        add('newest session is not blocking', state != 'requires_recovery',
            f'{root}: {state_zh_of(state)}'
            + ('; run lightburn_session.py recover before starting again'
               if state == 'requires_recovery' else ''))

    prefs = Path.home() / 'Library/Preferences/LightBurn/prefs.ini'
    if not prefs.exists():
        add('LightBurn TCP device configured', False, f'{prefs} not found')
    else:
        text = prefs.read_text(errors='replace')
        add('LightBurn TCP device configured', '4002' in text,
            'a device pointing at port 4002 is present' if '4002' in text
            else 'no LightBurn device references port 4002; add one (TCP, 127.0.0.1:4002)')

    add('daily flow', True,
        'export GCode -> daily_use.py start <file> --power-ceiling N --feed-ceiling M '
        '<--laser-isolated|--output-authorized> --run -> connect LightBurn and press Start')
    add('jog is translated, not forwarded', True,
        'a $J= jog becomes one explicit bounded relative move (G91 G1 ...), so it cannot '
        'run away as it did when forwarded as-is')
    return checks


def state_zh_of(state):
    from status_panel import STATE_ZH
    return STATE_ZH.get(state, state)


def datetime_stamp():
    import datetime
    return datetime.datetime.now().strftime('%Y%m%dT%H%M%S')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('status', help='Show the newest session record (no device access)')
    sub.add_parser('doctor', help='Check the whole daily chain offline (no device access)')
    transparent = sub.add_parser(
        'transparent',
        help='Start the USB-equivalent transparent bridge (no reviewed job, no whitelist)')
    transparent.add_argument('--laser-isolated', action='store_true')
    transparent.add_argument('--output-authorized', action='store_true')
    transparent.add_argument('--seconds', type=int, default=25200,
                             help="Session wall limit; the bridge's own ceiling is 25200s (7h)")
    transparent.add_argument('--run', action='store_true',
                             help='Actually start the bridge; default is dry-run')
    for name in ('review', 'start'):
        p = sub.add_parser(name)
        p.add_argument('job_file', type=Path)
        p.add_argument('--profile', choices=PROFILES)
        p.add_argument('--power-ceiling', type=int)
        p.add_argument('--feed-ceiling', type=int)
        p.add_argument('--seconds', type=int)
        p.add_argument('--laser-isolated', action='store_true')
        p.add_argument('--output-authorized', action='store_true')
        p.add_argument('--run', action='store_true', help='Actually execute; default is dry-run')
    args = parser.parse_args()

    if args.action == 'status':
        print(json.dumps(summarize(str(ROOT / 'sessions')), ensure_ascii=False, indent=2))
        return 0

    if args.action == 'doctor':
        checks = doctor()
        for check in checks:
            print(('  OK   ' if check['ok'] else '  WARN ') + check['check'] + ' — ' + check['detail'])
        return 0 if all(c['ok'] for c in checks) else 1

    if args.action == 'transparent':
        attestations = [flag for flag, on in (('--laser-power-isolated', args.laser_isolated),
                                              ('--laser-output-authorized', args.output_authorized)) if on]
        if len(attestations) != 1:
            print('refused: give exactly one of --laser-isolated or --output-authorized',
                  file=sys.stderr)
            return 2
        log = ROOT / 'logs' / ('transparent-' + datetime_stamp() + '.jsonl')
        command = [PYTHON, str(ROOT / 'tools' / 'pipeline_tcp_bridge.py'),
                   '--transparent', '--allow-transparent-bridge', '--execute',
                   '--allow-initialization', attestations[0],
                   '--seconds', str(args.seconds), '--log', str(log)]
        print(json.dumps(dict(run=args.run, command=command,
                              usb_equivalent=True, reviewed_job_required=False,
                              whitelist=False), ensure_ascii=False))
        if not args.run:
            print('dry-run: add --run to start the transparent bridge', file=sys.stderr)
            return 0
        log.parent.mkdir(parents=True, exist_ok=True)
        return subprocess.call(command)

    profile, why = choose_profile(args.job_file, args.profile,
                                  args.power_ceiling, args.feed_ceiling)
    print(json.dumps(dict(profile=profile, why=why), ensure_ascii=False))
    if profile is None:
        print('refused: no reviewed profile admits this file', file=sys.stderr)
        return 2
    try:
        digest, reviewed = review_file(args.job_file, profile,
                                       args.power_ceiling, args.feed_ceiling)
    except (OSError, ValueError) as error:
        print(json.dumps(dict(review='refused', reason=str(error)), ensure_ascii=False),
              file=sys.stderr)
        return 2
    summary = reviewed.summary()
    print(json.dumps(dict(review='passed', sha256=digest, commands=summary['command_count'],
                          profile=summary['profile'], power=summary['power'],
                          power_ceiling=summary['power_ceiling'],
                          arcs=summary['arc_count'], rapids=summary['rapid_segments'],
                          x_range=summary['x_range'], y_range=summary['y_range'],
                          end=summary['end']), ensure_ascii=False))
    if args.action == 'review':
        return 0
    attestations = [flag for flag, on in (('--laser-power-isolated', args.laser_isolated),
                                          ('--laser-output-authorized', args.output_authorized)) if on]
    if len(attestations) != 1:
        print('refused: give exactly one of --laser-isolated or --output-authorized',
              file=sys.stderr)
        return 2
    command = build_start_command(args.job_file, profile, digest, reviewed,
                                  attestation=attestations[0], seconds=args.seconds)
    print(json.dumps(dict(run=args.run, command=command), ensure_ascii=False))
    if not args.run:
        print('dry-run: add --run to execute this exact command', file=sys.stderr)
        return 0
    return subprocess.call(command)


if __name__ == '__main__':
    raise SystemExit(main())
