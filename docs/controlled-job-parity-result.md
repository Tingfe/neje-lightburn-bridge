# USB / 真实 LightBurn BLE 控制对照结果（E52）

2026-10-12，北京时间20:59–21:02。用户已明确授权并确认激光供电物理隔离、净空及独立断电条件。Pi USB一次、真实LightBurn经Mac BLE一次均已执行，不重放；两路原始捕获的独立协议审计通过。用户随后确认“两次均暂停后恢复并返回，无其他轴异常、无出光”。

本轮验证了固定14行零功率作业的暂停、恢复、完整收尾及完工后空闲Stop/复位。尚未验证运动中取消、运动中真实BLE断连、通用长作业及实际加工；最终USB等效目标保持进行中。

## 实测对照

两路使用同一已审阅文件，X=0→10→0mm，Y400、Z/A0，60mm/min、M4 S0。USB直接串口；BLE输入文件和控制字节均来自真实LightBurn 2.0.05界面，没有用独立探针代替界面操作。

| 核验 | Pi USB | LightBurn BLE |
| --- | --- | --- |
| `$N`真实读取 | `$N0=`、`$N1=`均为空 | 两块均为空 |
| 实际控制字节 | !、~、0x18各一次 | !、~、0x18各一次 |
| 暂停稳定状态 | Hold:0，X2.400，FS0,0 | Hold:0，X6.388，FS0,0 |
| Hold:0至恢复写入 | 2.018秒 | 18.164秒，见偏差说明 |
| 恢复后的新状态 | Run，随后Idle | Run，随后Idle |
| 完整收尾 | 14条真实终端；$G/G0/$G及最后Idle | 同样通过，内部应答未充作LightBurn额度 |
| 完工机器坐标 | X0/Y400/Z0/A0 | X0/Y400/Z0/A0 |
| 空闲复位 | 真实Grbl启动行、新Idle/X0 | 真实Grbl启动行、新Idle/X0 |
| 原始接收字节核验 | 8,574字节 | 16,216字节，NEJE帧/CRC/计数核验通过 |
| 清理 | 串口已关闭，队列/在途均0 | way=0、app=3，TCP关闭；队列/在途均0 |

BLE最终可靠计数write=5033、read=6066，清理未知=false。LightBurn已通过实际菜单退出并核验进程未运行；TCP4002无监听，Pi串口无人占用。reset后的控制状态仍按保守策略标记position_trusted=false，不能把本轮新Idle报告解释为允许自动恢复旧作业。

## 实验偏差及证据边界

USB按计划在前进途中暂停约2秒。LightBurn界面文件选择和控制核验耗时较长，暂停发生在回程X约6.388mm，Hold:0后至~为18.164秒，而非计划的前进途中约2秒。恢复后真实Run报告X6.325进一步支持回程判断。完整记录偏差，不据此宣称暂停时机和持续时间严格匹配；本轮没有补跑第三次作业。

两路控制序列、真实收尾、终点及空闲复位可作功能对照，不能作延迟性能对照。现场观察已补齐，但没有独立位移计量或带电加工成品。空闲Stop成功不代表运动中Stop、失联停止或所有复位分支均已通过。BLE断开是正常关闭结果，不能当作在途意外断链验收。

## 实现及借鉴

沿用已固定版本的gnea/grbl流控及cncjs实时控制实现研究，来源及提交散列见`evidence/control-state-machine/sources.json`和`evidence/stream-transport/sources.json`。没有伪造ok或重放；!/~不占普通应答FIFO，0x18等待真实启动行及新状态。

本轮把完整作业收尾接入同一流水账本：终点核验后，私有$G验证安全模态、裸G0恢复、再次$G验证G0，最后Idle/X0才能发出controlled_job_completed。私有ok及GC内容不进入LightBurn作业额度，真实错误及异步状态/温度仍透传。暂停和取消可以抢占私有请求；EOF/故障不继续恢复或补发返程。

新增USB候选默认离线、动作需明确参数，并与BLE共用控制和收尾核心。独立审计从原始TX/RX重建作业字节、FIFO归属、私有/公开响应和控制代次，不只相信实现自身的“passed”。审计自测拒绝伪造公开ok、改写运动行、改写验证文本及篡改复位字节。

## 证据与进度

证据目录：`evidence/control-completion/`。主要文件：

- `authorization.json`、`onsite-observation.json`：授权和现场反馈。
- `usb-control-01.jsonl`、`ble-control-01.jsonl`：未经修改的原始捕获。
- `usb-audit-01.json`、`ble-audit-01.json`、`comparison.json`：独立核验和时序对照。
- `lightburn-hold.png`、`lightburn-completed.png`、`lightburn-exit-state.json`：实际界面及退出记录。
- `tests-final.txt`：193项完整回归通过，28.751秒。
- `pi-readiness.json`、`stage-manifest.json`、`pi-staged-offline.json`：树莓派环境和候选散列核验。
- `source/`、`SHA256SUMS`：执行与审计源快照及归档散列。

整体工程完成度由约50%调整为**约55%**（权重得分56/100）。下一步优先完成运动中停止及在途断连的受限验证，再推进通用文件入口、较长作业和实际加工效果；新增动作必须有对应现场授权，不能沿用本轮已消耗的两次次数。
