#!/usr/bin/env python3
"""Capture a pre-flash GRBL snapshot over USB; fixed read-only command list."""

import argparse
import datetime
import json
from pathlib import Path
import time


QUERIES = (
    ("status_before", b"?"),
    ("build", b"$I\n"),
    ("settings", b"$$\n"),
    ("parameters", b"$#\n"),
    ("modal", b"$G\n"),
    ("startup", b"$N\n"),
    ("status_after", b"?"),
)


def response_complete(name, data):
    lines = data.replace(b"\r", b"").split(b"\n")
    complete = lines[:-1]
    if name.startswith("status_"):
        return any(line.startswith(b"<") and line.endswith(b">") for line in complete)
    return any(line == b"ok" or line.startswith((b"error:", b"ALARM:")) for line in complete)


def validate_response(name, data):
    text = data.decode("ascii", "strict").replace("\r", "")
    lines = text.splitlines()
    if name.startswith("status_"):
        if len(lines) != 1 or not lines[0].startswith("<Idle|") or "|FS:0,0" not in lines[0]:
            raise RuntimeError(f"{name}: controller is not confirmed Idle / FS:0,0")
    elif not lines or lines[-1] != "ok" or any(line.startswith(("error:", "ALARM:")) for line in lines):
        raise RuntimeError(f"{name}: no successful terminal response")
    elif name == "build" and not any(line.startswith("[VER:") and "NEJE" in line for line in lines):
        raise RuntimeError("build: unexpected controller version reply")
    elif name == "settings" and not any(line.startswith("$130=") for line in lines):
        raise RuntimeError("settings: travel limit missing")
    elif name == "startup" and lines != ["$N0=", "$N1=", "ok"]:
        raise RuntimeError("startup: startup blocks are not both empty")
    return text


def capture(port, name, payload, log):
    log("tx_attempt", name=name, hex=payload.hex())
    sent = port.write(payload)
    if sent != len(payload):
        raise RuntimeError(f"{name}: partial write; no retry")
    data = bytearray()
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        chunk = port.read(4096)
        if chunk:
            data.extend(chunk)
            log("rx_chunk", name=name, hex=chunk.hex())
            if len(data) > 16384:
                raise RuntimeError(f"{name}: response overflow")
            if response_complete(name, data):
                text = validate_response(name, data)
                log("query_verified", name=name, text=text)
                return
    raise TimeoutError(f"{name}: no complete response; no retry")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--laser-power-isolated", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"offline_only": True, "port": args.port,
                          "queries": [name for name, _ in QUERIES], "writes_settings": False}))
        return 0
    if not args.laser_power_isolated:
        parser.error("A physical laser power isolation confirmation is required")
    import serial
    with args.log.open("x") as stream:
        def log(event, **fields):
            row = {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "event": event, **fields}
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            print(json.dumps(row, ensure_ascii=False), flush=True)

        port = serial.Serial(port=None, baudrate=115200, timeout=0.1,
                             write_timeout=1, exclusive=True, xonxoff=False,
                             rtscts=False, dsrdtr=False)
        port.dtr = False
        port.rts = False
        port.port = args.port
        try:
            port.open()
            log("opened", port=args.port, dtr=False, rts=False)
            # Opening USB may emit a banner. Drain it before the first query.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                chunk = port.read(4096)
                if chunk:
                    log("passive_rx", hex=chunk.hex())
            for name, payload in QUERIES:
                capture(port, name, payload, log)
            log("backup_complete", queries=len(QUERIES))
            return 0
        except Exception as exc:
            log("backup_failed", error=repr(exc))
            return 1
        finally:
            port.close()
            log("closed", port=args.port, is_open=port.is_open)


if __name__ == "__main__":
    raise SystemExit(main())
