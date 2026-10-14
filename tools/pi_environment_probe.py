#!/usr/bin/env python3
"""Collect Pi/NEJE metadata without opening serial or Bluetooth devices."""

import datetime
import grp
import importlib.util
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import stat
import subprocess


def read_text(path):
    try:
        return Path(path).read_text().rstrip("\x00\n")
    except OSError as exc:
        return {"error": str(exc)}


def command(argv):
    if not shutil.which(argv[0]):
        return {"argv": argv, "unavailable": True}
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=10,
            env={**os.environ, "SYSTEMD_PAGER": "cat", "PAGER": "cat", "LC_ALL": "C"},
        )
        return {"argv": argv, "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "error": str(exc)}


def main():
    node = Path("/dev/ttyACM0")
    serial = {"path": str(node), "exists": node.exists()}
    if node.exists():
        info = node.stat()
        serial.update(
            mode=stat.filemode(info.st_mode), owner=pwd.getpwuid(info.st_uid).pw_name,
            group=grp.getgrgid(info.st_gid).gr_name,
            readable=os.access(node, os.R_OK), writable=os.access(node, os.W_OK),
            driver=str(Path("/sys/class/tty/ttyACM0/device/driver").resolve()),
        )
    serial["by_id"] = [
        {"path": str(p), "target": str(p.resolve())}
        for p in sorted(Path("/dev/serial/by-id").glob("*"))
    ]
    commands = [
        ["id"], ["lsusb"], ["lsusb", "-t"],
        ["udevadm", "info", "--query=property", "--name=/dev/ttyACM0"],
        ["fuser", "/dev/ttyACM0"],
        ["systemctl", "is-active", "bluetooth", "ModemManager", "ser2net"],
        ["rfkill", "list", "bluetooth"], ["ss", "-ltn"],
        ["dpkg-query", "-W", "bluez", "python3-venv", "python3-serial", "ser2net"],
    ]
    print(json.dumps({
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "metadata_only_no_serial_open_no_ble_connection_no_grbl_commands",
        "hostname": platform.node(), "model": read_text("/proc/device-tree/model"),
        "os_release": read_text("/etc/os-release"), "kernel": platform.release(),
        "architecture": platform.machine(), "python": platform.python_version(),
        "python_modules": {m: importlib.util.find_spec(m) is not None for m in ("serial", "bleak", "venv")},
        "serial": serial, "checks": [command(argv) for argv in commands],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
