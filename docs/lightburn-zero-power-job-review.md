> 最新状态：两对固定零功率USB/真实LightBurn BLE作业已授权并完成控制器终点核验；现场肉眼位移未确认。第二对退出时有在途状态查询取消，清理读回成功。详见[实测报告](lightburn-reviewed-job-result.md)。下文准备/待授权表述为执行前历史记录。

# LightBurn 零功率固定作业审阅（2026-10-12）

本轮完成原生导出与离线命令策略，未执行硬件作业。最终目标仍是 LightBurn 原生 BLE 操作与 USB 效果一致；此次不构成完整作业、加工或控制功能验收。

## 原生配置与导出

保留原 10×10 mm 测试配置，另建 `LaserLink NEJE5 Offline Review`，260×400 mm、前左原点、自动回零关闭、TCP 127.0.0.1:4002。实际设备设置界面的自定义 GCode 模板设为 start_user/end_user=M5 与 S0、tool_on=M4 S0，气辅开关替换为注释。最终原生导出包 `evidence/stream-transport/neje5-review-final.lbzip` 及其解出的 `.lbdev` 已保存。

先前自行准备的 Settings.StartGCode/EndGCode 和项目 airAssist 字段没有被 LightBurn 的该配置格式采用。实测应使用 Settings.GCodeBlocks 下的 start_user/end_user/tool_on/air_on/air_off。最终模板由真实界面保存并重新导出核对，不依据自行编写的字段宣称生效。

原生 `lightburn-zero-power-reviewed-v2.gc` 的 SHA256 为 `b2ef9334e8914688f5359c2b4e38faed071a602aafaceac531e04f1dd5d4a315`，有效指令按序为：

```gcode
M5
S0
G00 G17 G40 G21
G54
G90
M4 S0
G0 X0Y400
G1 X1S0F60
G1 X0
M5
G90
M5
S0
M2
```

预期起点与终点均为 X0/Y400/Z0/A0；X 在 0–1 mm 之间、60 mm/min，其他轴不变。该结论依赖执行前确认 G54/G92 偏移为零、G21/G90/G94、Idle、M5/S0、位置一致及设备设置符合基线。没有 M8/M9、回零、复位、解锁或设置写入。M4 S0 仍属新增模式命令，物理激光隔离及现场运动授权是执行前提；本文件不授予授权。

第一个候选 `.gc` 尚带 M8/M9，作为失败审阅版本保留，不应用于实验。初始默认 20% 功率文件同样保留，未执行。

## 一次性白名单

新增 `tools/reviewed_job.py`，仅离线运行、不打开 BLE/USB/TCP。固定导出散列、固定命令顺序，拒绝重复作业、乱序、额外运动、非零功率、气辅、复位/暂停/恢复实时字符、非 ASCII 输入及过长行，故障锁定后拒绝后续数据。仅允许 ? 插入查询；初始化固定命令仅在作业前后允许。TCP 分片重组后保存原始 ASCII 行、CRLF 和注释，接受不代表已发送或已完成。

导出的中文注释仅用于离线审阅，不能直接作为实机字节透传：高位字节可能与 GRBL 扩展实时命令冲突。未来桥接应核对真实 LightBurn 发送的 ASCII 指令，不能因为导出含注释而关闭输入检查。不得伪造 ok 或向应用报告尚未完成的成功。

73 项回归通过，新增测试覆盖全部 TCP 双片切分位置、查询插入半行、错误输入锁定、重复作业、未完成 EOF 和文件变更。证据：`evidence/stream-transport/tests-reviewed-job.txt`、`reviewed-job-audit.json`。这些均为离线测试，没有执行新增运动。

## 尚待完成

策略尚未接入实机桥接，因此现有 `stream_tcp_bridge.py` 仍仅允许原四条初始化请求，不允许该作业。下一步实现启动前设置/模态/偏移/Idle 检查、真实应答计数与完成后 Idle/终点核验，验证异常后不续发和不重放，再准备同文件 USB 与真实 LightBurn BLE 对照的现场授权范围。暂停/恢复/取消、长作业吞吐及实际加工仍需单独证据。

本轮 LightBurn 已退出。设备包保存对话框曾将绝对路径当作文件名，生成 Documents 下含冒号的包文件；其内容已复制到上述项目证据路径，不影响设备命令或原配置。
