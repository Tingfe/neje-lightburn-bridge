# 真实LightBurn BLE运动中Stop

2026-10-13，E75。USB运动中一次复位停止已有E72证据；E74单轴XY回零已恢复控制器Idle/X0/Y400基准，设置/模态/偏移未改变。按用户持续有效行程内移动及停止授权推进，不再逐次询问同类运动。

真实Mac LightBurn加载同一固定14条、144作业字节的 `evidence/pipeline-lightburn/ten-mm-reviewed.gc`，SHA-256 `89c60986076dca8edd9031e9f9b6c0c09b9fd07affa2bf32cd9cbea26b062d17`。X0→10→0/Y400/Z/A0、F60、M4 S0，激光供电仍物理隔离。使用现有pipeline_tcp_bridge固定作业+控制入口，真实BLE重新预检Idle/参数/模态/偏移/空启动块。

应用原生Run GCode启动文件，观察原始BLE状态Run且X2–8窗口，点击真实Stop按钮一次。桥接应只提交一次0x18，所有在途普通应答/实际启动行与新状态保留，不伪造成功，不把复位后的坐标作为可信终点。若界面延迟错过窗口，停止成功不作确认；不因观察超时重复启动/点击，不自动补跑或返程。

收集ALARM/Grbl启动行及新Idle/Alarm，退出LightBurn，核验关闭way0/app3、计数、TCP/进程/通知尾部。独立运行check_motion_cancel_capture的BLE后端，核验真实TCP输入、原始GATT分帧与CRC、完整字节计数/应答账本、一次复位、在途失效和关闭。Job必须保持未完成，位置可信标记false。界面错误显示与设备报警分开记录，不能隐藏ALARM或伪造Ready。

本次只做一次固定作业Stop，不故障注入、不暂停恢复、不解锁、不回零、不写设置、不追加任何返程。停止后定位恢复是后续显式步骤，不在作业内自动执行。界面按钮/状态由CUA实际读取，不使用脚本向TCP代发应用控制命令。

源码、方案、日志、应用观察及独立审计保存在 `evidence/lightburn-motion-stop-20261013/`。新管理入口暂只接受正常完成审计，因此本次控制实验使用原受限工具，不通过新会话目录绕过其故障锁；原管理入口状态保持原E71结果。
