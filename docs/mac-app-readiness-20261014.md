# macOS应用打包状态

**后续状态已更新**：独立图形包NEJE Bridge 1.1.0/build 2已完成，参见[图形打包结果](gui-mac-package-result.md)与[测试指南](gui-mac-test-guide.md)。下面保留本轮早期对Terminal包装.app的只读审查，不代表最新图形包状态。

2026-10-14只读复查。当前候选位于dist/transparent-hardening-20261014/NEJE Bridge.app，约9.3MiB，arm64（Apple Silicon）。内部透明桥接CLI已经打包，帮助、干跑及内置模式检查帮助均通过。

.app当前为shell启动器，双击调用Terminal运行CLI，不是独立图形窗口；源码tools/neje_app.py的Tk控制台尚未打入此.app。本轮未打开.app，也未以打包产物连接BLE，不能把此前源码LightBurn验证当作二进制运行验收。

codesign --verify明确显示外层.app未签名；内部Mach-O只有adhoc签名，没有开发者TeamIdentifier。没有Apple Developer签名/公证或其他Mac的安装与运行验收。启动器还会在Contents/Resources写入run-in-terminal.sh，后续签名版本应改成不修改应用包的启动方式。

结论：能够生成本机候选.app和独立CLI，但完整图形Mac应用交付尚未完成。后续需图形入口打包、修正启动/权限及日志路径、真实BLE/LightBurn验收，再处理分发签名、公证。整体工程估算仍约90%，本轮没有新增实现或实机成果。

结构、签名命令原始输出及散列证据：evidence/mac-app-readiness-20261014/。
