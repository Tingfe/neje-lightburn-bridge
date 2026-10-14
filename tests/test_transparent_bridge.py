import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from passthrough_policy import PassthroughPolicy, MAX_LINE_BYTES


class PassthroughTests(unittest.TestCase):
    """Transparent mode must forward everything verbatim without fabricating anything."""

    def kinds(self, data):
        policy = PassthroughPolicy()
        return policy.feed(data)

    def test_arbitrary_gcode_is_forwarded_not_matched(self):
        # None of these would pass the reviewed-job policy: they are not a reviewed job.
        # Jog is deliberately absent; it is covered by JogRefusalTests.
        data = (b'G0 X5Y5\nG1 X50S800F1200\n$H\n$100=80.000\n'
                b'G10 L20 P1 X0Y0\nM3 S500\nM5\n')
        result = self.kinds(data)
        self.assertTrue(all(kind == 'ordinary' for kind, _ in result))
        self.assertEqual(b''.join(wire for _, wire in result), data)

    def test_realtime_bytes_stay_realtime(self):
        result = self.kinds(b'?')
        self.assertEqual(result, [('status', b'?')])
        self.assertEqual(self.kinds(b'!'), [('hold', b'!')])
        self.assertEqual(self.kinds(b'~'), [('resume', b'~')])
        self.assertEqual(self.kinds(b'\x18'), [('cancel', b'\x18')])

    def test_realtime_inside_a_stream_does_not_enter_the_line(self):
        result = self.kinds(b'G1 X5\n?\nG1 X6\n')
        # The '?' is out of band; the newline that followed it is a real (empty) line,
        # which is exactly what a serial cable would deliver.
        self.assertEqual([kind for kind, _ in result], ['ordinary', 'status', 'ordinary', 'ordinary'])
        ordinary = [wire for kind, wire in result if kind == 'ordinary']
        self.assertEqual(ordinary, [b'G1 X5\n', b'\n', b'G1 X6\n'])
        self.assertNotIn(b'?', b''.join(ordinary))

    def test_nothing_is_fabricated_and_nothing_repeats(self):
        data = b'G1 X1\nG1 X2\nG1 X3\n'
        result = self.kinds(data)
        forwarded = b''.join(wire for _, wire in result)
        self.assertEqual(forwarded, data)
        self.assertEqual(forwarded.count(b'G1 X2\n'), 1)

    def test_non_text_bytes_are_forwarded_like_a_serial_cable(self):
        # USB equivalence means the bridge does not editorialise: a byte a cable would
        # carry is a byte this mode carries. The device still judges it.
        result = self.kinds(b'G1 X1\x00\n')
        self.assertEqual(b''.join(wire for _, wire in result), b'G1 X1\x00\n')

    def test_oversized_line_is_refused_and_latched(self):
        policy = PassthroughPolicy()
        with self.assertRaises(ValueError):
            policy.feed(b'G1 X1' + b'0' * (MAX_LINE_BYTES + 1))
        with self.assertRaises(ValueError):
            policy.feed(b'G1 X2\n')

    def test_a_truncated_final_line_is_reported_not_completed(self):
        policy = PassthroughPolicy()
        policy.feed(b'G1 X5\nG1 X6')
        self.assertEqual(policy.eof(), b'G1 X6')

    def test_forwarded_byte_count_matches_the_input(self):
        policy = PassthroughPolicy()
        data = b'G1 X5\n?\n$H\n'
        policy.feed(data)
        self.assertEqual(policy.forwarded_bytes, len(data))


class JogTranslationTests(unittest.TestCase):
    """A measured runaway means $J= cannot be forwarded as-is; it is translated into the
    explicit relative move it always meant, and malformed forms are still refused."""

    def forward(self, data):
        policy = PassthroughPolicy()
        return b''.join(wire for kind, wire in policy.feed(data) if kind == 'ordinary')

    def test_the_runaway_command_becomes_a_bounded_relative_move(self):
        self.assertEqual(self.forward(b'$J=Y-1F100\n'), b'G91 G1 Y-1 F100\n')

    def test_multi_axis_jog_keeps_every_axis(self):
        self.assertEqual(self.forward(b'$J=X10Y-10F3000\n'), b'G91 G1 X10 Y-10 F3000\n')

    def test_whitespace_is_ignored_like_grbl_does(self):
        self.assertEqual(self.forward(b' $J = Y-1 F100\n'), b'G91 G1 Y-1 F100\n')

    def test_one_line_in_one_line_out_so_flow_control_holds(self):
        policy = PassthroughPolicy()
        result = policy.feed(b'$J=X1F100\n')
        self.assertEqual(len(result), 1)

    def test_oversized_modal_or_settings_jogs_are_refused(self):
        for bad in (b'$J=X200F100\n', b'$J=G91X1F100\n', b'$J=X1S500F100\n',
                    b'$J=X1\n', b'$J=X1F99999\n'):
            policy = PassthroughPolicy()
            with self.assertRaises(ValueError):
                policy.feed(bad)
            self.assertTrue(policy.failed)

    def test_other_commands_still_pass_untouched(self):
        self.assertEqual(self.forward(b'$H\n$100=80\nG0 X5\n'),
                         b'$H\n$100=80\nG0 X5\n')


class TransparentGateTests(unittest.TestCase):
    def parse(self, argv):
        import pipeline_tcp_bridge as bridge
        return bridge.parse_args(argv)

    def test_transparent_needs_its_own_authorization(self):
        with self.assertRaises(SystemExit):
            self.parse(['--transparent', '--execute', '--allow-initialization',
                        '--laser-output-authorized', '--log', '/tmp/x.jsonl'])

    def test_transparent_needs_a_laser_attestation(self):
        with self.assertRaises(SystemExit):
            self.parse(['--transparent', '--allow-transparent-bridge', '--execute',
                        '--allow-initialization', '--log', '/tmp/x.jsonl'])

    def test_transparent_is_exclusive_with_a_reviewed_job(self):
        with self.assertRaises(SystemExit):
            self.parse(['--transparent', '--allow-transparent-bridge', '--execute',
                        '--allow-initialization', '--laser-output-authorized',
                        '--log', '/tmp/x.jsonl', '--job-file', 'a.gc',
                        '--expected-job-sha256', 'a' * 64, '--file-profile', 'xy-linear-zero',
                        '--allow-reviewed-file', '--allow-reviewed-xy-file'])

    def test_transparent_defaults_off(self):
        args = self.parse([])
        self.assertFalse(args.transparent)
        self.assertFalse(args.allow_transparent_bridge)




if __name__ == '__main__':
    unittest.main()
