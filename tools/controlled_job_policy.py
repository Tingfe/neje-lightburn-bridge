"""Exact reviewed job policy with explicitly gated realtime job controls.

Offline component only. A reset in a chunk preempts all ordinary bytes in that
chunk, including malformed input. Following source bytes are discarded forever.
"""
from reviewed_job import ReviewedJobParser


class ControlledJobPolicy:
    def __init__(self, job):
        self.policy = ReviewedJobParser(job=job)
        self.cancelled = False
        self.discarded_bytes = 0

    @property
    def pending(self):
        return self.policy.pending

    @property
    def index(self):
        return self.policy.index

    def feed(self, data):
        if self.cancelled:
            self.discarded_bytes += len(data)
            return []
        if b'\x18' in data:
            self.cancelled = True
            self.discarded_bytes += len(self.pending) + len(data) - 1
            self.pending.clear()
            return [('cancel', b'\x18')]
        result, segment = [], bytearray()
        for byte in data:
            if byte in (ord('!'), ord('~')):
                result.extend(self.policy.feed(bytes(segment)))
                segment.clear()
                result.append(('hold' if byte == ord('!') else 'resume', bytes([byte])))
            else:
                segment.append(byte)
        result.extend(self.policy.feed(bytes(segment)))
        return result

    def eof(self):
        if not self.cancelled:
            self.policy.eof()
