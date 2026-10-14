#!/usr/bin/env python3
"""One USB session; fixed $I/? whitelist, no retries or reset commands."""
import datetime
import json
import sys
import time

import serial

PORT = "/dev/serial/by-id/usb-Zhixinjie_NEJE_Device_NEJE_3_Series-if00"
QUERIES = {"build": b"$I\n", "status": b"?"}


def log(event, **fields):
    print(json.dumps({"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      "event": event, **fields}), flush=True)


def receive(port, phase, deadline, complete=None):
    data = bytearray()
    while time.monotonic() < deadline:
        chunk = port.read(4096)
        if chunk:
            data.extend(chunk)
            log("rx", phase=phase, hex=chunk.hex(), text=chunk.decode("ascii", "backslashreplace"))
            if len(data) > 16384:
                raise RuntimeError("response size limit exceeded")
            if complete and complete(bytes(data)):
                return bytes(data)
    return bytes(data)


def main():
    port = serial.Serial(port=None, baudrate=115200, timeout=0.1,
                         write_timeout=1, exclusive=True, xonxoff=False,
                         rtscts=False, dsrdtr=False)
    port.dtr = False
    port.rts = False
    port.port = PORT
    log("configuration", port=PORT, baudrate=115200, dtr=False, rts=False,
        exclusive=True, whitelist={k: v.hex() for k, v in QUERIES.items()},
        note="OS/driver control-line transitions are not physically measured")
    try:
        port.open()
        log("opened", is_open=port.is_open)
        receive(port, "passive_after_open", time.monotonic() + 2)
        for name in ("build", "status"):
            payload = QUERIES[name]
            log("tx_attempt", query=name, hex=payload.hex())
            written = port.write(payload)
            log("tx_returned", query=name, count=written)
            if written != len(payload):
                raise RuntimeError("partial write; no retry")
            if name == "build":
                complete = lambda data: b"ok" in data.splitlines() or any(
                    line.startswith(b"error:") for line in data.splitlines())
            else:
                complete = lambda data: b">" in data
            response = receive(port, name, time.monotonic() + 3, complete)
            if name == "build":
                valid = b"NEJE" in response and b"[VER:" in response and b"ok" in response.splitlines()
            else:
                valid = response.startswith(b"<") and b">" in response
            log("query_result", query=name, valid=valid, hex=response.hex())
            if not valid:
                raise RuntimeError("missing/unexpected response; stop without retry")
        log("complete", queries=2)
        return 0
    except Exception as exc:
        log("failure", error=repr(exc))
        return 1
    finally:
        port.close()
        log("closed", is_open=port.is_open)


if __name__ == "__main__":
    sys.exit(main())
