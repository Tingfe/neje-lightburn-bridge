import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from emergency_stop import send_stop, STOP_BYTES


class FakeSerial:
    instances = []

    def __init__(self, *a, **k):
        self.written = []
        self.in_waiting = 0
        self.closed = False
        FakeSerial.instances.append(self)

    def write(self, payload):
        self.written.append(bytes(payload))
        return len(payload)

    def read(self, n=0):
        return b''

    def reset_input_buffer(self):
        pass

    def close(self):
        self.closed = True


class EmergencyStopTests(unittest.TestCase):
    """A stop must be exactly three realtime bytes, in order, and nothing else."""

    def setUp(self):
        FakeSerial.instances = []
        self.real = sys.modules.get('serial')
        sys.modules['serial'] = types.SimpleNamespace(Serial=FakeSerial)

    def tearDown(self):
        if self.real is not None:
            sys.modules['serial'] = self.real
        else:
            del sys.modules['serial']

    def test_only_the_stop_bytes_are_written(self):
        rows = []
        send_stop('/dev/fake', lambda event, **f: rows.append(dict(event=event, **f)),
                  settle=0, read_seconds=0)
        written = FakeSerial.instances[0].written[:len(STOP_BYTES)]
        self.assertEqual(written, [b'!', b'\x85', b'\x18'])
        self.assertEqual(FakeSerial.instances[0].written[len(STOP_BYTES):], [b'?'])
        self.assertFalse(any(b[0:1] in (b'G', b'M', b'S', b'$') for b in written))

    def test_every_stop_byte_is_recorded_before_the_readback(self):
        rows = []
        send_stop('/dev/fake', lambda event, **f: rows.append(dict(event=event, **f)),
                  settle=0, read_seconds=0)
        sent = [r for r in rows if r['event'] == 'emergency_stop_sent']
        self.assertEqual([r['label'] for r in sent], ['feed_hold', 'jog_cancel', 'soft_reset'])
        self.assertEqual([r['hex'] for r in sent], ['21', '85', '18'])
        self.assertTrue(any(r['event'] == 'emergency_stop_state' for r in rows))

    def test_the_port_is_always_closed(self):
        send_stop('/dev/fake', lambda *a, **k: None, settle=0, read_seconds=0)
        self.assertTrue(FakeSerial.instances[0].closed)


if __name__ == '__main__':
    unittest.main()
