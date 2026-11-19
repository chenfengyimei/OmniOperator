# V2 停止前验核（K11）

## 结论与范围

K11 补上通用 **verify-on-stop gate**：当模型在本回合应用了写动作、却试图直接以最终文本结束时，若这些写动作的**结构化回执没有验证证据**，回合循环不立即接受停止，而是把候选答案作为 interim 行追加、再追加一条 user 角色 nudges，让模型在**有界次数**内补做验证；次数用尽后以候选答案收尾。

**唯一成功证据是持久化动作回执的 `verification` 字段**（既有 `ActionResult.verification`，落库于 `actions.result`）。模型的最终答案文本、任务清单（todo）勾选状态**一律不作**成功证据——gate 只读结构化动作回执，不读模型文本、不读清单。

参考 `xuexi/hermes-agent/agent/turn_stop_gates.py` 与 `agent/verification_stop.py`（固定提交 `9976f4857b72439d79016354d7b6e1a3ee952e2f`）的「文本停止门」设计（候选答案保留为预算耗尽 fallback、清 final_response、user 角色 nudge 追加在 assistant 答案行之后以保持角色交替、有界 attempts），在 V2 四层内独立适配。参考是编码场景、读工作区验证账本（passed/unverified）且默认 OFF（opt-in）；V2 无工作区验证账本，改用**逐动作回执的 verification**作为证据源，同样默认 OFF。未复制第三方源码、未拉取远程、未用真实账号/模型。

## 证据源与判定

- 证据查询 `run_applied_writes(agent_id, run_id)`：仅取本 run、本 Agent、`writes=1` 且 `effect='applied'` 的动作，按 created_at 排序、上限 200，返回 `{tool, verification}`（verification 从 `actions.result` JSON 提取，缺失/非字符串视为空）。
- `effect='none'`（失败/拒绝/未应用）与只读动作（`writes=0`）**不计入**——它们没有应用变更，无需验证。`effect='unknown'` 的动作在到达停止点前已被既有 `action_outcome_unknown` 失败结算，不会进入 gate。
- 域规则 `unverified_writes`：回执 verification 去空白后为空者即「未验证」，按序去重收集 tool 名。`build_verify_nudge(writes, attempts)`：无未验证项或 `attempts >= MAX_VERIFY_NUDGES` 返回 None（调用方据此收尾）；否则生成列出未验证工具（≤8，超出显示「... and N more」）的 nudge，明确要求做具体验证（回读/检查/观察后置条件）、修复失败、报告实际观察到的证据，并声明最终文本与清单勾选不是成功证据、无法验证时说明具体阻塞而非宣称已验证。

## 四层接线

- **domain** `domain/verification_stop.py`：纯规则。`MAX_VERIFY_NUDGES=2`、`MAX_NUDGE_TOOLS=8`；`unverified_writes`、`build_verify_nudge`。只判定与塑形 nudge，不等待、不派发、不触存储。
- **adapters** `adapters/persistence/repository.py`：`run_applied_writes` 只读查询（既有 `actions` 表，无新表/迁移）。`UnitOfWork` 端口同步签名。
- **engine** `application/engine.py`：构造器 `verify_on_stop: bool = False`。`_run` 在自然停止点（`if not reply.calls:`，费用结算与退化守卫之后）当 `self._verify_on_stop and not remote` 时查 `run_applied_writes`→`build_verify_nudge(writes, verify_nudges)`；非 None 则 `verify_nudges += 1`、追加 assistant 候选答案行 + user nudge 行、`continue` 进入下一步；None 则照旧 `finish(reply.text)`。`verify_nudges` 为 per-run 局部计数。
- **composition** `composition/instance.py`：`open_instance(verify_on_stop=False)` 透传 `Application(verify_on_stop=...)`。`composition/http.py`：`http_app(verify_on_stop=False)` 透传 `open_instance`，给生产 HTTP 入口显式启用通道。

## 停止点边界

- gate 只作用于**模型自然文本停止**（`not reply.calls`）。**预算内收敛 grace 路径**（K07，步预算耗尽后的一次无工具收尾）本就是 fallback，不再套 gate，避免无界循环。
- **所有者命令路径**（owner_command 单动作）不经模型循环，不套 gate。
- **远程 QQ run**（空 registry）跳过 gate（`not remote` 守卫，且无写动作）。
- nudge 是回合内瞬态控制行（与 grace 的 `append_runtime_notice` 同性质），不单独持久化、不新增事件保留种类；持久证据源是既有 `actions` 回执。候选答案保留在回合上下文，次数用尽后由 `finish` 落库为最终 assistant 消息。
- 每次 nudge 消耗一个步迭代，受既有 `max_steps` 硬限与费用预留约束；委派子 run 的严格调用数上限同样自然约束 nudge 次数。写动作**绝不重放**（gate 只读回执、追加对话行，不重新派发动作）。

## 限额

`MAX_VERIFY_NUDGES=2`（每 run 最多 2 次 nudge）、`MAX_NUDGE_TOOLS=8`（nudge 列出工具上限）、`run_applied_writes` 查询上限 200。默认 OFF（opt-in，与参考一致）。

## 明确不在本项范围

- 不新增表/迁移/事件保留种类/run 状态；证据复用既有 `actions.result.verification`。
- 参考的工作区验证账本（`hermes verify` recipe、canonical command、LSP 迟到 verdict、pre_verify 插件钩子、kanban 终止工具守卫）**未对等**：V2 以逐动作回执 verification 为证据，无工作区 recipe/LSP/插件钩子/看板链路。
- 参考默认 OFF 由 env/config 驱动；V2 由 `open_instance`/`http_app` 的 `verify_on_stop` 显式开关驱动，未接入 Agent 设置严格 schema（避免改 KEYS 全集与大量调用方）。
- 「后续独立验证动作是否回溯认定先前写已验证」未实现：判定以写动作**自身回执**是否带 verification 为准，单独读动作的 verification 不自动归属到写。
- 无 interim 答案的流式对外发射（V2 循环内候选答案留在上下文，finish 时落库）；参考的 `pending_verification_response_previewed` 流式前缀语义未对等。

## 完成条件

域规则/只读证据查询/engine 自然停止点 gate/composition 与 HTTP 启用通道已接；有界 nudge、候选答案 fallback、已验证写不触发、默认 OFF 不触发、无写不触发、effect/只读过滤与 run/Agent 隔离、写不重放均有专项回归。仅类/提示词/空工具名不算完成。本项关闭不代表整个内核对标或 V2 完成；真实模型/设备/72 小时验收另列。