# NEJE 5 Plus：固件 2.4.0 下 BLE GRBL 连接桥状态无应答

状态：**2.4.0 故障的历史报障材料，尚未发给 NEJE。** 用户后来通过官方 Windows v6.8.5 回退至三个 2.3.2；完整重启后 BLE 通道和真实 LightBurn 初始化恢复。[回退验收](firmware-rollback-decision-20261019.md)。本文不含设备序列号、蓝牙地址、加工文件或本机绝对路径。原始日志留在本项目本地 `evidence/connection-20261019/`。

## 设备和版本

- 设备：NEJE 5 Plus，主板硬件 2.0.1，协议 Verify 返回 model 55。
- 上位机：macOS 27.2 上基于官方旧版通信帧实现的 BLE→LightBurn 桥接 1.3.0（升级前可用）；Windows NEJE Neo v7.0.0 用于用户执行的固件升级。
- Neo 界面：当前固件包 `v3.0.0 调试版3`。NEJE 的[公开配置](https://api.neje99.com/v1/configs/neje_neo/config.json)映射 `debug_3 / v3.0.0` → `system_v2.4.0, grbl_v2.4.0, neje_v2.4.0`；设备 BLE SystemRead 实读三项均为 2.4.0。蓝牙模块标准 GATT 固件修订另为 V3.1.2。

## 可复现现象

升级前，System/NEJE/GRBL 均为 **2.3.2**。2026-10-18 12:58（北京时间），在 GRBL 应用 ID 3 下向 FFE9 写入 `FF AA 08 40 01 00 00 55`（GrblCommExGetStatus），FFEA 收到真实 `FF AA 0D 41 01 00 00 00 00 00 00 00 55`（way=0）；桥接随后成功运行和正常关闭。用户完成了实际切割。

升级后，三项均为 **2.4.0**。在同一主板、GRBL 应用 ID 3 下，BLE Verify 与 SystemRead 仍正常，USB GRBL `$I` 和 `?` 也正常；但同一 `0x40` GetStatus 写入后 5 秒内 FFEA/FFE9 均无协议通知。连续 8 次桥接启动在这一步失败；超时后 SystemRead 仍可立即返回，说明 BLE 连接与通知并未整体失效。NEJE 3 的 Mac 蓝牙客户端也可正常使用 NEJE 应用 ID 2。

已排除单一初始化顺序解释：SystemRead(ALL)、SystemRead(INFO)、跳过 SystemRead、等待系统多帧读回、FFE9 带响应写入均未恢复 `0x40` 应答。按旧版官方 Windows v6.8.5 的顺序单次尝试 SetStatus(BLE=2) → GetStatus，仍无回复；随后尝试一次 SetStatus(0) → GetStatus，也无回复。**最终路由状态无法读回，不能把清理命令的发送视为已关闭。** 该次探针没有发送 GRBL 作业字节、运动或出光指令。

## 希望 NEJE 确认

1. `debug_3 / v3.0.0` 包内的 GRBL v2.4.0 是否仍支持 BLE GRBL 可靠通信命令组 `0x40`？若支持，GetStatus 的帧、通知特征值和初始化条件是否改变？
2. 当前 Verify/SystemRead 可用、app=3、USB GRBL 可用，却没有任何 `0x40` 通知，是否属于已知问题？有无无需刷机的诊断与恢复步骤？
3. SetStatus(2) 与 SetStatus(0) 均无回复时，如何可靠确认路由已回 way=0？
4. 若需要回退，请确认**适用于 NEJE 5 Plus、主板 2.0.1 的安全回退流程**、三分项配套/刷写顺序、失败恢复和 GRBL 设置备份/复原步骤。Neo v7.0.0 页面没有 2.3.2；旧 Windows v6.8.5 的[官方固件目录](https://api.neje99.com/v1/configs/neje3_firmware/config.json)将 System/NEJE/GRBL 2.3.2 全部标为启用并列出 NEJE 5 Plus，文件 CRC 匹配，用户现场也确认旧更新中心可选择三项 2.3.2。实际 2.4.0→2.3.2 回退结果仍待核验；本项目**没有刷写或尝试回退**。

本材料按 NEJE 的[软件问题报告清单](https://wiki.neje99.com/en/general/report-a-bug)提供设备型号、软件/固件版本、复现步骤、发生频率（本机重复）、预期与实际结果。可按厂家要求另提供经人工检查和脱敏的原始日志；不要直接外发本地完整日志。
