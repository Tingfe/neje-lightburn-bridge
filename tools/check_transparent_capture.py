"""Audit a completed native transparent capture against raw NEJE wire evidence.

This proves transport/accounting and cleanup, never machining acceptance. Open or
failed captures are retained but cannot receive a complete-session pass verdict.
The ingress oracle is independent of the runtime parser and does not translate jogs.
"""
import json
from pathlib import Path
from collections import deque, Counter

from check_byte_stream_capture import audit as audit_raw
from lightburn_init_bridge import reported_idle


def _concat(rows, event):
    return b''.join(bytes.fromhex(row['hex']) for row in rows if row['event'] == event)


def _one(rows, event):
    found = [row for row in rows if row['event'] == event]
    assert len(found) == 1, f'expected exactly one {event}, found {len(found)}'
    return found[0]


def audit(path, in_progress=False):
    path = Path(path)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert rows and not any(row.get('simulated') for row in rows), 'empty or simulated capture'
    mode = _one(rows, 'pipeline_transparent_mode')
    assert mode['usb_equivalent'] is True and mode['reviewed_job_required'] is False
    assert mode['whitelist'] is False
    stopped = [row for row in rows if row['event'] == 'pipeline_transmission_stopped']
    if not stopped and in_progress:
        return dict(passed=False, in_progress=True, clean_shutdown=False,
                    shutdown_verified=False, reason='open session is not complete acceptance')
    stop = _one(rows, 'pipeline_transmission_stopped')
    server = _one(rows, 'pipeline_server_stopped')
    assert not server.get('cleanup_unknown') and not server.get('failure_reason'), 'failed/unknown session'
    assert stop['no_replay'] is True and stop['staged_bytes'] == 0
    assert stop['queued_bytes'] == 0 and stop.get('pending_lines', 0) == 0
    assert stop.get('queued_controls', 0) == 0
    assert stop.get('pending_statuses', 0) == 0 and not stop.get('unsent_urgent_hex')

    # Replay only the socket framing rules, not the product implementation. A reset
    # preempts all other bytes in its read chunk and closes this input generation.
    realtime = {0x18, 0x21, 0x3f, 0x7e, *range(0x80, 0x100)}
    pending, expected, cancelled = bytearray(), [], False
    reset_count, reset_banner = 0, False
    discarded = 0
    for row in rows:
        event = row['event']
        if event == 'pipeline_local_control':
            assert row['hex'] == '18'
            cancelled = True
            reset_count += 1
            reset_banner = False
            discarded += len(pending)
            pending.clear()
        elif event == 'pipeline_tcp_received':
            chunk = bytes.fromhex(row['hex'])
            if cancelled:
                discarded += len(chunk)
            elif b'\x18' in chunk:
                discarded += len(pending) + len(chunk) - 1
                pending.clear()
                cancelled = True
                reset_count += 1
                reset_banner = False
                expected.append(b'\x18')
            else:
                for byte in chunk:
                    if byte in realtime:
                        expected.append(bytes([byte]))
                    else:
                        pending.append(byte)
                        if byte == 10:
                            expected.append(bytes(pending))
                            pending.clear()
        elif event == 'control_reset_banner':
            assert cancelled, 'reset banner without a requested reset'
            reset_banner = True
        elif event == 'pipeline_input_generation_opened':
            assert cancelled and reset_banner and row['real_reset_banner'] is True
            assert row['position_trusted'] is False and row['no_replay'] is True
            cancelled, reset_banner = False, False
        elif event == 'pipeline_tcp_eof':
            assert bytes.fromhex(row['pending_hex']) == bytes(pending)
            discarded += len(pending)
            pending.clear()
    assert not pending, 'unaccounted input tail'
    ingress = _concat(rows, 'pipeline_ingress')
    assert ingress == b''.join(expected), 'ingress differs from native client framing'
    # Legacy logs have no source-discard field unless the controlled profile was on.
    if stop.get('control_phase') is not None:
        assert stop['discarded_source_bytes'] == discarded

    written = _concat(rows, 'pipeline_write_attempt')
    ordinary_lines = [wire for wire in expected if len(wire) != 1 or wire[0] not in realtime]
    source_ordinary = list(ordinary_lines)
    continued_resets = any(row['event'] == 'pipeline_input_generation_opened' for row in rows)
    # Native cancellation/alarm may invalidate host-owned lines, without inventing
    # the ok's they never earned. Record each discarded full line exactly once.
    for row in rows:
        dropped = row.get('lines', []) if row['event'] == 'pipeline_jog_unsent_discarded' else \
            row.get('discarded_lines', []) if row['event'] == 'pipeline_alarm_ledger_invalidated' else []
        for line in dropped:
            wire = bytes.fromhex(line['hex'])
            if row['event'] == 'pipeline_jog_unsent_discarded':
                assert wire.lstrip().upper().startswith(b'$J=')
            assert wire in ordinary_lines, 'discarded line was never received'
            ordinary_lines.remove(wire)
    ordinary_in = b''.join(ordinary_lines)
    ordinary_written = bytes(byte for byte in written if byte not in realtime)
    if continued_resets:
        assert written.count(b'\x18') == reset_count, 'reset dropped/duplicated'
        completed = [row for row in rows if row['event'] == 'pipeline_native_reset_completed']
        assert len(completed) == reset_count and not cancelled, 'reset not confirmed'
        for row in completed:
            assert row['connection_retained'] and row['no_replay']
            assert row['position_trusted'] is False and row['requires_explicit_recovery']
        assert _concat(rows, 'pipeline_line_enqueued') == b''.join(source_ordinary), 'new-generation source changed'
    elif cancelled:
        assert ordinary_in.startswith(ordinary_written), 'reset duplicated/reordered ordinary bytes'
        assert written.count(b'\x18') == 1, 'reset not actually attempted exactly once'
        assert _one(rows, 'controlled_session_cancelled')['requires_explicit_recovery'] is True
        assert any(row['event'] == 'control_reset_banner' for row in rows)
    else:
        assert ordinary_in == ordinary_written, 'ordinary client bytes dropped/duplicated/changed'

    # Rebuild terminal ownership from reservation and write events, rather than
    # accepting the pipeline's own terminal ledger as the oracle.
    pending_lines, terminal_owners = deque(), deque()
    enqueued, attempted, discarded_sequences, aborted_sequences = {}, {}, set(), set()
    for row in rows:
        if row['event'] == 'pipeline_line_enqueued':
            assert row['sequence'] not in enqueued, 'duplicate line sequence'
            enqueued[row['sequence']] = bytes.fromhex(row['hex'])
        elif row['event'] == 'pipeline_line_reserved':
            sequence = row['sequence']
            assert sequence not in attempted and enqueued.get(sequence) == bytes.fromhex(row['hex'])
            line = dict(sequence=sequence, wire=bytes.fromhex(row['hex']), attempted=0)
            attempted[sequence] = line
            pending_lines.append(line)
        elif row['event'] == 'pipeline_write_attempt':
            payload = bytes(byte for byte in bytes.fromhex(row['hex']) if byte not in realtime)
            for line in pending_lines:
                amount = min(len(payload), len(line['wire'])-line['attempted'])
                assert payload[:amount] == line['wire'][line['attempted']:line['attempted']+amount]
                line['attempted'] += amount
                payload = payload[amount:]
                if not payload: break
            assert not payload, 'ordinary write has no reserved owner'
        elif row['event'] == 'pipeline_response_lines':
            for wire in bytes.fromhex(row['hex']).splitlines(keepends=True):
                terminal = wire.strip()
                if terminal == b'ok' or terminal.startswith(b'error:'):
                    assert pending_lines and pending_lines[0]['attempted'] == len(pending_lines[0]['wire'])
                    owner = pending_lines.popleft()
                    terminal_owners.append((owner['sequence'], owner['wire'], wire))
                elif terminal.startswith((b'Grbl ', b'ALARM:')):
                    pending_lines.clear()
        elif row['event'] == 'pipeline_terminal':
            assert terminal_owners, 'terminal ledger has no real device response'
            assert terminal_owners.popleft() == (row['sequence'], bytes.fromhex(row['command_hex']), bytes.fromhex(row['hex']))
        elif row['event'] in ('pipeline_jog_unsent_discarded', 'pipeline_alarm_ledger_invalidated', 'control_unsent_discarded'):
            dropped = row.get('discarded_lines', []) if row['event'] == 'pipeline_alarm_ledger_invalidated' else row.get('lines', [])
            for line in dropped:
                sequence = line['sequence']
                assert enqueued.get(sequence) == bytes.fromhex(line['hex']), 'discard not sourced from client'
                assert sequence not in discarded_sequences and sequence not in attempted
                discarded_sequences.add(sequence)
        elif row['event'] == 'control_reset_banner':
            aborted_sequences.update(row['aborted_sequences'])
    assert not terminal_owners and not pending_lines, 'unfinished/misattributed terminal ledger'
    if continued_resets:
        for sequence, wire in enqueued.items():
            amount = attempted[sequence]['attempted'] if sequence in attempted else 0
            assert amount == len(wire) or sequence in discarded_sequences or sequence in aborted_sequences, 'unaccounted job byte loss'
        assert aborted_sequences <= attempted.keys(), 'aborted line was never reserved'
    requested_controls = Counter(byte for byte in ingress+_concat(rows, 'pipeline_local_control') if byte in realtime and byte != 0x3f)
    actual_controls = Counter(byte for byte in written if byte in realtime and byte != 0x3f)
    assert all(actual_controls[byte] <= requested_controls[byte] for byte in actual_controls), 'fabricated/replayed realtime control'
    if not reset_count:
        assert actual_controls == requested_controls, 'native realtime controls dropped/duplicated'

    # The raw auditor checks notify -> frame/CRC -> monotonic modulo counters ->
    # reservation/encoded GATT packet -> device counter snapshot -> confirmed bytes.
    responses = b''.join(bytes.fromhex(row['hex']) for row in rows
                         if row['event'] in ('job_response_lines', 'job_async_lines', 'pipeline_response_lines'))
    allowed = {str(i): bytes.fromhex(row['hex']) for i, row in enumerate(rows)
               if row['event'] in ('job_request_attempt', 'pipeline_write_attempt')}
    raw = audit_raw(path, allowed=allowed, response_bytes=responses,
                    completion_event='controlled_cancel_responses_drained' if cancelled
                    else 'pipeline_eof_responses_drained')
    assert raw['unconsumed_notification_tail'] == 0, 'incomplete raw notification tail'

    # Every public byte must be delivered or explicitly suppressed after peer EOF.
    # Baseline query replies stay private, except real asynchronous MSG lines.
    public = bytearray()
    visible = bytearray()
    for row in rows:
        if row['event'] in ('pipeline_response_lines', 'job_async_lines'):
            public.extend(bytes.fromhex(row['hex']))
        elif row['event'] == 'job_response_lines':
            data = bytes.fromhex(row['hex'])
            public.extend(data if row.get('public') else
                          b''.join(line for line in data.splitlines(keepends=True) if line.startswith(b'[MSG:')))
        elif row['event'] in ('pipeline_tcp_delivered', 'pipeline_tcp_delivery_suppressed'):
            if row['event'] == 'pipeline_tcp_delivery_suppressed':
                assert row['reason'] == 'peer_eof' and any(r['event'] == 'pipeline_tcp_eof' for r in rows)
            visible.extend(bytes.fromhex(row['hex']))
    assert public == visible, 'public confirmed bytes missing, fabricated or reordered'
    # A clean route alone does not prove buffered motion finished. A private
    # post-EOF query must return a real Idle/FS:0,0 report. Reset may have made
    # coordinates untrusted, but it does not bar a new *connection* after this
    # proof; the bridge never restores coordinates or replays a job.
    last_status = next((i for i in range(len(rows)-1, -1, -1)
                        if rows[i]['event'] == 'control_status_observed'), None)
    restart_safe = False
    alarm_seen = any(row['event'] == 'pipeline_alarm_ledger_invalidated' for row in rows)
    eof_at = next((i for i, row in enumerate(rows) if row['event'] == 'pipeline_tcp_eof'), None)
    probes = [(i, row) for i, row in enumerate(rows)
              if row['event'] == 'pipeline_eof_idle_probe']
    assert len(probes) <= 1, 'multiple post-EOF idle probes'
    if probes:
        probe_at, probe = probes[0]
        assert eof_at is not None and eof_at < probe_at < rows.index(stop), 'idle probe outside EOF cleanup'
        status_lines = [line.decode('ascii').strip() for row in rows[eof_at+1:probe_at]
                        if row['event'] == 'pipeline_response_lines'
                        for line in bytes.fromhex(row['hex']).splitlines()
                        if line.startswith(b'<') and line.endswith(b'>')]
        assert status_lines and probe['status'] == status_lines[-1], 'idle probe lacks real status'
        assert probe['idle'] is reported_idle(probe['status']), 'idle probe flag mismatch'
        assert probe['no_replay'] is True and probe['position_trusted'] is False
        restart_safe = bool(not alarm_seen and last_status is not None and
                            eof_at < last_status < probe_at and
                            rows[last_status]['state'] == 'Idle' and
                            rows[last_status].get('matched_query') is True and
                            probe['idle'] and not any(
                                row['event'] == 'pipeline_line_reserved'
                                for row in rows[last_status+1:]))
    return dict(passed=True, backend='ble-native-transparent', whitelist=False,
                in_progress=False, shutdown_verified=True,
                client_bytes_in=len(_concat(rows, 'pipeline_tcp_received')),
                client_bytes_forwarded=len(ingress), client_bytes_discarded=discarded,
                device_bytes_staged=raw['raw_bytes_received'],
                bytes_delivered=len(_concat(rows, 'pipeline_tcp_delivered')),
                neje_writes=sum(row['event'] == 'stream_write_reserved' for row in rows),
                clean_shutdown=True, cleanup_way=0, no_replay=True, no_fabricated_bytes=True,
                control_reset_confirmed=bool(reset_count), reset_count=reset_count,
                connection_retained_after_reset=continued_resets,
                requires_explicit_recovery=not restart_safe,
                position_recovery_required=bool(reset_count) or alarm_seen,
                restart_safe=restart_safe,
                raw_audit=raw)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--in-progress', action='store_true')
    args = parser.parse_args()
    print(json.dumps(audit(args.capture, args.in_progress), ensure_ascii=False, indent=2))
