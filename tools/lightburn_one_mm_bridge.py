"""Separately authorized, one-session LightBurn console 1mm experiment only."""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
from lightburn_init_bridge import InitParser, InitSession, REQUESTS
from motion_parity_probe import MOVES, QUERIES, preflight, check_position, require_ok
from tcp_readonly_bridge import Bridge

class MotionConsoleParser(InitParser):
    def feed(self, data):
        commands = {v.rstrip(b'\n'): k for k,v in MOVES.items()}
        result=[]
        for kind,value in super().feed(data):
            if kind=='blocked' and bytes.fromhex(value) in commands:
                result.append(('query',commands[bytes.fromhex(value)]))
            else: result.append((kind,value))
        return result

class MotionConsoleSession(InitSession):
    request_table=QUERIES
    next_move=0
    async def query(self,name):
        if name not in MOVES:
            return await super().query(name)
        expected=('x_out','x_back')
        if self.next_move>=2 or name!=expected[self.next_move]:
            raise RuntimeError('Only one ordered 0->1->0 cycle permitted; no replay')
        start=0 if name=='x_out' else 1
        settings=await super().query('settings')
        modal=await super().query('modal')
        parameters=await super().query('parameters')
        status=await super().query('status')
        preflight(*(part.decode('ascii') for part in (settings,modal,parameters,status)),start_x=start)
        # Consume authorization before any write. An uncertain send can never be retried.
        self.next_move+=1
        data=await super().query(name)
        require_ok(data.decode('ascii'))
        deadline=asyncio.get_running_loop().time()+5
        target=1 if name=='x_out' else 0
        while asyncio.get_running_loop().time()<deadline:
            report=(await super().query('status')).decode('ascii')
            state,position=check_position(report,target,False)
            if state=='Idle':
                check_position(report,target)
                self.log('console_motion_arrival',name=name,position=position)
                return data
            await asyncio.sleep(.1)
        raise TimeoutError('Controller completion unconfirmed; close without retry')

async def run(args,log):
    from bleak import BleakScanner
    devices=await BleakScanner.discover(timeout=8)
    targets=[d for d in devices if d.name=='NEJE 5 Plus']
    if len(targets)!=1: raise RuntimeError('Expected exactly one NEJE 5 Plus')
    def session_factory(client,logger):
        session=MotionConsoleSession(client,logger)
        session.next_move=1 if args.return_only else 0
        return session
    bridge=Bridge(targets[0],log,90,MotionConsoleParser,session_factory)
    async def handle(reader,writer):
        # One accepted connection only; reconnect cannot reset movement allowance.
        server.close()
        await bridge.handle(reader,writer)
    server=await asyncio.start_server(handle,'127.0.0.1',4002)
    log('listening',bind='127.0.0.1:4002',profile='one-mm-console',one_connection_only=True)
    try:
        await asyncio.sleep(90)
    finally:
        server.close()
        if bridge.active:
            bridge.active.cancel()
            await asyncio.gather(bridge.active,return_exceptions=True)
        await server.wait_closed()
        log('server_stopped',cleanup_unconfirmed=bridge.poisoned)
    if bridge.poisoned: raise RuntimeError('Cleanup unconfirmed; explicit recovery required')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--allow-lightburn-one-mm',action='store_true')
    p.add_argument('--laser-power-isolated',action='store_true')
    p.add_argument('--return-only',action='store_true',help='Requires explicit separate remaining-return authorization')
    p.add_argument('--log',type=Path)
    args=p.parse_args()
    if not args.execute:
        print(json.dumps(dict(offline_only=True,exact_motion={k:v.decode() for k,v in MOVES.items()},
                             interface='LightBurn console',full_job_streaming=False))); return
    if not(args.allow_lightburn_one_mm and args.laser_power_isolated and args.log):
        p.error('Requires separate onsite LightBurn 1mm authorization, isolation and new log')
    with args.log.open('x') as stream:
        def log(event,**fields):
            row=json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),event=event,**fields))
            stream.write(row+'\n');stream.flush();print(row,flush=True)
        try: asyncio.run(run(args,log))
        except Exception as exc:
            log('error',error=repr(exc));return 1
    return 0
if __name__=='__main__': raise SystemExit(main())
