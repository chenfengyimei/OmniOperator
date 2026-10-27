# OmniOperator 架构说明

> 本文是模块职责与依赖规则的唯一权威。**分层与组装根规则由 `tests/test_arch.py` 用 AST 强制执行**——改坏会直接测试红，不靠自觉。

## 1. 分层总览

```
              ┌─────────────────────────────────────┐
              │         入口层 channels/            │  cli.py（P2: qq.py  P5: web/）
              │  职责：界面展示 + 输入解析 + 调门面   │
              └──────────────────┬──────────────────┘
                                 │ 只碰 Runtime 门面
              ┌──────────────────▼──────────────────┐
              │        bootstrap.py 组装根            │  唯一允许构造
              │                                     │  Gateway / MemoryStore /
              └──────────────────┬──────────────────┘  ToolRegistry / Agent
        ┌───────────┬───────────┼───────────┬────────────┐
        ▼           ▼           ▼           ▼            ▼
     core/        llm/       memory/      tools/       safety/
     Agent 编排    Gateway    Store        Registry      Approver
     （内核循环）  （LiteLLM） （SQLite）   + builtin      （风险审批）
                                                  ↑
        config.py（.env + config.yaml，叶子层）────┘ 被所有层引用
```

## 2. 模块职责表

| 模块 | 职责 | 明确不做 | 关键文件 |
|---|---|---|---|
| `config.py` | 加载 .env 密钥与 config.yaml 配置 | 不含任何业务逻辑 | `config.py` |
| `core/` | Agent 编排循环：上下文组装（超限自动压缩为摘要，工具调用对不拆开）→ 不变量断言 → LLM → 工具 → 回答；步数预算 + grace call；回合后异步 review（记忆自进化）；心跳调度器（先推进再派发/至多一次）+ 任务链 context_from | 不实现具体工具、不感知通道存在；review 的工具白名单与执行器由 bootstrap 注入 | `core/agent.py`、`core/review.py`、`core/cron.py`、`core/compress.py` |
| `llm/` | LiteLLM 适配：多供应商归一、模型路由（chat/vision/local/review）、错误包装 | 不做记忆、不管理会话 | `llm/gateway.py` |
| `memory/` | SQLite 仓储：会话 / 消息 / 事实 / 用户档案（限额+报错逼合并）/ FTS5 全文检索 / review 日志 / 通道绑定 / 会话待办；常驻小记忆（MEMORY.md 式快照注入+限额合并协议）；技能存储（SKILL.md+linter，patch/rewrite 版本化）；定时任务存储（jobs + 执行账本，原子写） | 不知道 LLM 与工具的存在 | `memory/store.py`、`memory/resident.py`、`memory/skills.py`、`memory/cron.py` |
| `tools/` | Registry（注册/查找/规格生成/派发）+ 工具集（core：时间/记忆/档案/技能/活动摘要/待办/常驻笔记/子任务委派；shell：命令执行（只读白名单 SAFE + 任意 CONFIRM + 硬黑名单）；bilibili：读；qq：主动消息；cron：定时任务管理；browser：读 SAFE + 写 CONFIRM；android：读 SAFE + 写 CONFIRM） | 能力域之间互不 import | `tools/registry.py`、`tools/builtin.py`、`tools/shell_tools.py`、`tools/delegate_tools.py`、`tools/skill_tools.py`、`tools/cron_tools.py`、`tools/bilibili/`、`tools/qq/`、`tools/browser/`、`tools/android/` |
| `safety/` | 风险分级（SAFE/CONFIRM/DANGEROUS）+ 按上下文审批策略（含 smart 档：辅助模型预筛，参数视为不可信输入）+ 跨线程确认代理（Web 横幅确认流） | 不实现工具、不知道通道细节；模型调用经注入的 ask_model（保持叶子层纯净） | `safety/gates.py`、`safety/smart.py`、`safety/broker.py` |
| `channels/` | 入口适配：展示、输入解析、调 Runtime；每通道一个文件（CLI / Web 工作台（含内置终端 ConPTY）/ QQ / 心跳） | 不构造任何核心对象 | `channels/cli.py`、`channels/web.py`、`channels/terminal.py`、`channels/qq.py`、`channels/heartbeat.py` |
| `bootstrap.py` | 组装根：构造全部对象、暴露 Runtime 门面（new/use session） | 不含业务逻辑 | `bootstrap.py` |

## 3. 依赖规则（`tests/test_arch.py` 强制）

### 3.1 import 分层
依赖方向**自上而下**，禁止倒挂：

- `channels` / `bootstrap`（顶层）→ 可依赖一切
- `core` → 可依赖 `llm` / `memory` / `tools` / `safety` / `config`，禁止依赖 `channels` / `bootstrap`
- `tools` → 可依赖 `memory` / `safety` / `config`，禁止依赖 `core` / `llm` / `channels` / `bootstrap`
- `llm` → 只依赖 `config`
- `memory` → 只依赖 `config`
- `safety` / `config`（叶子）→ 不允许 import 任何内部模块

### 3.2 组装根规则
`Gateway`、`MemoryStore`、`SkillStore`、`JobStore`、`ToolRegistry`、`Agent`、`TurnEndReviewer`、`BrowserDriver`、`WriteBudget`、`ApprovalBroker` 十个类的**构造调用只允许出现在 `bootstrap.py`**（tests 除外）。通道与内核通过构造函数注入持有引用。

好处：换实现（如 P1 记忆升级向量库）只动 bootstrap 一处；全部依赖关系在构造处一眼可见；工具集以 `register_xxx_tools(registry, deps)` 模式注册进唯一注册表，多能力域无冲突合并。

### 3.3 错误传递约定
- **工具执行错误 / 审批拒绝**：以字符串回传给 LLM（让它自行换路径），不在循环里抛异常中断
- **审批默认值**：未注入审批器按 DenyApprover 处理；通用写工具声明 `requires_human=True`，smart 不可代替人工；DANGEROUS 始终拒绝，smart 不覆盖无人值守策略；只有完整的一词裁决有效
- **LLM 调用失败**：Gateway 包装成 `GatewayError` 抛给通道层
- **通道层**：只捕获 `GatewayError`，不感知 litellm 原始异常

## 4. 设计模式落位

| 模式 | 位置 | 解决什么问题 |
|---|---|---|
| Adapter（适配器） | `llm/gateway.py`、`channels/*` | 屏蔽 LiteLLM 与各平台差异，内核只见统一接口 |
| Repository（仓储） | `memory/store.py` | 持久化细节与业务解耦，P1 升级向量库不动上层 |
| Registry（注册表） | `tools/registry.py` | 新工具零侵入接入；toolset 分组即能力开关 |
| Strategy（策略） | `safety/gates.py` | 同一审批接口按上下文换策略：CLI 问人（ConsoleApprover）、无人值守拒绝（DenyApprover）、测试放行（AllowAllApprover） |
| Facade（门面） | `bootstrap.Runtime` | 通道只见 new/use/chat，不碰内部对象 |
| Composition Root（组装根） | `bootstrap.py` | 依赖只在一处发生，通道薄、全局可替换 |
| 依赖注入 | `Agent.__init__` 等全构造注入 | 测试可整体替换假件（见 `tests/test_agent.py` 的 ScriptedGateway） |
| 会话上下文（thread-local） | `tools/registry.SessionRef` + `core/agent.py` | 会话级工具（todo）在 CLI/Web/QQ/心跳并发下各线程取到自己的当前会话 |
| Grace call | `core/agent.py` 循环尾部 | 步数预算耗尽仍强制收敛出最终回答，防死循环不粗暴截断 |

## 5. 新能力接入指南（checklist）

### 5.1 加一个工具（例：P1 的 B站"看视频"）
1. 新建能力域包 `tools/bilibili/`：`api.py`（纯 HTTP 封装，**不知道 LLM 存在**）+ `tools.py`（薄包装成 Tool，**不知道 HTTP 细节**）
2. 每个 Tool 声明 `toolset`（如 `"bilibili"`）与 `risk`（读=SAFE；私信/发布=CONFIRM；支付/删除=DANGEROUS）
3. 写 `register_bilibili_tools(registry, deps...)`，照抄 `tools/builtin.py` 的模式
4. 在 `bootstrap.build_runtime` 接线（唯一需要改的既有文件）
5. 单测：api 层用假响应、tools 层用假 store；`uv run pytest` 全绿（架构守卫会自动检查新代码）

### 5.2 加一个通道（例：P2 的 QQ）
1. 新建 `channels/qq.py`：实现「收消息 → `runtime.agent.chat` → 发回消息」
2. 只从 `bootstrap.build_runtime(approver=...)` 拿 Runtime；审批策略按上下文选择，QQ 触发的写操作默认从严
3. 不 import litellm/memory 内部实现（test_arch 会拦截）
4. P2 同时引入 `core/events.py` 事件总线（见 §6）

### 5.3 加一种记忆（例：P1 人物档案）
1. 在 `memory/store.py` 加表与方法（上层不感知 SQL）
2. 需要暴露给 LLM 的，包装成工具或注入系统提示词——在哪注入由 bootstrap 决定

### 5.4 技能参考文件
`SkillStore` 负责 `references/` 路径校验、目录清单与有限文本读取；工具层通过
`skill_view(name, path=...)` 渐进披露，不执行附件。绝对路径、目录穿越、符号链接、
Windows junction/ADS 均拒绝，单文件上限 64 KiB；已有 `skills_root` 注入覆盖全部参考路径。

## 6. Phase 架构演进预告

| Phase | 新增 | 结构原则 |
|---|---|---|
| P1 | `tools/bilibili/`（读 API + WBI 签名）、memory 向量检索（sqlite-vec）、回合后 review 线程、`skills/` 技能系统 | api 层不知道 LLM；review 是 Gateway 的独立消费者（换廉价模型跑） |
| P2 | `channels/qq.py`（NapCat OneBot v11）、`core/events.py` 事件总线 | 通道把平台消息归一成 Event 投给总线；P5 心跳复用同一条总线 |
| P3 | `tools/browser/`（Playwright 持久 profile）、DANGEROUS 显式授权机制 | 写操作全走审批；拟人节奏做在工具层（pacing），不散落在循环里 |
| P4 | `tools/android/`（AdbDevice + uiautomator2 + ADBKeyboard） | 设备驱动独立模块，agent 只见语义工具（ui_tree / click / type） |
| P5 | `core/cron.py`（心跳）、Web 面板 | cron 一律 DenyApprover（fail-closed）；夜间整理 = 批量 review |

## 7. 编码约定

- Python 3.12+，全量类型标注，`from __future__ import annotations`
- 跨平台：只用 pathlib / 纯 Python，Windows 与 macOS 都要能跑；平台差异隔离在能力域内部
- 注释写"为什么"不写"是什么"；每个模块 docstring 写清「职责 + 明确不做」
- 每个新模块必须有单测；架构改动必须过 `tests/test_arch.py` 的两条守卫
