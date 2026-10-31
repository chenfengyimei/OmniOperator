# OmniOperator 数据模型总览

> 本文回答「每张表在哪、归谁管、什么时候清理」。分层与依赖规则以
> [ARCHITECTURE.md](ARCHITECTURE.md) 为唯一权威；目录与磁盘布局见 [STORAGE.md](STORAGE.md)。
> 声明与实现分离是本轮重构（2026-10-31）的硬约束：**表结构只在声明模块出现，
> 行为只在仓储模块出现**，守卫测试防止本文与真实 DDL 漂移。

## 1 两库分工

| 库 | 文件 | 定位 | 连接实现 |
|---|---|---|---|
| memory.db | `data/memory.db` | 对话与记忆（长期资产，用户关心内容） | `memory/store.py::MemoryStore`（RLock + WAL） |
| state.db | `data/state.db` | 运行状态（短生命周期账本、跨进程协调） | `memory/state.py::StateStore`（显式事务 + mixin） |

跨进程互斥、至多一次、原子额度只能靠 SQLite 事务，因此全部运行协调表都在
state.db；对话原文、事实、技能、档案等"丢了用户会心疼"的数据在 memory.db
或文件（skills/、MEMORY.md、产物文件）。

## 2 state.db（声明唯一来源 `memory/state_schema.py`）

运行入口 `StateStore` 只保留连接/WAL/事务原语、schema 迁移、旧 JSON 导入与
健康检查；域方法由三个 mixin 提供；各领域门面只消费注入的 `_tx/_read`。

### 任务与账本

| 表 | 引入版本 | 归属模块 | 生命周期 |
|---|---|---|---|
| `jobs` | v1 | `state_mixin_jobs.py`（JobsMixin） | 用户删除时清理；`cron_outputs` 同事务删除 |
| `job_claims` | v1 | `state_mixin_jobs.py` | TTL 过期滚动清理（领取时顺带删除过期行） |
| `executions` | v1 | `state_mixin_ledger.py`（LedgerMixin） | 滚动保留最近 200 条 |
| `budgets` | v1 | `state_mixin_ledger.py` | 按 (scope, day) 自然滚动；损坏迁移时 fail-closed |
| `cron_outputs` | v11 | `memory/cron_store.py::ExecutionLog` | 任务链真值；`remove_job` 同事务删除 |
| `migrations` | v1 | `state.py`（迁移域） | 幂等记录，只追加 |

### 运行、模型账本与产物

| 表 | 引入版本 | 归属模块 | 生命周期 |
|---|---|---|---|
| `runs` / `run_events` | v4 | `memory/runs.py::RunStore` | 事件按 run 单调 seq；重启对账 interrupted |
| `llm_calls` / `llm_budget` | v5 | `memory/usage.py` | 费用流水与当日预算；`cost_micro=NULL` 表示未知，不是 0 |
| `artifacts` | v6 | `memory/artifacts.py` | `expires_at` 到期由心跳清理；活跃 run 引用不删 |

### 投递与外部能力

| 表 | 引入版本 | 归属模块 | 生命周期 |
|---|---|---|---|
| `outbox` | v7/v10 | `memory/outbox.py::OutboxStore` | 活跃行（pending/sending/unknown）绝不删；终态滚动保留 200 条 |
| `mcp_schemas` | v8 | `memory/mcp_schemas.py::MCPSchemaStore` | 指纹漂移撤权；用户复查后确认 |
| `skill_acks` | v9 | `memory/skill_acks.py::SkillAckStore` | 只前进不后退；技能归档时删除 |
| `qq_messages` / `qq_sent` | v12 | `memory/qq.py` | 去重保留 30 天；容量耗尽拒绝新事件 |
| `publication_drafts` | v13 | `memory/publications.py` | 发布红线账本；unknown 不自动二次发布 |
| `command_jobs` | v14 | `memory/command_jobs.py::CommandJobStore` | 任务终态 + 进程身份；恢复演练覆盖 |

### 基础设施

| 表 | 引入版本 | 归属模块 | 说明 |
|---|---|---|---|
| `meta` | 基础 | `StateStore` | schema 版本等元数据 |
| `resource_leases` | v3 | `state_mixin_leases.py`（LeasesMixin） | TTL 到期自动可再领；崩溃不需人工解锁 |

## 3 memory.db

| 表 | 归属模块 | 说明 |
|---|---|---|
| `sessions` / `messages` / `message_contexts` | `memory/store.py::MemoryStore` | 会话与消息；回合快照随消息同事务 |
| `facts` / `tombstones` | `memory/store.py` | 事实与遗忘墓碑；FTS 触发器维护索引 |
| `profile` | `memory/store.py` | 用户档案（2000 字符限额） |
| `reviews` / `review_runs` | `memory/store.py` | 回合后整理账本与区间去重 |
| `bindings` | `memory/store.py` | 通道用户 ↔ 会话绑定（QQ 等） |
| `todos` | `memory/store.py` | 会话级待办 |
| `summaries` | `memory/store.py` | 持久压缩摘要（区间唯一） |
| `meta` | `memory/store.py` | FTS 分词器版本等 |
| `dialogue_log` | `memory/dialogue.py` | M2 全量对话流水 |
| `session_log` | `memory/session_log.py` | 会话/运行/工具过程流水 |
| `journal_notes` / `journal_runs` | `memory/journal.py` | 每日整理素材 |
| `people` / `person_accounts` / `person_entries` / `owner_migration` | `memory/people.py` | 人物档案与所有者迁移标记 |
| `fact_vectors` | `memory/vectors.py` | sqlite-vec 向量（默认不启用） |

FTS 虚表（`facts_fts` / `messages_fts` / `dialogue_fts` / `session_log_fts`）
随宿主表在各自模块声明，由触发器维护。

## 4 跨库关系与纪律（2026-11-01 实测清单）

**真值方向**：`session_id` 的真值是 memory.db 的 `sessions.id`；state.db 只存
id、不存内容、不级联（SQLite 跨库无外键）。**memory.db 的会话只增不减**
（`MemoryStore` 无会话删除方法），因此这些 id 永远有真值可指，删除清理路径
问题当前不存在。

state.db 引用 `session_id` 的表（从 DDL 动态提取，守卫测试校验本文不漂移）：

| 表 | 引用形态 | 归属校验点 |
|---|---|---|
| `runs` | 列 `session_id` | `RunStore.list_recent(session_id=…)` 过滤 |
| `llm_calls` | 列 `session_id` | 用量统计按会话聚合（`context_cache_usage` 等） |
| `artifacts` | 列 `session_id` | `ArtifactStore.get/list` 跨会话读取显式拒绝（`cross_session`） |
| `publication_drafts` | 列 `session_id` | `claim` 校验会话 + 内容哈希，不匹配拒绝 |
| `command_jobs` | 列 `session_id` | `CommandJobStore.get/list` 会话过滤，跨会话 404 语义 |
| `resource_leases` | **软引用**：键 `session:<id>` 字符串 | TTL 到期自动失效，无清理路径 |

其余 state.db 表（outbox 按 target_qq、qq_messages 按 bot+scene、cron_outputs
按 job）均不引用会话 id，跨库无关。

**是否合并两库（评估结论：不合并）**：
- 全部跨库消费都是「同一 session_id 分别查两个库」的应用层模式，**没有一条
  SQL 需要跨库 JOIN**（`grep` 核实各域模块）。
- 合并的收益（外键、JOIN）为零；代价是单库膨胀、备份粒度变粗（对话记忆与
  运行账本被迫一起备份/恢复）、WAL 写竞争加剧（心跳高频写 state.db，对话写
  memory.db）。保持两库。
- 新表如果将来要存 `session_id`，必须遵守：真值在 memory.db、不级联、写入
  前后不需要读 memory.db（校验归属靠应用层传入的 id）。

其余纪律：
- **金额口径**：`llm_calls.cost_micro` 一律微美元整数；`NULL` 是"算不出来"，
  `0` 是"确定免费"，两者不得混用。
- **状态语义**：`unknown` 永远表示"副作用未知"，不自动重试、不自动当成失败。
- **迁移纪律**：已发布版本（v1–v14）的 SQL 逐字节不变；新结构一律追加版本号，
  搬数据或删旧表的版本必须在 `state_schema.py` 注释里写清语义。

## 5 后续演进

1. 新表接入顺序：`state_schema.py` 声明 → 新版本号迁移 → 归属模块实现 →
   本文档登记 → 守卫测试通过。
2. 新增含 `session_id` 的表时，§4 清单与 `test_data_model.py` 的守卫会同时
   校验——漏登记文档会直接测试红。
3. 守卫测试（`agent/tests/test_data_model.py`）保证本文的 state.db 表清单、
   session_id 引用清单与真实 DDL 一致；改表必须同步改文档。
