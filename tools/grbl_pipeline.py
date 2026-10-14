"""Ordered GRBL pipeline over a caller-owned verified byte session.

No hardware discovery, CLI, command authorization, retries or synthetic replies.
The caller supplies reviewed commands, an Idle-validated receive budget and GATT
payload capacity. LightBurn still owns its sender; this ledger bounds forwarding
and checks terminal correspondence without inventing acknowledgements.
"""
import asyncio
from collections import deque
from dataclasses import dataclass

from stream_tcp_bridge import OutputLines


@dataclass
class Line:
    sequence: int
    wire: bytes
    attempted: int = 0


class GrblPipeline:
    def __init__(self, session, deliver, log, *, rx_budget, payload_capacity,
                 queue_bytes=16384, check_status=None, deliver_lines=False,
                 max_pending_lines=64):
        if not 1 <= rx_budget <= 16384 or not 1 <= payload_capacity <= 256:
            raise ValueError('Explicit bounded RX budget and GATT payload capacity required')
        if queue_bytes < rx_budget:
            raise ValueError('Queue budget must cover the receive budget')
        if not 1 <= max_pending_lines <= 16384:
            raise ValueError('Explicit bounded pending-line budget required')
        self.session, self.deliver, self.log = session, deliver, log
        self.rx_budget, self.capacity, self.queue_limit = rx_budget, payload_capacity, queue_bytes
        self.queued, self.pending, self.controls = deque(), deque(), deque()
        self.queued_bytes = self.occupied = self.sequence = self.statuses = 0
        self.partial = None
        self.output = OutputLines()
        self.fault = None
        self.check_status = check_status
        self.deliver_lines = deliver_lines
        self.max_pending_lines = max_pending_lines
        self.lock = asyncio.Lock()

    def require_live(self):
        if self.fault:
            raise RuntimeError('Pipeline stopped; no retry or continuation') from self.fault

    def enqueue(self, wire):
        self.require_live()
        if not isinstance(wire, bytes) or not wire.endswith(b'\n'):
            raise ValueError('One complete byte line required')
        body = wire[:-1].removesuffix(b'\r')
        if any(byte in b'?!~' or (byte != 9 and not 32 <= byte < 127) for byte in body):
            raise ValueError('Line contains realtime/control/non-ASCII bytes')
        if len(wire) > self.rx_budget:
            raise ValueError('Line exceeds explicit receive budget')
        if self.queued_bytes + len(wire) > self.queue_limit:
            raise asyncio.QueueFull('Apply TCP backpressure; never evict accepted lines')
        self.sequence += 1
        self.queued.append(Line(self.sequence, wire))
        self.queued_bytes += len(wire)
        return self.sequence

    def realtime(self, wire):
        self.require_live()
        if wire not in (b'?', b'!', b'~'):
            raise ValueError('Only explicit GRBL status/hold/resume bytes supported')
        if len(self.controls) + self.statuses >= 8:
            raise asyncio.QueueFull('Realtime queue is bounded')
        self.controls.append(wire)

    def _batch(self):
        if self.controls:
            control = self.controls.popleft()
            if control == b'?':
                self._reserve_status()
            return control
        payload = bytearray()
        while len(payload) < self.capacity:
            if self.partial is None:
                if not self.queued or self.occupied + len(self.queued[0].wire) > self.rx_budget:
                    break
                if len(self.pending) >= self.max_pending_lines:
                    break
                # A tiny $ query can produce a large answer. The controller's
                # input-byte credit does not bound its BLE response burst.
                # Drain prior ordinary lines, send one query, then wait for its
                # real terminal. Ordinary GCode still uses character streaming.
                if self.pending and (self._barrier(self.queued[0]) or
                        any(self._barrier(line) for line in self.pending)):
                    break
                self.partial = self.queued.popleft()
                self.queued_bytes -= len(self.partial.wire)
                self.occupied += len(self.partial.wire)
                self.pending.append(self.partial)
                self.log('pipeline_line_reserved', sequence=self.partial.sequence,
                         hex=self.partial.wire.hex(), occupied=self.occupied)
            line = self.partial
            amount = min(len(line.wire) - line.attempted, self.capacity - len(payload))
            payload.extend(line.wire[line.attempted:line.attempted + amount])
            line.attempted += amount
            if line.attempted == len(line.wire):
                self.partial = None
        return bytes(payload)

    def _reserve_status(self):
        self.statuses += 1

    def _barrier(self, line):
        return line.wire.lstrip().startswith(b'$')

    def _public_output(self, lines, data):
        return b''.join(lines) if self.deliver_lines else data

    def _response_line(self, wire):
        terminal = wire.strip()
        if terminal == b'ok' or terminal.startswith(b'error:'):
            if not self.pending or self.pending[0].attempted != len(self.pending[0].wire):
                raise RuntimeError('Terminal without a fully attempted command')
            line = self.pending.popleft()
            self.occupied -= len(line.wire)
            self.log('pipeline_terminal', sequence=line.sequence, hex=wire.hex(),
                     command_hex=line.wire.hex(), occupied=self.occupied)
            if terminal != b'ok':
                raise RuntimeError('Real controller error; discard unsent work')
        elif terminal.startswith((b'ALARM:', b'Grbl ')):
            raise RuntimeError('Real controller alarm/reset; stop without replay')
        elif terminal.startswith(b'<') and terminal.endswith(b'>'):
            if not self.statuses:
                raise RuntimeError('Status without an outstanding query')
            self.statuses -= 1
            if self.check_status is not None:
                self.check_status(terminal.decode('ascii'))
        # [MSG], [GC], parameters etc. are data, never terminal credit.

    async def _consume(self, data):
        # Only session.write/verify-confirmed bytes reach this method. Preserve
        # exact fragments for TCP; parse independently for ledger/fault handling.
        lines = self.output.feed(data)
        if lines:
            self.log('pipeline_response_lines', hex=b''.join(lines).hex())
        visible = self._public_output(lines, data)
        if visible:
            await self.deliver(visible)
        for wire in lines:
            self._response_line(wire)

    async def step(self, *, receive_only=False):
        """One GATT-sized write, or receive poll; no wait for a per-line ok.

        Reservations precede await because cancelled/failed writes may have
        reached hardware. Any transport/output/protocol failure latches a stop.
        """
        async with self.lock:
            self.require_live()
            payload = b''
            try:
                if not receive_only:
                    payload = self._batch()
                if payload:
                    self.log('pipeline_write_attempt', hex=payload.hex())
                    data = await self.session.write(payload)
                else:
                    data = await self.session.verify()
                await self._consume(data)
                return bool(payload)
            except BaseException as error:
                self.fault = error
                self.log('pipeline_stopped', message=repr(error), uncertain_payload_hex=payload.hex(),
                         queued_bytes=self.queued_bytes, pending_lines=len(self.pending), no_replay=True)
                raise

    @property
    def drained(self):
        return not (self.fault or self.queued or self.pending or self.controls or
                    self.statuses or self.output.pending)
