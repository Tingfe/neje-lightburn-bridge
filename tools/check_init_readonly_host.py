"""Real Mac regression of new session engine behind the ORIGINAL read-only parser."""
import argparse
import asyncio
import datetime
import json
from pathlib import Path

from bleak import BleakScanner
from lightburn_init_bridge import InitSession
from tcp_readonly_bridge import Bridge, QueryParser


async def run(log):
    devices = await BleakScanner.discover(timeout=8)
    targets = [d for d in devices if d.name == 'NEJE 5 Plus']
    if len(targets) != 1:
        raise RuntimeError('Expected exactly one NEJE 5 Plus')
    rows = []
    def capture(event, **fields):
        rows.append({'event': event, **fields}); log(event, **fields)
    bridge = Bridge(targets[0], capture, 30, QueryParser, InitSession)
    server = await asyncio.start_server(bridge.handle, '127.0.0.1', 0)
    reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
    try:
        writer.write(b'$I\n'); await writer.drain()
        build = await asyncio.wait_for(reader.readuntil(b'ok\n'), 20)
        assert b'NEJE' in build and b'[VER:' in build
        writer.write(b'?'); await writer.drain()
        status = await asyncio.wait_for(reader.readuntil(b'\n'), 10)
        assert status.startswith(b'<Idle|')
        # These reach only the local original parser and must never reach BLE.
        writer.write(b'G0\n$#\n'); await writer.drain()
        for _ in range(2):
            rejected = await asyncio.wait_for(reader.readuntil(b'error:20\n'), 5)
            assert b'local error follows' in rejected
    finally:
        writer.close(); await writer.wait_closed()
        server.close(); await server.wait_closed()
        if bridge.active:
            await asyncio.wait_for(asyncio.shield(bridge.active), 15)
    assert not bridge.poisoned
    assert sum(r['event'] == 'tcp_blocked' for r in rows) == 2
    for row in rows:
        if row['event'] == 'write_attempt' and row['label'].startswith('init_') and row['label'] != 'init_counters':
            assert bytes.fromhex(row['hex'])[8:-1] in (b'$I\n', b'?')
    assert any(r['event'] == 'cleanup_verified' and r['way'] == 0 and r['final_app'] == 3 for r in rows)
    log('readonly_engine_regression_passed', extra_commands_sent=False, cleanup_way=0, app=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print('Offline plan: new engine, original readonly parser, real $I/?, local G0/$# rejection, cleanup')
        return
    if not args.laser_power_isolated or not args.log:
        parser.error('Requires confirmed isolation and new log')
    with args.log.open('x') as stream:
        def log(event, **fields):
            row = json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'event': event, **fields})
            print(row, flush=True); stream.write(row + '\n'); stream.flush()
        asyncio.run(run(log))


if __name__ == '__main__':
    main()
