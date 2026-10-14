# 树莓派环境接管记录（2026-10-12）

**最新进度：**真实 LightBurn 经 Pi USB/BLE 查询、Linux BLE 在途断链及显式清理均已验证，见[最新报告](pi-lightburn-ble-comparison.md)。Pi 内核 aarch64，用户空间实际为 32 位 armhf；Bleak 仅安装于临时 venv。测试服务/转发已关闭，用户 SSH 主连接保留。前期基线见 [USB/TCP 对照验证](pi-usb-baseline.md)。下文保留先前接管过程及当时的访问障碍。

本轮通过用户已登录的 FinalShell 4.6.5 会话检查，不读取保存的密码、私钥或认证配置。尚未部署桥接，也未打开串口或发送 GRBL/BLE 命令。

## 已观察到的信息

| 项目 | 证据与结论 |
| --- | --- |
| 会话 | FinalShell 的 NEJE 会话显示已连接 |
| 地址与账号 | 界面 IP 为 `192.168.8.19`；终端提示符 `neje@NEJE-PI:~ $` |
| 系统 | 登录横幅显示 `Linux NEJE-PI 6.18.50+rpt-rpi-v8 #1 SMP PREEMPT Debian 1:6.18.50-1+rpt1 (2026-09-11) aarch64`；发行版版本、Pi 型号尚待命令核查 |
| 资源 | FinalShell 监控显示总内存约 3.9G、交换空间 2G、根分区 28.1G/可用约 23.3G |
| 网络 | 界面显示活动接口 `wlan0`；尚未验证 LightBurn 到桥接端口的通信 |
| USB 串口候选 | 通过现有 SFTP 会话列出 `/dev`，看到 `ttyACM0` 和 `serial`；没有打开设备节点 |
| 稳定名称 | 浏览 `/dev/serial/by-id` 时界面列表为空；SFTP 对符号链接的展示可能不完整，不能据此认定系统没有稳定名称 |

原始目录可访问性文本与截图保存在 `evidence/pi-environment/`。FinalShell 将部分特殊设备显示为“文件夹”，这不是 Linux 文件类型证据；只采用节点名称这一观察。

## 当前访问障碍

图形按钮和文件目录浏览可以操作，但多次聚焦终端/命令输入栏后，自动输入 `uname -a` 没有可见回显或结果。因此不能声称命令已执行。没有执行包安装、服务修改、权限修改或网络端口开放。

独立 SSH 先以严格主机校验尝试，因本机未记录该地址的主机密钥退出；随后使用临时专用 known_hosts 和首次连接记录方式尝试 BatchMode，返回 `Permission denied (publickey,password)`。现有 FinalShell 登录状态不能直接供独立 SSH 复用。未提取 FinalShell 凭据、未新增 authorized_keys。

已请用户在原会话手动运行以下元数据查询，等待读取输出：

```sh
uname -a; cat /proc/device-tree/model; echo; lsusb; udevadm info --query=property --name=/dev/ttyACM0
```

这些命令不打开串口、不修改设备设置。获得 VID/PID、产品名、驱动及端口关联后，才能把 `ttyACM0` 从候选提升为 NEJE USB 身份证据。

## 后续验证门槛

1. 先核查 USB 身份、串口权限、Python/BlueZ 环境与端口占用。
2. 研究并核对 USB 打开时的 DTR/RTS/复位行为，然后在已有隔离与只读授权范围内建立 `$I` / `?` 基线。
3. 接入带白名单的 TCP 层，再验证 LightBurn 初始化和断连清理。保留真实错误，不伪造 `ok`。

当前完成的是环境信息观察与设备节点发现，尚未完成 USB 通信、Pi TCP 桥接或 Linux BLE 验证。

## 用户恢复会话后的复查

已从当前终端滚动历史中读取到上述命令及真实输出，新增截图 `pi-model-lsusb.png`、`udev-neje.png`，并保存明确标记为人工转录的 `observed-usb.txt`。

- 型号确认：**Raspberry Pi 5 Model B Rev 1.0**。
- `lsusb` 确认：`Bus 003 Device 002: ID 3c2e:5740 Zhixinjie NEJE Device`。
- udev 将 `/dev/ttyACM0` 关联到 `ID_BUS=usb`、`ID_MODEL=NEJE_Device`、`ID_VENDOR=Zhixinjie`、VID/PID `3c2e:5740`；序列字符串为 `NEJE_3_Series`，USB revision 为 `0200`。这些字符串不能单独证明雕刻机具体型号或 GRBL 固件版本，后续仍需真实查询核对。

当前用户会话停在 udev 信息分页器 `lines 1-25`。重新绑定运行进程与应用路径、聚焦终端和命令栏、发送 `q`、粘贴只读命令均未产生新输出；鼠标滚动可读取既有输出。因此已确认 USB 枚举身份，但尚未恢复自动执行命令能力。没有把尝试发送的命令视为已执行，也没有进行串口通信。

下一步需要恢复可用终端输入，再核查账号权限、Python/BlueZ、串口驱动和占用，随后推进已约定的只读通信验证。

## 分页退出后的继续检查

用户已退出分页器，截图确认回到 `neje@NEJE-PI:~ $`。再次聚焦并输入环境查询仍无回显，说明当前障碍不能归因于分页器。尚未取得账号组、Python 模块、BlueZ 服务、串口占用的结果。

已准备 `tools/pi_environment_probe.py`，只读取系统与设备元数据，使用 `stat`/`os.access` 检查串口权限、读取 sysfs 驱动路径、列出稳定名称、查询现有软件和服务状态；不会打开串口、连接 BLE 或发送 GRBL 指令。已完成本机语法检查，尚未在树莓派执行。

为继续批量核查，已请用户在本机终端用现有认证建立临时 SSH 复用会话：

```sh
ssh -M -S /private/tmp/neje-pi-20261012.sock -o ControlPersist=30m -o UserKnownHostsFile=/private/tmp/neje-pi-known-hosts -o StrictHostKeyChecking=yes neje@192.168.8.19
```

该方式沿用已有账号，不添加 authorized_keys，不读取 FinalShell 保存的密码。主机密钥校验使用前次连接记录的专用文件。等待用户完成登录后，通过控制套接字执行元数据脚本并将 JSON 输出保存到项目。
