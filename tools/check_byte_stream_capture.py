"""Offline raw notifications, CRC, counter snapshots and byte delivery audit."""
import argparse
import json
from pathlib import Path
from grbl_readonly import ENABLE_BLE_RELIABLE,DISABLE_RELIABLE,accept_data
from grbl_transport import encode_write
from lightburn_init_bridge import REQUESTS
from neje_protocol import FrameDecoder,VERIFY,SYSTEM_READ_ALL,SYSTEM_READ_INFO,GRBL_COMM_STATUS,describe,crc32_mpeg2

def audit(path, allowed=None, response_event='stream_query_result', completion_event='continuous_readonly_regression_passed', response_bytes=None):
 allowed=REQUESTS if allowed is None else allowed
 rows=[json.loads(x) for x in path.read_text().splitlines()]
 decoded=[];frames=[];decoder=FrameDecoder();pending=bytearray();received=bytearray();verified=bytearray();delivered=bytearray()
 fixed={VERIFY,SYSTEM_READ_ALL,SYSTEM_READ_INFO,GRBL_COMM_STATUS,ENABLE_BLE_RELIABLE,DISABLE_RELIABLE}
 writes=[];reserved=None;last_status=None;base_r=None;confirmed_r=None
 for row in rows:
  e=row['event']
  assert e not in ('error','receive_fault','stream_write_uncertain','session_error'),row
  if e=='session_ready':
   w,r=row['write_check'],row['read_check'];base_r=confirmed_r=r
  elif e=='notify':decoded.extend(decoder.feed(bytes.fromhex(row['hex'])))
  elif e=='frame':
   packet=bytes.fromhex(row['hex']);frames.append(packet)
   description=describe(packet)
   for key in ('kind','payload_hex','read_check','write_check','way','device_tx_error','device_rx_error'):
    if key in description:assert row[key]==description[key], ('frame metadata differs from raw packet',key)
   if row['kind']=='grbl_data':
    assert packet[-1]==crc32_mpeg2(packet[1:-1]) & 255
    payload=bytes.fromhex(row['payload_hex']);r=accept_data(r,row['read_check'],payload)
    pending.extend(payload);received.extend(payload)
   elif row['kind']=='grbl_comm_status':
    last_status=row
    assert row['device_tx_error']==row['device_rx_error']==0
  elif e=='stream_write_reserved':
   assert reserved is None and row['before']==w
   payload=bytes.fromhex(row['hex']);reserved=(payload,w)
   w=(w+len(payload)) & 65535
   assert row['after']==w
  elif e=='write_attempt' and bytes.fromhex(row['hex']) not in fixed:
   assert row['label']=='stream_data' and reserved is not None
   payload,old=reserved
   assert bytes.fromhex(row['hex'])==encode_write(payload,old)
   assert payload in allowed.values(),payload
   writes.append(payload);reserved=None
  elif e=='stream_verified':
   part=bytes.fromhex(row['hex']);delta=(row['read_check']-confirmed_r) & 65535
   assert delta==len(part) and bytes(pending[:delta])==part
   del pending[:delta];verified.extend(part);confirmed_r=row['read_check']
   assert row['confirmed_total']==len(verified)
   assert row['write_check']==w
   assert last_status and (last_status['way'],last_status['write_check'],last_status['read_check'])==(2,w,confirmed_r)
  elif e=='stream_tcp_delivered':delivered.extend(bytes.fromhex(row['hex']))
 assert decoded==frames and reserved is None
 assert received==verified and not pending
 cleanup=next(row for row in reversed(rows) if row['event']=='cleanup_verified')
 assert (cleanup['way'],cleanup['final_app'],cleanup['write_check'],cleanup['read_check'])==(0,3,w,r)
 if delivered:
  baseline=next(row['text'].encode() for row in rows if row['event']=='stream_idle_baseline')
  assert bytes(verified)==baseline+bytes(delivered)
  assert not any(row['event']=='tcp_blocked' for row in rows)
  assert any(row['event']=='tcp_eof' for row in rows)
 else:
  assert any(row['event']==completion_event for row in rows)
  output=response_bytes if response_bytes is not None else b''.join(row['text'].encode() for row in rows if row['event']==response_event)
  assert output==bytes(verified)
 return dict(passed=True,file=str(path),requests={name:writes.count(payload) for name,payload in allowed.items()},
             raw_bytes_received=len(received),verified_bytes=len(verified),tcp_delivered_bytes=len(delivered),
             decoded_frames=len(frames),unconsumed_notification_tail=len(decoder.buffer),cleanup=cleanup,
             max_gatt_write=max((len(bytes.fromhex(row['hex'])) for row in rows if row['event']=='write_attempt'),default=0))
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('capture',type=Path);args=p.parse_args()
 print(json.dumps(audit(args.capture),indent=2))
