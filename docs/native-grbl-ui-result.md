# 原生 GRBL 配置的连接、故障与恢复后作业验证

2026-10-13，E88–E90。新增独立 `LaserLink NEJE5 Native GRBL` 配置，经真实LightBurn UI导入和落盘核对，原有五份设备配置逐项不变。使用原生GRBL驱动后，本轮实际标签与通信过程一致：初始化Ready，真实BLE断连后Disconnected，显式恢复后Ready，矩形作业Busy，完成后Ready。原始通信与清理审计通过，现场确认“四边运行并正常返回，无异常、无出光”。

这给出了当前可用的配置路径；没有证明Custom GCode标签问题的内部代码原因，也不能推定所有断连和界面工作流都已修复。没有更改桥接运行代码、伪造启动行/ok/Idle/Alarm、修改固件或写设备设置。

## 配置差异与来源

此前 `LaserLink NEJE5 Offline Review` 的Name/ProfilePath实际是Custom GCode，GCodeFlavor为grbl。此次由现有原生 `LaserLink ReadOnly TEST` 生成新的独立设备文件，LightBurn导入后实际列表显示GRBL，落盘Name/ProfilePath也均为GRBL；不是只改显示名称。新配置260×400mm、TCP127.0.0.1:4002、HomeOnStartup=false、Fire/Frame关闭。

LightBurn官方将[Custom GCode](https://docs.lightburnsoftware.com/legacy/CustomGCode)说明为可自定义输出模板和基础格式的单独设备类型；[GRBL网络连接说明](https://docs.lightburnsoftware.com/2.0/Guides/GRBLNetworkConnection/)将Ready/Disconnected用于连接状态。上述资料支持做驱动配置对照，不直接证明此处标签异常的原因。

开源参考仍采用已固定MIT许可cncjs `GrblController.js`：传输关闭、真实状态/启动应答及ready状态分开处理；它在未收到启动行的UART场景也使用真实状态查询判断ready。仅研究参照，本轮没有复制其代码或伪造启动行。固定版本、许可证及散列保存在references目录。官方页面通过web工具读取成功，直接导出HTML遇到403，归档的是来源及阅读摘要，不声称已保存完整HTML。

## 三个实际会话

| 阶段 | 实际结果 | 标签与证据 |
| --- | --- | --- |
| E88 正常初始化 | 真实G0/$I/$#、46次公开状态（另有1次内部基线）及47个空行；3661接收字节全部核验、正常关闭 | Ready；独立初始化审计通过，way0/app3、w24628/r25770 |
| E89 仅初始化真实BLE断连 | 真实$I应答后的一次状态交换中断开；无作业提交、无随后GATT写入、原关闭未知；管理拒绝直接再启动 | Disconnected；原管理状态requires_recovery，未伪造成功 |
| E90 显式恢复后受审文件 | 所属通道关闭、6次固定只读核查922字节及关闭审计通过；随后另行真实LightBurn执行矩形文件 | Ready→Busy→Ready；完整作业/私有收尾/关闭审计及现场观察通过 |

E89为主机请求的真实CoreBluetooth断连，不是RF衰落或设备掉电。设备确认不确定的3字节完整命令已接受，读计数增加317；三次所属路由状态稳定后关闭way0，再建立一次受限读取通道核查。原失败session.json及捕获保持失败/未知，没有把后续恢复追认为原会话成功。

## 恢复后的矩形作业

文件SHA-256 `ee68b21918bfee405ab9b140670847543b01c443fd741852abeb42b013b14b18`，使用既有真实LightBurn导出的矩形，源文件含注释439字节、实际16条/160作业字节。该文件当初由Custom GCode配置导出，本轮由原生GRBL驱动通过“运行GCode”发送，**没有重新宣称它是原生GRBL配置导出的新文件，也没有验证从新工程直接点击Start的生成路径**。

范围X0–10/Y390–400，四边40mm、F60/M4 S0，起终点X0/Y400/Z/A0。沿用有效行程内持续零功率授权，现场激光供电物理隔离。实际首次提交至私有完成核验41.148秒，73条真实Run报告，终点0,400,0,0、M5/S0、裸G0恢复通过。全部16129接收字节经CRC、序号和计数核验，通知残尾0；关闭way0/app3、w25245/r43280。

与E63历史Pi USB捕获重新独立审计对照，16条命令顺序及160作业字节完全相同；本轮没有新USB执行。LightBurn显示“流传输在0:40后完成”，此文案仅作为界面观察；完成结论由完整捕获、终点/模态/收尾/关闭审计及现场反馈支持。没有暂停、复位、回零、解锁、出光或设置写入。

## 交付与边界

推荐在当前受审文件实验中选择 `LaserLink NEJE5 Native GRBL`，配置已存在于本机LightBurn；可移植副本为 `evidence/native-grbl-ui-20261013/neje5-native-grbl-persisted.lbdev`。这是隔离下的测试配置，不是通用加工放行。旧配置原样保留。

当前69份工具源与E87的300项完整回归版本一致，没有重复全量测试；本轮新增的真实会话、恢复、历史USB及完整作业独立审计均通过。最终管理状态completed/job_completed=true、锁释放，LightBurn、实验进程及TCP4002无活动。

证据在 `evidence/native-grbl-ui-20261013/`：导入/落盘配置与偏好前后对照、执行源及散列、三个真实会话、所属路由/控制器恢复、独立审计、USB历史复审、现场反馈、实际UI观察和退出核查。UI截图在工具会话中实际查看；本目录不声称有PNG导出。

控制及状态项18/20、总分79→80/100，对外整体仍约80%。原生GRBL配置下的Start生成、暂停/停止/定位及更广故障流程仍需验收；设备半行真实断连、明确清空/定位恢复、数小时稳定和实际加工对照仍未完成。完整目标保持进行中。
