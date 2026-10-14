"""Offline audit of actual USB and real LightBurn BLE fixed-job captures."""
import json
from pathlib import Path

from grbl_readonly import ENABLE_BLE_RELIABLE, DISABLE_RELIABLE, accept_data
from grbl_transport import encode_write
from motion_parity_probe import check_position, require_ok
from neje_protocol import VERIFY, SYSTEM_READ_ALL, SYSTEM_READ_INFO, GRBL_COMM_STATUS, FrameDecoder, crc32_mpeg2
from reviewed_job import JOB, ReviewedJobParser

ROOT = Path(__file__).resolve().parents[1] / 'evidence/reviewed-job-bridge'
INTERNAL = dict(connection_baseline=b'?', preflight_settings=b'$$\n', preflight_modal=b'$G\n',
                preflight_parameters=b'$#\n', preflight_status=b'?', completion_status=b'?',
                completion_modal=b'$G\n', restore_rapid_mode=b'G0\n', restored_status=b'?')


def load(name):
    return [json.loads(line) for line in (ROOT / name).read_text().splitlines()]


def common(rows):
    assert not any(row['event'] in ('error', 'receive_fault', 'job_session_error', 'stream_write_uncertain', 'job_task_error') for row in rows)
    assert sum(row['event'] == 'job_preflight_passed' for row in rows) == 1
    completion = [row for row in rows if row['event'] == 'job_completion_verified']
    assert len(completion) == 1 and completion[0]['position'] == [0, 400, 0, 0]
    assert completion[0]['no_replay']
    results = [row for row in rows if row['event'] == 'job_request_verified']
    restored = next(row for row in results if row['name'] == 'restored_status')
    check_position(restored['text'], 0)
    return completion[0]


def usb_audit(filename='usb-job-01.jsonl'):
    rows = load(filename)
    completion = common(rows)
    attempts = [row for row in rows if row['event'] == 'job_request_attempt']
    writes = [bytes.fromhex(row['hex']) for row in rows if row['event'] == 'usb_job_write_attempt']
    assert writes == [bytes.fromhex(row['hex']) for row in attempts]
    job = [bytes.fromhex(row['hex']).strip() for row in attempts if row['name'].startswith('usb_job_')]
    assert job == list(JOB)
    for row in attempts:
        if not row['name'].startswith('usb_job_'):
            assert bytes.fromhex(row['hex']) == INTERNAL[row['name']]
    results = [row for row in rows if row['event'] == 'job_request_verified']
    assert [row['name'] for row in results] == [row['name'] for row in attempts]
    received = b''.join(bytes.fromhex(row['hex']) for row in rows if row['event'] == 'usb_job_rx')
    assert received == ''.join(row['text'] for row in results).encode()
    for row in results:
        payload = bytes.fromhex(attempts[results.index(row)]['hex'])
        if payload != b'?':
            require_ok(row['text'])
    assert rows[-1]['event'] == 'usb_closed' and not rows[-1]['is_open']
    return dict(passed=True, job_commands=job, received_bytes=len(received), completion=completion, port_closed=True)


def ble_audit(filename='lightburn-ble-job-01.jsonl'):
    rows = load(filename)
    completion = common(rows)
    fixed = {VERIFY, SYSTEM_READ_ALL, SYSTEM_READ_INFO, GRBL_COMM_STATUS, ENABLE_BLE_RELIABLE, DISABLE_RELIABLE}
    decoder = FrameDecoder()
    decoded, frames, attempts, written = [], [], [], []
    parser = ReviewedJobParser()
    accepted, sent = [], []
    staged, verified, raw, visible, delivered, line_buffer = (bytearray() for _ in range(6))
    current = reserved = status = None
    response_lines = []
    for row in rows:
        event = row['event']
        if event == 'session_ready':
            w, r = row['write_check'], row['read_check']
            confirmed_r = r
        elif event == 'notify':
            decoded.extend(decoder.feed(bytes.fromhex(row['hex'])))
        elif event == 'frame':
            packet = bytes.fromhex(row['hex'])
            frames.append(packet)
            if row['kind'] == 'grbl_data':
                assert packet[-1] == crc32_mpeg2(packet[1:-1]) & 255
                payload = bytes.fromhex(row['payload_hex'])
                r = accept_data(r, row['read_check'], payload)
                staged.extend(payload)
                raw.extend(payload)
            elif row['kind'] == 'grbl_comm_status':
                status = row
                assert row['device_tx_error'] == row['device_rx_error'] == 0
        elif event == 'job_tcp_receive':
            accepted.extend(parser.feed(bytes.fromhex(row['hex'])))
        elif event == 'job_line_sending':
            sent.append((row['kind'], bytes.fromhex(row['hex'])))
        elif event == 'job_request_attempt':
            assert current is None
            current = row
            response_lines = []
            payload = bytes.fromhex(row['hex'])
            if not row['public']:
                assert payload == INTERNAL[row['name']]
            attempts.append(payload)
        elif event == 'stream_write_reserved':
            assert reserved is None and current is not None
            payload = bytes.fromhex(row['hex'])
            assert payload == bytes.fromhex(current['hex']) and row['before'] == w
            reserved = (payload, w)
            w = (w + len(payload)) & 65535
            assert row['after'] == w
        elif event == 'write_attempt' and bytes.fromhex(row['hex']) not in fixed:
            assert reserved is not None
            payload, old = reserved
            assert bytes.fromhex(row['hex']) == encode_write(payload, old)
            written.append(payload)
            reserved = None
        elif event == 'stream_verified':
            payload = bytes.fromhex(row['hex'])
            delta = (row['read_check'] - confirmed_r) & 65535
            assert delta == len(payload) and payload == bytes(staged[:delta])
            del staged[:delta]
            confirmed_r = row['read_check']
            assert row['write_check'] == w
            assert (status['way'], status['write_check'], status['read_check']) == (2, w, confirmed_r)
            verified.extend(payload)
            assert row['confirmed_total'] == len(verified)
            line_buffer.extend(payload)
            while b'\n' in line_buffer:
                index = line_buffer.index(10)
                line = bytes(line_buffer[:index + 1])
                del line_buffer[:index + 1]
                assert not line.startswith((b'error:', b'ALARM:', b'Grbl '))
                if current:
                    response_lines.append(line)
                if current is None or current['public'] or line.startswith(b'[MSG:'):
                    visible.extend(line)
        elif event == 'job_tcp_delivered':
            payload = bytes.fromhex(row['hex'])
            assert payload == bytes(visible[:len(payload)])
            del visible[:len(payload)]
            delivered.extend(payload)
        elif event == 'job_request_verified':
            assert current and row['name'] == current['name'] and row['public'] == current['public']
            text = b''.join(response_lines).decode('ascii')
            assert row['text'] == text
            if bytes.fromhex(current['hex']) != b'?':
                require_ok(text)
            else:
                assert sum(line.startswith('<') and line.endswith('>') for line in text.splitlines()) == 1
                assert 'ok' not in text.splitlines()
            current = None
    parser.eof()
    assert parser.complete and accepted == sent
    job = [wire.split(b';', 1)[0].strip() for kind, wire in sent if kind == 'job']
    assert job == list(JOB)
    assert decoded == frames and current is None and reserved is None
    assert attempts == written and raw == verified and not staged and not line_buffer and not visible
    cleanup = next(row for row in reversed(rows) if row['event'] == 'cleanup_verified')
    assert (cleanup['way'], cleanup['final_app'], cleanup['write_check'], cleanup['read_check']) == (0, 3, w, r)
    stopped = next(row for row in rows if row['event'] == 'job_transmission_stopped')
    assert stopped['sent_commands'] == len(JOB) and stopped['job_completed'] and not stopped['endpoint_unknown']
    closed = next(row for row in rows if row['event'] == 'job_tcp_closed')
    assert not closed['cleanup_unknown'] and closed['discarded_unsent'] == 0
    assert rows[-1]['event'] == 'job_server_stopped' and not rows[-1]['cleanup_unknown']
    return dict(passed=True, job_commands=job, job_lines=len(job), all_tcp_items=len(sent),
                verified_bytes=len(verified), tcp_delivered_bytes=len(delivered), decoded_frames=len(frames),
                unconsumed_system_tail=len(decoder.buffer), cleanup=cleanup, completion=completion,
                no_synthetic_ack=True, no_replay=True)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', choices=('01', '02'), default='01')
    args = parser.parse_args()
    usb = usb_audit(f'usb-job-{args.run}.jsonl')
    ble = ble_audit(f'lightburn-ble-job-{args.run}.jsonl')
    assert usb.pop('job_commands') == ble.pop('job_commands')
    assert usb['completion']['modal_before_restore'] == ble['completion']['modal_before_restore']
    print(json.dumps(dict(passed=True, job_commands_identical=True, final_modal_identical=True,
                         usb=usb, lightburn_ble=ble, physical_displacement_independently_measured=False), indent=2))


if __name__ == '__main__':
    main()
