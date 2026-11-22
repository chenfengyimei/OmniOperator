# V2 cron 变体残余设计合同（K13 组 5 剩余：toolsets/workdir/deliver/failure_deliver、monitor/no_agent+script、prompt scan）

状态：**设计合同（design_contract_recorded，纯文档，无源码/测试/迁移/路由/worker 改动，2026-11-23）**。**未实现**。K13 cron 四批（`1d52cca1`/`a3436361`/`4acc46c9`/`42cf2e98`）已交付 `skills`/`context_from`/`continuity` + `cronjob_manage` 六动作 + 生产端到端；本册处理 [K13 审计](V2_TOOL_CHANNEL_AUDIT.md) 组 5 表内**其余未交付项**，供所有者决定是否移植、按何种安全边界接线。写扩面/分享/直播/沙箱/CDP 不并入。

## 参考事实（固定 `9976f485`，`cron/AGENTS.md` + `cron/job_definition.py`）

- **job 字段**：`skills`、`model/provider`、`script`（**必须是 `HERMES_HOME/scripts/` 下的文件**，绝不接受命令行；stdout 注入 prompt；`no_agent=True` 时脚本就是整个 job）、`context_from`（链式注入上游输出）、`workdir`（用该目录的 `AGENTS.md`/`CLAUDE.md` 作为上下文）、`deliver`/`failure_deliver`（多平台投递）、`enabled_toolsets`（允许清单）、`attach_to_session`、`monitor_script`/`monitor_url`（变化检测门控 run）、`pinned`/`reasoning_effort`。
- **调度纪律**：inactivity watchdog（默认 600s idle，`HERMES_CRON_TIMEOUT` 覆盖，0=无限）；脚本单独超时（默认 3600s）；catch-up 窗口=周期一半 clamp 120s–2h、one-shot 120s grace；at-most-once 记账（tick 先推进 `next_run_at` + `pending_slot`）；headless tick 拒绝 spawn；cron 会话 `skip_memory=True`、独立 session；可镜像/种子到回复会话。
- **prompt scan**（K13 审计记录）：`cronjob_prompt_scan` 对用户 prompt 做严格威胁扫描（技能体按 prose-proof）。

## V2 现状

- `domain/scheduling.py:164-185` `ScheduleValue`：`kind/timezone/expression/interval_seconds/start_at/session_id/prompt/misfire("latest")/skills/context_from/continuity`；v0049 两表一列保存技能绑定与输出依赖；无 `toolsets/workdir/deliver/failure_deliver/monitor/script/no_agent/attach/prompt scan`。
- `domain/cronjob.py` 的 `cronjob_manage` schema `additionalProperties:false`，六动作（create/list/update/pause/resume/remove）+ get/page；模型侧每次写动作走所有者审批门（K13 cronjob_tool）。
- 触发链：`SchedulingService.tick → materialize occurrence → accept_turn`（技能真激活、上游输出注入 + Redactor 脱敏、continuity 自喂）；失败 `previous_outcome_unknown` 阻断（D8 已记录 purpose=summary 交互）。
- 投递：所有出站必须经 `action_review` 所有者审批 + `delivery` outbox；**无 cron 级自动投递/失败投递**；无通用配额（D4 合同 F1）。
- 工作区：agent 级 `settings["workspace"]`，无 per-job cwd；模型 `shell_execute` 为一次性已审 argv；无无模型脚本执行路径；无监视器轮询。
- 无 prompt 威胁扫描；技能/来源审核与所有者审批是现门。

## 设计选项（需所有者选定）

### R1 `enabled_toolsets`（cron 级工具允许清单）

- **R1-a（推荐先做/可不做）**：不新增字段。V2 已有 run 档位（route mode）+ settings `disabled_tools` + 子 run 继承工具面；cron 级清单与档位模型重叠，收益低。审计登记「部分对等，不移植」。
- **R1-b**：`ScheduleValue.toolsets[]`（≤8，白名单名字）→ 触发 run 时与 baseline 交集（只减不增），持久化需新列 → A 迁移；需定义与 route 提升/委派继承/`disabled_tools` 的优先级。
- **R1-c**：仅允许映射到既有档位（`mode` 枚举），不加自定义清单。语义弱于参考但零迁移。

### R2 `workdir`（cron 级工作目录）

- **R2-a（推荐先做/可不做）**：不移植。V2 的 agent workspace 已固定，跨目录运行会绕过 workspace 边界；需要时由所有者改 agent workspace 或模型用已审 `files_*`/`shell_execute`（cwd 受 worker 限制）。
- **R2-b**：per-schedule `workdir`，**必须位于 agent workspace 之内**（realpath 包含校验、拒符号链接/上级穿越），持久化新列 + worker `workspace_path` 覆盖 → A 迁移 + 共享协议协调；参考的「加载该目录 AGENTS.md/CLAUDE.md」在 V2 无对应（V2 不读 AGENTS.md 作提示），不移植该语义。

### R3 `deliver` / `failure_deliver` / `attach_to_session`

- **R3-a（推荐先做）**：不新增自动投递。运行输出留在会话（工作台可见）；**失败通知**先做 owner 可见事件/工作台状态（若需要外部通知，走既有 `delivery` + 所有者审批，不自动）。`attach_to_session` 在 V2 语义不同：schedule 已有 `session_id`，触发即在该会话产生新 run（等价「附着到已存在会话」），无需新字段；「镜像到回复会话」不移植。
- **R3-b**：`deliver` 目标（owner 绑定渠道/`local`/`none`）+ `failure_deliver`：需要「cron 自动投递」授权模型（自动批准出站 = 绕过逐条 owner 审批），属独立授权合同；失败投递还必须覆盖 unknown 不重发、配额、去重。
- **R3-c**：只做失败通知到 owner 事件（零外部投递），成功不投递。

### R4 `monitor` / `no_agent` + `script`

- **R4-a（推荐先做）**：不移植。两者都需要**新的无模型执行面**：`script` 是文件执行（任意代码/脚本），`monitor_url` 是周期性出网 GET（SSRF/IMDS）；与 D2（代码沙箱）、D9（浏览器 URL floor）同族安全敏感。审计登记「不移植/另立合同」。
- **R4-b（script/no_agent）**：所有者预置脚本文件（存于实例数据目录、非模型可写、无参数命令行、固定 interpreter、env 清洗、临时 cwd、超时 ≤60s、输出 ≤64KB），由调度器执行且**不经模型**；需独立安全合同 + 所有者批准 + 审计；与 D2 的 S2/S3 关系必须写明（不得成为绕开审批的执行通道）。
- **R4-c（monitor_url）**：只读 HTTP GET + 内容 hash diff 门控 run；必须先落 D9 的 SSRF/IMDS floor，域名 allowlist、超时/大小上限、redirect 限制；`monitor_script` 归 R4-b。

### R5 `cronjob_prompt_scan`（prompt 威胁扫描）

- **R5-a（推荐）**：不移植。V2 的 cron prompt 由所有者/模型经 `cronjob_manage` 创建并受审批门与技能/来源审核约束；扫描规则是启发式、易误报，收益不明。
- **R5-b**：纯领域**建议式**扫描（只产生 owner 可见警告，绝不自动阻断/改写 prompt）；需规则集与误报评估，独立批。

## 决策问题

1. R1/R2 是否需要（默认不移植）？若需要，字段/迁移（A 编号）与优先级定义。
2. R3 自动投递是否开放？若开放，授权/配额/unknown/恢复合同（与 D4-Q2、D6 分享账本统一）。
3. R4 是否开放无模型脚本或 URL 监视？必须先于实现独立安全合同并接 D2/D9；禁止成为绕过审批的执行通道。
4. R5 是否要建议式扫描（默认不做）？

## 实现要点（获批准后独立批）

- R1-b/R2-b/R3-b：`ScheduleValue` 加性字段 + v00xx 迁移（A 编号）+ `cronjob_manage` schema（`additionalProperties` 保持 false，显式字段）+ 触发链交集/包含校验 + owner 投影；R2-b 另需 worker `workspace_path` 覆盖（A 协议协调）。
- R4-b：新 `monitor/script` 执行适配器（文件路径校验、argv-free、env scrub、超时/输出上限、审计）；R4-c：HTTP GET 适配器（复用 D9 floor）。
- R5-b：`domain/prompt_scan.py` 纯规则 + 只读警告事件。
- 无新授权机制；不新增自动投递除非 R3-b 获批；无第二内核/状态库。

## 测试计划

- R1：交集只减不增、与 route 提升/委派继承/`disabled_tools` 叠加、持久化投影、旧库升级。
- R2：containment（workspace 外/符号链接/`..` 拒绝）、worker cwd 覆盖、无 AGENTS.md 语义。
- R3：失败事件/状态投影；若 R3-b：自动投递授权、配额、去重、unknown 不重发、恢复。
- R4：脚本路径校验/argv-free/env scrub/超时/输出上限/审计；URL floor 负例（私网/元数据/重定向）；拒绝成为模型执行通道。
- R5：规则单元/误报样例/只读警告不影响 prompt。
- 调用方：scheduling/cronjob/engine/delivery/架构/类型守卫；真实账号/模型另验；未实现前不宣称组 5 对等。

## 明确不做

- 写扩面（D4）、主动观看（D5）、分享（D6）、直播（D7）、沙箱（D2）、CDP（D3）；前缀重构（D1）；真实模型/账号/72 小时；全量。

## 关联

- K13 审计：[V2_TOOL_CHANNEL_AUDIT](V2_TOOL_CHANNEL_AUDIT.md) 组 5。
- 决策登记：[V2_REMAINING_DECISIONS](V2_REMAINING_DECISIONS.md)「K13 组 5 残余」。
- 相关合同：[V2_CRON_MODEL_TOOL](V2_CRON_MODEL_TOOL.md)、[V2_CODE_SANDBOX](V2_CODE_SANDBOX.md)（D2）、[V2_BROWSER_CDP](V2_BROWSER_CDP.md)（D3/D9）、[V2_BILI_SHARE_POLICY](V2_BILI_SHARE_POLICY.md)（配额账本）、[V2_SCHEDULE_REVIEW_BLOCKER](V2_SCHEDULE_REVIEW_BLOCKER.md)（D8）。
- 证据：[v2-cron-variants-design.json](../../evidence/v2-cron-variants-design.json)。
