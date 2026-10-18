# 文档导航

第一次使用先看[项目首页](../README.md)。下表按用途选文档；带日期、轮次或版本号的页面记录**当时的实验**，其中的“当前”“待做”和百分比不代表今天的项目状态。

| 用途 | 从这里开始 |
| --- | --- |
| 日常操作与排障 | [使用指南](daily-use-guide.md) |
| 当前进度、已验证范围、剩余问题 | [当前状态](overall-progress.md) |
| 桥接结构和失联语义 | [工作原理](architecture.md) |
| LightBurn 设备设置 | [透明桥接接入记录](transparent-lightburn-setup.md) |
| 开发和复现实验 | [协议](protocol.md) → [可靠传输](grbl-reliable.md) → [实验索引](experiments.md) |

## 关键证据

| 问题 | 记录 |
| --- | --- |
| 图片加工、无法开始及恢复 | [图片作业诊断](image-job-no-start-20261015.md) |
| 小房子约 58 分钟实切 | [长时实切](house-cut-long-run-20261018.md) |
| 停止、失联与缓冲动作 | [运动停止](lightburn-motion-stop-hardware-result.md) · [作业断连](job-disconnect-result.md) |
| 图形应用和双语包 | [本机打包](gui-mac-package-result.md) · [双语与云端构建](bilingual-gui-20261018.md) |
| 气辅扩展 | [自动气辅方案](neje5-auto-air-assist-20261018.md)；尚未做实体阀验收 |

## 历史与其他工作

- [验收过程](acceptance.md)、[USB 等效目标演进](usb-parity-goal.md)、[完整历史台账](overall-progress-history.md)、[早期项目交接](project-history.md)：保留原轮次、失败和当时判断，用于追溯；以[当前状态](overall-progress.md)为准。
- `*-plan.md` 是当轮实验方案，`*-result.md` 是该轮结果；不能把离线计划当作实机完成。所有其他结果页可从[实验索引](experiments.md)按轮次查找。
- [受审文件管理入口](lightburn-managed-session-guide.md)、[早期 USB 等效规划](progress-to-usb-parity.md)及[固定作业桥接](reviewed-job-bridge.md)描述旧实验路径，已标明历史口径，不作为透明图形版的操作说明。
- 材料试切见[纸张](a4-paper-cutting-20261016.md)与[胶合板](4mm-plywood-cut-diagnosis-20261018.md)。这些是特定材料和现场条件下的记录，不是通用切割参数。

原始设备捕获主要保存在 `evidence/` 和本机 `~/Library/Logs/NEJE/`；`evidence/` 被 Git 忽略，公开仓库中的结论不能代替原始日志的独立复核。
