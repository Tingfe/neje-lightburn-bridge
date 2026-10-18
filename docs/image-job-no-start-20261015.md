# 图片作业“开始无反应”现场诊断（2026-10-15）

本记录先记下 10 月 15 日恢复工程的离线检查，再补充 10 月 16 日收到的现场实际加工反馈及其对应桥接日志。`Ready` 和离线预览均不能代替加工验收。

## 结论

**已确认一个可复现的工程故障**：重新打开的图片工程在 LightBurn 中变为空白。原 `.lbrn2` 的位图形状仍在 XML 内，但没有内嵌 `Data`，标有 `SourceHash="0"`；即使原始 PNG 路径存在，LightBurn Pro 2.0.05 重开后画布空白，预览为 `0:00`。这足以解释重开后的空作业。用户最初点击“开始”时的窗口/预览状态没有同步捕获，而同一会话稍后曾生成非空离线 GCode，所以**尚不能断言最初的点击无反应也完全由此造成**。桥接日志能证明当时未收到作业，不能证明 LightBurn 为什么没有提交。

已制作可重复打开的恢复副本 `evidence/image-no-start-20261015/recovered-image-original-settings.lbrn`。它保留原位图形状、位置和 C00 图层的 18% 功率、50 mm/s、Stucki 设置，含 35,860 字符的内嵌图片数据。LightBurn 重开后画布有图，预览为 `1:09`；再按“保存”后内嵌数据仍在，预览仍为 `1:09`。10 月 15 日离线检查时尚未执行实物加工；后续现场结果见下节。

## 现场复测（2026-10-16 补记）

用户确认：**昨晚使用本项目 NEJE Bridge，图片实际加工完成，停止功能正常**。这是现场使用反馈，不包含独立的成品尺寸/质量测量。

与之对应的 10 月 15 日 17:53–18:53 桥接会话中，18:13:02 开始的首段作业共有 **4,638 条可执行 GCode**，与此前 LightBurn 离线导出文件从 `M4` 到 `M2` 的可执行行**逐条一致**；其中 `S180` 正功率行 2,208 条。该段每行都有设备真实 `ok`，`M2` 于 18:14:12 收到 `ok`，18:14:13 首次真实状态为 `<Idle|MPos:0.000,0.000,0.000,0.000|...|FS:0,0|...>`。同一会话另有两段较短的正功率作业，也均有 `M2` 确认和随后 `Idle`/`FS:0,0`。这强力支持图片作业通过透明桥接完成；“停止功能正常”仍按用户现场反馈记录，本会话没有发现独立的 `0x18` 急停指令，不能把该反馈写成急停测量通过。

会话在 18:53 关闭时 `way=0`、最终 app 3 且无故障原因，未结算的仅是最后一个空换行（sequence 11231，前一条已收到 `ok`）。现有全会话严格审计器因此因 `pending_lines=1` 停在断言处，**没有完整会话 `passed=true` 结论**；已完成的三段作业逐行应答和终点状态是单独核验的结果。10 月 16 日 09:21 的另一次启动在发送作业前因设备不在 GRBL app 3 结束，不属于昨晚成功会话；再次使用本桥接需先确认设备处于 GRBL 模式。

注意：`pipeline_transmission_stopped.job_submitted=0` 在透明模式下只表示没有关联受控固定作业对象，并非累计发送行数。判断透明作业应看 `pipeline_line_enqueued`、相同 sequence 的 `pipeline_terminal` 和真实状态；不能据这个结束字段断言没有 GCode。

## 传输和应用证据

- 17:32:40–17:43:19 的第 4 会话中，LightBurn 与 `127.0.0.1:4002` 保持连接，919 次 TCP 接收、916 次真实 `Idle` 状态；非空命令仅 `G0`×6、`$I`、`$#`，没有图片作业行或停止复位字节，也没有传输故障。独立审计通过，1854/1854 客户端字节转发，无伪造和重放，结束时 `way=0` 清理通过。
- 当时未关闭的 LightBurn 内存工程能离线生成图像作业：预览 `1:09`，保存 GCode 得 4,646 行、53,234 字节，含 2,208 条正功率 `S180` 行。此 GCode **未发送到设备**。这解释了“保存前有预览，重开后没有”的表面矛盾。
- 用户授权后，先保存工程，再通过活动监视器对异常 LightBurn 执行普通“退出”；桥接仍监听 4002。重新打开 LightBurn 后 TCP/BLE 连接与真实 `Idle` 状态正常。随后再次退出 LightBurn，第 5 会话审计通过：1702/1702 客户端字节转发，`job_submitted=0`、无伪造/重放、`way=0` 且最终 app 3。之后重新启动 LightBurn 只做离线工程恢复；本记录不声称当前会话已结束清理。
- 原 `.lbrn2` 中的原始图片文件确实存在，为 387×142 PNG。用 LightBurn 直接重新导入图片可以显示；给工程形状补上 `Data=""` 能暂时让 `.lbrn2` 重开时显示，但 LightBurn 原生保存会移除这个空属性并写回 `SourceHash="0"`，下次重开又变空白。作为本机 LightBurn Pro 2.0.05 的实测绕行，使用“文件 → 另存为 → LightBurn 旧工程 (*.lbrn)”生成内嵌图片副本，经重开和再次保存验证稳定。LightBurn [官方文件菜单文档](https://docs.lightburnsoftware.com/latest/Reference/UI/FileMenu/)列出这两种工程格式；这里的兼容性结论限于上述本机实验，尚未确定上游根因或其他版本表现。

## 进度百分比

LightBurn [官方 Laser Window 文档](https://docs.lightburnsoftware.com/1.7/Reference/LaserWindow/)说明 `Ready` 表示连接就绪，旁边的百分比是**命令流送比例**，不是机器已走完路径的比例，预估时间也可能与实际加工不同。因此无需也不能为了表面进度去伪造 `ok`。后续实物验证应分别记录首次作业字节、最后命令确认、真实 `Idle`/零功率与现场观察。

## 后续验证

1. 若其他 `.lbrn2` 位图工程也重开空白，优先在 LightBurn 内导入原图并另存为 `.lbrn`，重开后看预览；必要时向 LightBurn 报告复现工程。勿直接把 `0:00` 预览当作桥接故障。
2. 后续若要评估加工质量或 USB/BLE 成品等效，需要同材料、同位置和同参数的独立测量；这次“正常加工”反馈不能替代该项。
3. 可单独调查会话末尾空换行未确认时的严格审计器行为，以及 10 月 16 日设备 GRBL 模式不符的启动问题；不要把两者归因于昨晚图片作业失败。

## 项目内证据

- `evidence/image-no-start-20261015/current-image-before-restart.lbrn2`：原问题工程副本。
- `evidence/image-no-start-20261015/top-unistar_web.png`：本地原图副本。
- `evidence/image-no-start-20261015/current-image-offline.gc`：保存前 LightBurn 离线 GCode，未执行。
- `evidence/image-no-start-20261015/recovered-image-original-settings.lbrn`：可重开的最终恢复工程。
- `evidence/image-no-start-20261015/recovery-verification.json`：文件哈希、位图数据、图层设置和两次会话审计摘要。
- `evidence/image-no-start-20261015/session4-no-job-raw.jsonl`、`session4-audit.json`：原问题会话及审计。
- `evidence/image-no-start-20261015/session5-restart-no-job-raw.jsonl`、`session5-audit.json`：重启与正常退出会话及审计。
- `evidence/image-no-start-20261015/session6-image-success-raw.jsonl.gz`：昨晚成功复测会话的完整压缩原始捕获。
- `evidence/image-no-start-20261015/session6-image-success-summary.json`：三段作业逐行应答、与离线 GCode 一致性、真实 `Idle` 和会话末尾边界的核验摘要及 SHA-256。

原始捕获和试验工程含本机路径，项目的 `evidence/` 按现有规则不纳入 Git；交接时需连同工程目录单独保留。
