# V2 模型侧调度工具 cronjob_manage 与任务输出依赖设计合同（K13 cron 变体缺口实现前合同）

## 状态

**设计合同（design_contract_recorded），非实现。** K13 审计缺口「cron 模型侧 cronjob_manage 工具 + 技能绑定 + 任务输出依赖（context_from/continuity）」仍未实现；本批交付实现前必须的**工具面 + 持久化 + 输出链 + 授权/预算边界**设计（按总对标纪律「先设计/持久化合同后接线」，本能力已由所有者授权自主规划，不属于待决策真断点）。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。

## 参考与 V2 现状（实读源码）

参考 `xuexi/hermes-agent`（同仓 `xuexi/`）：
- `tools/cronjob_tools.py:1056` `CRONJOB_SCHEMA` 单工具 `cronjob_manage`，`tools/cronjob_tools.py:957-964` 动作 `create/list/update/pause/resume/remove/run`；字段含 `schedule/name/prompt/repeat/deliver/failure_deliver/skill(s)/script/monitor/no_agent/context_from/continuity/enabled_toolsets/workdir/attach_to_session/paused/pinned`。
- `tools/cronjob_job_args.py:443-468`：`context_from` 是**任务 id 列表**，字面量 `self` 表示本任务自身；`continuity=true` 等价于把 `self` 加入 `context_from`，`false` 移除；非 `self` 引用在创建时校验存在。
- `tools/cronjob_tools.py:451-466` `_latest_job_output_excerpt`：触发时注入该任务**最近一次输出文件**摘录，上限 2000 字符，超长加截断标记。
- 参考 `run` 在后台立即触发并回投结果；`deliver` 自动把输出 POST 到渠道；`pinned/model/provider` 固化推理供应商；`script/monitor` 每 tick 执行脚本或抓取 URL。

V2 已有原语（均实读核验）：
- `domain/scheduling.py:117-149` `ScheduleValue`（`kind=cron|interval|once`、`timezone`、`expression`、`interval_seconds∈[60,31536000]`、`start_at`、`session_id`、`prompt`、`misfire=latest`，`extra="forbid"`），`Cron.parse`（`:67-81`，五段有界数字）、`next_due`（`:159-169`）、`due_selection`（`:172-190`，含 coalesced_from 合并语义）。
- `application/scheduling.py:37-71` `save`（时区解析 + 由 `max(now,last_scheduled_at)` 重算 next_at + revision CAS）；`:101-156` `tick`（批次 16，`schedule_materialize` 物化 occurrence，`schedule_enqueue` 内 `accept_turn` 入队）；`:124-140` accept 回调**只传** 5 个参数（无技能集）。
- `adapters/persistence/repository.py:206-235` `accept_turn` 已支持 `skill_ids`/`skill_platform`（仅 `origin=="chat"` 且需模型），`:331` 调 `skill_capture_run(...selected_skills, skill_platform)`；`skill_repository.py:281-312` 捕获当前 enabled 技能并 `skill_activate_run` 选中项；`application/skills.py:255-284` `notice` 把**已激活**技能的 SKILL.md 注入（正文原话：guidance only, never grants permission）；`application/engine.py:321,341` 非 remote 回合拼入该 notice。
- 运行输出：`repository.py:556-584` `finish` 写 `role=assistant` 消息并回填 `run_id`，**输出就是该消息正文**（无输出文件）；`occurrences` 表以 `run_id` 唯一关联 run（`schema.py:1184-1206`，state ∈ pending/enqueued/blocked/cancelled/legacy）。
- 写工具审批门：`engine.py:767-772` 先按 `ToolSpec.parameters` 做 Draft202012 校验，`:775-789` `writes=True` 时 `prepare_action` 后等待 `approved/denied/cancelled`（过期抛 `approval_expired`），`:790-807` 资源锁 + `authorize_action` + `invoke`；`domain/contracts.py:96-112` `ToolSpec(name/description/parameters/writes/timeout_s/resource)`。
- 能力注册与分档：`composition/capabilities.py:21-28`（spec 表 + `Capability` 包装）、`application/engine.py:59-62` `Capability(spec, adapter)`、`composition/instance.py:640-650` 装配、`domain/routing.py:15,120` `RETRIEVAL_TOOLS` 只放只读工具。
- 调度当前**仅所有者 HTTP**：`adapters/http.py:310-314` `ScheduleInput`、`:1135-1170` 列表/创建/读取/改/删路由；模型侧无任何 schedule 能力。
- 投递独立：`application/delivery.py:54-90` `create` 需要 `message_id + config_id + target` 且经所有者决定，调度**不会**自动把结果发到渠道。
- 迁移纪律：head `v2_0048`（`migrations/versions/v0048.py:5`），DDL 冻结/保留行/身份触发器/禁删触发器/`downgrade` 拒绝（`:37-45`）；`schema.py` 元数据共 **143** 张表，pin 在 `tests/v2/core/test_v2_action_review_migration.py:115`、`test_v2_memory_organization_migration.py:114`、`test_v2/platforms/common/test_v2_terminals_migration.py:85`。

**关键阻塞（模型侧调度无现成机制）：**
1. 无模型工具：`composition/capabilities.py` 无 schedule spec，`SchedulingService` 只被 HTTP 路由调用，模型无法创建/查看日程。
2. 无持久化载体：`schedule_revisions`（`schema.py:1131-1158`）是**强类型列**，无 JSON 值列；技能绑定与输出引用无处可存。
3. 无技能载体：`tick` 的 accept 回调未把技能集传进 `accept_turn`，`skill_capture_run` 的 `selected` 通路在调度上不可达。
4. 无输出链读取：没有"某日程最近一次成功输出"的查询，`accept_turn` 只接受纯文本，无注入点。
5. 无手工触发账本：`occurrences` 以 `(schedule_id, scheduled_at)`/`(schedule_id, sequence)` 唯一，并受 `last_scheduled_at` 游标守卫（`schedule_repository.py:291-300`），参考 `run` 无法在不伪造 occurrence 的前提下实现。
6. 无模型侧外发：`DeliveryService` 必须带渠道配置、目标与所有者审批；参考 `deliver` 的"自动 POST"与 V2 安全边界冲突。

## 设计决策

### 决策 1：工具面（不重建第二内核）

新增单工具 `cronjob_manage`（名字对齐参考，便于对标审计）：`writes=True`、`resource="schedules"`、`timeout_s=30`，动作枚举 `create/list/update/pause/resume/remove`。**实现为 `SchedulingService` 的薄适配器**：全部读写走既有 `save/get/page/archive`，不新增调度循环、不复制 due 计算、不绕开 revision CAS。

| 参考字段/动作 | V2 处置 | 理由 |
|---|---|---|
| `action=create/list/update/pause/resume/remove` | 采纳 | 与 `save/get/page/archive` 一一对应 |
| `action=run`（立即触发） | **不采纳**，`invalid_tool_arguments` 级别拒绝（枚举外） | 手工触发要么伪造 occurrence（破坏 `(schedule_id,scheduled_at)` 唯一与游标），要么引入第三本触发账本；需独立设计 |
| `schedule` / `name` / `prompt` / `timezone` | 采纳 | 既有 `ScheduleValue` 字段 |
| `skills`（有序技能绑定） | 采纳（`skill_id` 列表 ≤4，与运行时 `MAX_ACTIVE` 同源） | 复用 `skill_capture_run(selected)` 与 notice，不新建权限 |
| `context_from` / `continuity` | 采纳（refs ≤4 / 布尔） | 见决策 4 |
| `deliver` / `failure_deliver` | **不采纳** | 自动外发越界；V2 投递仍由所有者审批的 outbox 承担 |
| `model/provider/base_url/pinned` | **不采纳** | 无人值守花费指向别的模型是所有者决策；调度沿用主模型配置 |
| `script` / `monitor` / `no_agent` | **不采纳** | 任意代码执行/网络抓取面，属安全模型另议 |
| `enabled_toolsets` | **不采纳** | V2 用 tier + 审批门，不按工具集裁剪 |
| `workdir` / `attach_to_session` | **不采纳** | workspace 是 owner 设置；会话续接属投递模型 |
| `repeat`（次数上限） | **不采纳** | 需 occurrence 计数到量自动归档的收尾语义（新状态机分支），列后续 |

`list` 只返回有界投影：`job_id/name/enabled/kind/expression|interval_seconds/start_at/timezone/next_at/last_scheduled_at/last_occurrence{state,error_code}/prompt_preview(≤200)/skills_count/context_count`，**不回传** `session_id`、完整 prompt、tzdb 版本、审计；每页 ≤50。

**实现期修订（模型工具批）**：`skills_count/context_count` 由完整 `skills/context_from` 列表（各 ≤4）取代——update 是 patch 语义，模型必须能读到当前绑定才能保留或清空；投影同时补 `revision`（CAS 必需）与 `continuity`。`last_occurrence.state` 有 run 时取 `run_state`（succeeded/failed/…），无 run 时取 occurrence 自身状态（pending/blocked/cancelled）；`error_code` 优先 occurrence 错误码，否则 run 错误码。写动作回执携带同一投影（`data`），便于模型不再 list 即对账。

### 决策 2：日程语法（只接受可判定形式）

| 输入 | 映射 | 说明 |
|---|---|---|
| `0 9 * * *` | `kind=cron` | 既有 `Cron.parse` 五段有界数字 |
| `30m`/`90s`/`2h`/`1d`/`every 30m`/`every 2 hours` | `kind=interval`，`interval_seconds` | 归一化后落 `[60,31536000]` |
| `in 30m`/`in 2h`/`in 1d` | `kind=once`，`start_at=clock.now()+delta` | **服务端 Clock 计算**，禁止模型自算绝对时间 |
| `2026-12-01T09:00:00+08:00`（必须带显式偏移） | `kind=once`，`start_at` | 无时区 → 422 |
| `every monday 9am`/`weekdays at 9am` 等自然语言 | 422 `invalid_schedule` | 改写为 cron；自然语言解析需独立合同 |
| 其它 | 422 `invalid_schedule` | 错误正文提示可用形式 |

`timezone` 为 IANA 名称（默认 `UTC`），由 `Timezones.resolve` 校验并 pin `tzdb_version`；时区库变化后既有 `timezone_data_changed` 阻断不变。

### 决策 3：技能绑定（指导文本，不是权限）

- 新表 `schedule_skills(schedule_id, revision, agent_id, position, skill_id, revision_id)`，`position ∈ [1,8]`，唯一 `(schedule_id, revision, position)`，`scoped_fk` 分别指向 `schedules`/`skills`/`skill_revisions`（后两者只校验 `(id, agent_id)` 归属；`skill_id` 与其 `revision_id` 必须匹配由保存时校验保证，不额外加 `skill_revisions(id, skill_id, agent_id)` 唯一索引）。
- 保存时校验：技能存在、`state='enabled'`、manifest 支持当前平台；否则 422。修订**不可变**，技能绑定随修订固化（历史修订保留旧绑定）。
- 触发时 `tick` 把绑定 id 传给 `accept_turn(..., skill_ids=..., skill_platform=...)` → `skill_capture_run(selected)` 激活 → `SkillService.notice` 注入。**不新建技能判定**，不复制"已激活技能"逻辑。
- 触发时技能若已归档/禁用，capture 只捕获 enabled 项、run 继续；差异经 `skill_run_report` 与 occurrence 可见，**不把日程置 blocked**（避免维护性动作永久停摆）。技能正文按既有声明的 "guidance only; never grants permission" 生效，绑定不授予任何工具权限。

### 决策 4：任务输出依赖（context_from / continuity）

- 新表 `schedule_context_refs(schedule_id, revision, agent_id, position, ref_schedule_id)`，`position ∈ [1,4]`，唯一 `(schedule_id, revision, position)`，`scoped_fk` 到 `schedules`；`schedule_revisions` 增列 `continuity INTEGER NOT NULL DEFAULT 0`（`CHECK(continuity IN (0,1))`）表达"自身上一份输出"。参考的 `self` 字面量**不采纳**（创建时无法解析自身 id；自身一律由 `continuity` 表达）。
- 保存校验：refs 去重、同 agent、存在且未归档、非自身、≤4；**图无环**（DFS ≤64 节点/≤4 跳）。环会把输出链变成互相喂养的无界上下文，必须在保存时拒绝（`invalid_context_cycle`）。
- 触发注入（在 `tick` 物化后、`accept_turn` 之前）：按 position 依次取 ref 日程**最近一次 `succeeded` occurrence** → 其 `run_id` 对应、由 `finish` 写入的 assistant 消息正文；脱敏后截断 2000 字符并加截断标记；拼成固定前缀块（`Scheduled job context (<name>): ...`）后接 `Task:` 与原 prompt。无成功输出 → `(no completed output yet)`；已归档 → `(unavailable)`；不阻塞本次触发。
- 脱敏实现落点（实现期修订）：**不用** `system.observability.redact`。V2 分层守卫（`test_v2_architecture.py`）的 `ALLOWED["composition"]` 不含 `system`，从组合层导入即违规。改为由组合层注入**同一活实例** `adapters.diagnostics.Redactor.text`（`sink.redactor`）——它属于 `adapters` 层，正则覆盖面与 V1 的 `redact` 等价，且额外覆盖**运行期 `register()` 进来的真实凭证**，比合同初稿更强。脱敏**先于**截断，避免把凭证截成不再匹配模式的残片。
- `continuity=true` 用同一查询取**本日程**最近一次成功输出（首次触发为空）。
- 只取 `succeeded`：`failed/cancelled/unknown/blocked` 一律跳过（unknown 绝不重放，也不进上下文）。注入块实测上限 = 4 条 ×（2006 截断值 + `Scheduled job context (<name>): ` 前缀，名称 ≤100）≈ 8.6K 字符，加 prompt（≤ 20000）仍远低于 `accept_turn` 的 200000 上限（合同初稿的"≤8000"少算了前缀与截断标记，实现期已更正）；**不新增输出文件**，输出仍是消息（审计链不复制）。
- 摘录查询在 `schedule_enqueue` 的 accept 回调内、**复用同一 UnitOfWork 连接**完成（该回调已处在 `_db.write` 事务里，另起读连接会自锁）；查询为只读 join（occurrence→run→message），不产生新写。

### 决策 5：持久化与迁移 v0049

- `ALTER TABLE schedule_revisions ADD COLUMN continuity INTEGER NOT NULL DEFAULT 0` + `CHECK(continuity IN (0,1))`（SQLite 允许带默认值的加列，既有行保持 0 且语义等价）。
- 新建 `schedule_skills`、`schedule_context_refs` 两表：FK（`ON DELETE RESTRICT`）、唯一约束、position/长度 CHECK、`agent_id` 归属列、`ident()/scope()/stamp()` 与既有表一致。
- 修订子行**只插不改不删**：加身份触发器（禁止更新 `schedule_id/revision/position/skill_id|ref_schedule_id`）与禁删触发器（`RAISE(ABORT)`），与 `v0048` 的触发器纪律一致；`downgrade` 抛错拒绝。
- 表数 **143 → 145**，同步全部表计数 pin（实测 **14 处**，合同初稿估计的"三处"偏少）；迁移测试沿用既有范式：源表（含 `schedules/schedule_revisions/schedule_progress/occurrences`）行数与内容不变、新表初始为空、DDL 冻结、`upgrade→reflection` 全表存在、`downgrade` 拒绝。
- `ScheduleValue` 扩展 `skills: list[str] ≤4`、`context_from: list[str] ≤4`、`continuity: bool`（`extra="forbid"` 保持）；所有者 HTTP `ScheduleInput.value` 与模型工具**共用同一校验与同一 `save`**，杜绝两份定义漂移。

### 决策 6：授权与预算（不放大权限）

- 工具 `writes=True` → 走 `engine.py:775-807` 既有审批门：`prepare_action` → 所有者 `approved` → `authorize_action` → `resource="schedules"` 锁 → `invoke`；`denied/cancelled/expired` 沿用 `approval_denied`/`action_revoked`/`approval_expired`。**无需新授权机制**。
- 所有者批准的是"这份日程定义"，**不是**"每次触发的所有动作"：每次触发是普通 run，仍受同一工具分档与写工具审批门约束（日程内写动作照样等人审批/过期），投递仍走所有者审批的 outbox。
- 花费走既有日预算 `reserve_call`：预算不足 → run failed（不重试、不预支）；无新配额/新预算表。

### 决策 7：动作语义与幂等

- `create`：`name` 缺省取 prompt 前 50 字；不幂等（无自然键），动作结果 unknown 时**不重放**，模型应 `list` 对账。

**实现期修订（模型工具批）**：`create` 固定 `enabled=True`（工具面不暴露 enabled/paused 字段，启停由 pause/resume 承担）；缺省 name 实为 `prompt.strip()[:50]`（先剥前导空白，保证非空名通过保存校验）。`CronjobCapabilities(schedules, clock)` 注入 Clock——`in N` 相对一次性由服务端时钟计算（交接落点简写为 `(schedules)`，clock 是决策 2 的必需注入）。组合层仅在 `capabilities is None`（生产路径）attach，与 todos/clarify/background_review 同一门控先例，隔离测试注入的工具面字节等价零回归。`Application.attach` 除重名（含 owner 工具碰撞）外拒绝引擎控制名（request_mode/delegate_task/todo_list/clarify），防止 run 期 registry 静默覆盖；仅限 `start()` 前调用。已知公共错误码映射为 ERROR 回执，未知码上抛由引擎结算 UNKNOWN（writes=True 不重放）。审批过期在 waiting_approval 态由既有 `_end_error` 收尾为 **interrupted**（error_code=approval_expired），所有者撤销走 run cancel → cancelled；两者均不派发、不创建日程（有专项断言）。
- `update`：patch 语义（省略即保留；`skills=[]`、`context_from=[]`、`continuity=false` 清空）；必须带当前 `revision`，冲突 → `revision_conflict` 让模型重读；改 `schedule` 时按 `max(now,last_scheduled_at)` 重算 next_at（不补跑、不突发）。
- `pause`/`resume`：`save(enabled=false/true)`，恢复不补跑（合并语义不变）。
- `remove`：`archive`（revision CAS；pending occurrence → `cancelled`/`schedule_archived`，与既有 HTTP 删除一致）。

## 实现步骤（合同记录后，独立批次）

1. **持久化批**：v0049 迁移 + `schema.py` 两表/一列 + `ScheduleValue` 扩展 + `SchedulingService.save` 校验（存在性/平台/上限/环）+ 读写（`schedule_get` 投影、`schedule_save` 子行插入）+ 迁移测试与表计数 pin 更新（实测 14 处）。
2. **触发链批**：`tick` accept 传 `skill_ids`/`skill_platform`；输出摘录查询（occurrence→run→assistant 消息）+ `redact`/截断 + prompt 前缀拼接；测试技能激活、只取 succeeded、缺失/归档标注、环外正常链。
3. **模型工具批**：`application/cronjob.py` 工具实现 + `domain` spec（参数枚举/additionalProperties=false/长度上限）+ `composition` 注册（**不入** `RETRIEVAL_TOOLS`）+ list 投影；测试审批拒绝/撤销/过期、schema 422、CAS 冲突、archive 取消 pending、上限 8/4/256。
4. **端到端批**：create→审批→触发→通知注入→update/pause/resume/remove；strict（`pyrightconfig.strict.json` 仓库根）+ ruff + 调用方零回归 + `scripts/project_index.py`（并行 UI 会话期间聚合索引暂缓提交）。

## 有界与安全

数量：enabled ≤256、总数 ≤4096（既有）、技能 ≤4（= `domain.skills.MAX_ACTIVE`）、context refs ≤4、prompt ≤20000、name ≤100、list ≤50/页。注入块 ≤8.6K（4×(2006+名称前缀)）且脱敏、只取 succeeded。不自动外发、不 pin 模型、不执行脚本/抓取、不扩工具集、不改 workspace、不跨 agent、不伪造 occurrence（无 run-now）、不重放 unknown、revision CAS、archive 取消 pending、写动作仍走审批门、花费仍走日预算。

## 边界

本批**仅设计合同，无源码/测试/迁移改动**；`cronjob_manage`、技能绑定、输出注入、v0049 均未实现。不主张 cron 变体全集对等：`run`/`deliver`/`repeat`/自然语言日程/脚本/监控/模型 pin/工具集限制/手工触发账本仍为缺口；真实模型调度质量、长跑稳定性、真实通道外发另验。`whole_*_complete` 保持 false，不提前关闭任何对标项。
