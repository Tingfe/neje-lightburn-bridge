"""Offline evidence checks for debugger whitelist, delivery and explicit recovery."""
import collections
import json
from pathlib import Path

from grbl_readonly import ENABLE_BLE_RELIABLE, DISABLE_RELIABLE, READ_ONLY_QUERIES, encode_query
from neje_protocol import VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS

ROOT = Path(__file__).resolve().parents[1]
FIXED = {VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS, ENABLE_BLE_RELIABLE, DISABLE_RELIABLE}


def audit(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    queries, results = collections.Counter(), collections.Counter()
    verified = []
    for row in rows:
        if row['event'] == 'write_attempt':
            packet = bytes.fromhex(row['hex'])
            if packet not in FIXED:
                names = [name for name, payload in READ_ONLY_QUERIES.items() if packet[8:-1] == payload]
                assert len(names) == 1
                name = names[0]
                assert packet == encode_query(name, int.from_bytes(packet[6:8], 'little'))
                queries['ble_' + name] += 1
        if row['event'] == 'usb_tx_attempt':
            assert bytes.fromhex(row['hex']) == READ_ONLY_QUERIES[row['name']]
            queries['usb_' + row['name']] += 1
        if row['event'] == 'query_verified':
            verified.append(('ble', row['name'], row['text']))
        if row['event'] == 'usb_query_verified':
            verified.append(('usb', row['name'], bytes.fromhex(row['hex']).decode('ascii')))
        if row['event'] == 'frame' and row.get('kind') == 'grbl_comm_status':
            assert row['device_tx_error'] == row['device_rx_error'] == 0
    list_results = [(r['backend'], r['name'], r['text']) for r in rows if r['event'] == 'result']
    assert list_results == verified
    for backend, name, _ in list_results:
        results[backend + '_' + name] += 1
    final = rows[-1]
    assert final['event'] in ('final_state', 'test_final_state')
    assert final['state'] == 'disconnected' and final['owned_ble_route'] is None
    assert not any(r['event'] == 'command_error' for r in rows)
    cleanup = [r for r in rows if r['event'] in ('cleanup_verified', 'recovery_verified') and 'way' in r]
    assert cleanup and all(r['way'] == 0 and r.get('final_app', r.get('app')) == 3 for r in cleanup)
    recovery = next((r for r in rows if r['event'] == 'explicit_recovery_test_passed'), None)
    if recovery:
        assert recovery['no_query_replay'] and recovery['result_remains_unknown']
        assert queries == {'ble_build': 2, 'ble_status': 1}
        assert results == {'ble_build': 1, 'ble_status': 1}
        assert sum(r['event'] == 'fault_command_rejected' for r in rows) == 4
        fault_index = next(i for i, r in enumerate(rows) if r['event'] == 'fault_disconnect_requested')
        recovery_index = next(i for i, r in enumerate(rows) if r['event'] == 'explicit_recovery_test_passed')
        assert not any(r['event'] == 'result' for r in rows[fault_index:recovery_index])
        assert final['interrupted_query_result_unknown']
    else:
        assert queries == results == {'usb_build': 1, 'usb_status': 1, 'ble_build': 1, 'ble_status': 1}
    return {'file': str(path.relative_to(ROOT)), 'passed': True, 'queries_sent': dict(queries),
            'verified_results_delivered': dict(results), 'cleanup': cleanup,
            'explicit_recovery': bool(recovery), 'final': final}


if __name__ == '__main__':
    import sys
    print(json.dumps([audit(Path(arg).resolve()) for arg in sys.argv[1:]], indent=2))
