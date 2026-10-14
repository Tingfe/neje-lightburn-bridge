from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from motion_parity_probe import preflight, check_position, require_ok


class MotionGateTests(unittest.TestCase):
    def test_unsolicited_message_after_terminal_is_not_rejection(self):
        require_ok('ok\n[MSG: Laser temperature:27.1C]\n')
        for text in ('[MSG: ok]\n', 'error:20\nok\n', 'ok\nALARM:1\n', 'ok\nok\n', 'ok'):
            with self.assertRaises(RuntimeError): require_ok(text)

    def test_preflight_and_each_gate(self):
        args = ['$130=260\n$131=400\n$13=0\nok\n',
                '[GC:G0 G21 G90 G94 G54 M5 S0]\nok\n',
                '[G54:0,0,0,0]\n[G92:0,0,0,0]\nok\n',
                '<Idle|MPos:0,400,0,0|FS:0,0>\n']
        preflight(*args)
        for i, value in ((0, args[0].replace('260', '250')),
                         (0, args[0].replace('$13=0', '$13=1')),
                         (1, args[1].replace('G90', 'G91')),
                         (1, args[1].replace('M5', 'M4')),
                         (1, args[1].replace('S0', 'S1')),
                         (2, args[2].replace('G92:0', 'G92:1')),
                         (3, args[3].replace('MPos:0', 'MPos:1'))):
            with self.subTest(i=i, value=value):
                changed = list(args); changed[i] = value
                with self.assertRaises(RuntimeError): preflight(*changed)

    def test_run_is_not_completion_and_other_axes_must_stay_fixed(self):
        self.assertEqual(check_position('<Run|MPos:0.5,400,0,0|FS:60,0>\n', 1, False)[0], 'Run')
        for text in ('<Idle|MPos:0.5,400,0,0|FS:0,0>\n',
                     '<Idle|MPos:1,399,0,0|FS:0,0>\n',
                     '<Idle|MPos:1,400,0,0|FS:0,1>\n',
                     '<Alarm|MPos:1,400,0,0|FS:0,0>\n'):
            with self.assertRaises(RuntimeError): check_position(text, 1)


if __name__ == '__main__': unittest.main()
