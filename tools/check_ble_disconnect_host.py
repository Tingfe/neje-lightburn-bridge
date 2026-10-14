"""Real host BLE disconnect experiment; no mock radio or automatic reconnect.

The real BleakClient subclass only exposes the live instance to the test harness.
After the bridge fails closed, a separate explicit inspection reads device state
and closes this experiment's route if still enabled. No GRBL writes beyond $I.
"""
import argparse
import os
import asyncio
import datetime
import json
import sys
from pathlib import Path
from unittest.mock import patch

from tcp_readonly_bridge import Bridge, ReliableSession, BASE
from neje_protocol import VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS
from grbl_readonly import DISABLE_RELIABLE, encode_query


async def experiment(address, log, mode='idle', allow_latched_attach=False):
    from bleak import BleakClient, BleakScanner
    devices = await BleakScanner.discover(timeout=10)
    device = next(d for d in devices if d.address == address)
    instances = []
    rows = []
    pending_armed = False
    def bridge_log(event, **fields):
        rows.append(dict(event=event, **fields))
        log(event, **fields)
    class ObservedRealClient(BleakClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            instances.append(self)
        async def write_gatt_char(self, char_specifier, data, **kwargs):
            await super().write_gatt_char(char_specifier, data, **kwargs)
            if pending_armed and data == encode_query('build', verified['write_check']):
                log('pending_query_write_returned', hex=bytes(data).hex(' '),
                    note='Without-response GATT write completion is not device acceptance')
                await disconnect_link(self)

    async def disconnect_link(client):
        log('link_disconnect_requested', method='real BleakClient.disconnect', host_platform=sys.platform,
            tcp_peer_still_open=not writer.is_closing(), is_connected=client.is_connected,
            mode=mode, received_grbl_bytes=sum(len(bytes.fromhex(r['payload_hex'])) for r in rows if r['event']=='frame' and r.get('kind')=='grbl_data'))
        await client.disconnect()
        log('link_disconnect_returned', is_connected=client.is_connected)
    bridge = Bridge(device, bridge_log, session_timeout=45)
    server = await asyncio.start_server(bridge.handle, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    log('experiment_listener', port=port, real_ble=True)
    writer = None
    verified = None
    try:
        # This changes the constructor reference, not any BLE method or state.
        with patch('bleak.BleakClient', ObservedRealClient):
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(b'$I\n'); await writer.drain()
            response = await asyncio.wait_for(reader.readuntil(b'ok\n'), 30)
            assert b'[VER:1.1f.NEJE' in response
            verified = [r for r in rows if r['event'] == 'query_verified'][-1]
            if mode == 'pending':
                pending_armed = True
                log('pending_query_armed', baseline_write_check=verified['write_check'],
                    baseline_read_check=verified['read_check'], expected_response_bytes=len(response))
                writer.write(b'$I\n'); await writer.drain()
            else:
                await disconnect_link(instances[0])
            assert await asyncio.wait_for(reader.read(), 3) == b''
            assert not instances[0].is_connected
            for _ in range(100):
                if bridge.active is None: break
                await asyncio.sleep(.02)
            assert bridge.active is None and bridge.poisoned
            assert any(r['event']=='ble_disconnected' for r in rows)
            assert any(r['event']=='cleanup_unconfirmed' for r in rows)
            assert not any(r['event']=='cleanup_verified' for r in rows)
            assert not any(r.get('label')=='disable_reliable' for r in rows)
            assert sum(r['event']=='query_verified' for r in rows)==1
            assert sum(r['event']=='tcp_delivered' for r in rows)==1
            r2, w2 = await asyncio.open_connection('127.0.0.1', port)
            assert await asyncio.wait_for(r2.read(), 2) == b''
            w2.close(); await w2.wait_closed()
            log('bridge_fail_closed_verified', poisoned=bridge.poisoned, new_peer_rejected=True,
                ble_connections=len(instances), automatic_reconnect=False)
    finally:
        server.close()
        if bridge.active:
            bridge.active.cancel(); await asyncio.gather(bridge.active, return_exceptions=True)
        if writer:
            writer.close(); await writer.wait_closed()
        await server.wait_closed()
        log('experiment_server_stopped')
        # Deliberate separate inspection is never part of bridge reconnection.
        log('explicit_inspection_start', automatic_reconnect=False)
        async with BleakClient(device, timeout=20) as client:
            session = ReliableSession(client, lambda event, **fields: log('inspection_' + event, **fields))
            await client.start_notify(BASE.format('ffea'), session.notify)
            identity = await session.exchange('verify', VERIFY, 'verify')
            assert identity['model'] == 55
            system = await session.exchange('system', SYSTEM_READ_ALL, 'system_info')
            assert system['current_app'] == 3
            before = await session.exchange('status_before', GRBL_COMM_STATUS, 'grbl_comm_status')
            log('device_after_link_loss', **{k:before[k] for k in ('way','write_check','read_check','device_tx_error','device_rx_error')})
            for delay in (.2, .5):
                await asyncio.sleep(delay)
                stable = await session.exchange('status_repeat', GRBL_COMM_STATUS, 'grbl_comm_status')
                log('device_after_link_loss_repeat', **{k:stable[k] for k in ('way','write_check','read_check','device_tx_error','device_rx_error')})
                assert stable == before
            if before['way'] == 2:
                if verified is None:
                    # A channel latched by an abrupt disconnect cannot be proven owned:
                    # the proof itself needs a completed owned exchange, so requiring it
                    # is circular. Attaching is therefore allowed only under a separate
                    # explicit authorization, and the counters are recorded, not asserted.
                    assert allow_latched_attach, 'Ownership counters not verified; refusing route takeover'
                    log('latched_attach_explicitly_authorized', way=before['way'],
                        write_check=before['write_check'], read_check=before['read_check'],
                        ownership_proof_absent=True, remote_stop_inferred=False, no_replay=True)
                else:
                    # Only disable the still-owned route with the exact last verified counters.
                    dw=(before['write_check']-verified['write_check'])&65535
                    dr=(before['read_check']-verified['read_check'])&65535
                    if mode=='pending':
                        assert dw in (0,3) and dr<=len(response), 'Counters outside the single owned in-flight query'
                    else:
                        assert dw==dr==0
                    log('owned_route_counter_check', mode=mode, write_delta=dw, read_delta=dr,
                        device_errors=[before['device_tx_error'],before['device_rx_error']])
                await session.send('disable_owned_experiment_route', DISABLE_RELIABLE)
                await asyncio.sleep(.2)
            else:
                assert before['way'] == 0
            after = await session.exchange('status_final', GRBL_COMM_STATUS, 'grbl_comm_status')
            session.clean(after)
            assert after['way'] == 0
            final = await session.exchange('system_final', SYSTEM_READ_ALL, 'system_info')
            assert final['current_app'] == 3
            log('explicit_cleanup_verified', way=after['way'], final_app=final['current_app'],
                write_check=after['write_check'], read_check=after['read_check'])
            await client.stop_notify(BASE.format('ffea'))
    log('experiment_passed')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--mode', choices=('idle','pending'), default='idle')
    p.add_argument('--laser-power-isolated', action='store_true')
    p.add_argument('--allow-latched-attach', action='store_true',
                   help='Explicit authorization to attach to a channel latched by a prior abrupt '
                        'disconnect, where an ownership proof is circular; sends the single '
                        'DISABLE_RELIABLE with counters recorded instead of asserted')
    p.add_argument('--device', default=os.environ.get('NEJE_DEVICE'),
                   help='BLE address of the machine; or set NEJE_DEVICE. '
                        'Find it with: python tools/p0_probe.py --scan')
    p.add_argument('--log', type=Path)
    args = p.parse_args()
    if not args.execute:
        print('Offline plan ('+args.mode+'): real $I baseline -> host BLE disconnect while TCP open -> fail closed -> explicit status inspection and owned-route cleanup.'); return
    if not args.laser_power_isolated or not args.log:
        p.error('Requires physical isolation attestation and a new --log path')
    with args.log.open('x') as f:
        def log(event, **fields):
            row = json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),event=event,**fields),ensure_ascii=False)
            print(row,flush=True);f.write(row+'\n');f.flush()
        log('start', laser_power_isolated_user_attestation=True, fault='host_requested_real_ble_disconnect', mode=args.mode)
        asyncio.run(experiment(args.device, log, args.mode,
                               allow_latched_attach=args.allow_latched_attach))

if __name__ == '__main__': main()
