# USB / LightBurn BLE 控制对照：现场方案 A

状态：本轮已获授权并执行USB、真实LightBurn BLE各一次，授权次数已用完。结果见[实测报告](controlled-job-parity-result.md)；LightBurn暂停时机及持续时间有记录偏差，不补跑。最终目标保持LightBurn经Mac原生BLE达到USB使用效果。

## 本次范围

Pi USB一次、真实LightBurn经Mac BLE一次，均使用已审阅的同一14行文件`evidence/pipeline-lightburn/ten-mm-reviewed.gc`，SHA256为`89c60986076dca8edd9031e9f9b6c0c09b9fd07affa2bf32cd9cbea26b062d17`。

两次各X=0→10→0mm、Y=400、Z/A=0、60mm/min、M4 S0零功率。在前进到X约2–8mm时暂停，等待真实Hold:0，观察约2秒，再恢复并完整返回。正常步骤在真实终点/模态/裸G0恢复核验完成后，再各执行一次空闲停止复位，验证真实启动行、状态及关闭。每路最多一个复位，不重发。没有其他运动、出光、气辅、回零、解锁或设置写入。

本次新增读取`$N`启动块；必须实际读回两个空块`$N0=`、`$N1=`，否则不开放作业或复位。保留此前$$/$G/$#/?读取及裸G0初始化范围，全部成功响应来自设备。

现场需重新确认：激光供电仍物理隔离，X正向完整10mm净空，无人接触设备，能独立切断电机/设备供电。每路启动前提示观察。暂停是控制器保留缓冲后的减速停止，清空本机队列不能撤回已发送动作。

## 可执行入口与次序

1. Pi上仅运行`tools/controlled_usb_probe.py`的默认离线检查，核对归档源散列和文件散列；不打开串口。
2. 得到现场授权后执行USB候选：`--execute --allow-reviewed-ten-mm-job --allow-reviewed-job-controls --laser-power-isolated --log <新日志>`。固定程序完成预检→启动块检查→一次作业→Run/X2–8触发!→Hold:0观察2秒→~→完整收尾→Idle下0x18→真实启动行和Idle/X0状态→关闭串口。没有自动返程分支。
3. USB结果独立核验并关闭后，才启动Mac桥接候选`tools/pipeline_tcp_bridge.py --execute --allow-initialization --allow-reviewed-ten-mm-job --allow-reviewed-job-controls --laser-power-isolated --seconds 180 --log <新日志>`；默认TCP127.0.0.1:4002，单连接、单作业。
4. 真实LightBurn使用已配置的独立260×400mm设备与同一审阅文件，启动一次作业。确认Run和X约2–8mm后通过真实界面按Pause；必须捕获真实!且设备返回Hold:0。等待约2秒，通过真实界面Resume，必须捕获真实~和新的Run/Idle。界面操作没有发出对应字节时停止验收，不能用本机注入代替LightBurn操作并声称通过。
5. 等待`controlled_job_completed`及真实终点/模态/恢复核验后，再按真实LightBurn Stop。捕获0x18、真实启动行和新的Idle/Alarm报告；不会自动重新连入或续作业。关闭LightBurn并核验TCP/任务与BLE通道释放。

USB脚本和BLE桥接使用同一个控制/收尾实现；USB是协议基线，BLE必须由真实LightBurn输入文件及控制字节，不能用独立BLE探针替代。

## 异常与边界

- 异常不重发、没有自动补发返程或解锁。用户在异常时可触发停止；最多尝试一次0x18，保留真实Alarm/不确定坐标。需要独立断电时立即现场处理，不能等软件核验。
- NEJE复位若改变可靠通道或计数，严格计数检查会失败，结果记录为未知并关闭本机会话；不自动重设计数、不重新发送旧作业、不自动开启新通道。
- 若空闲复位返回Alarm、坐标变化或清理未知，停止后续动作，进行只读核查。只有原方案正常终点及可信空闲条件成立才开始第二路；不为了完成对照自动恢复设备。
- 运动中的主动取消及运动作业中真实BLE断链仍需后续专门验收。本次正常步骤验证暂停/恢复和空闲Stop，不据此宣称所有停止/故障场景已通过。

## 验收与证据

两路都需要原始TX/RX、实时控制时序、每条作业真实终端、暂停时坐标和Hold:0、恢复后的状态、14行完整性、收尾$G/G0/$G及最后Idle/X0、复位启动行和后续状态、关闭结果。LightBurn截图和现场观察单独保存，不能替代协议证据。

内部$G及裸G0使用同一FIFO账本，内部ok不会交给LightBurn作为作业额度。真实错误及异步状态/温度仍可见。完工必须有`controlled_job_completed`，单独的`controlled_job_endpoint_verified`不算完整收尾。

激光仍隔离，故本次不包含实际加工质量验收；整体目标进度按本轮实际证据另行更新。
