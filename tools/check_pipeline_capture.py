"""Offline audit of fixed real BLE/USB pipelined readonly query comparison."""
import argparse
import json
from pathlib import Path
from check_byte_stream_capture import audit as byte_audit
from lightburn_init_bridge import reported_idle


def common(rows):
    assert not any(row['event'] in ('error', 'pipeline_stopped', 'receive_fault', 'stream_write_uncertain') for row in rows)
    assert sum(row['event']=='readonly_pipeline_passed' for row in rows)==1
    payload=b''.join(row['text'].encode() for row in rows if row['event']=='pipeline_response_chunk')
    terminals=[row for row in rows if row['event']=='pipeline_terminal']
    assert len(terminals)==16
    assert [row['sequence'] for row in terminals]==list(range(1,17))
    assert all(row['hex']=='6f6b0a' and row['command_hex']=='24490a' for row in terminals)
    assert [row['occupied'] for row in terminals]==list(range(45,-1,-3))
    reserved=[row for row in rows if row['event']=='pipeline_line_reserved']
    assert [(row['sequence'],row['hex'],row['occupied']) for row in reserved]==[(i,'24490a',i*3) for i in range(1,17)]
    writes=[bytes.fromhex(row['hex']) for row in rows if row['event']=='pipeline_write_attempt']
    assert writes==[b'$I\n'*16,b'?']
    blocks=[]; block=[]; statuses=[]
    for line in payload.splitlines():
        assert not line.startswith((b'error:', b'ALARM:', b'Grbl '))
        if line.startswith(b'<') and line.endswith(b'>'):
            assert reported_idle(line.decode())
            statuses.append(line.decode())
        elif line.startswith(b'[MSG:'):
            continue  # Pushes are retained in raw payload; never terminal credit.
        else:
            block.append(line)
            if line==b'ok':
                assert len(block)==3 and block[0].startswith(b'[VER:') and block[1].startswith(b'[OPT:')
                blocks.append(b'\n'.join(block)+b'\n');block=[]
    assert len(blocks)==16 and not block and len(statuses)==2
    return payload,blocks,statuses


def audit_ble(path):
    result=byte_audit(path,allowed={'status':b'?', 'batch_build':b'$I\n'*16},
                      response_event='pipeline_response_chunk',completion_event='readonly_pipeline_passed')
    assert result['requests']=={'status':2,'batch_build':1}
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    payload,blocks,statuses=common(rows)
    assert len(payload)==result['verified_bytes']
    result.update(real_terminal_responses=16,no_synthetic_ack=True,no_replay=True,statuses=statuses)
    return result,blocks


def audit_usb(path):
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    payload,blocks,statuses=common(rows)
    writes=[bytes.fromhex(row['hex']) for row in rows if row['event']=='usb_pipeline_write_attempt']
    assert writes==[b'?',b'$I\n'*16,b'?']
    received=b''.join(bytes.fromhex(row['hex']) for row in rows if row['event']=='usb_pipeline_rx')
    assert received==payload
    assert rows[-1]['event']=='usb_closed' and not rows[-1]['is_open']
    return dict(passed=True,received_bytes=len(received),real_terminal_responses=16,
                port_closed=True,statuses=statuses),blocks


def audit(ble_path,usb_path):
    ble,ble_blocks=audit_ble(ble_path)
    usb,usb_blocks=audit_usb(usb_path)
    assert ble_blocks==usb_blocks
    return dict(passed=True,ble=ble,usb=usb,build_responses_byte_identical=True,
                full_status_lines_identical=False,motion_executed=False,
                physical_usb_equivalence_proven=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ble',type=Path,default=Path('evidence/grbl-pipeline/mac-readonly-01.jsonl'))
    parser.add_argument('--usb',type=Path,default=Path('evidence/grbl-pipeline/usb-readonly-01.jsonl'))
    args=parser.parse_args()
    print(json.dumps(audit(args.ble,args.usb),indent=2))
