"""Audit failed outbound and separately authorized successful LightBurn return."""
import json
from pathlib import Path
from grbl_readonly import ENABLE_BLE_RELIABLE,DISABLE_RELIABLE,accept_data
from grbl_transport import encode_write
from motion_parity_probe import QUERIES,MOVES,check_position
from neje_protocol import VERIFY,SYSTEM_READ_ALL,GRBL_COMM_STATUS,FrameDecoder,crc32_mpeg2
folder=Path(__file__).resolve().parents[1]/'evidence/lightburn-init-profile'
fixed={VERIFY,SYSTEM_READ_ALL,GRBL_COMM_STATUS,ENABLE_BLE_RELIABLE,DISABLE_RELIABLE}
results=[];all_moves=[]
for filename,failed in [('lightburn-console-motion-01.jsonl',True),('lightburn-console-return-01.jsonl',False)]:
 rows=[json.loads(x) for x in (folder/filename).read_text().splitlines()]
 decoder=FrameDecoder();decoded=[];frames=[];pending=None;data=bytearray();last_verified={};moves=[]
 for row in rows:
  e=row['event']
  if e=='session_ready': w,r=row['write_check'],row['read_check']
  elif e=='notify': decoded.extend(decoder.feed(bytes.fromhex(row['hex'])))
  elif e=='frame':
   packet=bytes.fromhex(row['hex']);frames.append(packet)
   if row['kind']=='grbl_data':
    assert packet[-1]==crc32_mpeg2(packet[1:-1]) & 255
    part=bytes.fromhex(row['payload_hex']);r=accept_data(r,row['read_check'],part);data.extend(part)
   if row['kind']=='grbl_comm_status': assert row['device_tx_error']==row['device_rx_error']==0
  elif e=='write_attempt' and bytes.fromhex(row['hex']) not in fixed:
   assert pending is None
   name=row['label'][len('init_'):]
   assert bytes.fromhex(row['hex'])==encode_write(QUERIES[name],w)
   w=(w+len(QUERIES[name])) & 65535
   pending=name;data.clear()
   if name in MOVES: moves.append(name);all_moves.append(name)
  elif e=='init_verified':
   assert row['name']==pending and row['text'].encode()==bytes(data)
   assert (w,r)==(row['write_check'],row['read_check'])
   last_verified[pending]=bytes(data);pending=None
  elif e=='tcp_delivered': assert bytes.fromhex(row['hex'])==last_verified[row['name']]
 assert decoded==frames
 cleanup=next(row for row in reversed(rows) if row['event']=='cleanup_verified')
 assert (cleanup['way'],cleanup['final_app'],cleanup['write_check'],cleanup['read_check'])==(0,3,w,r)
 errors=[row for row in rows if row['event']=='session_error']
 if failed:
  assert moves==['x_out'] and pending=='x_out' and len(errors)==1
  assert errors[0]['message']=='Counter mismatch; do not deliver response'
  assert bytes(data)==b'ok\n[MSG: Laser temperature:27.1C]\n'
  assert not any(row['event']=='tcp_delivered' and row['name']=='x_out' for row in rows)
 else:
  assert moves==['x_back'] and pending is None and not errors
  arrival=next(row for row in rows if row['event']=='console_motion_arrival')
  assert arrival['name']=='x_back' and arrival['position']==[0,400,0,0]
  check_position(last_verified['status'].decode(),0)
  assert last_verified['rapid_mode']==b'ok\n'
 results.append(dict(file=filename,expected_initial_failure=failed,moves=moves,cleanup=cleanup,
                     frames=len(frames),unconsumed_tail=len(decoder.buffer)))
assert all_moves==['x_out','x_back']
print(json.dumps(dict(passed=True,continuous_error_free_roundtrip=False,split_sessions_with_explicit_return_authorization=True,
                     no_duplicate_moves=True,return_response_and_temperature_verified=True,captures=results),indent=2))
