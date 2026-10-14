# 原生 GRBL 直接开始验证结果（E91，**已完成**）

2026-10-13。目标见[方案](native-start-plan.md)：让原生 GRBL 驱动从真实工程**直接点击开始**，与 Pi USB 对同一真实导出文件执行对照。

**结论：三条路径（Pi USB／运行GCode／原生直接 Start）的 18 条作业命令逐字节一致，原生 Start 路径实测通过并含现场观察。** 本轮把方案文档中列为缺口的「直接 Start 生成」补齐，控制项 18→19/20，总分 80→**81/100**（对外约 81%，仍处 80% 区间）。

## 一、结论摘要

| 目标项 | 结果 | 证据 |
| --- | --- | --- |
| Pi USB 执行同一受审文件 | **通过**（含现场观察） | `usb-rerun.jsonl`、`usb-rerun-audit.json` |
| macOS BLE：运行GCode 路径 | **通过**（含现场观察） | `ble-run.jsonl`、`ble-run-audit.json` |
| **macOS BLE：原生直接 Start** | **通过**（含现场观察） | `ble-start.jsonl`、`ble-start-audit.json` |
| 三路作业字节同源对照 | **18/18 逐字节一致** | `native-start-parity.json` |
| 设备参数自动读取（LightBurn ↔ 控制器） | **通过** | 握手含 `$$`，设置全文交付（见 §五） |
| 未审阅设计被正确拒绝 | **通过（fail-closed）** | `start-rejected-other-design.json` |

原生 Start 实际生成的 18 条命令（与受审文件、与 Pi USB 字节完全相同）：

```
M5 / S0 / G00 G17 G40 G21 G54 / G90 / M4 / M9 / G0 X0Y400 /
G1 X10S0F60 / G1 Y390 / G1 X0 / G1 Y400 / M9 / G1 S0 / M5 / G90 / M5 / S0 / M2
```

含**裸 `M4`（不带 S）**——即本轮审阅器放宽项，由强制 `M5`/`S0` 前缀与「拒绝一切非零 S」共同保证零功率；不含 `M8`。

**⚠ 本轮结论经过一次更正，必须保留**：早期结论为「原生 Start 未达成（Start 按钮置灰）」，并把原因假设为缺少激光器来源。用户随后指出真正原因是**当时没有载入工程设计**（空白画布无可发送内容）。载入受审工程后 Start 实测通过。因此「缺少激光器来源」的假设**已被证伪**；相关改动（§五）仍有独立价值，但不构成本轮 Start 通过的原因。

## 二、Pi USB 侧重跑（首次运行现场无人）

首次 Pi USB 执行（15:02）现场无人，用户要求重跑；旧捕获与审计原样保留、不被追认。

- 新目录 `/tmp/neje-e91-rerun-TOWNqj`，包内 **73 项源文件摘要与 manifest 逐一比对一致**，`job.gc`=`b189a348…`。
- 执行 17:34:56–17:35:39；独立审计：18 条命令、**13911 字节全部核验**、`no_synthetic_ack`/`no_replay`/`usb_port_closed` 成立、`passed: true`。
- 现场观察：用户确认「完整四边、正常返回、无异常、无出光」。

## 三、macOS BLE：运行GCode 路径

会话 `sessions/20261013T101307Z-98f8810b`，作业 18:18:31–18:19:12（约 41 秒）。独立审计 `passed: true`：47015/47015 字节、6706 帧、尾残留 0、`way=0`/`app3`。现场观察同上。

## 四、macOS BLE：原生直接 Start（本轮目标）

会话 `sessions/20261013T105113Z-050fab6d`（18:51:13–18:53:45），由真实 LightBurn 原生 GRBL 驱动**点击 Start** 触发，非「运行GCode」。

| 核查 | 结果 |
| --- | --- |
| 作业命令 | `pipeline_job_submitted` 18，终点核验 + 私有收尾通过 |
| 与受审文件比对 | **逐字节一致**（归一化换行后 true；原始行 + LF 也 true） |
| 与 Pi USB 比对 | **字节完全一致** |
| 独立审计 | **`passed: true`**；16684/16684 字节、2133 帧、尾残留 0 |
| 关闭 | `cleanup_verified` → `way=0`、`final_app=3` |
| 安全标记 | `no_synthetic_ack`、`no_replay` 成立 |
| 现场观察 | 「完整四边、正常返回、无异常、无出光」 |
| 会话状态 | `completed`，`requires_explicit_recovery: false` |
| 诚实记录 | 审计同时记录 `discarded_unsent_status_queries: 1`、`discarded_unsent_whitespace_bytes: 1`、`all_received_tcp_queries_forwarded: false` —— 退出瞬间有 1 次未发送的状态查询被丢弃，与 E40 同类；不因此宣称「全部查询均已转发」 |

## 五、设备参数自动读取（独立于 Start 的改进）

改动前 LightBurn **完全读不到**控制器参数；本轮打通并实测：

- 代码：[tools/lightburn_init_bridge.py](../tools/lightburn_init_bridge.py) 与 [tools/reviewed_job.py](../tools/reviewed_job.py) 白名单新增 `$$`（**只读**设置导出）；
- 配置：目标设备 `Settings.AllowSettingsFetch` false→true（界面标签 `Fetch Settings on Connect`），全局差异恰好 1 条，其余 5 个设备逐项不变；
- 实测握手变为 `G0`×10 → `$I` → **`$$`** → `$#` → `?` 轮询，控制器设置全文交付（含 `$30=1000`、`$31=0`、`$32=1`、`$40=0.200`、`$110/$111=18000`、`$130=260`、`$131=400`）。

**安全边界未放宽**：`$$` 仅允许出现在作业体之外（`index in (0, len(job))`），作业中途发 `$$` 仍拒绝；`$n=value` 写设置在**任何位置**都被拒；`?`/`!`/`~`/非 ASCII 行规则不变；**基础只读桥 [tcp_readonly_bridge.py](../tools/tcp_readonly_bridge.py) 的白名单刻意未动**（仍为 `$I`/`?`）。

理由：`$$` 是只读导出，且**桥接自己早就在预检中发它**（真实捕获 `preflight_settings` 请求字节为 `$$\n`），故本条不授予任何新的控制器能力。

**验证三件套**：① 定向测试 19 项通过（含 2 项新增：`$$` 被接受且视为只读、`$$$`/`$`/`$30=500`/`$I ` 仍被拒、作业中途 `$$` 被拒、基础只读桥仍拒 `$$`）；② **等价性证明**：用新代码重跑两份历史捕获（`usb-rerun.jsonl`、`ble-run.jsonl`）的独立审计，除捕获路径外**全部字段一致**、`cleanup` 一致、`passed` 仍为 true；③ **全量回归 303 项 OK**。

## 六、未审阅设计被拒绝（fail-closed 实证）

18:48:05 用户在**另一份设计**上按 Start（坐标 X115–237 / Y128–225、进给 **F6000**），与受审序列第 3 行即分叉：

- 网关判定 `Unexpected, repeated or out-of-order job command`，会话失败并进入 `requires_recovery`（随后经显式 `recover` 解除）；
- **零转发**：`pipeline_job_submitted` 0、`kind=job` 0、三行作业命令在写请求中**各 0 次**、`queued_bytes` 0；
- 设备 `cleanup_verified` → `way=0`/`app3`，**无运动、无出光**。

该证据表明：不只看功率（该设计图层功率也是 0），**速度与几何同样在授权边界内被强制**；未审阅内容一律挡在门外。

## 七、本轮发现的缺陷（未修，另行评审）

### 缺陷 1：会话走到墙钟上限必然要求显式恢复（复现 2 次）

到期时以 `pipeline_session_cancelled`＋`pipeline_stopped`（`CancelledError`）收尾，设备虽已 `cleanup_verified`（`way=0`/`app3`）且无作业无运动，`assess()` 仍因[审计器](../tools/check_pipeline_tcp_capture.py)第 13 行把 `pipeline_stopped` 列为禁止事件而 `AssertionError` → 状态变 `requires_recovery`。两次实例：17:24:28–17:29:36（初始化档）、17:50:13–18:05:22（作业档，0 次作业提交）。

修法建议：把「墙钟到期」作为显式终态与真实故障区分；**不要**放宽审计器第 13 行，否则会一并放过真实故障。

### 缺陷 2：LightBurn 自动重连与单客户端设计冲突

客户端断开后 LightBurn 在 0.3 秒内重连 62 次全部被拒（`connection_consumed_no_reconnect`），界面长期停在 Disconnected，用户表现为「连不上」且无法自愈。单客户端是该项目的**有意设计**，不应改为允许任意重连；建议加有限宽限期，或在 CLI 提示中明确「请重启桥接，而不是在界面里反复点连接」。

### 缺陷 3（新）：计数口径不同步

审计器对「全部查询均已转发」为 false 的判定（`discarded_unsent_status_queries`）在 E40 与本轮各出现一次，属退出竞态；现有实现会如实记录为不通过，符合标准，但缺少对「正常收尾下的单次丢弃」与「审计失败」的区分说明。

## 八、证据清单（`evidence/native-start-20261013/`）

| 文件 | 内容 |
| --- | --- |
| `ble-start.jsonl` / `ble-start-session.json` / `ble-start-audit.json` / `ble-start-launch.json` | **原生 Start** 原始捕获、会话、独立审计、启动记录 |
| `native-start-parity.json` | 三路作业字节同源对照 |
| `onsite-observation-start.json` | 原生 Start 的现场观察 |
| `start-rejected-other-design.jsonl` / `.json` | 被拒的另份设计（保全失败证据） |
| `ble-run.*` | 运行GCode 路径的捕获、审计与记录 |
| `usb-rerun.*` / `usb.*` | Pi USB 重跑与首次（现场无人）捕获及审计 |
| `lightburn-fetch-settings/` | LightBurn 配置改动前后快照、`.lbdev`、`change.json` |
| `manifest.json` | 全目录 SHA-256 清单 |

## 九、边界

本轮未暂停、故障注入、复位、回零、解锁、写设备设置或出光；`$$` 仅为**读取**。现场观察为用户口述，不替代独立位移/精度测量。**实际加工对照仍未开始**（激光物理隔离，权重 15 分得 0），这是 81→100 的主要缺口；输入半行、无线衰落/掉电、数小时稳定与图形化通用入口亦未验收。
