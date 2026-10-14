"""Offline raw-byte audit of the read-only BLE/TCP EOF race capture."""
import argparse
import json
from pathlib import Path
from check_byte_stream_capture import audit as byte_audit


def audit(path):
    result = byte_audit(path, allowed={'status': b'?'}, response_event='job_request_verified',
                        completion_event='readonly_eof_probe_passed')
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert result['requests'] == {'status': 2}
    assert not any(row['event'] in ('job_session_error', 'job_task_error', 'job_tcp_delivered') for row in rows)
    verified = [row for row in rows if row['event'] == 'job_request_verified']
    assert len(verified) == 2
    suppressed = [row for row in rows if row['event'] == 'job_tcp_delivery_suppressed']
    assert len(suppressed) == 1
    assert bytes.fromhex(suppressed[0]['hex']) == verified[-1]['text'].encode()
    eof = next(row for row in rows if row['event'] == 'job_tcp_eof')
    assert verified[-1]['utc'] > eof['utc']
    assert sum(row['event'] == 'job_eof_query_drain_finished' for row in rows) == 1
    closed = next(row for row in rows if row['event'] == 'job_tcp_closed')
    assert closed['discarded_unsent'] == 1 and not closed['cleanup_unknown']
    result.update(no_synthetic_ack=True, no_replay=True, inflight_query_verified_after_eof=True,
                  discarded_unsent_blank_lines=1, motion_executed=False)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.capture), indent=2))
