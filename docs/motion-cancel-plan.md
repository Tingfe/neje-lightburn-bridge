> 最新状态：USB 运动停止已于 E72–E73 实机完成（真实复位、ALARM:3/启动行/在途失效/关闭独立审计，现场确认前进后停住、无异常、无出光），并已在 E78–E81 完成真实 LightBurn BLE 运动中 Stop 对照。下文「未获得本轮新增运动授权，未执行硬件动作」为执行前历史记录。见[USB 停止实测](usb-motion-stop-hardware-result.md)与[真实停止及恢复](lightburn-motion-stop-hardware-result.md)。

# 运动中停止验证 B1：USB 基线

2026-10-12，候选已实现；未获得本轮新增运动授权，未执行硬件动作。上一轮E52两次作业/复位次数均已用完。最终目标仍是LightBurn原生BLE达到USB操作效果，本步骤用于先查明该NEJE固件运动复位的真实语义，再开展BLE对照。

## 一次实验范围

只执行Pi USB一次。激光供电保持物理隔离、X正向完整10mm净空、无人触碰设备，现场人员能独立切断设备/电机供电。

仍使用同一个LightBurn固定14行文件，SHA256 `89c60986076dca8edd9031e9f9b6c0c09b9fd07affa2bf32cd9cbea26b062d17`，X0→10→0mm、Y400/Z/A0、60mm/min、M4 S0。先只读查询?、$$、$G、$#、$N，必须起点Idle/X0/Y400/Z/A0、M5/S0、两个启动块均为空。预检失败不发送作业/复位。

开始后在真实Run且X约2–8mm时，仅发一次0x18运动中复位；不先暂停、不恢复、不补发返程、不解锁、不回零、不写设置。整份14行可能已在设备缓冲，包含返程；停止未及时生效时这些已缓存动作仍可能继续。不能把本机清队列当作撤回已缓存命令。

预期有M2等真实在途命令。必须分清已收到的真实ok、复位启动行使其失效的在途序列以及未发送队列；复位不是那些命令的成功应答。观察实际Grbl启动行，随后只发送?读取新的Idle/Alarm。位置一律标记不可信，不根据复位后MPos自动安排任何运动。

如果运动复位返回Alarm、坐标改变或链路不确定，结束此路，不自动开始BLE实验，不解锁或自动返程。现场人员可能需要后续人工处理，恢复方案必须根据实际只读证据单独确定。没有启动行、状态丢失或复位写入不确定时也不重试第二次。操作者需独立断电时不等待软件结果。

正常完成最多包含这一次复位；异常后不额外增加复位。没有捕获到Run窗口时不能称为运动停止通过，也不追加另一份作业来补跑。

## 已准备入口

`tools/controlled_usb_probe.py --scenario motion_cancel`默认只输出离线审阅结果，不开串口。

获得本轮明确现场授权后才执行：

```sh
/tmp/neje-p0-20261012/venv/bin/python /tmp/neje-controls-e53b-20261012/tools/controlled_usb_probe.py \
  --execute --scenario motion_cancel --allow-motion-cancel \
  --allow-reviewed-ten-mm-job --allow-reviewed-job-controls \
  --laser-power-isolated --log /tmp/neje-controls-e53b-20261012/usb-motion-cancel-01.jsonl
```

只执行一次；命令观察超时后继续检查同一进程，不重新运行。关闭后保存原始捕获，用`tools/check_motion_cancel_capture.py <日志>`离线重建真实源字节、ACK归属、复位前Run、失效序列、复位后新状态及端口关闭。缺失证据只能记录失败/未知，不能伪造成功。

## 与后续BLE实验的关系

B1仅USB基线，未授权自动扩展到真实LightBurn运动中Stop、运动在途BLE断链、其他作业或带电加工。BLE阶段仍使用真实LightBurn界面输入；不能用独立TCP客户端代替实际界面验收。

BLE断连需要分别记录断开前已发送/确认的字节、尚待终端的命令、连接断开后本机封闭与队列清理、设备继续/停止行为及NEJE通道残留。故障会话不得自动重连、重放或恢复；另一次显式只读检查及拥有通道的清理应与故障会话分开记录。不能拿正常退出的BLE断开当作在途链路故障验收。
