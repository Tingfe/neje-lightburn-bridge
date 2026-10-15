import asyncio
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import neje_app
import pipeline_tcp_bridge as tcp
from fake_ble import FakeClient
from transparent_fixture import wait_event


class OwnedArgumentsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def args(self, *extra):
        return tcp.parse_args(['--execute', '--transparent', '--allow-transparent-bridge',
                               '--allow-initialization', '--laser-power-isolated',
                               '--log', str(Path(self.folder.name)/'new.jsonl'), *extra])

    def test_unlimited_requires_a_valid_owner_and_transparent_mode(self):
        for extra in (['--seconds', '0'], ['--seconds', '0', '--owner-pid', '1']):
            with self.assertRaises(SystemExit): self.args(*extra)
        with self.assertRaises(SystemExit): tcp.parse_args(['--owner-pid', '123'])
        self.assertEqual(self.args('--seconds', '0', '--owner-pid', '123').seconds, 0)

    def test_cli_default_keeps_its_finite_limit(self):
        args = self.args()
        self.assertEqual(args.seconds, tcp.MAX_ACTIVE_SECONDS)
        self.assertIsNone(args.owner_pid)

    def test_default_gui_start_adds_current_parent_pid(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(neje_app, 'LOG_ROOT', Path(folder)), \
             patch.object(neje_app, 'port_in_use', return_value=False), \
             patch.object(neje_app.subprocess, 'Popen') as spawn:
            supervisor = neje_app.Supervisor(lambda message: None)
            supervisor.start(test=True)
            self.assertTrue(math.isinf(supervisor.deadline))
            command = spawn.call_args.args[0]
            self.assertEqual(command[command.index('--seconds')+1], '0')
            self.assertEqual(command[command.index('--owner-pid')+1], str(os.getpid()))
            supervisor.stop()
            self.assertEqual(supervisor.deadline, 0)
            self.assertIsNone(supervisor.restart_at)


class OwnedRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_loss_closes_active_tcp_and_owned_ble_route_no_replay(self):
        rows, clients, listening = [], [], asyncio.Event()
        parent = [45678]
        port = [None]
        original_server = asyncio.start_server

        class Client(FakeClient):
            def __init__(self, *args, **kwargs):
                super().__init__(); clients.append(self); self.payloads = []
            async def __aenter__(self): return self
            async def __aexit__(self, *args): self.is_connected = False
            async def write_gatt_char(self, uuid, packet, response):
                if packet[3:5] != b'\x40\x03':
                    return await super().write_gatt_char(uuid, packet, response)
                wire = packet[8:-1]
                self.writes.append(packet)
                self.payloads.append(wire)
                self.w = (self.w + len(wire)) & 65535
                self.data(b'<Idle|MPos:0,400,0,0|Bf:100,1500|FS:0,0>\n' if wire == b'?' else
                          b'[VER:1.1f.test]\nok\n')

        async def bind(handler, host, requested_port):
            server = await original_server(handler, host, 0)
            port[0] = server.sockets[0].getsockname()[1]
            listening.set()
            return server

        args = types.SimpleNamespace(device='FAKE', transparent=True, seconds=0, owner_pid=parent[0])
        with patch.object(tcp.os, 'getppid', side_effect=lambda: parent[0]), \
             patch.object(tcp.asyncio, 'start_server', side_effect=bind), \
             patch.dict(sys.modules, {'bleak': types.SimpleNamespace(BleakScanner=Mock(), BleakClient=Client)}):
            task = asyncio.create_task(tcp.run(args, lambda event, **kw: rows.append(dict(event=event, **kw))))
            await asyncio.wait_for(listening.wait(), 2)
            reader, writer = await asyncio.open_connection('127.0.0.1', port[0])
            try:
                await wait_event(rows, 'pipeline_tcp_ready')
                writer.write(b'$I\n'); await writer.drain()
                self.assertIn(b'[VER:', await asyncio.wait_for(reader.readuntil(b'ok\n'), 2))
                parent[0] = 1
                await asyncio.wait_for(task, 3)
                self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
            finally:
                writer.close(); await writer.wait_closed()
                if not task.done(): task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(clients[0].way, 0)
        self.assertFalse(clients[0].is_connected)
        self.assertEqual(clients[0].payloads.count(b'$I\n'), 1)
        stopped = [row for row in rows if row['event'] == 'pipeline_server_stopped'][-1]
        self.assertFalse(stopped['cleanup_unknown'])
        self.assertIsNone(stopped['failure_reason'])
        self.assertFalse([row for row in rows if row['event'] == 'pipeline_owner_exited'][0]['remote_stop_confirmed'])
        with self.assertRaises(OSError): await asyncio.open_connection('127.0.0.1', port[0])


class RealParentExitTests(unittest.TestCase):
    def test_actual_parent_termination_does_not_leave_a_listener(self):
        # Child uses real OS re-parenting and a real ephemeral TCP socket; no BLE client opens.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            child = root/'child.py'; parent = root/'parent.py'; events = root/'events.jsonl'
            child.write_text('''import asyncio, json, os, sys, types
sys.path.insert(0, sys.argv[1])
import pipeline_tcp_bridge as tcp
original = asyncio.start_server
async def bind(handler, host, port):
    server = await original(handler, host, 0)
    log('actual_bind', port=server.sockets[0].getsockname()[1], pid=os.getpid())
    return server
def log(event, **kw):
    with open(sys.argv[2], 'a') as stream: stream.write(json.dumps(dict(event=event, **kw))+'\\n')
asyncio.start_server = bind
args = types.SimpleNamespace(device='FAKE', transparent=True, seconds=0, owner_pid=os.getppid())
asyncio.run(tcp.run(args, log))
''')
            parent.write_text('''import subprocess, sys, time
subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]])
time.sleep(30)
''')
            process = subprocess.Popen([sys.executable, str(parent), str(child), str(Path(__file__).resolve().parents[1]/'tools'), str(events)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    if events.exists() and 'pipeline_listening' in events.read_text(): break
                    time.sleep(.03)
                self.assertTrue(events.exists() and 'pipeline_listening' in events.read_text())
                process.terminate(); process.wait(timeout=3)
                for _ in range(100):
                    if 'pipeline_server_stopped' in events.read_text(): break
                    time.sleep(.03)
                rows = [json.loads(line) for line in events.read_text().splitlines()]
                self.assertIn('pipeline_owner_exited', [row['event'] for row in rows])
                self.assertIn('pipeline_server_stopped', [row['event'] for row in rows])
                port = next(row['port'] for row in rows if row['event'] == 'actual_bind')
                import socket
                with socket.socket() as sock:
                    self.assertNotEqual(sock.connect_ex(('127.0.0.1', port)), 0)
            finally:
                if process.poll() is None: process.terminate(); process.wait(timeout=3)
