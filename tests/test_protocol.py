import sys
from pathlib import Path
import unittest
import json
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from neje_protocol import FrameDecoder, crc32_mpeg2, describe

class ProtocolTests(unittest.TestCase):
    def test_crc_known_check(self):
        self.assertEqual(crc32_mpeg2(b'123456789'), 0x0376e6e7)

    def test_fragmentation_noise_and_multiple_frames(self):
        frame = bytes.fromhex('ff aa 08 01 01 05 02 55')
        decoder = FrameDecoder()
        self.assertEqual(decoder.feed(b'noise'+frame[:3]), [])
        self.assertEqual(decoder.feed(frame[3:]+frame), [frame, frame])
        self.assertEqual(describe(frame)['model'], 5)

    def test_crc_trailer_and_corruption(self):
        body = bytes.fromhex('ff aa 08 01 01 05 02')
        frame = body+bytes([crc32_mpeg2(body[1:]) & 255])
        self.assertEqual(FrameDecoder().feed(frame), [frame])
        bad = bytearray(frame);bad[-1] ^= 1
        self.assertEqual(FrameDecoder().feed(bad+frame), [frame])

    def test_extended_length(self):
        frame = bytes.fromhex('ff ab 05 10 01')+bytes(255)+b'\x55'
        self.assertEqual(len(frame),261)
        self.assertEqual(FrameDecoder().feed(frame), [frame])

    def test_bad_length_and_short_semantic_frame(self):
        decoder=FrameDecoder()
        self.assertEqual(decoder.feed(bytes.fromhex('ff aa 00 ff aa 06 10 01 55')), [bytes.fromhex('ff aa 06 10 01 55')])
        self.assertEqual(describe(bytes.fromhex('ff aa 06 10 01 55'))['kind'],'unmapped')

    def test_real_device_notification_replay(self):
        path = Path(__file__).resolve().parents[1] / 'logs/p0-official-init-host.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(rows[-1]['event'], 'complete')
        for trial in (1, 2):
            chunks = [bytes.fromhex(r['hex']) for r in rows if r['event'] == 'notify' and r['trial'] == trial]
            decoder = FrameDecoder()
            frames = [frame for chunk in chunks for frame in decoder.feed(chunk)]
            self.assertEqual([len(f) for f in frames], [8,21,44,51,73,21])
            self.assertEqual(decoder.buffer, b'')
            app = describe(frames[4])
            self.assertEqual(app['current_app'], 2)
            self.assertEqual(app['apps'], [{'id':0,'version':[1,0,0]}] +
                             [{'id':i,'version':[2,3,2]} for i in (1,2,3)])
            # Notification boundaries must not affect stream reconstruction.
            stream = b''.join(chunks)
            for split in range(len(stream)+1):
                decoder = FrameDecoder()
                self.assertEqual(decoder.feed(stream[:split])+decoder.feed(stream[split:]), frames)

if __name__ == '__main__':unittest.main()
