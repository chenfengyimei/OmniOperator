# OmniWeave Architecture（OWA）

## Phase 0 基线

2026-11-26 从 Gitee 和 GitHub 的 `v2` 回读 `d897f2867e4b0a25fe961075264854f002c1ee5b`，从该提交创建 `owa-tickets`。Windows 隔离空库的迁移头是 `v2_0053`，共 53 个修订和 151 张声明业务表，`integrity_check=ok`，`foreign_key_check` 无记录。同提交的 GitHub 全量验证 12 个作业通过，Windows 测试共 8753 项通过、10 项跳过。`1a2810ea` 的 8739 项离线通过属于更早的架构冻结。OWA 本批采用离线专项。[同提交全量基线](https://github.com/chenfengyimei/OmniOperator/actions/runs/45801704584) · [历史冻结证据](../evidence/plugin-architecture-final-2026-11-25.json)

主设备检出未包含副设备策划和技术规格两份配套文件。首批使用所有者交接正文规定的 Phase 0–1 合同。

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

在隔离实例使用 `--omniweave-mode dispatch` 启用 Router。每条新受理增加一条作用域绑定的执行授权，过去的观察回执保持原决定。`observe` 记录受理，`off` 为默认配置。

```powershell
uv run --no-sync omni-v2 --home .\temp\build\owa-dispatch serve --omniweave-mode dispatch
```

- Router 在 `agent` 档可调用 `owa_plan` 提交至多四个 Worker。任务用计划内的键声明依赖。计划受理核对依赖无环、已提供的工具、可用队列容量和授权代次，在同一事务创建任务及原 V2 Run。计划回执即时返回，同一计划重投复用原 Ticket。
- Worker 使用独立的内部 Session 和 `owa-workspaces/<run_id>`。能力由编排调用实际提供的工作区文件工具和任务声明共同限定。模型调用、预算、逐次批准、动作回执和资源清理沿用内核。前置任务失败、取消、中断或有待核查结果时，依赖任务结束。结果未知的写入进入现有动作核查。
- Router 可以在原主会话继续接收新输入，并用 `owa_status` 查看本会话的 Ticket 状态和 Worker 提交的结果。现有 `/runs/{run_id}/cancel` 可取消根 Ticket，包括根 Run 已结束后仍在执行的 Worker。读 `GET /api/v1/agents/{agent_id}/omniweave/sessions/{session_id}/tickets` 可按 `run_id` 或 `ticket_id` 查询进度。会话、Run、消息、摘要和事件的通用 Web 路径执行内部作用域检查。
- `v2_0055` 新建执行授权、任务和依赖三张表，数据库触发器校验 Worker 和依赖的 Ticket 绑定。传统 `delegate_task` 和 OWA Worker 共用现有子任务容量与租约，Router 与普通回合保有独立的根回合执行容量。[迁移](../../../agent/src/omni/adapters/persistence/migrations/versions/v0055.py) · [查询入口](../../../agent/src/omni/adapters/http_routes/omniweave.py)

## 后续阶段

Phase 3 在 `owa_tasks` 的 Ticket 绑定上实现双向邮箱与版本化授权产物，再接主会话追加、重启后的消息与任务续接，以及 Router 在原会话交付。恢复依据原 Run 与工作项的持久状态核对授权和外部副作用。后续接长作业与 Watchdog、数字身体和外部事件。发布候选按实际 SHA 单独执行全量、升级回滚、真实平台和 72 小时常驻验收。
