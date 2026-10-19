import asyncio
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import probe_official_start_order as probe
from grbl_readonly import DISABLE_RELIABLE, ENABLE_BLE_RELIABLE
from neje_protocol import GRBL_COMM_STATUS, SYSTEM_READ_INFO, VERIFY


class Guards(unittest.TestCase):
    def test_default_is_offline(self):
        done = subprocess.run([sys.executable, str(ROOT / 'tools/probe_official_start_order.py')],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0)
        self.assertTrue(json.loads(done.stdout)['offline_only'])

    def test_execution_requires_current_site_confirmations(self):
        done = subprocess.run([sys.executable, str(ROOT / 'tools/probe_official_start_order.py'),
                               '--execute'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)
        self.assertIn('current-site confirmations', done.stderr)


class StartOrder(unittest.IsolatedAsyncioTestCase):
    async def simulate(self, status_reply=True):
        writes = []
        events = []

        class Client:
            def __init__(self, *_args, **_kwargs):
                self.is_connected = True

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                self.is_connected = False

            async def start_notify(self, *_args):
                pass

            async def stop_notify(self, *_args):
                pass

        class Session:
            def __init__(self, client, log):
                self.client, self.log = client, log
                self.route = 0

            def notify(self, *_args):
                pass

            async def send(self, label, packet):
                writes.append(packet)
                if packet == ENABLE_BLE_RELIABLE:
                    self.route = 2
                elif packet == DISABLE_RELIABLE:
                    self.route = 0

            async def exchange(self, label, packet, kind):
                writes.append(packet)
                if packet == VERIFY:
                    return {'model': 55}
                if packet == SYSTEM_READ_INFO:
                    return {'current_app': 3}
                if packet == GRBL_COMM_STATUS:
                    if not status_reply and self.route == 2:
                        raise TimeoutError('Missing grbl_comm_status')
                    return {'way': self.route, 'write_check': 0, 'read_check': 0,
                            'device_tx_error': 0, 'device_rx_error': 0}
                raise AssertionError('Unexpected packet')

            @staticmethod
            def clean(status):
                assert status['device_tx_error'] == status['device_rx_error'] == 0

        original_sleep = asyncio.sleep
        with patch.dict(sys.modules, {'bleak': types.SimpleNamespace(BleakClient=Client)}), \
             patch.object(probe, 'ReliableSession', Session), \
             patch.object(probe.asyncio, 'sleep', new=lambda _time: original_sleep(0)):
            try:
                await probe.run('test', lambda event, **fields:
                                events.append({'event': event, **fields}))
                error = None
            except Exception as exc:
                error = exc
        return writes, events, error

    async def test_official_order_and_verified_cleanup(self):
        writes, events, error = await self.simulate()
        self.assertIsNone(error)
        self.assertEqual(writes, [VERIFY, SYSTEM_READ_INFO, ENABLE_BLE_RELIABLE,
                                  GRBL_COMM_STATUS, DISABLE_RELIABLE,
                                  GRBL_COMM_STATUS, SYSTEM_READ_INFO])
        self.assertTrue(any(row['event'] == 'official_order_cleanup_verified' for row in events))

    async def test_missing_status_still_attempts_one_disable(self):
        writes, events, error = await self.simulate(status_reply=False)
        self.assertIsInstance(error, TimeoutError)
        self.assertEqual(writes, [VERIFY, SYSTEM_READ_INFO, ENABLE_BLE_RELIABLE,
                                  GRBL_COMM_STATUS, DISABLE_RELIABLE,
                                  GRBL_COMM_STATUS, SYSTEM_READ_INFO])
        self.assertTrue(any(row['event'] == 'official_order_cleanup_verified' for row in events))


if __name__ == '__main__':
    unittest.main()
