# 运动复位后的XY定位恢复

2026-10-13，E74。承接E72 USB运动停止后的Alarm状态。用户已经持续允许有效行程内零功率移动；本步骤属于该范围，不重复请求小行程授权。激光供电保持此前物理隔离。

NEJE 5 Plus官方手册给出左上方回零点、左下方坐标原点及轴选择$H命令；现代NEJE回零说明区分机械开关与电流检测。实际控制板版本和检测方式不从设备名称单独推定。开源gnea/grbl固定提交 `bfb67f0c7963fe3ce4aaf8a97f9009ea5a8db36e` 的system.c提供单轴$HX/$HY路径，协议层忽略空格。NEJE文档的轴选择写法因此采用$HX、$HY；若固件拒绝，结束，不转为全轴$H或解锁。官方Windows的NejeHoming是另一套系统命令，本次不发送。

先执行只读检查?、$$、$G、$#、$N、?。要求真实Alarm、两次报告状态/位置稳定且FS0,0；X报告0–10、Y400/Z/A0，仅作为识别这次停止现场，绝不代表坐标可信。重新确认$22=1、$23=1、$24=250/$25=2500、260×400mm、毫米状态、M5/S0/G21/G90/G94/G54、零偏移及空启动块。缺失、重复或变化均阻止回零。

随后单独再次执行同样预检，只发送一次$HX，等待真实ok并查询Idle/X0/Y400/Z/A0；符合后才发送一次$HY并核验同样终点。回零使用设备自身速度2500mm/min及定位速度250mm/min，不是此前作业F60。所有普通查询序列化，最长单条等待60秒，任一错误/超时/不确定写入立即结束，不重试，不自动回退$H/$X，不续跑旧作业或修改坐标/设置。机械异常由现场独立停止，不以软件日志代替现场判断。

最后重新读取$$/$G/$#/?，确认设置未改变、模态/偏移正确及Idle/X0/Y400。控制器回零完成和报告基准是通信证据；现场观察单独记录，不宣称独立定位精度测量。

候选入口 `tools/homing_recovery_probe.py` 默认离线，只支持USB和固定X/Y顺序。实际只读用 `--execute --inspect-only --laser-power-isolated --log <新文件>`；实际回零用 `--execute --allow-xy-homing --laser-power-isolated --log <新文件>`。执行前部署新不可变源包并核验摘要、串口空闲、日志不存在；进程只启动一次，超时只轮询原句柄。全部证据保存到 `evidence/homing-recovery-20261013/`。

官方来源：[NEJE 5 Plus手册](https://neje.wiki/doku.php?id=cn:nejelaser_cn_howto_install_n5_plus_cn)、[NEJE回零原理](https://wiki.neje99.com/en/manual/homing)、[NEJE回零故障说明](https://wiki.neje99.com/en/troubleshooting/homing)。这些资料只用于本次恢复，不将上游GRBL可选编译功能当作NEJE实测成功。
