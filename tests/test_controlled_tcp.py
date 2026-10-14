"""Real loopback TCP and real NEJE byte/framing layer, simulated controller."""
import asyncio
from pathlib import Path
import sys
import types
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from pipeline_tcp_bridge import PipelineTCPBridge
from controlled_reviewed_job import require_empty_startup
from reviewed_ten_mm import wire_lines
from test_byte_stream import FakeClient
from test_ten_mm_runtime import Session


class Controller(Session):
    def __init__(self, *, startup=b'$N0=\n$N1=\nok\n', reset_disconnect=False,
                 hold_disconnect=False, counter_reset=False, reset_idle=False, job_length=14,
                 defer_m2=True):
        super().__init__()
        self.state = 'Idle'
        self.x = 0
        self.held_queries = 0
        self.startup = startup
        self.deferred_m2 = False
        self.reset_disconnect = reset_disconnect
        self.hold_disconnect = hold_disconnect
        self.counter_reset = counter_reset
        self.reset_idle = reset_idle
        self.expected_job_length = job_length
        self.defer_m2 = defer_m2

    async def write(self, wire):
        if wire == b'$N\n':
            self.writes.append(wire)
            return self.startup
        if wire == b'!':
            self.writes.append(wire)
            self.state = 'Hold:1'
            return b'[MSG:temperature]\n'
        if wire == b'~':
            self.writes.append(wire)
            self.state = 'Run'
            return b''
        if wire == b'\x18':
            self.writes.append(wire)
            self.deferred_m2 = False
            self.partial.clear()
            self.state = 'Idle' if self.reset_idle else 'Alarm'
            return b'Grbl 1.1f\n' if self.reset_idle else b'ALARM:3\nGrbl 1.1f\n'
        if wire == b'?':
            self.writes.append(wire)
            if self.state == 'Hold:1':
                self.held_queries += 1
                if self.held_queries > 1: self.state = 'Hold:0'
            return f'<{self.state}|MPos:{self.x},400,0,0|Bf:100,1500|FS:0,0>\n'.encode()
        reply = await super().write(wire)
        if self.defer_m2 and self.job_count == self.expected_job_length and wire.endswith(b'M2\n'):
            self.state, self.x, self.deferred_m2 = 'Run', 2, True
            return reply[:-3]
        return reply


class ControlTCPTests(unittest.IsolatedAsyncioTestCase):
    async def wait_event(self, rows, event, **fields):
        for _ in range(300):
            if any(row['event'] == event and all(row.get(k) == v for k, v in fields.items())
                   for row in rows): return
            await asyncio.sleep(.01)
        self.fail(f'Missing {event} {fields}; tail={rows[-5:]}')

    async def exercise(self, scenario, **kwargs):
        cleanup_gate = kwargs.pop('cleanup_gate', None)
        file_review = kwargs.pop('reviewed_file', None)
        bridge_clock = kwargs.pop('bridge_clock', time.monotonic)
        bridge_timeout = kwargs.pop('bridge_timeout', 6)
        bridge_instance = kwargs.pop('bridge_instance', None)
        disconnect_on_job = kwargs.pop('disconnect_on_job', False)
        unverified_prefix = kwargs.pop('unverified_prefix', b'')
        controller_class = kwargs.pop('controller_class', Controller)
        client_wrapper = kwargs.pop('client_wrapper', None)
        gatt_limit = kwargs.pop('gatt_limit', 182)
        if file_review is not None: kwargs['job_length'] = len(file_review.commands)
        controller, rows, clients = controller_class(**kwargs), [], []
        class Client(FakeClient):
            def __init__(self, *a, **kw):
                super().__init__(); clients.append(self)
                self.disconnected_callback=kw.get('disconnected_callback')
                self.services = types.SimpleNamespace(get_characteristic=lambda uuid:
                    types.SimpleNamespace(max_write_without_response_size=gatt_limit))
            async def __aenter__(self): return self
            async def __aexit__(self, *a):
                self.is_connected = False
                if self.disconnected_callback:self.disconnected_callback(self)
            async def write_gatt_char(self, uuid, packet, response):
                if packet[3:5] != b'\x40\x03':
                    from grbl_readonly import DISABLE_RELIABLE
                    if packet == DISABLE_RELIABLE and cleanup_gate is not None:
                        cleanup_gate[0].set()
                        await cleanup_gate[1].wait()
                    return await super().write_gatt_char(uuid, packet, response)
                self.writes.append(packet)
                wire = packet[8:-1]
                assert int.from_bytes(packet[6:8], 'little') == self.w
                self.w = (self.w+len(wire)) & 65535
                reply = await controller.write(wire)
                if disconnect_on_job and controller.job_count:
                    if unverified_prefix:
                        self.data(unverified_prefix)
                    self.is_connected = False
                    raise ConnectionError('Job chunk accepted then BLE link lost before counter verification')
                if (wire == b'\x18' and controller.reset_disconnect or
                        wire == b'!' and controller.hold_disconnect):
                    self.is_connected = False
                    raise ConnectionError('Control accepted then BLE link lost')
                if wire == b'\x18' and controller.counter_reset:
                    self.w = self.r = 0
                for i in range(0, len(reply), 11): self.data(reply[i:i+11])
        if client_wrapper is not None:
            Client=client_wrapper(Client,lambda event,**fields:rows.append(dict(event=event,**fields)))
        bridge = PipelineTCPBridge('fake', lambda event, **kw:
            rows.append(dict(event=event, **kw)), timeout=bridge_timeout,
            reviewed_ten_mm=file_review is None, control_review=True,
            reviewed_file=file_review, clock=bridge_clock)
        if bridge_instance is not None: bridge_instance.bridge = bridge
        with patch.dict(sys.modules, {'bleak': types.SimpleNamespace(BleakClient=Client)}):
            server = await asyncio.start_server(bridge.handle, '127.0.0.1', 0)
            reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            try:
                await scenario(reader, writer, controller, clients, rows)
            finally:
                writer.close(); await writer.wait_closed()
                await asyncio.wait_for(bridge.finished.wait(), 4)
                server.close(); await server.wait_closed()
        return controller, clients[0], rows, bridge

    async def start_job(self, writer, rows):
        await self.wait_event(rows, 'pipeline_tcp_ready')
        writer.write(b''.join(wire_lines())); await writer.drain()
        await self.wait_event(rows, 'pipeline_terminal', sequence=13)

    async def test_fixed_control_job_polls_run_without_lightburn_status_queries(self):
        async def scenario(reader, writer, controller, clients, rows):
            await self.start_job(writer, rows)
            await self.wait_event(rows, 'control_status_observed', state='Run')
            public=b''.join(bytes.fromhex(r['hex']) for r in rows
                            if r['event']=='pipeline_tcp_delivered')
            self.assertIn(b'<Run|MPos:2,400,0,0|',public)
            # The TCP client sent only the job, no '?'. Private polling supplies
            # real Run evidence while M2 is still pending; it invents no ACK.
            self.assertFalse(any(r['event']=='pipeline_ingress' and r['kind']=='status' for r in rows))
            self.assertEqual(public.count(b'ok\n'),13)
            writer.write(b'\x18');await writer.drain()
            await self.wait_event(rows,'control_state',phase='cancelled')
        controller,client,rows,bridge=await self.exercise(scenario)
        self.assertEqual(controller.writes.count(b'\x18'),1)
        self.assertFalse(bridge.cleanup_unknown)

    async def test_real_tcp_hold_resume_and_endpoint_do_not_invent_ordinary_acks(self):
        async def scenario(reader, writer, controller, client, rows):
            await self.start_job(writer, rows)
            writer.write(b'!'); await writer.drain()
            await self.wait_event(rows, 'control_state', phase='holding')
            self.assertFalse(any(row['event'] == 'controlled_job_endpoint_verified' for row in rows))
            writer.write(b'~'); await writer.drain()
            await self.wait_event(rows, 'control_state', phase='running')
            controller.state, controller.x = 'Idle', 0
            client[0].data(b'ok\n')  # Actual deferred M2 acknowledgement.
            await self.wait_event(rows, 'controlled_job_completed')
            self.assertEqual(controller.job_count, 14)
        controller, client, rows, bridge = await self.exercise(scenario)
        public = b''.join(bytes.fromhex(row['hex']) for row in rows if row['event']=='pipeline_tcp_delivered')
        self.assertEqual(public.count(b'ok\n'), 14)
        self.assertNotIn(b'$N0=', public)
        self.assertEqual(controller.writes.count(b'!'), 1)
        self.assertEqual(controller.writes.count(b'~'), 1)
        self.assertEqual(controller.writes.count(b'G0\n'), 1)  # Private ledger-owned restore.
        self.assertEqual(controller.writes.count(b'$G\n'), 3)  # Preflight, before and after restore.
        self.assertTrue(any(row['event']=='cleanup_verified' for row in rows))
        self.assertFalse(bridge.cleanup_unknown)
        stopped = next(row for row in rows if row['event']=='pipeline_transmission_stopped')
        self.assertTrue(stopped['job_completed'])

    async def test_cancel_wins_over_invalid_source_and_reports_real_alarm_before_cleanup(self):
        async def scenario(reader, writer, controller, client, rows):
            await self.start_job(writer, rows)
            writer.write(b'invalid\x00\x18G1 X999\n!~'); await writer.drain()
            output = await asyncio.wait_for(reader.read(), 3)
            self.assertIn(b'Grbl 1.1f\n', output)
            self.assertIn(b'<Alarm|', output)
        controller, client, rows, bridge = await self.exercise(scenario)
        self.assertEqual(controller.writes.count(b'\x18'), 1)
        self.assertNotIn(b'~', controller.writes)
        public = b''.join(bytes.fromhex(row['hex']) for row in rows if row['event']=='pipeline_tcp_delivered')
        self.assertEqual(public.count(b'ok\n'), 13)
        stopped = next(row for row in rows if row['event']=='pipeline_transmission_stopped')
        self.assertEqual(stopped['control_phase'], 'cancelled')
        self.assertEqual(stopped['staged_bytes'], 0)
        self.assertEqual(stopped['queued_bytes'], 0)
        self.assertEqual(stopped['pending_lines'], 0)
        self.assertTrue(stopped['endpoint_unknown'])
        self.assertFalse(stopped['job_completed'])
        self.assertGreater(stopped['discarded_source_bytes'], 0)
        self.assertFalse(bridge.cleanup_unknown)
        self.assertEqual(client.way, 0)

    async def test_nonempty_startup_prevents_control_ready_and_all_motion(self):
        async def scenario(reader, writer, controller, client, rows):
            writer.write(b''.join(wire_lines())+b'\x18'); await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        controller, client, rows, bridge = await self.exercise(scenario,
            startup=b'$N0=G1X10\n$N1=\nok\n')
        self.assertEqual(controller.job_count, 0)
        self.assertNotIn(b'\x18', controller.writes)
        self.assertFalse(any(row['event']=='pipeline_tcp_ready' for row in rows))
        self.assertFalse(bridge.cleanup_unknown)

    async def test_reset_disconnect_never_retries_or_claims_cleanup(self):
        async def scenario(reader, writer, controller, client, rows):
            await self.start_job(writer, rows)
            writer.write(b'\x18'); await writer.drain()
            await asyncio.wait_for(reader.read(), 3)
        controller, client, rows, bridge = await self.exercise(scenario, reset_disconnect=True)
        self.assertEqual(controller.writes.count(b'\x18'), 1)
        self.assertFalse(any(row['event']=='controlled_session_cancelled' for row in rows))
        self.assertTrue(bridge.cleanup_unknown)
        self.assertTrue(any(row['event']=='stream_write_uncertain' for row in rows))
        self.assertEqual(sum(row['event']=='pipeline_tcp_connected' for row in rows), 1)

    async def test_reset_counter_restart_fails_without_automatic_rebase_or_resume(self):
        async def scenario(reader, writer, controller, client, rows):
            await self.start_job(writer, rows)
            writer.write(b'\x18'); await writer.drain()
            await asyncio.wait_for(reader.read(), 3)
        controller, client, rows, bridge = await self.exercise(scenario, counter_reset=True)
        self.assertFalse(any(row['event']=='controlled_session_cancelled' for row in rows))
        self.assertEqual(controller.writes.count(b'\x18'), 1)
        self.assertNotIn(b'~', controller.writes)
        self.assertTrue(any(row['event']=='stream_stopped' for row in rows))

    async def test_stop_before_first_job_discards_staging_and_never_starts_motion(self):
        async def scenario(reader, writer, controller, client, rows):
            await self.wait_event(rows, 'pipeline_tcp_ready')
            writer.write(b''.join(wire_lines())+b'\x18'); await writer.drain()
            await asyncio.wait_for(reader.read(), 3)
        controller, client, rows, bridge = await self.exercise(scenario)
        self.assertEqual(controller.job_count, 0)
        self.assertEqual(controller.writes.count(b'\x18'), 1)
        self.assertFalse(bridge.cleanup_unknown)

    async def test_pause_then_source_then_cancel_clears_already_accepted_staging(self):
        async def scenario(reader, writer, controller, client, rows):
            await self.wait_event(rows, 'pipeline_tcp_ready')
            writer.write(b'!'); await writer.drain()
            await self.wait_event(rows, 'control_state', phase='holding')
            writer.write(b''.join(wire_lines())); await writer.drain()
            for _ in range(200):
                if sum(row['event']=='pipeline_ingress' and row['kind']=='job' for row in rows)==14:
                    break
                await asyncio.sleep(.01)
            else: self.fail('Source was not accepted during Hold')
            writer.write(b'\x18'); await writer.drain()
            await asyncio.wait_for(reader.read(), 3)
        controller, client, rows, bridge = await self.exercise(scenario)
        discarded = next(row for row in rows if row['event']=='controlled_job_staging_discarded')
        self.assertEqual(discarded['staged_lines'], 14)
        self.assertEqual(discarded['staged_bytes'], len(b''.join(wire_lines())))
        self.assertEqual(controller.job_count, 0)
        stopped = next(row for row in rows if row['event']=='pipeline_transmission_stopped')
        self.assertEqual(stopped['staged_bytes'], 0)
        self.assertFalse(bridge.cleanup_unknown)

    async def test_ble_disconnect_during_hold_latches_unknown_without_resume(self):
        async def scenario(reader, writer, controller, client, rows):
            await self.start_job(writer, rows)
            writer.write(b'!'); await writer.drain()
            await asyncio.wait_for(reader.read(), 3)
        controller, client, rows, bridge = await self.exercise(scenario, hold_disconnect=True)
        self.assertEqual(controller.writes.count(b'!'), 1)
        self.assertNotIn(b'~', controller.writes)
        self.assertNotIn(b'\x18', controller.writes)
        self.assertTrue(bridge.cleanup_unknown)
        stopped = next(row for row in rows if row['event']=='pipeline_transmission_stopped')
        self.assertTrue(stopped['endpoint_unknown'])
        self.assertEqual(stopped['pending_lines'], 1)
        self.assertFalse(stopped['job_completed'])
        abandoned = next(row for row in rows if row['event']=='control_generation_abandoned')
        self.assertEqual(len(abandoned['before']['pending']), 1)
        self.assertFalse(any(abandoned['after'].values()))
        self.assertFalse(abandoned['remote_stop_inferred'])

    async def test_late_tcp_source_cannot_refill_during_owned_route_cleanup(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def scenario(reader, writer, controller, clients, rows):
            await self.start_job(writer, rows)
            controller.state = 'Jog'  # Real unexpected status, link remains live.
            writer.write(b'?'); await writer.drain()
            await asyncio.wait_for(entered.wait(), 2)
            writer.write(b'M3 S1000\n'); await writer.drain()
            try:
                await self.wait_event(rows, 'pipeline_input_after_close_discarded')
            finally:
                release.set()
            await asyncio.wait_for(reader.read(), 3)
        controller, client, rows, bridge = await self.exercise(scenario,
                                                             cleanup_gate=(entered,release))
        stopped = next(i for i,row in enumerate(rows) if row['event']=='pipeline_transmission_stopped')
        self.assertFalse(any(row['event']=='pipeline_ingress' for row in rows[stopped:]))
        self.assertNotIn(b'M3 S1000\n', controller.writes)
        self.assertFalse(bridge.cleanup_unknown)
        self.assertTrue(any(row['event']=='cleanup_verified' for row in rows))


class StartupPolicyTests(unittest.TestCase):
    def test_empty_startup_requires_two_unique_empty_blocks(self):
        require_empty_startup('$N1=\n$N0=\nok\n')
        for text in ('$N0=\nok\n', '$N0=\n$N0=\n$N1=\nok\n',
                     '$N0=G1X10\n$N1=\nok\n', '$N0=\n$N1= \nok\n'):
            with self.assertRaises(RuntimeError): require_empty_startup(text)

    def test_controls_cannot_be_enabled_outside_reviewed_profile(self):
        with self.assertRaises(ValueError): PipelineTCPBridge('fake', print, control_review=True)


if __name__ == '__main__': unittest.main()
