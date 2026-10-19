# 日常使用指南

适用范围：**NEJE 5 Plus / A40640、macOS 上的 NEJE Bridge、LightBurn 原生 GRBL/TCP 设备**。当前图形程序是透明桥接：不要求预先导出、审阅或锁定 GCode 文件。

**固件兼容提示（2026-10-19）：**本机实测版本组合为 System/NEJE/GRBL **2.3.2/2.3.2/2.3.2**。升级到三项 2.4.0 后，旧 BLE GRBL 状态查询失答；回退并完整重启后，USB、BLE 通道和真实 LightBurn 初始化恢复，但尚未重新加工。其他版本未验证。[版本核对与处理](firmware-compatibility.md) · [回退验收](firmware-rollback-decision-20261019.md)

## 首次设置

1. 安装 [macOS arm64 构建产物](https://github.com/Tingfe/neje-lightburn-bridge/actions/workflows/macos-build.yml)中的 **NEJE Bridge.app**。1.3.0 双语包的发布状态见[当前状态](overall-progress.md)。
2. NEJE 上电，确认三项固件版本及 GRBL 模式符合[已验证范围](firmware-compatibility.md)，且没有其他软件占用设备或本机 `4002` 端口。
3. 打开 NEJE Bridge，选择机器，点 **启动桥接**。
4. 若 LightBurn 尚无桥接设备，保存工程并退出 LightBurn，再点 **创建 LightBurn 设备**。程序会备份原配置；重新打开 LightBurn 后选择新增设备。
5. 核对设备为 **GRBL / TCP、127.0.0.1:4002**，工作范围和原点与机器一致。连接后应看到真实设备响应和 Ready；设备列表里出现名称本身不代表已连通。

以后使用：**先启动桥接，再连接 LightBurn，直接预览或 Start。** 不需要命令行、Pi 或 USB 对照流程。正常断开 LightBurn 后，程序会核查设备和清理结果；通过后继续待命，直到你关闭程序。

## 加工与停止

- 在 LightBurn 中按材料、对焦和图层设置检查预览，再开始作业。桥接传送 LightBurn 实际生成的命令；修改设备设置也会真实写入控制器。
- LightBurn **Stop** 或程序里的 **软件停止设备**会请求控制器停止。确认设备确实停下后，再处理材料或重新定位。复位后不要沿用旧坐标；必要时回零。
- **关闭桥接、退出程序、拔掉蓝牙连接都不能保证设备停止。** 控制器可能继续执行已经缓存的作业；软件停止未确认或运动异常时，用可独立操作的设备电源切断方式。
- 出光时现场看护，备好相应波长护目镜、通风和灭火设备，确保整个运动范围无人触碰。

## 常见问题

| 现象 | 先检查 |
| --- | --- |
| LightBurn 连不上 | 程序是否显示桥接运行；设备是否选 GRBL/TCP、`127.0.0.1:4002`。不要同时开两个桥接。 |
| 固件更新后桥接无法初始化 | 核对 System/NEJE/GRBL 三项版本并保留日志；本机三项 2.4.0 时曾出现 BLE `0x40` 失答。不要绕过状态检查或反复点击 Start。[兼容范围](firmware-compatibility.md) |
| 设备显示非 GRBL 模式，USB 与蓝牙都连不上 | 停止操作；确认设备未运动后，完全断开设备电源和设备端 USB 至少 10 秒，再先接电源、后接 USB。重启后先查模式和位置，不连续点击 Start/Home。[现场案例](connection-recovery-20261018.md) |
| Ready，但 Start 完全无动作 | 先别反复点击。看 LightBurn 预览是否有路径和非零时长；若工程图片丢失，重新导入并保存可重开的工程。预览正常时，再看桥接日志是否收到作业。[图片诊断](image-job-no-start-20261015.md) |
| 百分比到 100%，机器还在动 | LightBurn 显示的是命令传送进度，不是机械完成比例；以设备真实 `Idle` 和现场动作结束为准。[界面说明](https://docs.lightburnsoftware.com/latest/Reference/LaserWindow/) |
| 边框只走一圈 | 在所选设备的 **设备设置 → 基础设置**启用“持续构建边框”。[本机核查](frame-continuously-20261018.md) |
| 正常断开后不能重新连 | 等程序完成本次空闲核查和清理；若提示故障或状态未知，先确认机器真实状态，再按界面提示显式重新连接。不会自动接续旧作业。[生命周期](lightburn-disconnect-lifecycle-20261015.md) |
| Stop 后回零报超时 | 保留原始日志，不重复下发 Home；先确认运动已结束和位置基准，再检查版本及[回零等待记录](homing-status-watchdog-20261015.md)。 |

## 日志与反馈

程序菜单中的“打开日志文件夹”可查看 `~/Library/Logs/NEJE/`。遇到问题时保留对应 `neje-*.jsonl`、`.process.log` 和 `app.log`，记录操作时间、LightBurn 原始报错、现场运动及出光情况。后续成功不应覆盖失败记录。

开发者入口：`python3 tools/neje.py`；带隔离条件的试跑：`python3 tools/neje.py --test`；只检查设备模式：`python3 tools/neje.py --check`。这些命令需要源码依赖，不是普通用户的必需步骤。更多设计与证据见[文档索引](README.md)。
