"""Exact initialization USB baseline; requires authorization beyond read-only."""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
import time

from lightburn_init_bridge import REQUESTS, reported_idle
from readonly_debugger import USBBackend, USB_PORT


async def run(log):
    backend = USBBackend(USB_PORT, log)
    try:
        await backend.open()
        state = (await backend.query('status')).decode('ascii')
        if not reported_idle(state):
            raise RuntimeError('Requires reported Idle / FS:0,0')
        for name in ('rapid_mode', 'build', 'parameters'):
            payload = REQUESTS[name]
            log('usb_init_tx', name=name, hex=payload.hex())
            if backend.port.write(payload) != len(payload):
                raise RuntimeError('Partial write; no retry')
            output = bytearray()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                part = backend.port.read(4096)
                if part:
                    output.extend(part)
                    log('usb_init_rx', name=name, hex=part.hex())
                    if len(output) > 8192:
                        raise RuntimeError('Response overflow')
                    lines = output.decode('ascii').replace('\r', '').split('\n')[:-1]
                    terminal = [line for line in lines if line == 'ok' or line.startswith(('error:', 'ALARM:'))]
                    if terminal:
                        if len(terminal) != 1:
                            raise RuntimeError('Ambiguous response boundary')
                        log('usb_init_result', name=name, text=output.decode('ascii'), terminal=terminal[0])
                        if terminal[0] != 'ok':
                            raise RuntimeError('Device rejected initialization; response preserved')
                        break
                await asyncio.sleep(.01)
            else:
                raise TimeoutError('No terminal response; no retry')
        final = (await backend.query('status')).decode('ascii')
        if not reported_idle(final):
            raise RuntimeError('Final reported state is not Idle / FS:0,0')
        log('usb_init_complete', initial_status=state, final_status=final)
    finally:
        await backend.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--allow-initialization', action='store_true')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'offline_only': True, 'sequence': ['?', 'G0\n', '$I\n', '$#\n', '?'],
                          'requires_explicit_scope_authorization': True}))
        return 0
    if not args.allow_initialization or not args.laser_power_isolated or not args.log:
        parser.error('Requires initialization authorization, isolation and new log path')
    with args.log.open('x') as stream:
        def log(event, **fields):
            row = json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                              'event': event, **fields}, ensure_ascii=False)
            stream.write(row + '\n'); stream.flush(); print(row, flush=True)
        try:
            asyncio.run(run(log))
        except Exception as exc:
            log('error', error=repr(exc))
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
