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
| `memory/` | 两个 SQLite 库：`memory.db`（会话 / 消息 / 事实 / 用户档案（限额+报错逼合并）/ FTS5 全文检索 / review 日志 / 通道绑定 / 会话待办）与 `state.db`（定时任务 + 执行账本 + 写额度 + 迁移记录，schema_version/唯一键/短事务/原子领取/revision）；常驻小记忆（MEMORY.md 式快照注入+限额合并协议+跨进程锁）；技能存储（SKILL.md+linter，patch/rewrite 版本化，CAS+修订历史+回滚）；`memory/revisions.py` 提供两者的内容快照 | 不知道 LLM 与工具的存在 | `memory/store.py`、`memory/state.py`、`memory/jobs.py`、`memory/cron.py`（兼容再导出）、`memory/resident.py`、`memory/skills.py`、`memory/revisions.py` |
| `tools/` | Registry（注册/查找/规格生成/派发）+ 工具集（core：时间/记忆/档案/技能/活动摘要/待办/常驻笔记/子任务委派；shell：结构化只读观察 SAFE + 任意命令 CONFIRM + 硬黑名单 + 进程树回收；bilibili：读；qq：主动消息；cron：定时任务管理；browser：读 SAFE + 写 CONFIRM（单一所有者线程 + profile 跨进程租约）；android：读 SAFE + 写 CONFIRM） | 能力域之间互不 import | `tools/registry.py`、`tools/builtin.py`、`tools/shell/`、`tools/delegate_tools.py`、`tools/skill_tools.py`、`tools/cron_tools.py`、`tools/bilibili/`、`tools/qq/`、`tools/browser/`、`tools/android/` |
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
`Gateway`、`MemoryStore`、`SkillStore`、`JobStore`、`ToolRegistry`、`Agent`、`TurnEndReviewer`、`BrowserDriver`、`WriteBudget`、`ApprovalBroker`、`ServiceManager`、`MCPManager` 的**构造调用只允许出现在 `bootstrap.py`**（tests 除外）。通道与内核通过构造函数注入持有引用。

好处：换实现（如 P1 记忆升级向量库）只动 bootstrap 一处；全部依赖关系在构造处一眼可见；工具集以 `register_xxx_tools(registry, deps)` 模式注册进唯一注册表，多能力域无冲突合并。

### 3.3 错误传递约定
- **工具执行错误 / 审批拒绝**：以字符串回传给 LLM（让它自行换路径），不在循环里抛异常中断
- **审批默认值**：未注入审批器按 DenyApprover 处理；通用写工具声明 `requires_human=True`，smart 不可代替人工；DANGEROUS 始终拒绝，smart 不覆盖无人值守策略；只有完整的一词裁决有效
- **LLM 调用失败**：Gateway 包装成 `GatewayError` 抛给通道层
- **通道层**：只捕获 `GatewayError`，不感知 litellm 原始异常

### 3.4 共享状态与迁移契约（D05）

**两个库各管一件事**：`memory.db` 是"对话与记忆"（用户能看见的内容），`state.db` 是
"运行状态"（任务、账本、额度、迁移记录）。分开的理由是备份与恢复的粒度不同——
对话历史可以有损迁移，额度与任务领取不能。

| 契约 | 位置 | 必须满足 |
| --- | --- | --- |
| schema_version | `state.db` 的 `meta` 表 | 旧版升级按 `_MIGRATIONS` 逐级执行；**高于本程序版本直接拒绝打开**，不允许降级写入 |
| 任务定义 | `jobs` 表 | `revision` 只在定义变化时递增；调度器推进 `next_run_at` 用 `update_run_state()`，不碰 revision（否则用户表单永远冲突） |
| 至多一次 | `claim_due()` | 选任务 + 推进 `next_run_at` + 写 `job_claims` 在**同一个 `BEGIN IMMEDIATE`** 事务；多调度进程并发只有一个拿到 |
| 额度 | `budgets(scope, day, used)` | 用 `UPDATE ... WHERE used < limit` 的 rowcount 判定，不做"读-加-写"；跨进程竞争最后一份额度只有一个成功 |
| 迁移 | `migrations` 表 | 导入与"已迁移"标记同一事务（崩在中途就整份不生效）；记录源文件 sha256 与导入条数；旧文件保留不动 |
| 降级 | `degraded` | 旧文件损坏且库内没有可用数据时**停用该能力**（`jobs`/`budget`），不是当成空数据；错误必须能被界面/心跳看到 |

事务纪律：只包数据库操作，不把模型调用、人工审批、网络请求放进写事务。
只重试 `BEGIN`（此前没有任何语句执行过），**绝不重试事务体**。

### 3.5 会话作用域（D06）

"当前选中会话"是**客户端状态**，不是进程级变量。一个回合的会话 id 在请求开始时确定，
此后落库历史、工具作用域（`SessionRef`）、回合后 review 全部用它，不再回头读"现在是谁"。

| 契约 | 位置 | 必须满足 |
| --- | --- | --- |
| 请求固定会话 | `channels/web.py` 的 `/api/chat` | 会话 id 由请求带来（缺省取客户端当前会话），本回合内不写 `agent.session_id` |
| 进程内串行 | `core/session.SessionGate` | 同一会话按到达顺序执行；不同会话并行；等待队列有上限，满了返 429 |
| 跨进程互斥 | `state.db.session_leases` | 同一会话同一时刻只有一个进程在跑；拿不到就报 busy 并带上持有者；进程崩溃按 TTL 过期 |
| 绑定唯一 | `memory/store.get_or_create_binding` | `BEGIN IMMEDIATE` + 主键唯一，不允许"先查后插"建出两个会话 |
| 工具作用域 | `tools/registry.SessionRef` | 回合内工具（todo 等）写入请求会话，与进程级当前会话无关 |

明确不做：不用一把全局锁把不同会话也串起来——那只是把并发问题换成了响应变慢。
QQ 通道已有的按会话 `asyncio.Lock` 保留；QQ 侧接入跨进程租约留给 D22。

### 3.5 会话作用域（D06）

"当前选中会话"是**客户端状态**，不是进程级变量。一个回合的会话 id 在请求开始时确定，
此后落库历史、工具作用域（`SessionRef`）、回合后 review 全部用它，不再回头读"现在是谁"。

| 契约 | 位置 | 必须满足 |
| --- | --- | --- |
| 请求固定会话 | `channels/web.py` 的 `/api/chat` | 会话 id 由请求带来（缺省取客户端当前会话），本回合内不写 `agent.session_id` |
| 进程内串行 | `core/session.SessionGate` | 同一会话按到达顺序执行；不同会话并行；等待队列有上限，满了返 429 |
| 跨进程互斥 | `state.db.session_leases` | 同一会话同一时刻只有一个进程在跑；拿不到就报 busy 并带上持有者；进程崩溃按 TTL 过期 |
| 绑定唯一 | `memory/store.get_or_create_binding` | `BEGIN IMMEDIATE` + 主键唯一，不允许"先查后插"建出两个会话 |
| 工具作用域 | `tools/registry.SessionRef` | 回合内工具（todo 等）写入请求会话，与进程级当前会话无关 |

明确不做：不用一把全局锁把不同会话也串起来——那只是把并发问题换成了响应变慢。
QQ 通道已有的按会话 `asyncio.Lock` 保留；QQ 侧接入跨进程租约留给 D22。

### 3.6 浏览器所有权（D07）

Playwright 的**同步** API 绑定在创建它的线程上（greenlet）。`threading.RLock` 只保证不并发，
不改变线程归属——web 线程池里换个线程调用就会失败或行为异常。所以：

| 契约 | 实现 | 必须满足 |
| --- | --- | --- |
| 单一所有者线程 | `tools/browser/driver._OwnerThread` | 启动、每次调用、关闭全在同一条线程；`_ensure_page()` 断言当前线程就是所有者线程 |
| 有界队列 | 同上（`queue_size`，默认 8） | 浏览器卡住时不再堆任务；满了立刻 `BrowserBusy` |
| 超时≠取消 | `BrowserUnknown` | 等不到结果时说"可能仍在进行、状态未知"，不重试、不假装失败 |
| 关闭有界 | `BrowserDriver.close(wait_s)` | 超时返回"可能仍在运行"，不写"已关闭" |
| profile 互斥 | `state.db` 的 `resource_leases`（键 `browser_profile:<绝对路径>`） | 跨进程只有一个能打开同一 profile；拿不到带持有者报错；关闭即释放 |

租约 v3 起统一到 `resource_leases`（会话是 `session:<id>` 这一特例），一套 `acquire/renew/release`。
浏览器每次操作续期，进程崩溃后按 TTL 过期，不需要人工解锁。

### 3.7 技能与常驻记忆的一致性（D08）

技能（`skills/<name>/SKILL.md`）与常驻记忆（`data/MEMORY.md`）都是**文件**，而写入者有三个独立进程
（CLI、心跳、工作台）加若干线程（后台 review）。`threading.Lock` 只在单进程内有效，所以这里的
互斥必须是操作系统的建议锁。

| 契约 | 实现 | 必须满足 |
| --- | --- | --- |
| 跨进程互斥 | `system/filelock.FileLock`（Windows `msvcrt.locking` / POSIX `flock`） | 覆盖**整段读改写**，不只是最后一次 write；进程崩溃锁自动释放，不留僵尸锁；超时带持有者 |
| 原子落盘 | `system/atomic.atomic_write_text` | 同目录唯一临时名 + `fsync` + `os.replace`；失败删临时文件、原文件不动 |
| 内容指纹（CAS） | `SkillStore.read()` / `skill_view` 输出指纹 | 写操作可带 `expected_hash`（≥8 位前缀即可）；与当前不一致就**拒绝并保留原文件** |
| 隐式基线 | `SkillStore._seen` / `ResidentMemory._seen` | 本进程上次看到的内容指纹；用户用编辑器改过之后，不带 hash 的写入也会冲突 |
| 修订历史 | `memory/revisions.RevisionStore`（`data/history/<key>/<rev>-<sha8>.json`） | 每条修订含作者/来源/时间/摘要/内容/版号/附件清单；写入前先把磁盘现状纳入历史 |
| 回滚 | `SkillStore.rollback` / `ResidentMemory.rollback` | 回滚 = 取旧修订重新校验并写入，**产生新修订**，版号继续加，旧修订仍可取回 |
| 附件归属 | 修订里的 `references: {path: sha256}` | 主文修订不复制附件内容，但记录当时清单，`reference_drift()` 能报"附件是否跟着这一版" |

明确不做：不做多写者合并（冲突就报错让人/模型重新读一遍）；历史只保留最近 N 条（默认 50），
它是"能回滚"而不是"永久归档"；符号链接/junction 仍按 §3.3 的规则拒绝，写入路径同样受限。

## 4. 设计模式落位

| 模式 | 位置 | 解决什么问题 |
|---|---|---|
| Adapter（适配器） | `llm/gateway.py`、`channels/*` | 屏蔽 LiteLLM 与各平台差异，内核只见统一接口 |
| Repository（仓储） | `memory/store.py`、`memory/state.py`、`memory/revisions.py` | 持久化细节与业务解耦，P1 升级向量库不动上层；`JobStore`/`ExecutionLog`/`WriteBudget` 只是 `StateStore` 的薄门面 |
| Registry（注册表） | `tools/registry.py` | 新工具零侵入接入；toolset 分组即能力开关 |
| Strategy（策略） | `safety/gates.py` | 同一审批接口按上下文换策略：CLI 问人（ConsoleApprover）、无人值守拒绝（DenyApprover）、测试放行（AllowAllApprover） |
| Facade（门面） | `bootstrap.Runtime` | 通道只见 new/use/chat，不碰内部对象 |
| Composition Root（组装根） | `bootstrap.py` | 依赖只在一处发生，通道薄、全局可替换 |
| 依赖注入 | `Agent.__init__` 等全构造注入 | 测试可整体替换假件（见 `tests/test_agent.py` 的 ScriptedGateway） |
| 会话上下文（thread-local） | `tools/registry.SessionRef` + `core/agent.py` | 会话级工具（todo）在 CLI/Web/QQ/心跳并发下各线程取到自己的当前会话 |
| Grace call | `core/agent.py` 循环尾部 | 步数预算耗尽仍强制收敛出最终回答，防死循环不粗暴截断 |

工具消息按一批调用维护待返回 id 集合，允许连续多个 tool 结果；发起下一次模型请求前
必须一一对应完整。压缩保留整批调用与结果。Agent.chat 用 SessionRef.bind 建立回合作用域，
正常结束及异常均还原；delegate 的绑定键使用 UUID，不能用时分秒复用子任务上下文。

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

### 5.5 MCP 外部工具
`tools/mcp/client.py` 负责显式配置的 stdio 服务、会话所有者线程和子进程生命周期；
`tools/mcp/tools.py` 仅将发现、只读调用、人工调用注册到现有 Registry。MCPManager 在
bootstrap 构造，日志目录来自 `data_dir`。外部服务标注不授予 SAFE 权限，只读工具名单
来自用户配置；其它调用要求人工确认，smart 不代批，无人值守拒绝。SDK 不获得 sampling、
roots 或 elicitation 回调，故不能借服务器反向调用模型或扩大权限。
正常退出通过 `Runtime.close_mcp` / Web lifespan / atexit 收尾；超时封闭连接，不重放调用。

### 5.6 shell 能力域（结构化 SAFE）
`tools/shell/` 四层分工，**旧的 `tools/shell_tools.py` 只是转发薄层**（不得再长出逻辑）：

| 模块 | 职责 | 明确不做 |
|---|---|---|
| `spec.py` | 命令串 → 受控 argv：先拒绝 shell 元字符，再按"已知命令 + 有限参数"解析；`dir` 用 Python 实现；路径限定工作区且遮蔽密钥/个人数据 | 不解析通配符、不做变量展开、不支持管道/重定向/串联 |
| `process.py` | spawn（`shell=False`）、有界读取（256 KiB/流）、超时杀整棵进程树、SAFE 最小环境 | 不做沙箱与权限降级 |
| `danger.py` | 灾难级硬黑名单（确认也不执行） | 不参与"哪些能免确认"的判断 |
| `tools.py` | 工具契约与结果呈现；超时按"已确认回收 / 回收状态未知"分别话术 | 不判断命令是否安全 |

两条不变量：**SAFE 的执行器只接受 argv 列表**（没有 shell，就没有前缀绕过）；**未知参数一律拒绝**——
新增白名单命令要连参数形态一起写进 `_SPECS` 并补负例，不能只加一个前缀正则。
`register_shell_tools(registry, runner=..., shell_runner=..., cwd=..., root=...)` 的
两个 runner 都可注入，安全测试一律用假执行器并断言"调用次数为零"。

### 5.7 授权判定与场景作用域（D02）

**一处判定**：`safety.gates.private_target_allowed(allowed_users, target)` 是"能否给某个 QQ 号发私聊"
的唯一入口，入站、`qq_send_message`、`cron_add` 校验、cron 投递、Web 建任务全部走它。
空白名单一律视为"谁都不许"——不允许任何一处再写成"空 = 不限"。

**读时为准**：`config.load_qq_policy()` 每次从配置源重读 QQ 策略（读失败 → 全空，fail-closed）。
`bootstrap.Runtime.qq_policy()` 与注入工具的白名单提供者都接到它上面，所以撤销白名单
在下一条消息 / 下一次投递就生效，不用重启进程。`Runtime.config_path` 记录**实际读取**的那个文件，
测试用临时配置就不会热读到真机配置。

**场景作用域**：QQ 绑定键改为 `dm:<user>` / `group:<group_id>`，同一发送者在私聊与各群各自一条历史；
旧键 `qq:<user>` 只迁移给私聊，绝不进群。群聊回合 `Agent.chat(personal_context=False)`：
不注入档案/常驻笔记，并把 `core.agent.GROUP_DENIED_TOOLS` 从工具规格与派发两处同时剔除。
D12/D22 会把这里换成正式的记忆作用域策略，本节的 API 形状保持不变。

### 5.8 工作台身份（channels/web_auth.py，D03）

`WebAuth` 是工作台唯一的身份判定，`channels/web.py` 只在中间件与两处握手点调用它：

- **配对**：启动时在控制台打印 `http://127.0.0.1:8000/pair/<码>`，点开即设 HttpOnly + SameSite=Strict
  的签名会话 cookie（`sid.exp.hmac`，密钥在 `data/web_secret`）；码 15 分钟有效、
  连续 5 次错误即更换并重新打印。删除 `web_secret` 等于让所有已配对浏览器失效。
- **三级校验**：Host 必须是本地主机名（挡 DNS rebinding）→ 写方法校验 Origin 与 `Sec-Fetch-Site`（挡 CSRF）
  → 除 `/api/auth/*` 外一律要求会话。终端 WS 握手同样要 Host+Origin+会话，缺一即 `close(1008)`。
- **不启用 CORS**：CORS 不是认证；这里既不发 CORS 头，也不依赖它。
- **密钥不回显**：`/api/system/config` 对 `qq.access_token` 只回 `access_token_configured`；
  保存接口三态——缺省/空串保留旧值、`null` 清除、非空替换；含密钥字段的错误信息不回显值。
- **监听**：`_validate_bind_host` 只放行回环地址，非回环需 `OMNI_WEB_ALLOW_REMOTE=1`（默认失败关闭）。

新增接口时：路由只要在 `/api/` 下就自动受保护（中间件统一拦截），**不要**为某个接口单独放宽
`PUBLIC_PATHS`；前端不需要改，配对链接由浏览器导航完成。

### 5.9 路径与外部依赖隔离（config.RuntimePaths，D04）

`config.RuntimePaths` 是**唯一**声明可写路径的地方（data/config/cookies/skills/cache/
android_shots/android_cache/browser_profile/browser_shots/run/logs/mcp_logs）；
`bootstrap.build_runtime` 用 `RuntimePaths.create(data_root, config_path=..., skills_root=...)`
造出实例后逐个注入，模块内保留的 `DATA_DIR / ...` 只是"没注入时的默认值"，测试路径不走它。

两条不变量：

1. **`build_runtime(data_dir=...)` 即隔离**：注入的 data 根换掉全部写入位置，工作台的配置读写与
   B 站 Cookie 也走 `runtime.paths`，不再引用模块级 `CONFIG_PATH` / `BILI_COOKIES_PATH`。
   `skills_root` 仍独立注入；`config_path` 可指向别处（配置源与运行数据不必同根）。
2. **隔离实例不碰真机进程**：`data_dir` 一旦注入，`ServiceManager` 的端口探测返回空、
   终止与启动直接抛 `RuntimeError`——否则 `stop()` 的端口兜底会把用户正在跑的真服务杀掉。

依赖替换清单（都可注入）：shell runner、设备/ADB、HTTP client、模型网关、时钟、
服务探测/终止/启动、路径。新增能力时若需要外部副作用，先想清楚"测试里它从哪儿来"。

## 6. Phase 架构演进预告| Phase | 新增 | 结构原则 |
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
