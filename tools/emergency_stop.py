"""Emergency stop: send the GRBL realtime stop bytes and nothing else.

The runaway-jog incident (evidence/transparent-jog-incident-20261014/) exposed a real
gap: when the machine runs away there was no stop within reach. The bridge session had
already ended, and the serial port disappeared while the cable was being moved, so the
only remaining option was cutting power.

This tool exists so the stop is one command away. It writes exactly three realtime
bytes, in order, and then reads the state back:

    '!'   feed hold      - GRBL decelerates and holds
    0x85  jog cancel     - the designed stop for a jog
    0x18  soft reset     - unconditional stop; coordinates become untrusted

It sends no G-code, no settings, no motion of its own, and it never retries. Realtime
bytes are handled out of band by GRBL, so this works while the controller is in Run or
Jog state and needs no line terminator.

Offline by default. Real use needs --execute and a new --log path.
"""
import argparse
import datetime
import json
import time
from pathlib import Path

STOP_BYTES = ((b'!', 'feed_hold'), (b'\x85', 'jog_cancel'), (b'\x18', 'soft_reset'))
DEFAULT_PORT = '/dev/ttyACM0'


def send_stop(port, log, settle=0.3, read_seconds=3.0):
    import serial
    session = serial.Serial(port, 115200, timeout=0.3)
    try:
        session.dtr = False
        session.rts = False
        time.sleep(0.2)
        session.reset_input_buffer()
        for payload, label in STOP_BYTES:
            written = session.write(payload)
            log('emergency_stop_sent', label=label, hex=payload.hex(), bytes_written=written)
            time.sleep(settle)
        session.write(b'?')
        time.sleep(read_seconds)
        raw = b''
        for _ in range(10):
            if session.in_waiting:
                raw += session.read(session.in_waiting)
            time.sleep(0.2)
        text = raw.decode('ascii', 'replace').strip()
        log('emergency_stop_state', state=text[:200],
            contains_alarm='ALARM' in text, contains_idle='Idle' in text,
            coordinates_trusted_after_reset=False)
        return text
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--port', default=DEFAULT_PORT,
                        help='Serial port of the machine (default: the Pi USB link)')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(dict(offline_only=True, action='emergency_stop',
                              bytes=[{'hex': p.hex(), 'label': l} for p, l in STOP_BYTES],
                              gcode_sent=False, settings_written=False, retries=0,
                              note='Realtime bytes; works while Run/Jog; reset makes coordinates '
                                   'untrusted so a homing pass is required afterwards',
                              hardware_executed=False), ensure_ascii=False, indent=2))
        return 0
    if not args.log:
        parser.error('Requires a new --log path')
    with args.log.open('x') as output:
        def log(event, **fields):
            output.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                         event=event, **fields), ensure_ascii=False) + '\n')
            output.flush()
        log('emergency_stop_start', port=args.port, no_gcode=True, no_settings=True, no_retry=True)
        try:
            send_stop(args.port, log)
        except Exception as error:                       # noqa: BLE001
            log('emergency_stop_error', message=repr(error), no_retry=True)
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
