# 日常使用指南：LightBurn 通过蓝牙使用 NEJE 5 Plus

2026-10-13。本页是**日常操作**说明；协议、实验与证据见 [README](../README.md) 与各结果文档。

## 架构（为什么必须有一个程序在跑）

```
LightBurn ──TCP 127.0.0.1:4002──> 本项目桥接 ──BLE──> NEJE 5 Plus
```

**LightBurn 不直接连设备**，它连的是桥接；桥接再通过蓝牙转发。所以**每次加工前必须先把桥接跑起来**，这也是这个「程序」存在的理由。

## 一次性准备

1. **LightBurn 设备**：新建/使用 `LaserLink NEJE5 Native GRBL`，连接方式为 **TCP**，地址 `127.0.0.1`，端口 `4002`。
2. **设备模式**：必须是 **GRBL**（`current_app=3`）。设备若显示「not at GRBL mode，请重启切换」，**断电重启**即可回到 GRBL。
3. **位置基准**：完整断电会丢失基准（GRBL 报 `MPos:0,0,0,0`）。恢复方式：LightBurn 连 USB 设备点 **Home**，或用本项目工具（见下）。
4. **激光供电**：零功率试跑建议**物理隔离**；真实加工必须恢复供电并显式声明。

## 日常加工流程

### 第 1 步：在 LightBurn 里设计并**导出 GCode**

`File → Save GCode`，导出成 `.gc`。

> **这一步不是多余的**：桥接只放行**预先受审、字节完全一致**的作业，而你导出的这份 GCode 就是那个「受审文件」。实测确认 LightBurn 的 **Start 生成字节与导出的 GCode 逐字节一致**（见 [原生 Start 结果](native-start-result.md)），所以导出一次即可，之后照常按 Start。

### 基准丢失时：回零（**不需要换线**）

掉电后 `MPos` 变成无意义的 `0,0,0,0`，必须重新建立基准。**实测有效的形式是整轴 `$H`**：

```sh
# 经蓝牙，用受审工具（在能访问串口的一侧运行；或经透明模式发送 $H）
python tools/homing_recovery_probe.py --execute --allow-xy-homing --allow-idle-homing \
  --laser-power-isolated --log <新日志>
```

⚠️ **不要用 `$HX`/`$HY`**：本固件对它们**回 `ok` 但什么都不做**（实测）。旧形式仍可用 `--axis-selective-home` 复现，但**它不是有效的回零**。回零后应读到 `Idle X0.000 Y400.000`。

### 第 2 步：跑启动器（自己算摘要、自己选 profile、自己配旗标）

```sh
cd /Users/tyongs/code/NEJE
/Users/tyongs/neje-ble312/bin/python tools/daily_use.py start <你的文件>.gc \
  --power-ceiling 800 --feed-ceiling 3000 --output-authorized --run
```

- `--power-ceiling` / `--feed-ceiling`：**必须显式声明**的上限（相对设备 `$30=1000`）。审阅会拒绝任何超出它们的行。
- 功率与激光声明**二选一**：零功率试跑用 `--laser-isolated`；真实出光用 `--output-authorized`（**前提是激光供电已恢复**）。
- **不加 `--run` 就是干跑**：只打印将要执行的命令，不碰设备。
- 只想看文件是否可加工：把 `start` 换成 `review`。

启动器会打印它选的 profile 与理由，例如：

```
{"profile": "xy-general-power", "why": "xy-general-power accepts the file with the declared ceilings"}
```

### 第 3 步：LightBurn 连接并 Start

连接 `LaserLink NEJE5 Native GRBL` → 按 **Start**（或 `运行GCode` 发送同一份 `.gc`）。

桥接会逐行比对：**字节一致才放行**；一旦出现未受审的字节，**整条链路 fail-closed**，不会继续转发。

## 支持与不支持（如实）

| 功能 | 状态 |
| --- | --- |
| 连接、初始化、只读查询（`$I`/`?`/`$#`/`$$`） | ✅ |
| 用**导出的**自己的设计加工 | ✅ |
| 受控的暂停 / 恢复 / 停止 | ✅ |
| **点动 / Move 面板**（`$J=`） | ❌ 不在白名单 |
| **从 LightBurn 回零**（`$H`） | ❌ 不在白名单 |
| **改设备设置**（`$n=value`） | ❌ 明确拒绝 |
| 不先导出、直接 Start 且字节与受审文件不同 | ❌ 整份拒绝 |

## 日常用法（一键开启）

> **第一次真实出光前，请先看[e 级出光方案](level-e-test-plan.md)**：先决条件清单、小功率单段配方、验收判据。

## 一键开启（推荐日常用法）

```sh
python tools/neje.py          # 完整功能：激光已供电
python tools/neje.py --test   # 激光隔离：安全试跑
```

或者在 **Finder 里双击 `neje.command`**。**零必填参数、零配置**：模式、签证、会话时长（7 小时）、日志路径全部自动选定；**会话结束时自动对本次捕获做独立审计并打印结论**（篡改或伪造会被抓出并保留捕获）。

然后照常使用 LightBurn：连接 `127.0.0.1:4002`，**Start 自己的设计、回零、改设置、读位置** —— 全部可用，**不需要先导出 GCode**（实测：LightBurn 自行生成的 26 行作业经桥接原样转发，设备按图运动，150 个 `ok`、0 错误、0 告警）。

**唯一明确不支持：点动（`$J=`）** —— 实测一条 -1mm 的点动导致失控运动，故 fail-closed 拒绝。

## 透明模式（手动启动，可选）

如果不想「先导出再受审」，可以用**透明模式**：桥接不再比对作业、不再有白名单，LightBurn 获得与 **USB 直连等效**的能力（任意 GCode、点动 `$J=`、回零 `$H`、改设置 `$n=value` 全部放行）。

```sh
# 看它会执行什么（干跑，不碰设备）
/Users/tyongs/neje-ble312/bin/python tools/daily_use.py transparent --laser-isolated

# 真正启动（默认会话 8 小时，够一天使用）
/Users/tyongs/neje-ble312/bin/python tools/daily_use.py transparent --laser-isolated --run
```

**代价（已由用户逐项确认，见[透明桥接方案](transparent-bridge-plan.md)）**：**预先审阅这道闸门被移除**，出光与撞框风险回到与 USB 直连相同；**改设置会真实持久化写入设备**。

**仍然保留**：不伪造响应、不重放、不自动重连、计数器完整性、原始字节全量记录；且**客户端断开时正常收尾**（实测 `cleanup way=0`、`failure_reason: None`），日常用完无需额外恢复流程。

**点动的处理**：实测一条看似 1mm 的点动（`$J=Y-1F100`）曾持续失控运动 ≥168mm（详见 `evidence/transparent-jog-incident-20261014/`）。读数显示该固件把 `$J=` 当作**绝对**目标执行（"移到 Y=−1" 就是 401mm 行程；而从 X0 出发的 "移到 X=1" 与相对 +1mm 无法区分，所以当时看着正常）。因此桥接**不再原样转发点动**，而是把它**翻译成一条显式的有界相对移动**：`$J=Y-1F100` → `G91 G1 Y-1 F100` —— 意图一致、一行进一行出（`ok` 数量不变）、**相对位移在结构上不可能跑飞**。含模态/设置字、步长 >100mm、缺进给或进给越界的点动仍会被拒绝。

**独立审计**：`tools/check_transparent_capture.py <捕获>` —— 断言入口字节守恒、全部转发、**回传字节必来自设备（防伪造）**、计数推进、无重放；5 种失败模式已逐一验证会被抓出。

## 查看状态（图形化，不连接设备）

```sh
/Users/tyongs/neje-ble312/bin/python tools/status_panel.py --serve --port 4010
```

浏览器打开 `http://127.0.0.1:4010/`：显示最新会话的状态、作业摘要、独立审计与最近事件，每 3 秒刷新。它**只读本地记录，不连接设备**，所以无法绕过任何门槛。

## 安全规则（请务必记住）

1. **只有设备自身的电源能真正停下机器。** 已实测：主机侧的一切手段 —— 软件停止、干净断开、**无线链路突然消失** —— 都不能停住它。失联时设备会把**已经收到**的作业执行到底。
2. **唯一可靠的紧急停止 = 切断设备电源。**
3. **功率上限必须显式声明**，审阅会拒绝超出的行。
4. 出光前确认：护目镜、通风、灭火设备、可独立断电、行程净空、无人触碰。
5. 调试可疑文件时，先用 `review`（离线，不连设备）。

### 关于连接的一个重要特性

桥接的会话是**单客户端、一次性**的：**第一个连入的客户端离开后，本次会话就结束了**。

- 正常使用没问题：LightBurn 连上后一直保持连接，跑完再断开。
- 但如果你**在 LightBurn 里断开又重连**（例如切换设备、改端口设置），第二次会连不上 —— 因为会话已经用掉了。
- **处置很简单**：重新双击 `neje.command`（或再跑一次 `python tools/neje.py`），新会话立刻可用。工具会提示「已有桥接在运行」如果旧会话还活着。

**为什么这样设计**：盲目重连到「状态未知」的设备是危险的（这正是掉电/失联实验暴露的问题）。一次性会话保证每次连接都从一次干净的初始化开始。

## 已验证可用的出光参数（2026-10-14 实测）

| 材料 | 功率 | 速度 | 结果 |
| --- | --- | --- | --- |
| 低功率即显痕基材 | **S100（10%）** | **F300（300 mm/min ≈ 5 mm/s）** | ✅ **痕迹明显** |
| 同上 | S100（10%） | F6000（6000 mm/min = 100 mm/s） | ❌ 无痕（9mm 仅 0.09 秒，能量不足） |

**结论**：首次出光**务必先慢**（300 mm/min 量级）。桥接/链路从未是瓶颈 —— 同一会话内只改速度，结果就从"无痕"变为"痕迹明显"。

## 先跑一次自检

```sh
/Users/tyongs/neje-ble312/bin/python tools/daily_use.py doctor
```

它会**离线**逐环检查整条日常链路（不连设备）：桥接是否在 4002 监听、最新会话是否卡在 `requires_recovery`、LightBurn 是否已有指向 4002 的设备、以及日常流程与「不支持项」提醒。有警告时退出码为 1。

## 出问题时的处置

| 现象 | 处置 |
| --- | --- |
| 启动器报 `No reviewed profile accepts the file` | 看它给出的原因（括号里就是审阅器的原话） |
| 会话变成 `requires_recovery` | 跑 `lightburn_session.py recover`（只读核查 + 关闭所属通道），或看状态面板 |
| LightBurn 连不上 / 控制不了 | 先确认桥接在跑（`lsof -nP -iTCP:4002 -sTCP:LISTEN`），再确认设备是 GRBL 模式 |
| 设备位置看起来不对 | 重新 Home；掉电后必须回零 |
| **机器失控运动 / 需要立刻停** | **`tools/emergency_stop.py`**：一连串发 `!`（保持）→ `0x85`（点动取消）→ `0x18`（软复位），只发实时字节、不重试。默认离线，执行用 `--execute --log <新路径>`。**复位后坐标不可信，需重新回零。** 若它无效，**立即切断设备电源** —— 只有设备自身的电源一定能停下它 |
| 蓝牙断开后设备状态不明 | 用 `tools/terminate_line_probe.py --read-status --close-route` 附着并读取/清理；仍不明则完整断电（**USB 也要拔**） |

## 相关的受审工具

| 工具 | 用途 |
| --- | --- |
| [daily_use.py](../tools/daily_use.py) | **日常入口**：审阅 + 装配启动命令 |
| [lightburn_session.py](../tools/lightburn_session.py) | 管理会话：`review` / `start` / `status` / `recover` |
| [status_panel.py](../tools/status_panel.py) | 图形化只读状态面板 |
| [homing_recovery_probe.py](../tools/homing_recovery_probe.py) | 受审回零（含掉电后「Idle 且基准未知」情形） |
| [terminate_line_probe.py](../tools/terminate_line_probe.py) | 附着锁存通道：只读状态 / 清除 `way` 锁存 |
| [emergency_stop.py](../tools/emergency_stop.py) | **一键急停**：`!` + `0x85` + `0x18`，只发实时字节 |
