from pathlib import Path
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from grbl_transport import encode_write
from grbl_readonly import encode_query
from lightburn_init_bridge import InitParser, InitSession, REQUESTS, reported_idle
from tcp_readonly_bridge import QueryParser
from neje_protocol import GRBL_COMM_STATUS, crc32_mpeg2


class ProfileTests(unittest.TestCase):
    def test_idle_requires_exact_zero_power_field(self):
        self.assertTrue(reported_idle('<Idle|FS:0,0|MPos:0,0>\n'))
        for state in ('<Idle|FS:0,0.3>\n', '<Run|FS:0,0>\n', '<Idle|FS:0,0>\nALARM:1\n'):
            self.assertFalse(reported_idle(state))

    def test_exact_lines_at_every_split(self):
        data = b'G0\n$I\r\n$#\n?\n'
        expected = [('query', n) for n in ('rapid_mode', 'build', 'parameters', 'status')]
        for i in range(len(data) + 1):
            parser = InitParser()
            self.assertEqual(parser.feed(data[:i]) + parser.feed(data[i:]), expected)

    def test_existing_default_still_rejects_extra_commands(self):
        self.assertEqual(QueryParser().feed(b'G0\n$#\n'),
                         [('blocked', b'G0'.hex()), ('blocked', b'$#'.hex())])
        # The strict read-only bridge keeps its original whitelist: no $$ there.
        self.assertEqual(QueryParser().feed(b'$$\n'), [('blocked', b'$$'.hex())])

    def test_settings_dump_is_read_only_and_admitted(self):
        # $$ only dumps settings; the bridge itself already issues this read.
        self.assertEqual(REQUESTS['settings'], b'$$\n')
        for wire in (b'$$\n', b'$$\r\n'):
            with self.subTest(wire=wire):
                self.assertEqual(InitParser().feed(wire), [('query', 'settings')])
        # Every settings write stays blocked, in every profile.
        for command in (b'$30=500', b'$0=10', b'$$$', b'$', b'$I '):
            with self.subTest(command=command):
                self.assertTrue(all(kind == 'blocked'
                                    for kind, _ in InitParser().feed(command + b'\n')))

    def test_no_accidental_motion_reset_or_settings(self):
        for command in (b'G0 X1', b'G0X1', b'G00', b'G0 S1', b'G0?', b' G0', b'G0 ',
                        b'$H', b'$X', b'$J=G91X1F100', b'$40=0', b'M3', b'G0(comment)', b'\x18'):
            with self.subTest(command=command):
                self.assertTrue(all(kind == 'blocked' for kind, _ in InitParser().feed(command + b'\n')))

    def test_control_bytes_do_not_splice_an_allowed_line(self):
        self.assertEqual(InitParser().feed(b'G\x180\n'), [('blocked', '4718'), ('blocked', '30')])

    def test_transport_matches_existing_readonly_vectors(self):
        for name, payload in (('build', b'$I\n'), ('status', b'?')):
            for wcheck in (0, 65534, 65535):
                self.assertEqual(encode_write(payload, wcheck), encode_query(name, wcheck))

    def test_large_transport_header_and_crc(self):
        for n in (1, 20, 246, 247, 256):
            frame = encode_write(b'A' * n, 65535)
            self.assertEqual(frame[2] + (frame[1] - 170) * 256, n + 9)
            self.assertEqual(frame[-1], crc32_mpeg2(frame[1:-1]) & 255)
        for payload in (b'', b'A' * 257, 'G0\n'):
            with self.assertRaises(ValueError): encode_write(payload, 0)
        with self.assertRaises(ValueError): encode_write(b'?', 65536)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, name, output, counter_mismatch=False):
        rows, writes = [], []
        class Client:
            is_connected = True
            async def write_gatt_char(self, uuid, packet, response):
                writes.append(packet)
                if packet == GRBL_COMM_STATUS:
                    status = dict(kind='grbl_comm_status', way=2,
                                  write_check=(65534 + len(REQUESTS[name]) + int(counter_mismatch)) & 65535,
                                  read_check=(65530 + len(output)) & 65535,
                                  device_tx_error=0, device_rx_error=0)
                    session.frames.append(status)
                else:
                    self_check = encode_write(REQUESTS[name], 65534)
                    assert packet == self_check
                    first = output[:5]
                    session.frames.extend([
                        dict(kind='grbl_data', read_check=65530, payload_hex=first.hex()),
                        dict(kind='grbl_data', read_check=(65530 + len(first)) & 65535,
                             payload_hex=output[5:].hex())])
        session = InitSession(Client(), lambda event, **fields: rows.append((event, fields)))
        session.wcheck, session.rcheck = 65534, 65530
        response = await session.query(name)
        return response, rows, writes, session

    async def test_real_multiline_parameter_response_and_wrap(self):
        output = b'[G54:0,0,0]\n[G55:0,0,0]\n[TLO:0]\nok\n'
        response, rows, writes, session = await self.exercise('parameters', output)
        self.assertEqual(response, output)
        self.assertEqual(session.wcheck, 1)
        self.assertEqual(writes, [encode_write(b'$#\n', 65534), GRBL_COMM_STATUS])
        self.assertEqual(rows[-1][0], 'init_verified')

    async def test_device_error_is_preserved_not_replaced_by_ok(self):
        output = b'error:9\n'
        response, rows, _, _ = await self.exercise('rapid_mode', output)
        self.assertEqual(response, output)
        self.assertEqual(rows[-1][1]['terminal'], 'error:9')

    async def test_alarm_delivered_then_further_requests_blocked(self):
        response, _, writes, session = await self.exercise('rapid_mode', b'ALARM:1\n')
        self.assertEqual(response, b'ALARM:1\n')
        count = len(writes)
        with self.assertRaises(RuntimeError): await session.query('build')
        self.assertEqual(len(writes), count)

    async def test_unverified_counters_never_deliver(self):
        with self.assertRaises(RuntimeError):
            await self.exercise('rapid_mode', b'ok\n', counter_mismatch=True)



class TrailingNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_separate_msg_frame_is_counted_and_preserved(self):
        output=b'ok\n[MSG: Laser temperature:27.1C]\n'
        class Client:
            is_connected=True
            async def write_gatt_char(self,uuid,packet,response):
                if packet==GRBL_COMM_STATUS:
                    session.frames.append(dict(kind='grbl_comm_status',way=2,write_check=3,
                        read_check=len(output),device_tx_error=0,device_rx_error=0))
                else:
                    session.frames.extend([dict(kind='grbl_data',read_check=0,payload_hex=b'ok\n'.hex()),
                        dict(kind='grbl_data',read_check=3,payload_hex=output[3:].hex())])
        session=InitSession(Client(),lambda *a,**k:None)
        self.assertEqual(await session.query('rapid_mode'),output)
        self.assertEqual(session.rcheck,len(output))

if __name__ == '__main__':
    unittest.main()
