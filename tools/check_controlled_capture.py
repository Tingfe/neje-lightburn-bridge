"""Independent offline audit of successful pause/resume/idle-reset review A.

Rebuilds source order, real terminal ownership and private/public projection.
An alarm, partial/faulted session or missing reset proof does not pass this audit.
Those captures remain evidence of an incomplete experiment, never success.
"""
import argparse
from collections import deque
import json
from pathlib import Path

from check_byte_stream_capture import audit as byte_audit
from controlled_job_policy import ControlledJobPolicy
from controlled_reviewed_job import require_empty_startup, ControlledReviewedJob
from motion_parity_probe import preflight, require_ok, parse_status
from reviewed_ten_mm import TEN_MM_JOB, wire_lines
from reviewed_job_file import PROFILES


def audit_completed_eof_discard(rows, stopped, completed, reserved, private):
    """Allow only unsent post-completion status/whitespace, with proved disposal."""
    stop_index = rows.index(stopped)
    eof = [i for i,row in enumerate(rows) if row['event']=='pipeline_tcp_eof']
    assert len(eof)==1 and completed < eof[0] < stop_index
    assert not any(row['event'] in ('job_request_attempt','pipeline_write_attempt')
                   for row in rows[eof[0]+1:])
    ordinary = [(i,bytes.fromhex(row['hex'])) for i,row in enumerate(rows)
                if row['event']=='pipeline_ingress' and
                row['kind'] not in ('hold','resume','cancel','status')]
    public_count = sum(row['sequence'] not in private for row in reserved)
    unsent = ordinary[public_count:]
    assert all(completed<i<eof[0] and not wire.strip() for i,wire in unsent)
    assert sum(len(wire) for _,wire in unsent)==stopped['staged_bytes']
    writes = [i for i,row in enumerate(rows) if row['event']=='pipeline_write_attempt']
    queued_queries = [row for i,row in enumerate(rows) if i>writes[-1] and
                      row['event']=='pipeline_ingress' and row['kind']=='status']
    assert len(queued_queries)==stopped['queued_controls']
    assert all(bytes.fromhex(row['hex'])==b'?' for row in queued_queries)
    assert all(completed<i<eof[0] for i,row in enumerate(rows) if i>writes[-1] and
               row['event']=='pipeline_ingress' and row['kind']=='status')
    discarded = [row for row in rows[stop_index+1:]
                 if row['event']=='controlled_job_staging_discarded']
    assert len(discarded)==1 and not discarded[0]['endpoint_unknown']
    assert discarded[0]['staged_bytes']==stopped['staged_bytes']
    assert discarded[0]['staged_lines']==len(unsent)
    abandoned = [row for row in rows[stop_index+1:]
                 if row['event']=='control_generation_abandoned']
    assert len(abandoned)==1
    abandoned = abandoned[0]
    assert abandoned['reason']=='session_closed' and abandoned['local_only']
    assert abandoned['no_replay'] and abandoned['no_success_claimed']
    before=abandoned['before']
    assert before['queued_controls']==stopped['queued_controls']
    assert not any(before[key] for key in ('queued_bytes','queued_sequences','pending',
                   'occupied_bytes','pending_statuses','unsent_urgent_hex',
                   'response_tail_hex','internal_sequence','internal_results'))
    assert set(abandoned['after'])=={'queued_bytes','pending_lines','occupied_bytes',
               'pending_statuses','queued_controls','response_tail_bytes','internal_results'}
    assert not any(abandoned['after'].values())
    # Derived from the capture rather than hardcoded. This helper is only reached
    # when at least one post-completion item was discarded (see the caller's
    # guard), but the value must not silently stay False if that ever changes.
    # discard_cause records that these discards happened after the job completed
    # and at client EOF, so they are a bounded shutdown, not an audit failure.
    return dict(discarded_unsent_status_queries=len(queued_queries),
                discarded_unsent_whitespace_bytes=stopped['staged_bytes'],
                all_received_tcp_queries_forwarded=not queued_queries,
                discard_cause='post_completion_client_eof')


def audit(path, *, backend, simulated=False, reviewed_file=None,
          expected_controls=(b'!',b'~',b'\x18')):
    if expected_controls not in ((),(b'!',b'~',b'\x18')):
        raise ValueError('Only complete normal or pause/resume/idle-reset audits supported')
    job_commands=reviewed_file.commands if reviewed_file is not None else TEN_MM_JOB
    job_wires=reviewed_file.wires if reviewed_file is not None else wire_lines()
    rows = [json.loads(line) for line in Path(path).read_text().splitlines()]
    assert simulated or not any(row.get('simulated') for row in rows)
    if reviewed_file is not None:
        ready=[row for row in rows if row['event']==
               ('pipeline_tcp_ready' if backend=='ble' else 'usb_control_ready')]
        assert len(ready)==1 and ready[0]['reviewed_source_sha256']==reviewed_file.sha256
        if reviewed_file.geometry_profile!='x-linear-zero':
            assert ready[0]['file_geometry_profile']==reviewed_file.geometry_profile
            assert tuple(map(tuple,ready[0]['reviewed_position_bounds']))==reviewed_file.position_bounds
    faults = {'error', 'usb_control_error', 'pipeline_session_error', 'pipeline_task_error',
              'pipeline_stopped', 'receive_fault', 'stream_write_uncertain',
              'pipeline_session_timeout', 'pipeline_eof_drain_timeout',
              'pipeline_job_incomplete_at_eof'}
    assert not any(row['event'] in faults for row in rows)
    attempts = [row for row in rows if row['event']=='job_request_attempt']
    verified = [row for row in rows if row['event']=='job_request_verified']
    commands = [b'?', b'$$\n', b'$G\n', b'$#\n', b'?', b'$N\n']
    assert [bytes.fromhex(row['hex']) for row in attempts] == commands
    assert [row['name'] for row in attempts] == [row['name'] for row in verified]
    assert all(not row['public'] for row in verified)
    for row in verified:
        raw = b''.join(bytes.fromhex(part['hex']) for part in rows if
                       part['event']=='job_response_lines' and part['name']==row['name'])
        assert raw == row['text'].encode()
    internal = {row['name']:row['text'] for row in verified}
    preflight(internal['preflight_settings'], internal['preflight_modal'],
              internal['preflight_parameters'], internal['preflight_status'])
    settings = dict(line[1:].split('=', 1) for line in internal['preflight_settings'].splitlines()
                    if line.startswith('$'))
    assert settings['30']=='1000' and settings['32']=='1'
    if reviewed_file is not None and reviewed_file.rapid_segments:
        rapid_settings=[line[1:].split('=',1) for line in internal['preflight_settings'].splitlines()
                        if line.startswith(('$110=','$111=','$120=','$121='))]
        assert len(rapid_settings)==4
        assert {key:float(value) for key,value in rapid_settings}=={'110':18000.,'111':18000.,'120':250.,'121':250.}
    require_empty_startup(verified[-1]['text'])
    reserved = [row for row in rows if row['event']=='pipeline_line_reserved']
    private = {row['sequence']:row for row in rows if row['event']=='pipeline_internal_reserved'}
    assert [row['sequence'] for row in reserved] == list(range(1, len(reserved)+1))
    assert [(row['name'], bytes.fromhex(row['hex'])) for row in private.values()] == [
        ('modal', b'$G\n'), ('restore', b'G0\n'), ('restored_modal', b'$G\n')]
    submitted = [row for row in rows if row['event']=='pipeline_job_submitted']
    assert [row['index'] for row in submitted] == list(range(1,len(job_commands)+1))
    assert b''.join(bytes.fromhex(row['hex']) for row in submitted) == b''.join(job_wires)
    assert tuple(bytes.fromhex(row['hex']).split(b';', 1)[0].strip() for row in submitted) == job_commands
    if backend == 'ble':
        parser, accepted = ControlledJobPolicy(job_commands), []
        for row in rows:
            if row['event']=='pipeline_tcp_received':
                accepted.extend(parser.feed(bytes.fromhex(row['hex'])))
        parser.eof()
        assert accepted == [(row['kind'], bytes.fromhex(row['hex'])) for row in rows
                            if row['event']=='pipeline_ingress']
        public_source = [wire for kind, wire in accepted if kind not in ('hold','resume','cancel','status')]
        public_reserved = [bytes.fromhex(row['hex']) for row in reserved if row['sequence'] not in private]
        assert public_reserved == public_source[:len(public_reserved)]
    ledger, terminal_rows, private_data = deque(), [], {seq:bytearray() for seq in private}
    received, projected, expected_writes = bytearray(), bytearray(), []
    statuses, controls, banners = [], [], []
    query_epochs, epoch = deque(), 0
    for index, row in enumerate(rows):
        event = row['event']
        if event == 'pipeline_line_reserved': ledger.append(row)
        if event in ('job_request_attempt','pipeline_write_attempt'):
            wire = bytes.fromhex(row['hex']); expected_writes.append(wire)
            if event=='pipeline_write_attempt' and wire in (b'!',b'~',b'\x18'):
                epoch += 1
                controls.append((index, wire))
            elif event=='pipeline_write_attempt' and wire==b'?':
                query_epochs.append(epoch)
        if event == 'job_response_lines':
            data = bytes.fromhex(row['hex']); received.extend(data)
            projected.extend(b''.join(line for line in data.splitlines(keepends=True) if line.startswith(b'[MSG:')))
        elif event == 'job_async_lines':
            data = bytes.fromhex(row['hex']); received.extend(data); projected.extend(data)
        elif event == 'pipeline_response_lines':
            data = bytes.fromhex(row['hex']); received.extend(data)
            for wire in data.splitlines(keepends=True):
                line = wire.strip()
                owner = ledger[0] if ledger else None
                is_private = owner is not None and owner['sequence'] in private
                async_output = line.startswith((b'[MSG:', b'<', b'ALARM:', b'Grbl '))
                assert not line.startswith((b'error:', b'ALARM:'))
                if not is_private or async_output: projected.extend(wire)
                if is_private and not async_output: private_data[owner['sequence']].extend(wire)
                if line == b'ok':
                    assert owner is not None
                    ledger.popleft()
                    terminal_rows.append((owner['sequence'], owner['hex'], wire.hex()))
                elif line.startswith(b'<'):
                    # A powered review declares its own ceiling; without one the
                    # zero-power rule (reported spindle must be 0) is unchanged.
                    state, position = parse_status(
                        line.decode(),
                        power_ceiling=reviewed_file.summary()['power_ceiling'] if reviewed_file else None)
                    assert state in ('Idle','Run','Hold:0','Hold:1')
                    bounds=reviewed_file.position_bounds if reviewed_file else ((0,10),(400,400),(0,0),(0,0))
                    assert all(low-.001<=value<=high+.001 for value,(low,high) in zip(position,bounds))
                    assert query_epochs
                    statuses.append((index, state, position, query_epochs.popleft()))
                elif line.startswith(b'Grbl '):
                    assert not ledger
                    banners.append(index)
                    query_epochs.clear()
                    epoch += 1
    assert not ledger
    assert terminal_rows == [(row['sequence'],row['command_hex'],row['hex']) for row in rows
                              if row['event']=='pipeline_terminal']
    for seq, row in private.items():
        text = bytes(private_data[seq]).decode()
        require_ok(text)
        if row['name'] != 'restore':
            ControlledReviewedJob.require_modal(text, rapid=row['name']=='restored_modal')
    completed = [i for i,row in enumerate(rows) if row['event']=='controlled_job_completed']
    assert len(completed)==1
    private_terminals=[i for i,row in enumerate(rows) if row['event']=='pipeline_terminal'
                       and row['sequence'] in private]
    assert len(private_terminals)==3 and max(private_terminals)<completed[0]
    assert tuple(wire for _,wire in controls) == expected_controls
    if expected_controls:
        hold_write, resume_write, reset_write = [index for index,_ in controls]
        holds = [index for index,state,_,generation in statuses if state=='Hold:0' and generation==1 and hold_write < index < resume_write]
        resumes = [index for index,state,_,generation in statuses if state in ('Run','Idle') and generation==2 and resume_write < index < completed[0]]
        assert holds and resumes and completed[0] < reset_write
        assert len(banners)==1 and reset_write < banners[0]
        assert any(index>banners[0] and state=='Idle' and pos==(0,400,0,0) and generation==4
                   for index,state,pos,generation in statuses)
    else:
        assert not banners and statuses
        final_status=[row for row in statuses if max(private_terminals)<row[0]<completed[0]]
        assert final_status and final_status[-1][1:]==('Idle',(0,400,0,0),0)
        assert all(row[1:]==('Idle',(0,400,0,0),0) for row in statuses if row[0]>completed[0])
    writes = [bytes.fromhex(row['hex']) for row in rows if row['event']=='pipeline_write_attempt']
    normal = b''.join(wire for wire in writes if wire not in (b'?',b'!',b'~',b'\x18'))
    assert normal == b''.join(bytes.fromhex(row['hex']) for row in reserved)
    visible_event = ('pipeline_tcp_delivered','pipeline_tcp_delivery_suppressed') if backend=='ble' else ('usb_control_visible',)
    visible = b''.join(bytes.fromhex(row['hex']) for row in rows if row['event'] in visible_event)
    assert visible == projected
    assert visible.splitlines().count(b'ok') == len(reserved)-len(private)
    if backend == 'ble':
        actual = [bytes.fromhex(row['hex']) for row in rows if row['event']=='stream_write_reserved']
        assert actual == expected_writes
        result = byte_audit(path, allowed={wire.hex():wire for wire in actual},
                            completion_event='pipeline_server_stopped', response_bytes=bytes(received))
        assert rows[-1]['event']=='pipeline_server_stopped' and not rows[-1]['cleanup_unknown']
        assert not rows[-1].get('failure_reason')
        # The base auditor's older TCP event name is absent in this bridge.
        # This auditor checked the actual projection above; report its bytes.
        result['tcp_delivered_bytes'] = len(visible)
    else:
        actual = [bytes.fromhex(row['hex']) for row in rows if row['event'] in
                  ('usb_control_write_attempt','usb_ten_mm_write_attempt')]
        assert actual == expected_writes
        raw = b''.join(bytes.fromhex(row['hex']) for row in rows if row['event']=='usb_ten_mm_rx')
        assert raw == received
        assert rows[-1]['event']=='usb_closed' and not rows[-1]['is_open']
        result = dict(passed=True,verified_bytes=len(raw),usb_port_closed=True)
    stopped = next(row for row in rows if row['event']==
                   ('pipeline_transmission_stopped' if backend=='ble' else 'usb_control_transmission_stopped'))
    assert stopped['job_completed'] and not stopped['endpoint_unknown']
    assert not any(stopped[key] for key in ('pending_lines','pending_statuses','queued_bytes'))
    if stopped['staged_bytes'] or stopped.get('queued_controls',0):
        assert backend=='ble' and not expected_controls
        result.update(audit_completed_eof_discard(rows,stopped,completed[0],reserved,private))
    result.update(backend=backend,simulated=simulated,controller_control_sequence_verified=True,
                  complete_job_commands=len(job_commands),private_completion_commands=3,
                  reviewed_source_sha256=reviewed_file.sha256 if reviewed_file else None,
                  no_synthetic_ack=True,no_replay=True,physical_displacement_independently_measured=False)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--backend', choices=('usb','ble'), required=True)
    parser.add_argument('--reviewed-file',type=Path)
    parser.add_argument('--expected-job-sha256')
    parser.add_argument('--file-profile',choices=PROFILES,default='x-linear-zero')
    parser.add_argument('--power-ceiling',type=int,
                        help='Declared S ceiling for a powered profile; there is no default')
    parser.add_argument('--feed-ceiling',type=int,
                        help='Declared feed ceiling for a powered profile; no default')
    parser.add_argument('--normal-file-completion',action='store_true')
    args = parser.parse_args()
    if args.normal_file_completion and not args.reviewed_file:
        parser.error('Normal file audit requires --reviewed-file')
    profile=None
    if args.reviewed_file:
        if not args.expected_job_sha256:parser.error('File audit requires the pinned digest')
        from reviewed_job_file import load
        profile=load(args.reviewed_file,expected_sha256=args.expected_job_sha256,
                     profile=args.file_profile,power_ceiling=args.power_ceiling,
                     feed_ceiling=args.feed_ceiling)
    print(json.dumps(audit(args.capture,backend=args.backend,reviewed_file=profile,
        expected_controls=() if args.normal_file_completion else (b'!',b'~',b'\x18')),
        ensure_ascii=False,indent=2))
