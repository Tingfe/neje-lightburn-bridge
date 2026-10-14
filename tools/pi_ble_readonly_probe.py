#!/usr/bin/env python3
"""Linux BLE comparison using the existing verified reliable-session implementation."""
import argparse
import asyncio
import datetime
import json

from tcp_readonly_bridge import ReliableSession


def log(event, **fields):
    print(json.dumps({"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      "event": event, **fields}), flush=True)


async def run():
    from bleak import BleakClient, BleakScanner
    found = await BleakScanner.discover(timeout=8, return_adv=True)
    targets = [(device, adv) for device, adv in found.values()
               if (adv.local_name or device.name) == "NEJE 5 Plus"]
    log("scan", target_count=len(targets), targets=[
        {"address": device.address, "name": device.name, "rssi": adv.rssi,
         "service_uuids": adv.service_uuids} for device, adv in targets])
    if len(targets) != 1:
        raise RuntimeError("Expected exactly one NEJE 5 Plus; no connection attempted")
    device = targets[0][0]
    async with BleakClient(device, timeout=20,
                           disconnected_callback=lambda client: log("ble_disconnected", is_connected=client.is_connected)) as client:
        log("connected", address=device.address, is_connected=client.is_connected)
        session = ReliableSession(client, log)
        try:
            await session.start()
            for name in ("build", "status"):
                response = await session.query(name)
                log("query_result", query=name, hex=response.hex(), text=response.decode("ascii"))
        finally:
            await session.close()
    log("complete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--laser-power-isolated", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"offline_only": True, "queries": ["$I", "?"],
                          "requirements": "prior physical laser power isolation; no active transport; existing GRBL app 3"}))
        return 0
    if not args.laser_power_isolated:
        parser.error("requires user-confirmed laser power isolation")
    log("start", laser_power_isolated_user_attestation=True)
    try:
        asyncio.run(run())
        return 0
    except Exception as exc:
        log("error", error=repr(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
