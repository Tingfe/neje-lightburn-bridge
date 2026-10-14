"""Offline exact-wire, terminal/FIFO and raw BLE audit of initialization TCP capture."""
import argparse
from collections import Counter
import json
from pathlib import Path
from check_byte_stream_capture import audit as byte_audit
from reviewed_job import ReviewedJobParser
from control_capture_policy import ControlCapturePolicy


def audit(path):
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    assert not any(row['event'] in ('pipeline_session_error','pipeline_task_error','pipeline_stopped',
                                    'pipeline_session_timeout','pipeline_eof_drain_timeout','error') for row in rows)
    capture = next(row for row in rows if row['event']=='pipeline_listening').get(
        'control_capture_without_forwarding',False)
    parser=ControlCapturePolicy() if capture else ReviewedJobParser(job=())
    accepted=[];ingress=[];captured=[];recorded=[];chunks={};output=bytearray()
    sent_normal=bytearray();sent_status=0
    for row in rows:
        event=row['event']
        if event=='pipeline_tcp_received':
            for kind,wire in parser.feed(bytes.fromhex(row['hex'])):
                if kind=='blocked_control': captured.append(wire)
                else: accepted.append((kind,wire))
        elif event=='lightburn_control_captured':
            assert capture and row['forwarded'] is False and row['controller_success_claimed'] is False
            recorded.append(bytes.fromhex(row['hex']))
        elif event=='pipeline_ingress':ingress.append((row['kind'],bytes.fromhex(row['hex'])))
        elif event=='job_request_verified':
            assert row['name']=='pipeline_tcp_baseline' and not row['public']
            output.extend(row['text'].encode())
        elif event in ('pipeline_tcp_delivered','pipeline_tcp_delivery_suppressed'):
            output.extend(bytes.fromhex(row['hex']))
        elif event=='pipeline_write_attempt':
            wire=bytes.fromhex(row['hex']);chunks[wire.hex()]=wire
            if wire==b'?':sent_status+=1
            else:sent_normal.extend(wire)
    parser.eof()
    assert captured==recorded
    assert accepted==ingress
    normal=[wire for kind,wire in accepted if kind!='status']
    reserved=[row for row in rows if row['event']=='pipeline_line_reserved']
    assert [row['sequence'] for row in reserved]==list(range(1,len(reserved)+1))
    assert [bytes.fromhex(row['hex']) for row in reserved]==normal[:len(reserved)]
    assert sent_normal==b''.join(bytes.fromhex(row['hex']) for row in reserved)
    terminals=[row for row in rows if row['event']=='pipeline_terminal']
    assert [row['sequence'] for row in terminals]==list(range(1,len(reserved)+1))
    assert [row['command_hex'] for row in terminals]==[row['hex'] for row in reserved]
    assert all(row['hex']=='6f6b0a' for row in terminals)
    assert sent_status<=sum(kind=='status' for kind,wire in accepted)
    raw=bytes(output).splitlines()
    assert raw.count(b'ok')==len(terminals)
    assert sum(line.startswith(b'<') and line.endswith(b'>') for line in raw)==sent_status+1
    stopped=next(row for row in rows if row['event']=='pipeline_transmission_stopped')
    assert not stopped['pending_lines'] and not stopped['pending_statuses']
    assert stopped['queued_bytes']==sum(map(len,normal[len(reserved):]))
    assert stopped['queued_controls']==sum(kind=='status' for kind,wire in accepted)-sent_status
    assert rows[-1]['event']=='pipeline_server_stopped' and not rows[-1]['cleanup_unknown']
    allowed={key:wire for key,wire in chunks.items() if wire!=b'?'}
    allowed['status']=b'?'
    result=byte_audit(path,allowed=allowed,
                      completion_event='pipeline_server_stopped',response_bytes=bytes(output))
    eof=next(row for row in rows if row['event']=='pipeline_tcp_eof')
    assert not any(row['event']=='pipeline_write_attempt' and row['utc']>eof['utc'] for row in rows)
    result.update(no_synthetic_ack=True,no_replay=True,ordinary_lines=len(terminals),
        commands={wire.decode('ascii').strip() or '<blank>':count for wire,count in Counter(normal).items()},
        public_status_queries=sent_status,
        tcp_delivered_bytes=sum(len(bytes.fromhex(row['hex'])) for row in rows if row['event']=='pipeline_tcp_delivered'),
        post_eof_suppressed_bytes=sum(len(bytes.fromhex(row['hex'])) for row in rows if row['event']=='pipeline_tcp_delivery_suppressed'),
        rejected_connections=sum(row['event']=='pipeline_tcp_rejected' for row in rows),motion_executed=False,
        controls_captured_without_forwarding=[wire.hex() for wire in captured])
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path)
    args=parser.parse_args()
    print(json.dumps(audit(args.capture),indent=2))
