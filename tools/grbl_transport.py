"""NEJE transport framing only. Callers enforce command authorization/policy."""
from neje_protocol import crc32_mpeg2


def encode_write(payload, write_check):
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= 256:
        raise ValueError('Transport payload must be 1..256 bytes')
    if not 0 <= write_check <= 65535:
        raise ValueError('write_check must be uint16')
    size = len(payload) + 9
    frame = bytearray(bytes([255, 170 + size // 256, size % 256, 64, 3, 0]) +
                      write_check.to_bytes(2, 'little') + payload + b'\0')
    frame[-1] = crc32_mpeg2(frame[1:-1]) & 255
    return bytes(frame)
