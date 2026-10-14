#!/bin/sh
# 构建 macOS 可执行程序：单文件二进制 dist/neje + dist/NEJE Bridge.app
# 用法: ./packaging/build-macos.sh [python]
set -e
cd "$(dirname "$0")/.."
PY="${1:-python3}"
"$PY" -m pip install --quiet --upgrade pyinstaller bleak
"$PY" -m PyInstaller --onefile --name neje \
  --paths tools --hidden-import pipeline_tcp_bridge --hidden-import bleak \
  --collect-submodules bleak --console --noconfirm tools/neje.py

APP="dist/NEJE Bridge.app"
rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp dist/neje "$APP/Contents/Resources/neje"; chmod +x "$APP/Contents/Resources/neje"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>NEJE Bridge</string>
  <key>CFBundleIdentifier</key><string>io.github.tingfe.neje-bridge</string>
  <key>CFBundleExecutable</key><string>NEJE Bridge</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>NSBluetoothAlwaysUsageDescription</key><string>通过蓝牙连接 NEJE 5 Plus 激光雕刻机</string>
</dict></plist>
PLIST
cat > "$APP/Contents/MacOS/NEJE Bridge" <<'SH2'
#!/bin/sh
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
RUN="$(mktemp -t neje-bridge)"
printf '#!/bin/sh\ncd "$HOME"\n"%s/neje" "$@"\necho\necho "—— 会话已结束，可关闭此窗口 ——"\n' "$DIR" > "$RUN"
chmod +x "$RUN"
exec /usr/bin/open -a Terminal "$RUN"
SH2
chmod +x "$APP/Contents/MacOS/NEJE Bridge"
echo "构建完成:"; ls -lh dist/neje | awk '{print "  "$9" "$5}'; du -sh "$APP" | awk '{print "  "$2" "$1}'
