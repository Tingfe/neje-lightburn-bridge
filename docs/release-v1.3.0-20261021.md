# v1.3.0 发布核验

2026-10-21。GitHub [v1.3.0 Release](https://github.com/Tingfe/neje-lightburn-bridge/releases/tag/v1.3.0) 发布中英双语 macOS Apple Silicon 图形包。发布标签指向提交 `99c440733a8c36ac7c9ffd4a848b500c2cfef5ad`；该提交的 [macOS Actions 运行](https://github.com/Tingfe/neje-lightburn-bridge/actions/runs/39245829773) 成功。发布使用同一次运行的 `NEJE-Bridge-macOS-arm64` 产物，而不是本机另一次构建的 ZIP。

发布资产：`NEJE-Bridge-1.3.0-macOS-arm64.zip`，12,637,476 字节，SHA-256 `8af6c016ea118be5f6f0897897cbdd81eed3a30d4c4ee08e36818d44dcbcd73a`；另附 `SHA256SUMS.txt`。下载产物后，实际计算的 SHA-256 与构建元数据和校验文件一致；ZIP 解压完整，内含应用的版本号为 1.3.0，签名完整性核验通过，中文主窗口可打开。构建元数据标记 `hardware_tested=false`、`developer_id_signed=false`、`apple_notarized=false`。Intel 包未构建。

本机使用项目锁定的 Python 3.12 运行全套 474 项离线测试通过；默认 Python 3.15 缺少 Tk，不能用于本项目 GUI 全套回归。用户在本轮确认“1.3.0 是我验证过的”，作为用户实际验证反馈记录。具体测试场景、英文界面视觉切换以及这份 GitHub 云端 ZIP 的 BLE/LightBurn 设备作业没有逐项记录，因此不把版本确认扩展成这些项目的独立验收。既有图片和小房子实切证据另见[当前状态](overall-progress.md)。

固件兼容边界仍以[兼容性说明](firmware-compatibility.md)为准：本机三项 2.3.2 可用，三项 2.4.0 的当前 BLE GRBL 通道失答；其他设备与组合未验证。本次发布没有改变 BLE 协议或固件适配逻辑。当前整体目标估算仍为 **96/100**。
