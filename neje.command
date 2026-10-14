#!/bin/sh
# 双击本文件即可：NEJE 5 Plus 通过蓝牙对 LightBurn 可用（透明模式，全功能）。
# 需要在终端里做「激光隔离」试跑时：./neje.command --test
cd "$(dirname "$0")" || exit 1
PY=/Users/tyongs/neje-ble312/bin/python
[ -x "$PY" ] || PY=python3
exec "$PY" tools/neje.py "$@"
