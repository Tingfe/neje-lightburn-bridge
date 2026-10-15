# macOS 图形控制台打包结果

2026-10-14。NEJE Bridge 1.1.0 / build 2 已形成独立 windowed `.app` 与 ZIP，本机可正式测试。主模式保持原生透明桥接，服务地址仍为 `127.0.0.1:4002`。未提交或推送 GitHub；此前 CLI、Terminal 包装与实验候选保留。

## 交付文件

- `dist/NEJE-Bridge-1.1.0/NEJE Bridge.app`
- `dist/NEJE-Bridge-1.1.0/NEJE-Bridge-1.1.0-macOS-arm64.zip`
- [正式测试指南及反馈格式](gui-mac-test-guide.md)
- 原始构建、诊断、测试与只读设备证据：`evidence/gui-package-20261014/`

## 实现

采用开源 PyInstaller 的 EXE/COLLECT/BUNDLE windowed 方案，打入 Python 3.12.15、Tcl/Tk 9.1.0 和 bleak/PyObjC 依赖。BLE 桥接和模式检查通过同一包内 Mach-O 的内部参数重入，内部子进程不创建第二个窗口、不依赖外置解释器或丢失的 `.py` 文件。正常双击只打开界面，不自动启动设备连接。

修正窗口退出：停止所属桥接，等待子进程清理和审计结束后销毁窗口；菜单 Quit 和关闭按钮同一路径。子进程 stdout/stderr 保存为 `.process.log`，窗口异常保存为 `desktop-launch.log`，应用动作落到 `app.log`；运行日志全部位于用户 Library，运行时不修改应用包。首次“重新准备连接”不能绕过完整模式确认；隔离试跑新增明确确认，不能默默声明物理隔离。端口占用时不另起子进程。有效时长拒绝非数、非有限数及范围外值。

设备扫描、实际选择、已有 LightBurn 配置帮助与“打开日志”入口纳入界面；修正深色系统外观下硬编码深色文字不可读的问题，使用原生主题前景色。

参考实现与说明：[PyInstaller spec/BUNDLE 文档](https://pyinstaller.org/en/stable/spec-files.html)、[macOS 签名说明](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing)、[windowed 标准流限制](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#sys-stdin-sys-stdout-and-sys-stderr-in-noconsole-windowed-applications)。这些参考用于打包、签名和窗口诊断，不改变既有 NEJE/GRBL 协议语义。

## 验证与范围

- 完整回归 **431项、79.735秒、全部通过**；含12项图形入口/生命周期专项。最终完整回归之后仅补充build编号及默认产物目录，未改传输逻辑。此前428/430项候选输出也保留，不用候选结果冒充最终构建。
- 最终包三项离线入口检查均退出0：包内Tcl诊断、内部桥接干跑、内部模式检查帮助。清除外部PYTHONPATH/TCL_LIBRARY/TK_LIBRARY仍可运行，frozen=true，诊断读取到build 2，Tcl及_tkinter实际位于应用Frameworks目录。
- 本机实际打开窗口，启动确认取消、隔离试跑确认取消均未创建桥接；候选界面实际扫描到25个BLE设备并选择唯一NEJE 5 Plus。深色主题文字可读。最终build 2窗口/隔离确认取消/正常退出已实际核验；重新打开后实际扫描20个设备并选好唯一NEJE，交付时界面“已停止”，未启动桥接。
- **最终包内BLE只读查询通过**：18:40:16连接，向FFE9只提交verify及SystemRead(0xFF)两包；收到身份、system_info/app_info/system_flags，current_app=3；18:40:18正常断开，pending_hex为空、trial_result.success=true、complete，进程退出0。没有启用GRBL可靠路由，也没有读取/写入机器运动坐标。current_app=3仅证明应用ID，不单独证明GRBL ASCII通信。
- 正常Quit后应用清单isRunning=false；独立主机核查无NEJE Bridge进程、无TCP4002监听。此轮没有活动GRBL桥接子进程，因此实际退出核验只覆盖空闲窗口；“等待活动子进程清理后关窗”的流程经过模拟测试，带真实作业退出仍需正式反馈。
- 最终.app深度严格签名核验通过；ZIP完整性核验通过。应用约31MiB，ZIP约12MiB。清单与源码快照用于将后续反馈对应到当前产物。

本轮没有通过新打包 GUI 启动作业，没有运动、出光、回零、设备设置写入或复位；**也未把包内系统信息读取称作真实 LightBurn GRBL 初始化或 GUI 运动停止验收**。既有源码真实 LightBurn 初始化与严格关闭成果见[原生透明完善结果](native-transparent-hardening-result.md)。本轮 LightBurn 界面显示 No Machine；现有磁盘配置仍存在，正式测试需在实际面板选择该设备并核验 Ready。

## 分发边界与进度

本机 Apple Silicon / arm64，最低 macOS 27，当前运行 macOS 27.2。构建机器的 Python 运行时部署目标为 27，不能宣称兼容更早 macOS。本机没有有效 Developer ID 签名身份，采用 ad-hoc 签名并执行深度严格签名核验；没有 Apple 公证、跨 Mac 安装或公开发行验收。

整体工程估算 **90→91%**：日常交付入口及文档由 4/5 调整为 5/5，其余评分保持原值。这个分数表示本机图形候选可交付测试，不代表全部 GUI 设备控制已经实机通过或公开分发完成。达到 100% 还需当前透明版点动/停止/恢复的实机对照、BLE 数小时运行与同条件 USB/BLE 加工成品核验；按用户反馈继续完善。
