# OmniWeave Architecture（OWA）

## Phase 0 基线

2026-11-26 从 Gitee 和 GitHub 的 `v2` 回读 `d897f2867e4b0a25fe961075264854f002c1ee5b`，从该提交创建 `owa-tickets`。Windows 隔离空库的迁移头是 `v2_0053`，共 53 个修订和 151 张声明业务表，`integrity_check=ok`，`foreign_key_check` 无记录。同提交的 GitHub 全量验证 12 个作业通过，Windows 测试共 8753 项通过、10 项跳过。`1a2810ea` 的 8739 项离线通过属于更早的架构冻结。OWA 本批采用离线专项。[同提交全量基线](https://github.com/chenfengyimei/OmniOperator/actions/runs/45801704584) · [历史冻结证据](../evidence/plugin-architecture-final-2026-11-25.json)

首批按所有者发来的交接正文实现。2026-11-27，从 Gitee `owa-spec` 的 `788a7011` 同步三份正式文档到 `owa-tickets`，导入提交为 `5cd1c6a8`。后续以同步后的正式规格为准。[路线图](../OMNIWEAVE-ROADMAP.md) · [技术规格 V1.0](../architecture/OMNIWEAVE-ARCHITECTURE-SPEC-V1.0.md) · [主设备交接提示词](../OMNIWEAVE-HANDOFF-PROMPT.md)

| 共用合同 | 生产入口 |
|---|---|
| 所有者 Web 受理与 SSE | `create_app.turn`、`create_app.event_stream` 和 `EventStreams.open`。原有游标、鉴权和回执由现有入口负责。[HTTP](../../../agent/src/omni/adapters/http.py) · [SSE](../../../agent/src/omni/adapters/http_events.py) |
| 执行、队列与重启 | `Application.submit_turn` 与 `_dispatch` 使用同一个 `Database`。`Repositories.accept_turn` 写入 `inbox`、Run 与 `work_items`，`LeaseRepositories.claim_runs` 领取任务，`Repositories.recover` 按持久回执处理重启。[Application](../../../agent/src/omni/application/engine.py) · [受理与恢复](../../../agent/src/omni/adapters/persistence/repository.py) · [租约](../../../agent/src/omni/adapters/persistence/lease_repository.py) |
| 授权、动作与核查 | 审批、动作回执和未知结果核查沿用当前 Run 及其 Agent 作用域。[内核合同](KERNEL.md) · [核查功能组装](../../../agent/src/omni/composition/features/action_reviews.py) |
| 功能登记与启动顺序 | `BUILTIN_KERNEL_FEATURES` 使用 `KernelFeatureContext` 装配单例服务。需要后台工作的功能参考调度在内核恢复后的启动和排空。[功能目录](../../../agent/src/omni/composition/features/catalog.py) · [调度安装器](../../../agent/src/omni/composition/features/schedules.py) |

## Phase 1 观察受理

`serve` 和 `omni-web-v2` 默认使用 `off`。用以下命令在隔离实例启用 `observe`，也可在组装入口设置 `omniweave_mode="observe"`。

```powershell
uv run --no-sync omni-v2 --home .\temp\build\owa-observe serve --omniweave-mode observe
```

- 所有者对普通 Web 主会话发送 `POST /api/v1/agents/{agent_id}/turns` 时，HTTP 先验证现有令牌，写入事务再次确认它有效。观察记录的授权来源是实例的所有者 principal，令牌轮换后仍使用同一个 principal。
- Thread 按 Agent 和主 Session 绑定，已存在的 Thread 供下一次新输入复用。每次新受理产生一个根 Ticket，关联原有根 Run。Trigger V1 保存来源 `owner_web`、判断 `observe` 与原 `inbox` 的载荷指纹，通过入站 ID 关联原键。消息正文继续保存在原会话。
- 同键同载荷复用原回执。同键异载荷返回 `409 idempotency_conflict`。复用规则也适用于启用观察前已接受的请求，旧回执保持原决定。普通请求返回 HTTP 202 和 `run_id`、`session_id`、`duplicate`。
- 跨 Agent、跨主会话、QQ 绑定或既有委派子 Session 无法作为观察 Thread 的来源。存储约束同时核对 Thread、Ticket、Trigger 和原入站记录。OWA Worker 启动数为零，原有 `delegate_task` 路径继续执行已有合同。
- 迁移 `v2_0054` 只在同一 `omni.db` 新建三个观察表、索引和绑定约束。空库直接迁移至单头。旧库由现有启动流程自动创建并可校验迁移前快照。已受理回合没有回填。回滚沿用在新目录恢复已验证快照的流程。[加法迁移](../../../agent/src/omni/adapters/persistence/migrations/versions/v0054.py)

## Phase 1 验收

Windows x64 候选 `4082ccacaa31918a22d06bdd6dbf6710e8467906` 的 40 个受影响测试文件共 333 项通过，包含 10 项 OWA 新用例。覆盖事务回滚、重复提交、重启、受理期间令牌撤销、会话隔离、旧委派、事件流、调度、审批核查和发布迁移。空库和旧库副本的完整性检查通过。212 条 OpenAPI 路径的完整定义与基线相等，501 个文件的严格类型检查零错误、零警告。[验证证据](../evidence/owa-phase1-2026-11-26.json)

## Phase 2 Router 与任务依赖

在隔离实例使用 `--omniweave-mode enabled` 启用 Router，已有 `dispatch` 选项继续可用。所有者在工作台会话栏创建 OWA 主会话，或打开已有私有 Web 会话后选用它。每个 Agent 仅有一个当前选定会话，绑定跨重启保存。该主会话中的每条新受理增加一条作用域绑定的执行授权。选择前以及其他 Web 会话均沿用普通 V2 回合。`observe` 记录受理，`off` 为默认配置，重投沿用原决定。

```powershell
uv run --no-sync omni-v2 --home .\temp\build\owa-enabled serve --omniweave-mode enabled
```

- Router 的能力目录和执行注册仅加载 OWA 编排提供者。`agent` 档可调用 `owa_plan` 提交每根 Ticket 至多八个 Worker，任务用计划内键声明依赖。准入核对无环、当前 Agent 策略、可信工具规格、队列容量和授权代次，在同一事务创建任务与 V2 Run。计划回执即时返回，同一计划重投复用原 Ticket。
- Worker 使用独立的内部 Session 和 `owa-workspaces/<run_id>`。任务只可选装配时登记且当前策略允许的工作区文件工具。Worker 的上下文默认没有所有者的全局私有记忆和技能提示，新任务也不继承扩展、MCP 和技能目录。模型调用、预算、逐次批准、动作回执和资源清理沿用内核。前置任务失败、取消、中断或有待核查结果时，依赖任务结束。结果未知的写入进入现有动作核查。
- Router 可以在原主会话继续接收新输入，并用 `owa_status` 查看本会话的 Ticket 状态和 Worker 提交的结果。现有 `/runs/{run_id}/cancel` 可取消根 Ticket，包括根 Run 已结束后仍在执行的 Worker。读 `GET /api/v1/agents/{agent_id}/omniweave/sessions/{session_id}/tickets` 可按 `run_id` 或 `ticket_id` 查询进度。会话、Run、消息、摘要和事件的通用 Web 路径执行内部作用域检查。
- `v2_0055` 新建执行授权、任务和依赖三张表，数据库触发器校验 Worker 和依赖的 Ticket 绑定。Router、普通根回合和 OWA Worker 共用内核容量。默认容量为四时最多同时运行三个 OWA Worker，容量为一时串行轮转。`delegate_task` 保有既有并发额度，仍走原租约。[迁移](../../../agent/src/omni/adapters/persistence/migrations/versions/v0055.py) · [查询入口](../../../agent/src/omni/adapters/http_routes/omniweave.py)
- `v2_0056` 为具有 OWA 执行授权且由已登记的 `owa_plan` 调用产生的子 Run 提供八个名额。既有 `delegate_task` 继续每父 Run 最多四个，保留原来源、会话、快照和策略绑定。[数据库触发器](../../../agent/src/omni/adapters/persistence/migrations/versions/v0056.py)
- `GET /api/v1/agents/{agent_id}/omniweave/main-session` 读取启用状态与选定会话。`PUT` 带 `session_id` 选择既有私有 Web 会话，值为 `null` 时首次创建，重试返回已选会话。另选一个有效会话时返回 `409 owa_main_session_exists`。跨 Agent、委派子会话和 QQ 会话不参与选择。`v2_0057` 增补选择标记和唯一索引。旧执行回执继续可查，升级后首次新受理按显式选择决定路由。[HTTP](../../../agent/src/omni/adapters/http_routes/omniweave.py) · [迁移](../../../agent/src/omni/adapters/persistence/migrations/versions/v0057.py)

### Phase 2 验收

Windows x64 上对源码 `d40bf6b5` 跑了 43 个文件，373 项通过。一项 V28 历史种子碰到当时尚未建立的 Worker 表，在 `60a242df` 修正种子适配后复验 36 项全绿。两轮生产源码及依赖一致，合并去重后覆盖 379 项。已有 212 条 OpenAPI 路径的定义相同，新增一条进度查询。502 个文件的严格类型检查通过。空库、V53 和 V54 旧库副本均完成迁移核对。本批覆盖为离线专项。[Phase 2 验收证据](../evidence/owa-phase2-2026-11-26.json)

正式规格对齐批 `57990c47` 在 Windows x64 的十个受影响文件中通过 109 项离线专项。包含三 Worker 阻塞时实际处理主会话、独立第四个 Worker 排队、伪造 Router 文件调用、Worker 私有记忆隔离、旧委派和重启回归。严格类型检查覆盖 502 个文件，零错误、零警告。迁移头为 `v2_0055`。[对齐批证据](../evidence/owa-spec-alignment-2026-11-27.json)

容量批 `588efa89` 的 6 项生产入口与 OWA 迁移专项通过，验证八 Worker 入队、超限拒绝、三个同时活动和空库与 V53/V54/V55 副本完整性。另有 58 项旧委派、发布迁移与历史升级专项通过，两组含重复验证的 OWA 升级用例。该批迁移头为 `v2_0056`，严格类型检查覆盖 505 个文件，零错误、零警告。[容量批证据](../evidence/owa-worker-capacity-2026-11-27.json)

显式主会话批 `c9c47162` 通过 45 项生产入口、工作台、架构、事件流和委派专项，另有 13 项历史迁移回归通过。空库及 V53、V54、V55、V56 副本迁移至 `v2_0057`，完整性与外键检查通过。V56 已受理的 Router 回执保留，后续从明确选定的会话接收新 Router 回合。严格类型检查覆盖 506 个文件，零错误、零警告。[主会话批证据](../evidence/owa-main-session-2026-11-27.json)

## Phase 3 续接边界

已领取或刚进入 `running` 的 OWA Worker，在模型、动作和投递账本均无记录时，重启后用同一 Run 和工作项恢复排队。实例恢复释放旧租约。恢复 `dispatch` 后由当前进程重新核对 Ticket、会话、模型和策略，再领取工作。处在 `off` 或 `observe` 时可查询原计划，工作保持排队。有派发记录或待批准的中断尝试继续走原账本核查。

源码 `8a422751` 的 46 项离线专项通过，覆盖跨 boot 领取、重复重启、未知调用保留、旧委派与内核检查点。迁移头为 `v2_0055`。[恢复专项证据](../evidence/owa-pre-dispatch-recovery-2026-11-26.json)

正式规格剩余的前置开发是版本化 Worker Profile、根与执行 Mandate。随后实现同根同目标版本的持久邮箱、请求与回复、订阅和版本化授权对象，再接主会话追加、已确认检查点续接与 Router 原位交付。Phase 3 用校园助手离线场景验收。Phase 4–7 依次覆盖长作业与 Watchdog、数字身体、外部事件和发布。发布候选按实际 SHA 执行全量、配对基准、包生命周期、独立机器、真实平台和 72 小时常驻验收。
