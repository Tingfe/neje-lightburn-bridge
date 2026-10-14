# USB / Mac 原生 BLE：1 mm 无激光运动对照（2026-10-12）

用户现场明确确认激光模组供电物理隔离、X 正向 1 mm 无障碍、无人接触且能独立断电，授权 USB、Mac BLE 各一次 X 机器坐标 0→1→0 mm、60 mm/min。软件没有测量物理隔离或实际位移。

## 结果及异常处理

18:36:52，USB 实际发送一次 `G53 G1 X1.000 F60.000`。设备返回 `ok` 后紧跟 `[MSG: Laser temperature:27.1C]`。脚本错误地要求最后一行为 ok，主动停止并关闭串口；没有发返程，也没有重复前进。失败原始证据保留。

独立只读复查两次均为 Idle、MPos=1/400/0/0、FS=0/0；$G 为 G1/G21/G90/G94/M5/S0/F60。用户再次明确确认现场条件，授权仅完成剩余返程以及 BLE 对照。修复响应终结行判断并通过回归后，18:37:51 仅发送 USB `G53 G1 X0.000 F60.000`，报告 Idle、MPos=0/400/0/0；裸 G0 恢复快速模态。USB 两份运动日志合计恰好前进一次、返程一次。

18:38:20–25，Mac CoreBluetooth 原生 BLE 独立执行同一对照，未通过 Pi 中转。每条运动前重新读取设置、偏移、模态、坐标；前进、返程分别收到真实 ok 后轮询到 Idle 与目标 MPos。最终 G0/M5/S0/F60，MPos=0/400/0/0。BLE 正常关闭读回 way=0、app=3，w/r=435/21319，设备收发错误均0。

**控制器报告的 1 mm 往返及传输对照通过。** 这是独立探针执行，尚不是 LightBurn 控制台或作业执行；没有独立测量实际位移，没有实际加工。进给模态保留 F60，没有擅自恢复为未授权的 F0。未发送出光、回零、解锁、复位或设置写入命令。

## 证据

全部位于 `evidence/lightburn-init-profile/`：

- `motion-usb-01.jsonl`：前进和错误停止原始记录。
- `motion-usb-post-stop-readonly.jsonl`：停止后的真实只读状态。
- `motion-usb-return-01.jsonl`：单独授权的剩余返程。
- `motion-mac-ble-01.jsonl`：完整 Mac BLE 原始 GATT/帧/响应/计数/清理。
- `motion-audit.json`：离线重新解码、逐帧数据 CRC、连续计数、精确命令和发送次数、终点及清理审计通过。
- `tests.txt`：本轮回归结果。温度通知不会再被当作拒绝；缺失终结行、重复终结行、真实 error/ALARM 仍停止，不伪造成功。

随后取得新增现场授权，完成[真实 LightBurn 控制台分会话对照](lightburn-console-motion-result.md)，包含通知计数缺陷修复及明确授权返程。完整目标仍按 [USB 等价验收矩阵](usb-parity-goal.md)推进。
