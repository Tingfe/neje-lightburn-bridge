"""First-run helpers: find the machine over Bluetooth, and get it into LightBurn.

Two things a brand new setup cannot do without, and which the bridge alone cannot
answer:

  * `scan()` — list what is actually advertising nearby, so an operator with a device
    whose name is not exactly "NEJE 5 Plus" (or with two of them) can pick instead of
    hitting a bare "Expected exactly one NEJE 5 Plus".
  * `lightburn_tcp_device()` — LightBurn talks to the bridge over a TCP device on
    127.0.0.1:4002. That device is created once, by hand, in LightBurn's UI; this reads
    LightBurn's own configuration to say whether it is already there, and
    `add_instructions()` spells out the steps when it is not.

Nothing here writes to LightBurn's configuration.
"""
import asyncio
import datetime
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

PREFS = Path.home() / 'Library/Preferences/LightBurn/prefs.ini'
NAME_HINT = 'NEJE'
BRIDGE_PORT = 4002


async def _discover(seconds):
    from bleak import BleakScanner
    return await BleakScanner.discover(timeout=seconds)


def scan(seconds=6.0):
    """Return [(name, address)] of everything advertising, NEJE-looking ones first."""
    found = asyncio.run(_discover(seconds))
    rows = [((device.name or '?'), device.address) for device in found]
    return sorted(rows, key=lambda row: (NAME_HINT not in row[0].upper(), row[0].lower()))


def candidates(devices):
    """The rows that look like the machine we want."""
    return [row for row in devices if NAME_HINT in row[0].upper()]


def _devices():
    if not PREFS.exists():
        return None
    text = PREFS.read_text(errors='replace')
    start = text.find('[', text.find('"DeviceList"'))
    if start < 0:
        return []
    depth = 0
    for index in range(start, len(text)):
        if text[index] == '[':
            depth += 1
        elif text[index] == ']':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:index + 1])
                except ValueError:
                    return []
    return []


def lightburn_tcp_device(port=BRIDGE_PORT):
    """DisplayNames of LightBurn devices pointing at 127.0.0.1:port.

    Returns None when LightBurn's configuration cannot be found at all, and [] when it
    is there but no such device exists yet -- two very different situations.
    """
    devices = _devices()
    if devices is None:
        return None
    names = []
    for device in devices:
        if str(device.get('Type', '')).upper() != 'TCP':
            continue
        settings = device.get('Settings', {})
        net = device.get('NetworkPort') or settings.get('NetworkPort')
        if str(net) == str(port):
            names.append(device.get('DisplayName') or device.get('Name') or '?')
    return names


def lightburn_running():
    return subprocess.run(['pgrep', '-x', 'LightBurn'],
                          capture_output=True).returncode == 0


def quit_lightburn(wait=25):
    """Ask LightBurn to exit and wait for it, because it writes prefs.ini on the way out.

    Editing the file while LightBurn runs is pointless: it rewrites the device list from
    memory, which is exactly how the previously working TCP device disappeared.
    """
    if not lightburn_running():
        return True
    subprocess.run(['osascript', '-e', 'quit app "LightBurn"'], capture_output=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        if not lightburn_running():
            return True
        time.sleep(0.5)
    return False


def _block_span(text):
    start = text.find('[', text.find('"DeviceList"'))
    depth = 0
    for index in range(start, len(text)):
        if text[index] == '[':
            depth += 1
        elif text[index] == ']':
            depth -= 1
            if depth == 0:
                return start, index + 1
    raise ValueError('DeviceList not found')


def build_device(devices, port=BRIDGE_PORT, name='LaserLink NEJE5 (蓝牙桥接)'):
    """Synthesise a TCP device from the best template LightBurn already has.

    A TCP entry that works is the ideal template; otherwise the first GRBL device is
    converted. The GRBL profile is kept, since that is what the machine speaks.
    """
    template = next((d for d in devices
                     if str(d.get('Type', '')).upper() == 'TCP'
                     and str(d.get('NetworkPort') or d.get('Settings', {}).get('NetworkPort')) == str(port)),
                    None)
    if template is None:
        template = next((d for d in devices if str(d.get('Name', '')).upper() == 'GRBL'), None)
    if template is None:
        template = next((d for d in devices if d.get('Type')), None)
    if template is None:
        raise ValueError('LightBurn has no device to use as a template')
    device = json.loads(json.dumps(template))
    device['DisplayName'] = name
    device['Type'] = 'TCP'
    device['Info'] = '127.0.0.1'
    device['NetworkPort'] = port
    device.pop('CommPort', None)
    device['GUID'] = datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')[:12]
    device['HomeOnStartup'] = False
    return device


def add_tcp_device(apply=False, port=BRIDGE_PORT):
    """Add the bridge as a LightBurn device. Returns (changed, message)."""
    devices = _devices()
    if devices is None:
        return False, f'找不到 LightBurn 配置：{PREFS}'
    for device in devices:
        net = device.get('NetworkPort') or device.get('Settings', {}).get('NetworkPort')
        if str(device.get('Type', '')).upper() == 'TCP' and str(net) == str(port):
            return False, f'已存在指向 127.0.0.1:{port} 的设备：{device.get("DisplayName")}'
    device = build_device(devices, port=port)
    if not apply:
        return True, ('干跑：将新增设备 ' + json.dumps({k: device[k] for k in
                      ('DisplayName', 'Type', 'Info', 'NetworkPort', 'Name', 'ProfilePath')},
                      ensure_ascii=False))
    if lightburn_running():
        return False, 'LightBurn 正在运行：它会用内存里的设备表覆盖配置，请先关闭'
    text = PREFS.read_text(errors='replace')
    start, end = _block_span(text)
    devices.append(device)
    backup = PREFS.with_name(f'prefs.ini.neje-backup-{datetime.datetime.now():%Y%m%dT%H%M%S}')
    shutil.copy2(PREFS, backup)
    PREFS.write_text(text[:start] + json.dumps(devices, indent=4) + text[end:])
    check = lightburn_tcp_device(port)
    if not check:
        shutil.copy2(backup, PREFS)
        return False, f'写入后校验失败，已回滚（备份仍在 {backup}）'
    return True, f'已新增设备「{device["DisplayName"]}」（备份 {backup.name}）'


def add_instructions(port=BRIDGE_PORT):
    return (
        'LightBurn 里还没有指向本程序的设备，添加一次即可：\n'
        '  1. LightBurn → 设备（Devices）→ 添加新设备 / 查找我的激光\n'
        '  2. 设备类型选 GRBL（若列表里没有，选「其他」/Custom GCode）\n'
        f'  3. 连接方式选 TCP/以太网，地址 127.0.0.1，端口 {port}\n'
        '  4. 保存后连接该设备\n'
        '（只需做一次。之后本程序启动时它就能直接连上。）'
    )


def status_text():
    """One line for the control panel."""
    names = lightburn_tcp_device()
    if names is None:
        return ('⚠️ 找不到 LightBurn 的配置（LightBurn 是否安装过？）', False)
    if not names:
        return (f'⚠️ LightBurn 里还没有指向 127.0.0.1:{BRIDGE_PORT} 的设备 —— 点「添加步骤」', False)
    return (f'✅ LightBurn 设备已就绪：{", ".join(names)}', True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan', action='store_true', help='List nearby BLE devices')
    parser.add_argument('--seconds', type=float, default=6.0)
    parser.add_argument('--add-lightburn-device', action='store_true',
                        help='Add the bridge as a LightBurn TCP device (dry run unless --apply)')
    parser.add_argument('--apply', action='store_true',
                        help='With --add-lightburn-device: actually write prefs.ini')
    args = parser.parse_args()
    if args.scan:
        rows = scan(args.seconds)
        print(f'发现 {len(rows)} 个蓝牙设备：')
        for name, address in rows:
            mark = '  ← NEJE' if NAME_HINT in name.upper() else ''
            print(f'  {name:28s} {address}{mark}')
    if args.add_lightburn_device:
        changed, message = add_tcp_device(apply=args.apply)
        print(('✅ ' if changed else 'ℹ️ ') + message)
    text, ok = status_text()
    print(text)
    if not ok:
        print(add_instructions())
