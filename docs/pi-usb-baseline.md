# 树莓派 USB / TCP 对照验证（2026-10-12）

已完成 Pi 5 的 USB 只读通信，以及 Mac 经 Wi-Fi/SSH/TCP 到 USB 的往返验证。所有设备写入限定为 `$I\n` 与 `?`，没有扩大白名单、伪造 `ok`、发送复位/运动/设置指令或修改设备模式。本轮未启动 LightBurn，不能称为 LightBurn 实际联调完成。

## 环境与访问

复用用户在 Mac 手动认证的临时 SSH 主连接；未提取 FinalShell 密码、未安装密钥或修改账号权限。树莓派为 Raspberry Pi 5 Model B Rev 1.0，Raspbian 13.6/trixie，aarch64 内核，Python 3.13.5、pySerial 3.5、BlueZ 5.82。系统尚未安装 Bleak 或 ser2net。普通账号 `neje` 已属于 dialout，有串口读写权限。

NEJE USB 为 `3c2e:5740`，驱动 `cdc_acm`，稳定路径：

```text
/dev/serial/by-id/usb-Zhixinjie_NEJE_Device_NEJE_3_Series-if00
```

该链接指向 `/dev/ttyACM0`。这也说明此前 FinalShell 的 by-id 空列表不能代表 Linux 实际无链接。原始元数据见 `evidence/pi-environment/pi-environment.json`。

## 实机结果

| 验证 | 结果 | 原始证据 |
| --- | --- | --- |
| USB 独立会话，115200/8N1 | `$I` 返回 NEJE GRBL v2.3.2、`[OPT:VMZHL,100,1500]` 和真实 `ok`；`?` 返回 Idle、FS:0,0 | `usb-readonly-01.jsonl` |
| Pi 回环 TCP → USB | 分片 `$I`、状态查询、第二客户端拒绝均通过；G0/$40=0/Ctrl-X 在 TCP 层被拒绝 | `usb-tcp-host-01.json`、`usb-tcp-01.jsonl` |
| Pi 本地客户端 EOF | 串口约 3.4 ms 后关闭，可正常建立下一会话 | 同上 |
| Pi 本地客户端真实 RST | 捕获 ConnectionResetError，约 3.1 ms 后关闭串口；该桥实例进入故障拒绝状态 | 同上 |
| Mac → Wi-Fi/SSH 转发 → Pi 回环 TCP → USB | 查询、分片、白名单、单客户端测试通过 | `usb-tcp-wifi-host-01.json`、`usb-tcp-wifi-01.jsonl` |
| 保持 TCP 客户端时 SIGTERM | 已完成查询后关闭桥接，串口释放、客户端收到 EOF | `usb-tcp-wifi-shutdown.json` |
| 最终资源检查 | Pi/Mac 均无 4002 监听；普通权限 fuser 未发现串口占用 | `pi-final.json`、`mac-final-listener.txt` |

Wi-Fi 测试中的 Mac RST 经过 SSH 转发后在 Pi 上表现为 EOF，不能把它当作远端真实 RST；真实 RST 另由 Pi 本地客户端验证。SSH 转发监听仅限 Mac `127.0.0.1:4002`，Pi 桥也仅监听 `127.0.0.1:4002`，没有开放局域网裸控制端口。测试结束已取消该端口转发。用户的 SSH 主登录保留，未关闭用户窗口。

USB 独立会话的真实状态：

```text
<Idle|MPos:0.000,400.000,0.000,0.000|Bf:100,1500|FS:0,0|Pn:P|Ov:100,100,100>
```

FS:0,0 是控制器报告，不是物理无出光的测量。本轮沿用用户此前确认的激光模组供电物理隔离状态。

## 实现与开源借鉴

`tools/usb_readonly_probe.py` 使用系统已有的开源 [pySerial](https://github.com/pyserial/pyserial)，只暴露固定两种查询。`tools/usb_tcp_readonly_bridge.py` 复用项目既有 `QueryParser`，保留有界队列、单客户端、明确本地错误与不重试原则；既有解析器的 TCP 设计参考 MIT ble-serial。没有照搬会伪造成功或自动复位的开源桥。

已读取实际安装的 pySerial POSIX open/reconfigure 实现，保存为 `pyserial-posix-open.txt`，发行版权文件为 `pyserial-copyright.txt`。打开前设置 DTR/RTS=False、关闭流控，独占锁定端口；pySerial open 仍会配置 tty、应用控制线并清理输入缓存。[官方 API 文档](https://pyserial.readthedocs.io/en/stable/pyserial_api.html#serial.Serial.open)明确提示操作系统/驱动可能在打开瞬间激活控制线。因此没有“证明不会复位”的结论，也不能用未看到启动横幅证明无复位；未进行控制线电气测量。

115200 是本轮候选参数并已实测成功。此前官方客户端 IL 中的 115200 出现在虚拟 COM 桥设置，不能单凭它推断实体 USB 参数。USB 独立查询验证了当前端点参数可用。

26 项既有回归测试通过。原始日志审计 `usb-capture-audit.json` 确认两个 TCP 捕获共 7 次 USB 写入均在白名单内、7 次转发匹配设备响应、5 次打开均对应关闭。独立 USB 捕获另有 2 次白名单写入。证据目录含 SHA256 清单。

## 对蓝牙开发的结论与下一步

USB 与此前 BLE `$I` 文本一致，说明树莓派可以提供同固件的 GRBL 对照通路，并复用 TCP 白名单层。USB 路径没有 BLE 的可靠帧计数，可用于区分上层解析与 BLE 封装问题。

严格 `$I/?` 白名单仍会拒绝 LightBurn 的 G0/$# 初始化；USB 成功不能自动解决初始化错误。本轮尚未重新运行 LightBurn，尚未安装/验证 Pi Linux BLE，未验证 USB 拔线、在途 USB 查询断连、长时间传输或 USB/BLE 争用。下一步先让现有 LightBurn 只读配置接入临时 SSH 转发作实际对照，再在 Pi 建立隔离 Python 环境验证 BLE 后端；均继续保持白名单与真实响应原则。
