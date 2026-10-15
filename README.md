# LightBurn ⇄ NEJE 5 Plus 原生透明桥接

将 LightBurn 的 TCP 连接 `127.0.0.1:4002` 转成 NEJE 5 Plus 的可靠 BLE 通道。目标是直接使用 LightBurn 的设计、Start、移动、暂停恢复和设备管理。当前整体验收估算约 **94%**；[界面与图片/Stop记录](docs/gui-polish-20261015.md)和[回零超时修正](docs/homing-status-watchdog-20261015.md)区分现场反馈、故障原始日志与新版软件验证。

```text
LightBurn → TCP 127.0.0.1:4002 → 原生 GRBL 流水 → NEJE BLE
```

## 使用

本机图形版 **NEJE Bridge 1.2.3 / build 6**：双击 `dist/NEJE-Bridge-1.2.3/NEJE Bridge.app`，点击“启动桥接”。首次发现唯一 NEJE 时自动选用，以后记住设备；多台设备时选择一次。LightBurn 正常断开后，真实空闲查询及清理通过即可自动准备下一次连接；退出程序时才停止待命。主窗口一排操作按钮和专用图标；保留 Stop 后 `$H` 回零状态超时修正。新包待独立实机复测。应用内含 Python、Tcl/Tk 与 BLE 依赖。安装与反馈清单见[正式测试指南](docs/gui-mac-test-guide.md)，断连修正见[专项记录](docs/lightburn-disconnect-lifecycle-20261015.md)。

“创建 LightBurn 设备”会在 LightBurn 已退出时直接建立可选择的原生 GRBL/TCP 条目，并备份配置；已有本机 4002 设备时改为“打开 LightBurn”。无需模板、文件导入或填写网络参数，也不会自动关闭未保存的 LightBurn 工程。


```sh
python3 -m pip install bleak
python3 tools/neje.py              # 原生透明模式
python3 tools/neje_app.py          # 源码图形控制台，需要可用 tkinter
python3 tools/neje.py --test        # 仅在激光供电确已物理隔离时使用
python3 tools/neje.py --dry-run     # 离线查看参数
```

Mac 可双击 `neje.command`。LightBurn 选择原生 **GRBL / Ethernet-TCP**，地址 `127.0.0.1`、端口 `4002`，工作区域 260×400mm，左前原点，S 最大值1000，关闭启动自动回零。已有本机配置已保存，详见[接入记录](docs/transparent-lightburn-setup.md)。

透明模式不要求导出文件、摘要匹配或查询白名单。桥接不改写 GCode、不伪造 `ok`、不自动重放；设备自身决定命令是否有效，真实 `error:` 会回传并释放对应输入额度。

## 点动与实时控制

- `$J=` 按原字节转发，保留 G20/G21、G90/G91、G53 等词。需要相对毫米点动时，发送器应明确指定 `$J=G91 G21 ... F...`。省略这些词会继承设备模态，桥接不猜测操作者意图。
- `?`、`!`、`~`、`0x85`及扩展实时字节走实时通道。设备真实状态用于显示和通信核验。未发送点动在取消时废弃，不制造其 `ok`；已开始发送的半行先完成，再发点动取消。
- **Stop / 0x18** 优先丢弃旧作业的未发送队列并执行复位。真实复位横幅和新鲜 Idle/Alarm 到达后，保留当前 TCP/BLE 连接，允许 LightBurn 重新初始化；旧作业不重放，坐标仍不可信，需要显式恢复位置基准。缺少真实确认仍按故障关闭。
- GUI 的软件停止使用当前所属 BLE 桥接进程，分别报告“请求已提交”和“真实状态已确认”。它不是独立硬件急停。

参照 [GRBL 官方点动规范](https://github.com/gnea/grbl/wiki/Grbl-v1.1-Jogging)及[串口实时控制实现](https://github.com/gnea/grbl/blob/master/grbl/serial.c)。本项目独立实现传输，未将 GPL 源码复制到 MIT 运行代码。

## 会话与恢复

一个桥接进程服务一个客户端会话。CLI 结束后重新运行；GUI 仅在进程正常退出、严格审计通过、way=0 清理确认且结束前有新鲜 Idle、之后未发新运动行时，自动准备下一会话。

审计失败、BLE 失联、未知清理或断开后未获真实 Idle/FS:0,0 时，GUI 禁止自动另起连接；检查设备状态后显式“重新连接”。正常断开且空闲/清理已核验时可自动待命，即使会话里曾执行 Stop；复位后坐标仍不可信，需要操作者确认或回零。连接健康时的 LightBurn Stop 保留原连接，与“关闭桥接”分开处理。绑定检测不建立 TCP 业务连接，重复打开入口不会消耗会话。关闭程序或无线断连**不会停止已缓冲动作**；软件停止无法确认时使用独立断电。

图片点击 Start 完全没动作时，先用 LightBurn 预览或保存 GCode 确认有雕刻路径。用户提供的白色透明 PNG 在普通图像模式下导出边界为 0×0；启用该图层“负片图像”后已生成标志扫描路径，背景保持透明。用户随后反馈该图片可正常加工、Stop 功能正常；会话日志也记录了复位后的连接保留与后续作业传输。这一设置只适用于该白色图，不应对所有照片默认开启。见[图片及 Stop 诊断](docs/lightburn-image-and-stop-result.md)和[现场反馈](docs/gui-polish-20261015.md)。

## 审计与测试

结束时核对原始通知、解码帧、CRC、连续滚动计数、GATT 写包、设备确认快照、终端归属及完整交付。缺少回传、计数错位、未完成或未知清理不会显示通过。开放中的捕获只能显示待完成。

```sh
python3 -m unittest discover -s tests -v
python3 tools/check_transparent_capture.py <会话.jsonl>
```

本轮结果记录在项目文件夹。公开假 BLE fixture 可随源码运行；依赖私有真机捕获的测试在缺失时明确跳过。测试通过不代表真实停止距离、BLE 数小时稳定或加工效果已经等效。

## 交付与边界

- 最新图形包：`dist/NEJE-Bridge-1.2.3/NEJE Bridge.app`；同目录 ZIP 可解压使用。arm64、最低 macOS 版本以构建元数据为准，本机 ad-hoc 签名；尚无 Developer ID、公证或其他 Mac 验收。旧 CLI/Terminal 包装保留供历史对照。
- 1.2.3 本机全量离线回归465项通过，应用图标、ZIP及签名已核验；本版还未独立重新连接真机。源图与生成程序在 `assets/` 和 `packaging/generate-icon.py`。
- 按换行组帧，支持 LF/CRLF；单行最多512字节，发送额度取自真实 Bf，最多64条在途。不是无界网络串口。
- 长运动、回零、驻留不套用初始化的8秒 `ok` 等待上限；可靠通道、状态查询及总会话时限继续生效。未获终端应答不会被推定完成。
- `$H` 为整轴回零；既有轴选择回零没有建立位置的记录保留。设备设置写入会持久化。
- 设备 `FS` 功率字段不能证明实际无出光；真实加工需要现场条件和成品核验。
- 首次使用新原生点动/停止实现，运动及故障恢复仍需实机对照。既有 USB 长运行不替代当前 BLE 路径的长期验收。

## 自动打包

已准备 [macOS GitHub Actions 流程](.github/workflows/macos-build.yml)：更新 main 的相关源码、版本标签或手动触发后，在 arm64 macOS runner 安装固定依赖、运行离线回归、生成 app/ZIP、检查签名及包内依赖，上传 ZIP、SHA-256 和构建元数据（保留 30 天）。本轮流程尚未推送，云端打包尚未验收；当前交付来自本机同一构建脚本。自动打包不等同于 Developer ID 签名、公证或硬件验收。

[MIT](LICENSE)。历史受审文件通路继续用于可复现实验，见[日常指南](docs/daily-use-guide.md)和[项目历史](docs/project-history.md)。
