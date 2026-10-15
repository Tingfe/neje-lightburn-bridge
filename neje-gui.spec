# Native windowed app; keep Tcl/Tk, Python and BLE libraries in a sealed bundle.
import os
import sysconfig
import runpy
from PyInstaller.utils.hooks import collect_submodules

identity = os.environ.get('NEJE_CODESIGN_IDENTITY') or None
version = runpy.run_path('tools/app_version.py')
analysis = Analysis(
    ['tools/neje_desktop.py'], pathex=['tools'], binaries=[],
    datas=[('LICENSE', '.')],
    hiddenimports=collect_submodules('bleak'),
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True,
    name='NEJE Bridge', console=False, argv_emulation=False,
    bootloader_ignore_signals=False, strip=False, upx=False,
    target_arch='arm64', codesign_identity=identity, entitlements_file=None,
)
collection = COLLECT(exe, analysis.binaries, analysis.datas,
                     strip=False, upx=False, name='NEJE Bridge')
app = BUNDLE(
    collection, name='NEJE Bridge.app', bundle_identifier='io.github.tingfe.neje-bridge',
    version=version['VERSION'],
    icon='assets/NEJE Bridge.icns',
    info_plist={
        'CFBundleDisplayName': 'NEJE Bridge',
        'CFBundleVersion': version['BUILD'],
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': True,
        'LSUIElement': False,
        'LSMinimumSystemVersion': sysconfig.get_config_var('MACOSX_DEPLOYMENT_TARGET') or '27.0',
        'NSBluetoothAlwaysUsageDescription': '扫描并通过蓝牙连接 NEJE，以便 LightBurn 使用透明桥接。',
        'NSBluetoothPeripheralUsageDescription': '通过蓝牙连接 NEJE 激光设备。',
    },
)
