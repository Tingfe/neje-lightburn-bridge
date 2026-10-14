> 这是项目的历史技术交接文档（原 README）。当前对外说明见 [项目 README](../README.md)。
> 文中提到的 `evidence/` 目录未随仓库发布。

# LaserLink / NEJE 5 Plus

macOS 原生 BLE → LightBurn GRBL 桥接调查项目。

**当前状态（2026-10-13 E91）：原生 GRBL 直接点击 Start 路径已打通——真实 LightBurn 从受审工程点击 Start 生成的 18 条命令，与受审文件及 Pi USB 执行字节逐字节一致，独立审计与现场观察通过；设备参数自动读取（`$$` 只读）同步打通。整体约100%（100/100）：**七项验收全部闭环** —— 初始化、参数/行程、通用作业传输、控制与停止、故障恢复（半行/失联/掉电/数小时稳定）、实际加工 USB/BLE 对照（卡尺实测）、日常交付入口（图形化面板 + 日常启动器）。**数小时稳定**：Pi USB 连续 2 小时 13 分、118,272 状态样本、249 万字节核验、审计 `passed`。**

> **日常使用（一键）**：`python tools/neje.py`，或在 Finder 里双击 **`neje.command`** —— 零参数、零配置，蓝牙透明桥接即开；LightBurn 连 `127.0.0.1:4002` 后**照常 Start 自己的设计、回零、改设置、点动**，会话结束时**自动独立审计**并打印结论。
>
> 详见[日常使用指南](daily-use-guide.md)（含一键用法、支持的边界、安全规则、故障与急停）与[e 级出光方案](level-e-test-plan.md)（首次真实出光前的先决条件与低功率单段配方）。
>
> 需要「预先受审」的旧路径仍可用（`tools/daily_use.py`：导出 GCode → 审阅 → Start），适合想要每份作业都有上限声明的场景。

用户现已持续允许有效行程内移动和本次上下文中的零功率停止验证，不再逐个小行程重复确认，详见[当前操作授权](operating-authorization.md)。旧文档的次数额度和待授权状态均属历史记录。

> **E91（完成）**：Pi USB、运行GCode、**原生直接 Start** 三路执行同一受审文件（18 条命令、摘要 `b189a348…`），作业字节 **18/18 逐字节一致**，各自独立审计与现场观察通过；同时打通设备参数自动读取（桥接白名单新增 `$$` 只读 + LightBurn `Fetch Settings on Connect`），并实证一次未审阅设计被 fail-closed 拒绝（零转发、无运动）。更正记录：Start 置灰的真实原因是**当时未载入工程设计**，早期「缺少激光器来源」的假设已证伪。**控制 18→19/20，总分 80→81/100。** 另记两个确定性缺陷：墙钟到期必然要求显式恢复、LightBurn 自动重连与单客户端设计冲突。详见[结果文档](native-start-result.md)与[方案](native-start-plan.md)。

> **状态权威来源**：进度分数以[整体完成度估算](overall-progress.md)为唯一权威；验收矩阵以[最终目标矩阵](usb-parity-goal.md)为准；逐轮实验台账见[实验记录](experiments.md)；设备操作权限见[当前操作授权](operating-authorization.md)。**本页其余段落均为历史副本，如有冲突一律以上述文件为准。**

此前结果：[USB运动中停止](usb-motion-stop-hardware-result.md)、[选择性系统信息关闭修复](selective-close-info-result.md)。该轮263项回归通过；真实BLE仅一次状态读取及关闭审计通过，原可选通知半帧在这次实测中为0字节。没有把只读关闭算作真实LightBurn停止验收。

此前进展：[真实LightBurn运动中Stop与单独恢复](lightburn-motion-stop-hardware-result.md)、[BLE圆弧/G0与USB同文件对照](ble-arc-rapid-parity-result.md)。见[最终目标矩阵](usb-parity-goal.md)、[整体完成度](overall-progress.md)和[管理入口说明](lightburn-managed-session-guide.md)。

最新实机：[长文件同源对照及管理入口](long-job-parity-result.md)。最终Idle/X0/Y400、M5/S0，way0/app3、管理锁与进程/端口释放已核验。本次为生成的压力文件，不是原生LightBurn导出或实际加工。

最新故障实测：[真实LightBurn作业断连与独立清理](job-disconnect-result.md)。断连不保证停机，写入返回不保证设备接受；不以流传输“完成”文案判定故障作业成功。管理CLI已明确提示失联结果未知，LightBurn自身文案未修改；下一步补受控恢复、输入半行及更长时间稳定性。

最新恢复：[管理入口显式恢复及真实再次连接](managed-recovery-result.md)。通信与管理恢复已通过；故障后Ready及恢复连接中Disconnected的界面不一致已保存，未伪造应答。

最新配置及实机：[原生GRBL状态对照与恢复后矩形](native-grbl-ui-result.md)。当前受审文件运行选择 `LaserLink NEJE5 Native GRBL`，已在本机保存；正常/断连/恢复后标签本轮一致，原Custom GCode异常记录保留，不能推定所有界面流程已通过。

## 先前阶段记录

以下状态和权限描述保留为历史；当前结论以上文及其链接为准。

> **本节内出现的「约65%」「额度已用完」「待授权」等均为 E52–E63 期间的当时记录，不是当前状态。**当前进度为 **80/100**。为保留历史原貌，此处不逐条改写，冲突时以[整体完成度估算](overall-progress.md)、[最终目标矩阵](usb-parity-goal.md)、[实验记录](experiments.md)为准。

**当前状态（2026-10-13）：Mac原生BLE→TCP→真实LightBurn初始化、一轴固定作业及原生10×10mm二维零功率文件已完成USB对照。最新二维两路计数/响应/控制器路径范围/终点/清理审计通过，现场确认两次完整返回、无异常及出光；圆弧、移动G0、长作业、作业故障及实际加工尚未验收。**

此前控制实测：[USB/真实LightBurn BLE暂停、恢复及空闲停止对照](controlled-job-parity-result.md)。两路各一次固定10mm零功率作业通过真实协议及完整收尾审计，用户确认正常暂停、恢复及返回，无其他轴异常、无出光。LightBurn回程暂停约18秒，偏离计划约2秒，已如实记录。193项回归通过；通道、串口、TCP及应用关闭已核验。本轮授权次数已用完。

最新实机：[原生二维文件USB／真实LightBurn BLE对照](xy-native-hardware-result.md)。两路各一次10×10mm、40mm路径/F60/S0，16条命令及160作业字节一致，控制器路径范围/终点/模态及完整关闭独立审计通过；现场确认两次完整返回、无异常及出光。240项回归通过，两次运动授权已用完，整体约65%。

最新离线进展：[真实LightBurn曲线导出、重开及USB/BLE模拟对照](lightburn-curve-native-result.md)。78条/1256作业字节，64段圆周G1加两段进出线；开源几何参照与两路独立模拟审计通过，尚未实机执行，不计G2/G3验收。

最新实机：[原生曲线USB／真实LightBurn BLE对照](curve-native-hardware-result.md)。78条/1256字节，两路独立审计及现场圆形返回观察通过，无明显卡顿、异常及出光；终点/收尾/关闭通过。实际为G1短线，不计G2/G3验收。本次两次运动额度已用完，整体约65%。

最新控制准备：[USB／BLE运动停止独立审计及当前USB基线候选](ble-motion-cancel-audit-result.md)。244项完整回归通过，模拟停止/断链与真实验收分开，Pi新包离线核验通过；未新增设备动作，现场授权待确认。

日常入口候选：[管理会话CLI操作说明](lightburn-managed-session-guide.md)。默认离线预览，实际启动复用现有授权门槛；自动固定作业副本、保存日志并独立审计，故障后阻止同根目录直接再启动。254项完整回归通过，本轮未新增设备连接或动作，详见[结果](lightburn-managed-session-result.md)。

管理入口状态更新：[只读阶段显示及实际拥有者锁核查](lightburn-managed-status-result.md)。258项完整回归通过；该轮界面因Mac锁屏未启动设备会话；E71解锁后已完成初始化实测。

最新管理入口实测：[真实LightBurn两次初始化、正常退出及显式再次启动](managed-init-hardware-result.md)。用户已解锁，两份原始捕获独立审计、状态/实际拥有者锁与关闭通过；没有新作业或运动，停止现场问题仍待答复，整体约65%。

整体目标完成度估算：[约65%](overall-progress.md)（**历史值**；当前为 80/100）。运动中停止、作业中意外BLE断连、通用长作业、实际加工和日常使用入口仍待完成。

此前实现：[控制状态机](control-state-machine-result.md)；此前可靠性修复：[控制采集及512行只读查询](lightburn-control-capture-result.md)。

此前软件候选（当轮239项回归通过）：[快速定位零功率候选及预检对照](xy-rapid-result.md)。含移动G0与圆弧的受审文件两路模拟通过；速度/加速度必须在首次作业提交前重新读取匹配，旧授权不扩大。G0不受F60限制，本轮没有设备动作。该轮Mac锁屏；E61已解除并完成原生二维导出。

此前软件进展（233项回归通过）：[XY圆弧零功率候选及开源对照](xy-arc-result.md)。顺逆时针、I/J/正负R及整圆审阅通过，35份几何对照及三份USB/BLE模拟独立审计通过；未执行实机曲线，新增独立圆弧门槛。该轮原生二维导出待解锁；E61现已完成，实机停止问题仍待答复。

此前软件进展（225项回归通过）：[二维零功率直线候选及开源几何对照](xy-linear-result.md)。10mm和25mm闭合轮廓的USB/BLE模拟分别审计通过，34份直线几何与固定版cncjs参照一致；二维执行有独立范围门槛，原一轴授权不扩大。本轮未连接设备。此前[同文件及断连候选](reviewed-file-parity-result.md)保留；USB停止现场确认仍待答复。

最新应用进展：[真实LightBurn二维原生导出及同文件离线对照](lightburn-xy-native-result.md)。Mac已解锁，10×10mm原生工程保存、重开及导出完成，现有白名单/CNCjs/USB和BLE模拟独立审计通过。E57旧候选被原生丢弃的失败证据已保留。没有新增设备连接或二维运动，整体仍约60%。

以下为各阶段历史记录，当前结论以上述实测和最终目标矩阵为准。

最新见 [固定作业结果与证据边界](lightburn-reviewed-job-result.md)、[桥接实现](reviewed-job-bridge.md)及[最终目标矩阵](usb-parity-goal.md)。82项离线回归通过。第一对完整会话严格审计通过；第二对作业完成后退出时取消一条状态查询，清理成功，但不计全部查询审计通过。

最新修复：[EOF查询收尾实测](eof-query-drain-result.md)。真实BLE只读复验已通过，EOF后不再取消正常在途状态查询；旧作业捕获结论不变。

最新传输进展：[GRBL流水核心与USB/BLE批量只读实测](grbl-pipeline-result.md)。16份真实信息查询应答逐字节一致，102项离线回归通过；核心尚未接入LightBurn作业入口。

最新接入：[流水TCP→实际LightBurn初始化及退出实测](pipeline-lightburn-result.md)。两次真实初始化/清理通过，修复了退出后的反复重连；10mm零功率候选已原生导出但未执行。

最新准备：[10mm固定作业入口及USB/BLE只读预检](ten-mm-runtime-result.md)。固定入口、前后核验和45秒超时已就绪；两路只读预检及清理通过，10mm实际运动仍待现场授权。

实机已验证固定14条零功率命令入口；变长受限文件入口为软件候选，未开放通用加工。初始化、只读和运动为不同权限范围，默认只读范围不变。

真实 NEJE 5 Plus 两次独立连接均完成官方 verify + SystemRead 查询，取得系统及应用版本信息。详见 [调查结论](investigation.md)、[已验证协议](protocol.md)、[实验记录](experiments.md)、[验收边界](acceptance.md)。

## 运行

使用已有 Python 3.12 环境（Bleak 3.0.2）。默认只扫描：

```sh
cd /Users/tyongs/code/NEJE
/Users/tyongs/neje-ble312/bin/python tools/p0_probe.py
```

复现已验证的两条系统查询：

```sh
/Users/tyongs/neje-ble312/bin/python tools/p0_probe.py --query --repeat 2
```

默认按扫描到的精确名称 `NEJE 5 Plus` 选择唯一设备；多台同名设备用 `--device <当前扫描UUID>` 指定。日志默认生成新文件，不覆盖已有记录。关闭其他正在占用设备的 BLE 客户端，在安全空闲的设备上执行。工具不包含通用写命令入口、GRBL 查询、模式切换或加工命令。

Codex 执行沙箱内可能报告 NO_BLUETOOTH；本轮宿主环境扫描和连接均成功。直接在 Mac 终端运行，若提示权限则允许该终端的蓝牙访问。不要因沙箱报错刷写或重置设备。

## 离线验证

```sh
/Users/tyongs/neje-ble312/bin/python -m unittest discover -s tests -v
node tools/check_official.mjs
```

第二条只离线执行已归档官方 JS 的命令生成及解码部分，不访问蓝牙或固件服务。实机原始日志位于 `logs/p0-official-init-host.jsonl`。

## 文件

- `tools/p0_probe.py`：扫描与两条查询白名单；JSONL 原始记录。
- `tools/neje_protocol.py`：流式帧重组、已确认字段解码。
- `tools/fetch_official.py`：重新获取官方网页引用的模块；会更新证据快照，保留已有证据后再运行。
- `evidence/web/`：官方公开源码、页面及模块来源散列。
- `evidence/native/`：本机 NEJE 3 客户端可见符号摘要。
- `evidence/github/`：开源候选的 commit、许可证和适用性证据。

本轮直接复用 MIT 许可 Bleak。ble-serial 可供后续 TCP 层借鉴；旧 NEJE USB 驱动没有 NEJE 5 Plus BLE 兼容性证据。官方 Web 源码未见开源许可证，保留作本地调查证据，不能把“公开可读取”当作开源许可。

## GRBL 可靠协议调查更新

详见 [第二轮调查](grbl-reliable.md)。已从官方 Windows v6.8.5 定位可靠帧、计数和纠错逻辑，实机三次收到桥接状态响应，已完成真正的 `$I` 会话：返回 GRBL 1.1f / NEJE v2.3.2 和 `ok`，收发计数3/62一致，关闭后 way=0 已读回。

用户已确认此前切换过设备模式，并在确认激光模组供电物理隔离后授权本次实验；实验前后 app=3。正常启停和单次查询已验证，重传、断连恢复及长时稳定尚未验证。实机证据见 `logs/grbl-build-authorized-host-20261012-01.jsonl`。

查看已准备的实验计划（离线，不访问 BLE）：

```sh
/Users/tyongs/neje-ble312/bin/python tools/grbl_readonly_probe.py
```

程序默认不执行；`--execute` 还要求用户确认物理隔离。不要在现场状态及授权未明确时执行。`p0_probe.py --grbl-status` 同样新增物理隔离确认参数，不能把 GetStatus 方法名理解为无固件副作用保证。

## TCP / LightBurn 只读联调更新

后续：[初始化补查与在途查询断链](lightburn-pending.md)。两次真实在途断链均发现设备计数+3/+62，但第二条查询没有交付TCP；way仍为2，独立显式关闭后读回0。26项测试通过。已排除模拟ok及连接时复位的开源兼容方案，初始化无错误仍待客户端配置支持；上游草稿仅本地保存，未发送。

最新进展见[初始化与真实BLE断连报告](init-disconnect.md)：Custom GCode / GRBL 风格仍发送G0和$#，初始化错误尚未消除。真实主机侧BLE断连后桥接约0.10秒关闭TCP并拒绝新客户端；独立重连查询发现way=2，显式关闭后读回way=0。白名单保持$I / ?。无线衰落和设备断电尚未验证。

[联调报告](tcp-lightburn.md)：已实现单客户端 `127.0.0.1:4002` 桥接，白名单仅 `$I` / `?`。LightBurn Pro 2.0.05 完成2次信息查询、43次状态查询；初始化 G0 / $# 被本地拦截并显示错误提示。正常/异常TCP断连、SIGTERM均读回way=0；25项离线测试通过。物理BLE丢失、重传和加工仍未验收。

```sh
# 默认只显示离线计划
/Users/tyongs/neje-ble312/bin/python tools/tcp_readonly_bridge.py
# 用户确认激光模组供电物理隔离后执行；默认300秒结束
/Users/tyongs/neje-ble312/bin/python tools/tcp_readonly_bridge.py --execute --laser-power-isolated
```

新增 LightBurn 配置 `LaserLink ReadOnly TEST`，10×10mm测试占位尺寸、127.0.0.1:4002、自动回零关闭；不是加工配置。原有设备配置逐项比对未变。实验结束时LightBurn及桥接均已退出，未留下监听端口。

树莓派最新结果：[LightBurn USB/BLE 对照与在途断链](pi-lightburn-ble-comparison.md)。真实 LightBurn 经 Pi USB 完成 20 次只读查询，经 Pi BLE 完成 122 次；两种后端都拦截 G0/$#，初始化错误仍存在。Linux 在途断链时设备计数+3/+62，第二份响应虽收到但未核验、未交付；独立显式清理后 way=0/app=3。26 项回归测试通过，服务和转发已关闭。前期环境及 USB/TCP 基线见[报告](pi-usb-baseline.md)，其中未启动 LightBurn/未验证 Linux BLE 为当时历史状态。

已新增 [USB/BLE 统一只读调试工具](readonly-debugger.md)：`tools/readonly_debugger.py` 默认离线计划；执行后提供 connect usb/ble、build、status、state、disconnect、recover 固定入口。故障状态阻止查询及切换后端；显式恢复只清理本进程拥有的通道，不重发查询、不自动开始新会话。Linux 实机已验证双后端顺序查询、在途断链后的故障锁定、显式恢复及恢复后的独立连接查询。日志保存在 `evidence/readonly-debugger/`。LightBurn 初始化错误仍未解决。
