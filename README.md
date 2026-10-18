<img src="assets/NEJE Bridge.png" width="96" alt="NEJE Bridge">

# NEJE Bridge

简体中文 · [English](README.en.md)

让 Mac 上的 LightBurn 通过蓝牙使用 NEJE 5 Plus。程序在本机提供一个 GRBL/TCP 设备：`127.0.0.1:4002`，LightBurn 的作业和控制指令经桥接送到机器。

## 开始使用

1. Apple Silicon Mac 从 [macOS 自动构建](https://github.com/Tingfe/neje-lightburn-bridge/actions/workflows/macos-build.yml)下载 `NEJE-Bridge-macOS-arm64`，安装并打开 **NEJE Bridge.app**。1.3.0 双语版已生成工作流产物；截至 2026-10-18，正式 Release 仍为 1.2.4。应用尚未经过 Apple 公证，Intel 图形包未验证。
2. 确认 NEJE 已上电并处于 GRBL 模式，打开程序，点 **启动桥接**。
3. 首次使用时，先保存并退出 LightBurn，再点程序里的 **创建 LightBurn 设备**。以后直接选用该设备。
4. 在 LightBurn 连接设备：**GRBL / TCP、127.0.0.1、端口 4002**。确认收到真实设备响应后，就可以照常预览和开始作业。

正常断开 LightBurn 后，图形程序会核查设备和本次会话，核查通过才继续待命。关闭图形程序会关闭桥接；它不会重发未确认的作业，也不会伪造控制器的 `ok`。

## 已验证到哪里

图片和矢量作业已通过本项目蓝牙桥接实际加工；其中一次小房子切割约 **58 分钟**，作业和成品完成。用户反馈日常体验已难以与 USB 区分。当前工程验收估算 **96%**：该长作业结束后的 BLE 清理未确认，同设计、同条件的 USB/BLE 成品受控对照仍缺。数字是工程验收估算，不是加工成功率。证据和剩余项见[当前状态](docs/overall-progress.md)。

## 使用时注意

- **断开蓝牙或退出程序不会停止机器已缓存的动作。** LightBurn Stop 和程序的“软件停止设备”会请求控制器停止；若未收到确认，立即使用独立的设备电源切断方式。
- 停止复位或设备完全断电后，坐标基准可能失效。再次加工前先核对位置，必要时回零。
- 出光前确认相应波长护目镜、通风、可独立断电、行程净空，并现场看护。

## 继续阅读

| 想了解 | 文档 |
| --- | --- |
| 日常连接、停止、故障处理 | [使用指南](docs/daily-use-guide.md) |
| 桥接原理与能力边界 | [工作原理](docs/architecture.md) |
| 验收进度与证据 | [当前状态](docs/overall-progress.md) |
| 协议研究与实验记录 | [文档索引](docs/README.md) |

源码运行：`python3 tools/neje.py`（需先安装 Python 依赖）。本机打包：`sh packaging/build-macos.sh python dist/macos-app`。[MIT 许可](LICENSE) · [安全说明](DISCLAIMER.md) · [贡献指南](CONTRIBUTING.md)。
