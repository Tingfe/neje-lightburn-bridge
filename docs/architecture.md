# 桥接如何工作

```text
LightBurn ──本机 TCP 127.0.0.1:4002──> NEJE Bridge ──BLE 可靠通道──> NEJE 5 Plus
           <────────────── 真实 GRBL 应答与状态 <──────────────
```

LightBurn 把本程序当作原生 GRBL 设备。透明模式接收普通 GCode 行和实时控制字节，经 NEJE 的 BLE 可靠通道发送；设备的真实响应再交还 LightBurn。桥接处理分帧、流控、应答归属、计数与 CRC，不凭空生成 `ok`，也不自动重放失败作业。现行透明模式**没有作业内容白名单**：开始、运动、功率、回零与设置命令会真实到达控制器。[透明模式设计记录](transparent-bridge-plan.md)

## 一次连接的生命周期

1. 图形程序启动桥接，监听仅绑定本机 `127.0.0.1:4002`。
2. LightBurn 连入后，桥接建立一条属于本次会话的 BLE 路由；同一时刻只服务一个 LightBurn 客户端。
3. 桥接逐行转发普通命令，单独处理状态、暂停、恢复、停止等实时字节；原始帧和结果写入本机日志。
4. LightBurn 正常断开后，仅在旧应答排空、设备确认为空闲、清理与审计通过时，图形程序才准备下一次连接。异常断开会提示检查和显式恢复，不接续旧作业。[断开生命周期](lightburn-disconnect-lifecycle-20261015.md)

控制器可能已经缓存整段作业。因此**软件断开不等于物理停止**；停止请求也要区分“已发出”“控制器确认”“现场运动停止”。软件复位后的坐标基准必须重新确认。[停止实测](lightburn-motion-stop-hardware-result.md) · [断连实测](job-disconnect-result.md)

## 软件组成

| 部分 | 作用 |
| --- | --- |
| [图形程序](../tools/neje_app.py)、[启动入口](../tools/neje.py) | 选择设备、启动/关闭桥接、显示状态、保存日志 |
| [TCP/BLE 桥接](../tools/pipeline_tcp_bridge.py)、[透明传输](../tools/transparent_grbl_pipeline.py) | 会话、流控、停止代际与故障处理 |
| [输入分流](../tools/passthrough_policy.py) | 区分 GCode 行与 GRBL 实时字节；原生 `$J` 点动不改写为 GCode |
| [捕获审计](../tools/check_transparent_capture.py) | 离线核查转发字节、真实应答、计数及清理 |

早期“受审文件/只读白名单”工具仍保留，用于协议研究和受限实验；**不是当前图形程序的日常作业流程**。底层帧与可靠传输细节见[协议](protocol.md)和[可靠通道](grbl-reliable.md)。
