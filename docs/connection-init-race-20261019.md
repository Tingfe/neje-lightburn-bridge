# 2026-10-19 桥接初始化失败：实机诊断

**结案更新：**本页按时间保留 2.4.0 固件下的失败诊断和当时结论。用户随后通过官方 Windows v6.8.5 将三项固件回退至 2.3.2；完整断电重启后，BLE `0x40` 状态和真实 LightBurn 初始化恢复。当前结果与 SHA 证据见[回退验收](firmware-rollback-decision-20261019.md)。2.4.0 入口为何失答仍未确定，不能泛化为所有设备均不兼容。

## 现场证据

- Mac 上运行的是本地 `NEJE Bridge.app` 1.3.0 和 LightBurn；10:00 前 `127.0.0.1:4002` 无监听，未发现今日作业字节提交。原始日志在 `~/Library/Logs/NEJE/neje-20261019T*.jsonl`，本轮独立模式读取在 `evidence/connection-20261019/mode-check.jsonl`（未纳入公开仓库）。
- 今天首次启动读到 `current_app=1`，部分启动读到 `2`，其余 8 次读到 `3`。对 `app=2` 的拒绝符合现有保护策略。用户随后确认无 USB/其他客户端，且设备电源线、USB 线都断开过再重启；这说明重启后的持续故障不能简单归因于客户端占用或只断开一条供电路径，仍不能倒推更早的应用 ID 变化原因。
- 8 次 `app=3` 启动均成功连接 BLE、完成 Verify 和 SystemRead，却在首个 `GRBL_COMM_STATUS` 等待 5 秒后报 `Missing grbl_comm_status`。没有启用可靠传输、没有转发 LightBurn 作业指令。
- 这 8 次原始通知中，`system_info` 帧刚返回，桥接即发送 `bridge_before`；SystemRead(ALL) 的后续 `app_info`、`system_flags` 均在 `bridge_before` **之后**才到。相比之下，2026-10-18 12:58 的成功启动在这两帧到齐后才发送 `bridge_before`，约 50 ms 后收到真实通道状态。日志支持“完整系统读回期间固件忽略下一请求”的时序假设；不能仅凭相关性断言固件内部原因。
- 10:00 用已安装包仅发送官方 Verify + SystemRead(ALL) 的独立探针：扫描、BLE 连接及完整系统读回成功，当前 `app=3`。这排除了“蓝牙完全不可用”，**不等于**通道状态已恢复。

## 时序假设及反证

曾推测多帧 `SystemRead(ALL)` 尾部未回完时发送通道状态请求会导致固件忽略。为验证此假设，临时实验构建将透明桥接的 `ByteStreamSession.start()` 改用官方 `SystemRead(INFO)`（`ff aa 08 10 01 01 00 55`），只读取应用 ID。旧只读 `ReliableSession` 保持原流程。此改动没有改变作业字节或失败清理策略。

用户确认设备已完整断电重启、没有 USB/其他客户端、目前静止且无加工/出光后，进行了两次独立实机探针：

- `evidence/connection-20261019/init-1p3p1.jsonl`：新 INFO 单帧成功返回 `app=3`，但首次 `GRBL_COMM_STATUS` 仍在 5 秒后超时。未启用可靠通道，也未发送 GRBL 字节。
- `evidence/connection-20261019/status-settle-1s.jsonl`：再次建连，在 INFO 返回后等待 1 秒才单独查询通道状态，仍超时。此次甚至没有尝试启用通道。原始通知显示 Verify 和 INFO 均有真实回复，状态查询没有任何回复。
- `evidence/connection-20261019/status-after-usb-read.jsonl`：USB 查询后以原无响应 GATT 写法再试一次，仍无状态回复。
- `evidence/connection-20261019/status-write-response.jsonl`：FFE9 特征同时支持 `write` 与 `write-without-response`；将同一状态帧改为 GATT 带响应写入，GATT 调用完成，但依然没有协议状态通知。
- `evidence/connection-20261019/dual-notify-status.jsonl`：同时订阅 FFEA、FFE9，状态查询在两条通知通道均无回复；超时后再发 SystemRead(INFO)，FFEA 正常返回 `app=3`。由此可见当时 BLE 连接/通知整体未失效，失效集中在 `0x40` 通道状态应答。
- `evidence/connection-20261019/status-direct-after-verify.jsonl`：连 SystemRead 都不发送，Verify 成功后直接查询一次 `0x40` 状态，仍无回复。因此本次失效也不是 SystemRead 本身造成的请求顺序问题。

因此 **多帧尾部竞态不是本次故障的充分解释**，1.3.1 只能视为实验构建，不能称作已修复版本。验证失败后已将临时源码和版本号回退到 1.3.0；实验包仅留作证据，不提供日常使用。GRBL 组无响应的具体原因仍待查。2026-10-18 12:58 的成功会话使用同一个 `ff aa 08 40 01 00 00 55` 查询帧，返回 `way=0`；成功作业结束又验证 `way=0/app=3`，不支持“最后一次桥接遗留 way=2”的直接判断。

临时改动的离线专项回归 5 项通过，完整回归在项目 Python 3.12/Tk 环境及允许本机测试端口监听的条件下运行 470 项，全部通过；实机反证后源码已回退，故这些测试仅说明临时改动没有破坏离线测试，不代表连接修复。`git diff --check` 通过。macOS 1.3.1 实验包已构建，离线校验与本机临时签名结构检查通过：`dist/NEJE-Bridge-1.3.1/NEJE-Bridge-1.3.1-macOS-arm64.zip`，SHA-256 `c31d546bfcbe5724e8447280cb63741a15d7432cd270c917b3181a9ea051087f`。该包未做 Developer ID 签名、公证；现场初始化实测**失败**，不应替换日常使用的 1.3.0。

## USB 对照与当前判断

用户接上 USB 后，Mac 枚举 `/dev/cu.usbmodemNEJE_3_Series1`，`lsof` 未发现占用者。固定只读 `$I`、`?` 一次均有真实回复，记录在 `evidence/connection-20261019/usb-readonly.jsonl`：`[VER:1.1f.NEJE build version:v2.4.0:]`、`ok`；`<Idle|MPos:0.000,0.000,0.000,0.000|Bf:100,1500|FS:0,0|Pn:P|...>`。探针关闭串口，未发送运动、回零、出光或设置写入。前一天成功桥接期间也曾报告 `Pn:P`，故它不能单独解释今天的失败。

**USB GRBL 核心可用，BLE Verify/SystemRead 可用；BLE 0x40 状态组暂时无回复。** 这不足以判断是固件模式状态、蓝牙端组处理器、Mac/设备 BLE 交互，还是其他硬件问题。没有真实 `GRBL_COMM_STATUS` 前，不启动 LightBurn 蓝牙作业、不猜测 `way`；本项目探针不发送 SetStatus、切换应用或复位命令。官方[连接故障排查](https://wiki.neje99.com/en/troubleshooting/connect)建议排除客户端占用；官方[桥接说明](https://wiki.neje99.com/zh/manual/conn-bridge)指出可靠模式与普通 GRBL 模式切换有关。

## 官方客户端模式对照

用户确认激光模组供电物理隔离并允许官方客户端连接/退出和模式往返后，启动 Mac 上已安装的 `NEJE 3.app`。客户端自动连接，短暂显示 `Homing, Please Wait...`，随后显示 `Status: IDLE` 和激光模组温度；操作员未点击 Home/Start/Stop。由于这超出了计划中的纯连接行为，立即退出客户端。用户现场确认**确实发生回零运动**，完成后设备静止、无异常。该动作由官方客户端启动流程触发，并非本项目探针发送；后续不得把启动 NEJE 3 当作纯只读检查。

退出后独立 BLE Verify + SystemRead(INFO) 读到 `current_app=2`，原始记录为 `evidence/connection-20261019/mode-after-neje3.jsonl`。这证明客户端已将设备切入 NEJE 应用。用户继续保持激光供电隔离，把设备主电源和 USB 全断至少 10 秒后仅接回主电源；独立探针 `evidence/connection-20261019/status-after-mode-roundtrip.jsonl` 确认已回 GRBL app=3，`0x40` GetStatus 仍无回复。模式往返未恢复桥接。

用户现场观察到重新上电后主板**红灯亮、黄灯灭**。官方[GRBL 连接桥说明](https://wiki.neje99.com/zh/manual/conn-bridge)称启用可靠传输模式后黄灯会亮；这使“设备仍明显停在可靠模式”的解释缺少现场灯光支持，但灯光观察不能替代缺失的 0x40 状态帧，`way` 仍未知。

公开的 NEJE 软件发布页曾记录修复“NEJE 5 主板在蓝牙连接时的连接测试失败”（Windows 客户端 v6.6.5），但[当前公开发布说明](https://neje99.com/software/neje-control-software-for-windows.html)没有直接说明 v2.4.0 固件的这项 0x40 失答问题，不能据此推断是同一故障或推荐刷写。已准备[厂家排障摘要草稿](neje-support-report-20261019.md)，未对外发送。

## 当前处理结论

- **蓝牙桥接未恢复。** 在取得真实 `0x40` 状态帧前，保持失败即关闭的行为，不绕过初始化、不伪造 `way` 或 `ok`。
- USB 的 `$I/?` 只读对照正常，可作为后续 USB 直连排查的依据；本轮未测试 LightBurn 经 USB 完成作业，不能把该只读结果写成加工验证。
- 进一步恢复需 NEJE 官方确认 v2.4.0 的通道诊断或无刷机恢复办法，或在另一主机上复现实验以区分 Mac BLE 栈与设备侧。现有证据不足以修改固件或发未知命令。
- `tools/check_connection_init_host.py` 提供离线默认、显式启用的只读复现入口；原始日志保留在忽略目录 `evidence/connection-20261019/`。诊断摘要尚未发送厂家。

## 进一步线索：官方 Windows 桥接的启动顺序

用户指出 NEJE 3 客户端仍可蓝牙连接。实测它连接后把应用切到 ID 2；本项目桥接要求 ID 3，并依赖 0x40 可靠 GRBL 通道。因此 NEJE 3 可连接只能证明 BLE 系统/NEJE 应用工作，不能证明 0x40 通道可用。

对照已存档的官方 Windows v6.8.5 .NET 方法 `evidence/grbl-research/il/0433-MoveNext.il.txt`（`GrblCommWorker.Start`）：在可靠模式下，官方实现先发送 `GrblCommExSetStatus(connection way)`，随后才调用 `ReadGrblCommStatus`。本项目 `ReliableSession.start()` 则先读 `GetStatus` 并要求 `way=0`，再发送 `SetStatus(BLE)`。这是**真实的顺序差异**；此前在 2.3.2 上本项目的读先行流程成功过，但 2.4.0 上即使采用官方顺序仍失答。

已准备独立探针 `tools/probe_official_start_order.py`，默认只输出离线计划；显式执行时仅在 Verify=model55、SystemRead=app3 后尝试一次官方顺序：SetStatus(BLE=2) → GetStatus → SetStatus(0) → GetStatus → SystemRead(INFO)。不发 GRBL 字节、G-code、运动、回零或出光命令。若启用后的状态仍无回复，仍尝试一次关闭；关闭状态也无回复则记录为**路由未知**，不把 BLE 断开视为清理成功。执行门槛包括设备静止、激光物理隔离、没有 NEJE 3/LightBurn/桥接占用，以及针对临时通道切换的现场授权。离线测试 `tests/test_probe_official_start_order.py` 4 项通过。

用户随后确认设备静止、激光模组供电物理隔离、NEJE 3/LightBurn/桥接均已断开设备，并授权该一次探针。首次在受限执行环境运行时，Bleak 立即返回 `NO_BLUETOOTH`，原始记录 `evidence/connection-20261019/official-start-order.jsonl`；**未连接设备、未发任何 BLE 帧**。尝试申请本机蓝牙权限三次，自动审批均因“Selected model is at capacity”未能完成，命令没有启动；这不是安全性判定，也不是设备故障。未绕过审批执行；随后审批服务恢复，才进行了下一段实机实验。

2026-10-19 17:59（北京时间），本机蓝牙权限审核通过，按既定探针进行了**一次实机实验**；原始记录 `evidence/connection-20261019/official-start-order-host.jsonl`。Verify 真正返回 model55，SystemRead(INFO) 真正返回 app3。随后发送 `SetStatus(BLE=2)` 一次，等待 200 ms 后发送 `GetStatus`；5 秒内没有任何通知。异常清理发送 `SetStatus(0)` 一次，等待 200 ms 后再次 `GetStatus`；仍无通知并超时。日志中仅有 Verify、SystemRead 的两条通知，六次写入严格为 Verify、SystemRead(INFO)、SetStatus(2)、GetStatus、SetStatus(0)、GetStatus；**没有发送 GRBL 字节或运动/出光命令**。

这次实验排除了“仅因本项目先读状态而未按官方顺序启用”作为足以恢复连接的解释。由于 SetStatus 不提供可验证的独立确认，关闭命令虽已发送，**最终路由状态仍未知**；程序按失败处理，没有继续作业或自动重试。后续不得把本次实验写成关闭成功，也不要在此基础上反复尝试启动。待现场灯光观察与厂家/跨主机诊断补全。

用户随后现场观察主板仍为**红灯亮、黄灯灭**。这与黄灯未显示可靠模式启用相符，但不能补足缺失的 0x40 路由确认帧；记录仍标注路由未知。原始日志 SHA-256：`10f24d96f7303f43296ec23995755f93cc0e83e4578c66230036cc9feadc1ef8`。项目 Python 3.12 环境中的专项回归 19 项通过，`git diff --check` 通过；这些是离线代码检查，不改变实机失败结论。

## 固件版本变化：主要新线索

复核原始帧后纠正先前“同一固件”的错误判断：

| 时间（北京时间） | 系统读回中的 System / NEJE / GRBL 版本 | 0x40 状态 | 结果 |
| --- | --- | --- | --- |
| 2026-10-18 12:58 | 2.3.2 / 2.3.2 / 2.3.2 | 真实返回 way=0 | 桥接可启动并完成作业；`~/Library/Logs/NEJE/neje-20261018T125844336339.jsonl` |
| 2026-10-19 首次完整读回 | 2.4.0 / 2.4.0 / 2.4.0 | 无回复 | 连接失败；`evidence/connection-20261019/mode-check.jsonl` |
| 2026-10-19 17:59 | SystemRead(INFO) 确认 app3；之前 USB `$I` 返回 GRBL 2.4.0 | 官方启用顺序后仍无回复 | 清理状态未知；`evidence/connection-20261019/official-start-order-host.jsonl` |

用户确认成功作业后确实升级过固件。**版本变化与故障出现高度相关**，但尚不能区分 2.4.0 的协议变更、升级后状态异常或个别升级失败；未找到官方 2.4.0 详细变更说明，也未核实官方是否提供可安全回退的 2.3.2 包。不能在未确认设备型号、映像、设置保留和回退流程的情况下刷写。厂家问题已补到[诊断摘要](neje-support-report-20261019.md)。

## 官方 GRBL 配置页与 NEJE Neo 对照（2026-10-19）

用户确认升级是通过从 `neje.club` 下载的 **Windows NEJE Neo v7.0.0** 完成。这里 `7.0.0` 是上位机版本，设备实读 `2.4.0` 是固件版本，不应混用。[NEJE 下载页](https://neje.club/)当前给出的 Windows 安装文件名为 `neje_neo_setup.exe`。已保存当前公开安装包至 `evidence/connection-20261019/neje_neo_setup-20261019.exe`，大小 12,162,864 字节，SHA-256 `807d0eb72a5b1cd6eb5b899531553481427a974a7d501e4d0fad9c25683da5f3`；静态 PE 资源含 `NEJE Neo` 和 `7.0.0`。此包**未运行**，也无法证明它与用户当时运行的文件逐字节相同。

用户指出的[NEJE 官方 GRBL 配置页](https://wiki.neje99.com/en/manual/grbl-config)说明：GRBL 文本命令 `$40` 控制空闲对焦光亮度，默认 `0.200`（0.2%）；`$41` 是温度自动汇报间隔，`$42` 是自动休眠时间。该页另称升级 GRBL 固件会复位 GRBL 配置。升级前的项目实录 `evidence/reviewed-job-bridge/usb-preflight-01.jsonl` 中 `$40=0.200`；升级后的完整 `$$` 尚未读取，因此**不能断言具体哪个设置已改变**。尤其 `$40` 与本次失答的 **BLE 二进制命令组 `0x40`** 只是编号相似：配置页没有说明 `0x40` 帧协议，也不能据此推断修改 `$40` 可修复桥接。

用户按记忆没有在 Windows NEJE Neo v7.0.0 中看到“GRBL Bridge / 连接桥”入口；这不是功能不存在的证明。[官方连接桥页](https://wiki.neje99.com/zh/manual/conn-bridge)描述的是 Windows NEJE 控制软件桥接及 GRBL v2.0.0 引入的可靠模式，未明确覆盖 Neo v7.0.0 / 固件 2.4.0。公开搜索也未找到这组版本的桥接协议变更记录。因此旧版 v6.8.5 的实现只能作对照，不能视为新版规范。

下一步优先取得 NEJE Neo v7.0.0 对固件 2.4.0 的 GRBL 连接桥说明或厂家答复，核实二进制 `0x40` 帧是否仍受支持、是否改了初始化或通知端点。若设备 USB 可用，再只读查询一次 `$$` 并与升级前记录比较；此项用于确认行程、速度、激光模式等加工参数，**不作为 BLE 故障的直接修复**。在新固件桥接状态没有真实应答前，不绕过初始化、不刷回旧固件、不写 GRBL 设置。

## Neo 固件包版本与设备分项版本（已核对）

用户明确：NEJE Neo 软件把 **`v3.0.0 调试版3`** 标为**当前设备固件**，不是“可升级目标版本”；Neo 软件本身也有“调试版”标识。用户只通过 Windows Neo 升级过。先前把此标签与设备读回 2.4.0 视作冲突，下面的公开配置已解释两层版本的关系。

2026-10-19 **18:20 北京时间**，用户确认设备在 Mac 范围内、Windows Neo 已断开；`tools/p0_probe.py` 仅发送 Verify 与 SystemRead(ALL)，没有 `0x40` 查询、GRBL 字节、模式切换或固件操作。沙箱内第一次运行因 `NO_BLUETOOTH` 在扫描前结束，无设备通信；随后本机蓝牙权限核验成功。原始记录 `evidence/connection-20261019/version-readback-after-neo-host.jsonl`，SHA-256 `c9d59605743bdb12f73778e1327db2f1e3a8a4ba8ae0996187d99b6f94b8936c`。真实回复：model 55、硬件 2.0.1、app 3；System/NEJE/GRBL **仍分别为 2.4.0 / 2.4.0 / 2.4.0**。`system_info` 与 `app_info` 两帧分别和早先 `mode-check.jsonl` **逐字节相同**，支持读到的是同一设备且版本未变。订阅正常退出，BLE 已断开；未试探失答的桥接命令。

2026-10-19 **18:42 北京时间**，只读标准 GATT Device Information 特征值，未发送任何设备协议写入。真实读数：蓝牙模块型号 `TD5322A`、硬件 `1.0.0`、固件修订 `V3.1.2`、厂商字段 `TD5322A_V3.1.2`；与旧[协议记录](protocol.md)一致。原始记录 `evidence/connection-20261019/gatt-versions-after-neo.jsonl`，SHA-256 `81f5bd30e4ef5c0e0327aa50fdb7497267f21810d17927a3e2eb2bc54f4313bb`。因此 Neo 的 `v3.0.0` 也不是标准 GATT 蓝牙模块固件修订号。

从 [NEJE 下载页](https://neje.club/)取得当前公开 Android Neo APK（仅静态分析，**未安装/运行**；`evidence/connection-20261019/neje_neo-20261019.apk`，SHA-256 `54fbe5f5307801d866262f7eff69d270b0c41a4320bb63e52da4064e9e1e38f8`），其 Flutter 程序中引用 `https://api.neje99.com/v1/configs/neje_neo/config.json`。2026-10-19 读取该 NEJE 公共配置并保存为 `evidence/connection-20261019/neo-public-config-20261019.json`（SHA-256 `7cad397fa83ff2725a94d916fd1dbcd504c4f8cdeb6ace53bb300fe09c61d8db`）。配置明确给出：

| Neo 显示的固件包 | 配置中的 `bundle_type` | 包内分项 `fw_bundle` |
| --- | --- | --- |
| `v3.0.0 调试版3` | `debug_3` | `system_v2.4.0, grbl_v2.4.0, neje_v2.4.0` |

同一配置把 Windows Neo 上位机列为 `v7.0.0 / debug_2`，说明**上位机版本、固件包版本、包内应用版本、BLE 模块版本是四个不同字段**。配置中的 `debug_3` 备注是“带 crc 异常提示”，但没有说明旧 BLE GRBL `0x40` 通道是否改动。由此，Neo 的“当前固件 `v3.0.0 调试版3`”与实读三个 2.4.0 **一致，不再是版本冲突**；桥接失答仍需独立诊断。配置还列出未启用的 release 包及一个未纳入所选 bundle 的 System 2.4.1，不能据此建议更新或回退。

随后仅**下载文件并作离线校验**，从未运行刷写工具、向设备传送固件或修改设置。当前 NEJE 配置明确引用的官方 `grbl_v2.4.0.bin` 文件大小 38,912 字节，SHA-256 `4b61c9c2577b1fd396afd4fefba6a393acada3399b1da77fcdaf96214dbb9a26`；整文件 CRC32/MPEG-2 为 `0x3BCEC150`，与配置中的 `bin_crc` 一致。官方服务器上也仍可读取三个 `v2.3.2` 文件（System/NEJE/GRBL），已仅为静态比对保存于 `evidence/connection-20261019/`：

| 旧分项文件 | 字节 | SHA-256 | CRC32/MPEG-2 |
| --- | ---: | --- | --- |
| `system_v2.3.2.bin` | 44,032 | `6d6151b8bf3263587059ecdefbb1f6fd78702acfac98c11a086f047c89c23b15` | `0x7CFD8889` |
| `neje_v2.3.2.bin` | 37,888 | `3a21ab29fcc09f7ffada1669d748772061004233aa6042a1e748f8d08380f9bb` | `0x5BC24BDD` |
| `grbl_v2.3.2.bin` | 37,888 | `50a7f375cd832575b55dfd42e8477bbc0c6ac2bf94a7bd67d13a3efd2c2c5788` | `0x0C9E9C60` |

**文件存在不等于可安全回退。** 当前公共配置没有启用的 2.3.2 bundle、没有这台 NEJE 5 Plus 主板 2.0.1 的回退说明，也没有 2.3.2 的官方校验元数据和升级/降级后设置处理说明。需 NEJE 确认三分项版本配套、刷写顺序、失败恢复及 GRBL 配置备份/复原后，才能考虑回退；本项目没有执行或安排设备刷写。

## 2026-10-19 补充：旧版 Windows 固件目录

上段“没有 2.3.2 官方校验元数据”仅指 **Neo 配置**；随后检查 NEJE 官网仍提供的 Windows v6.8.5。其程序字符串所列主配置指向固件目录 `https://api.neje99.com/v1/configs/neje3_firmware/config.json`。该目录截至本次读取时把 System、NEJE、GRBL 2.3.2 **全部标为启用**，各项 `match_device_list` 包含 NEJE 5 Plus；三个 CRC32/MPEG-2 与已存档文件相符，目录 CDN 与先前下载源的文件 SHA-256 逐一相同。原始目录保存于 `evidence/connection-20261019/legacy-windows-firmware-config-20261019.json`，SHA-256 `8f38d9bafc5ea7cce5a97ddac4a04ae538db72098599de93086a91e27ecc104d`。用户现场只看到 Neo v7.0.0 的 `v3.0.0 调试版3`，没有 2.3.2 选项；随后在旧客户端 v6.8.5 更新中心**确认可选择三项 2.3.2**，但没有刷写。回退门槛和后续验收见[专项决策](firmware-rollback-decision-20261019.md)。
