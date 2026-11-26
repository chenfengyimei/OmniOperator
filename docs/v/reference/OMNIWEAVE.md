# OmniWeave Architecture（OWA）

## Phase 0 基线

2026-11-26 从 Gitee 和 GitHub 的 `v2` 回读 `d897f2867e4b0a25fe961075264854f002c1ee5b`，从该提交创建 `owa-tickets`。Windows 隔离空库的迁移头是 `v2_0053`，共 53 个修订和 151 张声明业务表，`integrity_check=ok`，`foreign_key_check` 无记录。同提交的 GitHub 全量验证 12 个作业通过，Windows 测试共 8753 项通过、10 项跳过。`1a2810ea` 的 8739 项离线通过属于更早的架构冻结。OWA 本批采用离线专项。[同提交全量基线](https://github.com/chenfengyimei/OmniOperator/actions/runs/45801704584) · [历史冻结证据](../evidence/plugin-architecture-final-2026-11-25.json)

主设备检出未包含副设备策划和技术规格两份配套文件。首批使用所有者交接正文规定的 Phase 0–1 合同。

| 共用合同 | 生产入口 |
|---|---|
| 所有者 Web 受理与 SSE | `create_app.turn`、`create_app.event_stream` 和 `EventStreams.open`。原有游标、鉴权和回执由现有入口负责。[HTTP](../../../agent/src/omni/adapters/http.py) · [SSE](../../../agent/src/omni/adapters/http_events.py) |
| 执行、队列与重启 | `Application.submit_turn` 与 `_dispatch` 使用同一个 `Database`；`Repositories.accept_turn` 写入 `inbox`、Run 与 `work_items`，`LeaseRepositories.claim_runs` 领取 work；`Repositories.recover` 按持久回执处理重启。[Application](../../../agent/src/omni/application/engine.py) · [受理与恢复](../../../agent/src/omni/adapters/persistence/repository.py) · [租约](../../../agent/src/omni/adapters/persistence/lease_repository.py) |
| 授权、动作与核查 | 审批、动作回执和未知结果核查沿用当前 Run 及其 Agent 作用域。[内核合同](KERNEL.md) · [核查功能组装](../../../agent/src/omni/composition/features/action_reviews.py) |
| 功能登记与启动顺序 | `BUILTIN_KERNEL_FEATURES` 使用 `KernelFeatureContext` 装配单例服务；需要后台工作的功能参考调度在内核恢复后的启动和排空。[功能目录](../../../agent/src/omni/composition/features/catalog.py) · [调度安装器](../../../agent/src/omni/composition/features/schedules.py) |

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

## Phase 2 切入点

以观察 Ticket 的根 Run 与主 Session 绑定为起点，先定义 Router 的计划及 Worker 子项和依赖。继续用功能安装器接入同一个 Application 和工作队列。派发沿用租约、预算、权限代次、逐次批准和原动作核查，执行清理完成后再释放资源。先把内部 Session 与 Worker 文本的访问限制接到服务端公开入口，再允许 Router 派发。验收用例同时执行三个 Worker，并证明 Router 能继续接收所有者在原主会话中的新输入。

Phase 3 接续同 Ticket 的双向邮箱、授权产物版本、追加需求与重启。后续再接长作业与 Watchdog、数字身体和外部事件。发布候选按实际 SHA 单独执行全量、升级回滚、真实平台和 72 小时常驻验收。
