"""One command to put the NEJE 5 Plus on Bluetooth for LightBurn.

    python tools/neje.py          full functionality - the laser is powered
    python tools/neje.py --test   laser physically isolated (safe dry runs)

This starts the transparent bridge with every setting already chosen: Bluetooth
transport, reliable channel, a session long enough for a working day, and a timestamped
log next to the repo. Nothing has to be configured and no flag has to be remembered.

Then, in LightBurn, connect to the TCP device on 127.0.0.1:4002 and work normally:
Start, jog-free positioning, homing, settings, any design. Press Ctrl-C here when done.

Two things this program deliberately still refuses, because they were measured to be
unsafe rather than assumed: jog commands ($J=) fail the session closed, and nothing the
bridge sends is ever fabricated or replayed.

The operator still owns the physical safety facts: eye protection, ventilation, a way
to cut power, clear travel, and hands clear. Only the machine's own power switch can
stop it once it is moving.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'tools' / 'pipeline_tcp_bridge.py'
PYTHON = sys.executable
DEFAULT_SECONDS = 25200                     # 7 hours, the bridge's own ceiling
SAFETY_LINES = (
    '护目镜 / 通风 / 灭火 / 可独立断电 / 行程净空 / 手离开',
    '只有设备自身的电源能真正停下机器 —— 出事先断它的电',
    '急停随时可用：python tools/emergency_stop.py --execute --log <新日志>',
)


def build_command(seconds=DEFAULT_SECONDS, log=None, test=False):
    log = Path(log) if log else ROOT / 'logs' / ('neje-' + datetime.datetime.now()
                                                 .strftime('%Y%m%dT%H%M%S') + '.jsonl')
    command = [PYTHON, str(BRIDGE), '--transparent', '--allow-transparent-bridge', '--execute',
               '--allow-initialization',
               '--laser-power-isolated' if test else '--laser-output-authorized',
               '--seconds', str(seconds), '--log', str(log)]
    return command, log


def audit_after(log):
    """Self-audit the capture once the session ends.

    One-command use should not require the operator to remember a second, separate
    verification step, so the bridge's own output is audited here and the verdict is
    printed in plain sight. Independent of the operator, and it never runs the machine.
    """
    path = Path(log)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] \
        if path.exists() else []
    if not any(row['event'] == 'pipeline_tcp_connected' for row in rows):
        print('  没有客户端连入过 —— 本次没有可审计的会话')
        return 0
    sys.path.insert(0, str(ROOT / 'tools'))
    from check_transparent_capture import audit
    try:
        result = audit(path)
    except AssertionError as error:
        print(f'  ✗ 审计未通过: {error}')
        print(f'    捕获保留在 {path} —— 不要删，先查清楚')
        return 1
    # A translated jog is longer than the jog itself, so the two byte counts are not
    # meant to be equal; what is asserted is that the forwards are exactly what the
    # documented policy makes of the client's bytes.
    print('  ✓ 审计通过: 客户端 {} 字节 → 按策略转发 {} 字节（点动会被翻译成相对移动）、'
          '设备 {} 字节、回传 {}、NEJE 写 {} 次、无伪造、无重放、干净收尾={}'.format(
              result['client_bytes_in'], result['client_bytes_forwarded'],
              result['device_bytes_staged'], result['bytes_delivered'],
              result['neje_writes'], result['clean_shutdown']))
    return 0


def port_in_use(port=4002):
    """True when a bridge is already serving that port.

    Double-clicking the entry twice must not produce a raw bind traceback: the second
    run simply tells the operator that the link is already up.
    """
    import socket
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def probe_device_mode(python=None, log_path=None):
    """Read the device's current application over BLE, before any bridge starts.

    `current_app` 3 is GRBL, which is the only mode this program can drive. Running it
    with the bridge already connected would contend for the radio, so --check is meant
    to be used when no session is up.
    """
    probe = ROOT / 'tools' / 'p0_probe.py'
    log_path = log_path or ROOT / 'logs' / ('mode-check-' + datetime.datetime.now()
                                            .strftime('%Y%m%dT%H%M%S') + '.jsonl')
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([python or PYTHON, str(probe), '--query', '--grbl-status',
                             '--laser-power-isolated', '--log', str(log_path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return None, f'无法完成检查: {(result.stderr or result.stdout).strip()[:200]}'
    apps = []
    for line in Path(log_path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get('kind') == 'system_info' and 'current_app' in row:
            apps.append(row['current_app'])
    if not apps:
        return None, '检查未读到设备信息'
    return apps[-1], f'current_app={apps[-1]}'


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--test', action='store_true',
                        help='Laser physically isolated: safe for checking the setup')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print the exact command without starting anything')
    parser.add_argument('--check', action='store_true',
                        help='Check the device mode over BLE and exit (use when no session '
                             'is running: it contends for the radio otherwise)')
    args = parser.parse_args()
    if args.check:
        if port_in_use():
            print('已有桥接在运行 —— 请先结束它再检查（检查会与桥接争用蓝牙）。')
            return 1
        app, detail = probe_device_mode()
        if app is None:
            print(f'  ✗ {detail}')
            return 1
        if app == 3:
            print(f'  ✓ 设备处于 GRBL 模式（{detail}）—— 可以开始')
            return 0
        print(f'  ✗ 设备不在 GRBL 模式（{detail}）—— 请断电重启设备后再试')
        return 1
    command, log = build_command(test=args.test)
    log.parent.mkdir(parents=True, exist_ok=True)

    print('NEJE 5 Plus over Bluetooth for LightBurn')
    print(f'  模式: {"激光隔离（试跑）" if args.test else "完整功能（激光已供电）"}')
    print(f'  会话: {DEFAULT_SECONDS // 3600} 小时  日志: {log}')
    if not args.test:
        print('  出光前请确认: ' + ' / '.join(SAFETY_LINES))
    print('  LightBurn: 连接 TCP 设备 127.0.0.1:4002，然后照常 Start / 回零 / 改设置')
    print('  结束: 按 Ctrl-C')
    print()
    if args.dry_run:
        print(json.dumps({'dry_run': True, 'command': command}, ensure_ascii=False, indent=2))
        return 0
    if port_in_use():
        print('已有桥接在运行 —— 直接在 LightBurn 里连接 127.0.0.1:4002 即可。')
        print('（若想换一个会话，先在它的窗口按 Ctrl-C）')
        return 0
    try:
        code = subprocess.call(command)
    except KeyboardInterrupt:
        code = 0
    except OSError as error:
        print(f'启动失败: {error}')
        return 1
    print()
    print('会话结束，正在独立审计本次捕获')
    return audit_after(log) or code


if __name__ == '__main__':
    raise SystemExit(main())
