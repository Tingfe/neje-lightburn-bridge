"""Real loopback TCP + real NEJE codec/counters + a fake BLE controller."""
import asyncio
import datetime
import sys
import types
from unittest.mock import patch

from fake_ble import FakeClient
from pipeline_tcp_bridge import PipelineTCPBridge


async def exercise(scenario, *, disconnect=False, reset_reply=True, baseline_state='Idle', slow_ack=False,
                   coalesce_status=False, session_timeout=5):
    rows, clients = [], []
    offset = [0]

    def log(event, **fields):
        rows.append(dict(event=event, utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), **fields))

    class Client(FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.payloads, self.partial = [], bytearray()
            self.deferred_ticks = 0
            self.status_count = 0
            self.state, self.modal = baseline_state, b'[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]\n'
            self.services = types.SimpleNamespace(get_characteristic=lambda uuid:
                types.SimpleNamespace(max_write_without_response_size=29))
            clients.append(self)

        async def __aenter__(self): return self
        async def __aexit__(self, *args): self.is_connected = False

        async def write_gatt_char(self, uuid, packet, response):
            from neje_protocol import GRBL_COMM_STATUS
            if packet == GRBL_COMM_STATUS and self.deferred_ticks:
                self.deferred_ticks -= 1
                offset[0] += 1
                if not self.deferred_ticks: self.data(b'ok\n')
            if packet[3:5] != b'\x40\x03':
                return await super().write_gatt_char(uuid, packet, response)
            wire = packet[8:-1]
            self.writes.append(packet)
            assert int.from_bytes(packet[6:8], 'little') == self.w
            self.w = (self.w + len(wire)) & 65535
            self.payloads.append(wire)
            if disconnect and len(self.payloads) > 1:
                self.is_connected = False
                raise ConnectionError('accepted bytes then BLE loss')
            answer = bytearray()
            for byte in wire:
                if byte == 0x18:
                    self.partial.clear()
                    self.state = 'Idle'
                    if reset_reply: answer.extend(b'Grbl 1.1f [reset]\n')
                elif byte == 0x3f:
                    self.status_count += 1
                    if not coalesce_status or self.status_count not in (2, 3):
                        answer.extend(f'<{self.state}|MPos:0,400,0,0|Bf:100,1500|FS:0,0>\n'.encode())
                elif byte == 0x21: self.state = 'Hold:0'
                elif byte == 0x7e: self.state = 'Run'
                elif byte == 0x85: self.state = 'Idle'
                elif byte >= 0x80: pass
                else:
                    self.partial.append(byte)
                    if byte == 10:
                        line = bytes(self.partial).strip()
                        self.partial.clear()
                        if line == b'$G': answer.extend(self.modal)
                        if line == b'$I': answer.extend(b'[VER:1.1f.test]\n')
                        if line in (b'$X', b'$H'): self.state = 'Idle'
                        if line.startswith(b'$J='): self.state = 'Jog'
                        if slow_ack and line == b'G4P60': self.deferred_ticks = 30
                        else: answer.extend(b'error:20\n' if line == b'INVALID' else b'ok\n')
            for i in range(0, len(answer), 17): self.data(bytes(answer[i:i+17]))

    log('pipeline_transparent_mode', usb_equivalent=True, reviewed_job_required=False, whitelist=False)
    import time
    bridge = PipelineTCPBridge('fake', log, timeout=session_timeout, transparent=True,
                               clock=lambda: time.monotonic()+offset[0])
    with patch.dict(sys.modules, {'bleak': types.SimpleNamespace(BleakClient=Client)}):
        server = await asyncio.start_server(bridge.handle, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        try:
            await scenario(reader, writer, rows, bridge, clients, port)
        finally:
            writer.close()
            await writer.wait_closed()
            await asyncio.wait_for(bridge.finished.wait(), 6)
            server.close()
            await server.wait_closed()
    log('pipeline_server_stopped', cleanup_unknown=bridge.cleanup_unknown, failure_reason=bridge.failure_reason)
    return rows, clients[0], bridge


async def wait_event(rows, event):
    for _ in range(400):
        if any(row['event'] == event for row in rows): return
        await asyncio.sleep(.005)
    raise AssertionError('missing event: ' + event)


async def faithful_rows():
    async def scenario(reader, writer, *args):
        writer.write(b'$I\n$G\n')
        await writer.drain()
        for _ in range(2): await asyncio.wait_for(reader.readuntil(b'ok\n'), 2)
        writer.write(b'?')
        await writer.drain()
        await asyncio.wait_for(reader.readuntil(b'>\n'), 2)
    return (await exercise(scenario))[0]
