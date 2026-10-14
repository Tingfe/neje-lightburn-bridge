import asyncio
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from tcp_readonly_bridge import QueryParser, Bridge
from grbl_readonly import ENABLE_BLE_RELIABLE, DISABLE_RELIABLE, encode_query
from neje_protocol import VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS, crc32_mpeg2


class ParserTests(unittest.TestCase):
    def test_tcp_fragmentation_and_whitelist(self):
        raw=b'$I\r\n?G0 X1\n$40=0\n\x18$$\n$I\n'
        expected=[('query','build'),('query','status'),('blocked',b'G0 X1'.hex()),('blocked',b'$40=0'.hex()),('blocked','18'),('blocked','2424'),('query','build')]
        for split in range(len(raw)+1):
            parser=QueryParser()
            self.assertEqual(parser.feed(raw[:split])+parser.feed(raw[split:]),expected)
    def test_partial_motion_never_becomes_query(self):
        p=QueryParser()
        self.assertEqual(p.feed(b'G0?\n'),[('blocked',b'G0?'.hex())])
        self.assertEqual(p.feed(b'$I'),[])
    def test_overflow(self):
        with self.assertRaises(ValueError): QueryParser().feed(b'G'*129)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, mode):
        logs=[];writes=[];instances=[]
        def log(event,**fields):logs.append({'event':event,**fields})
        class Client:
            def __init__(self,*args,**kw):
                self.is_connected=True;self.way=0;self.wcheck=65534;self.rcheck=65530
                self.disconnected_callback=kw.get('disconnected_callback')
                instances.append(self)
            async def __aenter__(self):return self
            async def __aexit__(self,*args):
                self.is_connected=False
                if self.disconnected_callback:self.disconnected_callback(self)
            async def start_notify(self,uuid,cb):self.cb=cb
            async def stop_notify(self,uuid):pass
            def emit(self,b):self.cb(types.SimpleNamespace(uuid='ffea'),b)
            async def write_gatt_char(self,uuid,b,response):
                writes.append(b)
                if b==VERIFY:self.emit(bytes.fromhex('ffaa080101373055'))
                elif b==SYSTEM_READ_ALL:self.emit(bytes.fromhex('ffaa15100101ff37030200014e4e424b4a32585055'))
                elif b==ENABLE_BLE_RELIABLE:self.way=2
                elif b==DISABLE_RELIABLE:self.way=0
                elif b==GRBL_COMM_STATUS:
                    self.emit(bytes.fromhex('ffaa0d4101')+bytes([self.way])+self.wcheck.to_bytes(2,'little')+self.rcheck.to_bytes(2,'little')+bytes.fromhex('000055'))
                elif b==encode_query('build',self.wcheck):
                    self.wcheck=(self.wcheck+3)&65535
                    if mode=='pending_ble_loss':
                        self.is_connected=False
                        if self.disconnected_callback:self.disconnected_callback(self)
                        return
                    if mode=='pending_eof':return
                    text=b'[VER:test]\nok\n'
                    body=bytes([255,170,len(text)+9,64,4,0])+self.rcheck.to_bytes(2,'little')+text
                    self.emit(body+bytes([crc32_mpeg2(body[1:]) & 255]))
                    self.rcheck=(self.rcheck+len(text))&65535
                else:raise AssertionError(b.hex())
        bridge=Bridge('test',log,session_timeout=3)
        with patch.dict(sys.modules,{'bleak':types.SimpleNamespace(BleakClient=Client)}):
            server=await asyncio.start_server(bridge.handle,'127.0.0.1',0)
            port=server.sockets[0].getsockname()[1]
            reader,writer=await asyncio.open_connection('127.0.0.1',port)
            writer.write(b'$I\n');await writer.drain()
            for _ in range(200):
                if any(r.get('label')=='query_build' for r in logs):break
                await asyncio.sleep(.01)
            if mode=='success':
                self.assertEqual(await reader.readuntil(b'ok\n'),b'[VER:test]\nok\n')
                other_r,other_w=await asyncio.open_connection('127.0.0.1',port)
                self.assertEqual(await other_r.read(),b'')
                other_w.close();await other_w.wait_closed()
            elif mode=='ble_loss':instances[0].is_connected=False
            elif mode=='cancel':bridge.active.cancel()
            elif mode=='pending_ble_loss':self.assertEqual(await asyncio.wait_for(reader.read(),1),b'')
            writer.close();await writer.wait_closed()
            for _ in range(200):
                if bridge.active is None:break
                await asyncio.sleep(.01)
            server.close();await server.wait_closed()
        self.assertIsNone(bridge.active)
        return logs,writes,bridge

    async def test_tcp_eof_cleanup_and_nonzero_wrap(self):
        logs,writes,bridge=await self.exercise('success')
        self.assertTrue(any(r['event']=='query_verified' and r['write_check']==1 for r in logs))
        self.assertTrue(any(r['event']=='cleanup_verified' for r in logs))
        self.assertEqual(writes.count(DISABLE_RELIABLE),1)
        self.assertFalse(bridge.poisoned)
        self.assertTrue(any(r['event']=='ble_disconnected' and not r['is_connected'] for r in logs))
    async def test_eof_during_pending_query_cleans_without_retry(self):
        logs,writes,bridge=await self.exercise('pending_eof')
        self.assertEqual(sum(b[3:5]==bytes.fromhex('4003') for b in writes),1)
        self.assertTrue(any(r['event']=='cleanup_verified' for r in logs))
    async def test_cancellation_waits_for_cleanup(self):
        logs,writes,bridge=await self.exercise('cancel')
        self.assertTrue(any(r['event']=='cleanup_verified' for r in logs))
    async def test_inflight_ble_loss_never_delivers_or_retries(self):
        logs,writes,bridge=await self.exercise('pending_ble_loss')
        self.assertTrue(bridge.poisoned)
        self.assertNotIn(DISABLE_RELIABLE,writes)
        self.assertEqual(sum(b[3:5]==bytes.fromhex('4003') for b in writes),1)
        self.assertFalse(any(r['event'] in ('query_verified','tcp_delivered','cleanup_verified') for r in logs))
        self.assertTrue(any(r['event']=='cleanup_unconfirmed' for r in logs))
    async def test_ble_loss_unknown_and_reject_future_peer(self):
        logs,writes,bridge=await self.exercise('ble_loss')
        self.assertTrue(bridge.poisoned)
        self.assertNotIn(DISABLE_RELIABLE,writes)
        self.assertTrue(any(r['event']=='cleanup_unconfirmed' for r in logs))
        self.assertFalse(any(r['event']=='cleanup_verified' for r in logs))
        server=await asyncio.start_server(bridge.handle,'127.0.0.1',0)
        reader,writer=await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1])
        self.assertEqual(await asyncio.wait_for(reader.read(),1),b'')
        writer.close();await writer.wait_closed()
        server.close();await server.wait_closed()

if __name__=='__main__':unittest.main()

class ServerShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_peer_before_wait_closed(self):
        from tcp_readonly_bridge import run
        cleaned=asyncio.Event(); logs=[]
        class Scanner:
            @staticmethod
            async def discover(timeout):return [types.SimpleNamespace(name='NEJE 5 Plus')]
        async def start_server(callback,host,port):
            async def active_peer():
                try:await asyncio.Event().wait()
                finally:cleaned.set()
            callback.__self__.active=asyncio.create_task(active_peer())
            class Server:
                def close(self):pass
                async def wait_closed(self):await cleaned.wait()
            return Server()
        args=types.SimpleNamespace(device=None,session_timeout=1,port=0,lifetime=.01)
        with patch.dict(sys.modules,{'bleak':types.SimpleNamespace(BleakScanner=Scanner)}), patch('tcp_readonly_bridge.asyncio.start_server',start_server):
            await asyncio.wait_for(run(args,lambda event,**fields:logs.append(event)),1)
        self.assertTrue(cleaned.is_set())
        self.assertIn('server_stopped',logs)
