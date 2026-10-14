"""Separate inspection of a proved fixed-job fault; no new GRBL stream or job.

Only identity/system/status and closing this capture's owned BLE route. The
failed bridge must already be terminal. No automatic reconnect from the bridge.
"""
import argparse
import asyncio
import datetime
import json
from pathlib import Path

from check_job_disconnect_capture import audit
from grbl_readonly import DISABLE_RELIABLE
from neje_protocol import VERIFY,SYSTEM_READ_INFO,GRBL_COMM_STATUS
from tcp_readonly_bridge import ReliableSession,BASE


def require_owned(status,proof):
    if status['way'] not in (0,2) or status['device_tx_error'] or status['device_rx_error']:
        raise RuntimeError('Unexpected route/errors; inspection cannot take ownership')
    delta_w=(status['write_check']-proof['last_verified_write_counter'])&65535
    delta_r=(status['read_check']-proof['last_verified_read_counter'])&65535
    if delta_w not in (0,proof.get('unverified_write_bytes',proof['attempted_job_bytes'])) or delta_r>1024:
        raise RuntimeError('Counters outside the single owned fixed-job window; no cleanup write')
    return dict(device_write_delta=delta_w,device_read_delta=delta_r,
                job_acceptance_complete=proof.get('job_payload_device_counter_verified',False) or delta_w==proof['attempted_job_bytes'],
                job_execution_or_completion_proven=False)


async def run(proof,log):
    from bleak import BleakClient,BleakScanner
    targets=[d for d in await BleakScanner.discover(timeout=8) if d.name=='NEJE 5 Plus'
             and (not proof.get('device_address') or d.address==proof['device_address'])]
    if len(targets)!=1:raise RuntimeError('Requires exactly one NEJE 5 Plus')
    log('inspection_target',name=targets[0].name,address=getattr(targets[0],'address',None))
    async with BleakClient(targets[0],timeout=20) as client:
        session=ReliableSession(client,log)
        await client.start_notify(BASE.format('ffea'),session.notify)
        try:
            identity=await session.exchange('inspection_verify',VERIFY,'verify')
            if identity['model']!=55:raise RuntimeError('Unexpected device model')
            system=await session.exchange('inspection_system',SYSTEM_READ_INFO,'system_info')
            if system['current_app']!=3:raise RuntimeError('Device is not in GRBL app')
            before=await session.exchange('inspection_status',GRBL_COMM_STATUS,'grbl_comm_status')
            owned=require_owned(before,proof)
            log('owned_fault_route_observed',status=before,**owned)
            for delay in (.2,.5):
                await asyncio.sleep(delay)
                current=await session.exchange('inspection_repeat',GRBL_COMM_STATUS,'grbl_comm_status')
                require_owned(current,proof)
                if current!=before:raise RuntimeError('Route counters not stable; no cleanup write')
                log('owned_fault_route_stable',status=current)
            if before['way']==2:
                await session.send('disable_owned_fault_route',DISABLE_RELIABLE)
                await asyncio.sleep(.2)
            after=await session.exchange('inspection_final_status',GRBL_COMM_STATUS,'grbl_comm_status')
            session.clean(after)
            if after['way']!=0 or (after['write_check'],after['read_check'])!=(before['write_check'],before['read_check']):
                raise RuntimeError('Owned route close did not read back unchanged counters and way0')
            final=await session.exchange('inspection_final_system',SYSTEM_READ_INFO,'system_info')
            if final['current_app']!=3:raise RuntimeError('Final app changed')
            log('owned_fault_route_cleanup_verified',way=0,final_app=3,
                write_check=after['write_check'],read_check=after['read_check'],
                new_grbl_commands=0,automatic_reconnect=False,**owned)
        finally:await client.stop_notify(BASE.format('ffea'))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--allow-owned-route-cleanup',action='store_true')
    parser.add_argument('--laser-power-isolated',action='store_true')
    parser.add_argument('--log',type=Path)
    args=parser.parse_args(argv)
    if not args.execute:
        print(json.dumps(dict(offline_only=True,new_grbl_commands=0,
            scope='separate identity/status/owned route close after proved terminal fault',
            automatic_reconnect=False)));return 0
    if not (args.capture and args.log and args.allow_owned_route_cleanup and args.laser_power_isolated):
        parser.error('Requires proved capture, new log, owned-route scope and isolation')
    proof=audit(args.capture)
    with args.log.open('x') as output:
        def log(event,**fields):
            output.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                        event=event,**fields))+'\n');output.flush()
        log('explicit_fault_inspection_start',capture=str(args.capture),proof=proof,
            automatic_reconnect=False,starts_new_grbl_route=False)
        try:asyncio.run(run(proof,log))
        except Exception as error:log('inspection_error',message=repr(error));return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
