"""Public offline NEJE GATT fixture; contains no archived capture dependency."""
import types
from neje_protocol import crc32_mpeg2,VERIFY,SYSTEM_READ_ALL,SYSTEM_READ_INFO,GRBL_COMM_STATUS
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

