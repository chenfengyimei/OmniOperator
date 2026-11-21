# V2 run 内工具上下文压缩（K06）

## 结论与范围

本合同对应 [Agent 内核对标](V2_AGENT_CORE_PARITY.md) 的 K06。V2 此前已有持久化**整回合**压缩（`ContextService.prepare` 在 run 起点按 `run_id` 分组摘要旧回合），但 run 内多轮工具转录只在内存 `messages` 增长：持久化 `s.messages` 仅由 submit 写 user、finish 写最终 assistant，中间 assistant tool_calls/tool 结果不落消息行。长 run 会撞上 `ModelCalls.invoke` 的 `CONTEXT_CHARS`（48000）字符硬限或 token 预算硬限而失败，无法继续。

本批在共享内核内加入 **run 内有界可续压缩**：`ContextService.compact_run` 在每轮模型调用前检查 live 转录，超阈值时把**旧的完整工具轮**摘要为一条有界 summary，保留冻结系统前缀、初始 head 与最近若干完整工具轮，使长 run 得以继续。参考 `xuexi/hermes-agent` 固定提交 `9976f4857b72439d79016354d7b6e1a3ee952e2f` 的 `agent/context_engine.py`（`prune_tool_results_only`/`compress` 的有界、保工具对思想），在 V2 四层内独立实现，未复制第三方源码。无数据库迁移、无第二内核/状态库。

## 压缩合同

1. 触发：`len(serialized_request(messages, tools)) > RUN_COMPACT_TRIGGER_CHARS`（`CONTEXT_CHARS // 2 = 24000`），远低于硬限，给继续留余量。低于阈值直接 no-op，短 run 行为不变。
2. 工具轮边界（纯领域 `domain/context.py`）：`tool_round_starts` 取携带 `tool_calls` 的 assistant 消息索引，每个轮拥有其 assistant 消息与全部后续 tool 结果。`run_compaction_window` 返回 `(head_end, cut)`：`head_end` 为首个工具轮起点，`cut` 为最近 `RUN_COMPACT_KEEP_ROUNDS`（2）轮的首轮起点；轮数不足 keep 时返回 None（不压缩）。
3. 不拆工具对：压缩区间 `messages[head_end:cut]` 整段替换为一条 user summary；`messages[cut:]`（最近 2 轮及其 tool 结果、尾部 runtime notice）**原样保留**。被压缩的旧轮整体摘要掉，绝不留下半个工具对。
4. 冻结前缀与 head：`messages[0]` 系统消息与 `head_end` 以下的初始 history/user head 从不触碰，满足 K01 `TurnPrompt` 冻结不变量；压缩后下一轮 `prompt.update` 仍校验前缀字节不变。
5. 状态/任务清单保留：尾部 runtime/todo notice 位于 `cut` 之后被保留，且每轮 `prompt.update` 重新注入当前 notice；压缩不改 todo 服务或 run 状态。
6. 有界摘要源：`bounded_tool_summary_sources` 以 `SUMMARY_INPUT_CHARS`（32000）预算截断旧轮内容并赋合成 id，保证摘要请求自身有界；summary 文本截断到 `SUMMARY_CHARS`（4000）。

## 费用、授权与删除合同

1. 摘要是一次计费的 `purpose="summary"` 模型调用：`reserve_call` 预留 → `ModelCalls.invoke(reserved_id=...)`（用 `summary_tokens` 输出、跳过普通回包工具校验）→ `settle_call` 结算。费用进同一账本，可经 `query("calls")` 追溯（回归断言 settled 与实际 cost）。
2. 摘要走与任何模型调用相同的 `assert_context`/`assert_model_authorized` 守卫；`context_invalidated`、`model_configuration_revoked`、`token_counter_unavailable` 与取消向上抛出，保持来源删除屏障。
3. 摘要回包若携带工具调用则拒绝（`summary_tools_forbidden` 语义），不派发任何动作、不替换转录；摘要失败或预算不足时结算该调用并**保持转录不变**，不重放、不暗中付费重试。
4. 取消/中断按原 `_execute`/run 清理链处理；压缩不释放仍执行的任务。

## 验收

- 长 run（多轮大工具结果）触发压缩后继续成功；未压缩会撞 token 预算硬限。
- 压缩后下一轮模型输入：系统前缀逐字节稳定、含 summary、旧轮内容消失、最近轮内容保留、工具对完整（assistant tool_calls id 与 tool 结果 id 同序匹配）。
- 摘要调用计费 settled、purpose=summary；短 run 不触发、无摘要调用。
- 多轮长 run 反复压缩，始终只保留最近 2 轮。
- 摘要携带工具调用被拒绝、不产生额外动作；仅 2 个超大轮时不压缩（不拆保留轮）。

## 限制

- 仅压缩 run 内内存工具转录；持久化整回合压缩仍由 `prepare` 负责，二者衔接但本批不改 `prepare`。
- 保留最近 2 轮；单个超大保留轮仍可能超硬限（固有上限，不放宽）。
- 摘要输入按预算截断，极旧超大轮的内容会有损（压缩固有取舍）。
- 无新增压缩管理界面；未跑当前源码全量；未真实模型/设备/72 小时验收。

## 证据

见 [run 内压缩证据](../../evidence/v2-run-compaction.json)。

## 缺陷修复（2026-11-21，FIX.run_compaction_settlement）

K06 首版 `compact_run` 在 `ModelCalls.invoke(reserved_id=...)` 之后又写了一次 `settle_call`。`ModelCalls._invoke_once` 是 reserved call 的唯一结算点（正常回包、known-not-sent、已派发 unknown 三条路径都会结算），第二次结算抛 `already_settled`；因此摘要回包带 `tool_calls`（引擎只用于拒绝）或摘要失败时，长 run 会在业务工具已成功后整轮失败。修复后 `compact_run` 不再自行结算，`ModelCalls` 保持唯一结算点；摘要带工具调用仍只返回 False、不动转录。回归见 [settlement 证据](../../evidence/v2-run-compaction-settlement.json)，在旧代码上以 `already_settled` 失败、修复后通过。