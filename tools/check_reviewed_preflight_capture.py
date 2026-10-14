"""Offline raw byte and readonly-scope audit for the new job preflight captures."""
import json
from pathlib import Path
from check_byte_stream_capture import audit
from motion_parity_probe import preflight, parse_status

ROOT = Path(__file__).resolve().parents[1] / 'evidence/reviewed-job-bridge'
ALLOWED = dict(settings=b'$$\n', modal=b'$G\n', parameters=b'$#\n', status=b'?')


def results(rows):
    output = {row['name']: row['text'] for row in rows if row['event'] == 'job_request_verified'}
    assert set(output) == {'preflight_' + name for name in ALLOWED}
    preflight(*(output['preflight_' + name] for name in ALLOWED))
    passed = next(row for row in rows if row['event'] == 'reviewed_job_probe_passed')
    assert passed['preflight_only'] and not passed['new_motion_commands_sent']
    assert not any(row['event'] in ('error', 'job_completion_verified', 'job_session_error') for row in rows)
    return output


def main():
    ble_path = ROOT / 'mac-preflight-01.jsonl'
    ble = audit(ble_path, ALLOWED, 'job_request_verified', 'reviewed_job_probe_passed')
    assert ble['requests'] == {name: 1 for name in ALLOWED}
    ble_rows = [json.loads(line) for line in ble_path.read_text().splitlines()]
    ble_data = results(ble_rows)
    usb_rows = [json.loads(line) for line in (ROOT / 'usb-preflight-01.jsonl').read_text().splitlines()]
    usb_data = results(usb_rows)
    writes = [bytes.fromhex(row['hex']) for row in usb_rows if row['event'] == 'usb_job_write_attempt']
    assert writes == list(ALLOWED.values())
    received = b''.join(bytes.fromhex(row['hex']) for row in usb_rows if row['event'] == 'usb_job_rx')
    assert received == ''.join(usb_data.values()).encode()
    assert usb_rows[-1]['event'] == 'usb_closed' and not usb_rows[-1]['is_open']
    for name in ('settings', 'modal', 'parameters'):
        assert usb_data['preflight_' + name] == ble_data['preflight_' + name]
    assert parse_status(usb_data['preflight_status']) == parse_status(ble_data['preflight_status'])
    print(json.dumps(dict(passed=True, ble=ble, usb_received_bytes=len(received),
                         settings_modal_parameters_byte_identical=True, state_position_power_identical=True,
                         no_new_motion=True, usb_port_closed=True), indent=2))


if __name__ == '__main__':
    main()
