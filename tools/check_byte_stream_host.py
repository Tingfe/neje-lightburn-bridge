"""Mac BLE continuous transport regression, fixed $I/? only; offline by default."""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
from neje_byte_stream import ByteStreamSession
from lightburn_init_bridge import reported_idle

async def run(log):
    from bleak import BleakClient,BleakScanner
    found=await BleakScanner.discover(timeout=8)
    targets=[d for d in found if d.name=='NEJE 5 Plus']
    if len(targets)!=1:raise RuntimeError('Expected exactly one NEJE 5 Plus')
    async with BleakClient(targets[0],timeout=20) as client:
        characteristic=client.services.get_characteristic('0000ffe9-0000-1000-8000-00805f9b34fb')
        log('gatt_limit',max_write_without_response_size=characteristic.max_write_without_response_size)
        session=ByteStreamSession(client,log)
        try:
            await session.start()
            async def query(payload):
                assert payload in (b'$I\n',b'?')
                output=bytearray(await session.write(payload))
                for _ in range(40):
                    lines=output.decode('ascii').replace('\r','').split('\n')[:-1]
                    if any(line.startswith(('error:','ALARM:','Grbl ')) for line in lines):
                        raise RuntimeError('Device error/alarm/reset')
                    done=('ok' in lines and any(line.startswith('[VER:') for line in lines)) if payload==b'$I\n' else any(line.startswith('<') and line.endswith('>') for line in lines)
                    if done:
                        log('stream_query_result',payload_hex=payload.hex(),text=output.decode('ascii'))
                        return bytes(output)
                    await asyncio.sleep(.1)
                    output.extend(await session.verify())
                raise TimeoutError('Read-only result incomplete; no resend')
            if not reported_idle((await query(b'?')).decode('ascii')):
                raise RuntimeError('Expected reported Idle / FS:0,0')
            for _ in range(5):
                await query(b'$I\n')
                if not reported_idle((await query(b'?')).decode('ascii')):
                    raise RuntimeError('State changed during read-only regression')
                await asyncio.sleep(.15)
                idle=await session.verify()
                log('stream_idle_poll',hex=idle.hex())
        finally:
            await session.close()
    log('continuous_readonly_regression_passed',build_queries=5,status_queries=6,
        no_movement=True,no_fake_response=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--laser-power-isolated',action='store_true')
    p.add_argument('--log',type=Path)
    args=p.parse_args()
    if not args.execute:
        print(json.dumps(dict(offline_only=True,allowed=['$I','?'],motion=False)));return 0
    if not args.laser_power_isolated or not args.log:p.error('Requires existing isolation authorization and new log')
    with args.log.open('x') as stream:
        def log(event,**kw):
            row=json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),event=event,**kw))
            stream.write(row+'\n');stream.flush();print(row,flush=True)
        try:asyncio.run(run(log))
        except Exception as exc:log('error',message=repr(exc));return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
