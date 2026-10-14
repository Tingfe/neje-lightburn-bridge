import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import grbl_readonly_probe as probe
from grbl_readonly import ENABLE_BLE_RELIABLE,DISABLE_RELIABLE,encode_query
from neje_protocol import VERIFY,SYSTEM_READ_ALL,GRBL_COMM_STATUS,crc32_mpeg2


class ProbeGuards(unittest.TestCase):
    def test_default_is_offline_plan(self):
        result=subprocess.run([sys.executable,str(ROOT/'tools/grbl_readonly_probe.py')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        self.assertTrue(json.loads(result.stdout)['offline_only'])

    def test_execution_requires_isolation(self):
        result=subprocess.run([sys.executable,str(ROOT/'tools/grbl_readonly_probe.py'),'--execute'],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertIn('physical laser power isolation',result.stderr)


class ProbeSession(unittest.IsolatedAsyncioTestCase):
    async def simulate(self,variant):
        writes=[];logs=[]
        class Scanner:
            @staticmethod
            async def discover(timeout):return [types.SimpleNamespace(name='NEJE 5 Plus',address='test')]
        class Client:
            def __init__(self,*args,**kwargs):
                self.is_connected=True;self.way=0;self.write_check=0;self.read_check=0
            async def __aenter__(self):return self
            async def __aexit__(self,*args):self.is_connected=False
            async def start_notify(self,uuid,callback):self.callback=callback
            async def stop_notify(self,uuid):pass
            def emit(self,frame):self.callback(types.SimpleNamespace(uuid='ffea'),frame)
            async def write_gatt_char(self,uuid,packet,response):
                writes.append(packet)
                if packet==VERIFY:self.emit(bytes.fromhex('ff aa 08 01 01 37 30 55'))
                elif packet==SYSTEM_READ_ALL:
                    self.emit(bytes.fromhex('ff aa 15 10 01 01 ff 37 02 02 00 01 4e 4e 42 4b 4a 32 58 50 55'))
                elif packet==GRBL_COMM_STATUS:
                    self.emit(bytes.fromhex('ff aa 0d 41 01')+bytes([self.way])+
                              self.write_check.to_bytes(2,'little')+self.read_check.to_bytes(2,'little')+bytes.fromhex('00 00 55'))
                elif packet==ENABLE_BLE_RELIABLE:self.way=1 if variant=='bad_enable' else 2
                elif packet==DISABLE_RELIABLE:self.way=0
                elif packet==encode_query('build',self.write_check):
                    self.write_check=(self.write_check+3)&65535
                    text=b'ALARM:1\r\n' if variant=='alarm' else b'[VER:2.3.2:test]\r\nok\r\n'
                    seq=self.read_check+1 if variant=='bad_sequence' else self.read_check
                    body=bytes([255,170,len(text)+9,64,4,0])+seq.to_bytes(2,'little')+text
                    self.emit(body+bytes([crc32_mpeg2(body[1:]) & 255]))
                    self.read_check=(self.read_check+len(text))&65535
                else:raise AssertionError('Unexpected command')
        fake=types.SimpleNamespace(BleakScanner=Scanner,BleakClient=Client)
        error=None
        with patch.dict(sys.modules,{'bleak':fake}):
            try:await probe.run(types.SimpleNamespace(device=None),lambda event,**data:logs.append({'event':event,**data}))
            except Exception as e:error=e
        return writes,logs,error

    async def test_success_has_exact_query_and_verified_cleanup(self):
        writes,logs,error=await self.simulate('success')
        self.assertIsNone(error)
        self.assertEqual(writes.count(encode_query('build',0)),1)
        self.assertEqual(writes[-3:],[DISABLE_RELIABLE,GRBL_COMM_STATUS,SYSTEM_READ_ALL])
        self.assertTrue(any(r['event']=='grbl_build_verified' for r in logs))
        self.assertTrue(any(r['event']=='cleanup_verified' for r in logs))

    async def test_bad_sequence_stops_and_cleans_up_without_retry(self):
        writes,logs,error=await self.simulate('bad_sequence')
        self.assertIsInstance(error,ValueError)
        self.assertEqual(writes.count(encode_query('build',0)),1)
        self.assertIn(DISABLE_RELIABLE,writes)

    async def test_failed_enable_sends_no_query_and_cleans_up(self):
        writes,logs,error=await self.simulate('bad_enable')
        self.assertIsInstance(error,RuntimeError)
        self.assertNotIn(encode_query('build',0),writes)
        self.assertIn(DISABLE_RELIABLE,writes)

    async def test_alarm_stops_without_reset_and_cleans_up(self):
        writes,logs,error=await self.simulate('alarm')
        self.assertIsInstance(error,RuntimeError)
        self.assertIn(DISABLE_RELIABLE,writes)
        self.assertNotIn(b'\x18',writes)

if __name__=='__main__':unittest.main()
