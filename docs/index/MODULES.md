# 模块与源码索引

> 由 `uv run --no-sync python scripts/project_index.py` 生成。不要手改；分类改 system/catalog/schema.py，分层改 ARCHITECTURE.md。

源码指纹：`337d11c278b826ad1f293226180eb46908fb138bbc7ba01644cff2cc21677d17`。包含 54 个职责模块、243 个源码/界面文件。

## Agent 空间与目录 · agent-directories

分类：平台与数据。独立空间注册、目录校验、修订并发控制与有效路径解析；不构造执行对象或搬移数据。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/system/agent_spaces/__init__.py](../../src/omni/system/agent_spaces/__init__.py) | 5 | Agent 目录注册与纯路径解析；执行对象仍由 bootstrap 构造。 |
| [src/omni/system/agent_spaces/catalog.py](../../src/omni/system/agent_spaces/catalog.py) | 80 | 配置注册表：有界读取、CAS、跨进程互斥、原子写；不搬运用户数据。 |
| [src/omni/system/agent_spaces/layout.py](../../src/omni/system/agent_spaces/layout.py) | 119 | 本机 Agent 布局的声明与校验，不创建目录或打开数据。 |

文档：[docs/AGENT_STORAGE.md](../../docs/AGENT_STORAGE.md)

测试：[tests/test_agent_storage.py](../../tests/test_agent_storage.py)

直接静态依赖：`config`、`platform`

## Agent 设置与文件管理 · http-storage

分类：Web 接口。目录设置、待重启差异与当前空间公开文件区的有界浏览和人工上传。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/storage.py](../../src/omni/channels/web_api/storage.py) | 157 | Agent 目录设置和当前公开文件区；只消费组装根注入的目录与路径策略。 |
| [src/omni/channels/web_static/storage/index.html](../../src/omni/channels/web_static/storage/index.html) | 63 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/storage/storage.css](../../src/omni/channels/web_static/storage/storage.css) | 130 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/storage/storage.js](../../src/omni/channels/web_static/storage/storage.js) | 78 | 平台脚本或界面资源 |

文档：[docs/AGENT_STORAGE.md](../../docs/AGENT_STORAGE.md)

测试：[tests/test_agent_storage.py](../../tests/test_agent_storage.py)

直接静态依赖：`agent-directories`、`assembly`、`files`、`platform`、`toolkit`

## 运行时组装 · assembly

分类：内核与模型。唯一组装根，构造依赖并暴露 Runtime 门面。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/bootstrap.py](../../src/omni/bootstrap.py) | 662 | 组装根（Composition Root）：全项目唯一负责装配的地方。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[tests/test_paths.py](../../tests/test_paths.py)

直接静态依赖：`agent-directories`、`android`、`bilibili`、`browser`、`command-jobs`、`community`、`config`、`delegation`、`desktop`、`engine`、`files`、`mcp`、`models`、`personal`、`platform`、`plugins`、`qq-tools`、`safety`、`scheduling`、`shell`、`skills`、`storage`、`system-tools`、`toolkit`、`web-fetch`

## 配置与路径契约 · config

分类：平台与数据。配置模型、加载与 RuntimePaths；不包含业务编排。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/config.py](../../src/omni/config.py) | 576 | 配置加载：.env 密钥 + config.yaml 主配置 + 运行期路径表。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[tests/test_config.py](../../tests/test_config.py)

直接静态依赖：无内部模块依赖

## Agent 内核 · engine

分类：内核与模型。回合、上下文、工具循环、取消、委派约束、review 与调度。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/core/__init__.py](../../src/omni/core/__init__.py) | 1 | Agent 核心：编排循环。工具注册表在 tools 包，审批在 safety 包。 |
| [src/omni/core/agent.py](../../src/omni/core/agent.py) | 844 | Agent 核心循环：组装上下文 → 调 LLM → 执行工具 → 直到给出回答。 |
| [src/omni/core/compress.py](../../src/omni/core/compress.py) | 299 | 上下文压缩 + 消息不变量（Hermes compression_facade 的最小版，NOTES §1.2）。 |
| [src/omni/core/cron.py](../../src/omni/core/cron.py) | 440 | 心跳调度器：tick 循环 + 至多一次派发 + 任务执行/投递（Hermes cron 最小版）。 |
| [src/omni/core/evaluation.py](../../src/omni/core/evaluation.py) | 24 | 固定样本检索评测；不把 mock 模型的排序表现当成真实语义收益。 |
| [src/omni/core/qq_policy.py](../../src/omni/core/qq_policy.py) | 37 | 主动提醒策略：显式所有者、安静时段和独立每日额度；不做网络发送。 |
| [src/omni/core/retrieval.py](../../src/omni/core/retrieval.py) | 87 | FTS + 向量 RRF 融合；后台有界增量索引，网络请求不占数据库事务。 |
| [src/omni/core/review.py](../../src/omni/core/review.py) | 236 | 回合后异步 review：自进化最小闭环（Hermes background_review 的 P1 简化版， |
| [src/omni/core/run.py](../../src/omni/core/run.py) | 264 | 运行（run）的身份、取消信号与上下文（D09）。 |
| [src/omni/core/session.py](../../src/omni/core/session.py) | 147 | 会话作用域：同一会话串行、不同会话并行、排队有界、跨进程有租约。 |
| [src/omni/core/vision.py](../../src/omni/core/vision.py) | 89 | 工具产物 → 模型图像块（D15/A13）。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[tests/test_agent.py](../../tests/test_agent.py)、[tests/test_runs.py](../../tests/test_runs.py)、[tests/test_parallel_tools.py](../../tests/test_parallel_tools.py)

直接静态依赖：`config`、`models`、`qq-tools`、`safety`、`storage`、`toolkit`

## 模型网关与 embedding · models

分类：内核与模型。模型路由、能力、重试、流式、预算与 embedding 传输。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/llm/__init__.py](../../src/omni/llm/__init__.py) | 1 | LLM 层：LiteLLM 统一网关。 |
| [src/omni/llm/budget.py](../../src/omni/llm/budget.py) | 174 | 费用预留与结算的契约（D10）。 |
| [src/omni/llm/embedding.py](../../src/omni/llm/embedding.py) | 116 | 可切换 embedding 出口：本地 Ollama / LiteLLM API；费用账本从 bootstrap 注入。 |
| [src/omni/llm/errors.py](../../src/omni/llm/errors.py) | 206 | 模型调用的错误分类与脱敏（D10）。 |
| [src/omni/llm/gateway.py](../../src/omni/llm/gateway.py) | 676 | LiteLLM 统一网关：一套 OpenAI 格式接所有供应商（海外/国产/本地 Ollama）。 |
| [src/omni/llm/usage.py](../../src/omni/llm/usage.py) | 185 | 用量（tokens）与价格：把"这次调用大概/实际花了多少"变成可记账的数字。（D10） |

文档：[docs/RETRIEVAL.md](../../docs/RETRIEVAL.md)

测试：[tests/test_gateway.py](../../tests/test_gateway.py)、[tests/test_usage.py](../../tests/test_usage.py)

直接静态依赖：`config`

## 审批与权限 · safety

分类：内核与模型。风险分级、人工审批与无人值守拒绝。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/safety/__init__.py](../../src/omni/safety/__init__.py) | 1 | 安全层：审批闸门（工具风险分级 + 按上下文选择审批策略）。 |
| [src/omni/safety/broker.py](../../src/omni/safety/broker.py) | 116 | 审批代理：跨线程确认流（Web 工作台用）。 |
| [src/omni/safety/gates.py](../../src/omni/safety/gates.py) | 146 | 审批闸门：工具风险分级 + 按上下文选择审批策略（Strategy 模式）。 |
| [src/omni/safety/smart.py](../../src/omni/safety/smart.py) | 82 | smart 审批档（Hermes approval_smart 同款）：辅助 LLM 先判风险，拿不准再问人。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[tests/test_safety.py](../../tests/test_safety.py)

直接静态依赖：无内部模块依赖

## 记忆与运行账本 · storage

分类：平台与数据。会话/事实/技能/产物及 state.db 事务、迁移、额度、投递。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/memory/__init__.py](../../src/omni/memory/__init__.py) | 1 | 记忆层：SQLite 持久化（P0 版；P1 升级向量检索与三层记忆）。 |
| [src/omni/memory/artifacts.py](../../src/omni/memory/artifacts.py) | 296 | 产物存储（D15 / A13）：截图、文件、报告统一按 artifact_id 管理。 |
| [src/omni/memory/command_jobs.py](../../src/omni/memory/command_jobs.py) | 45 | 命令任务元数据仓储；不执行或终止操作系统进程。 |
| [src/omni/memory/cron.py](../../src/omni/memory/cron.py) | 61 | 定时任务模型与存储的公开入口（保留旧导入路径）。 |
| [src/omni/memory/guard.py](../../src/omni/memory/guard.py) | 130 | 记忆写入的确定性闸门（D12 / A15）。 |
| [src/omni/memory/jobs.py](../../src/omni/memory/jobs.py) | 138 | 定时任务模型与调度时间（纯数据 + 纯函数，不碰存储）。 |
| [src/omni/memory/publications.py](../../src/omni/memory/publications.py) | 88 | 人工发布草稿与发送身份账本；进程中断/结果未知不自动二次发布。 |
| [src/omni/memory/qq.py](../../src/omni/memory/qq.py) | 74 | QQ 入站去重、撤回墓碑与回复身份；只保存 ID/状态，不保存原文。 |
| [src/omni/memory/resident.py](../../src/omni/memory/resident.py) | 377 | 常驻小记忆（Hermes MEMORY.md 式，NOTES §1.3a）：跨会话冻结快照注入系统提示词。 |
| [src/omni/memory/revisions.py](../../src/omni/memory/revisions.py) | 240 | 内容修订历史（D08）：技能与常驻记忆的"可回滚快照"。 |
| [src/omni/memory/runs.py](../../src/omni/memory/runs.py) | 355 | 运行记录（run）与事件流（`state.db`）：一次"问—想—做—答"的可查账本。 |
| [src/omni/memory/skill_inbox.py](../../src/omni/memory/skill_inbox.py) | 477 | 外来技能包隔离安装（D18 后置项）：inbox 收包 → 隔离区校验 → 用户激活。 |
| [src/omni/memory/skills.py](../../src/omni/memory/skills.py) | 690 | 技能存储（程序性记忆）：skills/<name>/SKILL.md，agentskills.io 兼容子集。 |
| [src/omni/memory/state.py](../../src/omni/memory/state.py) | 1607 | 共享运行状态（`data/state.db`）：定时任务、执行账本、额度与迁移记录。 |
| [src/omni/memory/store.py](../../src/omni/memory/store.py) | 1081 | SQLite 记忆存储：会话 / 消息 / 事实 / 用户档案 / 全文检索 / review 日志。 |
| [src/omni/memory/usage.py](../../src/omni/memory/usage.py) | 242 | 模型调用的用量与费用账本（`state.db`，D10）。 |
| [src/omni/memory/vectors.py](../../src/omni/memory/vectors.py) | 93 | sqlite-vec 派生索引。与事实同库；遗忘/纠错触发器只用普通 SQL，无扩展依赖。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)、[docs/RETRIEVAL.md](../../docs/RETRIEVAL.md)

测试：[tests/test_state.py](../../tests/test_state.py)、[tests/test_memory.py](../../tests/test_memory.py)

直接静态依赖：`command-contracts`、`config`、`platform`

## 内置终端 · terminal

分类：交互与终端。ConPTY 后端、会话、会话池、WebSocket 四层；与 agent shell 工具分工。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/terminal/__init__.py](../../src/omni/channels/terminal/__init__.py) | 6 | 工作台终端公共入口；平台适配、会话、会话池和 WS 路由分别维护。 |
| [src/omni/channels/terminal/backend.py](../../src/omni/channels/terminal/backend.py) | 36 | ConPTY 平台适配：检测可用性与创建进程；不管理会话池或 HTTP。 |
| [src/omni/channels/terminal/managed.py](../../src/omni/channels/terminal/managed.py) | 75 | 可恢复的人类终端：持续排空、有限输出环与单写者；不向模型暴露。 |
| [src/omni/channels/terminal/pool.py](../../src/omni/channels/terminal/pool.py) | 130 | 终端会话池：并发上限、关闭预算和残留报告。 |
| [src/omni/channels/terminal/routes.py](../../src/omni/channels/terminal/routes.py) | 77 | 内置终端 WebSocket 适配；复用工作台身份及注入的会话池。 |
| [src/omni/channels/terminal/session.py](../../src/omni/channels/terminal/session.py) | 42 | 单终端会话的读、写、窗口尺寸和退出确认。 |
| [src/omni/channels/terminal/sessions.py](../../src/omni/channels/terminal/sessions.py) | 138 | 人工终端 v2：身份归属、结构化消息及只回放输出。旧 WS 独立兼容。 |
| [src/omni/channels/web_static/terminal/index.html](../../src/omni/channels/web_static/terminal/index.html) | 51 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/terminal/terminal.css](../../src/omni/channels/web_static/terminal/terminal.css) | 106 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/terminal/terminal.js](../../src/omni/channels/web_static/terminal/terminal.js) | 93 | 平台脚本或界面资源 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)、[docs/TERMINAL.md](../../docs/TERMINAL.md)

测试：[tests/test_terminal.py](../../tests/test_terminal.py)、[tests/test_terminal_managed.py](../../tests/test_terminal_managed.py)、[tests/test_doctor_lifecycle.py](../../tests/test_doctor_lifecycle.py)

直接静态依赖：`config`、`web-ui`

## 项目与能力工作台 · workbench

分类：交互与终端。项目索引、API、已注册工具和扩展状态的只读 HTTP 适配。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_static/project/index.html](../../src/omni/channels/web_static/project/index.html) | 41 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/project/project.css](../../src/omni/channels/web_static/project/project.css) | 137 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/project/project.js](../../src/omni/channels/web_static/project/project.js) | 110 | 平台脚本或界面资源 |
| [src/omni/channels/workbench/__init__.py](../../src/omni/channels/workbench/__init__.py) | 4 | 专题工作台路由；只消费注入的 Runtime，不在通道构造核心对象。 |
| [src/omni/channels/workbench/routes.py](../../src/omni/channels/workbench/routes.py) | 71 | 只读项目目录、接口与实时能力；由 web.py 的统一鉴权中间件保护。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[tests/test_project_catalog.py](../../tests/test_project_catalog.py)

直接静态依赖：`assembly`、`catalog`、`config`

## 界面公共交互 · ui-shared

分类：交互与终端。统一导航、移动区域切换、有界请求、反馈和确认框；不执行工具或自动启动依赖。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_static/ui/app/api.js](../../src/omni/channels/web_static/ui/app/api.js) | 9 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/approvals.js](../../src/omni/channels/web_static/ui/app/approvals.js) | 29 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/artifacts.js](../../src/omni/channels/web_static/ui/app/artifacts.js) | 31 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/chat.js](../../src/omni/channels/web_static/ui/app/chat.js) | 103 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/desktop.js](../../src/omni/channels/web_static/ui/app/desktop.js) | 19 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/dom.js](../../src/omni/channels/web_static/ui/app/dom.js) | 18 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/header.js](../../src/omni/channels/web_static/ui/app/header.js) | 69 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/hub-cron.js](../../src/omni/channels/web_static/ui/app/hub-cron.js) | 129 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/hub-mcp.js](../../src/omni/channels/web_static/ui/app/hub-mcp.js) | 61 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/hub-models.js](../../src/omni/channels/web_static/ui/app/hub-models.js) | 297 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/hub-persona.js](../../src/omni/channels/web_static/ui/app/hub-persona.js) | 131 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/hub-usage.js](../../src/omni/channels/web_static/ui/app/hub-usage.js) | 34 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/hub.js](../../src/omni/channels/web_static/ui/app/hub.js) | 220 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/main.js](../../src/omni/channels/web_static/ui/app/main.js) | 27 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/memory.js](../../src/omni/channels/web_static/ui/app/memory.js) | 114 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/modals.js](../../src/omni/channels/web_static/ui/app/modals.js) | 18 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/publications.js](../../src/omni/channels/web_static/ui/app/publications.js) | 29 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/rail.js](../../src/omni/channels/web_static/ui/app/rail.js) | 26 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/runs.js](../../src/omni/channels/web_static/ui/app/runs.js) | 158 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/services.js](../../src/omni/channels/web_static/ui/app/services.js) | 66 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/sessions.js](../../src/omni/channels/web_static/ui/app/sessions.js) | 47 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/skills.js](../../src/omni/channels/web_static/ui/app/skills.js) | 136 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/state.js](../../src/omni/channels/web_static/ui/app/state.js) | 11 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/tabs.js](../../src/omni/channels/web_static/ui/app/tabs.js) | 38 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/app/tips.js](../../src/omni/channels/web_static/ui/app/tips.js) | 19 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/base.css](../../src/omni/channels/web_static/ui/base.css) | 11 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/components.css](../../src/omni/channels/web_static/ui/components.css) | 189 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/frame.css](../../src/omni/channels/web_static/ui/frame.css) | 425 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/icons.css](../../src/omni/channels/web_static/ui/icons.css) | 9 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/markdown.js](../../src/omni/channels/web_static/ui/markdown.js) | 116 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/shared.js](../../src/omni/channels/web_static/ui/shared.js) | 170 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/tokens.css](../../src/omni/channels/web_static/ui/tokens.css) | 109 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/ui/workbench.css](../../src/omni/channels/web_static/ui/workbench.css) | 928 | 平台脚本或界面资源 |

文档：[docs/UI_QA.md](../../docs/UI_QA.md)

测试：[tests/test_ui_flows.py](../../tests/test_ui_flows.py)

直接静态依赖：无内部模块依赖

## Web 界面与身份 · web-ui

分类：交互与终端。工作台业务路由、统一鉴权与静态界面；新专题使用独立 APIRouter。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web.py](../../src/omni/channels/web.py) | 458 | Web 工作台入口：uv run omni-web（默认 http://127.0.0.1:8000）。 |
| [src/omni/channels/web_auth.py](../../src/omni/channels/web_auth.py) | 341 | 工作台本地身份：配对码 → HttpOnly 会话 cookie，外加 Host/Origin/CSRF 校验。 |
| [src/omni/channels/web_static/index.html](../../src/omni/channels/web_static/index.html) | 439 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/vendor/xterm-addon-fit.js](../../src/omni/channels/web_static/vendor/xterm-addon-fit.js) | 2 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/vendor/xterm.js](../../src/omni/channels/web_static/vendor/xterm.js) | 6 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/vendor/xterm.min.css](../../src/omni/channels/web_static/vendor/xterm.min.css) | 8 | 平台脚本或界面资源 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)、[docs/UI_QA.md](../../docs/UI_QA.md)

测试：[tests/test_web.py](../../tests/test_web.py)、[tests/test_web_auth.py](../../tests/test_web_auth.py)、[tests/test_ui_flows.py](../../tests/test_ui_flows.py)

直接静态依赖：`assembly`、`config`、`engine`、`http-capabilities`、`http-contracts`、`http-routing`、`models`、`platform`、`safety`、`terminal`、`workbench`

## 会话与待办 API · http-sessions

分类：Web 接口。会话选择、历史、状态和待办；消费注入 Runtime。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/sessions.py](../../src/omni/channels/web_api/sessions.py) | 139 | 会话与待办 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[tests/test_web_api.py](../../tests/test_web_api.py)、[tests/test_sessions.py](../../tests/test_sessions.py)

直接静态依赖：`assembly`、`http-contracts`、`storage`

## 对话与流式 API · http-conversation

分类：Web 接口。普通/流式对话、显式运行入口；共用会话闸门、租约和取消。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/conversation.py](../../src/omni/channels/web_api/conversation.py) | 183 | 对话与流式 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[tests/test_web_api.py](../../tests/test_web_api.py)、[tests/test_web.py](../../tests/test_web.py)

直接静态依赖：`assembly`、`engine`、`http-contracts`、`models`

## 命令账本声明 · command-contracts

分类：平台与数据。state.db 命令账本的纯迁移声明，不导入仓储或执行器。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/memory/command_schema.py](../../src/omni/memory/command_schema.py) | 22 | 后台命令账本的迁移声明；不连接数据库、不启动进程。 |

文档：[docs/TERMINAL.md](../../docs/TERMINAL.md)

测试：[tests/test_command_jobs.py](../../tests/test_command_jobs.py)

直接静态依赖：无内部模块依赖

## 后台命令任务 · command-jobs

分类：工具能力。审批后的独立命令任务、状态仓储、有限日志、会话隔离与取消；不复用人工 PTY。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/command_jobs.py](../../src/omni/channels/web_api/command_jobs.py) | 23 | 后台命令管理适配；不提供绕过 Registry 审批的启动接口。 |
| [src/omni/channels/web_static/jobs/index.html](../../src/omni/channels/web_static/jobs/index.html) | 41 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/jobs/jobs.css](../../src/omni/channels/web_static/jobs/jobs.css) | 75 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/jobs/jobs.js](../../src/omni/channels/web_static/jobs/jobs.js) | 66 | 平台脚本或界面资源 |
| [src/omni/tools/shell/job_logs.py](../../src/omni/tools/shell/job_logs.py) | 44 | 命令输出的有界 JSONL 环：序号续读、跨流串行、最近日志与缺口标记。 |
| [src/omni/tools/shell/job_tools.py](../../src/omni/tools/shell/job_tools.py) | 51 | 后台任务的 Registry 适配；不复用人工 PTY，不接受外部会话身份。 |
| [src/omni/tools/shell/jobs.py](../../src/omni/tools/shell/jobs.py) | 230 | 独立后台命令执行器：审批后入队、有界进程/日志、会话归属与诚实回收。 |

文档：[docs/TERMINAL.md](../../docs/TERMINAL.md)

测试：[tests/test_command_jobs.py](../../tests/test_command_jobs.py)

直接静态依赖：`config`、`platform`、`shell`、`storage`、`toolkit`

## 运行与用量 API · http-runs

分类：Web 接口。运行查询、事件续读、协作取消和费用视图。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/runs.py](../../src/omni/channels/web_api/runs.py) | 102 | 运行与用量 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[tests/test_web.py](../../tests/test_web.py)

直接静态依赖：`assembly`、`models`、`storage`

## 记忆与档案 API · http-memory

分类：Web 接口。事实、档案、常驻、review 和检索状态。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/memory.py](../../src/omni/channels/web_api/memory.py) | 130 | 记忆与档案 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[tests/test_web.py](../../tests/test_web.py)

直接静态依赖：`assembly`、`http-contracts`、`storage`

## 技能与审阅 API · http-skills

分类：Web 接口。技能编辑/修订/差异/回滚和待审阅状态，保留静态路由优先级。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/skills.py](../../src/omni/channels/web_api/skills.py) | 230 | 技能与审阅 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[tests/test_web_api.py](../../tests/test_web_api.py)、[tests/test_skills_history.py](../../tests/test_skills_history.py)

直接静态依赖：`assembly`、`http-contracts`

## 产物 API · http-artifacts

分类：Web 接口。产物列表、指纹验证读取和清理，统一领域错误映射。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/artifacts.py](../../src/omni/channels/web_api/artifacts.py) | 84 | 产物 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[tests/test_artifacts.py](../../tests/test_artifacts.py)

直接静态依赖：`assembly`、`storage`

## 审批 API · http-approvals

分类：Web 接口。待批准事项和人工决定；不构造或绕过审批器。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/approvals.py](../../src/omni/channels/web_api/approvals.py) | 32 | 人工审批 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[tests/test_web_auth.py](../../tests/test_web_auth.py)

直接静态依赖：`assembly`、`http-contracts`

## MCP 管理 API · http-mcp

分类：Web 接口。只读状态、显式发现、目录复核和重连。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/mcp.py](../../src/omni/channels/web_api/mcp.py) | 135 | MCP 管理 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |
| [src/omni/channels/web_static/mcp/index.html](../../src/omni/channels/web_static/mcp/index.html) | 35 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/mcp/mcp.css](../../src/omni/channels/web_static/mcp/mcp.css) | 63 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/mcp/mcp.js](../../src/omni/channels/web_static/mcp/mcp.js) | 114 | 平台脚本或界面资源 |

文档：[docs/MCP.md](../../docs/MCP.md)

测试：[tests/test_mcp.py](../../tests/test_mcp.py)

直接静态依赖：`assembly`、`http-contracts`、`mcp`

## 服务与诊断 API · http-operations

分类：Web 接口。系统概览、doctor、备份、服务启停和日志。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/operations.py](../../src/omni/channels/web_api/operations.py) | 63 | 服务与诊断 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：[tests/test_web.py](../../tests/test_web.py)

直接静态依赖：`assembly`、`http-contracts`、`platform`

## 调度与投递 API · http-automation

分类：Web 接口。任务创建/编辑/启停、历史和 outbox 人工处理。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/automation.py](../../src/omni/channels/web_api/automation.py) | 212 | 调度与可靠投递 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[tests/test_cron.py](../../tests/test_cron.py)

直接静态依赖：`assembly`、`http-contracts`、`safety`、`storage`

## 发布草稿 API · http-publications

分类：Web 接口。草稿查询、乐观编辑和未知结果核查，不直接发布。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/publications.py](../../src/omni/channels/web_api/publications.py) | 52 | 发布草稿 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/BILIBILI_WRITING.md](../../docs/BILIBILI_WRITING.md)

测试：[tests/test_bilibili_publishing.py](../../tests/test_bilibili_publishing.py)

直接静态依赖：`assembly`、`bilibili`、`storage`

## 能力与诊断面板 · http-capabilities

分类：Web 接口。被动诊断和能力汇总；显式探测分开执行，不自动启动依赖。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/capabilities.py](../../src/omni/channels/web_api/capabilities.py) | 24 | 能力与诊断只读聚合：不连接 MCP、不访问手机、不启动终端/服务。 |
| [src/omni/channels/web_static/capabilities/capabilities.css](../../src/omni/channels/web_static/capabilities/capabilities.css) | 91 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/capabilities/capabilities.js](../../src/omni/channels/web_static/capabilities/capabilities.js) | 53 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/capabilities/index.html](../../src/omni/channels/web_static/capabilities/index.html) | 49 | 平台脚本或界面资源 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：[tests/test_android_streams.py](../../tests/test_android_streams.py)

直接静态依赖：无内部模块依赖

## 安卓设备 API 与面板 · http-android

分类：Web 接口。按设备固定目标、配对归属的观察与受控应用操作。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/android.py](../../src/omni/channels/web_api/android.py) | 77 | 按设备寻址的人工安卓适配；归属取已验配对身份，动作仍需单次观察。 |
| [src/omni/channels/web_api/android_stream.py](../../src/omni/channels/web_api/android_stream.py) | 23 | MJPEG 适配：订阅共享帧，异步取消及时归还连接，不给预览帧授权令牌。 |
| [src/omni/channels/web_static/android/android.css](../../src/omni/channels/web_static/android/android.css) | 84 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/android/android.js](../../src/omni/channels/web_static/android/android.js) | 104 | 平台脚本或界面资源 |
| [src/omni/channels/web_static/android/index.html](../../src/omni/channels/web_static/android/index.html) | 73 | 平台脚本或界面资源 |

文档：[docs/ANDROID.md](../../docs/ANDROID.md)

测试：[tests/test_android_devices.py](../../tests/test_android_devices.py)

直接静态依赖：`web-ui`

## 桌面与安卓 API · http-devices

分类：Web 接口。窗口观察、设备树/锁帧、受观察约束的手动动作和 MJPEG。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/devices.py](../../src/omni/channels/web_api/devices.py) | 123 | 桌面与安卓 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。 |

文档：[docs/ANDROID.md](../../docs/ANDROID.md)、[docs/DESKTOP.md](../../docs/DESKTOP.md)

测试：[tests/test_web.py](../../tests/test_web.py)、[tests/test_web_api.py](../../tests/test_web_api.py)

直接静态依赖：`assembly`、`http-android`、`http-contracts`

## HTTP 请求契约 · http-contracts

分类：Web 接口。公共请求模型；只承载数据声明，旧 web 模型名继续显式转发。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py) | 132 | 工作台请求契约；路由共用，旧 web.py 继续显式转发公共模型名。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[tests/test_web_api.py](../../tests/test_web_api.py)

直接静态依赖：无内部模块依赖

## HTTP 路由装配 · http-routing

分类：Web 接口。聚合注入 Runtime 的领域路由，由应用入口统一挂载；不与请求契约合并为同一职责。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/web_api/__init__.py](../../src/omni/channels/web_api/__init__.py) | 44 | 工作台领域路由聚合；应用生命周期与统一鉴权仍由 web.py 持有。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)、[docs/REFACTORING.md](../../docs/REFACTORING.md)

测试：[tests/test_web_api.py](../../tests/test_web_api.py)

直接静态依赖：`assembly`、`command-jobs`、`http-android`、`http-approvals`、`http-artifacts`、`http-automation`、`http-conversation`、`http-devices`、`http-mcp`、`http-memory`、`http-operations`、`http-publications`、`http-runs`、`http-sessions`、`http-skills`、`http-storage`

## CLI 入口 · cli

分类：交互与终端。终端对话命令与 Runtime 适配。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/__main__.py](../../src/omni/__main__.py) | 4 | python -m omni 入口。 |
| [src/omni/channels/cli.py](../../src/omni/channels/cli.py) | 132 | 本地终端 REPL 入口：uv run omni。 |

文档：[docs/HANDOFF.md](../../docs/HANDOFF.md)

测试：关联模块集成验证

直接静态依赖：`assembly`、`config`、`models`、`safety`

## QQ 入口 · qq-channel

分类：交互与终端。QQ 场景队列、事件去重和消息收发适配。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/qq.py](../../src/omni/channels/qq.py) | 316 | QQ 通道入口：uv run omni-qq（需先启动 NapCat 并在 config.yaml 开启 qq）。 |

文档：[docs/QQ_SCENARIOS.md](../../docs/QQ_SCENARIOS.md)

测试：[tests/test_qq.py](../../tests/test_qq.py)、[tests/test_qq_scope.py](../../tests/test_qq_scope.py)

直接静态依赖：`assembly`、`channels`、`config`、`engine`、`models`、`platform`、`qq-tools`、`safety`

## 心跳入口 · heartbeat

分类：交互与终端。定时领取、执行、投递与周期清理。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/heartbeat.py](../../src/omni/channels/heartbeat.py) | 81 | 心跳入口：uv run omni-heartbeat（定时任务常驻调度，无人值守 fail-closed）。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：[tests/test_cron.py](../../tests/test_cron.py)

直接静态依赖：`assembly`、`channels`、`config`、`engine`、`platform`、`safety`、`storage`

## 入口公共适配 · channels

分类：交互与终端。入口生命周期和只读诊断/检索评测命令。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/channels/__init__.py](../../src/omni/channels/__init__.py) | 1 | 入口层：QQ(NapCat)、CLI、Web（P0 只有 CLI）。 |
| [src/omni/channels/doctor.py](../../src/omni/channels/doctor.py) | 31 | 只读 doctor 命令；不组装运行态，以免诊断触发建库、迁移或后台任务。 |
| [src/omni/channels/eval_retrieval.py](../../src/omni/channels/eval_retrieval.py) | 75 | 在临时数据库评测合成样本；hybrid 模式须显式传配置，默认完全离线。 |
| [src/omni/channels/lifecycle.py](../../src/omni/channels/lifecycle.py) | 22 | 常驻入口响应本次启动的停止请求；不构造运行态、不自行终止其它进程。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：关联模块集成验证

直接静态依赖：`assembly`、`config`、`engine`、`platform`

## 个人记忆工具 · personal

分类：工具能力。时钟、事实、档案、常驻、todo、摘要与手动整理各自独立。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/personal/__init__.py](../../src/omni/tools/personal/__init__.py) | 41 | 个人内置能力的注册入口：事实、档案、常驻、待办、摘要和整理。 |
| [src/omni/tools/personal/clock.py](../../src/omni/tools/personal/clock.py) | 23 | 个人能力：clock；工具仅使用注入依赖，不构造运行时。 |
| [src/omni/tools/personal/digest.py](../../src/omni/tools/personal/digest.py) | 67 | 个人能力：digest；工具仅使用注入依赖，不构造运行时。 |
| [src/omni/tools/personal/facts.py](../../src/omni/tools/personal/facts.py) | 184 | 个人能力：facts；工具仅使用注入依赖，不构造运行时。 |
| [src/omni/tools/personal/profile.py](../../src/omni/tools/personal/profile.py) | 87 | 个人能力：profile；工具仅使用注入依赖，不构造运行时。 |
| [src/omni/tools/personal/refine.py](../../src/omni/tools/personal/refine.py) | 58 | 个人能力：refine；工具仅使用注入依赖，不构造运行时。 |
| [src/omni/tools/personal/resident.py](../../src/omni/tools/personal/resident.py) | 62 | 个人能力：resident；工具仅使用注入依赖，不构造运行时。 |
| [src/omni/tools/personal/todos.py](../../src/omni/tools/personal/todos.py) | 85 | 个人能力：todos；工具仅使用注入依赖，不构造运行时。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[tests/test_tools.py](../../tests/test_tools.py)、[tests/test_profile_tools.py](../../tests/test_profile_tools.py)、[tests/test_resident.py](../../tests/test_resident.py)

直接静态依赖：`storage`、`toolkit`

## 技能工具 · skills

分类：插件与扩展。技能查询/创建/编辑/回滚与外来技能隔离安装入口。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/skills/__init__.py](../../src/omni/tools/skills/__init__.py) | 4 | skills 能力域注册入口。 |
| [src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py) | 399 | 技能工具集（toolset=core）：技能的渐进披露与维护。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[tests/test_skills.py](../../tests/test_skills.py)、[tests/test_skill_inbox.py](../../tests/test_skill_inbox.py)

直接静态依赖：`storage`、`toolkit`

## 调度工具 · scheduling

分类：工具能力。周期/一次性任务、编辑、依赖和执行查询。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/scheduling/__init__.py](../../src/omni/tools/scheduling/__init__.py) | 4 | scheduling 能力域注册入口。 |
| [src/omni/tools/scheduling/tools.py](../../src/omni/tools/scheduling/tools.py) | 306 | 定时任务工具集（toolset=cron）：agent 可自主排程（Hermes cronjob_tools 精神）。 |

文档：[docs/HANDOFF.md](../../docs/HANDOFF.md)

测试：[tests/test_cron.py](../../tests/test_cron.py)

直接静态依赖：`safety`、`storage`、`toolkit`

## 委派工具 · delegation

分类：工具能力。受父预算/权限/取消约束的子任务注册入口。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/delegation/__init__.py](../../src/omni/tools/delegation/__init__.py) | 4 | delegation 能力域注册入口。 |
| [src/omni/tools/delegation/tools.py](../../src/omni/tools/delegation/tools.py) | 128 | 子任务委派（Hermes delegate_task 同款）：把独立子任务交给一个干净会话去跑。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[tests/test_delegate.py](../../tests/test_delegate.py)

直接静态依赖：`storage`、`toolkit`

## 系统服务工具 · system-tools

分类：工具能力。状态查询和受审批的服务操作适配。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/system/__init__.py](../../src/omni/tools/system/__init__.py) | 4 | system 能力域注册入口。 |
| [src/omni/tools/system/tools.py](../../src/omni/tools/system/tools.py) | 117 | 系统工具集（toolset=system）：AI 自查本地服务状态、引导用户处置。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)

测试：[tests/test_system.py](../../tests/test_system.py)

直接静态依赖：`platform`、`toolkit`

## Agent shell 工具 · shell

分类：工具能力。只读命令语法、执行器、硬黑名单与人工确认命令；不复用交互式 PTY。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/shell/__init__.py](../../src/omni/tools/shell/__init__.py) | 36 | shell 能力域：SAFE 观察（结构化 argv）与 CONFIRM 执行（shell 语义）。 |
| [src/omni/tools/shell/danger.py](../../src/omni/tools/shell/danger.py) | 45 | 硬性安全黑名单：灾难级操作命中即拒——即便用户已确认也不执行。 |
| [src/omni/tools/shell/process.py](../../src/omni/tools/shell/process.py) | 181 | 进程边界：argv 执行、输出上限、超时后的进程树回收与"副作用是否已知"。 |
| [src/omni/tools/shell/spec.py](../../src/omni/tools/shell/spec.py) | 506 | SAFE 命令解析：把命令串解析成**受控 argv**，解析不出来就拒绝。 |
| [src/omni/tools/shell/tools.py](../../src/omni/tools/shell/tools.py) | 263 | shell 工具集（toolset=shell）：peek_command（SAFE 观察）与 run_command（CONFIRM 执行）。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[tests/test_shell_tools.py](../../tests/test_shell_tools.py)

直接静态依赖：`config`、`toolkit`

## 文件工具 · files

分类：工具能力。允许根内的列表/搜索/读取/差异与受控写入回滚。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/files/__init__.py](../../src/omni/tools/files/__init__.py) | 19 | 结构化文件工具域（D13）：工作区内的有界读写、差异预览与回滚。 |
| [src/omni/tools/files/history.py](../../src/omni/tools/files/history.py) | 161 | 工作区文件的修订快照（D13）：写前备份、可回滚。 |
| [src/omni/tools/files/io.py](../../src/omni/tools/files/io.py) | 324 | 文本读写的有界实现（D13）：编码识别、二进制拒绝、窗口读取、原子替换。 |
| [src/omni/tools/files/paths.py](../../src/omni/tools/files/paths.py) | 282 | 工作区路径边界（D13）：规范化、根校验、敏感项与"检查后不变"。 |
| [src/omni/tools/files/search.py](../../src/omni/tools/files/search.py) | 169 | 有界目录遍历与内容搜索（D13）。 |
| [src/omni/tools/files/tools.py](../../src/omni/tools/files/tools.py) | 703 | 结构化文件工具（D13，toolset=files）。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[tests/test_files.py](../../tests/test_files.py)

直接静态依赖：`platform`、`toolkit`

## 网页读取工具 · web-fetch

分类：工具能力。公开网页的目标网络、重定向、体积限制与正文提取。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/web/__init__.py](../../src/omni/tools/web/__init__.py) | 19 | 通用网页读取（D14）：有界 HTTP 抓取 + 正文提取。 |
| [src/omni/tools/web/extract.py](../../src/omni/tools/web/extract.py) | 226 | 正文提取（D14）：把 HTML 变成"能读的文本"，不执行任何东西。 |
| [src/omni/tools/web/net.py](../../src/omni/tools/web/net.py) | 420 | 有界 HTTP 抓取（D14）：协议、目标网络、重定向、大小、超时全部受控。 |
| [src/omni/tools/web/tools.py](../../src/omni/tools/web/tools.py) | 200 | 网页读取工具（D14，toolset=web）：`web_read`。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[tests/test_web_fetch.py](../../tests/test_web_fetch.py)

直接静态依赖：`toolkit`

## 浏览器工具 · browser

分类：工具能力。Playwright 所有权、表单协议、观察与写预算。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/browser/__init__.py](../../src/omni/tools/browser/__init__.py) | 5 | 浏览器能力域（P3）：Playwright 驱动 + 写预算 + 工具。 |
| [src/omni/tools/browser/budget.py](../../src/omni/tools/browser/budget.py) | 73 | 每日写操作预算：写走 UI 的第一道闸（bili-whale 的 quota 精神，xuexi/NOTES.md §4.3）。 |
| [src/omni/tools/browser/driver.py](../../src/omni/tools/browser/driver.py) | 413 | Playwright 浏览器驱动：单一所有者线程 + profile 跨进程租约 + 拟人节奏。 |
| [src/omni/tools/browser/forms.py](../../src/omni/tools/browser/forms.py) | 166 | 固定配置的 UI 表单观察/提交；所有操作在 BrowserDriver 的所有者线程执行。 |
| [src/omni/tools/browser/tools.py](../../src/omni/tools/browser/tools.py) | 201 | 浏览器工具集（toolset=browser）：读 SAFE，写 CONFIRM。 |

文档：[docs/BILIBILI_WRITING.md](../../docs/BILIBILI_WRITING.md)

测试：[tests/test_browser.py](../../tests/test_browser.py)、[tests/test_browser_owner.py](../../tests/test_browser_owner.py)

直接静态依赖：`config`、`storage`、`toolkit`

## 安卓工具 · android

分类：工具能力。ADB、防护、UI 树、观察令牌、锁帧和输入法。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/android/__init__.py](../../src/omni/tools/android/__init__.py) | 7 | 安卓能力域（P4 地基）：纯 ADB 协议层 + 高层语义驱动 + 工具。 |
| [src/omni/tools/android/adb.py](../../src/omni/tools/android/adb.py) | 244 | 纯 ADB 协议层：设备无关的 adb 命令封装（不知道 LLM 与语义）。 |
| [src/omni/tools/android/driver.py](../../src/omni/tools/android/driver.py) | 338 | Android 观察令牌、单设备互斥、一次动作与回读。磁盘缓存仅用于展示。 |
| [src/omni/tools/android/process.py](../../src/omni/tools/android/process.py) | 29 | ADB 客户端的有界读取与超时；不把客户端退出当作设备操作已撤销。 |
| [src/omni/tools/android/stream.py](../../src/omni/tools/android/stream.py) | 152 | 按设备共享最新 JPEG；预览不产生观察授权，慢客户端跳过旧帧。 |
| [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py) | 237 | 安卓工具集（toolset=android）：观察 SAFE，动作 CONFIRM。 |
| [src/omni/tools/android/ui.py](../../src/omni/tools/android/ui.py) | 89 | UI 树解析（纯函数）：uiautomator XML → 结构化元素 → 文本树。 |

文档：[docs/ANDROID.md](../../docs/ANDROID.md)

测试：[tests/test_android.py](../../tests/test_android.py)、[tests/test_android_observations.py](../../tests/test_android_observations.py)

直接静态依赖：`config`、`platform`、`toolkit`

## 桌面工具 · desktop

分类：工具能力。Windows UIAutomation、窗口观察、租约与受控语义动作。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/desktop/__init__.py](../../src/omni/tools/desktop/__init__.py) | 1 | Windows 桌面域：观察绑定、语义后台动作、写后回读。 |
| [src/omni/tools/desktop/driver.py](../../src/omni/tools/desktop/driver.py) | 136 |  |
| [src/omni/tools/desktop/native.py](../../src/omni/tools/desktop/native.py) | 40 | 静态 UIAutomation 辅助程序。用户参数只经 stdin JSON 传递，不拼接 PowerShell。 |
| [src/omni/tools/desktop/tools.py](../../src/omni/tools/desktop/tools.py) | 43 |  |
| [src/omni/tools/desktop/uia.ps1](../../src/omni/tools/desktop/uia.ps1) | 178 | 平台脚本或界面资源 |

文档：[docs/DESKTOP.md](../../docs/DESKTOP.md)

测试：[tests/test_desktop.py](../../tests/test_desktop.py)

直接静态依赖：`config`、`toolkit`

## B站工具 · bilibili

分类：工具能力。读 API/转写及注入浏览器协议的草稿与受控写。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/bilibili/__init__.py](../../src/omni/tools/bilibili/__init__.py) | 1 | B站能力域（P1：只读）。api 层不知道 LLM，tools 层不知道 HTTP 细节。 |
| [src/omni/tools/bilibili/api.py](../../src/omni/tools/bilibili/api.py) | 256 | B站读 API 封装：只读端点 + 目标归一化 + WBI 签名。 |
| [src/omni/tools/bilibili/asr.py](../../src/omni/tools/bilibili/asr.py) | 96 | 无字幕视频的 ASR 兜底（faster-whisper，本地转写）。 |
| [src/omni/tools/bilibili/publishing.py](../../src/omni/tools/bilibili/publishing.py) | 126 | B站草稿→人工批准→一次 UI 提交→记录回读；浏览器能力通过协议注入。 |
| [src/omni/tools/bilibili/tools.py](../../src/omni/tools/bilibili/tools.py) | 167 | B站读工具集（toolset=bilibili，全部 SAFE 只读）。 |
| [src/omni/tools/bilibili/wbi.py](../../src/omni/tools/bilibili/wbi.py) | 43 | B站 WBI 签名（纯算法，无网络依赖）。 |
| [src/omni/tools/bilibili/write_tools.py](../../src/omni/tools/bilibili/write_tools.py) | 50 | B站草稿工具及逐次人工发布审批；所有浏览器依赖由组装根注入。 |

文档：[docs/BILIBILI_WRITING.md](../../docs/BILIBILI_WRITING.md)

测试：[tests/test_bilibili_tools.py](../../tests/test_bilibili_tools.py)、[tests/test_bilibili_publishing.py](../../tests/test_bilibili_publishing.py)

直接静态依赖：`config`、`storage`、`toolkit`

## QQ 工具 · qq-tools

分类：工具能力。OneBot 客户端与白名单内主动消息。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/qq/__init__.py](../../src/omni/tools/qq/__init__.py) | 4 | QQ 能力域（P2）：OneBot v11 客户端（NapCat）与主动消息工具。 |
| [src/omni/tools/qq/client.py](../../src/omni/tools/qq/client.py) | 276 | OneBot v11 正向 WebSocket 客户端（对接 NapCat）。 |
| [src/omni/tools/qq/tools.py](../../src/omni/tools/qq/tools.py) | 93 | QQ 工具集（toolset=qq）：主动发消息。 |

文档：[docs/QQ_SCENARIOS.md](../../docs/QQ_SCENARIOS.md)

测试：[tests/test_outbox.py](../../tests/test_outbox.py)

直接静态依赖：`safety`、`toolkit`

## MCP 扩展适配 · mcp

分类：插件与扩展。外部 stdio 工具发现、授权、漂移撤权与子进程生命周期。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/mcp/__init__.py](../../src/omni/tools/mcp/__init__.py) | 1 | 外部 MCP 能力域：本地 stdio 客户端与工具适配；不依赖 LLM 或内核。 |
| [src/omni/tools/mcp/client.py](../../src/omni/tools/mcp/client.py) | 529 | 同步 agent 到异步 MCP 的桥接：每服务一个会话所有者线程。 |
| [src/omni/tools/mcp/tools.py](../../src/omni/tools/mcp/tools.py) | 77 | MCP 工具适配：渐进发现、用户审核只读调用、人工确认调用；不决定传输细节。 |

文档：[docs/MCP.md](../../docs/MCP.md)

测试：[tests/test_mcp.py](../../tests/test_mcp.py)

直接静态依赖：`config`、`platform`、`toolkit`

## 工具公共协议与兼容层 · toolkit

分类：工具能力。Registry、ToolResult、SessionRef/RunRef、路径策略、截图协议及旧 import 薄转发。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/tools/__init__.py](../../src/omni/tools/__init__.py) | 7 | 工具层：registry（注册表）+ builtin（内置工具集）+ 后续各能力域包。 |
| [src/omni/tools/builtin.py](../../src/omni/tools/builtin.py) | 13 | 旧导入兼容层；新增个人工具请进入 tools/personal/。 |
| [src/omni/tools/cron_tools.py](../../src/omni/tools/cron_tools.py) | 4 | 旧导入兼容层；实现位于 tools/scheduling/。 |
| [src/omni/tools/delegate_tools.py](../../src/omni/tools/delegate_tools.py) | 4 | 旧导入兼容层；实现位于 tools/delegation/。 |
| [src/omni/tools/pathpolicy.py](../../src/omni/tools/pathpolicy.py) | 83 | 工作区路径与敏感项的单一策略（D13）。 |
| [src/omni/tools/registry.py](../../src/omni/tools/registry.py) | 253 | 工具注册表：LLM function calling 的统一入口（Registry 模式）。 |
| [src/omni/tools/result.py](../../src/omni/tools/result.py) | 132 | 工具结果的结构化契约（D09）。 |
| [src/omni/tools/runctx.py](../../src/omni/tools/runctx.py) | 48 | 低层可见的"当前运行"引用（D09）。 |
| [src/omni/tools/shell_tools.py](../../src/omni/tools/shell_tools.py) | 38 | 兼容薄层：旧的 `omni.tools.shell_tools` 导入路径继续可用。 |
| [src/omni/tools/shots.py](../../src/omni/tools/shots.py) | 66 | 截图工具与 ArtifactStore 的桥（D15/A13）。 |
| [src/omni/tools/skill_tools.py](../../src/omni/tools/skill_tools.py) | 4 | 旧导入兼容层；实现位于 tools/skills/。 |
| [src/omni/tools/system_tools.py](../../src/omni/tools/system_tools.py) | 4 | 旧导入兼容层；实现位于 tools/system/。 |

文档：[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)

测试：[tests/test_tools.py](../../tests/test_tools.py)

直接静态依赖：`delegation`、`personal`、`safety`、`scheduling`、`shell`、`skills`、`system-tools`

## 插件社区 · community

分类：插件与扩展。社区客户端、工具适配与独立分发服务器。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/plugins/community/__init__.py](../../src/omni/plugins/community/__init__.py) | 5 | 插件社区公共入口；客户端、工具与服务器分别维护。 |
| [src/omni/plugins/community/client.py](../../src/omni/plugins/community/client.py) | 288 | 插件社区客户端：搜索 / 详情 / 安装 / 发布。 |
| [src/omni/plugins/community/server.py](../../src/omni/plugins/community/server.py) | 261 | 插件社区参考服务器（FastAPI + 文件存储，单 owner 部署）。 |
| [src/omni/plugins/community/tools.py](../../src/omni/plugins/community/tools.py) | 142 | 社区工具适配；HTTP/发布逻辑由注入客户端承担。 |
| [src/omni/plugins/server.py](../../src/omni/plugins/server.py) | 7 | 兼容 python -m omni.plugins.server；实现位于 plugins/community/server.py。 |

文档：[docs/PLUGIN_SYSTEM.md](../../docs/PLUGIN_SYSTEM.md)

测试：[tests/test_plugin_community.py](../../tests/test_plugin_community.py)

直接静态依赖：`platform`、`plugins`、`toolkit`

## 插件宿主 · plugins

分类：插件与扩展。manifest、隔离校验、人工激活、插件注册与生命周期；进程内受信代码。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/plugins/__init__.py](../../src/omni/plugins/__init__.py) | 16 | 插件子系统（顶层包，与 channels/bootstrap 同级）。 |
| [src/omni/plugins/host.py](../../src/omni/plugins/host.py) | 799 | 插件宿主：外来功能包的隔离安装、激活、装载与卸载。 |
| [src/omni/plugins/manifest.py](../../src/omni/plugins/manifest.py) | 104 | 插件清单（manifest.json）：插件包的唯一身份与能力声明。 |
| [src/omni/plugins/tools.py](../../src/omni/plugins/tools.py) | 181 | 插件生命周期工具（面向 agent）：安装 → 审阅 → 激活 → 启停 → 卸载。 |

文档：[docs/PLUGIN_SYSTEM.md](../../docs/PLUGIN_SYSTEM.md)

测试：[tests/test_plugins.py](../../tests/test_plugins.py)

直接静态依赖：`platform`、`safety`、`toolkit`

## 项目索引基础设施 · catalog

分类：开发与验证。只读 AST 扫描、分类和确定性文档生成；不会执行待索引代码。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/system/catalog/__init__.py](../../src/omni/system/catalog/__init__.py) | 4 | 项目索引：只解析源码与显式分类，不导入被索引模块、不接触运行数据。 |
| [src/omni/system/catalog/render.py](../../src/omni/system/catalog/render.py) | 46 | 索引输出：从同一结构生成模块表、API 表和可机器查询的 JSON。 |
| [src/omni/system/catalog/scan.py](../../src/omni/system/catalog/scan.py) | 165 | 静态源码索引；扫描范围固定为注入根的 src/omni，不读取 config/data/.env。 |
| [src/omni/system/catalog/schema.py](../../src/omni/system/catalog/schema.py) | 105 | 模块分类与关键调用链；分层规则仍以 docs/ARCHITECTURE.md 为准。 |

文档：[docs/PROJECT_INDEX.md](../../docs/PROJECT_INDEX.md)

测试：[tests/test_project_catalog.py](../../tests/test_project_catalog.py)

直接静态依赖：`config`

## 平台基础设施 · platform

分类：平台与数据。服务、进程身份、配置写回、锁/原子文件、日志、doctor、备份与验收。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/system/__init__.py](../../src/omni/system/__init__.py) | 5 | 本地系统域：服务管理（NapCat / 常驻通道启停与状态）+ 配置读写。 |
| [src/omni/system/atomic.py](../../src/omni/system/atomic.py) | 39 | 原子写文件（A09 的同源问题）。 |
| [src/omni/system/backup.py](../../src/omni/system/backup.py) | 507 | 离线备份与恢复（D25 的提前子项，D05 迁移前必须先能用）。 |
| [src/omni/system/config_io.py](../../src/omni/system/config_io.py) | 396 | config.yaml 安全读写：保留注释的定向修改（只动白名单键）。 |
| [src/omni/system/doctor.py](../../src/omni/system/doctor.py) | 93 | 只读诊断：配置、安装元数据、DB 完整性和进程身份；不修复、不调用模型。 |
| [src/omni/system/filelock.py](../../src/omni/system/filelock.py) | 157 | 跨进程文件锁（D08）。 |
| [src/omni/system/observability.py](../../src/omni/system/observability.py) | 102 | 有界诊断日志。事件仅保留关联字段，排除对话、工具参数与结果正文。 |
| [src/omni/system/process_identity.py](../../src/omni/system/process_identity.py) | 231 | 进程身份与 Windows 句柄终止；不以裸 PID 或端口身份授权终止。 |
| [src/omni/system/provenance.py](../../src/omni/system/provenance.py) | 27 | 服务启动源码指纹，不依赖 Git 可用性，不包含配置/凭据/运行数据。 |
| [src/omni/system/release_gate.py](../../src/omni/system/release_gate.py) | 64 | 校验真实证据与常驻结果；永不执行发布或代替用户批准。 |
| [src/omni/system/services.py](../../src/omni/system/services.py) | 480 | 本地服务管理：NapCat 与 OmniOperator 常驻服务的 启动/停止/状态。 |
| [src/omni/system/soak.py](../../src/omni/system/soak.py) | 263 | 只读常驻采样与连续性核验；不会启动服务、调用模型或投递消息。 |
| [src/omni/system/stderr_log.py](../../src/omni/system/stderr_log.py) | 56 | 子进程 stderr 的有限管道泵：持续排空、跨块脱敏与文件轮转。 |

文档：[docs/OPERATIONS.md](../../docs/OPERATIONS.md)、[docs/ACCEPTANCE.md](../../docs/ACCEPTANCE.md)

测试：[tests/test_backup.py](../../tests/test_backup.py)、[tests/test_process_identity.py](../../tests/test_process_identity.py)

直接静态依赖：`config`

## Python 包入口 · package

分类：开发与验证。安装包标识。

| 文件 | 行数 | 职责 |
|---|---:|---|
| [src/omni/__init__.py](../../src/omni/__init__.py) | 3 | OmniOperator：像人一样生活在平台里的具身个人 agent。 |

文档：

测试：关联模块集成验证

直接静态依赖：无内部模块依赖
