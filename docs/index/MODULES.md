# 模块与源码索引

> 由 `uv run --no-sync python scripts/project_index.py` 生成。不要手改；分类改 system/catalog/schema.py，分层改 ARCHITECTURE.md。

源码指纹：`7929a848618d3f76204a496b63486720653de9f5f30cb912f101ea2c49c3d668`。包含 57 个职责模块、296 个源码/界面文件。

## 项目存储与 Agent 目录 · agent-directories

分类：平台与数据。全局与独立空间目录解析、CAS 配置、运维路径解析与用途索引；不构造执行对象或搬移数据。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/system/agent_spaces/__init__.py](../../agent/src/omni/system/agent_spaces/__init__.py) | 13 | Agent 目录注册与纯路径解析；执行对象仍由 bootstrap 构造。 |
| [agent/src/omni/system/agent_spaces/catalog.py](../../agent/src/omni/system/agent_spaces/catalog.py) | 136 | 配置注册表：有界读取、CAS、跨进程互斥、原子写；不搬运用户数据。 |
| [agent/src/omni/system/agent_spaces/inspection.py](../../agent/src/omni/system/agent_spaces/inspection.py) | 50 | 只读运维入口共用的路径选择，不组装服务、不建库、不创建目录。 |
| [agent/src/omni/system/agent_spaces/inventory.py](../../agent/src/omni/system/agent_spaces/inventory.py) | 43 | 路径用途索引：纯声明，不扫描磁盘或读取记忆、凭据内容。 |
| [agent/src/omni/system/agent_spaces/layout.py](../../agent/src/omni/system/agent_spaces/layout.py) | 216 | 本机 Agent 布局的声明与校验，不创建目录或打开数据。 |

文档：[docs/STORAGE.md](../../docs/STORAGE.md)、[docs/AGENT_STORAGE.md](../../docs/AGENT_STORAGE.md)

测试：[agent/tests/test_agent_storage.py](../../agent/tests/test_agent_storage.py)、[agent/tests/test_storage_cli.py](../../agent/tests/test_storage_cli.py)、[agent/tests/test_application_storage.py](../../agent/tests/test_application_storage.py)

直接静态依赖：`config`、`platform`

## 项目目录与文件管理 · http-storage

分类：Web 接口。全局与 Agent 目录设置、运行与待重启路径、用途索引及当前空间公开文件区的有界浏览和人工上传。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/storage.py](../../agent/src/omni/channels/web_api/storage.py) | 162 | Agent 目录设置和当前公开文件区；只消费组装根注入的目录与路径策略。 |
| [agent/src/omni/channels/web_static/storage/index.html](../../agent/src/omni/channels/web_static/storage/index.html) | 82 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/storage/storage.css](../../agent/src/omni/channels/web_static/storage/storage.css) | 128 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/storage/storage.js](../../agent/src/omni/channels/web_static/storage/storage.js) | 89 | 平台脚本或界面资源 |

文档：[docs/STORAGE.md](../../docs/STORAGE.md)、[docs/AGENT_STORAGE.md](../../docs/AGENT_STORAGE.md)

测试：[agent/tests/test_storage_settings.py](../../agent/tests/test_storage_settings.py)、[agent/tests/test_agent_storage_ui.py](../../agent/tests/test_agent_storage_ui.py)

直接静态依赖：`agent-directories`、`assembly`、`files`、`platform`、`toolkit`

## 上下文设置与统计 · http-context

分类：Web 接口。设置中心的只读窗口统计、供应商缓存样本和带版本检查的配置保存；不触发模型。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/context.py](../../agent/src/omni/channels/web_api/context.py) | 82 | 上下文设置与只读统计；不调用模型、不构造服务、不修改运行中的配置。 |
| [agent/src/omni/channels/web_static/ui/app/hub-context.js](../../agent/src/omni/channels/web_static/ui/app/hub-context.js) | 134 | 平台脚本或界面资源 |

文档：[docs/CONTEXT_SETTINGS.md](../../docs/CONTEXT_SETTINGS.md)、[docs/PROMPT_CONTEXT.md](../../docs/PROMPT_CONTEXT.md)

测试：[agent/tests/test_context_settings.py](../../agent/tests/test_context_settings.py)

直接静态依赖：`assembly`、`config`、`platform`、`storage`

## 运行时组装 · assembly

分类：内核与模型。唯一组装根，构造依赖并暴露 Runtime 门面。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/bootstrap.py](../../agent/src/omni/bootstrap.py) | 767 | 组装根（Composition Root）：全项目唯一负责装配的地方。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)、[docs/REPOSITORY_STRUCTURE.md](../../docs/REPOSITORY_STRUCTURE.md)

测试：[agent/tests/test_paths.py](../../agent/tests/test_paths.py)、[agent/tests/test_repository_layout.py](../../agent/tests/test_repository_layout.py)

直接静态依赖：`agent-directories`、`android`、`bilibili`、`browser`、`command-jobs`、`community`、`config`、`delegation`、`desktop`、`engine`、`files`、`mcp`、`models`、`personal`、`platform`、`plugins`、`qq-tools`、`safety`、`scheduling`、`shell`、`skills`、`storage`、`system-tools`、`toolkit`、`web-fetch`

## 配置与路径契约 · config

分类：平台与数据。配置模型、加载与 RuntimePaths；不包含业务编排。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/config.py](../../agent/src/omni/config.py) | 737 | 配置加载：.env 密钥 + config.yaml 主配置 + 运行期路径表。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_config.py](../../agent/tests/test_config.py)

直接静态依赖：无内部模块依赖

## 提示词与上下文 · prompt-context

分类：内核与模型。稳定系统前缀、回合上下文快照渲染与保留当前请求的摘要边界。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/core/compress.py](../../agent/src/omni/core/compress.py) | 328 | 上下文压缩 + 消息不变量（Hermes compression_facade 的最小版，NOTES §1.2）。 |
| [agent/src/omni/core/context.py](../../agent/src/omni/core/context.py) | 133 | 缓存友好的提示词布局：固定规则在前，每轮快照附于该轮用户正文末尾。 |

文档：[docs/PROMPT_CONTEXT.md](../../docs/PROMPT_CONTEXT.md)、[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_prompt_context.py](../../agent/tests/test_prompt_context.py)、[agent/tests/test_context.py](../../agent/tests/test_context.py)、[agent/tests/test_compress.py](../../agent/tests/test_compress.py)

直接静态依赖：`models`

## Agent 内核 · engine

分类：内核与模型。回合、上下文、工具循环、取消、委派约束、review 与调度。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/core/__init__.py](../../agent/src/omni/core/__init__.py) | 1 | Agent 核心：编排循环。工具注册表在 tools 包，审批在 safety 包。 |
| [agent/src/omni/core/agent.py](../../agent/src/omni/core/agent.py) | 1396 | Agent 核心循环：组装上下文 → 调 LLM → 执行工具 → 直到给出回答。 |
| [agent/src/omni/core/cron.py](../../agent/src/omni/core/cron.py) | 440 | 心跳调度器：tick 循环 + 至多一次派发 + 任务执行/投递（Hermes cron 最小版）。 |
| [agent/src/omni/core/evaluation.py](../../agent/src/omni/core/evaluation.py) | 24 | 固定样本检索评测；不把 mock 模型的排序表现当成真实语义收益。 |
| [agent/src/omni/core/journal.py](../../agent/src/omni/core/journal.py) | 371 | 记忆系统整理编排：日/周/月笔记的模型整理 worker（与 TurnEndReviewer 同构）。 |
| [agent/src/omni/core/modes.py](../../agent/src/omni/core/modes.py) | 211 | 按回合能力模式（chat / retrieval / agent）的声明与纯函数。 |
| [agent/src/omni/core/qq_policy.py](../../agent/src/omni/core/qq_policy.py) | 37 | 主动提醒策略：显式所有者、安静时段和独立每日额度；不做网络发送。 |
| [agent/src/omni/core/retrieval.py](../../agent/src/omni/core/retrieval.py) | 87 | FTS + 向量 RRF 融合；后台有界增量索引，网络请求不占数据库事务。 |
| [agent/src/omni/core/review.py](../../agent/src/omni/core/review.py) | 246 | 回合后异步 review：自进化最小闭环（Hermes background_review 的 P1 简化版， |
| [agent/src/omni/core/router.py](../../agent/src/omni/core/router.py) | 164 | 回合能力模式路由：先规则、后模型、最后回退。 |
| [agent/src/omni/core/run.py](../../agent/src/omni/core/run.py) | 274 | 运行（run）的身份、取消信号与上下文（D09）。 |
| [agent/src/omni/core/session.py](../../agent/src/omni/core/session.py) | 147 | 会话作用域：同一会话串行、不同会话并行、排队有界、跨进程有租约。 |
| [agent/src/omni/core/vision.py](../../agent/src/omni/core/vision.py) | 90 | 工具产物 → 模型图像块（D15/A13）。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_agent.py](../../agent/tests/test_agent.py)、[agent/tests/test_runs.py](../../agent/tests/test_runs.py)、[agent/tests/test_parallel_tools.py](../../agent/tests/test_parallel_tools.py)

直接静态依赖：`config`、`models`、`prompt-context`、`qq-tools`、`safety`、`storage`、`toolkit`

## 模型网关与 embedding · models

分类：内核与模型。模型路由、能力、重试、流式、预算与 embedding 传输。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/llm/__init__.py](../../agent/src/omni/llm/__init__.py) | 1 | LLM 层：LiteLLM 统一网关。 |
| [agent/src/omni/llm/budget.py](../../agent/src/omni/llm/budget.py) | 174 | 费用预留与结算的契约（D10）。 |
| [agent/src/omni/llm/embedding.py](../../agent/src/omni/llm/embedding.py) | 116 | 可切换 embedding 出口：本地 Ollama / LiteLLM API；费用账本从 bootstrap 注入。 |
| [agent/src/omni/llm/errors.py](../../agent/src/omni/llm/errors.py) | 206 | 模型调用的错误分类与脱敏（D10）。 |
| [agent/src/omni/llm/gateway.py](../../agent/src/omni/llm/gateway.py) | 716 | LiteLLM 统一网关：一套 OpenAI 格式接所有供应商（海外/国产/本地 Ollama）。 |
| [agent/src/omni/llm/usage.py](../../agent/src/omni/llm/usage.py) | 208 | 用量（tokens）与价格：把"这次调用大概/实际花了多少"变成可记账的数字。（D10） |

文档：[docs/RETRIEVAL.md](../../docs/RETRIEVAL.md)

测试：[agent/tests/test_gateway.py](../../agent/tests/test_gateway.py)、[agent/tests/test_usage.py](../../agent/tests/test_usage.py)

直接静态依赖：`config`

## 审批与权限 · safety

分类：内核与模型。风险分级、人工审批与无人值守拒绝。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/safety/__init__.py](../../agent/src/omni/safety/__init__.py) | 1 | 安全层：审批闸门（工具风险分级 + 按上下文选择审批策略）。 |
| [agent/src/omni/safety/broker.py](../../agent/src/omni/safety/broker.py) | 116 | 审批代理：跨线程确认流（Web 工作台用）。 |
| [agent/src/omni/safety/gates.py](../../agent/src/omni/safety/gates.py) | 146 | 审批闸门：工具风险分级 + 按上下文选择审批策略（Strategy 模式）。 |
| [agent/src/omni/safety/smart.py](../../agent/src/omni/safety/smart.py) | 83 | smart 审批档（Hermes approval_smart 同款）：辅助 LLM 先判风险，拿不准再问人。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_safety.py](../../agent/tests/test_safety.py)

直接静态依赖：无内部模块依赖

## 记忆与运行账本 · storage

分类：平台与数据。会话/事实/技能/产物及 state.db 事务、迁移、额度、投递。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/memory/__init__.py](../../agent/src/omni/memory/__init__.py) | 1 | 记忆层：SQLite 持久化（P0 版；P1 升级向量检索与三层记忆）。 |
| [agent/src/omni/memory/artifacts.py](../../agent/src/omni/memory/artifacts.py) | 296 | 产物存储（D15 / A13）：截图、文件、报告统一按 artifact_id 管理。 |
| [agent/src/omni/memory/command_jobs.py](../../agent/src/omni/memory/command_jobs.py) | 46 | 命令任务元数据仓储；不执行或终止操作系统进程。 |
| [agent/src/omni/memory/cron.py](../../agent/src/omni/memory/cron.py) | 61 | 定时任务模型与存储的公开入口（保留旧导入路径）。 |
| [agent/src/omni/memory/cron_store.py](../../agent/src/omni/memory/cron_store.py) | 106 | 定时任务域的两个门面（`JobStore` / `ExecutionLog`）。 |
| [agent/src/omni/memory/dialogue.py](../../agent/src/omni/memory/dialogue.py) | 388 | 对话全量记忆（M1）：完整对话文本一字不差的追加型存档。 |
| [agent/src/omni/memory/guard.py](../../agent/src/omni/memory/guard.py) | 130 | 记忆写入的确定性闸门（D12 / A15）。 |
| [agent/src/omni/memory/jobs.py](../../agent/src/omni/memory/jobs.py) | 138 | 定时任务模型与调度时间（纯数据 + 纯函数，不碰存储）。 |
| [agent/src/omni/memory/journal.py](../../agent/src/omni/memory/journal.py) | 420 | 笔记记忆（M3/M4/M5）：每日/每周/每月笔记的存储与到期判定。 |
| [agent/src/omni/memory/mcp_schemas.py](../../agent/src/omni/memory/mcp_schemas.py) | 77 | MCP 工具目录指纹仓储（D19，`state.db`）。 |
| [agent/src/omni/memory/outbox.py](../../agent/src/omni/memory/outbox.py) | 242 | QQ 投递 outbox 仓储（`state.db`）。 |
| [agent/src/omni/memory/owner.py](../../agent/src/omni/memory/owner.py) | 207 | 所有者记忆门面：档案行与常驻笔记统一进人物系统后的注入与维护面。 |
| [agent/src/omni/memory/owner_migrate.py](../../agent/src/omni/memory/owner_migrate.py) | 151 | 所有者记忆一次性迁移：profile 表与 MEMORY.md → owner 人物条目。 |
| [agent/src/omni/memory/people.py](../../agent/src/omni/memory/people.py) | 608 | 核心人物记忆（M6）：重要人物的账号、常驻档案、常驻记忆与临时事件。 |
| [agent/src/omni/memory/publications.py](../../agent/src/omni/memory/publications.py) | 88 | 人工发布草稿与发送身份账本；进程中断/结果未知不自动二次发布。 |
| [agent/src/omni/memory/qq.py](../../agent/src/omni/memory/qq.py) | 74 | QQ 入站去重、撤回墓碑与回复身份；只保存 ID/状态，不保存原文。 |
| [agent/src/omni/memory/resident.py](../../agent/src/omni/memory/resident.py) | 373 | 常驻小记忆（Hermes MEMORY.md 式，NOTES §1.3a）：跨会话冻结快照注入系统提示词。 |
| [agent/src/omni/memory/revisions.py](../../agent/src/omni/memory/revisions.py) | 241 | 内容修订历史（D08）：技能与常驻记忆的"可回滚快照"。 |
| [agent/src/omni/memory/runs.py](../../agent/src/omni/memory/runs.py) | 388 | 运行记录（run）与事件流（`state.db`）：一次"问—想—做—答"的可查账本。 |
| [agent/src/omni/memory/session_log.py](../../agent/src/omni/memory/session_log.py) | 409 | 会话全量记忆（M2）：对话 + 工具过程的完整流水（比 M1 多工具与系统边界事件）。 |
| [agent/src/omni/memory/skill_acks.py](../../agent/src/omni/memory/skill_acks.py) | 57 | 技能变更「已读」标记仓储（D18，`state.db`）。 |
| [agent/src/omni/memory/skill_inbox.py](../../agent/src/omni/memory/skill_inbox.py) | 547 | 外来技能包隔离安装（D18 后置项）：inbox 收包 → 隔离区校验 → 用户激活。 |
| [agent/src/omni/memory/skills.py](../../agent/src/omni/memory/skills.py) | 1049 | 技能存储（程序性记忆）：skills/<name>/SKILL.md，agentskills.io 兼容子集。 |
| [agent/src/omni/memory/state.py](../../agent/src/omni/memory/state.py) | 479 | 共享运行状态（`data/state.db`）：定时任务、执行账本、额度与迁移记录。 |
| [agent/src/omni/memory/state_errors.py](../../agent/src/omni/memory/state_errors.py) | 43 | state.db 的异常语义与错误翻译（唯一来源）。 |
| [agent/src/omni/memory/state_mixin_jobs.py](../../agent/src/omni/memory/state_mixin_jobs.py) | 262 | 定时任务域的 `state.db` 访问 mixin（任务定义 / revision / 原子领取）。 |
| [agent/src/omni/memory/state_mixin_leases.py](../../agent/src/omni/memory/state_mixin_leases.py) | 121 | 资源租约域的 `state.db` 访问 mixin（会话/浏览器 profile 等互斥资源）。 |
| [agent/src/omni/memory/state_mixin_ledger.py](../../agent/src/omni/memory/state_mixin_ledger.py) | 85 | 执行账本与写额度域的 `state.db` 访问 mixin。 |
| [agent/src/omni/memory/state_records.py](../../agent/src/omni/memory/state_records.py) | 55 | state.db 各域仓储返回值的数据形状（唯一来源）。 |
| [agent/src/omni/memory/store.py](../../agent/src/omni/memory/store.py) | 1151 | SQLite 记忆存储：会话 / 消息 / 事实 / 用户档案 / 全文检索 / review 日志。 |
| [agent/src/omni/memory/store_schema.py](../../agent/src/omni/memory/store_schema.py) | 155 | memory.db 的表结构、FTS 索引声明与额度常量（唯一来源）。 |
| [agent/src/omni/memory/system.py](../../agent/src/omni/memory/system.py) | 61 | 记忆系统门面：六个记忆模块对外的单点聚合（组合根纪律，无业务逻辑）。 |
| [agent/src/omni/memory/usage.py](../../agent/src/omni/memory/usage.py) | 242 | 模型调用的用量与费用账本（`state.db`，D10）。 |
| [agent/src/omni/memory/vectors.py](../../agent/src/omni/memory/vectors.py) | 93 | sqlite-vec 派生索引。与事实同库；遗忘/纠错触发器只用普通 SQL，无扩展依赖。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)、[docs/RETRIEVAL.md](../../docs/RETRIEVAL.md)

测试：[agent/tests/test_state.py](../../agent/tests/test_state.py)、[agent/tests/test_memory.py](../../agent/tests/test_memory.py)

直接静态依赖：`command-contracts`、`config`、`platform`

## 内置终端 · terminal

分类：交互与终端。ConPTY 后端、会话、会话池、WebSocket 四层；与 agent shell 工具分工。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/terminal/__init__.py](../../agent/src/omni/channels/terminal/__init__.py) | 6 | 工作台终端公共入口；平台适配、会话、会话池和 WS 路由分别维护。 |
| [agent/src/omni/channels/terminal/backend.py](../../agent/src/omni/channels/terminal/backend.py) | 37 | ConPTY 平台适配：检测可用性与创建进程；不管理会话池或 HTTP。 |
| [agent/src/omni/channels/terminal/managed.py](../../agent/src/omni/channels/terminal/managed.py) | 75 | 可恢复的人类终端：持续排空、有限输出环与单写者；不向模型暴露。 |
| [agent/src/omni/channels/terminal/pool.py](../../agent/src/omni/channels/terminal/pool.py) | 132 | 终端会话池：并发上限、关闭预算和残留报告。 |
| [agent/src/omni/channels/terminal/routes.py](../../agent/src/omni/channels/terminal/routes.py) | 80 | 内置终端 WebSocket 适配；复用工作台身份及注入的会话池。 |
| [agent/src/omni/channels/terminal/session.py](../../agent/src/omni/channels/terminal/session.py) | 44 | 单终端会话的读、写、窗口尺寸和退出确认。 |
| [agent/src/omni/channels/terminal/sessions.py](../../agent/src/omni/channels/terminal/sessions.py) | 138 | 人工终端 v2：身份归属、结构化消息及只回放输出。旧 WS 独立兼容。 |
| [agent/src/omni/channels/web_static/terminal/index.html](../../agent/src/omni/channels/web_static/terminal/index.html) | 51 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/terminal/terminal.css](../../agent/src/omni/channels/web_static/terminal/terminal.css) | 139 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/terminal/terminal.js](../../agent/src/omni/channels/web_static/terminal/terminal.js) | 102 | 平台脚本或界面资源 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)、[docs/TERMINAL.md](../../docs/TERMINAL.md)

测试：[agent/tests/test_terminal.py](../../agent/tests/test_terminal.py)、[agent/tests/test_terminal_managed.py](../../agent/tests/test_terminal_managed.py)、[agent/tests/test_doctor_lifecycle.py](../../agent/tests/test_doctor_lifecycle.py)

直接静态依赖：`config`、`web-ui`

## 项目与能力工作台 · workbench

分类：交互与终端。项目索引、API、已注册工具和扩展状态的只读 HTTP 适配。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_static/project/index.html](../../agent/src/omni/channels/web_static/project/index.html) | 15 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/project/panel.html](../../agent/src/omni/channels/web_static/project/panel.html) | 25 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/project/project.css](../../agent/src/omni/channels/web_static/project/project.css) | 126 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/project/project.js](../../agent/src/omni/channels/web_static/project/project.js) | 151 | 平台脚本或界面资源 |
| [agent/src/omni/channels/workbench/__init__.py](../../agent/src/omni/channels/workbench/__init__.py) | 4 | 专题工作台路由；只消费注入的 Runtime，不在通道构造核心对象。 |
| [agent/src/omni/channels/workbench/routes.py](../../agent/src/omni/channels/workbench/routes.py) | 71 | 只读项目目录、接口与实时能力；由 web.py 的统一鉴权中间件保护。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_project_catalog.py](../../agent/tests/test_project_catalog.py)

直接静态依赖：`assembly`、`catalog`、`config`

## 界面公共交互 · ui-shared

分类：交互与终端。统一导航、移动区域切换、有界请求、反馈和确认框；不执行工具或自动启动依赖。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_static/ui/app/api.js](../../agent/src/omni/channels/web_static/ui/app/api.js) | 9 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/approvals.js](../../agent/src/omni/channels/web_static/ui/app/approvals.js) | 45 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/artifacts.js](../../agent/src/omni/channels/web_static/ui/app/artifacts.js) | 37 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/chat.js](../../agent/src/omni/channels/web_static/ui/app/chat.js) | 117 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/desktop.js](../../agent/src/omni/channels/web_static/ui/app/desktop.js) | 24 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/dom.js](../../agent/src/omni/channels/web_static/ui/app/dom.js) | 36 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/header.js](../../agent/src/omni/channels/web_static/ui/app/header.js) | 132 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/hub-capabilities.js](../../agent/src/omni/channels/web_static/ui/app/hub-capabilities.js) | 10 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/hub-cron.js](../../agent/src/omni/channels/web_static/ui/app/hub-cron.js) | 140 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/hub-jobs.js](../../agent/src/omni/channels/web_static/ui/app/hub-jobs.js) | 9 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/hub-mcp-services.js](../../agent/src/omni/channels/web_static/ui/app/hub-mcp-services.js) | 10 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/hub-mcp.js](../../agent/src/omni/channels/web_static/ui/app/hub-mcp.js) | 68 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/hub-memory.js](../../agent/src/omni/channels/web_static/ui/app/hub-memory.js) | 3 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/hub-models.js](../../agent/src/omni/channels/web_static/ui/app/hub-models.js) | 440 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/hub-persona.js](../../agent/src/omni/channels/web_static/ui/app/hub-persona.js) | 142 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/hub-project.js](../../agent/src/omni/channels/web_static/ui/app/hub-project.js) | 9 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/hub-skills.js](../../agent/src/omni/channels/web_static/ui/app/hub-skills.js) | 443 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/hub-usage.js](../../agent/src/omni/channels/web_static/ui/app/hub-usage.js) | 34 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/hub.js](../../agent/src/omni/channels/web_static/ui/app/hub.js) | 334 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/main.js](../../agent/src/omni/channels/web_static/ui/app/main.js) | 65 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/memory.js](../../agent/src/omni/channels/web_static/ui/app/memory.js) | 36 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/modals.js](../../agent/src/omni/channels/web_static/ui/app/modals.js) | 23 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/publications.js](../../agent/src/omni/channels/web_static/ui/app/publications.js) | 35 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/rail.js](../../agent/src/omni/channels/web_static/ui/app/rail.js) | 31 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/runs.js](../../agent/src/omni/channels/web_static/ui/app/runs.js) | 173 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/services.js](../../agent/src/omni/channels/web_static/ui/app/services.js) | 78 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/sessions.js](../../agent/src/omni/channels/web_static/ui/app/sessions.js) | 51 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/skills.js](../../agent/src/omni/channels/web_static/ui/app/skills.js) | 153 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/state.js](../../agent/src/omni/channels/web_static/ui/app/state.js) | 15 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/tabs.js](../../agent/src/omni/channels/web_static/ui/app/tabs.js) | 39 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/app/tips.js](../../agent/src/omni/channels/web_static/ui/app/tips.js) | 19 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/base.css](../../agent/src/omni/channels/web_static/ui/base.css) | 11 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/components.css](../../agent/src/omni/channels/web_static/ui/components.css) | 205 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/embed-height.js](../../agent/src/omni/channels/web_static/ui/embed-height.js) | 25 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/frame.css](../../agent/src/omni/channels/web_static/ui/frame.css) | 485 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/icons.css](../../agent/src/omni/channels/web_static/ui/icons.css) | 9 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/markdown.js](../../agent/src/omni/channels/web_static/ui/markdown.js) | 116 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/shared.js](../../agent/src/omni/channels/web_static/ui/shared.js) | 202 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/terminal-theme.js](../../agent/src/omni/channels/web_static/ui/terminal-theme.js) | 48 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/theme.js](../../agent/src/omni/channels/web_static/ui/theme.js) | 82 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/tokens.css](../../agent/src/omni/channels/web_static/ui/tokens.css) | 200 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/ui/workbench.css](../../agent/src/omni/channels/web_static/ui/workbench.css) | 1128 | 平台脚本或界面资源 |

文档：[docs/UI_QA.md](../../docs/UI_QA.md)

测试：[agent/tests/test_ui_flows.py](../../agent/tests/test_ui_flows.py)

直接静态依赖：无内部模块依赖

## Web 界面与身份 · web-ui

分类：交互与终端。工作台业务路由、统一鉴权与静态界面；新专题使用独立 APIRouter。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py) | 522 | Web 工作台入口：uv run omni-web（默认 http://127.0.0.1:8000）。 |
| [agent/src/omni/channels/web_auth.py](../../agent/src/omni/channels/web_auth.py) | 341 | 工作台本地身份：配对码 → HttpOnly 会话 cookie，外加 Host/Origin/CSRF 校验。 |
| [agent/src/omni/channels/web_static/index.html](../../agent/src/omni/channels/web_static/index.html) | 632 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/memory/index.html](../../agent/src/omni/channels/web_static/memory/index.html) | 6 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/memory/memory.css](../../agent/src/omni/channels/web_static/memory/memory.css) | 153 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/memory/memory.js](../../agent/src/omni/channels/web_static/memory/memory.js) | 738 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/memory/page.js](../../agent/src/omni/channels/web_static/memory/page.js) | 2 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/memory/panel.html](../../agent/src/omni/channels/web_static/memory/panel.html) | 167 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/vendor/xterm-addon-fit.js](../../agent/src/omni/channels/web_static/vendor/xterm-addon-fit.js) | 2 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/vendor/xterm.js](../../agent/src/omni/channels/web_static/vendor/xterm.js) | 6 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/vendor/xterm.min.css](../../agent/src/omni/channels/web_static/vendor/xterm.min.css) | 8 | 平台脚本或界面资源 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)、[docs/UI_QA.md](../../docs/UI_QA.md)

测试：[agent/tests/test_web.py](../../agent/tests/test_web.py)、[agent/tests/test_web_auth.py](../../agent/tests/test_web_auth.py)、[agent/tests/test_ui_flows.py](../../agent/tests/test_ui_flows.py)

直接静态依赖：`assembly`、`config`、`engine`、`http-capabilities`、`http-contracts`、`http-routing`、`models`、`platform`、`safety`、`terminal`、`workbench`

## 会话与待办 API · http-sessions

分类：Web 接口。会话选择、历史、状态和待办；消费注入 Runtime。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/sessions.py](../../agent/src/omni/channels/web_api/sessions.py) | 105 | 会话与待办 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_web_api.py](../../agent/tests/test_web_api.py)、[agent/tests/test_sessions.py](../../agent/tests/test_sessions.py)

直接静态依赖：`assembly`、`http-contracts`、`storage`

## 对话与流式 API · http-conversation

分类：Web 接口。普通/流式对话、显式运行入口；共用会话闸门、租约和取消。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/conversation.py](../../agent/src/omni/channels/web_api/conversation.py) | 197 | 对话与流式 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_web_api.py](../../agent/tests/test_web_api.py)、[agent/tests/test_web.py](../../agent/tests/test_web.py)

直接静态依赖：`assembly`、`engine`、`http-contracts`、`models`

## 状态库与命令账本声明 · command-contracts

分类：平台与数据。state.db 全部表结构与迁移脚本的唯一声明源；command_schema 仅保留旧 import 兼容。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/memory/command_schema.py](../../agent/src/omni/memory/command_schema.py) | 6 | 命令账本声明的旧 import 兼容层；真实声明见 `memory/state_schema.py`。 |
| [agent/src/omni/memory/state_schema.py](../../agent/src/omni/memory/state_schema.py) | 303 | `state.db` 的表结构与迁移声明唯一来源。 |

文档：[docs/TERMINAL.md](../../docs/TERMINAL.md)

测试：[agent/tests/test_state.py](../../agent/tests/test_state.py)、[agent/tests/test_command_jobs.py](../../agent/tests/test_command_jobs.py)

直接静态依赖：`config`

## 后台命令任务 · command-jobs

分类：工具能力。审批后的独立命令任务、状态仓储、有限日志、会话隔离与取消；不复用人工 PTY。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/command_jobs.py](../../agent/src/omni/channels/web_api/command_jobs.py) | 23 | 后台命令管理适配；不提供绕过 Registry 审批的启动接口。 |
| [agent/src/omni/channels/web_static/jobs/index.html](../../agent/src/omni/channels/web_static/jobs/index.html) | 11 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/jobs/jobs.css](../../agent/src/omni/channels/web_static/jobs/jobs.css) | 88 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/jobs/jobs.js](../../agent/src/omni/channels/web_static/jobs/jobs.js) | 105 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/jobs/panel.html](../../agent/src/omni/channels/web_static/jobs/panel.html) | 28 | 平台脚本或界面资源 |
| [agent/src/omni/tools/shell/job_logs.py](../../agent/src/omni/tools/shell/job_logs.py) | 44 | 命令输出的有界 JSONL 环：序号续读、跨流串行、最近日志与缺口标记。 |
| [agent/src/omni/tools/shell/job_tools.py](../../agent/src/omni/tools/shell/job_tools.py) | 52 | 后台任务的 Registry 适配；不复用人工 PTY，不接受外部会话身份。 |
| [agent/src/omni/tools/shell/jobs.py](../../agent/src/omni/tools/shell/jobs.py) | 235 | 独立后台命令执行器：审批后入队、有界进程/日志、会话归属与诚实回收。 |

文档：[docs/TERMINAL.md](../../docs/TERMINAL.md)

测试：[agent/tests/test_command_jobs.py](../../agent/tests/test_command_jobs.py)

直接静态依赖：`config`、`platform`、`shell`、`storage`、`toolkit`

## 运行与用量 API · http-runs

分类：Web 接口。运行查询、事件续读、协作取消和费用视图。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/runs.py](../../agent/src/omni/channels/web_api/runs.py) | 102 | 运行与用量 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_web.py](../../agent/tests/test_web.py)

直接静态依赖：`assembly`、`models`、`storage`

## 记忆与档案 API · http-memory

分类：Web 接口。事实、档案、常驻、review 和检索状态。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py) | 164 | 记忆与档案 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_web.py](../../agent/tests/test_web.py)

直接静态依赖：`assembly`、`http-contracts`、`storage`

## 技能与审阅 API · http-skills

分类：Web 接口。技能编辑/修订/差异/回滚和待审阅状态，保留静态路由优先级。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py) | 484 | 技能 HTTP 适配：编辑/审阅/统计/归档/外来包，全部消费注入 Runtime。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_web_api.py](../../agent/tests/test_web_api.py)、[agent/tests/test_skills_history.py](../../agent/tests/test_skills_history.py)

直接静态依赖：`assembly`、`config`、`http-contracts`、`platform`、`storage`

## 产物 API · http-artifacts

分类：Web 接口。产物列表、指纹验证读取和清理，统一领域错误映射。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/artifacts.py](../../agent/src/omni/channels/web_api/artifacts.py) | 84 | 产物 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_artifacts.py](../../agent/tests/test_artifacts.py)

直接静态依赖：`assembly`、`storage`

## 审批 API · http-approvals

分类：Web 接口。待批准事项和人工决定；不构造或绕过审批器。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/approvals.py](../../agent/src/omni/channels/web_api/approvals.py) | 32 | 人工审批 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_web_auth.py](../../agent/tests/test_web_auth.py)

直接静态依赖：`assembly`、`http-contracts`

## MCP 管理 API · http-mcp

分类：Web 接口。只读状态、显式发现、目录复核和重连。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/mcp.py](../../agent/src/omni/channels/web_api/mcp.py) | 134 | MCP 管理 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |
| [agent/src/omni/channels/web_static/mcp/index.html](../../agent/src/omni/channels/web_static/mcp/index.html) | 10 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/mcp/mcp.css](../../agent/src/omni/channels/web_static/mcp/mcp.css) | 53 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/mcp/mcp.js](../../agent/src/omni/channels/web_static/mcp/mcp.js) | 165 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/mcp/panel.html](../../agent/src/omni/channels/web_static/mcp/panel.html) | 29 | 平台脚本或界面资源 |

文档：[docs/MCP.md](../../docs/MCP.md)

测试：[agent/tests/test_mcp.py](../../agent/tests/test_mcp.py)

直接静态依赖：`assembly`、`http-contracts`、`mcp`

## 服务与诊断 API · http-operations

分类：Web 接口。系统概览、doctor、备份、服务启停和日志。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/operations.py](../../agent/src/omni/channels/web_api/operations.py) | 63 | 服务与诊断 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：[agent/tests/test_web.py](../../agent/tests/test_web.py)

直接静态依赖：`assembly`、`http-contracts`、`platform`

## 调度与投递 API · http-automation

分类：Web 接口。任务创建/编辑/启停、历史和 outbox 人工处理。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/automation.py](../../agent/src/omni/channels/web_api/automation.py) | 212 | 调度与可靠投递 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_cron.py](../../agent/tests/test_cron.py)

直接静态依赖：`assembly`、`http-contracts`、`safety`、`storage`

## 发布草稿 API · http-publications

分类：Web 接口。草稿查询、乐观编辑和未知结果核查，不直接发布。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/publications.py](../../agent/src/omni/channels/web_api/publications.py) | 52 | 发布草稿 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/BILIBILI_WRITING.md](../../docs/BILIBILI_WRITING.md)

测试：[agent/tests/test_bilibili_publishing.py](../../agent/tests/test_bilibili_publishing.py)

直接静态依赖：`assembly`、`bilibili`、`storage`

## 能力与诊断面板 · http-capabilities

分类：Web 接口。被动诊断和能力汇总；显式探测分开执行，不自动启动依赖。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/capabilities.py](../../agent/src/omni/channels/web_api/capabilities.py) | 24 | 能力与诊断只读聚合：不连接 MCP、不访问手机、不启动终端/服务。 |
| [agent/src/omni/channels/web_static/capabilities/capabilities.css](../../agent/src/omni/channels/web_static/capabilities/capabilities.css) | 69 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/capabilities/capabilities.js](../../agent/src/omni/channels/web_static/capabilities/capabilities.js) | 91 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/capabilities/index.html](../../agent/src/omni/channels/web_static/capabilities/index.html) | 10 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/capabilities/panel.html](../../agent/src/omni/channels/web_static/capabilities/panel.html) | 37 | 平台脚本或界面资源 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：[agent/tests/test_android_streams.py](../../agent/tests/test_android_streams.py)

直接静态依赖：无内部模块依赖

## 安卓设备 API 与面板 · http-android

分类：Web 接口。按设备固定目标、配对归属的观察与受控应用操作。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/android.py](../../agent/src/omni/channels/web_api/android.py) | 77 | 按设备寻址的人工安卓适配；归属取已验配对身份，动作仍需单次观察。 |
| [agent/src/omni/channels/web_api/android_stream.py](../../agent/src/omni/channels/web_api/android_stream.py) | 24 | MJPEG 适配：订阅共享帧，异步取消及时归还连接，不给预览帧授权令牌。 |
| [agent/src/omni/channels/web_static/android/android.css](../../agent/src/omni/channels/web_static/android/android.css) | 101 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/android/android.js](../../agent/src/omni/channels/web_static/android/android.js) | 104 | 平台脚本或界面资源 |
| [agent/src/omni/channels/web_static/android/index.html](../../agent/src/omni/channels/web_static/android/index.html) | 73 | 平台脚本或界面资源 |

文档：[docs/ANDROID.md](../../docs/ANDROID.md)

测试：[agent/tests/test_android_devices.py](../../agent/tests/test_android_devices.py)

直接静态依赖：`web-ui`

## 桌面与安卓 API · http-devices

分类：Web 接口。窗口观察、设备树/锁帧、受观察约束的手动动作和 MJPEG。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/devices.py](../../agent/src/omni/channels/web_api/devices.py) | 123 | 桌面与安卓 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/ANDROID.md](../../docs/ANDROID.md)、[docs/DESKTOP.md](../../docs/DESKTOP.md)

测试：[agent/tests/test_web.py](../../agent/tests/test_web.py)、[agent/tests/test_web_api.py](../../agent/tests/test_web_api.py)

直接静态依赖：`assembly`、`http-android`、`http-contracts`

## HTTP 请求契约 · http-contracts

分类：Web 接口。公共请求模型；只承载数据声明，旧 web 模型名继续显式转发。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py) | 196 | 工作台请求契约；路由共用，旧 web.py 继续显式转发公共模型名。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_web_api.py](../../agent/tests/test_web_api.py)

直接静态依赖：无内部模块依赖

## 市场 API（人设与插件） · http-market

分类：Web 接口。社区源配置、人设卡与插件索引代理，以及插件的安装/激活/启停/卸载；安装只落隔离区，不执行代码。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/market.py](../../agent/src/omni/channels/web_api/market.py) | 290 | 市场 HTTP 适配：人设卡与插件列表/安装/激活，全部消费注入 Runtime。 |

文档：[docs/PLUGIN_SYSTEM.md](../../docs/PLUGIN_SYSTEM.md)

测试：[agent/tests/test_market_api.py](../../agent/tests/test_market_api.py)、[agent/tests/test_market_client.py](../../agent/tests/test_market_client.py)

直接静态依赖：`assembly`、`community`

## HTTP 路由装配 · http-routing

分类：Web 接口。聚合注入 Runtime 的领域路由，由应用入口统一挂载；不与请求契约合并为同一职责。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/web_api/__init__.py](../../agent/src/omni/channels/web_api/__init__.py) | 49 | 工作台领域路由聚合；应用生命周期与统一鉴权仍由 web.py 持有。 |
| [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py) | 249 | 记忆系统 HTTP 适配（M1–M6）：只消费注入 Runtime，路由聚合进既有鉴权闸门。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)、[docs/REFACTORING.md](../../docs/REFACTORING.md)

测试：[agent/tests/test_web_api.py](../../agent/tests/test_web_api.py)

直接静态依赖：`assembly`、`command-jobs`、`http-android`、`http-approvals`、`http-artifacts`、`http-automation`、`http-context`、`http-contracts`、`http-conversation`、`http-devices`、`http-market`、`http-mcp`、`http-memory`、`http-operations`、`http-publications`、`http-runs`、`http-sessions`、`http-skills`、`http-storage`、`storage`

## CLI 入口 · cli

分类：交互与终端。终端对话命令与 Runtime 适配。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/__main__.py](../../agent/src/omni/__main__.py) | 4 | python -m omni 入口。 |
| [agent/src/omni/channels/cli.py](../../agent/src/omni/channels/cli.py) | 152 | 本地终端 REPL 入口：uv run omni。 |

文档：[docs/HANDOFF.md](../../docs/HANDOFF.md)

测试：关联模块集成验证

直接静态依赖：`assembly`、`config`、`models`、`safety`

## QQ 入口 · qq-channel

分类：交互与终端。QQ 场景队列、事件去重和消息收发适配。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/qq.py](../../agent/src/omni/channels/qq.py) | 320 | QQ 通道入口：uv run omni-qq（需先启动 NapCat 并在 config.yaml 开启 qq）。 |

文档：[docs/QQ_SCENARIOS.md](../../docs/QQ_SCENARIOS.md)

测试：[agent/tests/test_qq.py](../../agent/tests/test_qq.py)、[agent/tests/test_qq_scope.py](../../agent/tests/test_qq_scope.py)

直接静态依赖：`assembly`、`channels`、`config`、`engine`、`models`、`platform`、`qq-tools`、`safety`

## 心跳入口 · heartbeat

分类：交互与终端。定时领取、执行、投递与周期清理。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/heartbeat.py](../../agent/src/omni/channels/heartbeat.py) | 82 | 心跳入口：uv run omni-heartbeat（定时任务常驻调度，无人值守 fail-closed）。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：[agent/tests/test_cron.py](../../agent/tests/test_cron.py)

直接静态依赖：`assembly`、`channels`、`config`、`engine`、`platform`、`safety`、`storage`

## 入口公共适配 · channels

分类：交互与终端。入口生命周期和只读诊断/检索评测命令。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/channels/__init__.py](../../agent/src/omni/channels/__init__.py) | 1 | 入口层：QQ(NapCat)、CLI、Web（P0 只有 CLI）。 |
| [agent/src/omni/channels/doctor.py](../../agent/src/omni/channels/doctor.py) | 42 | 只读 doctor 命令；不组装运行态，以免诊断触发建库、迁移或后台任务。 |
| [agent/src/omni/channels/eval_retrieval.py](../../agent/src/omni/channels/eval_retrieval.py) | 75 | 在临时数据库评测合成样本；hybrid 模式须显式传配置，默认完全离线。 |
| [agent/src/omni/channels/lifecycle.py](../../agent/src/omni/channels/lifecycle.py) | 22 | 常驻入口响应本次启动的停止请求；不构造运行态、不自行终止其它进程。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：关联模块集成验证

直接静态依赖：`agent-directories`、`assembly`、`config`、`engine`、`platform`

## 个人记忆工具 · personal

分类：工具能力。时钟、事实、档案、常驻、todo、摘要与手动整理各自独立。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/personal/__init__.py](../../agent/src/omni/tools/personal/__init__.py) | 49 | 个人内置能力的注册入口：事实、档案、常驻、待办、摘要和整理。 |
| [agent/src/omni/tools/personal/clock.py](../../agent/src/omni/tools/personal/clock.py) | 24 | 个人能力：clock；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/digest.py](../../agent/src/omni/tools/personal/digest.py) | 76 | 个人能力：digest；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/facts.py](../../agent/src/omni/tools/personal/facts.py) | 232 | 个人能力：facts；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/journal.py](../../agent/src/omni/tools/personal/journal.py) | 63 | 记忆系统工具（二）：日/周/月笔记的读取（M3/M4/M5）。 |
| [agent/src/omni/tools/personal/people.py](../../agent/src/omni/tools/personal/people.py) | 211 | 记忆系统工具（三）：核心人物记忆（M6）——查询、记住与临时事件。 |
| [agent/src/omni/tools/personal/profile.py](../../agent/src/omni/tools/personal/profile.py) | 89 | 个人能力：profile；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/refine.py](../../agent/src/omni/tools/personal/refine.py) | 59 | 个人能力：refine；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/resident.py](../../agent/src/omni/tools/personal/resident.py) | 64 | 个人能力：resident；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/todos.py](../../agent/src/omni/tools/personal/todos.py) | 86 | 个人能力：todos；工具仅使用注入依赖，不构造运行时。 |
| [agent/src/omni/tools/personal/transcript.py](../../agent/src/omni/tools/personal/transcript.py) | 224 | 记忆系统工具（一）：全量记忆两个模块的检索与精确读取（M1/M2）。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_tools.py](../../agent/tests/test_tools.py)、[agent/tests/test_profile_tools.py](../../agent/tests/test_profile_tools.py)、[agent/tests/test_resident.py](../../agent/tests/test_resident.py)

直接静态依赖：`storage`、`toolkit`

## 技能工具 · skills

分类：插件与扩展。技能查询/创建/编辑/回滚与外来技能隔离安装入口。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/skills/__init__.py](../../agent/src/omni/tools/skills/__init__.py) | 4 | skills 能力域注册入口。 |
| [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py) | 466 | 技能工具集（toolset=core）：技能的渐进披露与维护。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_skills.py](../../agent/tests/test_skills.py)、[agent/tests/test_skill_inbox.py](../../agent/tests/test_skill_inbox.py)

直接静态依赖：`storage`、`toolkit`

## 调度工具 · scheduling

分类：工具能力。周期/一次性任务、编辑、依赖和执行查询。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/scheduling/__init__.py](../../agent/src/omni/tools/scheduling/__init__.py) | 4 | scheduling 能力域注册入口。 |
| [agent/src/omni/tools/scheduling/tools.py](../../agent/src/omni/tools/scheduling/tools.py) | 313 | 定时任务工具集（toolset=cron）：agent 可自主排程（Hermes cronjob_tools 精神）。 |

文档：[docs/HANDOFF.md](../../docs/HANDOFF.md)

测试：[agent/tests/test_cron.py](../../agent/tests/test_cron.py)

直接静态依赖：`safety`、`storage`、`toolkit`

## 委派工具 · delegation

分类：工具能力。受父预算/权限/取消约束的子任务注册入口。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/delegation/__init__.py](../../agent/src/omni/tools/delegation/__init__.py) | 4 | delegation 能力域注册入口。 |
| [agent/src/omni/tools/delegation/tools.py](../../agent/src/omni/tools/delegation/tools.py) | 131 | 子任务委派（Hermes delegate_task 同款）：把独立子任务交给一个干净会话去跑。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_delegate.py](../../agent/tests/test_delegate.py)

直接静态依赖：`storage`、`toolkit`

## 系统服务工具 · system-tools

分类：工具能力。状态查询和受审批的服务操作适配。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/system/__init__.py](../../agent/src/omni/tools/system/__init__.py) | 4 | system 能力域注册入口。 |
| [agent/src/omni/tools/system/tools.py](../../agent/src/omni/tools/system/tools.py) | 122 | 系统工具集（toolset=system）：AI 自查本地服务状态、引导用户处置。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：[agent/tests/test_system.py](../../agent/tests/test_system.py)

直接静态依赖：`platform`、`toolkit`

## Agent shell 工具 · shell

分类：工具能力。只读命令语法、执行器、硬黑名单与人工确认命令；不复用交互式 PTY。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/shell/__init__.py](../../agent/src/omni/tools/shell/__init__.py) | 36 | shell 能力域：SAFE 观察（结构化 argv）与 CONFIRM 执行（shell 语义）。 |
| [agent/src/omni/tools/shell/danger.py](../../agent/src/omni/tools/shell/danger.py) | 45 | 硬性安全黑名单：灾难级操作命中即拒——即便用户已确认也不执行。 |
| [agent/src/omni/tools/shell/process.py](../../agent/src/omni/tools/shell/process.py) | 181 | 进程边界：argv 执行、输出上限、超时后的进程树回收与"副作用是否已知"。 |
| [agent/src/omni/tools/shell/spec.py](../../agent/src/omni/tools/shell/spec.py) | 506 | SAFE 命令解析：把命令串解析成**受控 argv**，解析不出来就拒绝。 |
| [agent/src/omni/tools/shell/tools.py](../../agent/src/omni/tools/shell/tools.py) | 268 | shell 工具集（toolset=shell）：peek_command（SAFE 观察）与 run_command（CONFIRM 执行）。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_shell_tools.py](../../agent/tests/test_shell_tools.py)

直接静态依赖：`config`、`toolkit`

## 文件工具 · files

分类：工具能力。允许根内的列表/搜索/读取/差异与受控写入回滚。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/files/__init__.py](../../agent/src/omni/tools/files/__init__.py) | 19 | 结构化文件工具域（D13）：工作区内的有界读写、差异预览与回滚。 |
| [agent/src/omni/tools/files/history.py](../../agent/src/omni/tools/files/history.py) | 161 | 工作区文件的修订快照（D13）：写前备份、可回滚。 |
| [agent/src/omni/tools/files/io.py](../../agent/src/omni/tools/files/io.py) | 320 | 文本读写的有界实现（D13）：编码识别、二进制拒绝、窗口读取、原子替换。 |
| [agent/src/omni/tools/files/paths.py](../../agent/src/omni/tools/files/paths.py) | 287 | 工作区路径边界（D13）：规范化、根校验、敏感项与"检查后不变"。 |
| [agent/src/omni/tools/files/search.py](../../agent/src/omni/tools/files/search.py) | 168 | 有界目录遍历与内容搜索（D13）。 |
| [agent/src/omni/tools/files/tools.py](../../agent/src/omni/tools/files/tools.py) | 703 | 结构化文件工具（D13，toolset=files）。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_files.py](../../agent/tests/test_files.py)

直接静态依赖：`platform`、`toolkit`

## 网页读取工具 · web-fetch

分类：工具能力。公开网页的目标网络、重定向、体积限制与正文提取。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/web/__init__.py](../../agent/src/omni/tools/web/__init__.py) | 19 | 通用网页读取（D14）：有界 HTTP 抓取 + 正文提取。 |
| [agent/src/omni/tools/web/extract.py](../../agent/src/omni/tools/web/extract.py) | 225 | 正文提取（D14）：把 HTML 变成"能读的文本"，不执行任何东西。 |
| [agent/src/omni/tools/web/net.py](../../agent/src/omni/tools/web/net.py) | 420 | 有界 HTTP 抓取（D14）：协议、目标网络、重定向、大小、超时全部受控。 |
| [agent/src/omni/tools/web/tools.py](../../agent/src/omni/tools/web/tools.py) | 200 | 网页读取工具（D14，toolset=web）：`web_read`。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_web_fetch.py](../../agent/tests/test_web_fetch.py)

直接静态依赖：`toolkit`

## 浏览器工具 · browser

分类：工具能力。Playwright 所有权、表单协议、观察与写预算。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/browser/__init__.py](../../agent/src/omni/tools/browser/__init__.py) | 5 | 浏览器能力域（P3）：Playwright 驱动 + 写预算 + 工具。 |
| [agent/src/omni/tools/browser/budget.py](../../agent/src/omni/tools/browser/budget.py) | 73 | 每日写操作预算：写走 UI 的第一道闸（bili-whale 的 quota 精神，xuexi/NOTES.md §4.3）。 |
| [agent/src/omni/tools/browser/driver.py](../../agent/src/omni/tools/browser/driver.py) | 428 | Playwright 浏览器驱动：单一所有者线程 + profile 跨进程租约 + 拟人节奏。 |
| [agent/src/omni/tools/browser/forms.py](../../agent/src/omni/tools/browser/forms.py) | 166 | 固定配置的 UI 表单观察/提交；所有操作在 BrowserDriver 的所有者线程执行。 |
| [agent/src/omni/tools/browser/tools.py](../../agent/src/omni/tools/browser/tools.py) | 201 | 浏览器工具集（toolset=browser）：读 SAFE，写 CONFIRM。 |

文档：[docs/BILIBILI_WRITING.md](../../docs/BILIBILI_WRITING.md)

测试：[agent/tests/test_browser.py](../../agent/tests/test_browser.py)、[agent/tests/test_browser_owner.py](../../agent/tests/test_browser_owner.py)

直接静态依赖：`config`、`storage`、`toolkit`

## 安卓工具 · android

分类：工具能力。ADB、防护、UI 树、观察令牌、锁帧和输入法。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/android/__init__.py](../../agent/src/omni/tools/android/__init__.py) | 7 | 安卓能力域（P4 地基）：纯 ADB 协议层 + 高层语义驱动 + 工具。 |
| [agent/src/omni/tools/android/adb.py](../../agent/src/omni/tools/android/adb.py) | 245 | 纯 ADB 协议层：设备无关的 adb 命令封装（不知道 LLM 与语义）。 |
| [agent/src/omni/tools/android/driver.py](../../agent/src/omni/tools/android/driver.py) | 338 | Android 观察令牌、单设备互斥、一次动作与回读。磁盘缓存仅用于展示。 |
| [agent/src/omni/tools/android/process.py](../../agent/src/omni/tools/android/process.py) | 29 | ADB 客户端的有界读取与超时；不把客户端退出当作设备操作已撤销。 |
| [agent/src/omni/tools/android/stream.py](../../agent/src/omni/tools/android/stream.py) | 150 | 按设备共享最新 JPEG；预览不产生观察授权，慢客户端跳过旧帧。 |
| [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py) | 237 | 安卓工具集（toolset=android）：观察 SAFE，动作 CONFIRM。 |
| [agent/src/omni/tools/android/ui.py](../../agent/src/omni/tools/android/ui.py) | 89 | UI 树解析（纯函数）：uiautomator XML → 结构化元素 → 文本树。 |

文档：[docs/ANDROID.md](../../docs/ANDROID.md)

测试：[agent/tests/test_android.py](../../agent/tests/test_android.py)、[agent/tests/test_android_observations.py](../../agent/tests/test_android_observations.py)

直接静态依赖：`config`、`platform`、`toolkit`

## 桌面工具 · desktop

分类：工具能力。Windows UIAutomation、窗口观察、租约与受控语义动作。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/desktop/__init__.py](../../agent/src/omni/tools/desktop/__init__.py) | 1 | Windows 桌面域：观察绑定、语义后台动作、写后回读。 |
| [agent/src/omni/tools/desktop/driver.py](../../agent/src/omni/tools/desktop/driver.py) | 136 |  |
| [agent/src/omni/tools/desktop/native.py](../../agent/src/omni/tools/desktop/native.py) | 40 | 静态 UIAutomation 辅助程序。用户参数只经 stdin JSON 传递，不拼接 PowerShell。 |
| [agent/src/omni/tools/desktop/tools.py](../../agent/src/omni/tools/desktop/tools.py) | 42 |  |
| [agent/src/omni/tools/desktop/uia.ps1](../../agent/src/omni/tools/desktop/uia.ps1) | 178 | 平台脚本或界面资源 |

文档：[docs/DESKTOP.md](../../docs/DESKTOP.md)

测试：[agent/tests/test_desktop.py](../../agent/tests/test_desktop.py)

直接静态依赖：`config`、`toolkit`

## B站工具 · bilibili

分类：工具能力。读 API/转写及注入浏览器协议的草稿与受控写。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/bilibili/__init__.py](../../agent/src/omni/tools/bilibili/__init__.py) | 1 | B站能力域（P1：只读）。api 层不知道 LLM，tools 层不知道 HTTP 细节。 |
| [agent/src/omni/tools/bilibili/api.py](../../agent/src/omni/tools/bilibili/api.py) | 256 | B站读 API 封装：只读端点 + 目标归一化 + WBI 签名。 |
| [agent/src/omni/tools/bilibili/asr.py](../../agent/src/omni/tools/bilibili/asr.py) | 101 | 无字幕视频的 ASR 兜底（faster-whisper，本地转写）。 |
| [agent/src/omni/tools/bilibili/publishing.py](../../agent/src/omni/tools/bilibili/publishing.py) | 126 | B站草稿→人工批准→一次 UI 提交→记录回读；浏览器能力通过协议注入。 |
| [agent/src/omni/tools/bilibili/tools.py](../../agent/src/omni/tools/bilibili/tools.py) | 172 | B站读工具集（toolset=bilibili，全部 SAFE 只读）。 |
| [agent/src/omni/tools/bilibili/wbi.py](../../agent/src/omni/tools/bilibili/wbi.py) | 43 | B站 WBI 签名（纯算法，无网络依赖）。 |
| [agent/src/omni/tools/bilibili/write_tools.py](../../agent/src/omni/tools/bilibili/write_tools.py) | 50 | B站草稿工具及逐次人工发布审批；所有浏览器依赖由组装根注入。 |

文档：[docs/BILIBILI_WRITING.md](../../docs/BILIBILI_WRITING.md)

测试：[agent/tests/test_bilibili_tools.py](../../agent/tests/test_bilibili_tools.py)、[agent/tests/test_bilibili_publishing.py](../../agent/tests/test_bilibili_publishing.py)

直接静态依赖：`config`、`storage`、`toolkit`

## QQ 工具 · qq-tools

分类：工具能力。OneBot 客户端与白名单内主动消息。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/qq/__init__.py](../../agent/src/omni/tools/qq/__init__.py) | 4 | QQ 能力域（P2）：OneBot v11 客户端（NapCat）与主动消息工具。 |
| [agent/src/omni/tools/qq/client.py](../../agent/src/omni/tools/qq/client.py) | 276 | OneBot v11 正向 WebSocket 客户端（对接 NapCat）。 |
| [agent/src/omni/tools/qq/tools.py](../../agent/src/omni/tools/qq/tools.py) | 93 | QQ 工具集（toolset=qq）：主动发消息。 |

文档：[docs/QQ_SCENARIOS.md](../../docs/QQ_SCENARIOS.md)

测试：[agent/tests/test_outbox.py](../../agent/tests/test_outbox.py)

直接静态依赖：`safety`、`toolkit`

## MCP 扩展适配 · mcp

分类：插件与扩展。外部 stdio 工具发现、授权、漂移撤权与子进程生命周期。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/mcp/__init__.py](../../agent/src/omni/tools/mcp/__init__.py) | 1 | 外部 MCP 能力域：本地 stdio 客户端与工具适配；不依赖 LLM 或内核。 |
| [agent/src/omni/tools/mcp/client.py](../../agent/src/omni/tools/mcp/client.py) | 530 | 同步 agent 到异步 MCP 的桥接：每服务一个会话所有者线程。 |
| [agent/src/omni/tools/mcp/tools.py](../../agent/src/omni/tools/mcp/tools.py) | 77 | MCP 工具适配：渐进发现、用户审核只读调用、人工确认调用；不决定传输细节。 |

文档：[docs/MCP.md](../../docs/MCP.md)

测试：[agent/tests/test_mcp.py](../../agent/tests/test_mcp.py)

直接静态依赖：`config`、`platform`、`toolkit`

## 工具公共协议与兼容层 · toolkit

分类：工具能力。Registry、ToolResult、SessionRef/RunRef、路径策略、截图协议及旧 import 薄转发。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/tools/__init__.py](../../agent/src/omni/tools/__init__.py) | 7 | 工具层：registry（注册表）+ builtin（内置工具集）+ 后续各能力域包。 |
| [agent/src/omni/tools/builtin.py](../../agent/src/omni/tools/builtin.py) | 13 | 旧导入兼容层；新增个人工具请进入 tools/personal/。 |
| [agent/src/omni/tools/cron_tools.py](../../agent/src/omni/tools/cron_tools.py) | 4 | 旧导入兼容层；实现位于 tools/scheduling/。 |
| [agent/src/omni/tools/delegate_tools.py](../../agent/src/omni/tools/delegate_tools.py) | 4 | 旧导入兼容层；实现位于 tools/delegation/。 |
| [agent/src/omni/tools/pathpolicy.py](../../agent/src/omni/tools/pathpolicy.py) | 83 | 工作区路径与敏感项的单一策略（D13）。 |
| [agent/src/omni/tools/registry.py](../../agent/src/omni/tools/registry.py) | 276 | 工具注册表：LLM function calling 的统一入口（Registry 模式）。 |
| [agent/src/omni/tools/result.py](../../agent/src/omni/tools/result.py) | 132 | 工具结果的结构化契约（D09）。 |
| [agent/src/omni/tools/runctx.py](../../agent/src/omni/tools/runctx.py) | 49 | 低层可见的"当前运行"引用（D09）。 |
| [agent/src/omni/tools/shell_tools.py](../../agent/src/omni/tools/shell_tools.py) | 38 | 兼容薄层：旧的 `omni.tools.shell_tools` 导入路径继续可用。 |
| [agent/src/omni/tools/shots.py](../../agent/src/omni/tools/shots.py) | 65 | 截图工具与 ArtifactStore 的桥（D15/A13）。 |
| [agent/src/omni/tools/skill_tools.py](../../agent/src/omni/tools/skill_tools.py) | 4 | 旧导入兼容层；实现位于 tools/skills/。 |
| [agent/src/omni/tools/system_tools.py](../../agent/src/omni/tools/system_tools.py) | 4 | 旧导入兼容层；实现位于 tools/system/。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[agent/tests/test_tools.py](../../agent/tests/test_tools.py)

直接静态依赖：`delegation`、`personal`、`safety`、`scheduling`、`shell`、`skills`、`system-tools`

## 插件社区 · community

分类：插件与扩展。社区客户端、工具适配与独立分发服务器。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/plugins/community/__init__.py](../../agent/src/omni/plugins/community/__init__.py) | 5 | 插件社区公共入口；客户端、工具与服务器分别维护。 |
| [agent/src/omni/plugins/community/client.py](../../agent/src/omni/plugins/community/client.py) | 330 | 社区客户端：插件与人设的索引 / 详情 / 安装 / 发布。 |
| [agent/src/omni/plugins/community/persona.py](../../agent/src/omni/plugins/community/persona.py) | 92 | 人设卡：社区分发的人设内容规格与收窄。 |
| [agent/src/omni/plugins/community/server.py](../../agent/src/omni/plugins/community/server.py) | 257 | 插件社区参考服务器（FastAPI + 文件存储，单 owner 部署）。 |
| [agent/src/omni/plugins/community/tools.py](../../agent/src/omni/plugins/community/tools.py) | 144 | 社区工具适配；HTTP/发布逻辑由注入客户端承担。 |
| [agent/src/omni/plugins/server.py](../../agent/src/omni/plugins/server.py) | 7 | 兼容 python -m omni.plugins.server；实现位于 plugins/community/server.py。 |

文档：[docs/PLUGIN_SYSTEM.md](../../docs/PLUGIN_SYSTEM.md)

测试：[agent/tests/test_plugin_community.py](../../agent/tests/test_plugin_community.py)

直接静态依赖：`platform`、`plugins`、`toolkit`

## 插件宿主 · plugins

分类：插件与扩展。manifest、隔离校验、人工激活、插件注册与生命周期；进程内受信代码。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/plugins/__init__.py](../../agent/src/omni/plugins/__init__.py) | 16 | 插件子系统（顶层包，与 channels/bootstrap 同级）。 |
| [agent/src/omni/plugins/host.py](../../agent/src/omni/plugins/host.py) | 420 | 插件宿主：外来功能包的隔离安装、激活、装载与卸载。 |
| [agent/src/omni/plugins/manifest.py](../../agent/src/omni/plugins/manifest.py) | 104 | 插件清单（manifest.json）：插件包的唯一身份与能力声明。 |
| [agent/src/omni/plugins/package_reader.py](../../agent/src/omni/plugins/package_reader.py) | 214 | 插件包读取与类型校验：纯数据检查，**零执行**。 |
| [agent/src/omni/plugins/quarantine.py](../../agent/src/omni/plugins/quarantine.py) | 255 | 插件隔离区与安装状态的文件层：隔离区落盘、清单记录、防篡改复核。 |
| [agent/src/omni/plugins/tools.py](../../agent/src/omni/plugins/tools.py) | 181 | 插件生命周期工具（面向 agent）：安装 → 审阅 → 激活 → 启停 → 卸载。 |

文档：[docs/PLUGIN_SYSTEM.md](../../docs/PLUGIN_SYSTEM.md)

测试：[agent/tests/test_plugins.py](../../agent/tests/test_plugins.py)

直接静态依赖：`platform`、`safety`、`toolkit`

## 项目索引基础设施 · catalog

分类：开发与验证。只读 AST 扫描、分类和确定性文档生成；不会执行待索引代码。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/system/catalog/__init__.py](../../agent/src/omni/system/catalog/__init__.py) | 4 | 项目索引：只解析源码与显式分类，不导入被索引模块、不接触运行数据。 |
| [agent/src/omni/system/catalog/render.py](../../agent/src/omni/system/catalog/render.py) | 46 | 索引输出：从同一结构生成模块表、API 表和可机器查询的 JSON。 |
| [agent/src/omni/system/catalog/scan.py](../../agent/src/omni/system/catalog/scan.py) | 169 | 静态源码索引；扫描范围为本地 Agent 的源码包（兼容旧布局），不读取 config/data/.env。 |
| [agent/src/omni/system/catalog/schema.py](../../agent/src/omni/system/catalog/schema.py) | 108 | 模块分类与关键调用链；分层规则仍以 docs/ARCHITECTURE.md 为准。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[agent/tests/test_project_catalog.py](../../agent/tests/test_project_catalog.py)

直接静态依赖：`config`

## 平台基础设施 · platform

分类：平台与数据。服务、进程身份、配置写回、锁/原子文件、日志、doctor、备份与验收。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/system/__init__.py](../../agent/src/omni/system/__init__.py) | 5 | 本地系统域：服务管理（NapCat / 常驻通道启停与状态）+ 配置读写。 |
| [agent/src/omni/system/atomic.py](../../agent/src/omni/system/atomic.py) | 39 | 原子写文件（A09 的同源问题）。 |
| [agent/src/omni/system/backup.py](../../agent/src/omni/system/backup.py) | 515 | 离线备份与恢复（D25 的提前子项，D05 迁移前必须先能用）。 |
| [agent/src/omni/system/config_io.py](../../agent/src/omni/system/config_io.py) | 473 | config.yaml 安全读写：保留注释的定向修改（只动白名单键）。 |
| [agent/src/omni/system/doctor.py](../../agent/src/omni/system/doctor.py) | 93 | 只读诊断：配置、安装元数据、DB 完整性和进程身份；不修复、不调用模型。 |
| [agent/src/omni/system/filelock.py](../../agent/src/omni/system/filelock.py) | 157 | 跨进程文件锁（D08）。 |
| [agent/src/omni/system/observability.py](../../agent/src/omni/system/observability.py) | 102 | 有界诊断日志。事件仅保留关联字段，排除对话、工具参数与结果正文。 |
| [agent/src/omni/system/path_guard.py](../../agent/src/omni/system/path_guard.py) | 45 | 跨域共享的路径安全原语：zip/目录条目名规范化与内容摘要。 |
| [agent/src/omni/system/process_identity.py](../../agent/src/omni/system/process_identity.py) | 231 | 进程身份与 Windows 句柄终止；不以裸 PID 或端口身份授权终止。 |
| [agent/src/omni/system/provenance.py](../../agent/src/omni/system/provenance.py) | 30 | 服务启动源码指纹，不依赖 Git 可用性，不包含配置/凭据/运行数据。 |
| [agent/src/omni/system/release_gate.py](../../agent/src/omni/system/release_gate.py) | 64 | 校验真实证据与常驻结果；永不执行发布或代替用户批准。 |
| [agent/src/omni/system/services.py](../../agent/src/omni/system/services.py) | 486 | 本地服务管理：NapCat 与 OmniOperator 常驻服务的 启动/停止/状态。 |
| [agent/src/omni/system/soak.py](../../agent/src/omni/system/soak.py) | 263 | 只读常驻采样与连续性核验；不会启动服务、调用模型或投递消息。 |
| [agent/src/omni/system/stderr_log.py](../../agent/src/omni/system/stderr_log.py) | 56 | 子进程 stderr 的有限管道泵：持续排空、跨块脱敏与文件轮转。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)、[docs/ACCEPTANCE.md](../../docs/ACCEPTANCE.md)

测试：[agent/tests/test_backup.py](../../agent/tests/test_backup.py)、[agent/tests/test_process_identity.py](../../agent/tests/test_process_identity.py)

直接静态依赖：`agent-directories`、`config`

## Python 包入口 · package

分类：开发与验证。安装包标识。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [agent/src/omni/__init__.py](../../agent/src/omni/__init__.py) | 3 | OmniOperator：像人一样生活在平台里的具身个人 agent。 |

文档：

测试：关联模块集成验证

直接静态依赖：无内部模块依赖
