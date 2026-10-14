# 固定零功率作业 USB / LightBurn BLE 对照结果

2026-10-12，用户确认激光供电物理隔离及现场净空后，执行 USB、真实 LightBurn BLE 各一次固定作业（第一对）；用户表示未看到，明确要求重复后，又各执行一次（第二对）。没有执行其他运动、实际出光或通用作业。

作业是实际 LightBurn 导出的 `lightburn-zero-power-reviewed-v2.gc`，SHA256 `b2ef9334e8914688f5359c2b4e38faed071a602aafaceac531e04f1dd5d4a315`。14条有效命令；X机器坐标0→1→0 mm，Y=400不变，60 mm/min，M4 S0，无气辅/回零/设置写入。每条作业等待真实终端响应，M2后另查真实Idle/终点/模态，最后内部裸G0恢复。没有伪造成功或自动重放。

| 证据 | 第一对 | 第二对（用户要求重复） |
|---|---|---|
| Pi USB作业完成（北京时间） | 19:13:28左右 | 19:20:57 |
| 真实LightBurn BLE作业完成 | 19:14:39 | 19:22:25 |
| 固定作业命令 | USB/BLE均14条，各一次 | USB/BLE均14条，各一次 |
| 控制器终点/模态 | Idle，X0/Y400/Z0/A0，M5/S0 | 相同 |
| USB关闭 | 串口已关闭 | 串口已关闭 |
| BLE清理 | way=0/app3，w1032/r38826 | way=0/app3，w1362/r45526 |
| 完整会话严格审计 | 通过 | 未通过：退出时取消了作业后的在途?查询 |
| LightBurn界面 | 流传输完成0:05 | 流传输完成0:04 |

第一对的严格审计逐一核验原始通知重组、CRC、连续计数、实际GATT写入、真实响应与TCP转发、14条作业顺序、无伪造ACK、无重放及清理，见 `evidence/reviewed-job-bridge/job-parity-audit.json`。LightBurn的状态轮询实际发送`?\n`，空行会产生真实额外ok；不能把总ACK数当作作业命令数。

第二对在作业完成18秒后退出LightBurn，EOF取消了一条正在发送的`?`，记录`stream_write_uncertain: CancelledError()`，未发送队列剩余一个空行。14条作业早已取得真实应答，完成核验已记录；取消后不重发查询/作业，通道关闭并读回way=0/app3、错误计数0。此事件不能表述为全部查询字节已核验。保留原严格审计标准，单独的 `audit-repeat.py` / `job-repeat-audit-02.json`仅验证第二对作业完成、原始帧重组、取消范围、无重放和清理，不冒充完整会话审计通过。下一步应改进正常EOF对在途只读查询的有界收尾，并模拟验证；本次不追加运动。

现场观察：用户原话“1mm的行程，根本看不到，似乎听到有响应，无出光”。记录为肉眼位移无法确认、疑似声音响应、无出光；不是可见往返通过，也不是独立位移测量。控制器坐标报告不替代机械位移测量。

代码验证：82项离线回归通过；随后审计工具仅增加选择01/02捕获文件的参数，3项捕获审计测试再次通过。真实截图为 `lightburn-ble-job-completed.png` 和 `lightburn-ble-job-02-completed.png`，日志/输出/审计/源码快照均在同一证据目录。

开源借鉴沿用已归档 gnea/grbl simple_stream/stream 与 cncjs GrblController：真实终端应答、异步MSG不充当ACK、普通行与实时查询区别处理。来源commit和许可证见 `evidence/stream-transport/sources.json`。本实验逐条等待，只证明固定小作业通信完成；通用作业吞吐、暂停/恢复/取消、真实作业在途BLE断连、实际加工和USB等效最终目标仍未完成。

最新E41：[正常EOF在途只读查询收尾修复与真实复验](eof-query-drain-result.md)。旧E40取消记录不被修改或追认为通过。
