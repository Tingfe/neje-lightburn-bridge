"""Windowed macOS entry with internal bridge/probe dispatch before any Tk window."""
import argparse
import json
from pathlib import Path
import platform
import sys

import neje
from app_version import VERSION, BUILD


def diagnostics(path):
    """Offline bundle checks: no scanner, device connection or GUI root."""
    import tkinter
    import _tkinter
    tcl = tkinter.Tcl()
    result = dict(version=VERSION, build=BUILD, frozen=bool(getattr(sys, 'frozen', False)),
                  executable=sys.executable, architecture=platform.machine(),
                  macos=platform.mac_ver()[0], python=platform.python_version(),
                  tcl=tcl.call('info', 'patchlevel'), tk=tkinter.TkVersion,
                  tcl_library=tcl.call('info', 'library'), tkinter_module=_tkinter.__file__,
                  logs=str(neje.data_root()), device_connected=False, offline_only=True)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return 0


def ensure_streams(force=False):
    """Windowed bootloaders may have no stdio; keep diagnostics outside the bundle."""
    if force or sys.stdout is None or sys.stderr is None:
        root = Path.home() / 'Library' / 'Logs' / 'NEJE'
        root.mkdir(parents=True, exist_ok=True)
        handle = (root / 'desktop-launch.log').open('a', encoding='utf-8', buffering=1)
        if force or sys.stdout is None: sys.stdout = handle
        if force or sys.stderr is None: sys.stderr = handle


def main():
    ensure_streams()
    # Children use this same Mach-O and bundle identity. They never make an extra
    # window, depend on an external Python, or try to execute a missing .py file.
    if sys.argv[1:2] == [neje.INTERNAL_BRIDGE]:
        return neje._run_bridge_in_process()
    if sys.argv[1:2] == [neje.INTERNAL_MODE_CHECK]:
        return neje._run_mode_check_in_process()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostics', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.diagnostics:
        if args.report is None: parser.error('--diagnostics requires --report')
        return diagnostics(args.report)
    ensure_streams(force=bool(getattr(sys, 'frozen', False)))
    import neje_app
    return neje_app.main()


if __name__ == '__main__':
    raise SystemExit(main())
