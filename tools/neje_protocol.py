"""Read-only subset derived from the archived official Web implementation.
Sources: evidence/web/{Commands,CommandPicker,CommandResolver,Tool}.js.
No GRBL forwarding, mode changes, or firmware update operations.
"""
VERIFY = bytes.fromhex('ff aa 08 01 01 5a a5 55')
SYSTEM_READ_ALL = bytes.fromhex('ff aa 08 10 01 ff 00 55')
# Official Windows SYS_READ_INFO=1; public Commands.SystemRead(1).
SYSTEM_READ_INFO = bytes.fromhex('ff aa 08 10 01 01 00 55')
# NEJE Windows v6.8.5 Commands.GrblCommExGetStatus, method token 06000087.
GRBL_COMM_STATUS = bytes.fromhex('ff aa 08 40 01 00 00 55')


def crc32_mpeg2(data):
    crc = 0xffffffff
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04c11db7 if crc & 0x80000000 else 0)) & 0xffffffff
    return crc


class FrameDecoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 3:
            if self.buffer[0] != 0xff or self.buffer[1] not in (0xaa, 0xab):
                del self.buffer[0]
                continue
            size = self.buffer[2] + (self.buffer[1] - 0xaa) * 256
            if size < 6:
                del self.buffer[0]
                continue
            if len(self.buffer) < size:
                break
            frame = bytes(self.buffer[:size])
            if frame[3] & 0xf0 == 0x40 and frame[4] == 4:
                if len(frame) < 9 or frame[-1] != crc32_mpeg2(frame[1:-1]) & 0xff:
                    raise ValueError('Invalid GRBL data frame CRC/length; stop without resync')
            if frame[-1] != 0x55 and frame[-1] != crc32_mpeg2(frame[1:-1]) & 0xff:
                del self.buffer[0]
                continue
            frames.append(frame)
            del self.buffer[:size]
        return frames


def describe(frame):
    """Decode only fields with explicit official offsets; retain unknowns as HEX."""
    result = {'hex': frame.hex(' '), 'size': len(frame), 'group': frame[3] & 0xf0,
              'version': frame[3] & 0x0f, 'command': frame[4], 'kind': 'unmapped'}
    if frame[3] & 0xf0 == 0 and frame[4] == 1 and len(frame) >= 8:
        result.update(kind='verify', model=frame[5], controller_version=frame[6])
    elif frame[3] & 0xf0 == 0x40:
        if frame[4] == 1 and len(frame) >= (13 if result['version'] >= 1 else 12):
            result.update(kind='grbl_comm_status', way=frame[5],
                          write_check=int.from_bytes(frame[6:8], 'little'),
                          read_check=int.from_bytes(frame[8:10], 'little'),
                          device_tx_error=frame[10])
            if result['version'] >= 1:
                result['device_rx_error'] = frame[11]
        elif frame[4] == 4 and len(frame) >= 9:
            result.update(kind='grbl_data', read_check=int.from_bytes(frame[6:8], 'little'),
                          payload_hex=frame[8:-1].hex(' '),
                          payload_ascii=frame[8:-1].decode('ascii', errors='replace'))
    elif frame[3] & 0xf0 == 0x10 and frame[4] == 1:
        subtype = frame[5]
        result['subtype'] = subtype
        if subtype == 1 and len(frame) >= 21:
            result.update(kind='system_info', current_app=frame[8], hardware_version=list(frame[9:12]))
        elif subtype == 8 and len(frame) >= 9 + frame[7] * 16:
            result.update(kind='app_info', current_app=frame[6], apps=[
                {'id': frame[k], 'version': list(frame[k+1:k+4])}
                for k in range(8, 8 + frame[7] * 16, 16)])
        elif subtype == 0x10 and len(frame) >= 21:
            result.update(kind='system_flags', current_app=frame[7], flags_hex=frame[8:20].hex(' '))
    return result
