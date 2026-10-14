"""Offline independent audit of 10 mm runtime/preflight captures; no device IO."""
import argparse
import json
from pathlib import Path

from check_byte_stream_capture import audit as byte_audit
from check_ten_mm_host import READS
from motion_parity_probe import preflight, require_ok
from reviewed_job import ReviewedJobParser
from reviewed_ten_mm import SHA256, TEN_MM_JOB, wire_lines


def audit(path, *, backend, tcp_job=False):
    rows=[json.loads(line) for line in Path(path).read_text().splitlines()]
    assert not any(r['event'] in ('error','pipeline_session_error','pipeline_task_error',
        'pipeline_stopped','pipeline_session_timeout','pipeline_eof_drain_timeout',
        'stream_write_uncertain','receive_fault') for r in rows)
    attempts=[r for r in rows if r['event']=='job_request_attempt']
    verified=[r for r in rows if r['event']=='job_request_verified']
    assert [r['name'] for r in attempts]==[r['name'] for r in verified]
    fixed=dict(pipeline_tcp_baseline=b'?',ten_mm_usb_baseline=b'?',
        preflight_settings=b'$$\n',preflight_modal=b'$G\n',preflight_parameters=b'$#\n',
        preflight_status=b'?',completion_status=b'?',completion_modal=b'$G\n',
        restore_rapid_mode=b'G0\n',restored_status=b'?')
    assert all(bytes.fromhex(r['hex'])==fixed[r['name']] for r in attempts)
    assert all(not r['public'] for r in verified)
    internal=dict((r['name'],r['text']) for r in verified)
    for name in ('preflight_settings','preflight_modal','preflight_parameters'):
        require_ok(internal[name])
    preflight(internal['preflight_settings'],internal['preflight_modal'],
              internal['preflight_parameters'],internal['preflight_status'])
    settings=dict(line[1:].split('=',1) for line in internal['preflight_settings'].splitlines()
                  if line.startswith('$'))
    assert settings['30']=='1000' and settings['32']=='1'
    received=bytearray();visible=bytearray();expected_writes=[]
    for row in rows:
        event=row['event']
        if event in ('job_async_lines','job_response_lines','pipeline_response_lines'):
            data=bytes.fromhex(row['hex']);received.extend(data)
            if event!='job_response_lines':visible.extend(data)
            else:visible.extend(b''.join(line for line in data.splitlines(keepends=True)
                                        if line.startswith(b'[MSG:')))
        if event in ('job_request_attempt','pipeline_write_attempt'):
            expected_writes.append(bytes.fromhex(row['hex']))
    motion=tcp_job or any(r['event']=='ten_mm_host_passed' and r['motion_executed'] for r in rows)
    if tcp_job:
        assert backend=='ble'
        review=next(r for r in rows if r['event']=='pipeline_fixed_job_review')
        assert review['sha256']==SHA256
        parser=ReviewedJobParser(job=TEN_MM_JOB);accepted=[]
        for row in rows:
            if row['event']=='pipeline_tcp_received':accepted.extend(parser.feed(bytes.fromhex(row['hex'])))
        parser.eof()
        ingress=[(r['kind'],bytes.fromhex(r['hex'])) for r in rows if r['event']=='pipeline_ingress']
        assert accepted==ingress and parser.complete
        normal=[wire for kind,wire in accepted if kind!='status']
        actual_visible=b''.join(bytes.fromhex(r['hex']) for r in rows
            if r['event'] in ('pipeline_tcp_delivered','pipeline_tcp_delivery_suppressed'))
        assert actual_visible==visible
        stop=next(r for r in rows if r['event']=='pipeline_transmission_stopped')
        assert rows[-1]['event']=='pipeline_server_stopped' and not rows[-1]['cleanup_unknown']
        eof_index=next(i for i,r in enumerate(rows) if r['event']=='pipeline_tcp_eof')
        assert not any(r['event'] in ('pipeline_write_attempt','job_request_attempt')
                       for r in rows[eof_index+1:])
    elif motion:
        assert backend=='usb'
        normal=list(wire_lines())
        stop=next(r for r in rows if r['event']=='usb_ten_mm_stopped')
        assert b''.join(bytes.fromhex(r['hex']) for r in rows
            if r['event']=='ten_mm_probe_visible')==visible
    else:
        normal=[]
        assert [bytes.fromhex(r['hex']) for r in attempts]==[b'$$\n',b'$G\n',b'$#\n',b'?']
        assert next(r for r in rows if r['event']=='ten_mm_host_passed')['preflight_only']
        assert b''.join(bytes.fromhex(r['hex']) for r in rows
            if r['event']=='ten_mm_probe_visible')==visible
    if motion:
        names=[r['name'] for r in attempts]
        assert names[:5]==['pipeline_tcp_baseline' if tcp_job else 'ten_mm_usb_baseline',
            'preflight_settings','preflight_modal','preflight_parameters','preflight_status']
        assert names[-3:]==['completion_modal','restore_rapid_mode','restored_status']
        assert names[5:-3] and set(names[5:-3])=={'completion_status'}
        reserved=[r for r in rows if r['event']=='pipeline_line_reserved']
        terminals=[r for r in rows if r['event']=='pipeline_terminal']
        assert [r['sequence'] for r in reserved]==list(range(1,len(reserved)+1))
        assert [bytes.fromhex(r['hex']) for r in reserved]==normal[:len(reserved)]
        sent=b''.join(bytes.fromhex(r['hex']) for r in rows
                     if r['event']=='pipeline_write_attempt' and r['hex']!='3f')
        assert sent==b''.join(bytes.fromhex(r['hex']) for r in reserved)
        assert [(r['sequence'],r['command_hex'],r['hex']) for r in terminals]==[
            (r['sequence'],r['hex'],'6f6b0a') for r in reserved]
        submitted=[r for r in rows if r['event']=='pipeline_job_submitted']
        assert [r['index'] for r in submitted]==list(range(1,15))
        assert tuple(bytes.fromhex(r['hex']).split(b';',1)[0].strip() for r in submitted)==TEN_MM_JOB
        assert all(r['sequence'] in {x['sequence'] for x in terminals} for r in submitted)
        first_job_sequence=submitted[0]['sequence']
        assert next(i for i,r in enumerate(rows) if r['event']=='job_preflight_passed') < next(
            i for i,r in enumerate(rows) if r['event']=='pipeline_line_reserved' and r['sequence']==first_job_sequence)
        completion=next(r for r in rows if r['event']=='job_completion_verified')
        assert next(i for i,r in enumerate(rows) if r is completion)>next(
            i for i,r in enumerate(rows) if r['event']=='pipeline_terminal' and
            r['sequence']==submitted[-1]['sequence'])
        assert completion['position']==[0,400,0,0]
        require_ok(internal['completion_modal'])
        assert {'M5','S0','G21','G90','G94','G54'} <= set(
            internal['completion_modal'].split('[GC:',1)[1].split(']',1)[0].split())
        assert stop['job_completed'] and not stop['endpoint_unknown'] and stop['job_submitted']==14
        assert not stop['pending_lines']
        if tcp_job:
            assert not stop['pending_statuses']
            sent_status=sum(r['event']=='pipeline_write_attempt' and r['hex']=='3f' for r in rows)
            remaining=sum(map(len,normal[len(reserved):]))+sum(k=='status' for k,w in accepted)-sent_status
            assert stop['queued_bytes']+stop['queued_controls']+stop['staged_bytes']==remaining
    actual_writes=[bytes.fromhex(r['hex']) for r in rows if r['event']==(
        'stream_write_reserved' if backend=='ble' else 'usb_ten_mm_write_attempt')]
    assert actual_writes==expected_writes
    allowed={wire.hex():wire for wire in expected_writes}
    if not motion:assert all(wire in READS for wire in actual_writes)
    if backend=='ble':
        result=byte_audit(Path(path),allowed=allowed,
            completion_event='pipeline_server_stopped' if tcp_job else 'ten_mm_host_passed',
            response_bytes=bytes(received))
    else:
        raw=b''.join(bytes.fromhex(r['hex']) for r in rows if r['event']=='usb_ten_mm_rx')
        assert raw==received
        assert rows[-1]['event']=='usb_closed' and not rows[-1]['is_open']
        result=dict(passed=True,verified_bytes=len(raw),usb_port_closed=True)
    result.update(backend=backend,preflight_only=not motion,motion_executed=motion,
                  no_synthetic_ack=True,no_replay=True,
                  physical_displacement_independently_measured=False)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path)
    parser.add_argument('--backend',choices=('usb','ble'),required=True)
    parser.add_argument('--tcp-job',action='store_true')
    args=parser.parse_args()
    print(json.dumps(audit(args.capture,backend=args.backend,tcp_job=args.tcp_job),indent=2))
