"""Explicit limited initialization profile; NOT a machining or read-only profile."""
import argparse
import asyncio
import contextlib
import datetime
import json
from pathlib import Path
import signal
import time

from grbl_readonly import READ_ONLY_QUERIES, accept_data
from grbl_transport import encode_write
from neje_protocol import GRBL_COMM_STATUS
from tcp_readonly_bridge import Bridge, QueryParser, ReliableSession

# settings ($$) is a read-only settings dump. It is admitted because the bridge
# itself already issues the same read in its own preflight; no settings write
# ($n=value) is admitted by this whitelist.
REQUESTS = dict(READ_ONLY_QUERIES, parameters=b'$#\n', rapid_mode=b'G0\n', settings=b'$$\n')
EXTRA_LINES = {b'$#': 'parameters', b'G0': 'rapid_mode', b'$$': 'settings'}


def reported_idle(text):
    line = text.strip()
    return (line.startswith('<Idle|') and line.endswith('>') and '\n' not in line
            and 'FS:0,0' in line[1:-1].split('|'))


class InitParser(QueryParser):
    def feed(self, data):
        output = []
        for kind, value in super().feed(data):
            if kind == 'blocked' and bytes.fromhex(value) in EXTRA_LINES:
                output.append(('query', EXTRA_LINES[bytes.fromhex(value)]))
            else:
                output.append((kind, value))
        return output


class InitSession(ReliableSession):
    request_table = REQUESTS

    async def start(self):
        await super().start()
        state = (await self.query('status')).decode('ascii')
        if not reported_idle(state):
            raise RuntimeError('Initialization requires reported Idle / FS:0,0')
        self.log('init_idle_baseline', text=state)

    async def query(self, name):
        if getattr(self, 'terminal_alarm', False):
            raise RuntimeError('Controller alarm; no further requests')
        payload = self.request_table[name]
        self.frames.clear()
        await self.send('init_' + name, encode_write(payload, self.wcheck))
        output = bytearray()
        deadline = time.monotonic() + 8
        terminal = None
        while time.monotonic() < deadline:
            frame = await self.wait_kind('grbl_data', max(.01, deadline - time.monotonic()))
            part = bytes.fromhex(frame['payload_hex'])
            self.rcheck = accept_data(self.rcheck, frame['read_check'], part)
            output.extend(part)
            if len(output) > 8192:
                raise RuntimeError('Response overflow')
            text = output.decode('ascii', errors='strict')
            lines = text.replace('\r', '').split('\n')[:-1]
            terminals = [line for line in lines if line == 'ok' or line.startswith(('error:', 'ALARM:'))]
            if terminals:
                if len(terminals) != 1:
                    raise RuntimeError('Multiple terminal responses for one request')
                terminal = terminals[0]
                break
            if name == 'status' and any(line.startswith('<') and line.endswith('>') for line in lines):
                break
        else:
            raise TimeoutError('Incomplete initialization response; no retry')
        self.wcheck = (self.wcheck + len(payload)) & 65535
        # A real terminal and unsolicited MSG can be separate BLE frames. Never
        # clear the queued MSG before checking the device's full read counter.
        await self.send('init_counters', GRBL_COMM_STATUS)
        counter_deadline = time.monotonic() + 5
        status = None
        while time.monotonic() < counter_deadline:
            if self.fault:
                raise self.fault
            if not self.client.is_connected:
                raise ConnectionError('BLE disconnected during counter verification')
            if not self.frames:
                await asyncio.sleep(.02)
                continue
            frame = self.frames.pop(0)
            if frame['kind'] == 'grbl_data':
                part = bytes.fromhex(frame['payload_hex'])
                self.rcheck = accept_data(self.rcheck, frame['read_check'], part)
                output.extend(part)
                if len(output) > 8192:
                    raise RuntimeError('Response overflow')
            elif frame['kind'] == 'grbl_comm_status':
                status = frame
                break
        if status is None:
            raise TimeoutError('Missing counter verification; no retry')
        text = output.decode('ascii', errors='strict')
        terminals = [line for line in text.replace('\r', '').split('\n')[:-1]
                     if line == 'ok' or line.startswith(('error:', 'ALARM:'))]
        if terminal is not None and terminals != [terminal]:
            raise RuntimeError('Additional terminal or alarm before counter verification')
        self.clean(status)
        if (status['way'], status['write_check'], status['read_check']) != (2, self.wcheck, self.rcheck):
            raise RuntimeError('Counter mismatch; do not deliver response')
        self.terminal_alarm = bool(terminal and terminal.startswith('ALARM:'))
        self.log('init_verified', name=name, text=text, terminal=terminal,
                 write_check=self.wcheck, read_check=self.rcheck)
        return bytes(output)


async def run(args, log):
    from bleak import BleakScanner
    devices = await BleakScanner.discover(timeout=8)
    targets = [d for d in devices if (d.address == args.device if args.device else d.name == 'NEJE 5 Plus')]
    if len(targets) != 1:
        raise RuntimeError('Expected exactly one NEJE 5 Plus')
    bridge = Bridge(targets[0], log, args.seconds, InitParser, InitSession)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    server = await asyncio.start_server(bridge.handle, '127.0.0.1', 4002)
    log('listening', bind='127.0.0.1:4002', profile='lightburn-init',
        allowed={k: v.hex() for k, v in REQUESTS.items()}, machining_enabled=False)
    try:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), args.seconds)
    finally:
        server.close()
        if bridge.active:
            bridge.active.cancel()
            await asyncio.gather(bridge.active, return_exceptions=True)
        await server.wait_closed()
        log('server_stopped', cleanup_unconfirmed=bridge.poisoned)
    if bridge.poisoned:
        raise RuntimeError('Cleanup unconfirmed; explicit inspection required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--allow-initialization', action='store_true')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--device')
    parser.add_argument('--seconds', type=int, default=90)
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'offline_only': True, 'profile': 'lightburn-init',
                          'allowed': {k: v.decode('ascii') for k, v in REQUESTS.items()},
                          'machining_enabled': False, 'requires_explicit_scope_authorization': True}))
        return 0
    if not args.allow_initialization or not args.laser_power_isolated or not args.log:
        parser.error('Requires explicit initialization authorization, isolation, and new log path')
    if not 1 <= args.seconds <= 300:
        parser.error('seconds must be 1..300')
    with args.log.open('x') as stream:
        def log(event, **fields):
            row = json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                              'event': event, **fields}, ensure_ascii=False)
            print(row, flush=True); stream.write(row + '\n'); stream.flush()
        try:
            asyncio.run(run(args, log))
        except Exception as exc:
            log('error', error=repr(exc))
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
