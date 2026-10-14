"""Reviewable, gated GRBL $I experiment. Default is an offline plan only.
Execution requires user confirmation of physical laser power isolation and explicit
permission to enable the temporary BLE reliable session. No motion, reset, mode
switch command, EEPROM write, retry, reconnect, or TCP listener is implemented.
"""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
import time
from neje_protocol import FrameDecoder, describe, VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS
from grbl_readonly import ENABLE_BLE_RELIABLE, DISABLE_RELIABLE, encode_query, accept_data

BASE = '0000{}-0000-1000-8000-00805f9b34fb'
PLAN = {
    'offline_only': True,
    'requires': 'Explicit permission + physical laser power isolation confirmed by user',
    'steps': ['scan exact NEJE 5 Plus', 'verify model 55', 'read system and bridge status',
              'refuse if bridge way is not 0', 'enable BLE reliable transport (way 2)',
              'read counters; send only wrapped $I newline', 'require VER build info + ok',
              'disable reliable transport in finally; read back way 0 and application'],
    'enable_hex': ENABLE_BLE_RELIABLE.hex(' '),
    'query_example_at_zero_hex': encode_query('build',0).hex(' '),
    'disable_hex': DISABLE_RELIABLE.hex(' '),
    'not_executed_yet': True,
}


async def run(args, log):
    from bleak import BleakScanner, BleakClient
    found = await BleakScanner.discover(timeout=10)
    targets=[d for d in found if (d.address==args.device if args.device else d.name=='NEJE 5 Plus')]
    if len(targets)!=1:
        raise RuntimeError('Expected one target; use current --device UUID if ambiguous')
    decoder, frames = FrameDecoder(), []
    def notify(char, data):
        log('notify', uuid=char.uuid, hex=bytes(data).hex(' '))
        for frame in decoder.feed(data):
            info=describe(frame);frames.append(info);log('frame',**info)
    async with BleakClient(targets[0],timeout=20) as client:
        await client.start_notify(BASE.format('ffea'),notify)
        async def send(label,packet):
            log('write_attempt',label=label,hex=packet.hex(' '),response=False)
            await client.write_gatt_char(BASE.format('ffe9'),packet,response=False)
            log('write_submitted',label=label)
        async def exchange(label,packet,kind):
            start=len(frames)
            await send(label,packet)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                for frame in frames[start:]:
                    if frame['kind']==kind:return frame
                await asyncio.sleep(.05)
            raise TimeoutError(f'{label}: no {kind} response')
        verify=await exchange('verify',VERIFY,'verify')
        if verify['model']!=55:raise RuntimeError('Unexpected model; stopping')
        initial=await exchange('system_before',SYSTEM_READ_ALL,'system_info')
        before=await exchange('bridge_before',GRBL_COMM_STATUS,'grbl_comm_status')
        log('baseline',current_app=initial['current_app'],**{k:v for k,v in before.items() if k not in ('kind','hex')})
        if before['way']!=0:raise RuntimeError('Existing bridge active; do not take over')
        attempted=False
        try:
            attempted=True
            await send('enable_ble_reliable',ENABLE_BLE_RELIABLE)
            await asyncio.sleep(.2)
            status=await exchange('bridge_enabled',GRBL_COMM_STATUS,'grbl_comm_status')
            if status['way']!=2 or status['device_tx_error'] or status.get('device_rx_error',0):
                raise RuntimeError('BLE reliable transport did not enter clean way 2')
            expected=status['read_check']
            start=len(frames)
            await send('grbl_build_query',encode_query('build',status['write_check']))
            output=bytearray();consumed=start;deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                for frame in frames[consumed:]:
                    consumed+=1
                    if frame['kind']=='grbl_data':
                        payload=bytes.fromhex(frame['payload_hex'])
                        expected=accept_data(expected,frame['read_check'],payload)
                        output.extend(payload)
                lines=output.decode('ascii',errors='strict').replace('\r','').split('\n')
                if any(line.startswith(('error:','ALARM:')) for line in lines):
                    raise RuntimeError('Controller returned error/alarm; no retry')
                if 'ok' in lines:
                    if not any(line.startswith('[VER:') for line in lines):
                        raise RuntimeError('ok without build information does not prove $I success')
                    log('grbl_build_verified',text=output.decode('ascii'),read_check=expected)
                    break
                await asyncio.sleep(.05)
            else:
                raise TimeoutError('No complete VER + ok response; no retry')
            after=await exchange('bridge_after_query',GRBL_COMM_STATUS,'grbl_comm_status')
            if after['way']!=2 or after['write_check']!=(status['write_check']+3)&0xffff:
                raise RuntimeError('Write counter did not advance by the exact query byte count')
            if after['read_check']!=expected or after['device_tx_error'] or after.get('device_rx_error',0):
                raise RuntimeError('Final read counter/error mismatch')
        finally:
            if attempted:
                if not client.is_connected:
                    log('cleanup_unconfirmed',reason='BLE disconnected; no reconnect or physical state claim')
                    raise RuntimeError('Unable to confirm transport cleanup after disconnect')
                await send('disable_reliable',DISABLE_RELIABLE)
                await asyncio.sleep(.2)
                cleanup=await exchange('bridge_cleanup',GRBL_COMM_STATUS,'grbl_comm_status')
                if cleanup['way']!=0:raise RuntimeError('Could not verify disabled transport')
                final=await exchange('system_after',SYSTEM_READ_ALL,'system_info')
                log('cleanup_verified',way=cleanup['way'],initial_app=initial['current_app'],final_app=final['current_app'])
        await client.stop_notify(BASE.format('ffea'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--laser-power-isolated',action='store_true',help='User has confirmed physical laser power isolation')
    p.add_argument('--device')
    p.add_argument('--log',type=Path)
    args=p.parse_args()
    if not args.execute:
        print(json.dumps(PLAN,ensure_ascii=False,indent=2));return 0
    if not args.laser_power_isolated:
        p.error('Execution requires physical laser power isolation confirmed by user')
    path=args.log or Path(__file__).resolve().parents[1]/'logs'/('grbl-build-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.jsonl')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:
        start=time.monotonic()
        def log(event,**fields):
            text=json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),elapsed_s=round(time.monotonic()-start,4),event=event,**fields),ensure_ascii=False)
            print(text,flush=True);f.write(text+'\n');f.flush()
        log('start',laser_power_isolated_user_attestation=True)
        try:asyncio.run(run(args,log))
        except Exception as error:
            log('error',type=type(error).__name__,message=str(error));return 1
        log('complete');return 0

if __name__=='__main__':raise SystemExit(main())
