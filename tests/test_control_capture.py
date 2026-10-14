from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from control_capture_policy import ControlCapturePolicy


class ControlCaptureTests(unittest.TestCase):
    def test_every_split_preserves_lines_and_realtime_order(self):
        wire=b'$I\r!\n?~G0\n\x18$#\n'
        for i in range(len(wire)+1):
            parser=ControlCapturePolicy()
            items=parser.feed(wire[:i])+parser.feed(wire[i:]);parser.eof()
            self.assertEqual(items,[('blocked_control',b'!'),('initialization',b'$I\r\n'),
                ('status',b'?'),('blocked_control',b'~'),('initialization',b'G0\n'),
                ('blocked_control',b'\x18'),('initialization',b'$#\n')])

    def test_capture_does_not_open_other_motion_or_nonascii(self):
        for wire in (b'G1 X1\n',b'M5\n',b'G0 X0\n',b'$N\n',b'\x85',b'\x90'):
            with self.assertRaises(ValueError):ControlCapturePolicy().feed(wire)
