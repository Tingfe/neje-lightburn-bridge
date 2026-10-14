"""Offline-only exact policy for the native LightBurn zero-power review export.

This module opens no device or socket. It does not grant motion authorization.
"""
import hashlib
import json
from pathlib import Path

EXPORT_SHA256 = 'b2ef9334e8914688f5359c2b4e38faed071a602aafaceac531e04f1dd5d4a315'
JOB = (
    b'M5', b'S0', b'G00 G17 G40 G21', b'G54', b'G90', b'M4 S0',
    b'G0 X0Y400', b'G1 X1S0F60', b'G1 X0', b'M5', b'G90', b'M5', b'S0', b'M2',
)


def review_export(path):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPORT_SHA256:
        raise ValueError('Export changed; a new review is required')
    lines = tuple(line.split(b';', 1)[0].strip() for line in raw.splitlines())
    lines = tuple(line for line in lines if line)
    if lines != JOB:
        raise ValueError('Native export does not match the reviewed command sequence')
    return dict(offline_only=True, sha256=EXPORT_SHA256, commands=[x.decode() for x in lines],
                start=[0, 400, 0, 0], end=[0, 400, 0, 0], x_range=[0, 1],
                feed_mm_min=60, power=0, air_commands=False,
                hardware_executed=False, authorization_required=True)


class ReviewedJobParser:
    """Single-use ordered command acceptance; any invalid input latches failure.

    Only '?' may interleave the job. Init commands are allowed before/after it.

    Init set: '$I', '$#', bare 'G0' (modal only) and '$$' (read-only settings
    dump). '$n=value' settings writes are never admitted. The position rule
    keeps every init command outside the job body, so an init line cannot
    reorder or interrupt the reviewed sequence.

    Returns original lines, including comments and CRLF, for byte preservation.
    Accepted is not sent, acknowledged, or physically completed.
    """
    def __init__(self, job=JOB):
        self.job = tuple(job)
        self.pending = bytearray()
        self.index = 0
        self.failed = False

    @property
    def complete(self):
        return self.index == len(self.job) and not self.failed

    def feed(self, data):
        if self.failed:
            raise ValueError('Policy fault is latched; no further input accepted')
        result = []
        try:
            for byte in data:
                if byte == ord('?'):
                    result.append(('status', b'?'))
                    continue
                if byte in (ord('!'), ord('~')) or (byte not in (9, 10, 13) and not 32 <= byte < 127):
                    raise ValueError('Realtime control or non-ASCII input is not authorized')
                self.pending.append(byte)
                if len(self.pending) > 512:
                    raise ValueError('Input line exceeds review limit')
                if byte != 10:
                    continue
                wire = bytes(self.pending)
                self.pending.clear()
                command = wire.split(b';', 1)[0].strip()
                if not command:
                    result.append(('comment', wire))
                elif self.index in (0, len(self.job)) and command in (b'$I', b'$#', b'G0', b'$$'):
                    result.append(('initialization', wire))
                elif self.index < len(self.job) and command == self.job[self.index]:
                    result.append(('job', wire))
                    self.index += 1
                else:
                    raise ValueError('Unexpected, repeated or out-of-order job command')
        except ValueError:
            self.failed = True
            raise
        return result

    def eof(self):
        if self.pending or self.index not in (0, len(self.job)):
            self.failed = True
            raise ValueError('Incomplete job input; discard unsent work, never replay')


if __name__ == '__main__':
    path = Path(__file__).resolve().parents[1] / 'evidence/stream-transport/lightburn-zero-power-reviewed-v2.gc'
    print(json.dumps(review_export(path), ensure_ascii=False, indent=2))
