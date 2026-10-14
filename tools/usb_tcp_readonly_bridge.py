#!/usr/bin/env python3
"""Bounded loopback USB/TCP comparison bridge, using the existing whitelist parser."""
import argparse
import asyncio
import contextlib
import signal
import time

import serial

from tcp_readonly_bridge import QueryParser
from usb_readonly_probe import PORT, QUERIES, log


class USBBridge:
    def __init__(self):
        self.active = None
        self.poisoned = False

    async def handle(self, reader, writer):
        if self.active or self.poisoned:
            log("tcp_rejected", reason="busy_or_fault")
            writer.close()
            await writer.wait_closed()
            return
        self.active = asyncio.current_task()
        port = serial.Serial(port=None, baudrate=115200, timeout=0,
                             write_timeout=1, exclusive=True,
                             xonxoff=False, rtscts=False, dsrdtr=False)
        port.dtr = port.rts = False
        port.port = PORT
        queue = asyncio.Queue(maxsize=16)
        tasks = []
        log("tcp_connected", peer=writer.get_extra_info("peername"))

        async def collect(phase, duration, complete=None):
            output = bytearray()
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                chunk = port.read(4096)
                if chunk:
                    output.extend(chunk)
                    log("usb_rx", phase=phase, hex=chunk.hex())
                    if len(output) > 16384:
                        raise RuntimeError("USB response overflow")
                    if complete and complete(bytes(output)):
                        return bytes(output)
                await asyncio.sleep(.01)
            return bytes(output)

        async def receive():
            parser = QueryParser()
            while True:
                data = await reader.read(512)
                if not data:
                    log("tcp_eof", pending_hex=parser.pending.hex())
                    return
                log("tcp_receive", hex=data.hex())
                for kind, value in parser.feed(data):
                    if kind == "blocked":
                        log("tcp_blocked", hex=value)
                        writer.write(b"[MSG:LaserLink read-only policy blocked input; local error follows]\nerror:20\n")
                        await asyncio.wait_for(writer.drain(), 2)
                    else:
                        queue.put_nowait(value)

        async def operate():
            await collect("passive_after_open", 2)
            while True:
                name = await queue.get()
                payload = QUERIES[name]
                log("usb_tx_attempt", query=name, hex=payload.hex())
                count = port.write(payload)
                log("usb_tx_returned", query=name, count=count)
                if count != len(payload):
                    raise RuntimeError("Partial USB write; no retry")
                if name == "build":
                    complete = lambda data: b"ok" in data.splitlines() or any(
                        line.startswith((b"error:", b"ALARM:")) for line in data.splitlines())
                else:
                    complete = lambda data: any(line.startswith(b"<") and line.endswith(b">")
                                                for line in data.splitlines())
                output = await collect(name, 3, complete)
                valid = (b"[VER:" in output and b"NEJE" in output and b"ok" in output.splitlines()
                         if name == "build" else output.startswith(b"<") and b">" in output)
                if not valid:
                    raise RuntimeError("Unexpected USB response; no retry")
                log("usb_query_verified", query=name, hex=output.hex())
                writer.write(output)
                await asyncio.wait_for(writer.drain(), 2)
                log("tcp_delivered", query=name, hex=output.hex())

        try:
            port.open()
            log("usb_opened", port=PORT, baudrate=115200, dtr=False, rts=False)
            tasks = [asyncio.create_task(receive()), asyncio.create_task(operate())]
            done, _ = await asyncio.wait(tasks, timeout=60, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise TimeoutError("TCP session lifetime exceeded")
            for task in done:
                task.result()
        except asyncio.CancelledError:
            log("session_cancelled")
            raise
        except Exception as exc:
            self.poisoned = True
            log("session_fault", error=repr(exc))
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            port.close()
            log("usb_closed", is_open=port.is_open, queued_abandoned=queue.qsize())
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            log("tcp_closed", poisoned=self.poisoned)
            self.active = None


async def main(seconds):
    bridge = USBBridge()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    server = await asyncio.start_server(bridge.handle, "127.0.0.1", 4002)
    log("listening", host="127.0.0.1", port=4002, seconds=seconds)
    try:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), seconds)
    finally:
        server.close()
        if bridge.active:
            bridge.active.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bridge.active
        await server.wait_closed()
        log("stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        parser.error("seconds must be 1..300")
    asyncio.run(main(args.seconds))
