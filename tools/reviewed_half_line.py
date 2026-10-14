"""Offline review of the half-line stress job; no hardware entry.

Purpose: the single padded motion line is longer than one GATT write payload, so
the bridge's character-level streaming must split it. The first write therefore
ends mid-line (no LF), which is exactly the state this fault experiment exists to
exercise. This module never opens a device, a socket or a serial port, and never
grants motion or laser permission.
"""
import hashlib
from pathlib import Path

from reviewed_job_file import load

EXPORT = Path(__file__).resolve().parents[1] / 'evidence/half-line-disconnect-20261013/half-line-job.gc'
SHA256 = 'fc05bc8bb4eed5543062f63360a87becca2b173376305ea99aeef5733baee659'
PROFILE = 'xy-linear-zero'
# Conservative lower bound for the NEJE no-response write limit measured at 173
# payload bytes. Used only to prove offline that the long line must be split.
MIN_SPLIT_CAPACITY = 173


def review(path=EXPORT):
    """Offline review only; the runtime gates live in the bridge and the CLI."""
    return load(path, expected_sha256=SHA256, profile=PROFILE)


def wire_lines(path=EXPORT):
    """Wire bytes actually streamed: inline comments are preserved, full-line
    comment rows are dropped by the bridge exactly as for the other reviews."""
    review(path)
    lines = tuple(line + b'\n' for line in Path(path).read_bytes().splitlines()
                  if line.split(b';', 1)[0].strip())
    b''.join(lines).decode('ascii')
    return lines


def job(path=EXPORT):
    return b''.join(wire_lines(path))


def split_plan(capacity=MIN_SPLIT_CAPACITY, path=EXPORT):
    """Offline proof that a partial-line write is unavoidable at this capacity.

    Returns the number of writes and whether the first write ends mid-line.
    """
    payload = bytearray()
    first = None
    writes = 0
    for line in wire_lines(path):
        offset = 0
        while offset < len(line):
            amount = min(len(line) - offset, capacity - len(payload))
            if amount <= 0:
                if first is None:
                    first = bytes(payload)
                writes += 1
                payload = bytearray()
                continue
            payload.extend(line[offset:offset + amount])
            offset += amount
            if len(payload) == capacity:
                if first is None:
                    first = bytes(payload)
                writes += 1
                payload = bytearray()
    if payload:
        if first is None:
            first = bytes(payload)
        writes += 1
    return dict(capacity=capacity, writes=writes, first_write_bytes=len(first),
                first_write_ends_with_lf=first.endswith(b'\n'),
                first_write_ends_mid_line=not first.endswith(b'\n'),
                longest_line_bytes=max(len(line) for line in wire_lines(path)),
                total_bytes=len(job(path)))


if __name__ == '__main__':
    import json
    print(json.dumps(dict(review=review().summary(), split=split_plan()), indent=2, ensure_ascii=False))
