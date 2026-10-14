# P0 调查结论（2026-10-12）

## 结果与边界

**P0 通过：不依赖官方 App，在真实 NEJE 5 Plus 上通过 BLE 完成两次可重复、可解释的应用层查询。** 两次分别重新连接，均收到 verify、系统基本信息、扩展信息、诊断信息、应用表、状态标志共六帧，完整重组，无剩余字节。

没有实现 GRBL 转发、TCP 服务或 LightBurn 通信；没有执行应用切换、运动、回零、出光、配置修改或固件升级。当前只读查询程序是 P1 的基础，不能把一次短时重复验证视为稳定性验收。

## 本机环境（本轮核实）

| 项目 | 结果 |
|---|---|
| macOS / CPU | 27.2，build 26B5101f，arm64 |
| Python | `/Users/tyongs/neje-ble312/bin/python`，3.12.15 |
| 依赖 | Bleak 3.0.2，ble-serial 3.0.0，pyobjc-core 12.2.2 |
| NEJE 官方客户端 | `/Applications/NEJE 3.app`，v1.0.9，build 10，bundle `com.zhixinjie.neje3` |
| 客户端权限 | 已声明 Bluetooth 和网络权限，启用 App Sandbox |
| LightBurn | 已安装；本轮未启动或连接 |
| 沙箱内蓝牙 | CoreBluetooth 返回 NO_BLUETOOTH，system_profiler 无控制器信息 |
| 宿主环境蓝牙 | 放宽执行沙箱后扫描成功，找到 NEJE 5 Plus，RSSI -77；实验扫描 -78 dBm |

不能将沙箱内 NO_BLUETOOTH 解释为这台 Mac 没有蓝牙。证据：`logs/environment.json`、`logs/official-client.json`、`logs/ble-scan-initial.log`、`logs/ble-scan-host.log`。

## 官方资料与源码

1. [官方 GRBL 连接桥说明](https://wiki.neje99.com/zh/manual/conn-bridge)：连接桥包含于 Windows 软件；可靠模式采用包序号、CRC 和重传。这是另一层 GRBL 传输机制的文字说明，并未公开其确切帧格式。快照 `evidence/web/conn-bridge.html`。
2. [官方 Web 工具](https://app.neje99.com/)：HTML 引用 `js/script.js`，沿公开 ES module import 递归保存 12 个 JS 模块。`evidence/web/modules-manifest.json` 记录各模块 URL、下载时间、SHA-256。
3. [官方 macOS 客户端入口](https://neje99.com/software/neje-control-software-for-ios-macos.html)：NEJE 3 支持 3/4/5 系列，链接 App Store ID 1616299293；不要误用旧 NEJE App（ID 1466589339）。页面快照 `evidence/web/macos.html`。
4. 本机客户端仅检查 plist、签名权限及未解密二进制可见字符串。可见 FFE5/FFE9/FFEA、CommandPicker、CommandResolver、CRC32、BleDataSender、SystemAppInfo，和 Web 模块命名相符。快照 `evidence/native/selected-symbols.txt`。**这些名称不能证明原生客户端逐字节执行了 Web 的同一流程；本轮未捕获原生 App 运行时 GATT 流量，也未绕过任何保护。**

### 关键源文件定位

| 源文件（位于 evidence/web） | 观察 |
|---|---|
| ConnBle.js，ConnectToBleDevice | FFE5 服务；FFE9 发送，FFEA 通知；先订阅，再发送 verify、SystemRead(0xFF) |
| Commands.js，verify / SystemRead | 固定握手及系统读取的 8 字节帧 |
| BleDataSender.js，Fire / GetDataFromQueue | writeValueWithoutResponse；发送块上限 50，目标 2048 字节/秒；命令可合并或拆分 |
| CommandPicker.js，ProcessNewData | 缓存通知流，以 FF AA/AB、长度、尾字节提取完整帧 |
| Tool.js，CheckHelper | 非反射多项式 0x04C11DB7，初值 FFFFFFFF，无最终异或 |
| CommandResolver.js，ResolveCommand | verify 及系统子类型字段偏移 |
| script.js | 显示 apps[1] 为 System、apps[2] 为 NEJE、apps[3] 为 GRBL |

官方 Web 首页“连接成功”的标志只是命令入队后的状态，并不等待验证响应。因此本项目以真实返回帧而非其 UI 状态作为成功判据。

## 为什么直接 `$I\n` 没有返回

已验证：此设备在当前状态接受官方二进制握手和系统查询，且通过 FFEA 返回二进制帧。此次 current_app=2；按官方 Web 的应用索引，它属于 NEJE 应用，GRBL 位于 ID 3。

据此，**把 FFE9 当成即连即用的 GRBL ASCII 透明串口没有依据**。此前 `$I` 未采用官方连接初始化与系统协议，是合理解释方向；当前应用选择也可能有关。但本轮没有进行“只握手后重试 `$I`”或模式切换的单变量实验，不能断言究竟是哪一个因素导致此前无响应，更不能认为任意 GRBL 文本放入本帧就能工作。

## 开源项目优先评估

以下已通过 GitHub API 保存仓库元数据、commit 与 README；可取得的 LICENSE 一并归档于 `evidence/github/<owner>--<repo>/`。

| 项目 | 许可证 / 适用性 | 决策 |
|---|---|---|
| [Bleak](https://github.com/hbldh/bleak) | MIT；macOS CoreBluetooth 通用 BLE | 直接复用已安装版本，本轮真实实验基于它 |
| [ble-serial](https://github.com/Jakeler/ble-serial) | MIT；通用 BLE 与串口/TCP 转发 | 已安装，后续借鉴 TCP 生命周期、收发队列；没有 NEJE 握手适配，不能直接当成已可用驱动 |
| [EzGraver](https://github.com/camrein/EzGraver) | MIT；Qt 串口，旧 NEJE 协议 v1/v2/v3；已归档 | 可参考工具组织与旧协议历史，未发现支持本机 NEJE 5 Plus BLE 的证据 |
| [NejeLaserEngraver](https://github.com/AndyQ/NejeLaserEngraver) | API 标示 MIT，根路径 LICENSE 未取得；DK-8-PRO / USB | 不复用协议；代码复用前需进一步核实许可证文件 |
| [nejePrint](https://github.com/AxelTB/nejePrint) | MIT；Python 串口工具 | 属于旧 USB 路线，不将其控制字节发给本机 |
| [neje-engraver](https://github.com/alexkuklin/neje-engraver) | GPL-3.0；57600 串口、旧单字节控制协议 | 不适用于现有证据；不复制其控制实现 |

搜索范围：GitHub repository search 的 NEJE bluetooth / NEJE laser / NEJE protocol，以及公开网页搜索。在此范围内未找到有 NEJE 5 Plus 原生 BLE→GRBL 实测证明的现成开源桥；这不是全网不存在的结论。

NEJE 官方 JS 可公开读取，但未发现开源许可证。这里保留为本地研究证据，不将整个官方实现作为可再发布的项目代码。本轮工具只独立实现已观察到的两条查询及必要解码。

## 后续最小工作

1. 基于已捕获的只读流量完善 P1 信息探针和错误判定；当前短时重复只证明可重现，未验证长时稳定性。
2. 调查真正的 GRBL 通道及可靠模式：优先公开资料或官方软件正常操作流量。Web 更新工具里虽有 SwitchApp，但本轮没有调用；不能将它当成已确认的 GRBL 桥接方案。
3. 区分 NEJE 系统协议的尾字节校验和官方连接桥的 GRBL 序号/CRC 协议，后者尚未知。
4. 获得真实 GRBL 请求/应答及传输映射后，再进行 P2 TCP 和 P3 LightBurn 只读联调。

## 第二轮进展

后续已定位官方 Windows 的 GRBL 可靠帧，三次实机取得桥接状态；真实 GRBL 文本仍未验证，且发现应用 ID 变化待现场澄清。最新证据及暂停边界见 [GRBL 可靠协议调查](grbl-reliable.md)。本文件此前的下一步为首轮历史状态。

## 授权实机验证更新（16:30）

用户已确认此前切换过模式并隔离激光模组供电。临时可靠 BLE 通道中的一次 `$I` 已通过，返回 GRBL 1.1f / NEJE v2.3.2 与 `ok`，计数3/62、错误0，关闭后 way=0 已读回。详见 [GRBL 调查](grbl-reliable.md) E11；旧的待授权状态保留为历史，重传/TCP/LightBurn仍未验证。

## TCP 与 LightBurn 更新

后续已打通白名单 `$I` / `?` 的 BLE→TCP→LightBurn 查询通路，并实测EOF/RST/SIGTERM关闭读回；原文未开始/未验证的TCP与LightBurn状态保留为历史。见 [第四轮联调报告](tcp-lightburn.md)，重传及物理BLE断连仍未验收。
