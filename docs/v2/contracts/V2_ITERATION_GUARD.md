# V2 迭代预算收敛与重复退化守卫（K07）

## 结论与范围

本合同对应 [Agent 内核对标](V2_AGENT_CORE_PARITY.md) 的 K07。V2 此前已有步数硬限（`max_steps`，1..32）与费用硬限（`reserve_call`/`delegation_check_budget`），空回复（K02）与截断/内容过滤已有明确失败结算，但缺**重复退化检测**与**预算内收敛 grace**。本批在共享内核内补上：

- 模型输出重复退化检测与明确退出（`domain/repetition.py`）；
- 非委派 run 步数耗尽时的一次有界 no-tool 收敛 grace。

参考 `xuexi/hermes-agent` 固定提交 `9976f4857b72439d79016354d7b6e1a3ee952e2f` 的 `agent/repetition_guard.py`（保守文本重复检测、stop-path runaway 阈值）与 `agent/iteration_budget.py`（预算内 grace 思想），在 V2 四层内独立实现，未复制第三方源码。无数据库迁移、无第二内核。**不放宽任何硬上限、不重发未知调用。**

## 重复退化检测合同

1. 纯领域 `domain/repetition.py`：
   - `is_repetition_dominated(text)`：保守检测——片段 ≥400 字符，且某 60 字符窗口重复次数覆盖至少一半（`max(5, ceil(n*0.5/60))`），或单一规范化行重复 ≥5 次且覆盖一半。短文本与合法重复（distinct 批处理行）不误判。
   - `is_runaway_repetition(text)`：在 dominated 基础上，若有行结构则要求至少一半行非 distinct（循环重发同一行），区分合法重复但正确的输出。
   - `is_degenerate_reply(text)`：**stop-path 守卫**——仅当 `len ≥ STOP_PATH_MIN_CHARS`（16000）且 runaway 时判退化。已完成回包丢弃的是完整答案，故只在 runaway 规模中止；小型合成/请求重复载荷（如 5000 字符）必须送达。
2. 引擎在每次 `invoke` 返回后、派发任何动作前检查 `is_degenerate_reply(reply.text)`；命中则 `BusinessError("model_repetition_degenerated")`。费用已由 `invoke` 结算保留，**不派发、不重放**。grace 回包同样检查。
3. 检测针对模型**输出文本**，不针对工具调用重复——合法的重复读取（如轮询/压缩测试中的多次相同读取）不被误判。

## 预算内 grace 收敛合同

1. 步数循环耗尽时：**委派 run 保持严格 `step_budget_exceeded`**（保留 K04 调用数合同，不额外消耗调用）；非委派 run 进入一次有界收敛 grace。
2. grace 追加一条 runtime 收敛通知（尾部 user，冻结系统前缀不动），先经 `compact_run` 有界压缩，再做**一次** `tools=()` 的 no-tool 模型调用。
3. 该调用仍经 `reserve_call` 费用闸门：预算耗尽（`budget_exceeded`/`delegation_budget_exceeded`/`database_busy`）→ `step_budget_exceeded`，**不放宽费用硬限**。
4. grace 回包无工具调用且非退化 → `finish` 成功收敛；仍携带工具调用或退化 → `step_budget_exceeded`。grace 恰好一次，不循环、不重发未知。

## 验收

- runaway 重复回包（≥16000 字符循环）使 run 失败 `model_repetition_degenerated`，无动作派发、费用 settled、调用仅一次。
- 小型均匀载荷（5000 字符）与大型 distinct 行不被判退化（stop-path 规模与行 distinct 守卫）。
- 非委派 run `max_steps=1` 耗尽后 grace 收敛成功（主调用 1 + grace 1），最终答案为 grace 文本。
- grace 仍调用工具 → 保持 `step_budget_exceeded`。
- 委派 calls 耗尽仍 `step_budget_exceeded`、子调用数不增（既有 K04 回归保持）。
- 正常短 run 不触发 grace。

## 限制

- 重复检测为保守文本启发式（窗口/行扫描），非参考全集算法（未含周期锚点扩展的全部变体）；只处理已完成回包的 stop-path 规模，截断回包仍由 K02 `model_response_incomplete` 结算。
- grace 仅非委派 run、恰好一次 no-tool 调用；委派 run 无 grace（保留调用数合同）。
- 不做模型/provider fallback（属 K12）；不放宽步数/费用硬限。
- 未跑当前源码全量；未真实模型/设备/72 小时验收。

## 证据

见 [迭代守卫证据](../../evidence/v2-iteration-guard.json)。