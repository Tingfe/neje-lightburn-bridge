# NEJE Bridge 1.2.0：简化入口、退出失效与自动打包

2026-10-14，build3。已完成本机 app/ZIP，并合入图片诊断后的 Stop/状态计数兼容修复。设备本轮不在旁边，未新增真实 BLE 连接或动作；源码、软件包、GUI 及真实 LightBurn 离线预览分别记录。

## 四项使用体验

| 用户要求 | 当前实现及核验 |
| --- | --- |
| 退出桥接程序即失效 | GUI 默认生命周期到退出；所属桥接携带 owner PID，父进程消失后关闭监听、等待 BLE 清理，不遗留自动续跑进程。正常退出和真实 OS 父进程退出的假 BLE/TCP 清理测试通过。CLI 默认7小时保持兼容 |
| GitHub 自动打包 | 新 macOS Actions workflow 已写好，main相关路径/版本标签/手动触发，离线回归→固定依赖→app/ZIP→包内诊断/签名/ZIP完整性→上传ZIP/散列/元数据30天。本轮未提交推送，未触发云端构建 |
| 一键启动、低门槛 | 主界面保留启动/关闭、创建设备/打开LightBurn、软件停止；小时数取消，设备选项与日志收起。唯一NEJE自动选用并记住名称/地址，多台时显式选择；现场条件确认只在本次应用运行保留。扫描广告6秒，异步初始化及扫描总截止14秒；本机出现过初始化等待偏长，故新增取消超时测试 |
| 创建可选择的LightBurn设备 | 直接建立原生GRBL/TCP DeviceList条目，不依赖旧模板。LightBurn退出后备份、写入并读回；运行状态未知则拒绝改写，不自动关闭未保存工程。已有localhost:4002条目复用并打开。临时配置创建/幂等/损坏保护已测试，本机真实LightBurn已选择现有原生条目；全新机器首次创建后UI验收仍待外部测试 |

新增记忆文件只保存设备名称及地址，不持久化现场授权。关闭或异常退出桥接不代表物理设备停止，已缓冲动作仍可能继续。LightBurn Stop 的健康连接保留与图像修正详见[诊断结果](lightburn-image-and-stop-result.md)。

## 当前交付

- 应用：`dist/NEJE-Bridge-1.2.0/NEJE Bridge.app`。
- ZIP：`dist/NEJE-Bridge-1.2.0/NEJE-Bridge-1.2.0-macOS-arm64.zip`，12817663字节。
- SHA-256：`613628ecc7596fb8e1ac52cb3ecc102af79ede9f38b06ca09b9bff11a4063a49`，同目录 `SHA256SUMS.txt` 与 `build-metadata.json` 可核对。
- arm64，当前本机最低 macOS27，已打开本机27.2图形窗口。包内Python/Tcl/Tk/BLE自包含、内部桥接参数分派、深层严格签名验证和ZIP完整性通过。
- ad-hoc签名，**没有Developer ID或Apple公证**；未承诺Intel及其他macOS版本可用。云端构建使用其实际Python/Tk系统目标，最低版本必须以每次构建元数据为准。

460项完整回归通过（78.520秒），含连续Stop后即时初始化、旧半行/队列清理、真实报告合并计数、无报告截止、父进程退出监听/路由清理、配置保护及一键入口。完整输出 `full-tests-final.txt`；本机打包输出 `build-final.txt`。真实GUI启动、打开已有LightBurn、正常退出已观察；早期本轮包扫描返回未发现NEJE，最终包在本机蓝牙初始化等待14秒后明确提示超时、清空待启动状态并展开重试入口。不能把扫描超时解释为设备成功连接，设备在场时需复测发现及连接。当前图片只保存/预览/导出，没有按Start。

新 macOS workflow 使用 `macos-15` arm64 标准runner；GitHub 官方当前表明该标签为M1 arm64，[runner文档](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)可核对。锁定构建依赖，依赖源码版本单一入口 `tools/app_version.py`。Actions目前在线只有旧tests记录，本轮新构建流程尚在本地；因此“能够自动打包”目前是准备好的实现与本机同脚本验证，尚不是已完成云端验收。后续推送后需检查Actions成功及下载ZIP复核。

## 证据与进度

当前证据 `evidence/gui-ease-20261014/`，保存本轮源码副本/散列、GUI截图/状态、包元数据、workflow解析、GitHub只读状态、用户图像与模拟通信。旧 `gui-package-20261014` 和 `transparent-hardening-20261014` 的冻结文件保持原样；旧1.1包保留。

整体完成度仍约 **91%**。本轮完善日常入口与已报告的软件缺陷，不以模拟和打包提高硬件分数。主要剩余：当前1.2真实图片与Stop复测、当前透明BLE长期运行、同材料同对焦加工对照、云端打包和其他机器发行验证。[测试指南](gui-mac-test-guide.md)已更新。
