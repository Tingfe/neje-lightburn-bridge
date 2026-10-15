"""Start the native TCP/BLE bridge for LightBurn at 127.0.0.1:4002.

GRBL lines, native jogs, settings and realtime controls pass to the device.
Responses are never fabricated. Reset ends this generation; link loss/closing
this program does not stop buffered motion. Use independent power cutoff if a
software stop cannot be confirmed. --test describes physical isolation only.
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
DEFAULT_SECONDS = 25200


def data_root():
    """Where logs live.

    A frozen build unpacks into a temporary directory, so writing logs next to the
    program would lose them the moment it exits -- and the audit needs to read the
    capture back afterwards. Frozen builds therefore use a stable per-user location.
    """
    if getattr(sys, 'frozen', False):
        return Path.home() / 'Library' / 'Logs' / 'NEJE'
    return ROOT / 'logs'


LOG_ROOT = data_root()                     # 7 hours, the bridge's own ceiling
SAFETY_LINES = (
    '护目镜 / 通风 / 灭火 / 可独立断电 / 行程净空 / 手离开',
    '断连不会停止已缓冲动作；软件停止未确认时使用独立断电',
    '已连接时用 LightBurn Stop 或 GUI 软件停止；复位后需恢复位置基准',
)


FROZEN = getattr(sys, 'frozen', False)
INTERNAL_BRIDGE = '--internal-run-bridge'
INTERNAL_MODE_CHECK = '--internal-mode-check'


def build_command(seconds=DEFAULT_SECONDS, log=None, test=False, device=None):
    log = Path(log) if log else LOG_ROOT / ('neje-' + datetime.datetime.now()
                                                 .strftime('%Y%m%dT%H%M%S') + '.jsonl')
    # Frozen (PyInstaller) builds have no python interpreter or .py files next to them,
    # so the same executable re-enters itself to run the bridge in-process.
    launcher = [sys.executable, INTERNAL_BRIDGE] if FROZEN else [PYTHON, str(BRIDGE)]
    command = launcher + ['--transparent', '--allow-transparent-bridge', '--execute',
               '--allow-initialization',
               '--laser-power-isolated' if test else '--laser-output-authorized',
               '--seconds', str(seconds), '--log', str(log)]
    if device:
        command += ['--device', device]
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
    except (AssertionError, ValueError, KeyError) as error:
        print(f'  ✗ 审计未通过: {error}')
        print(f'    捕获保留在 {path} —— 不要删，先查清楚')
        return 1
    print('  ✓ 审计通过: 客户端 {} 字节 → 原生转发 {} 字节、'
          '设备 {} 字节、回传 {}、NEJE 写 {} 次、无伪造、无重放、干净收尾={}'.format(
              result['client_bytes_in'], result['client_bytes_forwarded'],
              result['device_bytes_staged'], result['bytes_delivered'],
              result['neje_writes'], result['clean_shutdown']))
    return 0


def port_in_use(port=4002):
    """Check local address occupancy without connecting to the business socket.

    This says nothing about the listener's identity or health. A connect probe would
    consume the bridge's single-use client and cause BLE initialization/cleanup.
    """
    import errno
    import socket
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as error:
            if error.errno == errno.EADDRINUSE:
                return True
            raise
        return False


def probe_device_mode(python=None, log_path=None):
    """Read the device's current application over BLE, before any bridge starts.

    `current_app` 3 is GRBL, which is the only mode this program can drive. Running it
    with the bridge already connected would contend for the radio, so --check is meant
    to be used when no session is up.
    """
    probe = ROOT / 'tools' / 'p0_probe.py'
    log_path = log_path or LOG_ROOT / ('mode-check-' + datetime.datetime.now()
                                            .strftime('%Y%m%dT%H%M%S') + '.jsonl')
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    launcher = [sys.executable, INTERNAL_MODE_CHECK] if FROZEN else [python or PYTHON, str(probe)]
    # Application information needs only the official system read. Do not claim
    # laser isolation or open/query the GRBL reliable route to check the mode.
    result = subprocess.run(launcher + ['--query', '--log', str(log_path)],
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
    parser.add_argument('--valid-for', type=float, default=DEFAULT_SECONDS / 3600,
                        metavar='HOURS',
                        help='How long the bridge stays available (default: 7 hours). Within '
                             'this window bounds one client session; restart explicitly after '
                             'disconnect or reset')
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
    seconds = int(max(1, min(args.valid_for, DEFAULT_SECONDS / 3600)) * 3600)
    command, log = build_command(seconds=seconds, test=args.test)
    log.parent.mkdir(parents=True, exist_ok=True)

    print('NEJE 5 Plus over Bluetooth for LightBurn')
    print(f'  模式: {"激光隔离（试跑）" if args.test else "完整功能（激光已供电）"}')
    print(f'  有效时间: {seconds / 3600:g} 小时  日志: {log}')
    print('  结束方式: 会话结束后本程序退出（LightBurn 断开重连需再运行一次）')
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


def _run_bridge_in_process():
    """Multi-call entry used by frozen builds: become the bridge."""
    sys.path.insert(0, str(ROOT / 'tools'))
    import pipeline_tcp_bridge
    sys.argv = ['pipeline_tcp_bridge.py', *sys.argv[2:]]
    return pipeline_tcp_bridge.main()


def _run_mode_check_in_process():
    import p0_probe
    sys.argv = ['p0_probe.py', *sys.argv[2:]]
    return p0_probe.main()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == INTERNAL_BRIDGE:
        raise SystemExit(_run_bridge_in_process())
    if len(sys.argv) > 1 and sys.argv[1] == INTERNAL_MODE_CHECK:
        raise SystemExit(_run_mode_check_in_process())
    raise SystemExit(main())
