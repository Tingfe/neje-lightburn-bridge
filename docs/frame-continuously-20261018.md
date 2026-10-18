# LightBurn 连续构建边框设置核查（2026-10-18）

用户反馈所选 `LaserLink NEJE5 (蓝牙桥接)` 设备的边界预览只跑一圈。本机 LightBurn Pro 2.0.05 的设备设置窗口实际显示：**基础设置 → 常规选项 → 持续构建边框**，当前未勾选；只读解析同一设备 `prefs.ini`，`Settings.ContinuousFrame=false`。该设备的驱动类型为原生 **GRBL**，网络端口为 **4002**。核查后通过「取消」退出设置窗口，没有改动设备配置，也没有发出追踪/运动命令。

LightBurn [官方 Framing 指南](https://docs.lightburnsoftware.com/latest/GetStarted/FramingBeginner/)说明，启用 **Frame Continuously** 后，边界路径会重复运行，按 Frame 会出现提示窗口，点击 **OK** 才结束；其[官方论坛支持答复](https://forum.lightburnsoftware.com/t/encuadre-continuo/181766)确认 GRBL 设备类型可在 Device Settings 的 Basic Settings 找到此选项，而 Custom GCode 类型可能没有。故本机一次追踪的直接原因是该设备选项未启用，不能据此判断蓝牙桥接少转发命令。

需要连续预览时，在 LightBurn 当前桥接设备上打开 **设备设置 → 基础设置 → 常规选项 → 持续构建边框**，勾选并按「确定」。这是 LightBurn 的本地设备偏好，不需修改桥接协议或 NEJE 固件。实际循环追踪仍需现场观察；边界预览会让机器重复运动，不能把关闭弹窗视为已确认机器即时停止，停止结果以设备实际状态为准。
