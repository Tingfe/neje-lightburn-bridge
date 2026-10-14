#!/usr/bin/env python3
"""Interactive USB/BLE debugger: fixed queries, explicit recovery, no TCP listener."""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
import signal
import time

from grbl_readonly import DISABLE_RELIABLE, READ_ONLY_QUERIES
from neje_protocol import VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS
from tcp_readonly_bridge import BASE, ReliableSession

USB_PORT = '/dev/serial/by-id/usb-Zhixinjie_NEJE_Device_NEJE_3_Series-if00'


def validate_usb_response(name, data):
    text = data.decode('ascii', errors='strict')
    if not text.endswith('\n'):
        return False
    lines = text.replace('\r', '').splitlines()
    if any(line.startswith(('error:', 'ALARM:')) for line in lines):
        raise RuntimeError('Controller error/alarm; query result not accepted')
    if name == 'build':
        return any(line.startswith('[VER:') and 'NEJE' in line for line in lines) and 'ok' in lines
    return len(lines) == 1 and lines[0].startswith('<') and lines[0].endswith('>')


class USBBackend:
    def __init__(self, target, log):
        self.target, self.log, self.port = target, log, None

    async def open(self):
        import serial
        self.port = serial.Serial(port=None, baudrate=115200, timeout=0,
                                 write_timeout=1, exclusive=True,
                                 xonxoff=False, rtscts=False, dsrdtr=False)
        self.port.dtr = self.port.rts = False
        self.port.port = self.target
        self.port.open()
        self.log('usb_opened', port=self.target, dtr=False, rts=False)
        await self.collect('passive_after_open', 2)

    async def collect(self, name, seconds):
        output = bytearray()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            chunk = self.port.read(4096)
            if chunk:
                output.extend(chunk)
                self.log('usb_rx', phase=name, hex=chunk.hex())
                if len(output) > 8192:
                    raise RuntimeError('USB response overflow')
                if name in READ_ONLY_QUERIES and validate_usb_response(name, output):
                    return bytes(output)
            await asyncio.sleep(.01)
        if name != 'passive_after_open':
            raise TimeoutError('No complete USB response; no retry')
        return bytes(output)

    async def query(self, name):
        payload = READ_ONLY_QUERIES[name]
        self.log('usb_tx_attempt', name=name, hex=payload.hex())
        count = self.port.write(payload)
        if count != len(payload):
            raise RuntimeError('Partial USB write; no retry')
        output = await self.collect(name, 3)
        self.log('usb_query_verified', name=name, hex=output.hex())
        return output

    async def close(self):
        if self.port:
            self.port.close()
        self.log('usb_closed', is_open=bool(self.port and self.port.is_open))

    async def recover(self):
        # Closing the host port cannot establish whether an interrupted query ran.
        await self.close()
        self.log('usb_recovery_verified', host_port_closed=True,
                 interrupted_query_result='unknown', no_replay=True)


class BLEBackend:
    def __init__(self, target, log):
        self.target, self.log = target, log
        self.client = self.session = None
        self.owned = None
        self.expected_disconnect = False
        self.connecting_task = None
        self.connect_lost = False

    def session_log(self, event, **fields):
        if event == 'frame' and fields.get('kind') == 'grbl_comm_status' and fields['way'] == 0:
            self.before = (fields['write_check'], fields['read_check'])
        if event == 'write_attempt' and fields['label'] == 'enable_ble_reliable':
            self.owned = {'write_check': self.before[0], 'read_check': self.before[1], 'pending': None}
        if event == 'query_verified':
            self.owned = {'write_check': fields['write_check'], 'read_check': fields['read_check'], 'pending': None}
        if event == 'cleanup_verified':
            if fields['final_app'] != 3:
                raise RuntimeError('Application changed during cleanup')
            self.owned = None
        self.log(event, **fields)

    def disconnected(self, client):
        if client is not self.client:
            self.log('ble_disconnected_stale', is_connected=client.is_connected)
            return
        if self.connecting_task and not self.connecting_task.done():
            self.connect_lost = True
            self.connecting_task.cancel()
        self.log('ble_disconnected', is_connected=client.is_connected,
                 expected=self.expected_disconnect)

    async def connect_client(self):
        from bleak import BleakClient
        self.expected_disconnect = False
        self.connect_lost = False
        self.client = BleakClient(self.target, timeout=20, disconnected_callback=self.disconnected)
        self.connecting_task = asyncio.create_task(self.client.connect())
        try:
            await self.connecting_task
        except asyncio.CancelledError:
            if self.connect_lost:
                raise ConnectionError('BLE disconnected during connect; explicit recovery/reconnect required')
            raise
        if self.connect_lost or not self.client.is_connected:
            raise ConnectionError('BLE connection was interrupted')
        self.session = ReliableSession(self.client, self.session_log)

    async def open(self):
        from bleak import BleakScanner
        devices = await BleakScanner.discover(timeout=8)
        targets = [d for d in devices if (d.address == self.target if self.target else d.name == 'NEJE 5 Plus')]
        if len(targets) != 1:
            raise RuntimeError('Expected exactly one matching NEJE 5 Plus')
        self.target = targets[0].address
        await self.connect_client()
        await self.session.start()
        if not self.client.is_connected:
            raise ConnectionError('BLE lost during initialization')

    async def query(self, name):
        if self.owned is None:
            raise RuntimeError('No owned BLE session')
        self.owned['pending'] = name
        return await self.session.query(name)

    async def detach(self):
        self.expected_disconnect = True
        if self.client and self.client.is_connected:
            await self.client.disconnect()

    async def close(self):
        try:
            if self.owned:
                await self.session.close()
        finally:
            await self.detach()

    async def recover(self):
        if not self.owned:
            await self.detach()
            self.log('recovery_verified', owned_route=False, no_device_disable=True)
            return
        # Ownership is held only in this process; never adopt somebody else's route.
        lease = dict(self.owned)
        await self.detach()
        try:
            await self.connect_client()
            await self.client.start_notify(BASE.format('ffea'), self.session.notify)
            identity = await self.session.exchange('recovery_verify', VERIFY, 'verify')
            system = await self.session.exchange('recovery_system', SYSTEM_READ_ALL, 'system_info')
            if identity['model'] != 55 or system['current_app'] != 3:
                raise RuntimeError('Recovery identity/application mismatch')
            status = await self.session.exchange('recovery_status', GRBL_COMM_STATUS, 'grbl_comm_status')
            self.session.clean(status)
            dw = (status['write_check'] - lease['write_check']) & 65535
            dr = (status['read_check'] - lease['read_check']) & 65535
            limit = len(READ_ONLY_QUERIES[lease['pending']]) if lease['pending'] else 0
            if dw not in (0, limit) or dr > (8192 if lease['pending'] else 0):
                raise RuntimeError('Recovery counters outside owned query; refusing disable')
            if dw == 0 and dr != 0:
                raise RuntimeError('Read counter changed without owned query acceptance')
            if status['way'] not in (0, 2):
                raise RuntimeError('Unexpected transport; refusing disable')
            for _ in range(2):
                await asyncio.sleep(.2)
                stable = await self.session.exchange('recovery_status_repeat', GRBL_COMM_STATUS, 'grbl_comm_status')
                for field in ('way', 'write_check', 'read_check', 'device_tx_error', 'device_rx_error'):
                    if stable[field] != status[field]:
                        raise RuntimeError('Recovery state is changing; refusing disable')
            self.log('recovery_inspected', **status, owned_write_delta=dw, owned_read_delta=dr)
            if status['way'] == 2:
                await self.session.send('disable_owned_route', DISABLE_RELIABLE)
                await asyncio.sleep(.2)
            final = await self.session.exchange('recovery_final_status', GRBL_COMM_STATUS, 'grbl_comm_status')
            self.session.clean(final)
            app = await self.session.exchange('recovery_final_system', SYSTEM_READ_ALL, 'system_info')
            if final['way'] != 0 or app['current_app'] != 3:
                raise RuntimeError('Recovery cleanup not confirmed')
            if (final['write_check'], final['read_check']) != (status['write_check'], status['read_check']):
                raise RuntimeError('Counters changed during recovery')
            self.owned = None
            self.log('recovery_verified', way=0, app=3, write_check=final['write_check'],
                     read_check=final['read_check'], no_replay=True)
            await self.client.stop_notify(BASE.format('ffea'))
        finally:
            await self.detach()


class Debugger:
    def __init__(self, log, factory=None):
        self.log, self.factory = log, factory or {'usb': USBBackend, 'ble': BLEBackend}
        self.state, self.backend, self.kind = 'disconnected', None, None
        self.last_result = None
        self.query_unknown = False

    def event(self, event, **fields):
        self.log(event, **fields)
        if event == 'ble_disconnected' and not fields.get('expected'):
            self.state = 'fault'
            self.log('state', state='fault', reason='BLE lost; explicit recovery required')

    def snapshot(self):
        owned = getattr(self.backend, 'owned', None)
        return {'state': self.state, 'backend': self.kind, 'target': getattr(self.backend, 'target', None),
                'last_verified': self.last_result, 'interrupted_query_result_unknown': self.query_unknown,
                'owned_ble_route': owned, 'whitelist': ['$I', '?'],
                'next': 'recover' if self.state == 'fault' else 'connect' if self.state == 'disconnected' else 'build/status/disconnect'}

    async def connect(self, kind, target):
        if self.state != 'disconnected':
            raise RuntimeError('Disconnect or explicitly recover before connecting/switching backend')
        self.kind, self.state = kind, 'connecting'
        self.backend = self.factory[kind](target, self.event)
        try:
            await self.backend.open()
            if self.state == 'fault':
                raise ConnectionError('Link failed during connection')
            self.state = 'ready'
        except BaseException:
            self.state = 'fault'
            await self.try_close()
            raise

    async def query(self, name):
        if name not in READ_ONLY_QUERIES:
            raise ValueError('Only build/status are allowed')
        if self.state != 'ready':
            raise RuntimeError('Query requires ready state')
        self.state = 'querying'
        try:
            output = await self.backend.query(name)
            if self.state != 'querying':
                raise ConnectionError('Link lost before result delivery')
            self.last_result = {'name': name, 'text': output.decode('ascii'),
                                'backend': self.kind, 'target': self.backend.target,
                                'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
            self.state = 'ready'
            self.log('result', **self.last_result)
        except BaseException:
            self.query_unknown, self.state = True, 'fault'
            await self.try_close()
            raise

    async def try_close(self):
        try:
            await self.backend.close()
            return True
        except Exception as exc:
            self.log('cleanup_unconfirmed', error=repr(exc))
            return False

    async def disconnect(self):
        if self.state == 'fault':
            raise RuntimeError('Fault requires explicit recover; result remains unknown')
        if self.backend:
            self.state = 'closing'
            if not await self.try_close():
                self.state = 'fault'
                return
        self.state = 'disconnected'

    async def recover(self):
        if self.state != 'fault':
            raise RuntimeError('Recovery is only available in fault state')
        self.state = 'recovering'
        try:
            await self.backend.recover()
            self.state = 'disconnected'
        except BaseException:
            self.state = 'fault'
            raise
        # Clearing connection fault does not make the interrupted query known.

    async def command(self, text, usb_port=USB_PORT, ble_device=None):
        if text == 'connect usb':
            await self.connect('usb', usb_port)
        elif text == 'connect ble':
            await self.connect('ble', ble_device)
        elif text in ('build', 'status'):
            await self.query(text)
        elif text == 'disconnect':
            await self.disconnect()
        elif text == 'recover':
            await self.recover()
        elif text != 'state':
            raise ValueError('Allowed: connect usb | connect ble | build | status | state | disconnect | recover | quit')
        self.log('state', **self.snapshot(), show_last=(text == 'state'))


async def run(args, log):
    debugger = Debugger(log)
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    commands = iter(args.commands.split(';')) if args.commands else None
    try:
        while True:
            if commands is not None:
                command = next(commands, 'quit').strip()
            else:
                print('neje> ', end='', flush=True)
                future = loop.create_future()
                import sys
                def ready():
                    if not future.done():
                        future.set_result(sys.stdin.readline())
                loop.add_reader(sys.stdin.fileno(), ready)
                try:
                    line = await future
                    command = line.strip() if line else 'quit'
                finally:
                    loop.remove_reader(sys.stdin.fileno())
                if not command:
                    continue
            log('command', text=command)
            if command == 'quit':
                break
            try:
                await debugger.command(command, args.usb_port, args.ble_device)
            except Exception as exc:
                log('command_error', error=repr(exc), **debugger.snapshot())
                if commands is not None:
                    return 1
    except asyncio.CancelledError:
        log('signal_shutdown')
    finally:
        if debugger.backend:
            if debugger.state == 'fault':
                await debugger.try_close()
            else:
                await debugger.disconnect()
        log('final_state', **debugger.snapshot())
    return 2 if debugger.state == 'fault' else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--usb-port', default=USB_PORT)
    parser.add_argument('--ble-device')
    parser.add_argument('--commands', help='Semicolon-separated fixed tool commands; stop on first failure')
    parser.add_argument('--log', type=Path)
    parser.add_argument('--verbose', action='store_true', help='Print raw JSON events as well as recording them')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'offline_only': True, 'interface': 'interactive CLI',
                          'commands': ['connect usb', 'connect ble', 'build', 'status', 'state', 'disconnect', 'recover', 'quit'],
                          'whitelist': ['$I', '?'], 'recovery': 'explicit, owned route only, never replay or auto reconnect'}))
        return 0
    if not args.laser_power_isolated or not args.log:
        parser.error('--execute requires --laser-power-isolated and new --log path')
    with args.log.open('x') as stream:
        def log(event, **fields):
            row = json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                              'event': event, **fields}, ensure_ascii=False)
            stream.write(row + '\n'); stream.flush()
            if args.verbose:
                print(row, flush=True)
            elif event == 'command':
                print('> ' + fields['text'], flush=True)
            elif event == 'result':
                print(fields['text'].rstrip(), flush=True)
            elif event in ('state', 'final_state'):
                print('状态: ' + fields['state'] + ' | 后端: ' + str(fields.get('backend', '-')),
                      flush=True)
                if fields.get('owned_ble_route'):
                    print('BLE 计数: ' + json.dumps(fields['owned_ble_route']), flush=True)
                if fields.get('reason'):
                    print(fields['reason'], flush=True)
                if fields.get('show_last') and fields.get('last_verified'):
                    last = fields['last_verified']
                    print('最后核验: {backend} {target} {utc}\n{text}'.format(**last).rstrip(), flush=True)
                if fields.get('interrupted_query_result_unknown'):
                    print('此前中断查询的执行结果仍未确认；不会重发。', flush=True)
            elif event in ('command_error', 'cleanup_unconfirmed'):
                print('错误: ' + fields['error'], flush=True)
            elif event == 'recovery_verified':
                print('恢复检查完成，未重发查询；需要另行 connect。', flush=True)
            elif event == 'recovery_inspected':
                print('恢复读回: way={way}, w={write_check}, r={read_check}'.format(**fields), flush=True)
        return asyncio.run(run(args, log))


if __name__ == '__main__':
    raise SystemExit(main())
