# 管理会话缺陷修复结果（E92）

2026-10-13。修复 E91 期间实测到的三个缺陷。**本轮不改变任何验收结论，总分保持 81/100**；`tools/` 与 `tests/` 有改动，已配测试与历史捕获等价性证明。

## 缺陷 1：会话走到墙钟上限必然要求显式恢复（复现 2 次）

**原现象**：初始化档 300 秒、作业档 900 秒两次到期，均以 `pipeline_session_cancelled`＋`pipeline_stopped`（`CancelledError`）收尾；设备其实已 `cleanup_verified`（`way=0`/`app3`）、无作业无运动，但管理状态变 `requires_recovery`，下次 `start` 被拒。**根因**：到期与真实故障在捕获里无法区分，[审计器](../tools/check_pipeline_tcp_capture.py)第 13 行把 `pipeline_stopped` 列为禁止事件，故 `assess()` 抛 `AssertionError`。

**修法（不改审计器）**：

1. [tools/pipeline_tcp_bridge.py](../tools/pipeline_tcp_bridge.py) `run()`：墙钟到期时**先写出显式事件** `pipeline_wall_limit_reached`（带 `seconds`、`client_connected`），再走原有取消/收尾路径。到期与故障从此在捕获里可区分。
2. [tools/lightburn_session.py](../tools/lightburn_session.py) 新增 `assess_expired()` 与终态 `expired`：
   - 仅当捕获满足**全部**条件才走非阻塞终态：无故障事件（`pipeline_session_error`/`pipeline_task_error`/`receive_fault`/`stream_write_uncertain`/`error`）、`pipeline_server_stopped` 且 `cleanup_unknown=False` 且无 `failure_reason`、且「无作业或作业已报告完成」；
   - 否则**保持严格的 `requires_recovery`**（未完成作业、有故障、清理未核验、捕获被截断四种情形均有测试覆盖）。
   - `expired` **不是成功**：`audit.passed=False`、`independent_audit_run=False`、`reason=wall_limit_expiry_not_independently_audited`；`start()` 接受 `expired` 作为可再启动的前置状态；`status_message()` 明确「未做完整独立审计，不声明作业成功」。
3. **明确不放宽**审计器第 13 行 —— 真实故障仍然必须显式恢复。

## 缺陷 2：LightBurn 自动重连与单客户端设计冲突

**原现象**：客户端断开后 LightBurn 在 0.3 秒内重连 62 次全部被拒，界面长期停在 Disconnected，用户表现为「连不上」且无法自愈。

**修法（可观测性，不改安全属性）**：

- 拒绝日志新增 `new_session_required=True`，使原始捕获直接说明「需要新会话」而非让读者从 reason 推断；
- [管理入口说明](lightburn-managed-session-guide.md)已列出排查顺序与该语义；
- **有意不采用「断线后允许有限次重连」**：单客户端、一次性会话是本项目的安全属性（防止断开后出现第二个控制端），改成宽限期会实质削弱该属性。因此本缺陷的解决方式是把「必须重启桥接」这件事变得不可误解，而不是放开重连。

## 缺陷 3：`all_received_tcp_queries_forwarded` 被硬编码

**原现象**：[tools/check_controlled_capture.py](../tools/check_controlled_capture.py) 在「作业完成后 EOF 丢弃」路径上把该字段写死为 `False`。**更正说明**：该值在所有**可达**情形下本来就是正确的（调用方有 `if staged_bytes or queued_controls` 守卫，保证至少存在一项丢弃），所以它不是「错值」，而是**脆弱**：正确性依赖外部守卫，字段本身不表达来源。

**修法**：

- 改为从捕获推导：`all_received_tcp_queries_forwarded = not queued_queries`；
- 新增 `discard_cause='post_completion_client_eof'`，让读者一眼看出这些丢弃发生在**作业完成后的客户端 EOF**，属于有界收尾，不是审计失败；
- 保留原有诚实口径：有丢弃时不宣称「全部查询均已转发」。

## 验证

| 项目 | 结果 |
| --- | --- |
| 定向测试 | `test_completed_eof_discard_audit` 7、`test_lightburn_session` 17、`test_pipeline_tcp_bridge` 10，全过 |
| 新增测试 | 4 项：到期非阻塞终态、到期遇真实问题仍严格恢复、`expired` 可再启动且不声明成功、转发标志与丢弃计数一致（另在单连接用例中断言重连拒绝记录 `new_session_required`） |
| **历史捕获等价性** | `ble-start.jsonl` 差异 **1 项**（仅新增 `discard_cause`，`passed` 不变）；`ble-run.jsonl` 与 `usb-rerun.jsonl` **零差异** |
| 全量回归 | **307 项 OK** |

## 边界

本轮无设备连接、无运动、无出光、无设置写入。未改动审计器第 13 行的严格性，未放宽单客户端语义，未把到期当作成功。
