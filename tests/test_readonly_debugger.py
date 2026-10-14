import asyncio
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from readonly_debugger import BLEBackend, Debugger, USBBackend, validate_usb_response
from grbl_readonly import DISABLE_RELIABLE, READ_ONLY_QUERIES
from neje_protocol import VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS


class ResponseTests(unittest.TestCase):
    def test_partial_and_alarm_are_not_success(self):
        self.assertFalse(validate_usb_response('status', b'<Idle>'))
        self.assertFalse(validate_usb_response('build', b'[VER:NEJE]\nok'))
        self.assertTrue(validate_usb_response('build', b'[VER:NEJE]\nok\n'))
        self.assertFalse(validate_usb_response('status', b'<Idle>\n<Run>\n'))
        with self.assertRaises(RuntimeError):
            validate_usb_response('build', b'[VER:NEJE]\nok\nALARM:1\n')


class StateTests(unittest.IsolatedAsyncioTestCase):
    def make_debugger(self):
        logs = []
        class Backend:
            def __init__(self, target, log):
                self.target, self.log = target, log
                self.calls, self.fail_query, self.fail_close = [], False, False
            async def open(self): self.calls.append('open')
            async def query(self, name):
                self.calls.append(name)
                if self.fail_query:
                    self.log('ble_disconnected', expected=False)
                    raise ConnectionError('lost after write')
                return b'<Idle>\n'
            async def close(self):
                self.calls.append('close')
                if self.fail_close: raise ConnectionError('cleanup unknown')
            async def recover(self): self.calls.append('recover')
        debugger = Debugger(lambda event, **fields: logs.append((event, fields)),
                            {'usb': Backend, 'ble': Backend})
        return debugger, logs

    async def test_fault_requires_explicit_recovery_and_never_replays(self):
        debugger, logs = self.make_debugger()
        await debugger.command('connect ble')
        backend = debugger.backend
        backend.fail_query = backend.fail_close = True
        with self.assertRaises(ConnectionError): await debugger.command('build')
        self.assertEqual(debugger.state, 'fault')
        self.assertTrue(debugger.query_unknown)
        self.assertIsNone(debugger.last_result)
        for command in ('build', 'status', 'connect ble', 'connect usb', 'disconnect'):
            with self.assertRaises(RuntimeError): await debugger.command(command)
        self.assertEqual(backend.calls.count('build'), 1)
        await debugger.command('recover')
        self.assertEqual(debugger.state, 'disconnected')
        self.assertTrue(debugger.query_unknown)
        self.assertEqual(backend.calls[-1], 'recover')
        self.assertEqual(backend.calls.count('open'), 1)
        self.assertFalse(any(event == 'result' for event, _ in logs))

    async def test_switch_requires_close_and_invalid_commands_never_reach_backend(self):
        debugger, _ = self.make_debugger()
        await debugger.command('connect usb')
        backend = debugger.backend
        for command in ('G0', '$#', '$I', 'reset', 'status;G0', '\x18'):
            with self.assertRaises(ValueError): await debugger.command(command)
        self.assertEqual(backend.calls, ['open'])
        with self.assertRaises(RuntimeError): await debugger.command('connect ble')
        await debugger.command('status')
        await debugger.command('disconnect')
        await debugger.command('connect ble')
        self.assertEqual(debugger.kind, 'ble')
        self.assertEqual(backend.calls, ['open', 'status', 'close'])

    async def test_idle_loss_blocks_queries(self):
        debugger, _ = self.make_debugger()
        await debugger.command('connect ble')
        debugger.event('ble_disconnected', expected=False)
        with self.assertRaises(RuntimeError): await debugger.command('status')
        self.assertFalse(debugger.query_unknown)

    async def test_verified_result_not_replaced_by_failed_query(self):
        debugger, _ = self.make_debugger()
        await debugger.command('connect ble')
        await debugger.command('status')
        previous = debugger.last_result
        debugger.backend.fail_query = True
        with self.assertRaises(ConnectionError): await debugger.command('build')
        self.assertEqual(debugger.last_result, previous)
        self.assertTrue(debugger.query_unknown)

    async def test_failure_after_backend_return_does_not_deliver(self):
        debugger, logs = self.make_debugger()
        await debugger.command('connect ble')
        async def race(name):
            debugger.event('ble_disconnected', expected=False)
            return b'<Idle>\n'
        debugger.backend.query = race
        with self.assertRaises(ConnectionError): await debugger.command('status')
        self.assertFalse(any(event == 'result' for event, _ in logs))


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_client_callback_does_not_fault_new_connection(self):
        events = []
        backend = BLEBackend('test', lambda event, **fields: events.append(event))
        backend.client = types.SimpleNamespace(is_connected=True)
        backend.disconnected(types.SimpleNamespace(is_connected=False))
        self.assertEqual(events, ['ble_disconnected_stale'])
        self.assertFalse(backend.connect_lost)

    async def test_disconnect_during_connect_aborts_before_session_enable(self):
        instances = []
        class Client:
            is_connected = False
            def __init__(self, *args, **kwargs):
                self.callback = kwargs['disconnected_callback']
                instances.append(self)
            async def connect(self):
                self.callback(self)
                await asyncio.sleep(0)
                raise AssertionError('Backend retry should have been cancelled')
        backend = BLEBackend('test', lambda *args, **kwargs: None)
        with patch.dict(sys.modules, {'bleak': types.SimpleNamespace(BleakClient=Client)}):
            with self.assertRaises(ConnectionError): await backend.connect_client()
        self.assertIsNone(backend.session)
        self.assertIsNone(backend.owned)
        self.assertEqual(len(instances), 1)

    async def exercise(self, dw, dr, way=2, pending='build', mismatch=False):
        backend = BLEBackend('test', lambda *args, **kwargs: None)
        backend.owned = {'write_check': 65534, 'read_check': 65530, 'pending': pending}
        writes, connected = [], []
        class Client:
            is_connected = True
            async def start_notify(self, *args): pass
            async def stop_notify(self, *args): pass
            async def disconnect(self): self.is_connected = False
        class Session:
            def notify(self, *args): pass
            @staticmethod
            def clean(status):
                assert not status['device_tx_error'] and not status['device_rx_error']
            async def exchange(self, label, packet, kind):
                writes.append(packet)
                if packet == VERIFY: return {'model': 55}
                if packet == SYSTEM_READ_ALL: return {'current_app': 3}
                status = {'way': way if label.startswith('recovery_status') else 0,
                          'write_check': (65534 + dw) & 65535,
                          'read_check': (65530 + dr + (1 if mismatch and label == 'recovery_final_status' else 0)) & 65535,
                          'device_tx_error': 0, 'device_rx_error': 0}
                return status
            async def send(self, label, packet): writes.append(packet)
        async def connect():
            connected.append(True)
            backend.client, backend.session = Client(), Session()
        backend.connect_client = connect
        try:
            await backend.recover()
        except RuntimeError:
            return backend, writes, False
        return backend, writes, True

    async def test_pending_wraparound_cleanup_without_query_replay(self):
        backend, writes, passed = await self.exercise(3, 62)
        self.assertTrue(passed)
        self.assertIsNone(backend.owned)
        self.assertEqual(writes.count(DISABLE_RELIABLE), 1)
        self.assertTrue(all(p in (VERIFY, SYSTEM_READ_ALL, GRBL_COMM_STATUS, DISABLE_RELIABLE) for p in writes))
        self.assertFalse(backend.client.is_connected)

    async def test_foreign_counters_and_route_are_never_disabled(self):
        for args in ((4, 62), (0, 1), (3, 8193), (3, 62, 1)):
            with self.subTest(args=args):
                backend, writes, passed = await self.exercise(*args)
                self.assertFalse(passed)
                self.assertNotIn(DISABLE_RELIABLE, writes)
                self.assertIsNotNone(backend.owned)
                self.assertFalse(backend.client.is_connected)

    async def test_already_disabled_requires_no_disable(self):
        backend, writes, passed = await self.exercise(0, 0, way=0, pending=None)
        self.assertTrue(passed)
        self.assertNotIn(DISABLE_RELIABLE, writes)

    async def test_changed_counters_after_disable_retain_fault_ownership(self):
        backend, _, passed = await self.exercise(3, 62, mismatch=True)
        self.assertFalse(passed)
        self.assertIsNotNone(backend.owned)


class USBTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_write_is_not_retried(self):
        writes = []
        class Port:
            def write(self, data): writes.append(data); return 1
            def close(self): self.is_open = False
        backend = USBBackend('test', lambda *args, **kwargs: None)
        backend.port = Port()
        with self.assertRaises(RuntimeError): await backend.query('build')
        self.assertEqual(writes, [READ_ONLY_QUERIES['build']])
        await backend.recover()
        self.assertFalse(backend.port.is_open)


if __name__ == '__main__':
    unittest.main()
