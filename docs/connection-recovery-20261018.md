# 2026-10-18 USB/BLE 同时无法连接：现场诊断

## 已核实

- 用户报告 USB 直插 Mac，设备指示灯正常。
- 10:55–10:56，macOS 存在 `/dev/cu.usbmodemNEJE_3_Series1`；10:55 时 LightBurn（PID 57992）持有该串口。LightBurn 当前窗口是空白 `<untitled>`、设备为 USB 串口/GRBL，控制台反复显示设备原文：`[MSG: Your device is not at GRBL mode, Please restart to switch to GRBL mode.]`。
- 本机 `127.0.0.1:4002` 无监听。`~/Library/Logs/NEJE/app.log` 显示 10:48–10:52 多次尝试启动桥接，均以退出码 1 收尾；10:53 已关闭图形桥接。
- 原始捕获 `~/Library/Logs/NEJE/neje-20261018T105119828577.jsonl`：10:51:20 读到 `current_app=3`，但通信通道 `way=2`，桥接以 `Existing transport active; refusing takeover` 拒绝接管。`~/Library/Logs/NEJE/neje-20261018T105246406307.jsonl`：10:52:59 读到 `current_app=2`，桥接以 `Requires existing GRBL app 3` 拒绝启动。两次均无作业提交，且未转发作业字节。
- 10:56:54 再次核查，串口仍存在，但已无进程持有；4002 仍无监听。空白 LightBurn 窗口在尝试退出时已不再运行，未强制结束进程。

## 结论及边界

当前直接原因是设备应用模式不符：USB 侧收到固件明确提示，BLE 桥接也从系统信息读到了 `current_app=2`，而两条 LightBurn 路径要求 `current_app=3`（GRBL）。此前短暂的 `app=3/way=2` 表明另一路传输曾占用，桥接拒绝接管是预期的保护行为。**无法仅凭这些日志判定 app=3→2 的切换原因**；也不能将 4002 无监听误判为 USB 硬件未识别。

源码 CLI 的 `python3 tools/neje.py --check` 本轮未能独立复核设备：本机当前 Python 缺少 `bleak`，日志 `logs/mode-check-20261018T105635.jsonl` 记录 `ModuleNotFoundError`。此失败是诊断环境缺依赖，不是蓝牙硬件故障证据。

## 重启后恢复核验

用户已按要求使设备电源与 USB 均断开至少 10 秒，然后先接电源、再接 USB。10:57:27 串口重新枚举，且无人持有；4002 无监听。

- 已安装的 NEJE Bridge 1.2.4 在可访问主机蓝牙的上下文执行官方只读 Verify + SystemRead：扫描到 NEJE 5 Plus，BLE 建连成功，设备原始系统帧解码为 `current_app=3`；`trial_result.success=true`，正常断开。原始证据复制到 `evidence/connection-recovery-20261018/ble-mode-after-restart.jsonl`。
- USB 使用固定 `$I` 与 `?` 只读探针：`$I` 返回 `[VER:1.1f.NEJE build version:v2.3.2:]`、`ok`；`?` 返回 `<Idle|MPos:0.000,0.000,0.000,0.000|...|FS:0,0|...>`；串口正常关闭。原始证据为 `evidence/connection-recovery-20261018/usb-readonly-host.jsonl`。探针不发送回零、运动、出光或设置写入。受限终端曾在打开 USB 时收到 `Operation not permitted`，随后在可访问设备的主机上下文成功；前一次失败保留在同目录 `usb-readonly.jsonl`。
- LightBurn 的现有 **USB** GRBL 设备配置 `HomeOnStartup=True`，蓝牙 TCP 设备配置 `HomeOnStartup=False`。因此本次不通过 USB 设备启动 LightBurn 作只读验收，以免自动回零。

**结论：USB 的 GRBL 查询与 BLE 的官方模式读取均已恢复。** 还未核验本次重启后的完整 LightBurn→4002→BLE 初始化，也未触发 USB LightBurn 连接。完整断电可能丢失位置基准；当前 `MPos:0,0` 只是控制器报告，未经确认或回零前不执行作业。不以 GUI 的 `Ready` 字样单独认定完整作业链成功。故障排查期间没有运行运动、出光、回零或设置写入。
