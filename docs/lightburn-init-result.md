# Mac 原生 BLE 的 LightBurn 初始化通过（2026-10-12）

用户本轮明确授权：在原 `$I/?` 之外，仅允许固定 `$#\n` 和单独 `G0\n`。沿用激光模组供电物理隔离的现场确认。**当前已完成真实 LightBurn 无拒绝错误初始化；没有完成与 USB 等价的加工。**

## 实机证据

18:27:32，Pi USB 基线逐条执行 `? → G0 → $I → $# → ?`。设备实际回复 G0 为 `ok\n`、$I 为 NEJE v2.3.2/OPT/ok，$# 为 G54–G59、G28/G30/G92、TLO、PRB 和 ok。初始和最终均报告 Idle、MPos=0/400/0/0、FS=0/0。仅设置了快速运动模态，没有坐标请求、回零、复位或出光请求。

18:28，**Mac LightBurn Pro 2.0.05 → 127.0.0.1:4002 → Mac CoreBluetooth → NEJE 原生 BLE**，没有使用树莓派中转。沿用已存在的 `LaserLink Custom ReadOnly` 测试配置，该名称属于历史配置，实际桥接现在选择 `lightburn-init` 实验策略，不能据名称称作纯只读会话。

- 实际发送 5 次无参数 G0、1 次 $I、1 次 $#、24 次 ?（其中 1 次为内部初始 Idle 检查，不冒充任何 LightBurn 请求的响应）。LightBurn 等待设备连接期间重复了 G0；每次收到的 ok 均对应实际设备请求。
- 30 份实际响应交付 TCP，G0、$I、$# 响应逐字节与 USB 基线一致。没有合成 banner、ok 或替换错误；没有本地拒绝，也没有设备错误终结行。
- 连续 rCheck 与真实响应 CRC 核验通过；w/r 计数 302/16842→347/18810，收发错误 0。75 个原始帧重解码一致；系统信息通知尾部剩余 35 字节未完成帧，未参与上述判定。
- LightBurn 界面显示真实版本、参数和 Ready；完整初始化控制台截图没有此前 error:20。退出 LightBurn 后 EOF 清理，读回 way=0、app=3，正常断开 BLE。
- LightBurn 偏好前后逐项比较没有变化，未点击加工/回零/移动/复位按钮。位置未变化是设备报告结果，不是额外物理测量。

捕获、截图、USB/BLE 逐字节审计、源代码/测试快照和 SHA256 清单位于 `evidence/lightburn-init-profile/`；`parity-audit.json` 为离线审计结果。

## 实现与复验

`tools/lightburn_init_bridge.py` 为独立、限时的实验配置。默认只打印离线计划；实际执行必须显式选择初始化权限和既有隔离确认，不修改旧只读程序的默认命令范围。

```sh
/Users/tyongs/neje-ble312/bin/python tools/lightburn_init_bridge.py
# 仅在本轮授权及现场隔离条件下执行；新日志路径不能已存在
/Users/tyongs/neje-ble312/bin/python tools/lightburn_init_bridge.py \
  --execute --allow-initialization --laser-power-isolated --seconds 90 \
  --log logs/lightburn-init-new-session.jsonl
```

先启动桥接，再打开既有测试配置。Mac 沙箱内 BLE 扫描本轮报告 Bluetooth unsupported，未访问设备；经已授权的沙箱外执行才得到实际通信。失败记录保留，未修改蓝牙权限设置。

Bridge 仅增加可选 parser/session 工厂，以复用生命周期及故障关闭。旧 QueryParser/ReliableSession 默认不变；新策略精确匹配 G0 和 $#，不接受 G00、坐标、速度、注释、复位或设置写入。新 session 先验证设备报告 Idle/FS=0,0，再向 LightBurn 的每个请求交付实际响应，并验证计数。多行参数正确拼接；真实 error 保留，ALARM 交付后禁止后续请求。

`tools/grbl_transport.py` 增加独立的 1–256 字节可靠封装编码，覆盖 AA/AB 长度边界和 uint16 计数；**这只是传输编码，不是任意指令的执行授权或已验证的加工流控**。Bleak/pySerial 仍为直接复用的开源通信依赖；gnea/grbl 固定 commit 的 gcode.c/system.c 用于语义研究，未复制 GPL 实现。

初始化阶段 50 项测试通过（原有 39 项 + 新增 11 项）；后续运动及控制台准备测试另见 tests.txt，覆盖精确输入、所有分割位置、控制字节拼接拒绝、旧策略不变、长度/CRC/回绕、多行响应、真实错误保留、报警后停止、计数未确认不交付以及空闲检查要求精确 FS=0,0。新增引擎也先在旧只读 parser 后完成真实 Mac $I/? 回归并确认 G0/$# 仍被拦截。

## 与最终目标的差距

初始化成功已消除此前最直接的兼容障碍。当前桥接仍只支持这四种请求，不能点击开始加工；暂未开放真实作业流、暂停/取消/急停等字节，也未证明蓝牙吞吐、持续流控和作业完成效果与 USB 一致。

用户随后明确授权 `$$` / `$G` 读取，18:31–18:32 已完成 Pi USB 与 Mac 原生 BLE 的逐字节一致对照，证据 `profile-*.jsonl` / `profile-parity-audit.json`。$130/$131=260/400 mm，$30=1000、$32=1、$40=0.200；模态 G0/G54/G17/G21/G90/G94/M5/M9/T0/F0/S0，未写设置。BLE 最终关闭为 way=0/app3、计数355/19416。新增查询为独立 `machine_profile_probe.py`，没有扩大 LightBurn 初始化桥接的四请求策略。

用户随后授权的独立探针 [1 mm 无激光往返对照](one-mm-motion-result.md)已完成，包含温度通知解析误判后的显式授权返程。下一步准备真实 LightBurn 控制台同范围验证，再验证通用传输队列、离线 LightBurn 作业回放及受控作业控制，最后比较真实加工结果。不会使用现有 10×10mm 占位配置直接加工。完整验收范围见 [最终目标验收矩阵](usb-parity-goal.md)。
