# E57：真实LightBurn二维导出准备与当前阻塞

2026-10-12。已准备10×10mm二维零功率工程输入，并核查本机没有LightBurn进程或4002监听。电脑控制工具获取LightBurn时明确返回Mac锁屏、自动解锁失败；已通过异步问题请用户手动解锁，尚未收到答复。

本轮没有启动LightBurn、打开串口、连接BLE、运动、出光或复位。**没有完成原生二维GCode导出。**整体进度仍约60%，不增加实机或应用导出验收得分。

## 已完成的具体准备

工程[evidence/lightburn-xy-export/xy-zero-power-input-candidate.lbrn2](../evidence/lightburn-xy-export/xy-zero-power-input-candidate.lbrn2)从此前真实保存的10mm一轴工程复制结构，程序化替换为闭合四边形：X0..10、Y390..400，顶点(0,400)→(10,400)→(10,390)→(0,390)→闭合，Cut层速度1mm/s、两功率字段均0。

XML结构、层参数及闭合线段索引核查通过；文件SHA-256为31557fea886e000609c306d5df67402cb89f3c0b6b4ac5b24713abdc389e3cac。这是**程序生成的LightBurn输入候选**，尚未在LightBurn中打开验证；不能称为原生导出或获准作业。未修改原有工程或全局设备配置。

当前只读配置快照仍显示LaserLink NEJE5 Offline Review为260×400mm、Custom GCode、TCP127.0.0.1、自动回零关闭。仅保存相关字段，未复制完整偏好或许可证。主机进程及监听检查的原始退出码和时间戳在host-pre-export-state.json；均为无匹配/无监听，未用沙箱中的进程查询失败冒充结果。

## 新确认的导出注意点

[LightBurn官方Set Start Point指引](https://docs.lightburnsoftware.com/latest/Reference/SetStartPoint/)及[优化设置说明](https://docs.lightburnsoftware.com/1.7/Reference/OptimizationSettings/)表明，切割优化器可在减少时间时选择不同于图形输入的起点或方向。因此不能凭工程顶点0推定实际GCode从X0/Y400开始或结束；必须检查实际导出。官方仓库文档已归档并散列；站点页面直接下载返回403，失败原因一并记录，安装的2.0.05实际行为仍待界面验证。

解锁后先重新确认桥接未监听，再仅打开离线候选，确认选中TCP审阅配置、绝对坐标、0功率层、起点/方向及切割优化选项，保存由LightBurn实际写回的工程和GCode，并逐行审阅。若生成移动G0、其他切入点、非零功率或当前策略不支持的指令，保留失败文件并据此推进兼容；不得假称现有XY候选已通过，不自动改写或发送。

此前USB运动停止问题仍待现场答复；电脑解锁只允许继续原已授权的软件/离线工作，不授予新的运动、复位或出光权限。新二维动作仍按xy-linear-validation-plan.md另行确定具体文件及现场条件。

证据目录[evidence/lightburn-xy-export](../evidence/lightburn-xy-export/)包含输入候选、XML/参数审阅、只读设备字段、主机状态、电脑控制工具锁屏返回以及官方来源。上一轮225项测试仍是软件基线，本轮仅文档与输入工程准备，没有代码改动或新增回归测试。
