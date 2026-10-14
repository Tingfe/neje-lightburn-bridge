"""Observe LightBurn control bytes without forwarding any of them to hardware.

All other bytes retain the initialization whitelist. This is not a control or
job authorization, never emits success, and cannot be combined with motion.
"""
from reviewed_job import ReviewedJobParser


class ControlCapturePolicy:
    def __init__(self):
        self.policy = ReviewedJobParser(job=())

    @property
    def pending(self):
        return self.policy.pending

    def feed(self, data):
        result, segment = [], bytearray()
        for byte in data:
            if byte in (ord('!'), ord('~'), 0x18):
                result.extend(self.policy.feed(bytes(segment)))
                segment.clear()
                result.append(('blocked_control', bytes([byte])))
            else:
                segment.append(byte)
        result.extend(self.policy.feed(bytes(segment)))
        return result

    def eof(self):
        self.policy.eof()
