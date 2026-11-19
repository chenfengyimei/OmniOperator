# V2 结构化澄清与当前回合纠偏（K10）

## 结论与范围

K10 补上模型侧 `clarify` 工具：模型在一次调用里向所有者提出 1–5 个结构化问题，**当前 run 暂停等待**所有者响应、取消或超时，然后**同一个 run 继续**并把结构化答案喂回模型循环。排队的下一回合是独立 run，**不是**当前 run 的继续——这条边界由复用既有 run 状态机与 FIFO 租约保证。

参考 `xuexi/hermes-agent/tools/clarify_tool.py`（固定提交 `9976f4857b72439d79016354d7b6e1a3ee952e2f`）的薄派发器设计（归一化问题→平台回调等待→结构化 responses/outcome），在 V2 四层内独立适配。参考的等待 UI 在平台回调里；V2 的等待落在 run 协程内，复用写审批的暂停/续跑/取消/租约机制。未复制第三方源码、未拉取远程、未用真实账号/模型。

## 暂停与续跑（复用 waiting_approval）

clarify **复用既有 `waiting_approval` 暂停态**，不新增并行 run 状态：

- run 协程在 `_clarify_wait` 内轮询（每 0.1s），每轮先 `_checkpoint`（`assert_run_authorized` + 取消/关闭检查），与写审批等待循环同构。
- 进入等待时 `clarify_request` 把 run 从 `running` 转为 `waiting_approval`；`work_items` 保持 running、会话租约因 `waiting_approval ∈ LIVE` 持续续期，因此同会话后续回合仍被 FIFO 阻塞，直到本 run 续跑/结束。
- 所有者响应/取消/超时经 `_resolve` 把**同一 run** 转回 `running`（`check_transition` 允许 `waiting_approval → running`），重断言仍持有的租约。无 `accept_turn`、无新 `run_id`。
- run 级取消（`POST runs/{id}/cancel`）走既有 `cancel`：waiting→cancelling、pending clarification→cancelled、任务 `.cancel()`；`_checkpoint` 抛 `CancelledError`，`_end_error` 收尾。启动恢复 `cancel_approvals` 一并取消 pending clarification。

因此 run 状态机、`LIVE`、`accept_turn` 计数、`_end_error`、`terminal_recover`、事件保留种类白名单**均无需改动**——clarify 的区分由 `clarifications` 表与 `clarify.*` 审计承载，run 级事件仍是已白名单的 `run.waiting_approval`/`run.running`。

## 四层接线

- **domain** `domain/clarify.py`：纯规则。`CLARIFY` ToolSpec（questions 1–5、choices ≤4、multi_select）；`normalize_questions` 校验/去重/clamp、首选项标记 `(Recommended)`、空选项/越界/控制字符拒绝；`strip_recommended`/`mark_recommended` 保证展示不泄漏进答案；`build_result` 按 qid 生成 `responses`（status answered/skipped/unanswered + user_response）与 `outcome`（submitted/cancelled/timed_out/undelivered）；`render_result` 文本。`CLARIFY_TIMEOUT_S=300`。
- **application** `application/clarify.py`：`ClarifyService` 所有者用例 pending/get/respond/cancel（respond 校验 answers 为对象）。模型侧等待循环在 engine（需 `_checkpoint`）。
- **adapters** `adapters/persistence/clarify_repository.py`：`ClarifyRepositories`。`clarify_request`（要求 run running + policy 一致，建 pending 行 + 转 waiting_approval）；`_resolve`（要求 pending + run waiting_approval，响应/取消校验 revision CAS + 未过期 + policy，超时为系统动作免 revision/policy；置 state/answers/outcome/notice/resolved_at + revision+1 + 转 running）；`pending_clarifications`（pending 且未过期）；`cancel_run_clarifications`（run 取消清理）。跨 mixin 方法经 `cast(UnitOfWork, self)` 访问。
- **engine** `application/engine.py`：`_run` 在非远程生产路径注入 `registry[CLARIFY.name]`；`_batch_entry` 把 clarify 列为**串行屏障**（阻塞等待不得并行）；`_dispatch_call` 在档位门后拦截 clarify（仅 agent 档提供，chat/retrieval 不含）调 `_clarify_wait`；`_clarify_wait` 校验 schema→归一化→建请求→轮询 answered/cancelled/timed_out/过期→返回结构化 ActionResult。所有者 facade `clarifications`/`respond_clarify`/`cancel_clarify`。
- **composition** `composition/instance.py`：生产路径（`capabilities is None`）构造 `ClarifyService(database)` 传入 Application；隔离测试注入 capabilities 时不启用。
- **HTTP** `adapters/http.py`：3 个所有者路由 `GET /agents/{id}/clarifications`、`POST .../clarifications/{cid}/respond`（answers/revision/policy_revision）、`POST .../clarifications/{cid}/cancel`（revision/notice），`StrictInput` 禁额外字段。

## 持久化与迁移

`clarifications` 表（scoped：id/agent_id/created_at + run_id 复合 FK→runs、questions JSON、state、answers JSON、outcome、notice、revision、policy_revision、expires_at、resolved_at）。CHECK：state ∈ pending/answered/cancelled/timed_out；pending 必须 answers/outcome/resolved_at 均 NULL，非 pending 必须 outcome/resolved_at NOT NULL；outcome ∈ submitted/cancelled/timed_out/undelivered。

**JSON NULL 陷阱**：SQLAlchemy JSON 列把 Python `None` 序列化为 JSON `null`（非 SQL NULL），故 pending 插入时**省略 answers 列**让其默认 SQL NULL，满足 pending 分支 `answers IS NULL`。

v0044 冻结迁移（down_revision=v2_0043）：匿名 CHECK + `clarification_identity` 触发器（身份/questions/policy_revision/expires_at 不可变、revision 必须 +1、pending→answered/cancelled/timed_out 单向、终态不可逆）+ `clarification_keep` 禁删触发器。schema.py metadata 同步；14 处 `len(metadata.tables)` 断言 140→141。

## 限额

questions ≤5、choices ≤4（去重 clamp）、choice ≤8000 字符、question ≤4000 字符、notice ≤2000 字符、超时 300s、pending 列表 ≤100。

## 明确不在本项范围

- 不新增 run 状态（复用 waiting_approval）；不新增事件保留种类（run 级事件已白名单）。
- 参考的「Other 自由文本行」由所有者 answers 任意文本承载，不在 schema 强制；无参考的多平台 UI 回调（V2 走所有者 HTTP 路由）。
- 委派子 run 是否提供 clarify 未单独限制（继承 agent 档捕获）；显式用户 steering（运行中追加指令改变当前 run）的完整合同仍属后续，本项聚焦结构化 clarify 等待/响应/取消/超时。
- 超时由读取时 `expires_at <= now` 判定（与审批一致），无后台清扫器；并发响应/超时由 pending 状态守卫裁决，先到先得。

## 完成条件

生产工具/组装链/档位/串行屏障/派发已接；当前 run 暂停与同 run 续跑、所有者响应/取消/超时、run 取消清理、revision/policy CAS、不可变证据均有专项回归。仅类/提示词/空工具名不算完成。本项关闭不代表整个内核对标或 V2 完成；真实模型/设备/72 小时验收另列。