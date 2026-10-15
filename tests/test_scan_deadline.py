import asyncio
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import device_setup


class ScanDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_initialization_is_bounded_and_cancelled(self):
        cancelled = asyncio.Event()
        budgets = []
        async def discover(timeout):
            try: await asyncio.Event().wait()
            finally: cancelled.set()
        wait_for = asyncio.wait_for
        async def short_test_deadline(task, timeout):
            budgets.append(timeout)
            return await wait_for(task, .01)
        fake = types.SimpleNamespace(BleakScanner=types.SimpleNamespace(discover=discover))
        with patch.dict(sys.modules, {'bleak': fake}), \
             patch.object(device_setup.asyncio, 'wait_for', short_test_deadline):
            with self.assertRaises(TimeoutError): await device_setup._discover(6)
        self.assertEqual(budgets, [14])
        self.assertTrue(cancelled.is_set())
