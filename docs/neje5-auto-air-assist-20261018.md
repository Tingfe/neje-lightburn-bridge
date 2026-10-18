# NEJE 5 Plus 自动气辅方案（2026-10-18）

## 已确认

- NEJE 5 系列控制板官方列出 `M8`、`M7` 扩展，支持 12–24 V 主板输入；这证明系列能力，不能单凭页面推定本机接口针脚和额定输出电流。[NEJE 5 Series Laser Controller Board](https://neje.shop/products/neje-5-series-laser-motion-control-system-kit)
- NEJE 5 Plus 官方清单包含 12 V / 4 A 适配器、A40640 模块及高压空气辅助套件。随机器提供气嘴和气路，不等于已装自动控制阀。[NEJE 5 Plus 安装说明](https://neje.wiki/doku.php?id=cn:nejelaser_cn_howto_install_n5_plus_cn)
- NEJE AF3 官方 12 V 自动气阀套件标注适配 A40640、带 M8/M7 接口的 NEJE 主板；套件为 DC 12 V、2 针 PH2.0 控制线、8 mm 进气口、4 mm 气管，不适用 24 V 系统。[NEJE AF3 套件](https://neje.shop/products/neje-af3-24v-auto-control-electromagnetic-valve-air-assist-kit-for-neje-max-4-e80-and-neje-3-max-v2-e80-m8-control)
- LightBurn 设备设置中的 Air Assist 可选 `M7` 或 `M8`；「切割/图层」里的 `Air` 开关按图层启用气辅。[LightBurn 设备设置](https://docs.lightburnsoftware.com/latest/Reference/DeviceSettings/BasicSettings/) · [LightBurn 图层窗口](https://docs.lightburnsoftware.com/latest/Reference/CutsLayersWindow/)
- NEJE 的软件说明明确 `M8` 打开气辅、`M9` 关闭气辅（也会关闭 M7）；LightBurn 的 `M8` 需在目标图层启用。[NEJE 软件说明](https://neje.wiki/doku.php?id=neje_win)
- 本机当前 `LaserLink NEJE5 (蓝牙桥接)` 为原生 GRBL/TCP 设备，`127.0.0.1:4002`，`Settings.AirAssistM7=False`，即选用 `M8`。本项目透明桥接不会按只读白名单过滤作业指令；此项为本机配置/代码静态核查，**尚未证明实体 M8 插口动作或气阀开闭**。

## 推荐接法

优先保持现有气泵独立供电，只把气路交给 12 V、与 A40640 匹配的 M8 电磁阀：

```text
气泵（原有独立电源） → 合适的气管/转接头 → M8 电磁阀 → 4 mm 气管 → 激光模块气嘴
NEJE 5 Plus 主板标有 M8 的接口 ── 原厂匹配线束 ── M8 电磁阀
```

这种接法使 LightBurn 按图层开闭气流；气泵本体可继续运行。若要求气泵也自动启停，应另用 **M8 兼容的继电器/开关模块**控制气泵自己的电源，并核实模块触发规格及气泵的额定/启动电流。NEJE 在 Max4 V2 上公开过 M8 接继电器控制气泵的方案，但那不是 5 Plus 的针脚图。[NEJE Max4 V2 说明](https://neje.wiki/doku.php?id=neje_max4_v2)

不要把气泵直接接主板 M8 口：当前未找到本机板号对应的 M8 供电能力与针脚定义。对于交流市电气泵，使用合格的封闭式开关组件并由具备资格的人完成接线；本文不提供市电裸线接法。NEJE 还提示外接 M8/M7 器件电压必须匹配主板供电电压。[NEJE 主板说明](https://neje.wiki/doku.php?id=motherboard_neje_max)

## 软件设置与验收

1. 断电后核对主板实际板号、`M8` 丝印、接口形式和电源铭牌，再采购/连接 12 V 阀或相应额定模块。不能仅凭 5 系列产品页判断针脚极性。确认空气经过合适的过滤/减压，气管和阀耐压符合气源。
2. LightBurn 选择 `LaserLink NEJE5 (蓝牙桥接)`，在「设备设置 → Air Assist」选择 `M8`；本机目前已是这一设置。在需要气辅的切割图层打开 `Air`，雕刻图层按需要单独选择。
3. 装好之后，先在无加工、无人触碰且激光不出光的条件下，用控制台单独发送 `M8`、`M9`，观察真实阀/气流是否分别开启、关闭。然后导出一个小型作业文件，核对其中有开启 `M8` 与结束关闭 `M9`；最后现场看护小样实际运行，确认结束及停止后的气流状态。
4. 蓝牙断开、电脑睡眠或软件停止不能当作气路断电保证；若气泵独立供电，需要保留可立即切断气泵和激光的实体电源开关。

## 尚未验证

- 本机 5 Plus 主板的 `M8` 实际位置、针脚极性、输出电压/电流上限。
- 用户现有气泵规格、气管口径和压力，是否已装阀或继电器。
- 本机 `M8/M9` 通过 USB 与蓝牙桥接时的实体阀动作、停止/断连后的实际状态。

因此当前结论是**方案可行、软件配置已有基础，但硬件自动气辅尚未完成实机验收**。
