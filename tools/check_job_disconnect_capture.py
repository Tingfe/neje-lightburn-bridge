"""Independent audit of failed fixed-job transport; never a completed-job audit."""
import argparse
from collections import deque
import json
from pathlib import Path

from controlled_job_policy import ControlledJobPolicy
from controlled_reviewed_job import require_empty_startup
from grbl_readonly import accept_data,ENABLE_BLE_RELIABLE
from grbl_transport import encode_write
from motion_parity_probe import preflight,parse_status
from neje_protocol import (FrameDecoder,VERIFY,SYSTEM_READ_ALL,SYSTEM_READ_INFO,
                           GRBL_COMM_STATUS)
from reviewed_ten_mm import TEN_MM_JOB,wire_lines


def audit(path,*,simulated=False):
    rows=[json.loads(line) for line in Path(path).read_text().splitlines()]
    assert simulated or not any(row.get('simulated') for row in rows)
    def one(event):
        found=[row for row in rows if row['event']==event]
        assert len(found)==1,event
        return found[0]
    def index(event):return rows.index(one(event))
    requested=one('job_link_disconnect_requested')
    phase=requested.get('phase','after-write')
    assert phase in ('after-write','verified-run','partial-line')
    assert requested['is_connected'] and not requested['automatic_reconnect']
    assert not requested['remote_stop_inferred']
    assert not one('job_link_disconnect_returned')['is_connected']
    if phase=='partial-line':
        # The fault is injected on a write that ends inside a line, so the tool
        # records the partial payload instead of a complete reviewed-job write.
        partial=one('job_gatt_partial_write_returned')
        assert partial['ends_with_lf'] is False and partial['payload_bytes']>0
        assert partial['device_acceptance_verified'] is False
        assert requested['payload_ends_with_lf'] is False
        assert requested['payload_bytes']==partial['payload_bytes']
        assert index('job_gatt_partial_write_returned')<index('job_link_disconnect_requested')
    else:
        assert not one('job_gatt_write_returned')['device_acceptance_verified']
        assert index('job_gatt_write_returned')<index('job_link_disconnect_requested')
    assert index('job_link_disconnect_requested')<index('job_link_disconnect_returned')
    assert any(i>index('job_link_disconnect_requested') and row['event']=='pipeline_ble_disconnected'
               for i,row in enumerate(rows))
    assert not any(row['event'] in ('controlled_job_completed','cleanup_verified',
                                   'experiment_gatt_after_disconnect_request') for row in rows)
    assert not any(row['event'] in ('write_attempt','stream_verified','stream_write_reserved')
                   for row in rows[index('job_link_disconnect_requested')+1:])
    assert one('pipeline_tcp_connected') and one('pipeline_tcp_ready')
    ready=one('session_ready');w=ready['write_check'];r=confirmed_r=ready['read_check']
    decoder=FrameDecoder();decoded=[];frames=[];pending=bytearray();received=bytearray();verified=bytearray()
    expected_packet=None;last_status=None;writes=[]
    fixed={VERIFY,SYSTEM_READ_ALL,SYSTEM_READ_INFO,GRBL_COMM_STATUS,ENABLE_BLE_RELIABLE}
    for row in rows:
        event=row['event']
        if event=='notify':decoded.extend(decoder.feed(bytes.fromhex(row['hex'])))
        elif event=='frame':
            frames.append(bytes.fromhex(row['hex']))
            if row.get('kind')=='grbl_data':
                payload=bytes.fromhex(row['payload_hex'])
                r=accept_data(r,row['read_check'],payload)
                pending.extend(payload);received.extend(payload)
            elif row.get('kind')=='grbl_comm_status':
                assert row['device_tx_error']==row['device_rx_error']==0
                last_status=row
        elif event=='stream_write_reserved':
            assert expected_packet is None and row['before']==w
            payload=bytes.fromhex(row['hex']);expected_packet=encode_write(payload,w)
            w=(w+len(payload))&65535;assert row['after']==w
        elif event=='write_attempt':
            packet=bytes.fromhex(row['hex'])
            if packet not in fixed:
                assert packet==expected_packet and row['label']=='stream_data'
                writes.append(packet[8:-1]);expected_packet=None
        elif event=='stream_verified':
            payload=bytes.fromhex(row['hex']);delta=(row['read_check']-confirmed_r)&65535
            assert delta==len(payload) and payload==bytes(pending[:delta])
            del pending[:delta];verified.extend(payload);confirmed_r=row['read_check']
            assert row['confirmed_total']==len(verified) and row['write_check']==w
            assert (last_status['way'],last_status['write_check'],last_status['read_check'])==(2,w,confirmed_r)
    assert decoded==frames and expected_packet is None
    assert writes==[bytes.fromhex(row['hex']) for row in rows
                    if row['event'] in ('job_request_attempt','pipeline_write_attempt')]
    if phase=='partial-line':
        import reviewed_half_line as half_line
        raw_job=half_line.job()
        # ReviewedJobParser compares comment-stripped commands, so the expectation
        # must use the canonical command form; wire_lines() keeps inline comments.
        job_commands=half_line.review().commands
        job_wires=half_line.wire_lines()
        job_reserved=one('job_gatt_partial_write_returned')
        prefix=bytes.fromhex(job_reserved['payload_hex'])
        # A partial-line fault is defined by a write that stops inside a line: the
        # payload must be a proper prefix of the reviewed job that never reaches a
        # line boundary, while still carrying complete lines before the fragment.
        assert 0<len(prefix)<len(raw_job) and raw_job.startswith(prefix)
        assert not prefix.endswith(b'\n') and prefix.count(b'\n')>0
    else:
        raw_job=b''.join(wire_lines())
        job_commands=TEN_MM_JOB
        job_wires=wire_lines()
        job_reserved=one('job_gatt_write_returned')
        prefix=raw_job
        assert bytes.fromhex(job_reserved['payload_hex'])==raw_job
    source_reserved=[row for row in rows if row['event']=='stream_write_reserved' and row['hex']==prefix.hex()]
    assert len(source_reserved)==1
    source_reserved=source_reserved[0]
    last_reserved=[row for row in rows if row['event']=='stream_write_reserved'][-1]
    assert requested['attempted_counter_before']==source_reserved['before']
    assert requested['attempted_counter_after']==source_reserved['after']
    last_verified_w=[row['write_check'] for row in rows if row['event']=='stream_verified'][-1]
    if phase in ('after-write','partial-line'):
        assert writes[-1]==prefix and last_reserved==source_reserved
        assert one('stream_write_uncertain')['hex']==prefix.hex()
        assert last_verified_w==source_reserved['before']
    else:
        run=one('fault_run_verified');state,position=parse_status(run['report'])
        assert state=='Run' and 2<=position[0]<=8 and position[1:]==(400,0,0)
        assert requested['verified_run']==run['report']
        assert index('job_gatt_write_returned')<index('fault_run_verified')<index('job_link_disconnect_requested')
        assert any(row['event']=='pipeline_response_lines' and run['report'].encode()+b'\n' in bytes.fromhex(row['hex'])
                   for row in rows[:index('fault_run_verified')])
        assert bytes.fromhex(requested['trigger_packet_hex'])==GRBL_COMM_STATUS
        assert any(i>index('job_gatt_write_returned') and i<index('fault_run_verified') and
                   row['event']=='stream_verified' and row['write_check']==source_reserved['after']
                   for i,row in enumerate(rows))
        assert last_verified_w==w
    assert one('stream_stopped')['pending_bytes']==len(pending)
    attempts=[bytes.fromhex(row['hex']) for row in rows if row['event']=='job_request_attempt']
    assert attempts==[b'?',b'$$\n',b'$G\n',b'$#\n',b'?',b'$N\n']
    checks=[row for row in rows if row['event']=='job_request_verified']
    assert len(checks)==6 and all(not row['public'] for row in checks)
    internal={row['name']:row['text'] for row in checks}
    for row in checks:
        assert row['text'].encode()==b''.join(bytes.fromhex(part['hex']) for part in rows
                if part['event']=='job_response_lines' and part['name']==row['name'])
    preflight(internal['preflight_settings'],internal['preflight_modal'],
              internal['preflight_parameters'],internal['preflight_status'])
    require_empty_startup(checks[-1]['text'])
    parser=ControlledJobPolicy(job_commands);accepted=[]
    for row in rows:
        if row['event']=='pipeline_tcp_received':accepted.extend(parser.feed(bytes.fromhex(row['hex'])))
    assert accepted==[(row['kind'],bytes.fromhex(row['hex'])) for row in rows if row['event']=='pipeline_ingress']
    assert not any(kind in ('cancel','hold','resume') for kind,_ in accepted)
    submitted=[row for row in rows if row['event']=='pipeline_job_submitted']
    assert [row['index'] for row in submitted]==list(range(1,15))
    assert [bytes.fromhex(row['hex']) for row in submitted]==list(job_wires)
    ordinary=[wire for kind,wire in accepted if kind!='status']
    reserved=[row for row in rows if row['event']=='pipeline_line_reserved']
    assert [row['sequence'] for row in reserved]==list(range(1,len(reserved)+1))
    assert [bytes.fromhex(row['hex']) for row in reserved]==ordinary[:len(reserved)]
    response=b''.join(bytes.fromhex(row['hex']) for row in rows if row['event']=='pipeline_response_lines')
    preflight_response=b''.join(bytes.fromhex(row['hex']) for row in rows if row['event']=='job_response_lines')
    abandoned=one('control_generation_abandoned')
    tail=bytes.fromhex(abandoned['before']['response_tail_hex'])
    assert b'\n' not in tail and preflight_response+response+tail==verified
    assert b''.join(bytes.fromhex(row['hex']) for row in rows if row['event']=='pipeline_tcp_delivered')==response
    ledger=deque(reserved);terminals=[]
    for line in response.splitlines(keepends=True):
        if line.strip()==b'ok':
            owner=ledger.popleft();terminals.append((owner['sequence'],owner['hex'],line.hex()))
        assert not line.startswith((b'error:',b'ALARM:',b'Grbl '))
    assert terminals==[(row['sequence'],row['command_hex'],row['hex']) for row in rows
                       if row['event']=='pipeline_terminal']
    assert abandoned['local_only'] and not abandoned['remote_stop_inferred']
    assert abandoned['no_success_claimed'] and abandoned['no_replay']
    pending_before=abandoned['before']['pending']
    if phase=='partial-line':
        # The fault leaves a prefix of the reviewed job written but never confirmed:
        # the abandoned queue is exactly those lines in order, the last one truncated
        # mid-line, and the attempted bytes must add up to the partial payload length.
        assert [bytes.fromhex(p['hex']) for p in pending_before]==list(job_wires[:len(pending_before)])
        assert 0<len(pending_before)<len(job_wires)
        tail=pending_before[-1]
        assert tail['attempted_bytes']<len(bytes.fromhex(tail['hex']))
        assert not bytes.fromhex(tail['hex'])[:tail['attempted_bytes']].endswith(b'\n')
        assert sum(p['attempted_bytes'] for p in pending_before)==len(prefix)
    else:
        assert pending_before==[dict(sequence=row['sequence'],hex=row['hex'],
                   attempted_bytes=len(bytes.fromhex(row['hex']))) for row in ledger]
    assert set(abandoned['after'])=={'queued_bytes','pending_lines','occupied_bytes','pending_statuses',
                                     'queued_controls','response_tail_bytes','internal_results'}
    assert not any(abandoned['after'].values())
    stopped=one('pipeline_transmission_stopped')
    assert not stopped['job_completed'] and stopped['endpoint_unknown']
    assert stopped['job_submitted']==14 and stopped['pending_lines']==len(ledger)
    assert one('cleanup_unconfirmed') and one('pipeline_tcp_closed')['cleanup_unknown']
    server=one('pipeline_server_stopped');assert server['cleanup_unknown'] and server['failure_reason']
    assert rows[-1]['event']=='fault_experiment_capture_complete'
    assert rows[-1]['ble_connections']==1 and rows[-1]['bridge_failed']
    assert not rows[-1]['job_success_claimed'] and not rows[-1]['automatic_reconnect']
    return dict(passed=True,backend='ble',simulated=simulated,job_completed=False,
        source_commands=14,attempted_job_bytes=len(raw_job),
        raw_received_bytes=len(received),verified_received_bytes=len(verified),
        unverified_received_bytes=len(pending),verified_incomplete_line_bytes=len(tail),
        notification_tail_bytes=len(decoder.buffer),
        phase=phase,job_payload_device_counter_verified=phase=='verified-run',
        unverified_write_bytes=(w-last_verified_w)&65535,
        last_verified_write_counter=last_verified_w,
        last_verified_read_counter=confirmed_r,attempted_write_counter=w,
        discarded_pending_lines=len(ledger),remote_way_confirmed=False,remote_stop_inferred=False,
        explicit_inspection_required=True,no_synthetic_ack=True,no_replay=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('capture',type=Path)
    args=parser.parse_args();print(json.dumps(audit(args.capture),indent=2))
