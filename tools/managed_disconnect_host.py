"""One real managed initialization disconnect; separate experiment, no job.

Default offline. Disconnect after a real $I terminal, during the next status
exchange. Delegates all GATT writes unchanged; does not inspect/recover itself.
"""
import argparse
import json
from unittest.mock import patch

import lightburn_session as manager
from neje_protocol import GRBL_COMM_STATUS


def observed_client(base,log,instances):
    class Client(base):
        def __init__(self,*args,**kwargs):
            if instances:raise RuntimeError('One connection only in fault experiment')
            super().__init__(*args,**kwargs);instances.append(self)
            self.armed=False;self.requested=False
        async def write_gatt_char(self,char,data,**kwargs):
            packet=bytes(data)
            if self.requested:raise RuntimeError('No GATT write after disconnect request')
            await super().write_gatt_char(char,packet,**kwargs)
            if self.armed and packet==GRBL_COMM_STATUS:
                self.requested=True
                log('managed_link_disconnect_requested',real_ble=True,
                    after_real_build_terminal=True,no_motion=True,no_replay=True)
                await self.disconnect()
                log('managed_link_disconnect_returned',is_connected=self.is_connected)
    return Client


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--allow-managed-disconnect',action='store_true')
    p.add_argument('--laser-power-isolated',action='store_true')
    args=p.parse_args(argv)
    if not args.execute:
        print(json.dumps(dict(offline_only=True,no_motion=True,
            fault='real BLE disconnect after real $I terminal',automatic_recovery=False)));return 0
    if not (args.allow_managed_disconnect and args.laser_power_isolated):
        p.error('Requires explicit fault scope and laser power isolation')
    from bleak import BleakClient
    original_run=manager.bridge.run;instances=[]
    async def run(options,log):
        def observe(event,**fields):
            log(event,**fields)
            if event=='pipeline_terminal' and bytes.fromhex(fields['command_hex'])==b'$I\n':
                instances[0].armed=True
        client=observed_client(BleakClient,observe,instances)
        with patch('bleak.BleakClient',client):await original_run(options,observe)
    with patch.object(manager.bridge,'run',run):
        code=manager.main(['start','--execute','--allow-initialization',
                           '--laser-power-isolated','--seconds','180'])
    if code!=1 or len(instances)!=1 or not instances[0].requested or instances[0].is_connected:
        raise RuntimeError('Intended managed fault was not captured; no retry')
    return 0


if __name__=='__main__':raise SystemExit(main())
