# 树莓派 LightBurn USB/BLE 对照与在途断链（2026-10-12）

树莓派两条只读链路均已实测连通，Linux BLE 也复现了“设备已处理、TCP 未交付”的在途断链状态。LightBurn 初始化兼容问题仍存在：同一测试配置通过 USB 和 BLE 都发送 `G0`、`$#`，本地白名单层明确拒绝，两条错误提示不能靠更换传输层消除。

本轮沿用用户确认的激光模组供电物理隔离和只读实验授权。设备 GRBL 文本只允许 `$I\n`、`?`；没有发送运动、回零、复位、设置写入或切换应用指令，没有伪造成功。BLE 临时启停的是可靠通信通道。

## 实机结果

路径均为 Mac LightBurn Pro 2.0.05 → 本机回环 TCP 4002 → 现有 SSH 复用连接经局域网 → Pi 回环 TCP → NEJE；没有在 Pi 的 Wi-Fi 接口开放无认证端口。使用已有 `LaserLink Custom ReadOnly` 测试配置。

| 实验 | 中国时间 | 查询和结果 | 清理证据 |
| --- | --- | --- | --- |
| E23 LightBurn → Pi USB | 18:03–18:04 | 1 次 `$I`、19 次 `?`，真实 NEJE v2.3.2 / Idle；`G0`、`$#` 被拒绝，界面显示两条 error:20 和 Ready | 退出 LightBurn 后 EOF→串口关闭 2.680ms；测试服务停止 |
| E24 Pi Linux BLE 独立查询 | 18:05 | 扫描唯一 NEJE 5 Plus；1 次 `$I`、1 次 `?`，计数 148/7851→152/7975，错误计数 0 | 关闭读回 way=0、app=3，正常断开 |
| E25 LightBurn → Pi BLE | 18:06–18:07 | 两个 TCP 会话合计 1 次 `$I`、121 次 `?`；122 份交付数据逐字节匹配已核验响应；同样拦截 `G0`、`$#` | 60 秒会话超时后 way=0；LightBurn 主动再连，90 秒服务总时限到达后再次 way=0/app=3，BLE/TCP 均关闭 |
| E26 Pi Linux BLE 查询在途断链 | 18:09 | 基线 `$I` 核验并交付，第二次 `$I` GATT 写返回即请求真实断链；设备 279/16117→282/16179；第二次收到 62 字节但未核验、未交付 | BLE 回调→TCP 关闭 0.930ms，请求断链→TCP 关闭 2.709 秒；拒绝后续连接；独立检查三次 way=2/计数不变，显式关闭后 way=0/app=3 |

E25 第二次连接来自 LightBurn 的自动连接行为，桥接没有自动重连 BLE。正常超时清理确认后允许新客户端，断链清理无法确认时则拒绝新客户端，两种状态应在后续产品 UI 中明确区分。E25 保存的截图是在有界会话结束后截取，不能当作连接中的截图；连接中的两条错误和 Ready 为当时界面观察，初始化命令及查询结果有原始通信日志佐证。E23 另有连接中截图。

E26 使用真实 Bleak/BlueZ disconnect，不是模拟 BLE，也不是无线衰落或 NEJE 断电。BlueZ 在请求断链到完成断链之间仍交付了第二条响应；查询函数当时等待 disconnect 返回，未进入后续计数核验。原有 Mac 捕获没有收到第二条响应，但两者的“不交付未核验结果、禁止自动重发、断链后不能假定 way=0”结论一致。审计器已区分“收到数据”和“核验后交付”，旧 Mac 两份捕获重新审计通过。

## 开源复用与环境

直接使用现有项目 TCP 解析/白名单和可靠通信实现；Linux BLE 使用开源 [Bleak](https://github.com/hbldh/bleak) 3.0.2 / dbus-fast 5.2.0，经 BlueZ D-Bus 操作。安装仅在 Pi `/tmp/neje-p0-20261012/venv`，未更改系统 Python；安装日志和包内许可证原文已归档。USB 使用系统现有 [pySerial](https://github.com/pyserial/pyserial) 3.5。通用 ser2net 不实施此白名单，本轮没有安装它；前期排除的伪造 ok、每次连接复位方案没有采用。

Pi 5 Model B Rev 1.0，Raspbian 13.6、内核 aarch64；**用户空间为 32 位 armhf，Python 为 ELF32 ARM**。不能根据 uname 推断整个系统为 64 位。系统 Python 查不到 bleak 是正常的，依赖位于隔离 venv。适配器 BlueZ 5.82；NEJE Linux 地址 `88:7D:F9:55:7B:0F`，主机身份表示与 Mac UUID 不同。

新增 `tools/pi_ble_readonly_probe.py` 默认只打印离线计划；执行仍须带既有隔离确认标志。`tools/check_pi_ble_capture.py` 为纯离线审计，验证固定发送帧、仅两种查询、响应 CRC、连续计数、交付一致性、错误计数及关闭状态。真实断链工具仅把平台标签改为跨平台；断链行为和白名单未扩大。

## 证据与复验

- `evidence/pi-lightburn/`：USB LightBurn 原始日志、连接中截图、配置前后快照、审计。USB 轮配置仅 WindowState 变化；全部实验结束后与初始快照相比仅 WindowState/WindowPosY 变化，设备配置不变。
- `evidence/pi-ble/`：Linux 架构、适配器、依赖版本/许可、三份 BLE 捕获、正常与在途断链审计、Mac 历史捕获重新审计、最终环境、源码快照及 SHA256 清单。
- `capture-audit.json`：18/274 个解码帧、124/8080 字节核验响应。通知结束时分别残留 11/35 字节不完整尾帧，来自系统信息通知尾部；未用于查询或清理判定，不声称整个通知流全部完整。
- `pending-audit.json`：一份基线交付、第二份仅收到未交付、断链后无桥接写入、拒绝新客户端、最终显式清理。
- `regression-tests.txt`：26 项已有测试通过；这是既有协议/TCP 回归，不代表新增 USB 后端有完整单元覆盖。

```sh
/Users/tyongs/neje-ble312/bin/python tools/check_pi_ble_capture.py
/Users/tyongs/neje-ble312/bin/python tools/check_ble_disconnect_capture.py evidence/pi-ble/linux-ble-pending-01.jsonl
```

结束后 LightBurn 已退出，Mac/Pi 均无测试 4002 监听，Pi 串口 fuser 无可见占用，蓝牙停止扫描，设备最后读回 way=0/app=3。仅取消本轮端口转发，用户 SSH 主连接保留；Pi 临时脚本/venv 保留供复验，没有安装常驻服务。

## 下一步结论

树莓派已证明可作为 USB 基线和 Linux BLE 主机，后续开发可以在同一 Pi 上对照两种后端。当前 P0 的固定只读查询、真实 LightBurn 通路、正常退出及主机主动断链状态有实机证据；**初始化无错误、无线衰落/设备断电、USB 拔插、重传恢复、长时稳定和加工均未验收**。

保持当前白名单时，解决初始化错误需要 LightBurn 提供可关闭对应初始化命令的客户端能力；本轮没有找到并验证这种配置。现有上游问题草稿仍仅保存在本地、未发送。开发上应继续实现清晰的“正常可再次连接 / 断链状态待核实”展示和显式恢复流程，避免用自动重发掩盖执行结果不确定性。
