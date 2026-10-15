"""Narrow shutdown info read; reproduce the real optional SystemRead tail."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from neje_byte_stream import ByteStreamSession
from tcp_readonly_bridge import ReliableSession
from neje_protocol import SYSTEM_READ_ALL
from grbl_readonly import DISABLE_RELIABLE
from fake_ble import FakeClient

INFO=bytes.fromhex('ff aa 08 10 01 01 00 55')
SYSTEM_REPLY=bytes.fromhex('ffaa15100101ff37030200014e4e424b4a32585055')
DEBUG_TAIL=bytes.fromhex('ffaa3315010400009f2ee80cee10ce0c040000000c00170000000000b40401001b07c8')


async def no_delay(seconds):pass


class SystemClient(FakeClient):
    def __init__(self,*,split=None,fail_info=False,omit_info=False):
        super().__init__();self.disabled=False;self.split=split
        self.fail_info=fail_info;self.omit_info=omit_info
    async def write_gatt_char(self,uuid,packet,response):
        if packet==DISABLE_RELIABLE:self.disabled=True
        if packet==INFO:
            self.writes.append(packet)
            if self.fail_info:
                self.is_connected=False
                raise ConnectionError('Selective info accepted then link lost')
            if not self.omit_info:
                split=len(SYSTEM_REPLY) if self.split is None else self.split
                self.emit(SYSTEM_REPLY[:split]);self.emit(SYSTEM_REPLY[split:])
            return
        await super().write_gatt_char(uuid,packet,response)
        if packet==SYSTEM_READ_ALL and self.disabled:self.emit(DEBUG_TAIL)


class SelectiveSystemReadTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,client,session_class=ByteStreamSession):
        rows=[];session=session_class(client,lambda event,**kw:rows.append(dict(event=event,**kw)))
        with patch('tcp_readonly_bridge.asyncio.sleep',no_delay):
            await session.start();await session.close()
        return session,rows

    async def test_byte_stream_close_requests_only_info_and_leaves_no_tail(self):
        client=SystemClient();session,rows=await self.exercise(client)
        self.assertEqual(client.writes[-1],INFO)
        self.assertEqual(client.writes.count(SYSTEM_READ_ALL),1) # Startup stays unchanged.
        self.assertFalse(session.decoder.buffer)
        self.assertTrue(any(r['event']=='cleanup_verified' and r['final_app']==3 for r in rows))

    async def test_legacy_reliable_session_keeps_all_read_and_reproduces_tail(self):
        client=SystemClient();session,_=await self.exercise(client,ReliableSession)
        self.assertEqual(client.writes[-1],SYSTEM_READ_ALL)
        self.assertEqual(bytes(session.decoder.buffer),DEBUG_TAIL)

    async def test_selective_reply_is_decoded_at_every_fragment_boundary(self):
        for split in range(len(SYSTEM_REPLY)+1):
            with self.subTest(split=split):
                session,rows=await self.exercise(SystemClient(split=split))
                self.assertFalse(session.decoder.buffer)
                self.assertEqual(sum(r['event']=='cleanup_verified' for r in rows),1)

    async def test_accepted_info_disconnect_is_unknown_without_fallback_or_retry(self):
        client=SystemClient(fail_info=True);rows=[]
        session=ByteStreamSession(client,lambda event,**kw:rows.append(dict(event=event,**kw)))
        with patch('tcp_readonly_bridge.asyncio.sleep',no_delay):
            await session.start()
            with self.assertRaises(ConnectionError):await session.close()
        self.assertEqual(client.writes.count(INFO),1)
        self.assertEqual(client.writes.count(SYSTEM_READ_ALL),1)
        self.assertFalse(any(r['event']=='cleanup_verified' for r in rows))

    async def test_missing_info_does_not_fabricate_app_confirmation(self):
        class FastTimeout(ByteStreamSession):
            async def wait_kind(self,kind,timeout=5):
                return await super().wait_kind(kind,timeout=.001)
        client=SystemClient(omit_info=True);rows=[]
        session=FastTimeout(client,lambda event,**kw:rows.append(dict(event=event,**kw)))
        with patch('tcp_readonly_bridge.asyncio.sleep',no_delay):
            await session.start()
            with self.assertRaises(TimeoutError):await session.close()
        self.assertEqual(client.writes.count(INFO),1)
        self.assertFalse(any(r['event']=='cleanup_verified' for r in rows))


if __name__=='__main__':unittest.main()
