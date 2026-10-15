import asyncio
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from reviewed_job import JOB
from reviewed_job_bridge import JobChannel, ReviewedJobBridge
from fake_ble import FakeClient


class ChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_split_message_survives_public_internal_boundary(self):
        delivered = []
        class Session:
            async def verify(self):
                return b''
            async def write(self, payload):
                return b'ok\n[MSG:late' if payload == b'M5\n' else b' message]\n[GC:G0 M5 S0]\nok\n'
        async def deliver(data):
            delivered.append(data)
        channel = JobChannel(Session(), lambda *args, **kwargs: None, deliver)
        await channel.request(b'M5\n', 'public', public=True)
        await channel.request(b'$G\n', 'internal')
        self.assertEqual(b''.join(delivered), b'ok\n[MSG:late message]\n')


class JobBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def run_bridge(self, scenario, bad_offset=False, fail_forward=False, status_delay=0,
                         fail_status=False):
        clients, rows = [], []
        class Client(FakeClient):
            def __init__(self, *args, **kwargs):
                super().__init__()
                self.payloads = []
                self.status_count = 0
                clients.append(self)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                self.is_connected = False
            async def write_gatt_char(self, uuid, packet, response):
                if packet[3:5] != b'\x40\x03':
                    return await super().write_gatt_char(uuid, packet, response)
                self.writes.append(packet)
                assert int.from_bytes(packet[6:8], 'little') == self.w
                payload = packet[8:-1]
                if payload == b'?':
                    self.status_count += 1
                    if self.status_count > 1:
                        await asyncio.sleep(status_delay)
                self.payloads.append(payload)
                self.w = (self.w + len(payload)) & 65535
                command = payload.strip()
                if fail_status and command == b'?' and self.status_count > 1:
                    self.is_connected = False
                    raise ConnectionError('Injected BLE disconnect during EOF query drain')
                if fail_forward and command == b'G1 X1S0F60':
                    self.is_connected = False
                    raise ConnectionError('Injected physical-backend acceptance then disconnect')
                if command == b'?':
                    self.data(b'<Idle|MPos:0,400,0,0|FS:0,0>\n')
                elif command == b'$$':
                    self.data(b'$13=0\n$30=1000\n$32=1\n$130=260\n$131=400\nok\n')
                elif command == b'$G':
                    self.data(b'[GC:G0 G54 G17 G21 G90 G94 M5 S0 F60]\nok\n')
                elif command == b'$#':
                    x = b'1' if bad_offset else b'0'
                    self.data(b'[G54:' + x + b',0,0,0]\n[G92:0,0,0,0]\nok\n')
                else:
                    self.data(b'ok\n')
                    self.data(b'[MSG:temperature]\n')
        bridge = ReviewedJobBridge('fake', lambda event, **kw: rows.append(dict(event=event, **kw)), 5)
        with patch.dict(sys.modules, {'bleak': types.SimpleNamespace(BleakClient=Client)}):
            server = await asyncio.start_server(bridge.handle, '127.0.0.1', 0)
            port = server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            try:
                await scenario(reader, writer, rows, bridge, port)
            finally:
                writer.close()
                await writer.wait_closed()
                for _ in range(200):
                    if bridge.active is None:
                        break
                    await asyncio.sleep(.01)
                if bridge.active:
                    bridge.active.cancel()
                    await asyncio.gather(bridge.active, return_exceptions=True)
                server.close()
                await server.wait_closed()
        return clients[0], rows, bridge

    async def test_complete_job_real_acknowledgements_and_no_second_connection(self):
        async def scenario(reader, writer, rows, bridge, port):
            writer.write(b'$I\nG0\n$#\n' + b'\r\n'.join(JOB) + b'\r\n')
            await writer.drain()
            visible = bytearray()
            for _ in range(3 + len(JOB)):
                visible.extend(await asyncio.wait_for(reader.readuntil(b'ok\n'), 3))
            self.assertEqual(visible.count(b'ok\n'), 3 + len(JOB))
            self.assertNotIn(b'$130=', visible)
            self.assertNotIn(b'[GC:', visible)
            for _ in range(200):
                if any(r['event'] == 'job_completion_verified' for r in rows):
                    break
                await asyncio.sleep(.01)
            self.assertTrue(any(r['event'] == 'job_completion_verified' for r in rows))
            second_r, second_w = await asyncio.open_connection('127.0.0.1', port)
            self.assertEqual(await asyncio.wait_for(second_r.read(), 1), b'')
            second_w.close()
            await second_w.wait_closed()
        client, rows, bridge = await self.run_bridge(scenario)
        job_lines = [bytes.fromhex(r['hex']).strip() for r in rows
                     if r['event'] == 'job_line_sending' and r['kind'] == 'job']
        self.assertEqual(job_lines, list(JOB))
        self.assertFalse(bridge.cleanup_unknown)
        self.assertTrue(any(r['event'] == 'cleanup_verified' for r in rows))
        stopped = next(r for r in rows if r['event'] == 'job_transmission_stopped')
        self.assertTrue(stopped['job_completed'])
        self.assertFalse(stopped['endpoint_unknown'])

    async def test_changed_offset_blocks_first_job_command(self):
        async def scenario(reader, writer, *args):
            writer.write(b'M5\n')
            await writer.drain()
            await asyncio.wait_for(reader.read(), 3)
        client, rows, bridge = await self.run_bridge(scenario, bad_offset=True)
        self.assertNotIn(b'M5\n', client.payloads)
        self.assertTrue(any(r['event'] == 'job_session_error' for r in rows))
        self.assertTrue(any(r['event'] == 'cleanup_verified' for r in rows))

    async def close_during_status(self, reader, writer, rows, bridge, port):
        writer.write(b'?\n$I\n')
        await writer.drain()
        for _ in range(200):
            if any(r['event'] == 'job_request_attempt' and r['name'] == 'tcp_status' for r in rows):
                break
            await asyncio.sleep(.01)
        self.assertTrue(any(r['event'] == 'job_request_attempt' and r['name'] == 'tcp_status' for r in rows))
        writer.write_eof()
        self.assertEqual(await asyncio.wait_for(reader.read(), 4), b'')

    async def test_clean_eof_drains_only_inflight_status_without_forwarding_or_replay(self):
        client, rows, bridge = await self.run_bridge(self.close_during_status, status_delay=.15)
        self.assertEqual(client.payloads, [b'?', b'?'])
        self.assertTrue(any(r['event'] == 'job_eof_query_drain_finished' for r in rows))
        self.assertTrue(any(r['event'] == 'job_request_verified' and r['name'] == 'tcp_status' for r in rows))
        self.assertTrue(any(r['event'] == 'job_tcp_delivery_suppressed' for r in rows))
        self.assertFalse(any(r['event'] == 'stream_write_uncertain' for r in rows))
        self.assertEqual(next(r for r in rows if r['event'] == 'job_tcp_closed')['discarded_unsent'], 2)
        self.assertFalse(bridge.cleanup_unknown)

    async def test_eof_query_drain_is_bounded_and_does_not_retry(self):
        client, rows, bridge = await self.run_bridge(self.close_during_status, status_delay=3)
        self.assertTrue(any(r['event'] == 'job_eof_query_drain_timeout' for r in rows))
        uncertain = [r for r in rows if r['event'] == 'stream_write_uncertain']
        self.assertEqual([r['hex'] for r in uncertain], ['3f'])
        self.assertNotIn(b'$I\n', client.payloads)
        self.assertFalse(any(r['event'] == 'job_eof_query_drain_finished' for r in rows))

    async def test_ble_disconnect_during_eof_drain_reports_cleanup_unknown(self):
        client, rows, bridge = await self.run_bridge(self.close_during_status,
                                                   status_delay=.15, fail_status=True)
        self.assertEqual(client.payloads, [b'?', b'?'])
        self.assertTrue(bridge.cleanup_unknown)
        self.assertFalse(any(r['event'] == 'job_eof_query_drain_finished' for r in rows))
        self.assertTrue(any(r['event'] == 'stream_write_uncertain' for r in rows))

    async def test_inflight_disconnect_never_returns_or_replays(self):
        async def scenario(reader, writer, rows, bridge, port):
            writer.write(b'\n'.join(JOB) + b'\n')
            await writer.drain()
            await asyncio.wait_for(reader.read(), 3)
            second_r, second_w = await asyncio.open_connection('127.0.0.1', port)
            self.assertEqual(await asyncio.wait_for(second_r.read(), 1), b'')
            second_w.close()
            await second_w.wait_closed()
        client, rows, bridge = await self.run_bridge(scenario, fail_forward=True)
        self.assertEqual(client.payloads.count(b'G1 X1S0F60\n'), 1)
        self.assertNotIn(b'G1 X0\n', client.payloads)
        self.assertNotIn(b'G0\n', client.payloads)
        self.assertTrue(bridge.cleanup_unknown)
        self.assertFalse(any(r['event'] == 'job_completion_verified' for r in rows))
        self.assertTrue(next(r for r in rows if r['event'] == 'job_transmission_stopped')['endpoint_unknown'])

    async def test_partial_eof_and_invalid_command_discard_queued_work(self):
        async def scenario(reader, writer, *args):
            writer.write(b'M5\n')
            await writer.drain()
            await asyncio.wait_for(reader.readuntil(b'ok\n'), 3)
            writer.write(b'G1 X2\n')
            await writer.drain()
            await asyncio.wait_for(reader.read(), 3)
        client, rows, bridge = await self.run_bridge(scenario)
        self.assertEqual(client.payloads.count(b'M5\n'), 1)
        self.assertNotIn(b'G1 X2\n', client.payloads)
        self.assertNotIn(b'G1 X0\n', client.payloads)
        self.assertFalse(any(r['event'] == 'job_completion_verified' for r in rows))
        self.assertTrue(any(r['event'] == 'cleanup_verified' for r in rows))

    async def test_eof_after_first_line_never_finishes_remaining_job(self):
        async def scenario(reader, writer, *args):
            writer.write(b'M5\n')
            await writer.drain()
            await asyncio.wait_for(reader.readuntil(b'ok\n'), 3)
            writer.write_eof()
            await asyncio.wait_for(reader.read(), 3)
        client, rows, bridge = await self.run_bridge(scenario)
        self.assertEqual(client.payloads.count(b'M5\n'), 1)
        self.assertNotIn(b'S0\n', client.payloads)
        self.assertTrue(any(r['event'] == 'job_tcp_eof' for r in rows))
        self.assertTrue(any(r['event'] == 'job_session_error' for r in rows))
        self.assertFalse(any(r['event'] == 'job_completion_verified' for r in rows))
        self.assertTrue(any(r['event'] == 'cleanup_verified' for r in rows))


if __name__ == '__main__':
    unittest.main()
