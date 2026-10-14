# LightBurn 初始化与真实 BLE 断连验证（2026-10-12）

本轮继续保持设备 GRBL 白名单为 `$I\n` 和实时 `?`。**初始化红色错误尚未消除；真实主机侧 BLE 断连及桥接拒绝后续连接已验证。断连后独立重新连接查询时，设备路由仍为 way=2，必须明确关闭并读回，不能把 TCP 退出当作设备已清理。**

后续已完成两次在途查询断链及初始化开源方案补查，见[最新报告](lightburn-pending.md)；下文“在途查询未验收”属于本轮历史状态。

## 初始化调查结果

本机 LightBurn Pro 2.0.05 已有 GRBL 测试配置，本轮另建 `LaserLink Custom ReadOnly`，Custom GCode / GRBL 风格 / TCP 127.0.0.1:4002。仅为查询测试，尺寸10×10mm不是实际行程；不是默认设备，也不是加工配置。

检查[官方 Custom GCode 文档](https://docs.lightburnsoftware.com/2.0/Reference/DeviceSettings/CustomGCode/)与实际界面：可编辑复位、运动、工具、作业和获取位置模板，没有发现独立“连接测试命令”模板。空模板表示采用默认命令，不能视为禁用。`AllowSettingsFetch=false` 只控制连接时获取配置，并未阻止本机发出 G0 / $#。[普通 GCode 设置文档](https://docs.lightburnsoftware.com/2.0/Reference/DeviceSettings/GCode/)中的 Start/End GCode 属于作业流程，不是连接初始化开关。

[2021年官方工作人员的回答](https://forum.lightburnsoftware.com/t/how-to-change-the-startup-commands/39167)称启动命令内置；[另一条官方工作人员说明](https://forum.lightburnsoftware.com/t/my-machine-cant-connect-to-lightburn-why-is-that/42673)将 G0 用作连接测试。这些旧答复仅作解释线索，当前版本行为以本轮日志为准，不能据此断言所有版本都不可配置。

| 实验 | 北京时间 | 实测 |
|---|---|---|
| E18 / custom-host-01 | 16:49:43–16:50:19 | G0、$#、$H 各拦截一次；1次$I、49次?真实成功；结束way=0/app=3，计数118/6664 |
| E20 / custom-host-02 | 16:52–16:53:16 | 关闭自动回零后，无$H；G0、$#仍各拦截一次；1次$I、12次?真实成功；结束way=0/app=3，计数136/7603 |

E18 暴露配置保存差异：向导中曾观察到自动回零未选中，但最终落盘 `HomeOnStartup=true`，实际出现 `$H`。它被 TCP 白名单拦截，**未进入 BLE**。随后在基础设置中明确关闭“启动时自动复位”和“返回结束位置”，退出应用后读回 `HomeOnStartup=false`，并用 E20 验证 `$H` 不再出现。保留修改前、后的快照，不把向导观察当作成功保存。

E20 控制台仍显示本地 `error:20`；这是禁止输入的桥接策略拒绝，不是设备故障。实际$I响应含NEJE v2.3.2、OPT和ok，正常转发。没有返回伪造ok、隐藏错误、把G0改写成查询，或放宽白名单。Custom GCode并未解决当前初始化兼容问题。

可继续推进的方向是寻找或请求 LightBurn 提供独立初始化命令配置，使客户端只发送允许的查询。当前模板和已核查的设置不足以实现；不能以其他控制器风格冒充NEJE的GRBL协议，也不能用修改运动模板的方式隐式改变连接语义。本轮没有找到可直接接入、同时解决 NEJE 可靠协议与 LightBurn 内置初始化的开源实现；这是所查资料的范围结论，不是全网不存在的证明。

## 开源借鉴与改动

直接使用 MIT 许可 Bleak 3.0.2。检查其 CoreBluetooth `disconnect()` 路径：调用 `cancelPeripheralConnection_` 并等待断连回调，清除服务缓存和未完成Future。检查 MIT 的 Jakeler/ble-serial 3.0.0：传入 `disconnected_callback`，在断连处理器中记录事件并终止本地循环。其通用透传不知道 NEJE 的 way 状态，不能代替协议级关闭读回。

本项目仅给现有桥接增加公共 `disconnected_callback` 事件记录；保留原来的断连检测、清理未确认标记、拒绝新客户端和无自动重连策略。正常退出BLE也会触发该回调，不能仅凭回调判定发生异常。

对应版本源码、许可证和散列在 `evidence/init-disconnect/`。官方API：[BleakClient](https://bleak.readthedocs.io/en/latest/api/client.html)。部分官网直接HTTP归档返回403，已在sources.json如实保留，另保存web工具返回的文档摘录，不把摘录称为完整网页归档。

## E19：真实 BLE 链路断开

脚本 `tools/check_ble_disconnect_host.py` 默认离线；显式执行需要隔离确认和新日志路径。本次沿用用户确认的激光模组供电物理隔离，软件未独立测量无出光。

测试在真实TCP客户端仍打开、真实$I响应和设备计数已核对后，调用实际 `BleakClient.disconnect()`。测试替换构造器引用为真实BleakClient的子类，仅保存实例供故障注入，**没有重写BLE方法、模拟通知或修改is_connected**。本次是主机主动断开真实CoreBluetooth链路，不是仅断开TCP，也不是无线衰落、设备断电或远端失联测试。

| 北京时间 | 观察 |
|---|---|
| 16:51:40.114405 | 请求真实BLE disconnect，TCP仍打开，is_connected=true |
| 16:51:40.114967 | 实际断连回调，is_connected=false |
| 16:51:40.215085 | 桥接记录cleanup_unconfirmed，未尝试在断链后发送关闭命令 |
| 16:51:40.215739 | TCP关闭，距回调0.100772秒；清理未确认 |
| 16:51:40.216479 | 第二TCP客户端被拒绝；桥接只建立过1次BLE连接，没有自动重连 |
| 16:51:40.844852 | 停止测试服务器后，独立显式连接、验证model55/app3，再GetStatus：way=2，计数121/6726，收发错误均0 |
| 16:51:40.845031 | 确认计数与本次最后查询完全一致后，关闭本实验拥有的通道；没有重发$I |
| 16:51:41.151046 | 读回way=0/app3，计数121/6726，完成清理 |

断连后的重新连接只执行身份/系统/通道状态查询和本实验通道关闭；没有启用新通道、应用切换、GRBL配置或加工命令。这个独立检查属于实验步骤，不是桥接自动恢复。正式桥接仍拒绝接管初始way非0的设备。

**证据边界：**能确认重新连接查询时way仍为2，不能直接观测无链路期间是否存在短暂状态变化，也不能排除固件在重连时恢复状态。因此不宣称“断连期间始终为2”，但足以否定“看到BLE/TCP断开就可以确认way=0”的操作假设。最终关闭是显式SetStatus(0)后的设备读回。

## 验证与证据索引

- `logs/lightburn-custom-host-01.jsonl`、`02.jsonl`：LightBurn真实TCP/BLE原始记录。
- `logs/ble-disconnect-host-01.jsonl`：真实断连、拒绝后续客户端、独立状态检查及清理记录。
- `evidence/init-disconnect/ble-capture-audit.json`：`tools/check_ble_disconnect_capture.py`离线审计；核对原始notify重解码、精确白名单编码、连续接收计数、断连后零桥接写入、唯一查询、错误计数和明确关闭。
- `evidence/init-disconnect/tcp-capture-audit.json`：`tools/check_tcp_capture.py`核对前轮及本轮共5份TCP捕获，每会话关闭读回通过；保留尾部不完整诊断帧信息。
- `logs/tests-init-disconnect-final.txt`：25项离线测试通过，增加正常退出时断连回调记录断言；模拟测试与本轮真实BLE实验分别记录。
- `evidence/tcp-lightburn/custom-gcode-options.png`、`custom-gcode-initialization.png`及`evidence/init-disconnect/custom-gcode-corrected-initialization.png`：界面证据。
- `evidence/init-disconnect/lightburn-prefs-after-custom.ini`：E18后原始快照，HomeOnStartup=true。
- `lightburn-prefs-final.ini`、`lightburn-custom-final.json`、`preferences-comparison.json`：修正后落盘，原有No Machine/GRBL逐项保持一致，默认设备保持原值。
- `sources.json`、`web-tool-extract.json`、`manifest.json`：来源、抓取限制和文件散列。

本轮结束时LightBurn已退出，4002无监听，本次实验进程无残留；最后设备读回way=0/app3。进程/监听只读检查的空输出与检查说明保存在证据目录。初始化无错误仍未通过；无线衰落、远端断电、在途查询断链、重传、长时间稳定、加工与物理安全验收仍未完成。
