"""First-run helpers: find the machine over Bluetooth, and get it into LightBurn.

Two things a brand new setup cannot do without, and which the bridge alone cannot
answer:

  * `scan()` — list what is actually advertising nearby, so an operator with a device
    whose name is not exactly "NEJE 5 Plus" (or with two of them) can pick instead of
    hitting a bare "Expected exactly one NEJE 5 Plus".
  * `lightburn_tcp_device()` reads LightBurn's existing local TCP profile.
  * `add_tcp_device()` adds a native GRBL/TCP profile without a template.

Device creation writes only while LightBurn is closed, with backup and read-back.
"""
import asyncio
import datetime
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import neje_i18n

PREFS = Path.home() / 'Library/Preferences/LightBurn/prefs.ini'
NAME_HINT = 'NEJE'
BRIDGE_PORT = 4002


async def _discover(seconds):
    from bleak import BleakScanner
    # Bleak's timeout covers advertising, after adapter initialization. Bound
    # that initialization too, so absent/disabled Bluetooth cannot wait forever.
    return await asyncio.wait_for(BleakScanner.discover(timeout=seconds), seconds + 8)


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
    try:
        start, end = _block_span(text)
        return json.loads(text[start:end])
    except ValueError:
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
        net = network_port(device)
        if str(net) == str(port) and device.get('Info') in ('127.0.0.1', 'localhost'):
            names.append(device.get('DisplayName') or device.get('Name') or '?')
    return names


def network_port(device):
    """Current LightBurn stores the authoritative port inside Settings."""
    return device.get('Settings', {}).get('NetworkPort') or device.get('NetworkPort')


def lightburn_running():
    result = subprocess.run(['pgrep', '-x', 'LightBurn'], capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError('无法确认 LightBurn 已退出；未修改配置')
    return result.returncode == 0


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
    key = re.search(r'"DeviceList"\s*:\s*', text)
    if key is None: raise ValueError('DeviceList not found')
    start = key.end()
    value, length = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(value, list): raise ValueError('DeviceList must be an array')
    return start, start + length


def build_device(devices, port=BRIDGE_PORT, name='LaserLink NEJE5 (蓝牙桥接)'):
    """Create a self-contained native GRBL profile; do not inherit another machine."""
    return dict(DisplayName=name, Name='GRBL', ProfilePath='GRBL', Type='TCP',
        Info='127.0.0.1', NetworkPort=port,
        GUID=datetime.datetime.now().strftime('%Y%m%d%H%M%S%f'),
        Width=260, Height=400, HomeOnStartup=False,
        MirrorX=False, MirrorY=False, JogContinuous=False,
        EnableLaser2Offset=False, EnableProcessOffset=False,
        DefaultCutList=[], DefaultToolCutList=[], Macros=[], Checklist='',
        Settings=dict(NetworkPort=port, S_Scale=1000, Units=1, CutOrigin=2,
            TransferMode=0, AllowSettingsFetch=True, EnableGrblJCommand=True,
            EnableDTR=False, EnableZ=False, EnableU=False,
            StartGCode='', EndGCode='', LaserFire_Enable=False, LaserFire_Power=0,
            LaserFrame_Enable=False, GCodeClustering=False, EnableBoundsCheck=True))


def add_tcp_device(apply=False, port=BRIDGE_PORT, language='zh'):
    """Add the bridge as a LightBurn device. Returns (changed, message)."""
    t = lambda message, **values: neje_i18n.translate(message, language, **values)
    devices = _devices()
    if devices is None:
        return False, t('找不到 LightBurn 配置：{path}', path=PREFS)
    for device in devices:
        net = network_port(device)
        if (str(device.get('Type', '')).upper() == 'TCP' and str(net) == str(port)
                and device.get('Info') in ('127.0.0.1', 'localhost')):
            return False, t('已存在指向 127.0.0.1:{port} 的设备：{name}',
                            port=port, name=device.get('DisplayName'))
    name = 'LaserLink NEJE5 (BLE Bridge)' if language == 'en' else 'LaserLink NEJE5 (蓝牙桥接)'
    device = build_device(devices, port=port, name=name)
    if not apply:
        return True, (t('干跑：将新增设备 ') + json.dumps({k: device[k] for k in
                      ('DisplayName', 'Type', 'Info', 'NetworkPort', 'Name', 'ProfilePath')},
                      ensure_ascii=False))
    try:
        running = lightburn_running()
    except RuntimeError as error:
        return False, t('无法确认 LightBurn 已退出；未修改配置')
    if running:
        return False, t('LightBurn 正在运行：它会用内存里的设备表覆盖配置，请先关闭')
    text = PREFS.read_text(errors='replace')
    try:
        start, end = _block_span(text)
    except ValueError as error:
        return False, t('配置格式无法读取，未修改：{error}', error=error)
    devices.append(device)
    backup = PREFS.with_name(f'prefs.ini.neje-backup-{datetime.datetime.now():%Y%m%dT%H%M%S}')
    shutil.copy2(PREFS, backup)
    PREFS.write_text(text[:start] + json.dumps(devices, indent=4) + text[end:])
    check = lightburn_tcp_device(port)
    if not check:
        shutil.copy2(backup, PREFS)
        return False, t('写入后校验失败，已回滚（备份仍在 {backup}）', backup=backup)
    return True, t('已新增设备「{name}」（备份 {backup}）',
                   name=device['DisplayName'], backup=backup.name)


def add_instructions(port=BRIDGE_PORT, language='zh'):
    if language == 'en':
        return (
            'Add a LightBurn device for this bridge once:\n'
            '  1. Save your work and quit LightBurn, then click “Create LightBurn device” here.\n'
            '     Or use LightBurn → Devices → Create Manually.\n'
            '  2. Choose the native GRBL device type.\n'
            f'  3. Choose TCP/Ethernet, address 127.0.0.1, port {port}.\n'
            '  4. Save and connect to that device.\n'
            '(This setup is needed only once.)'
        )
    return (
        'LightBurn 里还没有指向本程序的设备，添加一次即可：\n'
        '  1. 优先保存并退出 LightBurn，点本程序「创建 LightBurn 设备」\n'
        '     也可在 LightBurn → 设备（Devices）→ 创建手动设备\n'
        '  2. 设备类型选原生 GRBL\n'
        f'  3. 连接方式选 TCP/以太网，地址 127.0.0.1，端口 {port}\n'
        '  4. 保存后连接该设备\n'
        '（只需做一次。之后本程序启动时它就能直接连上。）'
    )


def status_text():
    """One line for the control panel, saying only what was actually checked.

    This reads LightBurn's configuration FILE. It cannot know whether LightBurn accepts
    the entry -- only a LightBurn start can show that -- so it must not claim the device
    is ready.
    """
    names = lightburn_tcp_device()
    if names is None:
        return ('⚠️ 找不到 LightBurn 的配置文件（LightBurn 装过吗？）', False)
    if not names:
        return (f'⚠️ 配置里还没有指向 127.0.0.1:{BRIDGE_PORT} 的设备 —— '
                '点「创建 LightBurn 设备」或「添加步骤」', False)
    listed = '、'.join(names)
    try:
        running = lightburn_running()
    except RuntimeError:
        return (f'配置文件里已有「{listed}」；请在 LightBurn 中选择该设备。运行状态无法确认。', True)
    if running:
        return (f'LightBurn 正在运行；配置文件里有「{listed}」'
                '（本次启动是否显示它，需要在 LightBurn 里看一眼）', True)
    return (f'配置文件里已有「{listed}」（尚未在 LightBurn 中确认：启动 LightBurn 看它是否出现在设备列表）',
            True)


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
