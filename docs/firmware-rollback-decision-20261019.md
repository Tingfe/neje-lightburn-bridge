# 固件回退决策与验收（2026-10-19）

结论：**用户通过 Windows 官方旧版控制软件 v6.8.5 将 System、NEJE、GRBL 三项回退至 2.3.2；完整断电重启后，本机只读实测确认 GRBL、BLE 可靠通道及真实 LightBurn 初始化恢复。** 本轮尚未重新执行加工作业。下文保留回退前的判断与执行门槛，按时间阅读；以末尾的回退后验收为当前结果。

## 为什么考虑 2.3.2

- 2026-10-18 实机的 System/NEJE/GRBL 均为 **2.3.2**，BLE `0x40` 状态帧真实应答，本项目桥接完成过约 58 分钟的 LightBurn 切割。
- 用户随后通过 Windows NEJE Neo v7.0.0 升级。Neo 显示固件包 `v3.0.0 调试版3`；[官方公开配置](https://api.neje99.com/v1/configs/neje_neo/config.json)映射该包为三个 **2.4.0** 分项，与本机 BLE 实读一致。升级后 USB GRBL 和 NEJE 原生蓝牙功能可用，但旧 BLE GRBL 状态组 `0x40` 多次无应答。[原始证据与限定](connection-init-race-20261019.md)
- 当时版本变化与故障高度相关，尚未实测回退结果；现已完成回退后复测，见本页末尾。官方[固件更新说明](https://wiki.neje99.com/zh/manual/update)要求通过更新界面选择固件。

## 官方旧版客户端的新证据

[NEJE 官网](https://neje99.com/software/neje-control-software-for-windows.html)仍提供 Windows v6.8.5。离线检查该官网安装文件，发现其配置引用 `https://api.neje99.com/v1/configs/neje3_main_win/config.json`，后者指向[旧客户端固件目录](https://api.neje99.com/v1/configs/neje3_firmware/config.json)。2026-10-19 下载目录并保存为 `evidence/connection-20261019/legacy-windows-firmware-config-20261019.json`，SHA-256 `8f38d9bafc5ea7cce5a97ddac4a04ae538db72098599de93086a91e27ecc104d`。

该目录中 **System、NEJE、GRBL v2.3.2 三项均为 `enable: true`**，每项的 `match_device_list` 均含 `NEJE 5 Plus`，硬件和系统范围覆盖当前主板；目录给出的 CRC32/MPEG-2 与三个已保存旧固件文件逐一相符。从目录所指 `cdn.neje99.com` 再下载的三个文件，SHA-256 也与先前 `down.neje99.com` 文件逐一相同。这是回退前确认官方版本来源的证据；实际执行结果见文末。

用户随后在现场打开 v6.8.5 固件更新中心，确认**可以选择三项 2.3.2**。这里的“可选择”是用户现场反馈，不是本项目对 Windows 界面的直接屏幕捕获；后续独立读回见文末。

### 回退前 USB 快照（已完成）

2026-10-19 19:18（北京时间）用户将 USB 接回 Mac，退出 Windows 客户端并保持激光模组供电隔离。本项目只读发送 `? → $I → $$ → $# → $G → $N → ?`，设备首尾均为 `Idle`、`FS:0,0`、机器坐标四轴全 0，模态 `M5/S0`；`$N0=`、`$N1=` 均为空。`$I` 报告 GRBL 2.4.0，`$$` 的 46 行设置与 2026-10-12 成功作业前记录**逐字节一致**，包括 X/Y 行程 260/400 mm、`$32=1`、`$40=0.200`。串口已关闭；没有发送运动、激光、设置写入或复位命令。

完整证据 `evidence/connection-20261019/pre-rollback-usb-full-verified-host.jsonl`，SHA-256 `7fd6c82b7306d7b9285e3bd517ee316ed5110eaf408d8516d8f2405e7237407e`。一次中间尝试因为本地 `$N` 解析器错误地期待 `[N0:]` 而停下；设备当时实际回复 `$N0=`／`$N1=`，未报错，随后修复解析并完整重跑。失败记录和修复后的完整记录均保留，不将本地解析错误归因设备。

### 回退后首次读回（完整重启前）

用户随后在 Windows v6.8.5 更新中心按约定执行回退，并回复“固件恢复成功”。Mac USB 接回后首个只读状态探针收到控制板反复输出 `[MSG: Your device is not at GRBL mode, Please restart to switch to GRBL mode.]` 和 `[MSG: Boot app failed, try restart.]`，因此在发送 `?` 之后停止，未继续查询或启动桥接。记录 `evidence/connection-20261019/post-rollback-usb-host.jsonl`，SHA-256 `12183c3353b4621dbf11fc0aa3e0a6638592d40773ce99fa8f8ff98b85d9f001`。

随后仅用 BLE Verify + SystemRead(ALL) 读取同一 model 55、主板 2.0.1；**System/NEJE/GRBL 均实读为 2.3.2**，证明三项回退已写入；但当前应用为 **ID 1（System）**，并非 GRBL ID 3。记录 `evidence/connection-20261019/post-rollback-ble-system-host.jsonl`，SHA-256 `11611203899ed865cd13b52670f3667ca3d6c02b7601765b621a95dafa1edb95`。BLE 通道已断开，未发送 `0x40`、切换应用、复位或作业字节。此时固件版本回退成功，但 GRBL 尚未启动；用户随后按设备提示完成主电源和 USB 都断开的完整重启。

[官方英文连接桥说明](https://wiki.neje99.com/en/manual/conn-bridge)确认 NEJE 3/4/5 的 Windows 连接桥、分包编号/CRC 及重传的可靠模式，黄色 LED 亮时不能再直接让 LightBurn 连接普通 GRBL。该页没有 2.4.0 的帧协议或回退说明；本地 HTML 快照 `evidence/connection-20261019/official-conn-bridge-en-20261019.html`，SHA-256 `e5566cc5e0769025a815cc871707f09d2bc3228781a28363a57b04e2af6cc0b3`。

## 当时的执行门槛

1. Neo 由用户现场核实：**没有 2.3.2，只有 3.0 调试固件**；v6.8.5 现场可选择**三项 2.3.2**。回退前须核对更新中心显示的设备型号与三项目标版本；如出现版本混搭或识别不明，停止。
2. 当前 GRBL `$$`、`$#`、`$G`、`$I`、`$N` 和状态已备份；LightBurn 设备配置保留在 Mac，不在此流程中修改。继续保持激光模组供电物理隔离、完整行程净空、设备无作业、稳定供电，并准备独立断电方式。NEJE 官方[GRBL 配置说明](https://wiki.neje99.com/zh/manual/grbl-config)明确指出升级 GRBL 固件会复位配置。Neo 曾在连接时触发实际回零，因此不能把“仅打开更新界面”视为绝无运动。
3. 只通过官方 v6.8.5 界面选择与主板匹配的**配套版本**，按其[官方更新演示](https://wiki.neje99.com/zh/manual/update)分别更新 System、NEJE、GRBL；每项等待校验成功和软件重新连接后才进行下一项，不并行、不跳过错误。若出现版本混搭、强制升级提示、未知错误或不明校验状态，立即停止并保留日志，不尝试自制刷写序列。更新过程中不要拔线或断电。
4. 官方流程完成并重新上电后，先只读核验三个分项版本、USB `$I/?/$$`、BLE app ID 3 与 `0x40` GetStatus 的真实应答和退出后的 way=0。设置复位时对照备份逐项恢复，并独立核验行程、速度、激光模式与 `$40` 对焦亮度；不能盲目把旧设置全部写回。
5. 只有读回、清理和低风险联调通过后，再在现场条件具备时用 LightBurn 做受控作业。若 2.3.2 仍无状态应答，保留失败记录，不能把“降级完成”写成“桥接恢复”。

## 兼容性写法

现在可确认的是：**本项目桥接曾在这台设备的 System/NEJE/GRBL 2.3.2 上完成真实作业；升级后三项 2.4.0 的旧协议入口失答；回退至三项 2.3.2 并完整重启后，连接和 LightBurn 初始化恢复。** 不能由一台设备推导“所有 2.4.0 均不兼容”。适用范围见[固件兼容性](firmware-compatibility.md)；2.4.0 的协议适配仍可独立研究。

Neo 公共配置没有启用的 2.3.2 bundle；旧版客户端目录和用户界面均提供了三项 2.3.2，但仍没有针对当前 `2.4.0 → 2.3.2` 状态的公开救援说明。**文件可下载不等于适合直接烧录。**

## 回退后验收：2026-10-19

用户在 Windows v6.8.5 中完成三项回退。刚刷完时 BLE 读到三个 2.3.2，但应用停在 System ID 1，USB 报 `Boot app failed, try restart`。用户按设备提示断开主电源和设备端 USB 至少 10 秒，先接电源、再接 USB；以下核验都在完整重启后执行，激光模组供电物理隔离。

| 核验 | 实机结果 | 原始记录及 SHA-256 |
| --- | --- | --- |
| BLE 版本和应用 | model 55、硬件 2.0.1；System/NEJE/GRBL **2.3.2/2.3.2/2.3.2**，当前 **GRBL ID 3** | `post-power-cycle-ble-system-host.jsonl` · `6b55b86985cda9db814a0ef67d5d0ace36a171597ac8625558e7384d2775399f` |
| USB 只读配置 | `$I` 返回 GRBL 2.3.2；首尾 `Idle`、四轴坐标全 0、`FS:0,0`；`$$` 46 项、`$#/$G/$N` 与回退前逐字节一致，M5/S0、启动块为空 | `post-power-cycle-usb-host.jsonl` · `86d2341632a9eb6419dd75bec52f0802b84aafbeab732f46679f6b78c1b1408f` |
| BLE 可靠状态 | `0x40` GetStatus 真实回复 `way=0`，双计数及双错误为 0 | `post-rollback-ble-status-host.jsonl` · `03967c8ce8628b0c050ef419ab44e4000653d0415b027a5fd2a1afa0a78b011d` |
| 桥接初始化与关闭 | 现有 `ByteStreamSession` 无 GRBL 字节探针：`way=0 → 2 → 0`，最终 app3、双错误 0 | `post-rollback-bridge-init-cleanup-host.jsonl` · `4cc8c119ebe418cc8bc31cb82d30c02dc164a732e25c57668604e22bb9a2948a` |

上述文件均在 `evidence/connection-20261019/`。USB 串口和 BLE 会话均已关闭；没有写设置、运动、回零、出光或作业提交。

### TCP 与真实 LightBurn 初始化

19:28（北京时间）启动本项目源码透明桥接于 `127.0.0.1:4002`。原计划由受控 TCP 客户端仅查询 `$I/?`，但**仍打开的 LightBurn 自动连入了端口**；因此这次是实际 LightBurn 初始化，不能标为“仅 `$I/?` 的受控客户端测试”。捕获中 LightBurn 只发送四行不带坐标的 `G0`、`$I`、`$#` 和持续 `?`，没有坐标运动、出光、回零、设置写入或作业。设备真实返回四个 `ok`、GRBL 2.3.2 版本、参数表及反复 `Idle/FS:0,0`；桥接记录 `pipeline_tcp_ready`，BLE 计数推进，设备双错误保持 0。

为避免持续开放透明入口，本项目向临时桥接进程发送 SIGTERM。退出路径记录待发队列 0、待答行 0、无重放，随后真实读回 **way=0 / app3**、`cleanup_unknown=false`，监听端口关闭。原始捕获 `evidence/connection-20261019/post-rollback-tcp-readonly-host-2.jsonl`，SHA-256 `af3b7922275e6583f818b992dd8367fad637117cfa22d852d04dc0e30f4381b5`。首次在受限环境启动因 `NO_BLUETOOTH` 立即失败，未连接设备，记录另存 `post-rollback-tcp-readonly-host.jsonl`。

`check_transparent_capture.py` 的**完整会话审计未通过**：主动 SIGTERM 没有客户端 EOF 后的 `pipeline_eof_responses_drained` 标记。它不能给这次会话“正常断开”结论；原始帧可分别证明初始化回复和路由关闭。回退后的图形包、LightBurn 正常断开及实际加工仍待单独复测。
