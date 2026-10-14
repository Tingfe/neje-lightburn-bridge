"""Continuous byte bridge with explicit command policy; default initialization only.

No general machining entry point. It preserves GRBL data for LightBurn to parse.
"""
import argparse
import asyncio
import contextlib
import datetime
import json
from pathlib import Path
import signal
import time
from lightburn_init_bridge import InitParser, REQUESTS, reported_idle
from neje_byte_stream import ByteStreamSession


class OutputLines:
    def __init__(self): self.pending=bytearray()
    def feed(self,data):
        self.pending.extend(data)
        if len(self.pending)>8192:raise RuntimeError('GRBL line buffer overflow')
        lines=[]
        while b'\n' in self.pending:
            i=self.pending.index(10)
            lines.append(bytes(self.pending[:i+1]));del self.pending[:i+1]
        return lines


class StreamBridge:
    def __init__(self,device,log,session_timeout=90):
        self.device,self.log,self.timeout=device,log,session_timeout
        self.active=None;self.poisoned=False

    async def handle(self,reader,writer):
        if self.active is not None or self.poisoned:
            self.log('tcp_rejected',reason='busy_or_cleanup_unconfirmed')
            writer.close();await writer.wait_closed();return
        self.active=asyncio.current_task()
        queue=asyncio.Queue(maxsize=16)
        self.log('tcp_connected',peer=writer.get_extra_info('peername'))
        output=OutputLines()
        async def deliver(data):
            if not data:return
            lines=output.feed(data)
            writer.write(data);await asyncio.wait_for(writer.drain(),2)
            self.log('stream_tcp_delivered',hex=data.hex())
            if any(line.startswith((b'error:',b'ALARM:',b'Grbl ')) for line in lines):
                raise RuntimeError('Controller error/alarm/reset forwarded; stop unsent queue')
        async def receive():
            parser=InitParser()
            while True:
                data=await reader.read(512)
                if not data:
                    self.log('tcp_eof',pending_hex=parser.pending.hex());return
                self.log('tcp_receive',hex=data.hex())
                for kind,name in parser.feed(data):
                    if kind=='blocked':
                        self.log('tcp_blocked',hex=name)
                        writer.write(b'[MSG:LaserLink initialization policy blocked input; local error follows]\nerror:20\n')
                        await asyncio.wait_for(writer.drain(),2)
                    else:
                        # Await capacity to propagate backpressure to TCP. No
                        # unbounded accumulation and no eviction of old commands.
                        await queue.put(REQUESTS[name])
        async def operate():
            from bleak import BleakClient
            def disconnected(client):self.log('ble_disconnected',is_connected=client.is_connected)
            async with BleakClient(self.device,timeout=20,disconnected_callback=disconnected) as client:
                session=ByteStreamSession(client,self.log)
                try:
                    await session.start()
                    baseline=bytearray(await session.write(b'?'))
                    deadline=time.monotonic()+5
                    while b'\n' not in baseline:
                        if time.monotonic()>deadline:raise TimeoutError('No Idle baseline')
                        await asyncio.sleep(.05);baseline.extend(await session.verify())
                    if not reported_idle(baseline.decode('ascii')):
                        raise RuntimeError('Requires reported Idle / FS:0,0')
                    self.log('stream_idle_baseline',text=baseline.decode('ascii'))
                    char=client.services.get_characteristic('0000ffe9-0000-1000-8000-00805f9b34fb')
                    capacity=min(256,char.max_write_without_response_size-9)
                    if capacity<1:raise RuntimeError('GATT write capacity too small')
                    self.log('stream_payload_capacity',bytes=capacity)
                    while True:
                        try:payload=await asyncio.wait_for(queue.get(),.15)
                        except TimeoutError:
                            await deliver(await session.verify());continue
                        # NEJE payloads are one ordered byte stream. Packet
                        # boundaries need not coincide with GRBL line boundaries.
                        for offset in range(0,len(payload),capacity):
                            await deliver(await session.write(payload[offset:offset+capacity]))
                finally:
                    try:await session.close()
                    except BaseException:
                        self.poisoned=True;raise
        tasks=[asyncio.create_task(receive()),asyncio.create_task(operate())]
        try:
            done,_=await asyncio.wait(tasks,timeout=self.timeout,return_when=asyncio.FIRST_COMPLETED)
            if not done:self.log('session_timeout')
            for task in done:task.result()
        except asyncio.CancelledError:
            self.log('session_cancelled');raise
        except Exception as error:self.log('session_error',type=type(error).__name__,message=str(error))
        finally:
            for task in tasks:
                if not task.done():task.cancel()
            for result in await asyncio.gather(*tasks,return_exceptions=True):
                if isinstance(result,Exception):self.log('task_error',message=str(result))
            writer.close()
            with contextlib.suppress(ConnectionError):await writer.wait_closed()
            self.active=None
            self.log('tcp_closed',cleanup_unconfirmed=self.poisoned,discarded_unsent_queue=queue.qsize())


async def run(args,log):
    from bleak import BleakScanner
    devices=await BleakScanner.discover(timeout=8)
    targets=[d for d in devices if d.name=='NEJE 5 Plus']
    if len(targets)!=1:raise RuntimeError('Expected exactly one NEJE 5 Plus')
    bridge=StreamBridge(targets[0],log,args.seconds)
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
    server=await asyncio.start_server(bridge.handle,'127.0.0.1',4002)
    log('listening',bind='127.0.0.1:4002',allowed={k:v.hex() for k,v in REQUESTS.items()},profile='continuous-init')
    try:
        with contextlib.suppress(TimeoutError):await asyncio.wait_for(stop.wait(),args.seconds)
    finally:
        server.close()
        if bridge.active:
            bridge.active.cancel();await asyncio.gather(bridge.active,return_exceptions=True)
        await server.wait_closed();log('server_stopped',cleanup_unconfirmed=bridge.poisoned)
    if bridge.poisoned:raise RuntimeError('Cleanup unknown; explicit recovery required')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true');p.add_argument('--allow-initialization',action='store_true')
    p.add_argument('--laser-power-isolated',action='store_true');p.add_argument('--seconds',type=int,default=90)
    p.add_argument('--log',type=Path);args=p.parse_args()
    if not args.execute:
        print(json.dumps(dict(offline_only=True,allowed={k:v.decode() for k,v in REQUESTS.items()},machining=False)));return 0
    if not(args.allow_initialization and args.laser_power_isolated and args.log):p.error('Requires existing initialization/isolation authorization and new log')
    if not 1<=args.seconds<=300:p.error('seconds must be 1..300')
    with args.log.open('x') as stream:
        def log(event,**fields):
            row=json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),event=event,**fields))
            stream.write(row+'\n');stream.flush();print(row,flush=True)
        try:asyncio.run(run(args,log))
        except Exception as exc:log('error',message=repr(exc));return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
