# NEJE 5 Plus BLE 协议：已确认子集

日期：2026-10-12。证据：官方公开 JS 快照 + `logs/p0-official-init-host.jsonl` 两次实机连接。

## GATT 与连接顺序

- 服务：`0000ffe5-0000-1000-8000-00805f9b34fb`。
- 发送：FFE9，write without response（本轮 `response=False`）。
- 接收：FFEA，notify。FFE9 自身也支持 notify，但本轮官方流程只订阅 FFEA。
- 广告列出的服务为 FF00；实际连接后发现 FFE5。不要仅用广告 FFE5 过滤设备。
- 扫描选择当前设备对象 → 连接 → 枚举 GATT → 订阅 FFEA → verify → SystemRead(FF) → 等待通知。
- 本轮两次独立连接，每次两次写入，写入之间等待 100 ms。官方 sender 目标为 2048 B/s、50 字节发送块；100 ms 是本轮保守实验间隔，不代表设备要求。

## 两条已实测查询

| 功能 | FFE9 写入 HEX | 来源 |
|---|---|---|
| verify | `FF AA 08 01 01 5A A5 55` | Commands.verify |
| 系统读取全部 | `FF AA 08 10 01 FF 00 55` | Commands.SystemRead(0xFF) |

verify 返回两次均为 `FF AA 08 01 01 37 30 55`。按官方解析：model=0x37，ctrlVer=0x30；不自行把它们映射成芯片型号或语义版本。

## 帧封装

| 偏移 | 意义 |
|---|---|
| 0 | FF |
| 1 | AA 或 AB |
| 2 | 总长度低 8 位；总长度 = byte[2] + (byte[1]-AA)*256 |
| 3 | 高 4 位为命令组，低 4 位为协议版本 |
| 4 | 子命令 |
| 5 … N-2 | 随命令变化的数据 |
| N-1 | 55，或下述 CRC32 结果的低 8 位 |

官方接收器接受固定 55 尾字节，或 CRC32(frame[1:N-1]) & FF。CRC 多项式 04C11DB7，初值 FFFFFFFF，非反射，不做最终异或；不是 Python zlib.crc32 默认算法。离线校验 `123456789` → 0376E6E7，Python 和官方 JS 一致。本轮实际全部接收帧都以 55 结尾；CRC 尾字节和 AB 长帧路径仅有源码/离线验证，尚无本机实测样本。

一次通知不是一帧：实测通知既会包含多帧，也会在帧中间截断。按长度重组；不按换行、通知边界或第一个 55 切分，载荷中也可能出现 55。

## 系统信息结果

系统组为 10，子命令 byte[4]=01，byte[5] 决定信息类型。

| subtype | 本轮长度 | 已解析结果 / 限定 |
|---|---|---|
| 01 | 21 | current_app=byte[8]=2；hardware version bytes[9:12]=2.0.1 |
| 02 | 44 | 扩展信息；保留原始帧，工具未展开唯一标识等字段 |
| 04 | 51 | 诊断信息，协议低位版本为 5；保留原始帧，未按旧版字段做完整解释 |
| 08 | 73 | current_app=byte[6]=2，app_count=byte[7]=4；每个 app 记录 16 字节 |
| 10 | 21 | current_app=byte[7]=2；状态标志按原始 HEX 保留，未猜测位意义 |

应用表：ID 0 为 1.0.0，ID 1/2/3 均为 2.3.2。依据官方 script.js 显示映射，1=System，2=NEJE，3=GRBL。因此可报告“系统表中 GRBL 应用版本 2.3.2”，**不能报告“已经建立 GRBL ASCII 会话”**。

这些是主控制器系统协议字段；交接文档中的 TD5322A / HW 1.0.0 / SW V3.1.2 是标准 GATT Device Information，两个层次不混用。

## 未确认

GRBL 原始通道、模式切换后的行为、GRBL 可靠帧的序号/CRC/ACK/重传、分包极限、背压、断连设备执行状态。未实现这些功能，未运行 TCP 服务。SwitchApp/Power/Reboot/Flash 等官方源码中的其他命令仅作为证据存档，未进入可执行探针。

## 第二轮补充：GRBL 0x40 组

官方 Windows v6.8.5 静态分析已确认可靠 GRBL 文本帧使用同一外层帧、16 位字节计数及 CRC32 的低8位；实机已收到 0x41/01 状态响应。完整字段、方法 token、重传逻辑和证据等级见 [GRBL 可靠协议调查](grbl-reliable.md)。这更新了上文“确切格式未确认”的调查状态，但 GRBL 文本及重传路径仍未实测。

注意：首次状态实验前 app=2，后续重连首次系统读取 app=3，变化原因尚未确认；查询的固件副作用尚未排除。后续 GRBL 组实验需现场澄清及物理隔离确认。

## 授权实机验证更新（16:30）

用户已确认此前切换过模式并隔离激光模组供电。临时可靠 BLE 通道中的一次 `$I` 已通过，返回 GRBL 1.1f / NEJE v2.3.2 与 `ok`，计数3/62、错误0，关闭后 way=0 已读回。详见 [GRBL 调查](grbl-reliable.md) E11；旧的待授权状态保留为历史，重传/TCP/LightBurn仍未验证。

## TCP 与 LightBurn 更新

后续已打通白名单 `$I` / `?` 的 BLE→TCP→LightBurn 查询通路，并实测EOF/RST/SIGTERM关闭读回；原文未开始/未验证的TCP与LightBurn状态保留为历史。见 [第四轮联调报告](tcp-lightburn.md)，重传及物理BLE断连仍未验收。
