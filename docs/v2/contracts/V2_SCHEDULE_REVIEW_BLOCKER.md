# V2 调度阻塞与后台评审辅助调用（K13 设计合同，先合同后接线）

状态：**设计合同（design_contract_recorded，纯文档，无源码/测试/迁移改动，2026-11-22）**。K13 cron 四批已交付（`1d52cca1`/`a3436361`/`4acc46c9`/`42cf2e98`）；本册记录交付时已发现、`42cf2e98` 明确标注「属安全敏感独立批（需合同）」的残留交互，供所有者决定后再接线。**本批不实现、不改生产守卫。**

## 勘定事实（实读源码 + 现有回归，2026-11-22，基线 `0793f254`）

1. **K08 回合后评审以 `summary` 辅助调用记账，但挂在被评审 run 名下**：`application/background_review.py:147-156` 在每个评审循环里 `reserve_call(agent_id, run_id, …, purpose="summary")`，随后 `invoke(agent_id, run_id, …, reserved_id=call_id)`。`model_calls.purpose` CHECK 只允许 `turn`/`summary`（`schema.py:986-991`），没有第三种「辅助」身份。
2. **调度准入把该 run 名下所有 reserved/unknown 模型调用都算作未解决工作**：`adapters/persistence/lease_repository.py:120-155` 的 `schedule_blocker` 规格为 `model_calls IN ('reserved','unknown')`、`actions IN ('executing','unknown')`、`outbox IN ('unknown')`，`model_calls` 条件**没有 purpose 过滤**；只有 `schedule_reviews` 中同 `occurrence_id`/同 id/同 state/同 `review_epoch` 的显式确认才排除（`:137-155`）。
3. **触发链会因该 blocker 终态失败**：`assert_run_lease` 在 run 有 occurrence 且 blocker 非空时抛 `previous_outcome_unknown`（`:99-105`）；`schedule_repository.schedule_materialize` 在 blocker `reason=="unknown"` 时把日程置 `error_code="previous_outcome_unknown"` 并**不物化下一次 occurrence、不推进 `next_at`**（`:477-483`），于是每个 tick 重试都再次失败，直到评审结束或所有者手动 review。
4. **所有者可手动解除但不合理**：`POST /agents/{agent_id}/schedules/{schedule_id}/review`（`adapters/http.py:1199-1202`）→ `schedule_repository.schedule_review`（`:580-605`）按 blocker `items` 逐条写 `schedule_reviews`，可确认一个**仍 in-flight 的 summary reserved 调用**；对每次定时运行都要人工介入，且把辅助调用显示成「未知结果」误导所有者。
5. **同因还包括 K06 run 内压缩**：`application/context.py:151/289` 的 `compact_run`/摘要也以 `purpose="summary"`、同 run 记账；崩溃或取消后若遗留 reserved/unknown summary，同样会永久阻塞该日程的后续触发（直到 review）。
6. **现有端到端回归以显式等待绕过**：`test_v2_cronjob_e2e.py:61-78` 的 `wait_review_settled` 文档串明说「schedule admission guard counts an in-flight post-turn review's reserved model call as unresolved work」，测试在触发下一 occurrence 前轮询 `background_review_batches` 离开 queued/running；`42cf2e98` 记录修复前合跑 2/2（77.05/74.64 秒）与单跑 3/3（34.15/31.70/32.38 秒）均在第二次触发失败，属真实失败而非 flaky，非负载所致。
7. **判定**：守卫目标（未复核的**外部结果**：turn 调用、动作、投递）正确且必须保留；把**辅助 `summary` 调用**（K06 压缩/K08 评审，内部计费、无外部副作用、通常瞬时结算）也当作外部结果，是范围错误。影响为短周期/背靠背日程在评审窗口内丢触发，或评审崩溃后日程长期停摆；不会重放任何已派发外部动作。

## 决策选项（需所有者选定）

- **A（推荐）：blocker 只统计 `purpose="turn"` 的模型调用**。`model_calls` 条件加 `purpose == "turn"`，`summary`（K06/K08 辅助）无论 reserved 还是 unknown 都不再阻塞下一定时触发；turn 调用、`actions`（executing/unknown）、`outbox`（unknown/未见回执）与 `schedule_reviews` 语义全部不变。理由：辅助调用无外部副作用，预留在日预算内已受 `reserve_call` 闸门约束，unknown 成本仍在 `model_calls`/usage/审计中可见，不因不阻塞而消失；守卫的语义是「未复核的外部结果」，不是「未结算的内部成本」。
- **B：保留阻塞，但 blocker 对「仅含 summary 项且 run 已终态」自动确认**（materialize 时静默写 `schedule_reviews`）。实现更绕、审计上等于自动 review，放弃显式复核语义，不推荐。
- **C：调度器等评审批次终态后再物化下一次**（触发链串行化）。会让短周期日程被评审时长牵制，且把 K08 从「回合后后台」变成调度关键路径，不推荐。
- **D：维持现状 + 文档提示所有者手动 review**。当前行为，短周期日程与评审崩溃场景仍会停摆，不满足 cron 可用性。

## 推荐方案 A 的实现要点（获批准后独立批次）

1. `lease_repository.schedule_blocker` 的 `model_calls` 条件增加 `s.model_calls.c.purpose == "turn"`（`specs` 对 `model_calls` 携带该附加条件，`conditions()` 通用形状不变；`actions`/`outbox` 零改动）。
2. `unknown` 列表/fingerprint 只包含仍会阻塞的项；`schedule_reviews` 的唯一键/check 不变（summary 调用不再产生 blocker，也无需 review）。
3. 不改 `reserve_call`/`settle_call`/预算/usage/审计；不改 K06/K08 记账；**无新表、无迁移、无路由、无 worker 协议、无共享端口签名改动**。
4. 观测：`bilibili` 式 `health()` 不涉及；建议在日程 blocker/owner occurrences 投影里继续可见「summary 调用仍在途」的计数（可选加性字段，需单独批准），避免完全不可见。

## 不变量（实现必须保持）

- `purpose="turn"` 的 reserved/unknown 模型调用**仍然阻塞**下一次触发（预算/计费闸门与结果复核不被绕过）。
- `actions` executing/unknown、`outbox` unknown/未见回执**仍然阻塞**；`schedule_reviews` 的 `review_epoch`/fingerprint/CAS 语义不变。
- 已批准投递/已派发动作**绝不因本改动重发**；unknown 结算与恢复链不变。
- 不改 run 状态机、租约、事件保留；不新增第二内核/状态库。
- 跨 agent/跨日程隔离不变。

## 测试计划（获批准后）

- 新增领域/仓储回归（冻结时钟 + 真实隔离 SQLite，无需模型）：
  1. 上一 occurrence run 终态 + 其 run 名下 **reserved summary**（模拟在途 K08/K06）→ `schedule_blocker` 为空，下一次触发成功；
  2. 同场景 **reserved turn** → 仍返回 `reason="unknown"`，触发仍 `previous_outcome_unknown`；
  3. **unknown summary**（崩溃遗留）→ 不阻塞且成本仍在 `model_calls`/usage 可见；
  4. `actions` unknown / `outbox` unknown 仍阻塞（既有语义）；
  5. `purpose` 过滤不影响 `schedule_reviews` 对 turn/action/outbox 的确认。
- 调用方：`test_v2_cronjob_e2e.py`（可移除/保留 `wait_review_settled` 均可，保留不冲突）、`test_v2_cronjob_tool.py`、`test_v2_schedule_context.py`、`test_v2_schedule_bindings.py`、`test_v2_background_review.py`、`test_v2_maintenance.py`、租约/执行/委派/事件保留相关守卫。
- 回退验证：摘掉 purpose 过滤 → 新增用例（1/3）必须全红，证明用例锁住行为。
- 验证级别：离线专项 + 调用方 + strict/ruff/索引；真实模型/72 小时不适用（评审在真实运行中由模型驱动，但行为等价性由离线冻结时钟覆盖）。

## 边界

- 本册**不实现**任何改动；`schedule_blocker`/`assert_run_lease`/`schedule_materialize`/`schedule_review` 生产行为保持现状（`42cf2e98` 纪律不变）。
- 不做：K08 评审调度策略/预算、K06 压缩触发策略、日程重试退避、自动 review、评审失败重试。
- 需所有者批准 A（或另选 B/C）后，按独立批次实现；共享接口/迁移/公共 CI 由 A 协调（本方案预计不触迁移）。

## 关联

- K13 审计：[V2_TOOL_CHANNEL_AUDIT](V2_TOOL_CHANNEL_AUDIT.md)（cron 组）。
- 证据：[v2-delivered-verification.json](../../evidence/v2-delivered-verification.json)。
- 决策登记：[V2_REMAINING_DECISIONS](V2_REMAINING_DECISIONS.md) D8。
