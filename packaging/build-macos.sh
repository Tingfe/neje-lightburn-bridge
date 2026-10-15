#!/bin/sh
# Build the native windowed controller, including its BLE bridge and Tcl/Tk.
# Usage: ./packaging/build-macos.sh [python] [output-directory]
set -eu
cd "$(dirname "$0")/.."
BUILD_PYTHON="${1:-python3}"
GUI_VERSION="$("$BUILD_PYTHON" -c 'import runpy; print(runpy.run_path("tools/app_version.py")["VERSION"])')"
GUI_DIST="${2:-dist/NEJE-Bridge-$GUI_VERSION}"
GUI_WORK="build/macos-gui-$GUI_VERSION"
"$BUILD_PYTHON" -c 'import PyInstaller, bleak, tkinter, _tkinter'
PYINSTALLER_CONFIG_DIR="$(pwd)/$GUI_WORK/pyinstaller-cache" \
"$BUILD_PYTHON" -m PyInstaller --noconfirm --distpath "$GUI_DIST" \
  --workpath "$GUI_WORK" neje-gui.spec
GUI_APP="$GUI_DIST/NEJE Bridge.app"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$GUI_APP"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$GUI_APP" "$GUI_DIST/NEJE-Bridge-$GUI_VERSION-macOS-arm64.zip"
"$BUILD_PYTHON" packaging/verify-macos.py "$GUI_DIST"
/usr/bin/du -sh "$GUI_APP"
