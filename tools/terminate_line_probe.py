"""Attach to a BLE reliable channel and send one bare line terminator.

Offline by default. This exists for one situation: an abrupt disconnect during a
mid-line write leaves the NEJE channel latched at way=2, and in that state the GRBL
data plane answers on neither BLE nor USB, so the usual "take a baseline, then act"
tools can never reach their action. Ownership proof is also circular here, because
proving ownership needs a completed owned exchange.

GRBL exposes no command that dumps its line buffer, so the only way to learn whether
an interrupted write left a half line behind is behavioural: send a single b'\\n'.
It carries no motion word and no setting, it only closes whatever already sits in the
line buffer. If the half line survived, the controller parses the interrupted motion
and the machine moves; if it did not, the terminator is an empty line and nothing
moves. The conclusion therefore comes from on-site observation, not from a readback.

No G-code, no settings write, no unlock, no reset, no retry, no replay and no
automatic reconnect. Exactly one GRBL frame is sent.
"""
import argparse
import os
import asyncio
import datetime
import json
from pathlib import Path

from tcp_readonly_bridge import ReliableSession, BASE
from neje_protocol import VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS
from grbl_readonly import DISABLE_RELIABLE, ENABLE_BLE_RELIABLE, encode_query


async def probe(address, log, allow_terminate_line=False, close_route=False, read_status=False):
    from bleak import BleakClient, BleakScanner
    devices = await BleakScanner.discover(timeout=10)
    device = next(d for d in devices if d.address == address)
    client = BleakClient(device)
    await client.connect()
    try:
        session = ReliableSession(client, lambda event, **fields: log('session_' + event, **fields))
        await client.start_notify(BASE.format('ffea'), session.notify)
        identity = await session.exchange('verify', VERIFY, 'verify')
        assert identity['model'] == 55, 'Unexpected device model'
        system = await session.exchange('system', SYSTEM_READ_ALL, 'system_info')
        before = await session.exchange('status_before', GRBL_COMM_STATUS, 'grbl_comm_status')
        log('attached_state_recorded', current_app=system['current_app'], way=before['way'],
            write_check=before['write_check'], read_check=before['read_check'],
            ownership_proof_absent=True, remote_stop_inferred=False, no_replay=True)
        if before['way'] == 2:
            write_check = before['write_check']
        else:
            assert before['way'] == 0, 'Unexpected transport way'
            await session.send('enable_reliable', ENABLE_BLE_RELIABLE)
            await asyncio.sleep(.3)
            enabled = await session.exchange('status_enabled', GRBL_COMM_STATUS, 'grbl_comm_status')
            assert enabled['way'] == 2, 'Reliable channel did not enable'
            write_check = enabled['write_check']
            log('reliable_channel_enabled', way=enabled['way'], write_check=write_check)
        # Exactly one GRBL frame. Either the bare terminator, or the whitelisted
        # realtime status query, which is non-destructive because GRBL handles '?'
        # immediately without touching the line buffer.
        if read_status:
            frame = encode_query('status', write_check)
            name = 'status'
        else:
            assert allow_terminate_line, 'Requires --allow-terminate-line'
            frame = encode_query('terminate_line', write_check)
            name = 'terminate_line'
        await session.send(name, frame)
        log('grbl_frame_sent', name=name, hex=frame.hex(),
            payload_hex={'status': b'?', 'terminate_line': b'\n'}[name].hex(),
            write_check=write_check, grbl_frames_sent=1, contains_motion_word=False,
            contains_setting=False, no_replay=True)
        await asyncio.sleep(2.)
        after = await session.exchange('status_after', GRBL_COMM_STATUS, 'grbl_comm_status')
        log('state_after_terminator', way=after['way'], write_check=after['write_check'],
            read_check=after['read_check'], no_replay=True)
        if close_route:
            await session.send('disable_owned_experiment_route', DISABLE_RELIABLE)
            await asyncio.sleep(.2)
            final = await session.exchange('status_final', GRBL_COMM_STATUS, 'grbl_comm_status')
            log('route_closed', way=final['way'])
        await client.stop_notify(BASE.format('ffea'))
    finally:
        await client.disconnect()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--allow-terminate-line', action='store_true',
                   help='Separate authorization for the single bare line terminator')
    p.add_argument('--read-status', action='store_true',
                   help='Send the whitelisted realtime status query instead of the terminator; '
                        'it is non-destructive and reveals MPos without touching the line buffer')
    p.add_argument('--close-route', action='store_true',
                   help='After the terminator, send the single DISABLE_RELIABLE')
    p.add_argument('--laser-power-isolated', action='store_true')
    p.add_argument('--laser-output-authorized', action='store_true')
    p.add_argument('--device', default=os.environ.get('NEJE_DEVICE'),
                   help='BLE address of the machine; or set NEJE_DEVICE. '
                        'Find it with: python tools/p0_probe.py --scan')
    p.add_argument('--log', type=Path)
    args = p.parse_args()
    if not args.execute:
        print(json.dumps(dict(offline_only=True, action='attach_and_send_one_terminator',
            new_motion_command=False, new_setting=False, retries=0, replay=False,
            automatic_reconnect=False, step='attach -> record state -> send one b"\\n" -> '
            'record state -> optional single DISABLE_RELIABLE',
            observation='on-site: if the machine moves X0->X10 the half line survived',
            hardware_executed=False), ensure_ascii=False, indent=2))
        return 0
    if not args.laser_power_isolated and not args.laser_output_authorized:
        p.error('Requires a laser state attestation: --laser-power-isolated or --laser-output-authorized')
    if not args.read_status and not args.allow_terminate_line:
        p.error('Requires --allow-terminate-line')
    if not args.log:
        p.error('Requires a new --log path')
    with args.log.open('x') as output:
        def log(event, **fields):
            output.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                        event=event, **fields), ensure_ascii=False) + '\n')
            output.flush()
        log('start', laser_power_isolated=args.laser_power_isolated,
            laser_output_authorized=args.laser_output_authorized,
            allow_terminate_line=args.allow_terminate_line, close_route=args.close_route,
            read_status=args.read_status,
            fault='channel latched by a prior abrupt mid-line disconnect')
        asyncio.run(probe(args.device, log, allow_terminate_line=args.allow_terminate_line,
                          close_route=args.close_route, read_status=args.read_status))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
