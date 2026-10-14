# 树莓派 USB / Wi-Fi 对照通路评估（2026-10-12）

**实施更新：**已完成真实 LightBurn→Pi USB/BLE 只读对照及 Linux BLE 在途断链，见[最新实机报告](pi-lightburn-ble-comparison.md)。USB 与 BLE 均有初始化 G0/$# 拦截错误；树莓派通路可用，但不自动解决客户端初始化兼容。下面为实施前评估，相关“尚未验证”状态以新报告为准。

树莓派有帮助，建议作为USB对照平台及后续Linux BLE实验主机。现已通过用户已登录的 FinalShell 观察到 `neje@NEJE-PI`、`192.168.8.19`、aarch64 系统及 `/dev/ttyACM0` 候选节点；型号和 USB 身份尚待核查，没有部署或验证树莓派通路。详见[环境接管记录](pi-environment.md)。

拟验证两条路径：

```text
LightBurn → 局域网 Wi-Fi/TCP → 树莓派 → USB → NEJE 5 Plus
LightBurn → 局域网 Wi-Fi/TCP → 树莓派 → BLE可靠协议 → NEJE 5 Plus
```

USB路径用于先确认串口身份、通信参数、真实$I/?响应、TCP分片和断连行为；BLE路径复用相同TCP解析、白名单与日志，仅替换设备传输层。由此可以对比初始化行为、响应边界、计数和异常处理，区分TCP/客户端问题与BLE可靠封装问题。USB正常不等于BLE可靠性已验证。

LightBurn官方支持GRBL Ethernet/TCP连接：[网络连接指南](https://docs.lightburnsoftware.com/legacy/Guides/GRBLNetworkConnection)。开源[ser2net](https://github.com/cminyard/ser2net)提供TCP到串口接口，可优先借鉴。其[配置文档](https://github.com/cminyard/ser2net/blob/master/ser2net.yaml.5)支持单客户端、来源地址限制等设置；通用透明转发不实施本项目的$I/?命令白名单，不能直接对LightBurn开放一个无过滤串口端口。

建议测试结构为：LightBurn → 白名单/审计层 → 仅本地可访问的串口后端，或直接由现有Python桥接增加串口后端。端口仅向指定局域网客户端开放，不改变目前Mac回环桥接的绑定规则。串口波特率、DTR/RTS行为和设备应用状态必须现场核实，不预设打开USB不会导致复位或路由变化。

LightBurn G0/$#初始化行为与USB或BLE无直接等价关系。若仍只允许$I/?，换成USB也必须拒绝G0/$#，不能承诺错误提示因此消失。USB透明桥接可能通过真实设备执行获得初始化应答，但它会扩大当前可执行命令范围，因此不是本轮已授权的只读实现。未来若审核并允许最小初始化命令，可以返回真实设备响应，不需要伪造ok。

树莓派还可运行现有Bleak代码，其[Linux后端](https://bleak.readthedocs.io/en/latest/backends/linux.html)使用BlueZ/D-Bus，提供macOS CoreBluetooth以外的对照。地址识别、通知行为、断连时序等仍需Linux实测。不能假定USB/BLE可同时控制或相互恢复；先验证是否争用同一个GRBL路由。普通蓝牙适配器也不能直接视为空中协议嗅探器。

不应直接使用官方LightBurn Bridge镜像作为NEJE方案；[该产品文档](https://docs.lightburnsoftware.com/2.0/Reference/LightBurnBridge/)面向Ruida控制器，与这里的GRBL USB/TCP桥接不同。

建议顺序：

1. 树莓派接USB，核查枚举、串口参数和打开端口副作用，保持现场激光供电隔离与$I/?白名单，建立日志基线。
2. 让LightBurn接树莓派TCP白名单层，记录初始化拒绝和真实查询；验证Wi-Fi/TCP退出时的清理。
3. 在同一树莓派切换到BLE后端，沿用查询、日志与故障注入，比较USB与BLE；独立核查串口/BLE争用后再考虑USB辅助状态检查。

实施需要树莓派型号、系统版本、局域网IP、SSH用户名及现有登录方式；不要在聊天中粘贴密码或私钥。
