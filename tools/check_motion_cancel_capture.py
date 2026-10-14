"""Offline audit of one USB/BLE motion-reset experiment, not recovery.

Independently rebuilds the real ordinary ACK ledger, reset-aborted commands,
Run evidence and new post-banner status. An unknown capture cannot pass.
"""
import argparse
from collections import deque
import json
from pathlib import Path

from controlled_reviewed_job import require_empty_startup
from controlled_job_policy import ControlledJobPolicy
from check_byte_stream_capture import audit as byte_audit
from motion_parity_probe import parse_status, preflight
from reviewed_ten_mm import wire_lines


def audit(path, *, simulated=False, backend='usb'):
    if backend not in ('usb','ble'):
        raise ValueError('Unknown motion-cancel backend')
    rows = [json.loads(line) for line in Path(path).read_text().splitlines()]
    assert simulated or not any(row.get('simulated') for row in rows)
    assert not any(row['event'] in ('usb_control_error', 'pipeline_stopped',
                                   'error', 'pipeline_session_error', 'pipeline_task_error',
                                   'pipeline_session_timeout', 'pipeline_eof_drain_timeout',
                                   'controlled_job_completed', 'pipeline_internal_reserved') for row in rows)
    attempts = [r for r in rows if r['event']=='job_request_attempt']
    verified = [r for r in rows if r['event']=='job_request_verified']
    assert [bytes.fromhex(r['hex']) for r in attempts] == [b'?',b'$$\n',b'$G\n',b'$#\n',b'?',b'$N\n']
    assert [r['name'] for r in attempts] == [r['name'] for r in verified]
    for row in verified:
        raw = b''.join(bytes.fromhex(r['hex']) for r in rows
                       if r['event']=='job_response_lines' and r['name']==row['name'])
        assert raw == row['text'].encode() and not row['public']
    texts = {r['name']:r['text'] for r in verified}
    preflight(texts['preflight_settings'],texts['preflight_modal'],
              texts['preflight_parameters'],texts['preflight_status'])
    require_empty_startup(verified[-1]['text'])
    submitted = [r for r in rows if r['event']=='pipeline_job_submitted']
    assert [r['index'] for r in submitted] == list(range(1,15))
    assert [bytes.fromhex(r['hex']) for r in submitted] == list(wire_lines())
    reserved = [r for r in rows if r['event']=='pipeline_line_reserved']
    assert [r['sequence'] for r in reserved] == list(range(1,len(reserved)+1))
    if backend=='usb':
        assert [(r['sequence'],r['hex']) for r in reserved] == [(r['sequence'],r['hex']) for r in submitted]
    else:
        parser,accepted=ControlledJobPolicy(tuple(w.split(b';',1)[0].strip() for w in wire_lines())),[]
        for r in rows:
            if r['event']=='pipeline_tcp_received':accepted.extend(parser.feed(bytes.fromhex(r['hex'])))
        parser.eof()
        assert accepted==[(r['kind'],bytes.fromhex(r['hex'])) for r in rows if r['event']=='pipeline_ingress']
        ordinary=[wire for kind,wire in accepted if kind not in ('status','cancel','hold','resume')]
        assert [bytes.fromhex(r['hex']) for r in reserved]==ordinary
        assert [(r['sequence'],r['hex']) for r in submitted]==[(r['sequence'],r['hex']) for r in reserved[-14:]]
    ledger, terminals, aborted = deque(), [], []
    expected_writes, received, visible = [], bytearray(), bytearray()
    reset_index = banner_index = final_index = None
    pending_at_reset, last_run, last_state, reset_queries = None, None, None, 0
    final_report = None
    for index,row in enumerate(rows):
        event = row['event']
        if event=='pipeline_line_reserved': ledger.append(row)
        if event in ('job_request_attempt','pipeline_write_attempt'):
            wire=bytes.fromhex(row['hex']); expected_writes.append(wire)
            if event=='pipeline_write_attempt':
                if reset_index is not None: assert wire==b'?'
                if wire==b'\x18':
                    assert reset_index is None and last_run is not None and last_state=='Run' and ledger
                    assert 2<=last_run[0]<=8
                    reset_index=index; pending_at_reset=[r['sequence'] for r in ledger]
                elif wire==b'?':
                    if banner_index is not None: reset_queries+=1
                else: assert wire not in (b'!',b'~')
        if event in ('job_response_lines','job_async_lines','pipeline_response_lines'):
            raw=bytes.fromhex(row['hex']);received.extend(raw)
            for wire in raw.splitlines(keepends=True):
                line=wire.strip()
                if event=='pipeline_response_lines' or event=='job_async_lines' or line.startswith(b'[MSG:'):
                    visible.extend(wire)
                if event!='pipeline_response_lines':continue
                assert not line.startswith(b'error:')
                if line==b'ok':
                    assert ledger and banner_index is None
                    owner=ledger.popleft();terminals.append((owner['sequence'],owner['hex']))
                elif line.startswith(b'Grbl '):
                    assert reset_index is not None and banner_index is None
                    banner_index=index;aborted=[r['sequence'] for r in ledger];ledger.clear()
                elif line.startswith(b'ALARM:'):
                    assert reset_index is not None
                elif line.startswith(b'<'):
                    state,position=parse_status(line.decode())
                    if reset_index is None:
                        last_state=state
                        assert state in ('Run','Idle')
                        assert 0<=position[0]<=10.001 and position[1:]==(400,0,0)
                        if state=='Run':last_run=position
                    elif banner_index is not None:
                        assert reset_queries and state in ('Idle','Alarm')
                        assert float(line.decode().split('|FS:')[1].split(',')[0])==0
                        final_index=index
                        final_report=(state,position)
    assert reset_index is not None and banner_index is not None and final_index is not None
    assert reset_index<banner_index<=final_index and not ledger and aborted
    actual=[bytes.fromhex(r['hex']) for r in rows if r['event'] in
            (('stream_write_reserved',) if backend=='ble' else
             ('usb_control_write_attempt','usb_ten_mm_write_attempt'))]
    assert actual==expected_writes
    normal=b''.join(bytes.fromhex(r['hex']) for r in rows if r['event']=='pipeline_write_attempt'
                    and bytes.fromhex(r['hex']) not in (b'?',b'\x18'))
    assert normal==b''.join(bytes.fromhex(r['hex']) for r in reserved)
    assert terminals==[(r['sequence'],r['command_hex']) for r in rows if r['event']=='pipeline_terminal']
    banner=next(r for r in rows if r['event']=='control_reset_banner')
    assert banner['aborted_sequences']==aborted and banner['no_success_claimed']
    visible_events=('pipeline_tcp_delivered','pipeline_tcp_delivery_suppressed') if backend=='ble' else ('usb_control_visible',)
    assert visible==b''.join(bytes.fromhex(r['hex']) for r in rows if r['event'] in visible_events)
    assert visible.splitlines().count(b'ok')==len(terminals)
    stopped=next(r for r in rows if r['event']==
                 ('pipeline_transmission_stopped' if backend=='ble' else 'usb_control_transmission_stopped'))
    assert not stopped['job_completed'] and stopped['endpoint_unknown']
    assert not any(stopped[k] for k in ('pending_lines','pending_statuses','queued_bytes','staged_bytes'))
    observed=next(r for r in rows if r['event']==
                  ('controlled_session_cancelled' if backend=='ble' else 'usb_control_cancel_observed'))
    if backend=='usb':
        assert not observed['position_trusted'] and observed['endpoint_unknown'] and observed['no_return']
        final_state,reported_position=observed['state'],observed['reported_position']
    else:
        assert stopped['control_phase']=='cancelled' and stopped['control_position_trusted'] is False
        assert observed['requires_explicit_recovery'] and observed['no_replay']
        final_state,reported_position=parse_status(observed['status'])
    assert (final_state,tuple(reported_position))==final_report
    abandoned=next(r for r in rows if r['event']=='control_generation_abandoned')
    assert not any(abandoned['after'].values()) and abandoned['local_only']
    assert not abandoned['remote_stop_inferred']
    if backend=='usb':
        assert received==b''.join(bytes.fromhex(r['hex']) for r in rows if r['event']=='usb_ten_mm_rx')
        assert rows[-1]['event']=='usb_closed' and not rows[-1]['is_open']
        result=dict(usb_port_closed=True)
    else:
        result=byte_audit(Path(path),allowed={w.hex():w for w in actual},
                          completion_event='pipeline_server_stopped',response_bytes=bytes(received))
        assert rows[-1]['event']=='pipeline_server_stopped' and not rows[-1]['cleanup_unknown']
        assert not rows[-1].get('failure_reason')
        assert any(r['event']=='pipeline_ble_disconnected' for r in rows)
        result['tcp_delivered_bytes']=len(visible)
    result.update(passed=True,simulated=simulated,backend=backend,reset_attempts=1,
                acknowledged_job_lines=sum(sequence in {r['sequence'] for r in submitted}
                                            for sequence,_ in terminals),
                acknowledged_ordinary_lines=len(terminals),pending_at_reset=pending_at_reset,
                reset_aborted_sequences=aborted,reported_run_position=list(last_run),
                final_state=final_state,reported_position=reported_position,
                position_trusted=False,job_completed=False,no_replay=True,no_auto_return=True,
                local_generation_cleared=True,verified_rx_bytes=len(received),
                physical_stop_independently_measured=False)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path)
    parser.add_argument('--backend',choices=('usb','ble'),default='usb')
    args=parser.parse_args()
    print(json.dumps(audit(args.capture,backend=args.backend),ensure_ascii=False,indent=2))
