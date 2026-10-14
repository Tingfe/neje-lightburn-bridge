# 实验记录

所有时间为 2026-10-12，北京时间（UTC+8）。JSONL 同时包含 UTC 和相对秒数。

| 编号 | 操作 | 结果 | 原始证据 |
|---|---|---|---|
| E01 | 沙箱内环境检查、BLE 10 秒扫描 | Python 依赖具备；蓝牙 NO_BLUETOOTH，未连接或写入 | logs/environment.json；logs/ble-scan-initial.log |
| E02 | 宿主环境只读扫描 | 发现 NEJE 5 Plus，RSSI -77；排除“宿主机器不支持蓝牙”的解释 | logs/ble-scan-host.log |
| E03 | 16:09:51，第 1 次独立连接，先订阅 FFEA，发送官方 verify、SystemRead(FF) | 握手约 59 ms 后返回；系统查询约 52 ms 后首包返回；得到六帧，重组缓冲清空 | logs/p0-official-init-host.jsonl，trial=1 |
| E04 | 16:10:04，重新连接，重复 E03 | 同样六帧；握手及应用版本一致，诊断动态字段不同；缓冲清空；16:10:14 主动断开，正常结束 | 同上，trial=2 |
| E05 | 官方 JS 生成命令 / CRC，并对两次实测通知离线解码 | 两条发送帧一致；CRC check 一致；两次各 6 帧与 Python 逐字节一致 | logs/official-js-check.json |
| E06 | Python 离线测试 | CRC、噪声重同步、通知分片/合并、校验错误、AB 长帧、坏长度、实测回放 | logs/tests.txt |

原始 HEX 含设备标识字段，仅存于本地证据；外部分享前按需要脱敏。E03/E04 是应用层日志，不是底层 HCI 抓包，不含官方原生 App 的运行时流量。

本轮没有再发送 `$I`、没有切换应用，也没有运动/出光；不能用 E03/E04 确定此前 `$I` 失败的唯一原因。

## 第二轮：GRBL 通道调查

| 编号 | 操作 | 结果 | 证据 |
|---|---|---|---|
| E07 | 下载官方 Windows v6.8.5，离线读取 .NET 元数据/方法体 | 定位 GRBL 0x40 组，计数、CRC、收发及重传；未执行程序 | evidence/grbl-research/NEJE_v6.8.5.metadata.json；il/ |
| E08 | 16:15:56，系统查询后发 GetStatus | 真实返回 `FF AA 0D 41 01 00 00 00 00 00 00 00 55`；查询前 app=2 | logs/grbl-status-readonly-host.jsonl |
| E09 | 16:18:23 / 16:18:36，两次独立连接重做状态查询并读回系统 | 两次 way=0、计数/错误0；每次应用前后均为3；**与 E08 查询前 app=2 不同，原因未确认** | logs/grbl-status-repeat-host.jsonl |
| E10 | 独立 JS 参考编码与 Python 对比、实测状态回放、白名单及计数回绕测试 | 17 项测试通过；仅离线，未发送 GRBL 文本 | evidence/grbl-research/offline-packet-vectors.json；logs/tests-grbl-research.txt |

E08 的“只读”指官方主机方法的命名与预期用途，不代表已排除固件状态副作用。应用变化出现后已停止进一步实机写入，等待现场信息；未执行 SetStatus、SwitchApp 或 `$I`。详细分析见 `docs/grbl-reliable.md`。

## 第三轮：现场澄清后授权实验

用户确认 E08/E09 之间切换过模式，并明确确认激光模组供电物理隔离、允许临时启用可靠通道仅查询信息。上文“原因未确认”“未执行 `$I`”为第二轮结束时的历史状态。

| 编号 | 操作 | 结果 | 证据 |
|---|---|---|---|
| E11 | 16:30:06，verify/SystemRead/GetStatus；SetStatus(2)并读回；一次 `$I`；GetStatus；finally SetStatus(0)并读回/SystemRead | 返回 GRBL 1.1f / NEJE v2.3.2、OPT 与 ok；wCheck=3、rCheck=62、错误0；关闭way=0；app前后3；正常断开 | logs/grbl-build-authorized-host-20261012-01.jsonl |
| E12 | 离线审计 E11 全部发送标签、通知流全部切分位置、数据CRC、计数和关闭状态 | 通过；无额外 BLE 操作 | evidence/grbl-research/authorized-capture-audit.json；tools/check_grbl_capture.py |

## 第四轮：TCP 与 LightBurn

E13（16:35）：TCP分片/查询白名单/第二客户端/EOF/RST实测通过。E14（16:37–16:39）：初版MSG拒绝使LightBurn重复G0，且服务退出顺序延迟清理；初版证据保留。E15/E16（16:40）：修复本地拒绝应答和退出顺序，LightBurn 2次$I、43次?通过，保持TCP连接时SIGTERM约0.349秒读回way=0。E17（16:41）：最终版TCP流程复验通过。25项离线测试；真实回放与详细证据路径见 [联调报告](tcp-lightburn.md)。物理BLE链路丢失未实测。

## 第五轮：E18–E20 初始化与真实BLE断连（2026-10-12）

详见[报告](init-disconnect.md)：E18 Custom GCode实际发G0/$#/$H，均拦截，1次$I和49次?成功；E19真实CoreBluetooth断连时TCP仍打开，桥接0.10秒后关闭TCP并拒绝新客户端，独立重连发现way=2，显式关闭读回0；E20关闭自动回零后复验，无$H，G0/$#仍拒绝，1次$I和12次?成功，最终way=0/app3。原始日志、只读白名单审计与配置落盘证据在evidence/init-disconnect及logs对应文件。无错误初始化、无线衰落、设备断电、在途查询断链仍未通过验收。

## 第六轮：E21/E22 在途查询断链（2026-10-12）

17:24与17:26两次真实主机侧断链：基线$I完成后发新$I，真实GATT写返回即断链；第二条查询未交付TCP，设备计数却分别139/7665→142/7727、145/7789→148/7851。独立重连三次GetStatus均way=2、计数一致、错误0；显式关闭后读回way=0/app3。详见[报告](lightburn-pending.md)及logs/ble-disconnect-pending-host-01、02.jsonl。26项测试与两份捕获离线审计通过；初始化兼容补查及开源候选排除依据已保存，未伪造成功、未重发、未复位。

## 第七轮：E23–E26 树莓派双后端对照（2026-10-12）

18:03–18:09，真实 LightBurn 经 Pi USB 完成 1 次 $I/19 次 ?，经 Pi BLE 完成 1 次 $I/121 次 ?；两条通路同样拦截 G0/$#。Pi Linux 独立 BLE 查询通过；60 秒会话超时和 90 秒服务总时限均关闭读回 way=0，期间 LightBurn 主动再连一次。Linux 在途断链时第二份响应收到 62 字节但未核验、未交付，设备计数 279/16117→282/16179；拒绝新客户端，独立显式关闭后 way=0/app3。26 项回归及新捕获审计、旧 Mac 捕获重新审计通过。详见[对照报告](pi-lightburn-ble-comparison.md)。

## 第八轮：E27/E28 统一调试工具与显式恢复（2026-10-12）

E27：统一 CLI 完成 USB $I/? → 断开 → BLE $I/? → 断开。初次实测发现 BlueZ 连接阶段断开回调后 Bleak 继续连接的问题，失败记录保留；工具现于首个回调取消连接，修复版实测通过。E28：统一工具基线 $I 后第二次 $I 在途真实断链；fault 状态拒绝 build/status/connect usb/connect ble；显式 recover 读回 way=2，检查计数及三次稳定状态后关闭至 0/app3，不重发；随后另行 connect ble/status 成功并正常关闭。工具及限制见[说明](readonly-debugger.md)，捕获/审计位于 evidence/readonly-debugger。


## 第九轮：E29–E31 初始化、参数和受控运动（2026-10-12）

E29：授权裸 G0/$# 后，USB 基线与真实 Mac BLE→LightBurn 初始化均通过，无伪造 ok、无 error:20；EOF 关闭读回 way=0/app3。E30：另行授权 $$/$G 后 USB/Mac BLE 设置及模态逐字节一致。E31：现场授权的 USB/Mac BLE 各一次 X 0→1→0 mm 对照完成。首次 USB 真实 ok 后带温度通知，脚本误判并停止；只读确认 X=1 后，用户单独授权剩余返程，没有重发前进。Mac BLE 完整往返、计数/CRC/终点及 way=0/app3 清理审计通过。详见 [初始化](lightburn-init-result.md)及[运动](one-mm-motion-result.md)报告。证据位于 evidence/lightburn-init-profile。完整 LightBurn 作业/实际加工仍未验收。

E32：真实 LightBurn 控制台新增一次 1 mm 对照。首次前进真实 ok 与31字节温度通知分帧，旧计数核验清空通知导致停止，way=0/app3；只读确认 X=1。用户另行授权剩余返程，修复版在相同温度分帧情况下真实返程成功、裸 G0 成功、EOF 清理通过。两会话合计前进/返程各一次；连续无异常完整往返未验证。详见 [控制台实测](lightburn-console-motion-result.md)。57项回归通过。

E33/E34：持续 BLE 字节层完成 Mac 5 次 $I/6 次 ? 和真实 LightBurn 初始化复验；分别 725/2913 接收字节全部通过 CRC、连续序号和设备计数核验，正常清理 way=0/app3。68 项离线回归通过。E35：真实 LightBurn 离线导出暴露默认 20% 功率与 Y5 定位，未执行；零功率 Y400 候选因 10×10 mm 测试工作区被拒绝导出，尚无候选 GCode。详见 [持续层报告](stream-transport.md)。未新增运动，未验证新层实机断链或完整作业。

E36：独立 260×400 mm 审阅配置实际导出成功；首版仍有 M8/M9，界面修正自定义模板后第二版包含起始 M5/S0、M4 S0、无气辅，X0→1→0/Y400/F60。保存原生设备包、工程、GCode、散列及离线固定顺序策略。73 项回归通过，策略未接入硬件入口，没有新增实机运动。见 [作业审阅](lightburn-zero-power-job-review.md)。

E37：固定作业桥接接入持续数据层和内部预检/完成核验；模拟验证应答隔离、完整小作业、在途断链不返程/不重放、EOF、第二连接拒绝及偏移变化拦截。E38：新通道仅实机读取 $$/$G/$#/? 各一次，USB/Mac BLE 设置、模态、参数逐字节一致，Idle/X0/Y400/FS0；BLE862字节全部核验后 way=0/app3，USB串口关闭。新增作业仍待授权，未实测。详见 [报告](reviewed-job-bridge.md)与 [固定对照方案](lightburn-reviewed-job-plan.md)。

E39：用户授权固定作业后，USB与真实LightBurn BLE各一次14行X0→1→0/Y400/F60/M4S0对照，终点/模态及完整会话审计通过，正常关闭。E40：用户表示未看到并要求同范围重复，USB/BLE各再执行一次；控制器完成核验一致，LightBurn显示完成。退出时取消在途?，未发送一个空行被丢弃；关闭读回way0/app3，未重放。第二对不计完整查询审计通过。现场原话“1mm的行程，根本看不到，似乎听到有响应，无出光”，物理位移仍未确认。82项回归通过，见[固定作业结果](lightburn-reviewed-job-result.md)。

E41：修复正常EOF仅有界收尾已开始的状态查询；真实Mac BLE/TCP仅两个?查询，EOF后真实响应及152字节全部核验，未发送空行丢弃，关闭way0/app3 w1364/r45678，无重发/不确定写，没有运动。新增超时及BLE断连的离线模拟。详见[EOF收尾结果](eof-query-drain-result.md)。

E42/E43：新增流水传输核心，Mac BLE与Pi USB各固定16条$I批量+2次?。16份VER/OPT/ok逐字节一致；BLE1131字节全部核验后way0/app3 w1414/r46809，USB1116字节串口关闭。瞬时Bf/Ov状态不同未混为一致；无新增运动。102项回归通过，6000行与实时控制字节均只模拟。见[流水传输结果](grbl-pipeline-result.md)。

E44/E45：流水核心接入真实Mac BLE→TCP→LightBurn，初始化无协议拒绝错误；4051/1579字节全部核验，分别59/22个真实终端FIFO映射通过，way0/app3。E44退出后服务继续监听导致2158次重连拒绝；清理完成即关闭监听后E45为0次，LightBurn正常退出。10mm零功率候选原生导出/离线审阅完成，未授权/执行。见[流水接入结果](pipeline-lightburn-result.md)。

E46：固定10mm运行入口、前后屏障、45秒超时与整行消息交付接入，默认仍不开放运动。Pi USB/Mac BLE各仅$$/$G/$#/?四项预检，设置/模态/参数一致，Idle X0/Y400/FS0；BLE847字节全部核验后way0/app3 w1601/r53286，USB串口关闭。130项离线回归通过，新增实际运动未授权/执行；用户要求加快后收敛下一步为直接可观察的10mm固定对照。见[准备报告](ten-mm-runtime-result.md)。

E47：用户另行确认现场条件、授权USB/真实LightBurn BLE各一次10mm零功率固定作业。20:05–20:08两路均14条真实作业终端、相同命令字节/终点/模态，约20秒往返；USB1275字节、BLE8729字节捕获审计通过，BLE way0/app3 w1989/r62015，TCP关闭、LightBurn退出，0不确定写/重放/重连拒绝。用户原话“两次均正常往返，无其他轴异常、无出光”，可见机械观察通过，未独立尺量。最后17项专项通过，授权次数用完。见[实测结果](ten-mm-job-result.md)，通用作业/控制/加工未验收。

E48–E50：真实 LightBurn 控制采集只在本机拦截；Idle/只读场景停止发0x18，暂停/恢复没有有效运行态证据。首次512行信息查询暴露1400字节接收缺口及关闭任务被并发错误打断，停止不重放；独立核查原关闭已到way0/app3。新增查询前后屏障、64行数量上限及受保护的有界关闭。第二会话真实error:3与故障前残留半行相符，停止且清理通过；第三会话初始化及同文件512条查询全部通过，加初始化共513次$I，45305字节完整审计通过，way0/app3 w4480/r55386，队列清空。138项全套及随后2项归属/6项捕获专项通过。没有新增运动/出光或实际复位。见[控制采集与修复报告](lightburn-control-capture-result.md)。

E51：新增控制状态机与受限固定作业入口；暂停冻结普通发送、确认Hold后恢复、取消清空队列及启动行/状态确认、复位异常不重试。34项新增测试覆盖真实回环TCP/实际NEJE协议代码/模拟设备，全套177项通过；新增集成轨迹明确标记simulated。没有新设备连接、运动或实际控制；$N及控制实验仍需下一轮具体范围确认。整体估算约50%，详见[本轮结果](control-state-machine-result.md)。


## 2026-10-12 E52：暂停/恢复及空闲复位实测

USB、真实LightBurn BLE各一次固定10mm零功率作业已执行，两路$N空块、Hold:0→恢复、完整$G/G0/$G收尾、Idle下0x18及新启动行/Idle均通过独立原始捕获审计。用户确认两次均正常暂停后恢复并返回，无其他轴异常、无出光。BLE最终way=0/app=3，串口/TCP/LightBurn均关闭，193项回归通过。LightBurn实际回程暂停18.164秒，偏离计划前进约2秒，已记录且未补跑。运动中停止、运动在途意外断连及实际加工未验收。本轮两次授权已用完；整体估算约55%，最终目标仍进行中。详见[报告](controlled-job-parity-result.md)及`evidence/control-completion/`。


2026-10-12 E53：新增明确的本机会话废弃与关闭期间TCP输入封闭；USB运动停止候选及独立审计已实现。205项回归通过，含变长零功率文件离线审阅及8/166条核心模拟。树莓派最终62文件候选散列核验通过，未开串口；新增USB运动停止现场问题已发出，尚未执行。运动中停止及在途BLE断连实机验收未完成。整体预估保持约55%。详见[准备结果](motion-cancel-preparation-result.md)及[现场方案](motion-cancel-plan.md)。


2026-10-12 E54：214项完整回归通过。受限变长零功率文件接入实际桥接候选，默认离线，执行必须摘要匹配及单独文件/控制授权；动态活动预算、暂停计时与失败退出已接入。166条/虚拟100秒及8,006条/76,021作业字节的真实回环TCP+NEJE实现模拟通过独立审计，发送计数跨界两次。没有新增设备连接、运动或复位；实际LightBurn变长文件、运动中停止、在途BLE断连及加工仍未验收。整体工程估算约60%，详见[文件入口结果](reviewed-file-runtime-result.md)。


2026-10-12 E55：USB变长受限文件入口接入同一审阅、摘要锁定及收尾核心，支持有界增量入队；166条和8,006条USB模拟分别独立审计通过，与E54 BLE模拟作业字节及终端命令顺序一致。新增BLE接受数据块后、零响应/单字节未核验响应断连模拟，核验失败状态、计数保留、本机废弃和设备关闭未确认；修复清理异常覆盖原传输原因。219项完整回归通过，旧E52实机捕获复审通过。没有新设备连接或动作，现场停止问题仍待答复，整体约60%。详见[同文件及断连结果](reviewed-file-parity-result.md)。


2026-10-12 E56：显式XY零功率直线文件审阅及USB/BLE候选接入，按文件包围盒同时核验X/Y，最终确认X0/Y400；二维执行有新增独立门槛，旧一轴范围不扩大。225项回归通过；10mm/25mm轮廓两路模拟各自独立审计，34份几何文件与固定版本cncjs参照一致，圆弧端点反例已归档。未连接设备或运动，实际二维/停止/在途断连及加工仍未验收。整体约60%（权重60/100），见[二维结果](xy-linear-result.md)。


2026-10-12 E57：生成10×10mm二维零功率LightBurn工程输入候选，XML/层参数/闭合索引核查通过；宿主只读确认LightBurn未运行、4002无监听。电脑控制工具报告Mac锁屏，手动解锁问题已发出，实际应用打开及原生二维导出未完成。官方资料确认优化器可能改变切入点，后续必须以真实导出核验。未连接设备或动作，整体仍约60%。见[离线导出准备](lightburn-xy-export-preparation.md)。


2026-10-13 E61：用户已解锁，真实LightBurn原生二维工程保存、重开及GCode导出完成。发现E57候选在原生读取时丢弃图形，保留失败副本并改用SVG原生导入。新10×10mm闭合文件16条命令、40mm/F60/S0，起终点X0/Y400，无位移G0；现有白名单、固定CNCjs参照及USB/BLE同文件模拟独立审计通过。未连接设备或运动；最近全量回归仍239项，整体约60%。见[原生二维导出结果](lightburn-xy-native-result.md)。


2026-10-13 E62：本轮确认既有Pi SSH主会话存活、USB节点存在，新建独立临时目录并逐项核验当前62份工具源文件及原生工程/GCode共64份散列。Pi USB、Mac BLE入口默认离线审阅一致，初始化/状态交错策略检查通过，精确文件和执行门槛已整理。新增二维现场授权问题已发出；未开串口、连接BLE或运动，未新增完整回归，整体约60%。见[二维实机对照准备](xy-native-hardware-plan.md)。


2026-10-13 E63：已授权的Pi USB和真实LightBurn BLE二维零功率矩形各一次完成，两路16条/160作业字节一致、独立原始捕获审计通过，终点X0/Y400、M5/S0及完整私有收尾/关闭通过。现场机械观察待答复，不替代精度或加工验收。修复USB文件构造意外依赖旧固定文件的问题，首次失败未打开串口或提交动作；240项回归通过，新Pi包64文件及构造核验通过。整体约65%（63/100），两次作业额度已用完。见[实机二维结果](xy-native-hardware-result.md)。

> 补录（2026-10-13 收敛轮）：以下 E64–E71 原缺失，据各轮结果文档与 `evidence/` 补录，**非当时落盘**；只记录该轮增量，不重复分数口径（分数见[整体完成度估算](overall-progress.md)）。

2026-10-13 E64：真实LightBurn圆形工程重开及两次相同导出，实际64段圆周G1加两段进出线、78条/1256字节；固定cncjs参照与USB/BLE同文件模拟独立审计通过；未连接设备，不计G2/G3验收。见[曲线离线结果](lightburn-curve-native-result.md)。

2026-10-13 E65：Pi USB、真实LightBurn BLE各一次原生曲线零功率作业完成，78条/1256字节、最终X0/Y400、M5/S0及私有收尾/关闭通过；用户确认两次圆形返回、无明显卡顿、无异常、无出光；关闭含11字节可选系统帧尾部。实际为G1折线，不计G2/G3。见[曲线实机结果](curve-native-hardware-result.md)。

2026-10-13 E66：运动停止独立审计新增BLE后端，原始TCP分帧/ACK/复位失效与新状态及关闭均独立核验；244项完整回归及3份停止模拟审计通过；未新增设备连接、运动或复位。见[停止审计结果](ble-motion-cancel-audit-result.md)。

2026-10-13 E67：新增review/start/status管理会话CLI，复用现有权限门槛与独立审计，实现固定作业副本、独立日志、同根目录进程锁及跨进程故障阻止再启动；254项完整回归及10项新入口验证通过；默认CLI预览未创建会话或连接设备。见[管理入口结果](lightburn-managed-session-result.md)。

2026-10-13 E68：真实管理入口初始化前CUA确认Mac锁屏，未启动BLE/串口或动作；期间完成只读状态阶段与实际进程锁核查，损坏/半行/遗留状态不冒充完成；14项入口及258项完整回归通过。见[状态入口结果](lightburn-managed-status-result.md)。

2026-10-13 E69–E70：仅重新核查，分类为`no_progress`；同一锁屏条件连续三轮，请求将目标标记blocked并保留完整范围；未新增设备动作或完整回归。见[阻塞核查](validation-blocker-status.md)。

2026-10-13 E71：用户已解锁，新管理入口真实LightBurn初始化/正常退出两次及同目录显式再次启动通过，状态ready/锁持有与completed/锁释放一致，way0/app3及端口释放核验；未载入作业、无运动、无出光或复位。见[管理初始化实测](managed-init-hardware-result.md)。

2026-10-13 E72–E73：用户持续允许有效行程内移动；USB固定10mm运动中一次复位，在Run/X2.013触发，真实ALARM:3/启动行/新状态及序列11–14失效、串口关闭独立审计通过，现场确认前进后停住、无异常或出光。位置不可信，无自动返程或解锁。ByteStream选择性关闭INFO=1修复通过263项回归和真实BLE仅一次?的78字节/13帧核验、way0/app3关闭，通知尾部0字节。真实LightBurn运动中Stop、定位恢复和作业在途断连仍未验收。整体约70%（68/100）。见[停止实测](usb-motion-stop-hardware-result.md)、[关闭修复](selective-close-info-result.md)、[持续操作授权](operating-authorization.md)。


2026-10-13 E74–E75：USB实际XY回零恢复及参数/模态/偏移不变通过独立审计；新增真实LightBurn固定作业正常完整返回，但没有发送Stop。修复固定控制profile缺少只读状态轮询，271项回归通过。下一轮CUA确认新锁屏，未连接设备；所有监听和进程已关闭。新增USB恢复证据使可靠性8→9/15，目标保持active，整体约70%（69/100）。见[回零恢复](homing-recovery-result.md)、[停止进展](lightburn-motion-stop-progress.md)。

2026-10-13 E76–E77：新增实际USB G2/G3半圆9条/83字节及两段移动G0+G1文件10条/80字节，分别核验11132和4806接收字节、真实应答、终点、私有收尾和关闭。圆弧155条Run报告符合本次半径5mm范围，但不代表独立机械精度；快速段使用真实重新读取的18000mm/min和250mm/s²配置，未测得实际峰值速度。两路输入为手工协议文件，不计原生LightBurn导出或BLE验收；现场观察待答复。Mac锁屏再次确认，未新增BLE会话；源无改动、无重复全量，整体约70%。见[结果](usb-arc-rapid-baseline-result.md)。

2026-10-13 E78–E81：真实LightBurn BLE运动中Stop、真实ALARM:3/启动/新状态、在途账本失效与清理独立审计通过；用户确认回程中途停住、无异常、无出光。随后以单独USB $HX/$HY恢复Idle/X0/Y400，未自动返程或重放。真实LightBurn又完成9条/83字节G2/G3半圆及10条/80字节G0/G1文件，与USB源字节及提交顺序一致，真实应答/终点/私有收尾/way0清理通过；USB现场正常反馈已收到，新增BLE现场待回复。快速作业退出瞬间未发送的问号及换行明确丢弃，独立审计修正并保留首次拒绝证据，不宣称所有TCP查询获应答；277项完整回归通过。运行代码未变，当前无活动应用/实验进程或4002监听，目标active，整体约75%（73/100）。见[真实停止及恢复](lightburn-motion-stop-hardware-result.md)和[圆弧/G0同文件对照](ble-arc-rapid-parity-result.md)。

2026-10-13 E82–E83：新增真实LightBurn固定作业两种实际BLE断连：第一轮写入返回但设备写计数未增加，保留18字节已核验未成行状态和44字节未核验接收；第二轮完整144字节已核验且Run/X2.125时断连，保留并废弃5条待答。两路原桥接均失败/远端清理未知、无重放或假ACK；独立身份/计数稳定核查后关闭仍为way2的拥有路由，读回way0/app3；随后USB各5条只读确认Idle/X0/Y400、M5/S0。用户确认第二轮断连后仍完成已缓冲往返，无异常或出光。LightBurn第二轮显示Disconnected，但两轮“流传输完成”不能当作业成功；首轮Ready提示仍需改善。287项完整回归通过，可靠性9→11/15，总分73→75/100，对外仍约75%。下一步故障提示/恢复入口、输入半行、长作业、完整日常交付和实际加工。见[断连实测](job-disconnect-result.md)。

2026-10-13 E84–E85：Pi USB与真实LightBurn BLE各完成4007条／145046字节、约200秒同文件五圈零功率作业，源字节及提交顺序、真实应答、终点/模态/清理独立审计通过；用户确认两次连续运行并返回，无异常、无出光。BLE发送计数实际回卷两次、接收一次。既有管理入口首次完成文件实机及独立审计/锁释放，新增只读中文状态提示，4项专项及完整291项回归通过。Pi与Mac源仅状态展示文件不同，两套版本另存。最终Idle/X0/Y400、M5/S0、way0/app3，应用/桥接/端口及可见串口拥有者均关闭；未出光、写设置或故障注入。整体75→78/100，对外约80%；输入半行、完整恢复、更长时间稳定性及实际加工仍待完成。见[长文件结果](long-job-parity-result.md)。

2026-10-13 E86–E87：管理入口新增显式recover，真实LightBurn仅初始化断连后保留requires_recovery并拒绝直接start；同地址及原始计数证明、稳定所属路由关闭、6次只读核查/950字节/CRC与计数/关闭独立审计通过，原失败日志不变且原会话仍失败。随后同目录另行真实初始化，6343字节完整核验及way0/app3关闭、completed/锁释放通过，无作业或运动。300项完整回归通过；LightBurn故障时Ready、恢复后真实Idle通信中仍Disconnected，界面一致性未通过。可靠性12→13/15，总分78→79/100，对外仍约80%；半行/复位定位恢复、更长稳定性、完整日常界面和实际加工仍未完成。见[显式恢复实测](managed-recovery-result.md)。

2026-10-13 E88–E90：新增并真实导入独立原生GRBL配置，原有五份设备配置逐项不变；正常初始化Ready、真实仅初始化BLE断连Disconnected、显式恢复后Ready、受审零功率矩形Busy及完成Ready均实际观察。正常初始化3661字节、恢复只读922字节、矩形16129字节及清理独立审计通过；16条/160字节与E63历史USB顺序一致，现场确认完整四边返回、无异常及出光。旧故障不追认成功，无伪造响应或运行代码改动；最近完整300项回归版本一致，未重复全量。原生GRBL直接Start生成、更多控制/故障、半行/定位恢复、长时稳定和实际加工仍需验证。控制17→18/20，总分79→80/100，对外约80%。见[原生GRBL对照](native-grbl-ui-result.md)。

2026-10-13 E91（进行中，任务被中断）：原生 GRBL 直接开始与同文件 USB 对照。审阅器放开「裸 M4 必须显式 S0」，改由强制 `M5`/`S0` 前缀与拒绝一切非零 S 共同保证零功率（逐行复核成立：前缀在 `reviewed_job_file.load` 强制，非零 S 在任何位置均拒绝，`M8` 仍不在允许集内）；301 项完整回归通过（归档 62.956 秒，另由只读复核会话独立复现 58.428 秒）；USB/BLE 两路模拟审计与 cncjs 几何 oracle 通过；Pi 包 73 文件核验且 `serial_owner_absent`。Pi USB 一次 18 条命令／13926 字节真实执行完成，终端与终点核验通过、returncode 0。该真实捕获当时**没有**独立审计产物，已由后续只读复核会话补跑并通过（`no_synthetic_ack`/`no_replay`/`usb_port_closed` 成立，两次运行输出逐字节一致）。macOS 原生 BLE 侧未开始：sessions 管理根无新会话、4002 无监听；现场无人在场、未取得现场观察（用户说明「再来一遍，刚才没在现场」），该次运行需择机重跑；本目录当时缺 `manifest.json`。LightBurn 于 15:01:53 启动后被中断，现已于 17:14:58 关闭并核验（无进程、无 4002 监听、无相关 python 进程）。**关闭后复核发现：持久化 `CutList` 已不再是本轮零功率设置（C00 由 maxPower 0／speed 1 变为 20／100），与归档快照差异 14 行且全部集中在 `CutList` 与窗口状态；E91 的 `prefs-comparison.json` 只比对设备 Settings 段、未覆盖 `CutList`，故当时不会发现。已导出的 `native-reviewed.gc` 未受影响（SHA-256 仍为 b189a348…，全部 S0）。重跑或进入真实界面步骤前必须重新读取确认图层零功率。**

E91 连通性复查（2026-10-13 17:21:57–17:22:26，非计分小项）：用户报告 LightBurn 连不上 `LaserLink NEJE5 Native GRBL`。只读诊断确认 **4002 无任何监听**，根因是桥接未运行（LightBurn 是 TCP 客户端），不是设备或配置故障；设备当时正常广播（RSSI −75）。随后以初始化档（无作业）启动一次管理会话并通过完整审计：2238 字节全部核验、`$I`×1／`$#`×1／`G0`×7／状态查询 26 次、`way=0`／`final_app=3` 关闭、`no_synthetic_ack`/`no_replay`/`motion_executed=false`。客户端断开后 2 秒内 62 次重连全部 `tcp_rejected`，与单客户端不自动重连的设计一致。会话目录 `sessions/20261013T092157Z-22cd5e09`，结论不变（总分 80/100）。已把排查顺序补入[管理入口说明](lightburn-managed-session-guide.md)。本轮未新增能力，整体保持 80/100，不因半轮次加分。见[方案与状态](native-start-plan.md)。

2026-10-13 E91 续（收尾，**部分完成**）：原生直接开始的目标**未达成**，但同文件两路对照完成。
- **Pi USB 重跑**（首次运行现场无人，故重跑）：新远程目录 `/tmp/neje-e91-rerun-TOWNqj`，73 项源文件摘要与包 manifest 逐一一致；17:34:56–17:35:39 执行一次；独立审计 `passed`，18 条命令／13911 字节全核验、`no_synthetic_ack`/`no_replay`/`usb_port_closed` 成立；现场观察「完整四边、正常返回、无异常、无出光」。旧捕获 `usb.jsonl` 原样保留。
- **macOS 原生 BLE**：新会话 `sessions/20261013T101307Z-98f8810b`，作业 18:18:31–18:19:12（约41秒，名义40秒），18:20:47 正常收尾；独立审计 `passed`，47015／47015 字节、6706 帧、通知尾残留 0、`way=0`／`final_app=3`；现场观察同上。**该次经 LightBurn「运行GCode」发送，不是 Start 路径。**
- **同源对照**：两路 18 条作业行**逐字节一致**；含裸 `M4`（本轮审阅器放宽项）与 `M9`，不含 `M8`，全量 S 为 0。
- **未完成项**：原生 GRBL **Start 按钮置灰不可点击**，原因未查明；同期桥接侧健康（`?` 196 次→196 次状态响应全部交付、投递抑制/会话错误/任务错误/连接抖动/重连被拒均为 0；握手 `G0`×9→`$I`→`$#`→`?` 轮询），故属 LightBurn 侧使能条件。此前所有实机作业均走「运行GCode」，Start 从未被使用过，本次为首次尝试而非回归。
- **缺陷 1（确定性，复现 2 次）**：会话走到墙钟上限时以 `pipeline_session_cancelled`＋`pipeline_stopped`（`CancelledError`）收尾，设备虽已 `cleanup_verified`（`way=0`/`app3`）且无作业无运动，`assess()` 仍因[审计器](../tools/check_pipeline_tcp_capture.py)第 13 行把 `pipeline_stopped` 列为禁止事件而 `AssertionError`，管理状态变 `requires_recovery`。两次实例：17:24:28–17:29:36 初始化档、17:50:13–18:05:22 作业档（0 次作业提交）；两次均经显式 `recover` 解除。属可用性缺陷，非安全问题。
- **缺陷 2**：LightBurn 自动重连与本项目单客户端不重连设计冲突（客户端断开后 0.3 秒内 62 次重连全部被拒，界面长期停留 Disconnected）。
- 本轮**未新增已验收能力，总分保持 80/100**；`tools/` 与 `tests/` 未改动。见[结果文档](native-start-result.md)。

2026-10-13 E91 最终（**完成**，**原生 Start 路径打通**）：真实 LightBurn 从受审工程 `native-square.lbrn2` **直接点击 Start**，会话 `sessions/20261013T105113Z-050fab6d`（18:51:13–18:53:45）。原生 Start 生成的 18 条命令与 `native-reviewed.gc` **逐字节一致**，且与 Pi USB 执行字节**完全一致**；独立审计 `passed`（16684/16684 字节、2133 帧、尾残留 0、`way=0`/`app3`、`no_synthetic_ack`/`no_replay`）；现场观察「完整四边、正常返回、无异常、无出光」。审计同时如实记录退出瞬间 `discarded_unsent_status_queries: 1` 与 `all_received_tcp_queries_forwarded: false`，不据此宣称全部查询已转发。
- **设备参数自动读取**同步打通：[tools/lightburn_init_bridge.py](../tools/lightburn_init_bridge.py) 与 [tools/reviewed_job.py](../tools/reviewed_job.py) 白名单新增 `$$` **只读**；LightBurn 目标设备 `AllowSettingsFetch` false→true（全局差异恰好 1 条）。握手变为 `G0`×10 → `$I` → **`$$`** → `$#` → `?`，控制器设置全文交付（`$30=1000`、`$32=1`、`$130/131=260/400` 等）。边界未放宽：`$$` 仅限作业体外、`$n=value` 写设置任何位置都被拒、基础只读桥白名单未动。验证：定向 19 项通过、**两份历史捕获审计结论逐字段不变**、**全量回归 303 项 OK**。
- **fail-closed 实证**：18:48:05 用户在另一份设计上按 Start（X115–237/Y128–225、**F6000**），与受审序列第 3 行分叉 → `Unexpected, repeated or out-of-order job command`；`pipeline_job_submitted` 0、三行作业命令写请求各 0 次、`queued_bytes` 0，设备 `way=0`/`app3`。未审阅内容零转发、无运动。
- **更正记录**：早期把 Start 置灰归因于「缺少激光器来源」，实际原因是**当时未载入工程设计**；该假设已证伪，「自动读取」改动仍有独立价值但不构成本轮通过的原因。
- 缺陷仍记录在案：墙钟到期必然 `requires_recovery`（复现 2 次）、LightBurn 自动重连与单客户端设计冲突（0.3 秒 62 次重连全被拒）。
- **控制项 18→19/20，总分 80→81/100，对外约 81%（仍处 80% 区间）。** 见[结果文档](native-start-result.md)。

2026-10-13 E92（缺陷修复，**不改变任何验收结论**）：修 E91 期间实测到的三个缺陷。
- **缺陷 1（墙钟到期必然要求恢复，复现 2 次）**：[tools/pipeline_tcp_bridge.py](../tools/pipeline_tcp_bridge.py) 到期时先写显式事件 `pipeline_wall_limit_reached`；[tools/lightburn_session.py](../tools/lightburn_session.py) 新增终态 `expired` —— 仅在「无故障事件 + `cleanup_known=False`/无 `failure_reason` + 无未完成作业」时非阻塞，且**不声明成功**（`audit.passed=False`、`independent_audit_run=False`）；未完成作业、故障、清理未核验、捕获截断四种情形**仍保持严格 `requires_recovery`**。**审计器第 13 行的严格性未放宽。**
- **缺陷 2（LightBurn 自动重连 vs 单客户端）**：拒绝日志新增 `new_session_required=True`；[管理入口说明](lightburn-managed-session-guide.md)已列出排查顺序。**有意不采用断线宽限期**：单客户端是一次性会话的安全属性，放宽会允许断开后出现第二个控制端。
- **缺陷 3（`all_received_tcp_queries_forwarded` 硬编码）**：更正说明——该值在所有可达情形下本就正确（调用方有丢弃守卫），问题是**脆弱而非错值**；改为从捕获推导，并新增 `discard_cause='post_completion_client_eof'` 说明丢弃发生在作业完成后的客户端 EOF，属有界收尾而非审计失败。
- **验证**：定向 7+17+10 项全过；新增 4 项测试；**历史捕获等价性**——`ble-start.jsonl` 只多 `discard_cause` 一项（`passed` 不变）、`ble-run.jsonl` 与 `usb-rerun.jsonl` **零差异**；**全量回归 307 项 OK**。无设备连接、无运动、无出光。总分保持 81/100。见[修复结果](managed-session-fixes-result.md)。

2026-10-13 E93（半行断连能力，**纯离线**）：新增第三断连相位 **`partial-line`**（[tools/job_disconnect_host.py](../tools/job_disconnect_host.py)）与**受审长行输入** `half-line-job.gc`（314 字节长行，配 [tools/reviewed_half_line.py](../tools/reviewed_half_line.py)）。离线可证：容量 173 → 3 次写、**第一次写正好 173 字节且结束在半行**；作业总长 393 字节 > NEJE 单帧上限 256，故半行是结构性的。触发规则：GATT 写载荷是受审作业**真前缀且不以 LF 结尾**。定向 11 项、**全量回归 308 项 OK**；无设备动作。见 `evidence/half-line-disconnect-20261013/`。

2026-10-13 E94（带功率审阅 profile + Phase 1 准备，**纯离线、未出光**）：用户逐项确认加工授权并选定 **Phase 1 改用低功率即显痕基材**（保持 `S≤50`）。新增 [tools/reviewed_job_file.py](../tools/reviewed_job_file.py) 的 **`xy-linear-power`** profile —— 这是整个项目**唯一允许非零 `S` 的入口**，规则：`power_ceiling`/`feed_ceiling` **必须显式声明、无默认值**（设备满量程 1000 不是保护性上限）；`0≤S≤power_ceiling`；`0<F≤feed_ceiling`；每条运动行必须显式带 `S`（不依赖模态继承）；非零 `S` 前必须已 `M4`；带功率 `G0` 位移被拒；前后缀 `M5/S0 … M5/S0/M2` 不变。**六类负向边界全部拒绝**（S 超封顶、运动行缺显式 S、F 超上限、缺 M4、带功率 G0、零功率 profile 收非零 S）；其中「缺 M4」是在边界测试中发现并补上的漏洞。Phase 1 受审文件 `phase1-single-segment.gc`（12 条、`G1 Y390S50F300` 为唯一出光段、`S0` 回程、起终点 X0/Y400、包络 X0/Y390–400）。定向 10 项、**全量回归 312 项 OK**。**未连接设备、未解除隔离、未出光**；总分保持 81/100，实际加工仍 0/15。见[加工方案](machining-authorization-plan.md)与 `evidence/machining-phase1-20261013/`。

2026-10-13 E95（**首次真实出光**：Phase 1 + Phase 2，均为 BLE、均含现场观察）：激光器经用户确认为 **NEJE A40640**（官方 wiki：455nm、模块功率标注 40W／店铺页写 12W，官方自身命名不一致；宣称 2000mm/min 不锈钢表面标记、500mm/min 深色雕刻需 100% 功率；切割能力仅对轻质木材）。据此**推翻原定「金属 2mm」Phase 1**：5% 在金属上几乎不会留下痕迹，经用户同意改为**低功率即显痕基材**，保持 `S≤50`。

- **Phase 1 单段（10mm，`S=50` 即 5%，`F300`）两次尝试**：①第一次出光确实发生（设备回 `[MSG: Laser temperature:27.2C]`，状态报 `FS:300,50`），但软件在首个 `Run` 状态处中止（`parse_status` 拒绝任何非零报告功率）；**设备已把整块 12 行缓冲在手里，自行跑完并以安全态收尾**（`M5/S0`、X0/Y400）—— 又一次实证「软件中止 ≠ 设备停止」；②修复后**受控跑通**：`controlled_job_endpoint_verified` + `controlled_job_completed`、独立审计 `passed`（12 条、11280 字节核验）。两次现场均为可见痕迹。
- **Phase 2 二维闭合轮廓**：闭合 10×10mm 正方形（X20–30/Y390–400，`S=50`、`F300`，20mm 空走 + 40mm 出光 + 20mm 零功率返回，共 16 条）。**一次受控跑通、会话 `completed`、审计 `passed`**（9874/9874 字节、1236 帧、尾残留 0、`way=0`/`app3`、`no_synthetic_ack`/`no_replay`、`requires_explicit_recovery=false`）。现场确认：**形状闭合、位置正确、痕迹与 Phase 1 相近、无异常**。
- **本轮修掉 5 处**：①[parse_status](../tools/motion_parity_probe.py) 把「报告功率必须为 0」改为**按声明封顶**判定（不声明封顶时行为不变）；②[controlled_reviewed_job](../tools/controlled_reviewed_job.py) 接入封顶；③[bridge](../tools/pipeline_tcp_bridge.py) 新增 `--power-ceiling`/`--feed-ceiling`/`--allow-powered-marking`，并**把出光声明与隔离声明拆开**（出光运行不得再签「已隔离」，须用 `--laser-output-authorized`）；④[审计器](../tools/check_controlled_capture.py) 存在**同一零功率拒绝**且 CLI 写死 profile 列表，一并修复；⑤[recover](../tools/lightburn_session.py) 原先硬要隔离声明，现接受出光授权声明。
- **我的错误已记录**：E94 的离线端到端模拟给出**假绿灯** —— mock 会话恒报 `FS:0,0`，永远碰不到非零主轴路径。现补上用真实 `FS:300,50` 报告的测试。**模拟通过不等于能力存在。**
- **全量回归 314 项 OK**；**总分保持 81/100，实际加工仍 0/15** —— 该项口径为「USB/BLE **对照**」，Phase 3 的 Pi USB 参照尚未执行。证据：`evidence/machining-phase1-20261013/`、`evidence/machining-phase2-20261013/`。

2026-10-13 E96（**Phase 3：Pi USB 同源对照**）：同一受审文件（闭合 10×10mm 正方形，摘要 `d791f0c4…`，16 条，声明封顶 `power_ceiling=50`/`feed_ceiling=2000`）在 **Pi USB** 上执行一次，用户换上**同材质新基材**以避免两次痕迹重叠。**结果**：USB 侧 18 秒受控完成、698 行真实捕获、零 error、`controlled_job_completed` + 终点核验（X0/Y400）+ 模态 `[GC:… M5 M9 T0 F300 S0]` + `usb_closed`；独立审计 `passed`（6514 字节、`usb_port_closed=true`、`no_synthetic_ack`/`no_replay`）；串口持有进程 0。**两路对照**：作业字节**逐字节一致**（16/16），执行侧观察到的 `(F,S)` 组合均为 `{(0,0),(300,0),(300,50)}`、Y 轨迹均为 390–400 —— 即**控制器在两条通路上收到并报告了相同的进给与功率、走相同轨迹**。**现场**：尺寸基本一致、位置符合预期、闭合正常、无异常；**USB 侧痕迹目视偏淡**，因执行侧数据已排除通路与指令原因，归因为对焦距离/摆放角度/材料差异（用户自己指出该混淆），**未做排除性复测**。为跑此轮另给 [USB 入口](../tools/controlled_usb_probe.py) 补齐 `--power-ceiling`/`--feed-ceiling`/`--allow-powered-marking` 与出光声明（与桥接同规则：出光运行不得再签「已隔离」）。回归 314 项 OK。**评分：矩阵该项 0→10/15，总分 81→91/100** —— 未给满分，因方案自身要求「需独立测量」而实际仅目视估计，且深浅差异未做对焦受控复测。证据：`evidence/machining-phase3-20261013/`。

2026-10-13 E101（**通用带功率 profile：日常使用版本的关键一步**，纯离线）：文档此前自述「尚不是通用加工版本」，技术根因是**没有任何 profile 能覆盖真实设计** —— `xy-linear-power` 允许功率但禁圆弧与 G0，`xy-arc-zero` 允许圆弧但禁功率。新增 **`xy-general-power`**（[reviewed_job_file.py](../tools/reviewed_job_file.py)）：允许 G0 快移 + G2/G3 圆弧 + 真实功率，同时保留全部声明式上限（`power_ceiling`/`feed_ceiling` 必须显式给出、床身包络 X0–260/Y0–400、圆弧弧顶也须在行程内、前后缀 `M5/S0 … M5/S0/M2` 不变、零功率 profile 行为不变）。同时把「终点」从暗示的 X0/Y400 改为**由受审文件声明**：新增 `ReviewedFile.end_position` 与 `ControlledReviewedJob.end_position`（**默认仍为 `(0,400,0,0)`，既有行为零变化**，由专项 12+8 项测试确认），并接入 [桥接](../tools/pipeline_tcp_bridge.py) 与 [USB 入口](../tools/controlled_usb_probe.py)。**验证**：真实风格设计（快移+圆弧+带功率切割、终点 `(20,300)`）通过审阅（14 条、1 圆弧、1 快移、包络 X0–70/Y300–400、`end=[20,300,0,0]`）；三类边界拒绝（功率超上限、进给超上限、圆弧越程）；其他 profile 仍拒绝圆弧；**全量回归 326 项 OK**。**未完成**：端到端模拟因既有 mock 位置固定为 `(0,400,0,0)` 而无法模拟「终点不在原点」的设计，故该路径**尚未端到端验证**，需位置可跟随的 mock 或一次真机跑；日常启动器（消掉「算摘要 + 选 profile + 记 8 个旗标」的手工负担）尚未实现。
