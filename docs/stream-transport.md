# 持续 BLE 数据接收与 LightBurn 初始化复验（2026-10-12）

## 已完成的现场授权

本次用户确认仅允许剩余 LightBurn 返程。该返程已在 18:43:38 完成：实际应用发送 X=1→0 mm 的固定指令，收到真实 ok 和分帧温度通知，控制器报告 Idle、X=0/Y=400/Z=A=0；随后裸 G0 成功，EOF 清理读回 way=0/app3。没有重发前进指令。详见 [控制台运动报告](lightburn-console-motion-result.md)及 `evidence/lightburn-init-profile/console-motion-audit.json`。

这些是控制器报告及通信证据，未独立测量机械位移。原先异常和单独授权的返程为两个会话，不能称为一次连续无异常往返。剩余返程授权已执行完毕；以下新增实机验证仅查询与已授权初始化，没有新增运动或加工。

## 传输实现

`tools/neje_byte_stream.py` 持续保存异步 GRBL 数据，不再以单条查询的终结行为边界丢弃后续温度通知。接收序号、CRC 和设备累计计数共同核验，只有得到核验的连续前缀才交付 TCP。错误、序号缺口和不确定写入停止会话，不自动重发、重连或续跑。

GRBL 数据帧强制核验真实 CRC，不能用尾字节 0x55 绕过。停止后专用清理允许重新建立控制帧解码状态，目的是关闭本进程拥有的通道，不恢复业务写入。关闭通道不等同于停止已经进入控制器缓冲的运动。

`tools/stream_tcp_bridge.py` 目前仍仅允许 `$I`、`?`、`$#` 和单独裸 G0；它不是通用加工入口。单客户端、有界队列、TCP 写入超时及 EOF 丢弃未发送队列已实现。实际 Bleak 特征报告单次无响应写入上限 182 字节，扣除 9 字节可靠帧开销后载荷最多 173 字节；协议理论上限不能直接当成本机可发送尺寸。

## 实测与离线验证

证据目录：`evidence/stream-transport/`。

| 验证 | 结果 | 主要证据 |
| --- | --- | --- |
| Mac 原生 BLE 5 次 $I / 6 次 ? | 725 接收字节全部核验，way=0/app3 | mac-readonly-01.jsonl、mac-readonly-audit.json |
| 真实 LightBurn 持续层初始化 | 1 次 $I、1 次 $#、7 次裸 G0、38 次 ?（含一次内部基线）；无本地白名单拒绝 | lightburn-continuous-init-01.jsonl、lightburn-continuous-audit.json、lightburn-continuous-initialized.png |
| 初始化数据一致性 | 2913 字节全部核验；扣除内部基线 62 字节后，2851 字节真实交付 TCP；EOF 后 way=0/app3 | 同上；35 字节未完成通知尾保留，未用于生成响应 |
| 离线回归 | 68 项通过；包含 CRC、温度分帧、异步消息、序号缺口、不确定写入、EOF 等 | tests.txt、tests/test_byte_stream.py、tests/test_stream_bridge.py |
| 长流与计数回绕 | 模拟 6000 行分包，发送和返回字节完整、计数回绕核对通过 | tests/test_byte_stream.py；纯模拟，没有向实机发送这些字符串 |

持续层尚未做新的实机在途断链实验；以前查询层的断链证据不能代替新实现的验证。长流测试也不能证明真实吞吐、加工或 USB 等价。

## 开源借鉴与离线作业发现

参考已固定提交的 GRBL 官方流式脚本和 cncjs 控制器，来源、散列及许可证保存于 sources.json 和对应源码快照。借鉴其区分普通 ok/error 与异步推送、控制发送窗口的处理方式；目前未复制其控制器代码。LightBurn 管理 GRBL 发送窗口，桥接管理 NEJE 字节计数，两者不能互相替代。也不能把标准 GRBL 的缓冲尺寸直接套用到本设备。

真实 LightBurn 离线导出 `lightburn-offline-original.gc` 显示默认图层为 20% 功率、6000 mm/min，并定位到 X4.5/Y5，超出现有运动授权，未执行。已保存零功率、60 mm/min、Y=400 的候选 `lightburn-zero-power-review.lbrn2`；当前 10×10 mm 测试设备配置使 LightBurn 拒绝导出该工作区外图形，候选 GCode 尚未生成，不能作为已审核作业。

后续先独立准备符合真实 260×400 mm 行程的作业审阅配置，完成原生导出、逐行白名单和异常停止审计，再提交具体现场实验范围。当前没有额外运动授权，完整作业、暂停/恢复/取消、作业级断链及实际加工仍未验收。

后续更新：独立审阅配置和第二版零功率原生导出已完成，固定作业离线策略及 73 项回归通过；策略尚未接入硬件桥接，未新增运动。最新状态以 [零功率作业审阅](lightburn-zero-power-job-review.md)为准，上述导出被工作区拒绝为早期实验记录。
