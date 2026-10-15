import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
import copy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from check_transparent_capture import audit
from transparent_fixture import exercise, wait_event


class TransparentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def audit_rows(self, rows):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'capture.jsonl'
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            return audit(path)

    async def test_reset_reaches_real_transport_and_preempts_same_chunk(self):
        async def scenario(reader, writer, rows, *args):
            await wait_event(rows, 'pipeline_tcp_ready')
            writer.write(b'G1 X100\n\x18G1 X200\n')
            await writer.drain()
            output = await asyncio.wait_for(reader.readuntil(b'Grbl 1.1f [reset]\n'), 3)
            self.assertIn(b'Grbl ', output)
            await wait_event(rows, 'pipeline_native_reset_completed')
        rows, client, bridge = await exercise(scenario)
        self.assertEqual(client.payloads, [b'?', b'\x18', b'?', b'?'])
        self.assertIsNone(bridge.failure_reason)
        result = self.audit_rows(rows)
        self.assertTrue(result['control_reset_confirmed'])
        self.assertFalse(result['requires_explicit_recovery'])
        self.assertTrue(result['position_recovery_required'])
        self.assertTrue(result['restart_safe'])

    async def test_local_owner_stop_uses_existing_connection(self):
        async def scenario(reader, writer, rows, bridge, clients, port):
            await wait_event(rows, 'pipeline_tcp_ready')
            self.assertTrue(bridge.request_stop())
            self.assertIn(b'Grbl ', await asyncio.wait_for(reader.readuntil(b'Grbl 1.1f [reset]\n'), 3))
            await wait_event(rows, 'pipeline_native_reset_completed')
            self.assertEqual(len(clients), 1)
        rows, client, bridge = await exercise(scenario)
        self.assertEqual(client.payloads, [b'?', b'\x18', b'?', b'?'])
        self.assertTrue(self.audit_rows(rows)['control_reset_confirmed'])

    async def test_stop_then_immediate_initialization_and_second_stop_keep_one_link(self):
        async def scenario(reader, writer, rows, bridge, clients, port):
            await wait_event(rows, 'pipeline_tcp_ready')
            for _ in range(2):
                writer.write(b'G1 X100\n\x18G1 X200\n')
                await writer.drain()
                await asyncio.wait_for(reader.readuntil(b'Grbl 1.1f [reset]\n'), 2)
                # No sleep: initialize immediately after the real banner on the same socket.
                writer.write(b'$I\n$G\n'); await writer.drain()
                response = await asyncio.wait_for(reader.readuntil(b'ok\n'), 2)
                self.assertIn(b'[VER:', response)
                self.assertIn(b'[GC:', await asyncio.wait_for(reader.readuntil(b'ok\n'), 2))
                self.assertEqual(len(clients), 1)
            writer.write(b'$X\n?'); await writer.drain()
            await asyncio.wait_for(reader.readuntil(b'ok\n'), 2)
        rows, client, bridge = await exercise(scenario)
        self.assertIsNone(bridge.failure_reason)
        self.assertNotIn(b'G1 X100', b''.join(client.payloads))
        self.assertNotIn(b'G1 X200', b''.join(client.payloads))
        self.assertEqual(client.payloads.count(b'\x18'), 2)
        result = self.audit_rows(rows)
        self.assertTrue(result['passed'])
        self.assertEqual(result['reset_count'], 2)
        self.assertTrue(result['connection_retained_after_reset'])
        self.assertTrue(result['restart_safe'])
        self.assertTrue(result['position_recovery_required'])
        # Audit must still reject a fabricated reset confirmation or fresh reply.
        changed = copy.deepcopy(rows)
        next(row for row in changed if row['event'] == 'pipeline_native_reset_completed')['position_trusted'] = True
        with self.assertRaises(AssertionError): self.audit_rows(changed)
        changed = copy.deepcopy(rows)
        next(row for row in changed if row['event'] == 'pipeline_tcp_delivered')['hex'] = b'ok\n'.hex()
        with self.assertRaises(AssertionError): self.audit_rows(changed)

    async def test_coalesced_native_status_reports_do_not_leave_phantom_queries(self):
        async def scenario(reader, writer, rows, *args):
            writer.write(b'???'); await writer.drain()
            self.assertIn(b'<Idle|', await asyncio.wait_for(reader.readuntil(b'>\n'), 2))
            writer.write(b'$I\n'); await writer.drain()
            self.assertIn(b'[VER:', await asyncio.wait_for(reader.readuntil(b'ok\n'), 2))
        rows, client, bridge = await exercise(scenario, coalesce_status=True)
        self.assertEqual(client.payloads.count(b'?'), 5)  # baseline, three client bytes, EOF probe
        self.assertIsNone(bridge.failure_reason)
        self.assertTrue(any(row['event'] == 'control_status_coalesced' for row in rows))
        self.assertTrue(self.audit_rows(rows)['passed'])

    async def test_extra_real_status_does_not_replace_post_eof_probe(self):
        async def scenario(reader, writer, rows, bridge, clients, port):
            writer.write(b'?'); await writer.drain()
            await asyncio.wait_for(reader.readuntil(b'>\n'), 2)
            extra = b'<Idle|MPos:0,400,0,0|FS:0,0>\n'
            clients[0].data(extra)
            self.assertEqual(await asyncio.wait_for(reader.readuntil(b'>\n'), 2), extra)
        rows, client, bridge = await exercise(scenario)
        self.assertIsNone(bridge.failure_reason)
        result = self.audit_rows(rows)
        self.assertTrue(result['passed'])
        self.assertTrue(result['restart_safe'])
        self.assertEqual(client.payloads[-1], b'?')

    async def test_post_eof_run_status_blocks_automatic_reconnect(self):
        async def scenario(reader, writer, rows, bridge, clients, port):
            await wait_event(rows, 'pipeline_tcp_ready')
            clients[0].state = 'Run'
        rows, client, bridge = await exercise(scenario)
        self.assertIsNone(bridge.failure_reason)
        self.assertEqual(client.payloads, [b'?', b'?'])
        result = self.audit_rows(rows)
        self.assertTrue(result['passed'])
        self.assertFalse(result['restart_safe'])
        self.assertTrue(result['requires_explicit_recovery'])

    async def test_native_jog_preserves_words_and_modal_reply_and_cancel_byte(self):
        async def scenario(reader, writer, rows, bridge, clients, port):
            writer.write(b'$J=G91 G21 X1 F100\n')
            await writer.drain()
            await asyncio.wait_for(reader.readuntil(b'ok\n'), 2)
            writer.write(b'?')
            await writer.drain()
            self.assertIn(b'<Jog|', await asyncio.wait_for(reader.readuntil(b'>\n'), 2))
            writer.write(b'\x85$G\n')
            await writer.drain()
            response = await asyncio.wait_for(reader.readuntil(b'ok\n'), 2)
            self.assertIn(b'G0 G54 G17 G21 G90', response)
        rows, client, bridge = await exercise(scenario)
        payload = b''.join(client.payloads)
        self.assertIn(b'$J=G91 G21 X1 F100\n', payload)
        self.assertNotIn(b'G91 G1 ', payload)
        self.assertIn(b'\x85', payload)
        self.assertIsNone(bridge.failure_reason)
        self.assertTrue(self.audit_rows(rows)['passed'])

    async def test_real_error_releases_credit_without_fake_ok_or_closing(self):
        async def scenario(reader, writer, *args):
            writer.write(b'INVALID\n')
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readuntil(b'\n'), 2), b'error:20\n')
            writer.write(b'$I\n')
            await writer.drain()
            self.assertIn(b'[VER:', await asyncio.wait_for(reader.readuntil(b'ok\n'), 2))
        rows, _, bridge = await exercise(scenario)
        self.assertIsNone(bridge.failure_reason)
        self.assertTrue(self.audit_rows(rows)['passed'])

    async def test_alarm_baseline_allows_native_unlock_on_same_connection(self):
        async def scenario(reader, writer, *args):
            writer.write(b'$X\n?')
            await writer.drain()
            # Status is out of band and may precede the unlock's ordinary reply.
            await asyncio.wait_for(reader.readuntil(b'ok\n'), 2)
            writer.write(b'?')
            await writer.drain()
            self.assertIn(b'<Idle|', await asyncio.wait_for(reader.readuntil(b'>\n'), 2))
        rows, _, bridge = await exercise(scenario, baseline_state='Alarm')
        self.assertIsNone(bridge.failure_reason)
        self.assertTrue(self.audit_rows(rows)['passed'])

    async def test_native_non_text_and_optional_extended_bytes_are_not_whitelisted(self):
        async def scenario(reader, writer, *args):
            writer.write(b'\x86\xffG1 X0\x00\n')
            await writer.drain()
            await asyncio.wait_for(reader.readuntil(b'ok\n'), 2)
        rows, client, bridge = await exercise(scenario)
        self.assertIn(b'G1 X0\x00\n', b''.join(client.payloads))
        self.assertIn(b'\x86\xff', b''.join(client.payloads))
        self.assertIsNone(bridge.failure_reason)
        self.assertTrue(self.audit_rows(rows)['passed'])

    async def test_lightburn_idle_resume_is_forwarded_instead_of_silently_ignored(self):
        async def scenario(reader, writer, *args):
            writer.write(b'~$G\n')
            await writer.drain()
            self.assertIn(b'[GC:', await asyncio.wait_for(reader.readuntil(b'ok\n'), 2))
        rows, client, bridge = await exercise(scenario)
        self.assertEqual(client.payloads.count(b'~'), 1)
        self.assertIsNone(bridge.failure_reason)
        self.assertTrue(self.audit_rows(rows)['passed'])

    async def test_slow_native_command_does_not_hit_the_initialization_ack_timeout(self):
        async def scenario(reader, writer, *args):
            writer.write(b'G4P60\n')
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readuntil(b'ok\n'), 3), b'ok\n')
        rows, client, bridge = await exercise(scenario, slow_ack=True)
        self.assertIsNone(bridge.failure_reason)
        self.assertEqual(b''.join(client.payloads).count(b'G4P60\n'), 1)
        self.assertTrue(self.audit_rows(rows)['passed'])

    async def test_jog_cancel_discards_host_queued_jogs_without_fake_ok(self):
        async def scenario(reader, writer, rows, *args):
            await wait_event(rows, 'pipeline_tcp_ready')
            writer.write(b'$J=G91X1F100\n$J=G91X2F100\n\x85$G\n')
            await writer.drain()
            output = await asyncio.wait_for(reader.readuntil(b'ok\n'), 2)
            self.assertEqual(output.count(b'ok\n'), 1)
        rows, client, bridge = await exercise(scenario)
        self.assertNotIn(b'$J=', b''.join(client.payloads))
        self.assertIn(b'\x85', client.payloads)
        self.assertIsNone(bridge.failure_reason)
        self.assertTrue(self.audit_rows(rows)['passed'])

    async def test_pause_and_resume_require_actual_states(self):
        async def scenario(reader, writer, rows, *args):
            writer.write(b'!')
            await writer.drain()
            self.assertIn(b'Hold:0', await asyncio.wait_for(reader.readuntil(b'>\n'), 2))
            writer.write(b'~')
            await writer.drain()
            self.assertIn(b'<Run|', await asyncio.wait_for(reader.readuntil(b'>\n'), 2))
        rows, client, bridge = await exercise(scenario)
        self.assertIn(b'!', client.payloads)
        self.assertIn(b'~', client.payloads)
        self.assertTrue(self.audit_rows(rows)['passed'])
        self.assertFalse(self.audit_rows(rows)['restart_safe'])

    async def test_loss_after_acceptance_keeps_counts_and_never_replays(self):
        async def scenario(reader, writer, rows, *args):
            writer.write(b'$I\n')
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        rows, client, bridge = await exercise(scenario, disconnect=True)
        self.assertEqual(client.payloads, [b'?', b'$I\n'])
        self.assertTrue(bridge.cleanup_unknown)
        self.assertTrue(any(row['event'] == 'control_generation_abandoned' for row in rows))
        self.assertTrue(any(row['event'] == 'stream_write_uncertain' for row in rows))
        with self.assertRaises(AssertionError): self.audit_rows(rows)
