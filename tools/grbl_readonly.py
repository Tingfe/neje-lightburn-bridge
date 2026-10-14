"""Offline packet builders for the read-only GRBL experiment.
Protocol evidence: NEJE Windows v6.8.5, methods 06000087--0600008a.
No general G-code builder is exposed. Communication mode bytes are NOT sent here.
"""
from neje_protocol import crc32_mpeg2

# Temporary transport routing, not a verified safe physical operating mode.
ENABLE_BLE_RELIABLE = bytes.fromhex('ff aa 08 40 02 02 00 55')
DISABLE_RELIABLE = bytes.fromhex('ff aa 08 40 02 00 00 55')
READ_ONLY_QUERIES = {'build': b'$I\n', 'status': b'?',
                     # A bare line terminator. It is not G-code, describes no motion and
                     # adds no word: it only closes whatever already sits in the controller
                     # line buffer. That makes it the one observation that can show whether
                     # an interrupted write left a half line behind, because GRBL exposes no
                     # command that dumps its line buffer.
                     'terminate_line': b'\n'}


def encode_query(name, write_check):
    if name not in READ_ONLY_QUERIES:
        raise ValueError("Only the whitelisted read-only queries and the bare line terminator are allowed")
    if not 0 <= write_check <= 0xffff:
        raise ValueError('write_check must be uint16')
    payload = READ_ONLY_QUERIES[name]
    frame = bytearray(bytes([0xff,0xaa,9+len(payload),0x40,3,0]) +
                      write_check.to_bytes(2,'little') + payload + b'\0')
    frame[-1] = crc32_mpeg2(frame[1:-1]) & 0xff
    return bytes(frame)


def encode_read_from(read_check):
    if not 0 <= read_check <= 0xffff:
        raise ValueError('read_check must be uint16')
    frame = bytearray(bytes.fromhex('ff aa 09 40 04 00') + read_check.to_bytes(2,'little') + b'\0')
    frame[-1] = crc32_mpeg2(frame[1:-1]) & 0xff
    return bytes(frame)


def accept_data(expected, received, payload):
    """Refuse gaps/duplicates; no silent resynchronization or automatic resend."""
    if received != expected:
        raise ValueError(f'read counter mismatch: expected {expected}, received {received}')
    return (expected + len(payload)) & 0xffff


if __name__ == '__main__':
    import json
    print(json.dumps({'offline_only':True,'enable_ble':ENABLE_BLE_RELIABLE.hex(' '),
                      'build_query_at_zero':encode_query('build',0).hex(' '),
                      'disable':DISABLE_RELIABLE.hex(' ')},indent=2))
