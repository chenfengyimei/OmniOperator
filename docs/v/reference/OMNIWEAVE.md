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
- `v2_0058` 为新 Ticket 捕获不可变根 Mandate，为每个 Worker 捕获同目标版本的执行 Mandate。根授权从已认证的所有者 Web 输入和当前策略生成。快照固定主会话、所有者、目标摘要、策略与历史代次、工具规格和读写风险、预算及期限。执行授权绑定 Worker Run 和对应隔离工作区，权限、预算与期限受根授权约束。根期限默认 24 小时，Worker 可指定更短期限。升级时保留已登记 Worker 的原授权，升级前尚无计划的 Router 回合须由所有者提交新回合才能启动新计划。[数据库合同](../../../agent/src/omni/adapters/persistence/migrations/versions/v0058.py) · [运行授权](../../../agent/src/omni/adapters/persistence/omniweave_authority.py)
- `owa_plan` 支持 `research`、`coding`、`verification`、`documentation` 四个 v1 Profile。省略时按 `coding` 处理。当前 Research 与 Verification 只读，Coding 与 Documentation 可在自己的工作区请求文件写入，每次继续走 V2 逐次审批。任务视图公开 Profile 名称和版本及目标版本。领取、发送模型请求和工具派发复核当时权限，授权到期后已批准的动作也会被拦截。终端、外部来源和 Execution Profile 随后续阶段接入。[Profile 合同](../../../agent/src/omni/domain/omniweave.py)

### Phase 2 验收

Windows x64 上对源码 `d40bf6b5` 跑了 43 个文件，373 项通过。一项 V28 历史种子碰到当时尚未建立的 Worker 表，在 `60a242df` 修正种子适配后复验 36 项全绿。两轮生产源码及依赖一致，合并去重后覆盖 379 项。已有 212 条 OpenAPI 路径的定义相同，新增一条进度查询。502 个文件的严格类型检查通过。空库、V53 和 V54 旧库副本均完成迁移核对。本批覆盖为离线专项。[Phase 2 验收证据](../evidence/owa-phase2-2026-11-26.json)

正式规格对齐批 `57990c47` 在 Windows x64 的十个受影响文件中通过 109 项离线专项。包含三 Worker 阻塞时实际处理主会话、独立第四个 Worker 排队、伪造 Router 文件调用、Worker 私有记忆隔离、旧委派和重启回归。严格类型检查覆盖 502 个文件，零错误、零警告。迁移头为 `v2_0055`。[对齐批证据](../evidence/owa-spec-alignment-2026-11-27.json)

容量批 `588efa89` 的 6 项生产入口与 OWA 迁移专项通过，验证八 Worker 入队、超限拒绝、三个同时活动和空库与 V53/V54/V55 副本完整性。另有 58 项旧委派、发布迁移与历史升级专项通过，两组含重复验证的 OWA 升级用例。该批迁移头为 `v2_0056`，严格类型检查覆盖 505 个文件，零错误、零警告。[容量批证据](../evidence/owa-worker-capacity-2026-11-27.json)

显式主会话批 `c9c47162` 通过 45 项生产入口、工作台、架构、事件流和委派专项，另有 13 项历史迁移回归通过。空库及 V53、V54、V55、V56 副本迁移至 `v2_0057`，完整性与外键检查通过。V56 已受理的 Router 回执保留，后续从明确选定的会话接收新 Router 回合。严格类型检查覆盖 506 个文件，零错误、零警告。[主会话批证据](../evidence/owa-main-session-2026-11-27.json)

Profile 与 Mandate 批 `5e7baa36` 通过 64 项生产入口与内核专项、59 项历史迁移和 1 项快照恢复回归。真实 HTTP 调用替身证明，所有者已批准的写入在派发前遇到授权到期时不启动动作。空库和 V53 至 V57 副本迁移至 `v2_0058`，严格类型检查覆盖 508 个文件，零错误、零警告。[授权批证据](../evidence/owa-worker-mandates-2026-11-27.json)

## Phase 3 持久邮箱与续接

`v2_0059` 将 Worker 的稳定任务身份与 Run 轮次关联。每个轮次固定目标版本，沿用原 Worker Session、工作区和 Mandate。所有轮次的模型花费与调用次数累计进同一个 Worker 和根 Ticket 账本。重启继续使用既有租约与回执处理边界。[Worker 轮次](../../../agent/src/omni/adapters/persistence/omniweave_attempts.py) · [迁移](../../../agent/src/omni/adapters/persistence/migrations/versions/v0059.py)

- Router 在计划中以 `peers` 声明单向请求范围。Worker 仅能向同一根 Ticket 中被授权的对端调用 `request_peer`，只有实际收到原请求的对端可以 `reply_peer`。请求与回复均检查目标版本。请求同键同载荷重用回执，异载荷冲突，首个终态回复关闭请求。
- 阻塞请求和计划依赖合并查环。当前上限为每根每版本 16 个请求、每个发件者 4 个未关闭请求和 4 层因果深度。默认期限 5 分钟，同时受双端 Mandate 期限约束。到期给请求方写入可接续的超时结果。
- 前一轮成功并且没有待处理动作、未知账本或持有租约后，邮箱将待处理消息、确认回执和新轮次写入同一事务。回复早到时先持久保存，等原轮次结束再投给同一 Worker。确认前重启可重新投递。确认后重启沿用已排队的 Run。公共 Ticket 投影显示 `waiting_peer`，内部消息和 Run 维持私有范围。[邮箱](../../../agent/src/omni/adapters/persistence/omniweave_mailbox.py)

已领取或刚进入 `running` 的 OWA Worker，在模型、动作和投递账本均无记录时，重启后用同一 Run 和工作项恢复排队。实例恢复释放旧租约。恢复 `dispatch` 后由当前进程重新核对 Ticket、会话、模型和策略，再领取工作。处在 `off` 或 `observe` 时可查询原计划，工作保持排队。有派发记录或待批准的中断尝试继续走原账本核查。

源码 `8a422751` 的 46 项离线专项通过，覆盖跨 boot 领取、重复重启、未知调用保留、旧委派与内核检查点。迁移头为 `v2_0055`。[恢复专项证据](../evidence/owa-pre-dispatch-recovery-2026-11-26.json)

邮箱首批 `d04f8f8e` 通过 74 项生产入口和内核专项，历史迁移 59 项通过、1 项失败。`3cd651ec` 修正早期图像库的根回合领取查询，12 项修复复验通过。按用例去重合计 134 项通过，分批覆盖范围和 SHA 随证据保存。最终严格类型检查覆盖 511 个文件，零错误、零警告。[邮箱批证据](../evidence/owa-peer-mailbox-2026-11-27.json)

### 版本化产物与订阅

- `owa_plan` 用 `artifact_readers` 批准同一根 Ticket 内的生产者与读者关系。生产者用 `publish_artifact` 发布到获准读者的子集，`expected_version` 核对当前版本。同键同载荷返回原发布回执，每根每目标版本最多发布 32 次。目前对象为至多 16 KiB 的 UTF-8 文本，可承载摘要、结构化证据和文本补丁。[工具合同](../../../agent/src/omni/domain/omniweave_artifacts.py)
- `v2_0060` 在现有对象库中保存不可变内容，版本、源 Run、动作、授权读者及通知回执进入同一数据库。`subscribe_artifacts` 控制后续发布通知，关闭订阅会使未消费的旧通知失效。通知携带版本引用，经原 Worker 内会话接续。`read_shared` 在读取前后核对同根、双方目标版本、来源与共享授权。新版替换旧版的可读资格。[数据库合同](../../../agent/src/omni/adapters/persistence/migrations/versions/v0060.py) · [产物仓储](../../../agent/src/omni/adapters/persistence/omniweave_artifacts.py)
- 主会话 Ticket 进度返回当前产物元数据与读者键。所有者可用 `DELETE /api/v1/agents/{agent_id}/omniweave/sessions/{session_id}/artifacts/{artifact_id}/shares/{reader_key}` 撤回该读者对该版本的访问。来源擦除后产物退出当前投影。普通对象列表与下载入口排除 OWA 私有对象。[HTTP](../../../agent/src/omni/adapters/http_routes/omniweave.py)

产物批 `f1b9a261` 通过 82 项生产入口、对象和委派专项及 16 项截屏调用方回归。历史迁移首轮 58 项通过、1 项失败，`b6a26088` 修正 V49 普通对象引用统计后，20 项修复复验通过。合计 157 个不同用例通过。验证覆盖投递前取消订阅、旧版拒读、对象读取中撤权、重启、通用对象入口拒绝及来源擦除。该批迁移头为 `v2_0060`。严格类型检查覆盖 514 个文件，零错误、零警告。[产物批证据](../evidence/owa-artifact-subscriptions-2026-11-27.json)

### 主会话追加与目标修订

- 所有者可在主会话的输入栏选择一个已规划的 Ticket，查看它的标题和当前目标版本，再发送补充要求。发生版本冲突时，工作台刷新候选并保留输入草稿，用户核对版本后可再次发送。[工作台入口](../../../web/ui/v2/main.js)
- `POST /api/v1/agents/{agent_id}/turns` 接受可选的 `goal_update: {ticket_id, expected_goal_version}`。它要求启用 dispatch、显式指定已选的主会话并提供文字。原有 HTTP 202 回执继续使用 `run_id`、`session_id` 和 `duplicate`。同键重试按完整请求指纹返回原回执。不同键引用同一旧版本时，后到的事务返回 `409 revision_conflict`。已取消的目标从新请求启动。[HTTP](../../../agent/src/omni/adapters/http.py) · [准入事务](../../../agent/src/omni/adapters/persistence/omniweave_repository.py)
- `v2_0061` 为同一个公开 Ticket 保存最多 64 个连续的目标版本。新增版本、源消息、路由、授权、旧工作取消和审批撤回在同一事务。Task 投影和 Router 查询只取当前版本，历史对象和账本保留原归属。当前修订按整组受影响处理，重新规划 Worker，分别使用新 Mandate、私有 Session 和工作区。所有版本共享根 Ticket 调用账本。[迁移](../../../agent/src/omni/adapters/persistence/migrations/versions/v0061.py) · [跨版本账本](../../../agent/src/omni/adapters/persistence/omniweave_goals.py)
- 新 Router 可以在主会话确认追加。新 Worker 等先前执行和租约退出，未知动作、费用与投递回执留在原账本。投影返回 `prior_work: draining | needs_review | null`。已完成的动作核查可解锁调度，旧动作不会自动重发。[调度闸门](../../../agent/src/omni/adapters/persistence/omniweave_repository.py)

源码 `32793f77` 通过 98 项生产入口、历史迁移与浏览器专项。另有 2 项类型清单守卫通过，严格类型检查覆盖 516 个文件，零错误、零警告。以上均为本批专项，完整套件待候选集成收尾统一运行。[目标修订批证据](../evidence/owa-goal-updates-2026-11-27.json)

### Router 原位交付

- `v2_0062` 为每个目标版本保留一个最终交付回执。现有调度器领取确定性的 Router 收尾回合，沿用原主会话和输入消息。收尾回合使用现有 Run、队列与会话租约。计划确认和最终交付分别记录。关闭 dispatch 后，已排队的交付等待恢复，普通对话可继续。[迁移](../../../agent/src/omni/adapters/persistence/migrations/versions/v0062.py) · [归约](../../../agent/src/omni/adapters/persistence/omniweave_delivery.py)
- 系统等必需 Worker 和可消费邮箱结束，检查动作、计费和投递账本。验证 Worker 获准读取的现行产物需要匹配的读取回执。交付前沿现有对象存储复验内容摘要，提交前在同一事务重验当前目标、证据范围和租约。已核验与缺失项由账本归约为完成、部分完成或未完成。未知结果保留原核查流程。交付消息、Run 终态和回执原子写入。[服务](../../../agent/src/omni/application/omniweave.py)
- 工作台通过现有会话消息流显示交付与文本产物下载。下载在读取前后复核所有者令牌、主会话、版本和来源。修订前的版次继续保留在审计账本，来源擦除使旧授权退出可下载范围。收尾回合输出结构化工作结论与已登记产物。[HTTP](../../../agent/src/omni/adapters/http_routes/omniweave.py) · [工作台](../../../web/ui/v2/main.js)

接续目标是消息中的授权证据引用、显式保留未受修订影响的证据，以及外部动作已有明确回执后的检查点续接。Phase 3 用校园助手离线场景验收完整链路。Phase 4–7 依次覆盖长作业与 Watchdog、数字身体、外部事件和发布。发布候选按实际 SHA 执行全量、配对基准、包生命周期、独立机器、真实平台和 72 小时常驻验收。

### 证据引用与回执续接

- `v2_0063` 登记显式沿用与对等消息引用。`request_peer` 与 `reply_peer` 可携带最多 16 条 `{artifact_id, version}` 引用，提交时逐条校验发送方与收件方均为生产者或持有未撤销授权读取回执，且指向现行版本；引用并入幂等指纹，审计记录引用清单。[迁移](../../../agent/src/omni/adapters/persistence/migrations/versions/v0063.py) · [邮箱](../../../agent/src/omni/adapters/persistence/omniweave_mailbox.py)
- 实际投递前在同一事务复核每条引用：产物仍是现行版本、发布动作未被擦除、生产者 Mandate 与版本匹配、发送方与收件方授权仍然有效。授权撤回、产物被替代或来源被擦除后，待投递消息的事件不再携带对应产物的名称、ID 或哈希。
- 修订后的计划任务可声明 `reuse: {task_key, goal_version}` 显式沿用先前版本已验收的结果。登记校验同根家族、较早版本、最新尝试终结、核验通过且来源 Mandate 仍受当前策略、所有者与会话约束；复用任务不得再声明工具、期限、对等与读取范围，其余任务不得依赖或引用复用键。[计划](../../../agent/src/omni/adapters/persistence/omniweave_repository.py)
- 交付归约在同一事务复核沿用证据：来源运行仍成功、核验回执仍为 verified/not_required、来源授权仍有效。沿用核验任务与现行核验任务一样，须对保留产物的授权读取回执完成覆盖，授权撤回或读取回执缺失时任务保持待核验，交付按部分完成或未完成归约，沿用产物以"沿用证据"列入收尾并保持可下载，未沿用的旧产物仍按修订语义退出。[归约](../../../agent/src/omni/adapters/persistence/omniweave_delivery.py)
- OWA Worker 与其他运行一样保存检查点：委派恢复对带检查点的 Worker 延迟终态，交给通用续接；`safe_resume` 对 OWA Worker 开放并随父路由策略传入子运行路由，邮箱触发的后续回合沿用同一已捕获策略。崩溃重启后，已结算的外部动作从记录回执续接而不重复执行，Worker 完成后交付照常在原主会话提交一次。[恢复](../../../agent/src/omni/adapters/persistence/delegation_repository.py) · [续接测试](../../../agent/tests/v2/http/test_v2_omniweave_receipt_resume.py)
