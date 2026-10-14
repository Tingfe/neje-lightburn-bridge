# LaserLink 管理入口：当前实验版本

2026-10-13。入口是 `tools/lightburn_session.py`，使用Mac已有Python环境，复用原生BLE桥接及现有白名单。无需树莓派转发，不安装常驻服务，不修改LightBurn全局配置。当前支持初始化和已审阅零功率文件的正常完成，尚不是通用加工版本。

## 平时先做这两步

在项目目录打开终端：

```sh
cd /Users/tyongs/code/NEJE
/Users/tyongs/neje-ble312/bin/python tools/lightburn_session.py status
```

查看上次会话状态、原始日志目录、故障原因及是否需要显式恢复。首次显示 `no_managed_session`。此命令不连接设备，也不创建会话。

离线检查准备运行的文件，例如已验证的原生圆形：

```sh
/Users/tyongs/neje-ble312/bin/python tools/lightburn_session.py review \
  --job-file evidence/lightburn-curve-native-20261013/curve-lead-native.gc \
  --expected-job-sha256 8c6b7397a75825180d278b4c3bf86251a32e67173e21a9a2e3aacd4eac9a437d \
  --file-profile xy-linear-zero
```

会显示文件摘要、边界、功率、距离和时间预算。`review`始终离线；`start`不加`--execute`也只是预览，均不扫描或连接BLE。

## 当前授权范围内的启动方式

当前用户已持续允许有效行程内零功率移动及本上下文停止/故障验证，旧逐次额度与待移动许可状态不再适用，见[当前授权](operating-authorization.md)。执行开关、精确摘要和预检仍约束实际运行；在持续授权范围内不为每份文件重复询问。激光供电仍物理隔离，出光、设置写入和固件升级未包含在当前授权中。

仅初始化连接时：

```sh
/Users/tyongs/neje-ble312/bin/python tools/lightburn_session.py start \
  --execute --allow-initialization --laser-power-isolated
```

初始化包含`$I/$#/G0/?`，其中裸G0是模态设置，不能笼统称作完全只读。该模式拒绝运动作业和出光指令。

在已授权范围内，把精确review文件的参数用于start，并添加原有执行开关。例如二维文件还需`--execute --allow-initialization --laser-power-isolated --allow-reviewed-file --allow-reviewed-xy-file --allow-reviewed-job-controls`。缺少摘要、初始化、隔离、文件、二维等门槛均在建立会话前拒绝；圆弧/移动G0仍有各自独立标志和真实参数预检。这些开关表示本次执行范围，不能代替实际授权及预检。固定10mm控制实验仍走原实验工具，本管理入口的作业统一使用`--job-file`。

启动会打印新会话目录；有作业时包含该目录下的`job.gc`，这是与授权摘要匹配的副本。等桥接日志出现`pipeline_listening`后，打开 `LaserLink NEJE5 Native GRBL` LightBurn配置（本机127.0.0.1:4002、绝对坐标、自动回零关闭）。E90已验证该配置的恢复后矩形文件运行；等待`pipeline_tcp_ready`后，通过LightBurn“运行GCode”选择打印出的`job.gc`，不要另行重导出后启动另一份文件。源文件随后被修改，也不会替换已经锁定的作业副本。

本版本仅自动接受正常完成审计；暂停/恢复/停止的运行核心沿用原实现，但控制实验应按其专门方案和审计执行，不能把管理入口的一次正常作业范围扩展为控制授权。需要提前停止时按现场处置方案执行，未完成会话保留未知结论，不自动返程或重放。

## 连不上设备时先查什么（E91 后补记）

LightBurn 是 TCP 客户端，`LaserLink NEJE5 Native GRBL` 只表示「连到 `127.0.0.1:4002`」。**没有桥接在监听就一定连不上**；此时界面显示 Disconnected 不是设备故障也不是配置损坏。按顺序查：

1. `lsof -nP -iTCP:4002 -sTCP:LISTEN` 是否为空。为空则先启动本入口的会话，等日志出现 `pipeline_listening`，再在 LightBurn 里连接。
2. 本入口是**单客户端、不自动重连**：客户端一旦断开，桥接完成有界收尾后即退出，其后所有连接一律被拒（E91 复查实测：客户端断开后 2 秒内 62 次重连全部记录为 `tcp_rejected`）。要再连必须重新 `start`，而不是在界面里反复点连接。
3. 墙钟上限：初始化档 300 秒（`--seconds` 最大 300），作业档默认 `作业预算+60`、最大 3660 秒。到期时桥接写出显式事件 `pipeline_wall_limit_reached`（E92 修复，不再与故障混同）：若捕获显示**无故障事件、清理已核验（`way=0`/`app3`）且无未完成作业**，状态为 **`expired`** —— 非阻塞，可直接 `start`，但**不声明作业成功、不做完整独立审计**。若存在未完成作业、故障事件、清理未核验或捕获被截断，**仍按 `requires_recovery` 处理**，必须显式 `recover`。仍建议在时限内主动断开 LightBurn，以换取完整审计的 `completed`。见[修复结果](managed-session-fixes-result.md)。
4. 会话状态若为 `requires_recovery`，先 `status` 查看，再走 `recover`；不要删状态文件或换根目录绕过。
5. **连接正常但 Start 按钮置灰**（E91 实测）：桥接侧健康（状态查询全部交付、无错误无抖动）时，Start 仍可能不可点击，属 LightBurn 侧使能条件，**原因尚未查明**。当前可用的替代路径是「运行GCode」选中会话目录内的 `job.gc`。同一现象下界面设备状态可能在 Ready 与 Disconnected 之间跳变，但桥接日志显示连接并未断开；**以原始捕获审计判断，不以界面标签判断**。
6. 界面反复重连会全部失败，这是设计要求的结果，不是又有新故障；先 `status`，必要时 `recover`，再重新 `start`。

## 完成与故障

正常结束后关闭LightBurn连接，桥接完成原有有界收尾与可靠通道关闭。入口随后运行独立原始捕获审计，成功才写`completed`。桥接返回0、Ready或一个ok均不足以令文件作业成功。初始化完成的`job_completed`为false；没有TCP客户端的正常超时标为`unused`，不会冒充设备作业完成。

数据默认保存在项目`sessions/`：每次一个新目录，包含`session.json`、`bridge.jsonl`、`stdout.txt`、`stderr.txt`，文件会话另有`job.gc`；顶层`state.json`指向最近会话。同一根目录由进程文件锁互斥。已有日志不会覆盖。

连接后的故障、中断、审计不通过或遗留`running`记录会标记或按`requires_recovery`处理，并拒绝下次直接启动；不会因为进程已死、本机队列归零或端口释放而认定设备安全。不要删除状态文件或换一个根目录绕过此提示。仅扫描阶段失败且捕获证明未进入TCP/BLE会话时标为`failed_before_connection`，允许下一次人工启动；不会自动重试。

当前有显式恢复检查入口，见下文。它不会自动发送复位、解锁、回零或运动，也不保证任何故障都能解除阻止。此管理约束只覆盖使用该入口及同一会话根目录，旧实验脚本不受它统一管理。

## 状态阶段的含义（E68更新）

status新增`observed_stage`及`owner_lock_held`。等待LightBurn、初始化、ready、作业/清理等阶段来自完整捕获行；损坏或未完成尾行不用于推定成功。running且锁未持有时显示拥有者缺失/无法核实，需要显式恢复，不按PID存在自动认定存活。原故障不会因后续清理成功而消失。此显示只读，`status_is_completion_audit=false`，不能替代完整捕获审计。

## 当前验证范围（E85更新）

最近完整回归为300项通过。E71已用本入口完成两次真实LightBurn初始化、正常退出和一次同目录显式再次启动；E85又在同一sessions根目录完成4007条／145046字节、约200秒文件，真实LightBurn作业及独立捕获审计通过，最终completed/job_completed=true/实际锁释放。E86–E87完成真实初始化断连、阻止直接重启、显式恢复和另行真实初始化及关闭，见[恢复实测](managed-recovery-result.md)。此前见[初始化实测](managed-init-hardware-result.md)和[长文件实测](long-job-parity-result.md)。

E85现场确认连续运行并返回，无异常、无出光。该次是生成的零功率压力文件，不是原生导出或加工；管理入口控制/故障恢复和图形化完整流程仍待验收，不能据此认定通用加工或全部USB等效已完成。

## 中文状态提示（E84更新）

status的message现在说明当前证据含义：正在等待、预检完成、传输作业、等待收尾/独立审计、已通过完整审计或异常。结束事件及进程退出不单独显示成功；初始化成功明确说明没有执行作业。故障、拥有者缺失或日志损坏提示设备尚未确认停止、已缓冲动作可能继续、勿直接重发。提示只读，不会清除状态、解锁或恢复设备。

当前新增4项提示验证及完整291项回归通过（54.316秒）。真实断连证据见[作业断连结果](job-disconnect-result.md)。LightBurn自身的流传输完成文案不能代替本入口的审计结论；本项目没有修改该应用。

## 显式故障恢复（E87更新）

先退出LightBurn，查看status并保留原始日志。预览不连接设备：

```sh
/Users/tyongs/neje-ble312/bin/python tools/lightburn_session.py recover
```

在当前持续授权及激光供电隔离条件下执行：

```sh
/Users/tyongs/neje-ble312/bin/python tools/lightburn_session.py recover \
  --execute --laser-power-isolated
```

该操作取得原会话目录的真实进程锁，以记录的BLE地址和原始计数证明所属路由，关闭并核验；仅完整命令边界才进行6次固定只读查询。当前仅接受Idle/X0/Y400、M5/S0、零偏移及空启动块的已验证基准。半行、不可信位置或其他异常仍阻止start，不补发换行、不自动复位或回零。

成功显示recovered，说明通道及当前控制器核查通过，原失败会话和作业仍保留未知结论。随后需要另行start并重新预检；不会自动接回LightBurn或重放旧作业。失败时保留原状态及新的recovery-*目录，不能删状态、换根目录或直接重复作业绕过检查。

本轮真实恢复后的通信/关闭已审计通过，但LightBurn曾在收到持续真实Idle时仍显示Disconnected；故障时也曾显示Ready。当前以管理状态和原始捕获审计判断恢复，不以该应用标签认定成功。完整图形化体验仍待修复。

## 原生 GRBL 配置（E90更新）

上述标签异常发生在Custom GCode配置的E86–E87。E88–E90新增独立原生GRBL设备并实测Ready→真实断连Disconnected→显式恢复Ready→零功率矩形Busy→完成Ready，完整捕获及现场观察通过；可用路径和边界见[配置对照结果](native-grbl-ui-result.md)。原有设备配置逐项保持不变。

本机可直接选择 `LaserLink NEJE5 Native GRBL`。移植时通过LightBurn设备管理导入 `evidence/native-grbl-ui-20261013/neje5-native-grbl-persisted.lbdev`；导入后核对260×400、TCP127.0.0.1:4002、自动回零关闭及Fire/Frame关闭。导入只改客户端配置，不写控制器设置。

当前验证的是精确受审文件经Run GCode运行。新设计的原生GRBL导出/直接Start仍须以真实生成文件审阅，不能用旧Custom GCode零功率模板推定新驱动的功率、气辅或命令输出。连接标签本轮一致仍不能替代设备状态及完整审计。
