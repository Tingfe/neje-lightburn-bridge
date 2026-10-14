"""Authorized real Pi BLE in-flight disconnect test of the debugger recovery flow."""
import argparse
import asyncio
import datetime
import json
from pathlib import Path

from grbl_readonly import encode_query
from readonly_debugger import Debugger


async def run(device, log):
    rows = []
    def capture(event, **fields):
        rows.append({'event': event, **fields})
        log(event, **fields)
    debugger = Debugger(capture)
    try:
        await debugger.command('connect ble', ble_device=device)
        await debugger.command('build')
        backend = debugger.backend
        packet = encode_query('build', backend.owned['write_check'])
        original_write = backend.client.write_gatt_char
        async def disconnect_after_write(char, data, **kwargs):
            await original_write(char, data, **kwargs)
            if bytes(data) == packet:
                capture('fault_disconnect_requested', owned_before=dict(backend.owned))
                await backend.client.disconnect()
                capture('fault_disconnect_returned', is_connected=backend.client.is_connected)
        backend.client.write_gatt_char = disconnect_after_write
        try:
            await debugger.command('build')
        except (ConnectionError, TimeoutError):
            pass
        else:
            raise AssertionError('In-flight response unexpectedly delivered')
        assert debugger.state == 'fault' and debugger.query_unknown
        assert sum(row['event'] == 'result' for row in rows) == 1
        queries_before = sum(row['event'] == 'write_attempt' and row['label'].startswith('query_')
                             and row['label'] != 'query_counters' for row in rows)
        for command in ('build', 'status', 'connect ble', 'connect usb'):
            try:
                await debugger.command(command)
            except RuntimeError:
                capture('fault_command_rejected', command=command)
            else:
                raise AssertionError('Fault command was accepted')
        await debugger.command('recover')
        assert debugger.state == 'disconnected' and backend.owned is None
        assert debugger.query_unknown
        assert sum(row['event'] == 'write_attempt' and row['label'].startswith('query_')
                   and row['label'] != 'query_counters' for row in rows) == queries_before == 2
        capture('explicit_recovery_test_passed', no_query_replay=True,
                no_automatic_reconnect=True, result_remains_unknown=True)
        # A separate user-equivalent connect command is required after recovery.
        await debugger.command('connect ble', ble_device=device)
        await debugger.command('status')
        await debugger.command('disconnect')
        capture('post_recovery_query_passed', state=debugger.state)
    finally:
        if debugger.backend:
            await debugger.try_close()
        capture('test_final_state', **debugger.snapshot())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--laser-power-isolated', action='store_true')
    parser.add_argument('--device', required=True)
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print('Offline plan: baseline $I; second $I real disconnect; fault lock; explicit recover; explicit connect; ?; close')
        return
    if not args.laser_power_isolated or not args.log:
        parser.error('Requires confirmed isolation and a new log path')
    with args.log.open('x') as stream:
        def log(event, **fields):
            row = json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                              'event': event, **fields}, ensure_ascii=False)
            stream.write(row + '\n'); stream.flush(); print(row, flush=True)
        asyncio.run(run(args.device, log))


if __name__ == '__main__':
    main()
