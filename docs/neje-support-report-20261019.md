# NEJE 5 Plus 蓝牙 GRBL 通道无响应：待提交诊断摘要

状态：**本地历史诊断草稿，未联系厂家。** 用户后来通过官方 Windows v6.8.5 将三项固件回退至 2.3.2，完整断电重启后，本项目实测 BLE 通道与 LightBurn 初始化恢复；下文保留 2.4.0 期间的诊断问题，不能当作当前设备仍失联。[回退验收](firmware-rollback-decision-20261019.md)。2026-10-19 故障发生时，Mac macOS 27.2；NEJE 5 Plus 主板硬件 2.0.1，GRBL 查询返回 `1.1f.NEJE build version:v2.4.0`。用户确认在成功作业后使用 Windows **NEJE Neo v7.0.0** 的提示升级过设备固件；`7.0.0` 是上位机版本，`2.4.0` 是设备固件版本。

## 故障

升级前的 2026-10-18 12:58 成功会话中，三个应用（System/NEJE/GRBL）的版本均为 **2.3.2**；`FF AA 08 40 01 00 00 55` 返回 `FF AA 0D 41 01 00 00 00 00 00 00 00 55`（way=0），随后能启用/关闭路由并完成作业。当天最终桥接清理读回 way=0、app=3。升级后的 2026-10-19 首次完整系统读回中，三个应用均为 **2.4.0**，0x40 状态不再回复。先前“同一固件”的判断已由原始帧推翻；版本变化是强关联线索，尚不单独证明 2.4.0 固件缺陷。

2026-10-19 起，设备 BLE 连接、Verify（model 55）和 SystemRead（app=3）均有真实回复，但相同的 GRBL 0x40 GetStatus 帧无任何协议通知，5 秒超时。未启用可靠路由，未发送 GRBL 作业字节。只读对照发现：

- SystemRead(ALL) / SystemRead(INFO) / 完全跳过 SystemRead 三种顺序均无效；INFO 后等待 1 秒也无效。
- FFE9 的 GATT 带响应写入能完成，但仍没有 0x40 协议回复；同时订阅 FFEA 与 FFE9 两条通知也无回复。状态超时后，SystemRead(INFO) 在 FFEA 仍立即正常回应，说明 BLE 整体未断。
- Mac USB CDC 独占打开、无其他客户端，固定 `$I` 和 `?` 查询分别返回真实版本及 `<Idle|...|FS:0,0|Pn:P|...>`；GRBL 核心可用。
- 用户确认已把设备主电源和 USB 都断开至少 10 秒，没有其他客户端占用。官方 NEJE 3 Mac 客户端连接后自动触发回零（用户现场看到正常完成），并把应用切到 ID 2；退出客户端、再次完整断电且仅恢复主电源后，独立 BLE 查询回到 app=3，但 0x40 GetStatus 仍无回复。激光模组供电在此对照前已物理隔离。
- 重新上电后的现场指示灯为红灯亮、黄灯灭。官方桥接说明称黄灯亮表示可靠模式已启用；这项观察不能代替缺失的 0x40 路由读回。
- 按官方 Windows v6.8.5 的 `GrblCommWorker.Start` 顺序单次对照：Verify/model55 与 SystemRead/app3 真实返回；随后先发 SetStatus(BLE=2)、再发 GetStatus，5 秒无任何通知。尝试一次 SetStatus(0) 清理后再查状态，仍无回复。没有发送 GRBL 作业字节。现场随后观察红灯亮、黄灯灭。**最终路由状态未知**，不将关闭命令的发送或灯光状态视为已关闭的证据。记录：`evidence/connection-20261019/official-start-order-host.jsonl`（SHA-256 `10f24d96f7303f43296ec23995755f93cc0e83e4578c66230036cc9feadc1ef8`）。这排除了“只是本项目 GetStatus 在 SetStatus 之前”的单一顺序解释。

## 希望厂家核实

1. 在固件 v2.4.0，app=3 且 USB GRBL 正常时，0x40 GetStatus 可能不回复的已知原因和无刷机恢复步骤。
2. 是否有可只读查询的路由/应用诊断字段，可区分 GRBL 桥接处理器未启动、状态忙或通信组被禁用。
3. 官方 NEJE 3 Mac 客户端连接时自动回零、切到 NEJE 模式的预期行为及关闭自动回零的方法。
4. 当 0x40 的 SetStatus(2)、GetStatus、SetStatus(0) 都没有协议应答时，如何只读确认可靠通道当前路由并安全恢复到 way=0。
5. 2.4.0 是否变更或停用了 GRBL 0x40 BLE 可靠通道；若属回归，是否提供适用于 NEJE 5 Plus、主板 2.0.1 的官方 2.3.2 回退包与校验/设置保留说明。
6. [官方 GRBL 配置页](https://wiki.neje99.com/en/manual/grbl-config)称升级 GRBL 固件会复位配置。NEJE Neo v7.0.0 对该主板的升级是否也如此？是否有官方的升级前后配置核查清单，以及固件 2.4.0 的 GRBL 连接桥协议说明？此页所述文本设置 `$40`（对焦亮度）与失答的 BLE 二进制命令组 `0x40` 不同。

用户未在 Windows NEJE Neo v7.0.0 中看到“GRBL Bridge / 连接桥”入口；这是使用印象，尚未进行界面核验。请确认 Neo 是否提供该功能及其官方入口。公开[旧版连接桥说明](https://wiki.neje99.com/zh/manual/conn-bridge)未标明适用于 Neo v7.0.0 / 固件 2.4.0。

补充版本映射：用户确认 Windows Neo v7.0.0 将 **`v3.0.0 调试版3`** 标为当前设备固件。2026-10-19 18:20（北京时间）Neo 断开后，本项目只发送 BLE Verify + SystemRead(ALL)，真实读回同一设备的 System/NEJE/GRBL 仍全为 **2.4.0**；两帧和早先 `mode-check.jsonl` 逐字节相同。原始记录 `evidence/connection-20261019/version-readback-after-neo-host.jsonl`，SHA-256 `c9d59605743bdb12f73778e1327db2f1e3a8a4ba8ae0996187d99b6f94b8936c`。随后只读 GATT 发现 BLE 模块固件另为 `V3.1.2`（`evidence/connection-20261019/gatt-versions-after-neo.jsonl`）。NEJE Neo 使用的[公开配置](https://api.neje99.com/v1/configs/neje_neo/config.json)明确映射 **bundle `v3.0.0 / debug_3` → System/GRBL/NEJE 全部 v2.4.0**，所以两种显示**并不冲突**。请厂家确认该调试包是否继续支持旧 BLE GRBL `0x40` 可靠通道，以及没有状态应答时的诊断与恢复办法。未试探新的桥接命令，也未刷写固件。

官方服务器上仍有 System/NEJE/GRBL 三个 `v2.3.2` 文件；本项目仅下载存档，没有刷写。当前公开配置**没有启用的 2.3.2 bundle 或适用于此主板的回退流程**。请厂家确认三分项是否可配套回退、所需校验/刷写顺序、失败恢复方式，以及 GRBL 配置是否会复位。不要仅凭文件 URL 指导用户刷机。文件哈希和大小见[排障记录](connection-init-race-20261019.md)。

后续补充：用户现场确认 Neo v7.0.0 页面只有 `v3.0.0 调试版3`，没有 2.3.2；但官网 Windows v6.8.5 引用的[旧固件目录](https://api.neje99.com/v1/configs/neje3_firmware/config.json)把三项 v2.3.2 标为启用，均列出 NEJE 5 Plus，CRC 与文件相符。请确认该旧客户端能否安全执行当前 2.4.0→2.3.2 回退，以及推荐的配套顺序、设置备份和故障恢复。旧客户端界面尚未核验，设备尚未刷写。

原始本机证据：`evidence/connection-20261019/`（工作区内不公开）、`~/Library/Logs/NEJE/neje-20261019T*.jsonl`；分析见 [同日排障记录](connection-init-race-20261019.md)。设备身份、序列号和本机日志应由用户审阅后再对外发送。
