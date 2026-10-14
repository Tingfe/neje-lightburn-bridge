# LightBurn 初始化补查与在途查询断链（2026-10-12）

**初始化无错误仍未通过；真实在途查询断链完成两次验证。** 白名单保持 `$I\n` / `?`，没有伪造执行成功、复位、应用切换、重发未知结果的查询或加工命令。设备最终读回 way=0、app=3。

## 初始化兼容调查的新增证据

前轮已实测本机 LightBurn Pro 2.0.05：普通 GRBL 和 Custom GCode / GRBL 风格都会发送 G0、$I、$#、周期?；关闭自动回零后没有$H，但G0和$#仍在初始化流中。本轮保留该记录，没有为了消除提示修改回复语义。

1. [2025年官方论坛讨论](https://forum.lightburnsoftware.com/t/custom-gcode-not-working-with-fluidnc-but-standard-grbl-ok/183993/17)中，工作人员记录过 Custom GCode / GRBL 的连接问题并建立内部报告。这是其他硬件的调查，不能当作NEJE修复方案，也不能据此认为切换Custom GCode即可解决初始化。
2. 搜索命中的[2026年“advanced connection / observe only”设置](https://forum.lightburnsoftware.com/t/cannot-connect-my-swift-7w-to-lightburn-2-0-0-5/189453/35)实际上属于链接到的第三方 **gcomposer** 诊断控制台，**不是LightBurn的可配置初始化入口**。不把搜索摘要中的设置归给LightBurn。
3. 对本机LightBurn二进制做只读字符串检索，只找到GCodeBlocks、AllowSettingsFetch及自动回零说明，未检出所搜的ConnectionTest / ConnectCommand / StartupGCode等名称。这个检索不能证明不存在隐藏设置，不据此修改偏好文件或宣称反向工程已定位启动逻辑。检索范围与输出已保存。

### 固定版本检查开源实现

| 候选 | 固定版本与实际实现 | 适用性 |
|---|---|---|
| [poesel/lightburn2linuxcnc](https://github.com/poesel/lightburn2linuxcnc/blob/ce67b962297f65f6e001cf80b6749c76e0595459/lightburn2linuxcnc.py) | ce67b962…；GPLv3许可证；连接时构造GRBL banner，源码126/135行直接发送ok，记录G-code供LinuxCNC使用 | 可参考其测试端定位，但它是模拟控制器，不是实际NEJE响应代理；不能采用其成功回复解决当前问题 |
| [enarges/GRBL-Bridge](https://github.com/enarges/GRBL-Bridge/blob/27d5de373394799f67485efa4ced294482d67840/main/main.cpp) | 27d5de37…；GPLv3许可证；main.cpp 311–313行每次新发送端连接时发送0x18以取得真实启动banner | 有真实设备桥接参考价值，但复位违反本轮白名单；没有NEJE可靠BLE封装，不能直接采用 |

源码、README、许可证、提交与散列保存在 `evidence/pending-initialization/`，没有安装或执行这些外部项目，也没有复制其实现到桥接。GitHub API限流、一次许可证下载超时及最初main.c路径404均保留；后续通过git ls-remote固定提交，取得许可证和正确main.cpp，不能把失败的路径查询当作源码不存在。

直接复用的通信实现仍是Bleak 3.0.2；前轮已经借鉴ble-serial的单客户端及断连处理。现有开源参考进一步支持将TCP、设备接收、设备输出和客户端确认分开记录，不能用一个合成ok同时代替这些阶段。

目前没有找到**已验证、符合本轮约束**的LightBurn初始化配置方案。有效方向仍是客户端提供独立连接探测命令与可选初始化查询开关；已保存[上游询问草稿](lightburn-initialization-upstream-draft.md)，**未发送**。不采用以下变通：放行G0/$#、伪造banner/ok、关闭错误显示，或主动发送$I并把其真实ok冒充G0的执行确认。真实响应同样必须保持请求对应关系。

## E21 / E22：真实在途查询断链

沿用用户对激光模组供电物理隔离的确认，未独立测量无出光。实验脚本 `tools/check_ble_disconnect_host.py --mode pending` 默认离线，实际执行需要 `--execute --laser-power-isolated --log 新路径`。

每轮先完成一次真正的$I并核对设备计数，建立基线。随后TCP客户端发送**一条新的$I**，在真实 `write_gatt_char(response=False)` 返回后，测试子类立即调用真实 `BleakClient.disconnect()`；没有模拟BLE方法、通知或is_connected。桥接本身没有新增故障注入开关，也没有自动重连或恢复逻辑。

GATT无响应写返回仅表示主机调用完成，**不等于设备已接收**。注入点位于桥接尚未完成第二条查询响应验证和TCP交付之前；是否到达设备由独立重连后的计数证据判断，而不是由写调用推断。

| 项目 | E21 / pending-host-01 | E22 / pending-host-02 |
|---|---|---|
| 北京时间 | 17:24:03–17:24:05 | 17:26:36–17:26:38 |
| 基线wCheck / rCheck | 139 / 7665 | 145 / 7789 |
| 断链后独立查询计数 | 142 / 7727 | 148 / 7851 |
| 相对基线增量 | +3 / +62 | +3 / +62 |
| 断链后路由 | way=2 | way=2 |
| 收/发错误 | 0 / 0 | 0 / 0 |
| 第二条查询TCP交付 | 0字节 | 0字节 |
| 断连回调至TCP关闭 | 0.001126秒 | 0.000725秒 |
| 明确关闭后读回 | way=0、app=3、142/7727 | way=0、app=3、148/7851 |

两轮在第二条查询发出之后都没有收到新的BLE通知，TCP仅收到基线查询的真实62字节响应，随后EOF。桥接记录cleanup_unconfirmed，拒绝第二客户端，没有尝试在已断开的链路上发送关闭命令，没有自动重连，也没有把第二条查询记为query_verified或tcp_delivered。

停止该轮TCP监听后，实验进行一次**独立显式状态检查**：重新验证同一设备model55/app3；GetStatus读回way=2及上述计数；间隔0.2秒和0.5秒再次查询，三份设备状态一致。核对wCheck增量属于唯一在途3字节请求、rCheck增量处于该已知$I响应长度范围内后，显式关闭本实验通道，再读回way=0/app3。关闭前后计数不变，全程没有补发$I或ReadFrom。

### 结论与边界

- 设备计数推进3字节请求和62字节输出，而主机没有得到第二条响应。这说明客户端超时/EOF不能判断请求未执行，不能据此重发。62字节增量与基线$I响应长度相符；本轮没有取回断链后的输出缓存，不声称验证了第二条输出的具体文本。
- 重新连接查询时way=2；仍不能观测无链路期间的瞬态状态，也不能排除固件在重连时恢复状态。确认关闭必须依赖显式SetStatus(0)后的读回。
- 桥接的安全行为是终止会话、保留结果不确定状态并拒绝新客户端；计数窗口核对和显式清理是实验脚本步骤，不是可投入使用的自动断线恢复。
- 两轮都属于**主机主动断开真实CoreBluetooth链路**、查询写入后响应验证前的注入点。不是无线衰落、远端断电、收到部分响应后断链或重传验证；毫秒级关闭时间是这两轮样本，不是性能保证。

## 验证与保存位置

- 原始记录：`logs/ble-disconnect-pending-host-01.jsonl`、`02.jsonl`，对应stdout记录保留。
- 离线审计：`tools/check_ble_disconnect_capture.py 捕获路径`；精确白名单帧、连续rCheck、原始notify重解码、仅基线响应交付、断链后零桥接写入、唯一在途请求、两次状态复查和最终关闭计数均核对。报告在 `evidence/pending-initialization/pending-capture-audit*.json`。
- 旧空闲断链记录回归：`idle-capture-regression.json`通过。
- 26项离线测试通过，`logs/tests-pending-final.txt`；新增在途BLE丢失时不交付、不重试、不伪称清理完成的测试。
- 研究资料：`research-web-extract.json`、`official-forum-extract.json`、`opensource-pinned-sources.json`、`correct-main-source.json`、`license-retry.json`、本机静态检索与检索范围记录；失败记录如实保留。
- `end-state.json`保存本轮结束检查，`manifest.json`保存相关文件散列。LightBurn配置本轮未修改，未启动LightBurn做重复界面试验；4002及实验监听无残留，最后状态way=0/app3、148/7851。

下一阶段仍需客户端初始化配置入口或厂商答复；无线衰落、设备断电、部分响应断链、缓存恢复、重传、长时稳定和加工/物理安全未验收。
