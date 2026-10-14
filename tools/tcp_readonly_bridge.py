"""Loopback-only NEJE reliable BLE bridge. Only $I and ?; no retries/reconnect.
TCP stream/single-peer design informed by MIT Jakeler/ble-serial 3.0.0.
Default prints an offline plan. Host execution requires physical isolation.
"""
import argparse
import asyncio
import contextlib
import datetime
import json
from pathlib import Path
import signal
import time
from neje_protocol import FrameDecoder, describe, VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS
from grbl_readonly import ENABLE_BLE_RELIABLE, DISABLE_RELIABLE, READ_ONLY_QUERIES, encode_query, accept_data

BASE = '0000{}-0000-1000-8000-00805f9b34fb'


class QueryParser:
    """Parse TCP fragments; never forward unvalidated raw bytes."""
    def __init__(self):
        self.pending = bytearray()

    def feed(self, data):
        result = []
        for b in data:
            if b in (10, 13):
                if self.pending:
                    line = bytes(self.pending); self.pending.clear()
                    result.append(('query', 'build') if line == b'$I' else ('blocked', line.hex()))
            elif b == 63 and not self.pending:
                result.append(('query', 'status'))
            elif b < 32 or b >= 127:
                # Controls never reach BLE. Discard partial line so it cannot be spliced.
                result.append(('blocked', (bytes(self.pending) + bytes([b])).hex()))
                self.pending.clear()
            else:
                self.pending.append(b)
                if len(self.pending) > 128:
                    raise ValueError('TCP command exceeds 128 bytes')
        return result


class ReliableSession:
    system_read_after = SYSTEM_READ_ALL

    def __init__(self, client, log):
        self.client, self.log = client, log
        self.decoder, self.frames = FrameDecoder(), []
        self.attempted = False
        self.wcheck = self.rcheck = 0
        self.fault = None

    def notify(self, char, data):
        self.log('notify', hex=bytes(data).hex(' '))
        try:
            frames = self.decoder.feed(data)
        except ValueError as error:
            self.fault = error
            self.log('receive_fault', message=str(error))
            return
        for frame in frames:
            info = describe(frame)
            self.frames.append(info)
            self.log('frame', **info)
        if len(self.frames) > 256:
            self.fault = RuntimeError('Unconsumed BLE frame overflow')

    async def send(self, label, packet):
        if not self.client.is_connected:
            raise ConnectionError('BLE disconnected')
        self.log('write_attempt', label=label, hex=packet.hex(' '))
        await self.client.write_gatt_char(BASE.format('ffe9'), packet, response=False)

    async def wait_kind(self, kind, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.fault: raise self.fault
            if not self.client.is_connected: raise ConnectionError('BLE disconnected')
            for i, frame in enumerate(self.frames):
                if frame['kind'] == kind:
                    del self.frames[:i+1]
                    return frame
            await asyncio.sleep(.02)
        raise TimeoutError('Missing ' + kind)

    async def exchange(self, label, packet, kind):
        self.frames.clear()
        await self.send(label, packet)
        return await self.wait_kind(kind)

    @staticmethod
    def clean(status):
        if status['device_tx_error'] or status.get('device_rx_error', 0):
            raise RuntimeError('Device reports transfer error; no recovery/retry')

    async def start(self):
        await self.client.start_notify(BASE.format('ffea'), self.notify)
        verify = await self.exchange('verify', VERIFY, 'verify')
        if verify['model'] != 55: raise RuntimeError('Unexpected model')
        initial = await self.exchange('system_before', SYSTEM_READ_ALL, 'system_info')
        # Do not implicitly change application in this phase.
        if initial['current_app'] != 3: raise RuntimeError('Requires existing GRBL app 3')
        before = await self.exchange('bridge_before', GRBL_COMM_STATUS, 'grbl_comm_status')
        if before['way'] != 0: raise RuntimeError('Existing transport active; refusing takeover')
        self.attempted = True
        await self.send('enable_ble_reliable', ENABLE_BLE_RELIABLE)
        await asyncio.sleep(.2)
        status = await self.exchange('bridge_enabled', GRBL_COMM_STATUS, 'grbl_comm_status')
        self.clean(status)
        if status['way'] != 2: raise RuntimeError('Transport did not enable')
        self.wcheck, self.rcheck = status['write_check'], status['read_check']
        self.log('session_ready', write_check=self.wcheck, read_check=self.rcheck)

    async def query(self, name):
        self.frames.clear()
        await self.send('query_' + name, encode_query(name, self.wcheck))
        output = bytearray()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            frame = await self.wait_kind('grbl_data', max(.01, deadline-time.monotonic()))
            payload = bytes.fromhex(frame['payload_hex'])
            self.rcheck = accept_data(self.rcheck, frame['read_check'], payload)
            output.extend(payload)
            if len(output) > 8192: raise RuntimeError('GRBL response overflow')
            text = output.decode('ascii', errors='strict')
            lines = text.replace('\r','').split('\n')[:-1]
            if any(x.startswith(('error:', 'ALARM:')) for x in lines):
                raise RuntimeError('Controller error/alarm; no retry')
            done = ('ok' in lines and any(x.startswith('[VER:') for x in lines)) if name == 'build' else any(x.startswith('<') and x.endswith('>') for x in lines)
            if done: break
        else: raise TimeoutError('Incomplete query response')
        self.wcheck = (self.wcheck + len(READ_ONLY_QUERIES[name])) & 65535
        status = await self.exchange('query_counters', GRBL_COMM_STATUS, 'grbl_comm_status')
        self.clean(status)
        if (status['way'],status['write_check'],status['read_check']) != (2,self.wcheck,self.rcheck):
            raise RuntimeError('Counter mismatch; response not delivered to TCP')
        self.log('query_verified', name=name, text=text, write_check=self.wcheck, read_check=self.rcheck)
        return bytes(output)

    async def close(self):
        if not self.attempted: return
        if not self.client.is_connected:
            self.log('cleanup_unconfirmed', reason='BLE lost; no automatic reconnect; device way unknown')
            raise ConnectionError('Cannot confirm disabled transport')
        await self.send('disable_reliable', DISABLE_RELIABLE)
        await asyncio.sleep(.2)
        status = await self.exchange('bridge_cleanup', GRBL_COMM_STATUS, 'grbl_comm_status')
        self.clean(status)
        if status['way'] != 0: raise RuntimeError('Cleanup way is not 0')
        final = await self.exchange('system_after', self.system_read_after, 'system_info')
        self.log('cleanup_verified', way=0, final_app=final['current_app'], write_check=status['write_check'], read_check=status['read_check'])
        await self.client.stop_notify(BASE.format('ffea'))


class Bridge:
    def __init__(self, device, log, session_timeout=120,
                 parser_factory=QueryParser, session_factory=ReliableSession):
        self.device, self.log, self.timeout = device, log, session_timeout
        self.active = None
        self.poisoned = False
        self.parser_factory, self.session_factory = parser_factory, session_factory

    async def handle(self, reader, writer):
        if self.active is not None or self.poisoned:
            self.log('tcp_rejected', reason='busy_or_cleanup_unconfirmed')
            writer.close(); await writer.wait_closed(); return
        self.active = asyncio.current_task()
        queue = asyncio.Queue(maxsize=16)
        self.log('tcp_connected', peer=writer.get_extra_info('peername'))
        async def receive():
            parser = self.parser_factory()
            while True:
                data = await reader.read(512)
                if not data:
                    self.log('tcp_eof', pending_hex=parser.pending.hex()); return
                self.log('tcp_receive', hex=data.hex(' '))
                for kind, value in parser.feed(data):
                    if kind == 'blocked':
                        self.log('tcp_blocked', hex=value)
                        writer.write(b'[MSG:LaserLink read-only policy blocked input; local error follows]\nerror:20\n')
                        await asyncio.wait_for(writer.drain(), 2)
                    else:
                        queue.put_nowait(value)
        async def operate():
            from bleak import BleakClient
            def disconnected(client):
                self.log('ble_disconnected', is_connected=client.is_connected)
            async with BleakClient(self.device, timeout=20, disconnected_callback=disconnected) as client:
                session = self.session_factory(client, self.log)
                try:
                    await session.start()
                    while True:
                        # Detect BLE loss even while TCP is idle.
                        try: name = await asyncio.wait_for(queue.get(), .1)
                        except TimeoutError:
                            if not client.is_connected: raise ConnectionError('BLE lost while idle')
                            continue
                        data = await session.query(name)
                        writer.write(data)
                        await asyncio.wait_for(writer.drain(), 2)
                        self.log('tcp_delivered', name=name, hex=data.hex(' '))
                finally:
                    try: await session.close()
                    except BaseException:
                        self.poisoned = True
                        raise
        tasks = [asyncio.create_task(receive()), asyncio.create_task(operate())]
        try:
            done, _ = await asyncio.wait(tasks, timeout=self.timeout, return_when=asyncio.FIRST_COMPLETED)
            if not done: self.log('session_timeout')
            for task in done: task.result()
        except asyncio.CancelledError:
            self.log('session_cancelled'); raise
        except Exception as error:
            self.log('session_error', type=type(error).__name__, message=str(error))
        finally:
            for task in tasks:
                if not task.done(): task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception): self.log('task_error', message=str(result))
            writer.close()
            with contextlib.suppress(ConnectionError): await writer.wait_closed()
            self.active = None
            self.log('tcp_closed', cleanup_unconfirmed=self.poisoned)


async def run(args, log):
    from bleak import BleakScanner
    devices = await BleakScanner.discover(timeout=10)
    targets = [d for d in devices if (d.address == args.device if args.device else d.name == 'NEJE 5 Plus')]
    if len(targets) != 1: raise RuntimeError('Expected one NEJE 5 Plus')
    bridge = Bridge(targets[0], log, args.session_timeout)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(sig, stop.set)
    server = await asyncio.start_server(bridge.handle, '127.0.0.1', args.port)
    log('listening', host='127.0.0.1', port=args.port, whitelist=['$I', '?'])
    try: await asyncio.wait_for(stop.wait(), args.lifetime)
    except TimeoutError: log('lifetime_expired')
    finally:
        server.close()
        if bridge.active:
            bridge.active.cancel()
            await asyncio.gather(bridge.active, return_exceptions=True)
        await server.wait_closed()
        log('server_stopped', cleanup_unconfirmed=bridge.poisoned)
    if bridge.poisoned: raise RuntimeError('Transport cleanup unconfirmed; explicit inspection required')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--laser-power-isolated', action='store_true')
    p.add_argument('--device')
    p.add_argument('--port', type=int, default=4002)
    p.add_argument('--lifetime', type=float, default=300)
    p.add_argument('--session-timeout', type=float, default=120)
    p.add_argument('--log', type=Path)
    args = p.parse_args()
    if not args.execute:
        print(json.dumps({'offline_only':True,'bind':'127.0.0.1:4002','whitelist':['$I','?'],'no_retry_or_reconnect':True,'cleanup':'TCP EOF/error/signal/timeout -> disable and read back; BLE loss -> unknown, block new peers'})); return 0
    if not args.laser_power_isolated: p.error('Requires confirmed physical isolation')
    path = args.log or Path(__file__).resolve().parents[1]/'logs'/('tcp-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.jsonl')
    with path.open('x') as f:
        def log(event, **fields):
            row = json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), event=event, **fields), ensure_ascii=False)
            print(row, flush=True); f.write(row+'\n'); f.flush()
        log('start', laser_power_isolated_user_attestation=True)
        try: asyncio.run(run(args, log))
        except Exception as error:
            log('error', type=type(error).__name__, message=str(error)); return 1
        log('complete'); return 0

if __name__ == '__main__': raise SystemExit(main())
