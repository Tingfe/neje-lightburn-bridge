"""Offline checks of the final macOS artifact, shared by local builds and CI."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import runpy
import subprocess
import sys
import zipfile


def verify(folder):
    folder = Path(folder).resolve()
    app = folder / 'NEJE Bridge.app'
    binary = app / 'Contents/MacOS/NEJE Bridge'
    version = runpy.run_path(str(Path(__file__).resolve().parents[1]/'tools/app_version.py'))
    env = {key: value for key, value in os.environ.items()
           if key not in ('PYTHONPATH', 'TCL_LIBRARY', 'TK_LIBRARY')}
    report = folder/'bundle-diagnostics.json'
    subprocess.run([str(binary), '--diagnostics', '--report', str(report)], env=env,
                   check=True, timeout=30)
    row = json.loads(report.read_text())
    assert row['frozen'] and row['offline_only'] and not row['device_connected'], row
    expected_arch = os.environ.get('NEJE_ARCH') or row['architecture']
    assert row['architecture'] in ('arm64', 'x86_64', 'universal2'), row
    assert row['architecture'] == expected_arch, (row, expected_arch)
    assert row['version'] == version['VERSION'] and row['build'] == version['BUILD'], row
    for key in ('executable', 'tcl_library', 'tkinter_module'):
        assert Path(row[key]).resolve().is_relative_to(app), (key, row[key])
    with (app/'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    assert info['CFBundleShortVersionString'] == version['VERSION'], info
    assert info['CFBundleVersion'] == version['BUILD'], info
    icon_name = info.get('CFBundleIconFile', '')
    assert icon_name and (app/'Contents/Resources'/icon_name).is_file(), info
    subprocess.run(['/usr/bin/sips', '-g', 'format', str(app/'Contents/Resources'/icon_name)],
                   check=True, capture_output=True, timeout=30)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                   check=True, timeout=30)
    # Internal bridge dispatch must parse without constructing a Tk window or BLE link.
    subprocess.run([str(binary), '--internal-run-bridge', '--help'], env=env, check=True, timeout=30)
    archive = folder/f'NEJE-Bridge-{version["VERSION"]}-macOS-{expected_arch}.zip'
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        assert any(name.endswith('Contents/MacOS/NEJE Bridge') for name in zipped.namelist())
    metadata = dict(version=version['VERSION'], build=version['BUILD'],
                    architecture=row['architecture'], minimum_macos=info['LSMinimumSystemVersion'],
                    icon_file=icon_name,
                    zip=archive.name, bytes=archive.stat().st_size,
                    sha256=hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest(),
                    offline_checks_passed=True, hardware_tested=False,
                    developer_id_signed=False, apple_notarized=False)
    (folder/'build-metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')
    (folder/'SHA256SUMS.txt').write_text(f'{metadata["sha256"]}  {archive.name}\n')
    print(json.dumps(metadata, indent=2))


if __name__ == '__main__':
    verify(sys.argv[1])
