# GRBL 通道与可靠传输调查（2026-10-12，第二轮）

## 当前结论

已从官方 Windows v6.8.5 的公开 .NET 程序中定位 GRBL 桥接方法和可靠收发逻辑。2026-10-12 16:30，真实 NEJE 5 Plus 已完成可靠 BLE 通道中的 `$I` 查询：收到 `[VER:1.1f.NEJE build version:v2.3.2:]`、`[OPT:VMZHL,100,1500]` 和 `ok`，收发字节计数及关闭读回全部通过。**只读 GRBL 通信验证通过；重传、断线恢复、TCP 与 LightBurn 尚未验收。**

用户已确认 16:16–16:18 之间曾切换设备模式，因此先前 app=2→3 有现场操作解释，不再列为原因不明异常；这不构成 GetStatus 无副作用的普遍证明。本次实验前用户明确确认激光模组供电已物理隔离，并授权临时启用通道、仅执行信息查询。现场隔离状态来自用户确认，软件未独立测量。

## 开源检索与复用决策

第二轮 GitHub repository search：`NEJE grbl`、`NEJE bluetooth`、`NEJE bridge`、`NEJE in:name`。前两类 BLE/bridge 查询没有匹配仓库，GRBL 的结果主要是旧主板改固件或通用 G-code 软件。存档：`evidence/grbl-research/github-search.json`。

第一轮的 MIT Bleak 继续作为实机 BLE 层；MIT ble-serial 作为后续传输队列/TCP 参考，仍未发现其包含 NEJE 可靠帧实现。

本轮补充检查：

- [gnea/grbl](https://github.com/gnea/grbl)：固定 commit 保存接口文档、`simple_stream.py`、`system.c`。可用于确认 `$I` 查询及标准 `ok/error` 行式流控；其序列号/校验机制不等于 NEJE 扩展。示例流式脚本会发送初始化字符及任意程序，不直接运行。代码为 GPL，尚未复制进本项目。
- [LaserGRBL](https://github.com/arkypita/LaserGRBL)：保存固定 commit README；本轮未完整审计其代码，不宣称已排除所有 NEJE 扩展。
- [dev-0x7C6/laser-engraver](https://github.com/dev-0x7C6/laser-engraver)：README 说明 GRBL/G-code 兼容；未提供可直接采用的 NEJE 5 Plus 可靠 BLE 驱动证据。
- [kfastov/nejetool](https://github.com/kfastov/nejetool)：取得 commit 和 GPL 许可证，根路径 README.md 未取得；不把它认定为已验证可用方案。

GitHub API 在后续补充抓取时触发限流，改用 `git ls-remote HEAD` 固定 commit，再读取 raw 文件，失败与回退分别记录在 `open-source-summary.json`、`open-source-fallback.json`。检索范围有限，不能证明不存在其他实现。

**目前没有可直接拿来运行的、已证实支持本机 NEJE 5 Plus 原生 BLE 可靠桥接的开源实现。** 因此继续复用开源 BLE 基础层，对 NEJE 特有部分进行小范围、证据驱动的独立实现。

## 官方程序证据来源

[官方 Windows 下载页](https://neje99.com/software/neje-control-software-for-windows.html) 链接：
`https://cdn.neje99.com/software/win/main/NEJE_v6.8.5.exe`

- 文件大小：2,925,448 字节。
- SHA-256：`12a524e47a231b7f9c32a5e68ec880405f8b66afc045c46fb25e0b1a2966493f`。
- 文件是可直接离线读取元数据和 CIL 方法体的 .NET assembly。
- 使用开源 dnfile / dncil / pefile 安装到 `/private/tmp/neje-analysis-libs`，没有改动已有 BLE 环境依赖。
- 未运行 Windows 程序、未解密、未绕过访问控制、未刷写设备；这不是客户端运行时抓包。
- 元数据：`evidence/grbl-research/NEJE_v6.8.5.metadata.json`。
- 可复现提取器：`tools/inspect_dotnet.py`；方法索引与 IL 位于 `evidence/grbl-research/il/`。
- 官方程序没有被认定为开源，不将其完整实现作为可再发布的依赖。IL 存档仅用于本地证据分析。

## GRBL 命令组：0x40

保留系统协议的 FF AA/AB、长度、命令组/版本字段。所有下表中“静态确认”的含义是从官方 CIL 逻辑直接读取，尚不等于本机固件完成了对应行为。

| 子命令 | 方法 / token | 用途 | 实机边界 |
|---|---|---|---|
| 01 | GrblCommExGetStatus / 06000087 | 查询通信路由、收发累计计数、错误码 | 已取得 13 字节响应；副作用尚未完全排除 |
| 02 | GrblCommExSetStatus / 06000088 | 设置可靠通道：0 关闭，1 USB，2 BLE | 未执行 |
| 03 | GrblCommExWrite / 06000089 | 封装主机→GRBL 文本及写计数 | 仅离线编码，未发送 |
| 04 | GrblCommExReadFrom / 0600008a | 从指定读计数重发；返回帧也使用 04 | 仅离线编码，未发送 |

`Generate8ByteCommand` 为 token 06000079。GetStatus 为：

```text
FF AA 08 40 01 00 00 55
```

SetStatus 的静态确认值：

```text
BLE 启用：FF AA 08 40 02 02 00 55
关闭：   FF AA 08 40 02 00 00 55
```

LinkWay enum：USB=1、BLE=2；模型 enum：NEJE_5_Plus=55，和此前 verify 返回 0x37 相符。见 `enums.json`。

## 状态响应字段

本机返回：

```text
FF AA 0D 41 01 00 00 00 00 00 00 00 55
```

| 偏移 | 字段 | 本轮值 |
|---|---|---|
| 3 | 0x40 组 + 协议版本低位 | 41：version 1 |
| 4 | 状态子命令 | 01 |
| 5 | way | 0，可靠通道关闭 |
| 6..7 | wCheck，小端 uint16 | 0 |
| 8..9 | rCheck，小端 uint16 | 0 |
| 10 | device_tx_error | 0 |
| 11 | device_rx_error，version>=1 时存在 | 0 |
| 12 | 尾字节 | 55 |

偏移证据：`0120-ResolveCommand.il.txt`，IL 0bc4..0c6b。不能据此判断设备处于物理安全、无出光或无任务状态。

## 文本封装与计数

`GrblCommExWrite(payload,wCheck)` 总长度为 `len(payload)+9`：

| 偏移 | 含义 |
|---|---|
| 0 | FF |
| 1 | AA + floor(总长度/256) |
| 2 | 总长度低 8 位 |
| 3 | 40 |
| 4 | 03 |
| 5 | 保留 00 |
| 6..7 | 起始 wCheck，小端 uint16 |
| 8..N-2 | GRBL ASCII 原始字节 |
| N-1 | CRC32(frame[1:N-1]) 的低 8 位 |

CRC 与 Web 的 CheckHelper 算法相同：多项式 04C11DB7、初始 FFFFFFFF、非反射、无最终 XOR；帧尾只有低 8 位，不是完整四字节 CRC32。证据：`0137-GrblCommExWrite.il.txt`、`1455-CRC32.il.txt`、`1458-CreateTable.il.txt`。

离线计算 `$I\n`、wCheck=0：`FF AA 0C 40 03 00 00 00 24 49 0A 2F`。**该帧已于 16:30:06 实机发送，收到有效信息响应与 `ok`，见下方 E11。**

设备数据帧使用子命令 04：bytes[6:8] 是该段起始 rCheck，bytes[8:N-1] 是文本。官方主机要求 rCheck 与预期一致，再累计有效载荷长度，并按 uint16 回绕。

官方发送线程最多从缓存取 256 字节组成一帧，发送后 `wCheck += payload.length`，按 uint16 回绕。因此两个计数都是**字节位置**，不是每包递增的包序号。

## 可靠性的源码逻辑

证据：`0433-MoveNext.il.txt`（GrblCommWorker.Start）、`0437-MoveNext.il.txt`（TaskRun）、`0429-OnDeviceDataReceived.il.txt`、`0435-MoveNext.il.txt`（Stop）。

1. 可靠启动发送 SetStatus(当前连接方式)，再 GetStatus，读取初始 wCheck/rCheck；随后使用带计数的二进制帧。
2. 收到数据段的计数不匹配时，不向上游交付错误位置的数据，记录读错误。
3. 设备报告 RX 错误，或计数不一致且 5 秒无新收发时，进入纠错判断；读写纠错各至少间隔 3 秒。
4. 写纠错按 `(主机wCheck-设备wCheck) mod 65536` 回退缓存读指针，并重发对应字节。
5. 读纠错发送 ReadFrom(主机rCheck)，从期望位置补发。
6. 状态轮询阈值为 100 ms；实际调用间隔还受线程循环、阻塞和 BLE 队列限制，不把阈值当成实测固定频率。
7. 停止可靠桥接时发送 SetStatus(0)。

没有单独用 command 03 的逐包 ACK：官方 resolver 对其返回未提取内容，发送确认主要依赖状态 wCheck。GRBL 自身的 `ok/error` 仍是另一层解析/执行反馈，不能用 GATT write 返回或 wCheck 变化替代。

这些只是官方主机实现的静态证据；本机固件的去重、缓存历史容量、重传极限尚未验证；本次正常退出的 way=0 已实机读回。项目不会照搬官方自动重连/自动续传加工逻辑，也未主动制造丢包或发送错误 CRC。

## 实机实验与状态变化

| 时间（北京时间） | 已发送操作 | 结果 |
|---|---|---|
| 16:15:56 | verify、SystemRead(FF)、GetStatus | 查询前 app=2；GetStatus 约 36 ms 后返回 way=0、计数0、错误0 |
| 16:18:23 | verify、SystemRead(FF)、GetStatus、再次 SystemRead(FF) | 初次系统读取已为 app=3；查询前后均为3，bridge way=0 |
| 16:18:36 | 新连接重复上一流程 | 查询前后 app=3，bridge way=0；完整重组，正常断开 |

证据：`logs/grbl-status-readonly-host.jsonl`、`logs/grbl-status-repeat-host.jsonl`。重复日志的完整发送清单只含上述查询；没有 `20 01` 应用切换或 `40 02` 通道启用命令。日志只能证明本探针发送了什么，不能排除现场、其他客户端或固件的动作。

## 白名单实验程序及授权条件

`tools/grbl_readonly_probe.py` 默认只输出离线计划。本次已在用户澄清应用变化、确认物理隔离并明确授权后执行。保留以下约束供复现：

- 核实目标 model=55，拒绝接管已有 way!=0 的桥接；记录初始应用和计数。
- 临时 SetStatus(2)，读回确认；只发送白名单 `$I\n`，不发送 reset、运动、回零、EEPROM 或电源命令。
- 必须得到真实 `[VER:...]` 和 `ok`，检查收发字节计数及错误码。
- finally 中 SetStatus(0)，读回确认；断连时不自动重连或声称已关闭。
- 再读系统应用，不擅自切回旧应用，记录是否发生隐式变化。

原因不只是流程偏好：[官方 GRBL 配置说明](https://wiki.neje99.com/en/manual/grbl-config) 指出 `$40` 用于空闲对焦光，文档默认值0.200对应0.2%亮度。本机实际 `$40` 未读取。本次初始应用已经是 GRBL，未验证从 NEJE 应用启用通道是否隐式切换；不能保证“只发查询”就“完全无光”。本轮不通过写 `$40=0` 改变配置来规避这个问题。

离线测试之外，以下 E11 提供真实请求、响应、计数及关闭读回证据；单次成功不代表长时稳定。

离线共17项测试通过，包含默认计划无 BLE 执行、隔离参数门禁、模拟成功会话、错误序号/报警/启用失败后的关闭读回。模拟通过不是实机关闭或无出光验收。

## E11：授权后的实机只读通信（16:30:06–16:30:07）

原始记录：`logs/grbl-build-authorized-host-20261012-01.jsonl`；独立离线审计：`evidence/grbl-research/authorized-capture-audit.json`；复验工具：`tools/check_grbl_capture.py`。

| 步骤 | 实际结果 |
|---|---|
| 身份、初始状态 | model=55，app=3，way=0，wCheck=0、rCheck=0，错误0 |
| 临时启用 | SetStatus(2)，状态读回 way=2、计数0、错误0 |
| 唯一 GRBL 查询 | `$I\n`，HEX `FF AA 0C 40 03 00 00 00 24 49 0A 2F` |
| 响应 | 约59 ms 后收到71字节封装帧，62字节文本，起始 rCheck=0，CRC尾34有效 |
| 查询后计数 | way=2，wCheck=3、rCheck=62，TX/RX错误均0 |
| 关闭读回 | SetStatus(0) 后读回 way=0；计数仍为3/62；系统 app=3；正常断开、complete |

返回原文：

```text
[VER:1.1f.NEJE build version:v2.3.2:]
[OPT:VMZHL,100,1500]
ok
```

本次发送清单只包含 verify、SystemRead、桥接状态/启停及一次 `$I`。未发送 `?`、reset、应用切换、运动、回零或设置修改；未自动重试。离线审计核对全部发送标签、全部通知流切分位置、真实数据CRC、字节计数和关闭事件，结果通过。

结论：NEJE 可靠帧封装、真实 GRBL 文本往返、正常启停已实测成立。尚未验证非零起始计数下的新会话、计数回绕、丢包/重传/去重、断连设备行为、加工或物理无出光；TCP/LightBurn 仍未接入。下一步可基于 Bleak 与 ble-serial 的队列/TCP设计准备桥接，并先保留查询白名单，另行验证断连清理与可靠恢复。

## TCP 与 LightBurn 更新

后续已打通白名单 `$I` / `?` 的 BLE→TCP→LightBurn 查询通路，并实测EOF/RST/SIGTERM关闭读回；原文未开始/未验证的TCP与LightBurn状态保留为历史。见 [第四轮联调报告](tcp-lightburn.md)，重传及物理BLE断连仍未验收。
