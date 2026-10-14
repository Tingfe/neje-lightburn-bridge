# evidence/ is deliberately not part of the repository; tests that read real captures
# skip themselves explicitly rather than failing or pretending to pass.
import os as _os, unittest as _unittest
if not _os.path.isdir(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'evidence')):
    raise _unittest.SkipTest('evidence/ is not part of this repository')
import asyncio
import json
from pathlib import Path
import sys
import types
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from neje_byte_stream import ByteStreamSession
from neje_protocol import FrameDecoder,crc32_mpeg2,VERIFY,SYSTEM_READ_ALL,SYSTEM_READ_INFO,GRBL_COMM_STATUS
from grbl_readonly import ENABLE_BLE_RELIABLE,DISABLE_RELIABLE


def data_frame(payload, counter, trailer=None):
    size=len(payload)+9
    body=bytes([255,170+size//256,size%256,64,4,0])+counter.to_bytes(2,'little')+payload
    return body+bytes([crc32_mpeg2(body[1:]) & 255 if trailer is None else trailer])

class FakeClient:
    def __init__(self):
        self.is_connected=True;self.way=0;self.w=65534;self.r=65530
        self.writes=[];self.tail_after_snapshot=None;self.fail_write=False
        self.services=types.SimpleNamespace(get_characteristic=lambda uuid:types.SimpleNamespace(max_write_without_response_size=265))
    async def start_notify(self,uuid,cb): self.cb=cb
    async def stop_notify(self,uuid): pass
    def emit(self,packet): self.cb(None,packet)
    def data(self,payload):
        self.emit(data_frame(payload,self.r));self.r=(self.r+len(payload)) & 65535
    async def write_gatt_char(self,uuid,packet,response):
        self.writes.append(packet)
        if packet==VERIFY:self.emit(bytes.fromhex('ffaa080101373055'))
        elif packet in (SYSTEM_READ_ALL,SYSTEM_READ_INFO):self.emit(bytes.fromhex('ffaa15100101ff37030200014e4e424b4a32585055'))
        elif packet==ENABLE_BLE_RELIABLE:self.way=2
        elif packet==DISABLE_RELIABLE:self.way=0
        elif packet==GRBL_COMM_STATUS:
            self.emit(bytes.fromhex('ffaa0d4101')+bytes([self.way])+self.w.to_bytes(2,'little')+self.r.to_bytes(2,'little')+b'\0\0\x55')
            if self.tail_after_snapshot:
                tail=self.tail_after_snapshot;self.tail_after_snapshot=None;self.data(tail)
        else:
            assert int.from_bytes(packet[6:8],'little')==self.w
            self.w=(self.w+len(packet[8:-1])) & 65535
            if self.fail_write: raise ConnectionError('Injected failure after device acceptance')
            self.data(b'ok\n');self.data(b'[MSG:temperature]\n')

class StrictCRC(unittest.TestCase):
    def test_55_is_not_crc_bypass(self):
        packet=data_frame(b'ok\n',0)
        self.assertNotEqual(packet[-1],85)
        with self.assertRaises(ValueError):FrameDecoder().feed(data_frame(b'ok\n',0,85))
        self.assertEqual(FrameDecoder().feed(packet),[packet])
    def test_every_split_of_real_temperature_pair(self):
        rows=[json.loads(x) for x in (Path(__file__).resolve().parents[1]/'evidence/lightburn-init-profile/lightburn-console-motion-01.jsonl').read_text().splitlines()]
        packets=[bytes.fromhex(r['hex']) for r in rows if r['event']=='frame' and r.get('kind')=='grbl_data' and r.get('read_check') in (23459,23462)]
        self.assertEqual(len(packets),2)
        stream=b''.join(packets)
        for i in range(len(stream)+1):
            decoder=FrameDecoder()
            self.assertEqual(decoder.feed(stream[:i])+decoder.feed(stream[i:]),packets)

class ByteStreamTests(unittest.IsolatedAsyncioTestCase):
    async def make(self):
        client=FakeClient();rows=[]
        session=ByteStreamSession(client,lambda e,**kw:rows.append(dict(event=e,**kw)))
        await session.start()
        return session,client,rows
    async def test_split_temperature_and_idle_notification_survive_next_write(self):
        s,c,rows=await self.make()
        self.assertEqual(await s.write(b'$I\n'),b'ok\n[MSG:temperature]\n')
        c.data(b'[MSG:idle]\n')
        self.assertEqual(await s.write(b'?'),b'[MSG:idle]\nok\n[MSG:temperature]\n')
        self.assertEqual(s.pending_rx,b'')
        self.assertEqual(s.wcheck,2)
        await s.close()
        self.assertTrue(any(r['event']=='cleanup_verified' and r['way']==0 for r in rows))
    async def test_notification_after_status_is_staged_until_next_verification(self):
        s,c,_=await self.make()
        c.tail_after_snapshot=b'[MSG:late]\n'
        self.assertEqual(await s.verify(),b'')
        self.assertEqual(s.pending_rx,b'[MSG:late]\n')
        self.assertEqual(await s.verify(),b'[MSG:late]\n')
        await s.close()
    async def test_uncertain_write_never_retries_but_cleans_route(self):
        s,c,rows=await self.make();c.fail_write=True
        with self.assertRaises(ConnectionError):await s.write(b'$I\n')
        n=len(c.writes)
        with self.assertRaises(ConnectionError):await s.write(b'$I\n')
        self.assertEqual(len(c.writes),n)
        await s.close()
        self.assertEqual(c.writes.count(DISABLE_RELIABLE),1)
    async def test_oversize_rejected_before_write_or_counter_reservation(self):
        s,c,_=await self.make()
        c.services=types.SimpleNamespace(get_characteristic=lambda uuid:types.SimpleNamespace(max_write_without_response_size=20))
        n=len(c.writes);before=s.wcheck
        with self.assertRaises(ValueError):await s.write(b'A'*12)
        self.assertEqual((len(c.writes),s.wcheck),(n,before))
        await s.close()
    async def test_gap_and_corruption_fault_without_forwarding(self):
        for packet in (data_frame(b'bad\n',65531),data_frame(b'bad\n',65530,85)):
            s,c,_=await self.make();c.emit(packet)
            with self.assertRaises(ValueError):await s.verify()
            self.assertEqual(s.confirmed_total,0)
            await s.close()
    async def test_disconnect_does_not_reconnect_or_claim_cleanup(self):
        s,c,rows=await self.make();c.is_connected=False
        with self.assertRaises(ConnectionError):await s.verify()
        with self.assertRaises(ConnectionError):await s.close()
        self.assertNotIn(DISABLE_RELIABLE,c.writes)
        self.assertTrue(any(r['event']=='cleanup_unconfirmed' for r in rows))

if __name__=='__main__':unittest.main()

class OfflineJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_job_crosses_multiple_counter_wraps_with_arbitrary_packet_boundaries(self):
        class JobClient(FakeClient):
            def __init__(self):super().__init__();self.received=bytearray();self.partial=bytearray();self.answers=bytearray();self.packets=0
            async def write_gatt_char(self,uuid,packet,response):
                if packet[3:5]!=b'\x40\x03':return await super().write_gatt_char(uuid,packet,response)
                self.writes.append(packet)
                self.assert_packet=packet
                assert int.from_bytes(packet[6:8],'little')==self.w
                payload=packet[8:-1];self.received.extend(payload);self.partial.extend(payload)
                self.w=(self.w+len(payload)) & 65535;self.packets+=1
                answer=bytearray()
                while b'\n' in self.partial:
                    i=self.partial.index(10);del self.partial[:i+1];answer.extend(b'ok\n')
                if self.packets%5==0:answer.extend(b'[MSG:temperature]\n')
                if answer:self.data(bytes(answer));self.answers.extend(answer)
        c=JobClient();s=ByteStreamSession(c,lambda *a,**kw:None);await s.start()
        # Pure simulation: these strings NEVER reach BLE or physical equipment.
        job=b'G1 X0.123 Y0.456 F1000 S100\n'*6000
        output=bytearray()
        for i in range(0,len(job),173):output.extend(await s.write(job[i:i+173]))
        self.assertEqual(c.received,job)
        self.assertEqual(output,c.answers)
        self.assertEqual(output.count(b'ok\n'),6000)
        self.assertEqual(s.wcheck,(65534+len(job)) & 65535)
        self.assertEqual(s.pending_rx,b'')
        self.assertEqual(c.partial,b'')
        await s.close()
