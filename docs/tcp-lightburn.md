# TCP 桥接与 LightBurn 只读联调（2026-10-12）

## 当前结论

后续更新：[初始化与真实BLE断连验证](init-disconnect.md)。已完成真实主机侧BLE断连；重新连接查询时设备way仍为2，独立显式关闭后读回0。Custom GCode未消除G0 / $#初始化拒绝提示。下文“物理BLE尚未实测”保留为前轮历史状态，以最新报告为准。

**P2 查询 TCP 桥接与 P3 LightBurn 查询联调通过。** 本机 LightBurn Pro 2.0.05 经 `127.0.0.1:4002` → Bleak → NEJE 可靠 BLE 通道，取得真实 `$I` 版本/选项/`ok` 和 `?` 状态响应。只允许这两种查询；初始化 `G0`、`$#` 及测试中的运动、配置写入、复位均在 TCP 入口被拦截。

正常 TCP EOF、异常 RST、客户端退出，以及 LightBurn 保持连接时对桥接发送 SIGTERM，均已实机确认关闭可靠路由并读回 `way=0`。没有发送加工、回零、激光或配置修改命令。激光模组供电隔离沿用用户本次会话的现场确认，未独立测量无出光。

![LightBurn 查询成功截图](../evidence/tcp-lightburn/lightburn-query-success.png)

## 开源与官方参考

优先检查已安装的 [Jakeler/ble-serial](https://github.com/Jakeler/ble-serial) 3.0.0（MIT）：`TCP_Socket` 使用 `asyncio.start_server`、单客户端检查和 TCP→BLE 接收回调。对应源文件、包元数据及 MIT 许可证保存在 `evidence/tcp-lightburn/ble-serial-*`，以版本和散列固定本地参考。

本项目直接复用开源 Bleak；TCP 层借鉴 ble-serial 的标准 asyncio 流接口与单客户端结构，没有直接调用其通用原始字节透传。其实现不识别 NEJE 可靠封装，也没有适合本轮的完整退出清理。独立实现仅补足查询解析、串行队列、NEJE 字节计数核对和关闭读回，不增加通用 G-code 通道。

[LightBurn 官方 GRBL 网络连接指南](https://docs.lightburnsoftware.com/2.2/Guides/GRBLNetworkConnection/) 和[设备基础设置](https://docs.lightburnsoftware.com/2.0/Reference/DeviceSettings/BasicSettings/) 支持 GRBL Ethernet/TCP 及可配置网络端口。页面原文、URL、抓取时间和散列见 `evidence/tcp-lightburn/sources.json`。指南版本为2.2/2.0，本机实际界面与行为以2.0.05实测为准。

## 实现与运行

- `tools/tcp_readonly_bridge.py`：默认离线计划；显式执行后只绑定 IPv4 回环地址，单客户端；初始设备必须已处于 app=3、way=0，拒绝接管其他客户端。
- TCP 按流解析，不把一次 read 当作完整命令。仅精确 `$I` 加 CR/LF，及行外实时 `?` 可进入队列；其他输入记录 HEX 并返回桥接本地拒绝。
- 有界队列16条、行长128字节、响应上限8192字节，逐条处理，无重试/自动重连/自动续传。每次先验证响应语义与连续 rCheck，再查询设备计数，匹配后才交付 TCP。
- TCP EOF/异常、会话超时、SIGINT/SIGTERM 或服务寿命结束，取消处理任务并在 finally 中 SetStatus(0)、GetStatus、SystemRead，确认 way=0 后断开 BLE。
- BLE 已丢失或关闭读回失败时记录 `cleanup_unconfirmed`，拒绝后续客户端；不把 TCP 已关闭误称为设备路由已关闭。需人工检查后另开进程，不自动接管。

```sh
cd /Users/tyongs/code/NEJE
# 只查看计划
/Users/tyongs/neje-ble312/bin/python tools/tcp_readonly_bridge.py
# 在用户确认激光模组供电隔离后运行；默认300秒结束、每会话120秒
/Users/tyongs/neje-ble312/bin/python tools/tcp_readonly_bridge.py --execute --laser-power-isolated
```

日志默认使用新文件且禁止覆盖。`--port` 只改变端口，不允许改成对外监听。首次扫描约10秒，BLE会话在TCP连接后建立；结束实验时应关闭桥接并核对日志中的清理结果。

## LightBurn 配置与观察

新建独立配置 `LaserLink ReadOnly TEST`：GRBL、Ethernet/TCP、127.0.0.1、NetworkPort=4002；关闭自动回零（配置字段 `HomeOnStartup=false`）、DTR、点火及返回结束位置。保留位置轮询用于只读验证。原有 No Machine / GRBL 配置保留；新配置未设为默认设备。

尺寸仅供查询测试，向导输入1×1后 LightBurn 实际夹限为10×10 mm；不是实测加工行程。不要将该测试配置用于加工。配置读回：`evidence/tcp-lightburn/lightburn-test-device.json`；应用前后偏好快照分别保存为 `lightburn-prefs-before.ini` 和 `lightburn-prefs-after.ini`。

LightBurn 初始化先发 `G0\n`，之后发 `$I\n`、`$#\n` 及周期 `?\n`。仅返回说明性 MSG 的初版让其重复 G0，阻塞了后续查询。最终版本对禁止输入返回：

```text
[MSG:LaserLink read-only policy blocked input; local error follows]
error:20
```

这是**桥接生成的本地策略错误**，不是控制器故障或设备执行结果；不返回伪造 `ok`。实际界面显示红色错误提示，但继续了白名单查询，显示真实版本、`Target buffer size found` 和 `ok`。既验证自动 `$I` 和状态轮询，也验证手动输入 `$I`。本轮不通过允许 G0 或 `$#` 消除提示，白名单始终只有 `$I` / `?`。

## 实机证据

全部时间为北京时间；原始 JSONL 为 UTC。

| 实验 | 实测结果 | 证据 |
|---|---|---|
| E13，16:35:50–16:35:54 | TCP分片 `$I`、`?`；第二客户端拒绝；G0 X1 / $40=0 / Ctrl-X仅本地拦截；正常EOF、RST后均读回way=0 | logs/tcp-bridge-host-01.jsonl；logs/tcp-client-host-01.json |
| E14，16:37:55–16:39:36 | LightBurn TCP已连接；初版仅MSG拒绝使G0重复；未向BLE转发G0；退出客户端后关闭读回 | 同上；evidence/tcp-lightburn/tcp_readonly_bridge-initial.py |
| E15，16:40:22–16:40:53 | 最终版本LightBurn联调：2次$I、43次?成功；G0与$#各拦截一次；全部计数/错误检查通过 | logs/tcp-bridge-host-02.jsonl；lightburn-query-success.png |
| E16，16:40:53 | LightBurn仍连接时SIGTERM；session_cancelled到cleanup_verified约0.349秒；way=0、app=3、计数59/3165；正常结束 | logs/tcp-bridge-host-02.jsonl |
| E17，16:41 | 最终版本重复TCP分片、拒绝、单客户端、EOF和RST流程，通过；最终关闭计数66/3349，way=0、app=3 | logs/tcp-bridge-host-03.jsonl；logs/tcp-client-host-final.json |

实际 `?` 响应含 `Idle`、4个 MPos 坐标、Bf、FS、Pn，部分轮次包含WCO；按原文转发，不把4坐标擅自截为3坐标。

首轮 SIGTERM 暴露 Python 3.12 下退出顺序问题：先 await server.wait_closed 会等待仍连接的客户端，导致未及时取消会话。最终版本改为先关闭监听、取消并等待会话清理，再等待 server.wait_closed；E16实机复验成功。初版日志和源码保留，不能把首轮延迟关闭记为即时清理成功。

## 测试及剩余边界

25项离线测试全部通过，日志 `logs/tests-tcp-lightburn-final-25.txt`。包括任意TCP分片位置、命令白名单、超长输入、非零计数及回绕、查询未返回时EOF、任务取消、模拟BLE丢失后不宣称关闭且拒绝新客户端、服务器退出不等待客户端先主动断开。旧No Machine / GRBL两份设备配置逐项比对保持一致，见 `preferences-comparison.json`。

真实原始日志另经 `tools/check_tcp_capture.py` 离线回放、白名单帧/计数/错误与每会话关闭核对，报告 `evidence/tcp-lightburn/capture-audit.json`。

回放审计另记录了部分会话结束时35字节未完成诊断帧：SystemRead(FF)会返回多种诊断，清理程序收到完整way=0状态及system_info/app=3后停止订阅，可能截断随后诊断子帧；不把这些尾部诊断称为完整采集。GRBL查询响应及关闭确认帧均完整且已核对。每个BLE会话使用独立解码缓冲，残片不带入下一连接。

当前验收范围是查询通路和可确认的主机侧断连清理。实际无线链路丢失/设备断电行为尚未实测；模拟测试不能替代物理BLE断连验证。重传、去重、缓存极限、长时运行、加工/急停/物理无出光仍未验收。若需要无红色初始化错误的兼容性，下一步应研究 LightBurn 可配置初始化，而不能擅自扩大设备白名单或伪造执行成功。

本轮结束时已退出LightBurn、停止所有本次TCP桥接，最后设备关闭读回way=0。没有常驻服务或对外端口。
