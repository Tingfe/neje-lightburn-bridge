"""Native GRBL lines and realtime bytes; no jog-to-GCode translation.

Reset preempts the whole input chunk and closes this input generation. Ordinary
bytes discarded on reset/EOF are accounted for, never replayed or acknowledged.
"""
MAX_LINE_BYTES = 512
REALTIME = {ord('!'): 'hold', ord('~'): 'resume', ord('?'): 'status', 0x18: 'cancel'}
# gnea/grbl serial.c removes all extended bytes from the line parser. Forward them
# out of band; the firmware decides which overrides/optional commands it supports.
EXTENDED_REALTIME = set(range(0x80, 0x100))


class PassthroughPolicy:
    def __init__(self):
        self.pending = bytearray()
        self.failed = self.cancelled = False
        self.forwarded_bytes = self.discarded_bytes = 0

    def cancel(self):
        self.cancelled = True
        self.discarded_bytes += len(self.pending)
        self.pending.clear()

    def reopen_after_reset_banner(self):
        """Accept a new generation only after the caller observes a real GRBL banner."""
        if not self.cancelled or self.failed or self.pending:
            raise RuntimeError('Input generation cannot reopen without a clean reset boundary')
        self.cancelled = False

    def feed(self, data):
        if self.cancelled:
            self.discarded_bytes += len(data)
            return []
        # A reset must not wait behind ordinary input, including an oversized line.
        if b'\x18' in data:
            self.cancel()
            self.discarded_bytes += len(data) - 1
            self.forwarded_bytes += 1
            return [('cancel', b'\x18')]
        if self.failed:
            raise ValueError('Policy fault is latched; no further input accepted')
        result = []
        for byte in data:
            if byte in REALTIME or byte in EXTENDED_REALTIME:
                result.append((REALTIME.get(byte, 'realtime'), bytes([byte])))
                self.forwarded_bytes += 1
                continue
            self.pending.append(byte)
            if len(self.pending) > MAX_LINE_BYTES:
                self.failed = True
                raise ValueError('Input line exceeds the transparent bridge limit')
            if byte == 10:
                wire = bytes(self.pending)
                self.pending.clear()
                result.append(('ordinary', wire))
                self.forwarded_bytes += len(wire)
        return result

    def eof(self):
        leftover = bytes(self.pending)
        self.discarded_bytes += len(leftover)
        self.pending.clear()
        return leftover
