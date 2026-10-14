"""Continuous NEJE byte transport, not command policy or a hardware CLI.

LightBurn owns GRBL command/character flow control. This layer owns GATT framing,
NEJE byte counters and bounded receive storage. It never invents ok or retries.
"""
import asyncio
import time
from grbl_readonly import accept_data
from grbl_transport import encode_write
from neje_protocol import GRBL_COMM_STATUS, FrameDecoder, SYSTEM_READ_INFO
from tcp_readonly_bridge import ReliableSession


class ByteStreamSession(ReliableSession):
    system_read_after = SYSTEM_READ_INFO  # Closing only needs current_app.
    receive_limit = 16384  # below half uint16 range: wrap comparisons stay unambiguous

    def __init__(self, client, log):
        super().__init__(client, log)
        self.streaming = False
        self.pending_rx = bytearray()
        self.received_total = self.confirmed_total = 0
        self.confirmed_rcheck = 0
        self.operation_lock = asyncio.Lock()
        self.failed_write = False

    async def start(self):
        await super().start()
        self.confirmed_rcheck = self.rcheck
        self.streaming = True

    def notify(self, char, data):
        super().notify(char, data)
        if not self.streaming or self.fault:
            return
        # Consume ALL data continuously, even between requests. Control replies
        # remain available to exchange(), which can only clear control frames.
        controls = []
        try:
            for frame in self.frames:
                if frame['kind'] != 'grbl_data':
                    controls.append(frame)
                    continue
                payload = bytes.fromhex(frame['payload_hex'])
                self.rcheck = accept_data(self.rcheck, frame['read_check'], payload)
                self.pending_rx.extend(payload)
                self.received_total += len(payload)
                if len(self.pending_rx) > self.receive_limit:
                    raise RuntimeError('Unconfirmed receive overflow; stop without discarding')
                self.log('stream_rx_staged', hex=payload.hex(), read_check=self.rcheck,
                         received_total=self.received_total)
        except (ValueError, RuntimeError) as error:
            self.fault = error
            self.log('receive_fault', message=str(error))
        self.frames = controls

    def require_live(self):
        if self.fault:
            raise self.fault
        if self.failed_write:
            raise RuntimeError('Previous write uncertain; no further writes or replay')
        if not self.streaming or not self.client.is_connected:
            raise ConnectionError('Byte stream is not connected')

    async def _verify(self):
        self.require_live()
        status = await self.exchange('stream_counters', GRBL_COMM_STATUS, 'grbl_comm_status')
        self.clean(status)
        if status['way'] != 2 or status['write_check'] != self.wcheck:
            raise RuntimeError('Write counter/route mismatch; no retry')
        delta = (status['read_check'] - self.confirmed_rcheck) & 65535
        if delta > self.receive_limit:
            raise RuntimeError('Read counter outside bounded receive window')
        target = self.confirmed_total + delta
        deadline = time.monotonic() + 5
        while self.received_total < target:
            self.require_live()
            if time.monotonic() >= deadline:
                raise TimeoutError('Device read counter ahead of received bytes; no replay')
            await asyncio.sleep(.01)
        self.require_live()
        # Data can arrive AFTER the status snapshot. Keep that suffix staged for
        # the next check rather than rejecting it or pretending it was confirmed.
        confirmed = bytes(self.pending_rx[:delta])
        del self.pending_rx[:delta]
        self.confirmed_total = target
        self.confirmed_rcheck = status['read_check']
        self.log('stream_verified', hex=confirmed.hex(), write_check=self.wcheck,
                 read_check=self.confirmed_rcheck, confirmed_total=target,
                 pending_bytes=len(self.pending_rx))
        return confirmed

    async def verify(self):
        async with self.operation_lock:
            try:
                return await self._verify()
            except BaseException as error:
                self.fault = error
                raise

    async def write(self, payload):
        """Send one bounded chunk; caller owns authorization and TCP backpressure.

        Frame size must fit the backend's reported GATT no-response limit. Never
        split an NEJE frame into GATT writes without protocol evidence.
        """
        async with self.operation_lock:
            self.require_live()
            packet = encode_write(payload, self.wcheck)
            characteristic = self.client.services.get_characteristic(
                '0000ffe9-0000-1000-8000-00805f9b34fb')
            limit = characteristic.max_write_without_response_size
            if len(packet) > limit:
                raise ValueError(f'NEJE frame {len(packet)} exceeds GATT write limit {limit}')
            # Reserve before await: a cancelled/failed GATT call may already have
            # reached the device. Keep the attempted payload and NEVER resend it.
            old = self.wcheck
            self.wcheck = (self.wcheck + len(payload)) & 65535
            self.log('stream_write_reserved', hex=payload.hex(), before=old, after=self.wcheck)
            try:
                await self.send('stream_data', packet)
                return await self._verify()
            except BaseException as error:
                self.failed_write = True
                self.fault = error
                self.log('stream_write_uncertain', hex=payload.hex(), message=repr(error))
                raise

    async def close(self):
        # Cleanup is allowed despite a receive/write fault. Clear only control
        # lookup fault, retain the recorded fault and never call write() again.
        prior = self.fault
        self.streaming = False
        self.log('stream_stopped', pending_bytes=len(self.pending_rx),
                 fault=repr(prior) if prior else None, no_replay=True)
        self.fault = None
        if prior:
            # A rejected frame remains in the strict decoder. Abandon its bytes
            # only for owned-route shutdown, never for continued transmission.
            self.decoder = FrameDecoder()
            self.frames.clear()
        try:
            await super().close()
        finally:
            self.fault = prior or self.fault
