import asyncio
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from stream_tcp_bridge import StreamBridge,OutputLines
from test_byte_stream import FakeClient

class OutputTests(unittest.TestCase):
    def test_interleaved_push_messages_do_not_manufacture_terminals(self):
        raw=b'ok\n[MSG: ok is text]\n<Idle|FS:0,0>\nALARM:1\n'
        for split in range(len(raw)+1):
            parser=OutputLines()
            self.assertEqual(parser.feed(raw[:split])+parser.feed(raw[split:]),raw.splitlines(keepends=True))

class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_bytes_policy_peer_rejection_and_eof_cleanup(self):
        clients=[];rows=[]
        class Client(FakeClient):
            def __init__(self,*args,**kwargs):super().__init__();clients.append(self)
            async def __aenter__(self):return self
            async def __aexit__(self,*args):self.is_connected=False
            async def write_gatt_char(self,uuid,packet,response):
                if packet[3:5]==b'\x40\x03':
                    self.writes.append(packet)
                    assert int.from_bytes(packet[6:8],'little')==self.w
                    payload=packet[8:-1];self.w=(self.w+len(payload)) & 65535
                    if payload==b'?':self.data(b'<Idle|MPos:0,400,0,0|FS:0,0>\n')
                    else:self.data(b'[MSG:temperature]\n');self.data(b'ok\n')
                else:await super().write_gatt_char(uuid,packet,response)
        bridge=StreamBridge('fake',lambda e,**kw:rows.append(dict(event=e,**kw)),5)
        with patch.dict(sys.modules,{'bleak':types.SimpleNamespace(BleakClient=Client)}):
            server=await asyncio.start_server(bridge.handle,'127.0.0.1',0)
            port=server.sockets[0].getsockname()[1]
            reader,writer=await asyncio.open_connection('127.0.0.1',port)
            writer.write(b'G0\n$I\n$#\n');await writer.drain()
            for _ in range(3):self.assertEqual(await asyncio.wait_for(reader.readuntil(b'ok\n'),3),b'[MSG:temperature]\nok\n')
            second_r,second_w=await asyncio.open_connection('127.0.0.1',port)
            self.assertEqual(await asyncio.wait_for(second_r.read(),1),b'')
            second_w.close();await second_w.wait_closed()
            writer.write(b'G0 X1\n');await writer.drain()
            self.assertIn(b'local error follows',await asyncio.wait_for(reader.readuntil(b'error:20\n'),1))
            writer.close();await writer.wait_closed()
            for _ in range(100):
                if bridge.active is None:break
                await asyncio.sleep(.01)
            server.close();await server.wait_closed()
        self.assertFalse(bridge.poisoned)
        self.assertIsNone(bridge.active)
        payloads=[p[8:-1] for p in clients[0].writes if p[3:5]==b'\x40\x03']
        self.assertEqual(payloads,[b'?',b'G0\n',b'$I\n',b'$#\n'])
        self.assertTrue(any(r['event']=='cleanup_verified' for r in rows))
        self.assertFalse(any(r['event']=='session_error' for r in rows))
if __name__=='__main__':unittest.main()
