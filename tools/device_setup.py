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
import json
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
    args = parser.parse_args()
    if args.scan:
        rows = scan(args.seconds)
        print(f'发现 {len(rows)} 个蓝牙设备：')
        for name, address in rows:
            mark = '  ← NEJE' if NAME_HINT in name.upper() else ''
            print(f'  {name:28s} {address}{mark}')
    text, ok = status_text()
    print(text)
    if not ok:
        print(add_instructions())
