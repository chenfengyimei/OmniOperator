# 开发状态记录

> 本文件是 R0–R7 的执行台账：谁在哪个 commit 关了哪个问题、证据是什么、还有哪些**真实环境**没验。
> 规则（来自 [开发大纲](DEEPSEEK_DEVELOPMENT_OUTLINE.md) §11）：
> 状态只能用 **待开始 / 开发中 / 待真实验收 / 完成 / 阻塞**；测试证据必须写命令、结果、日期和 HEAD；
> 不得把"测试跳过""仅 mock 通过""接口已存在"记成完成；历史失败不覆写成仿佛没发生。

## 1 基线（D00，2026-10-27）

### 2026-10-29 · 第二批结构重构：Web 领域路由与 HTTP 兼容合同

- 在 `934618f` 上继续按职责拆分，已发布 `channels/web.py` 从 **1766→405 行**，
  工作区（另含并行设置/密钥改动）从 1816→453 行。生命周期、统一鉴权、配对/设置和静态挂载仍归应用入口。
  `web_api/` 按 sessions/conversation/runs/memory/skills/artifacts/approvals/mcp/operations/
  automation/publications/devices 拆为 **12 组 APIRouter**，只消费同一个注入 Runtime。
- 21 个原有请求模型集中到 schemas.py，旧 web.ChatIn 等显式转发。聚合器和请求契约分别
  归类，避免分类图把装配和数据协议合成表面循环。AST 守卫新增路由反向依赖与 DTO 叶子边界负例。
- 从匹配已发布 934618f 的隔离源码提取 HTTP 合同：**90 操作、23 schema**，冻结方法/路径、
  operationId、参数、requestBody、responses 和模型定义；允许分组 tags 调整与新增接口。
  独立应用不串 Runtime/会话，技能静态路由优先级保留；旧业务行为继续跑原套件。
- 项目索引现为 **47 模块、174 源码/界面文件、96 接口、71 静态工具声明**；
  API 页支持按所属模块筛选、从详情跳到模块/依赖，新增重构记录入口。文件/机器索引由精确提交源码生成。
- 测试过程保留：首轮新鉴权断言误把读取方法也要求 Origin 拒绝，按原规则修为各方法 Host 检查、
  写方法 Origin/CSRF；没有更改权限实现。技能测试初始正文未满足既有 linter，改成合法四节正文。
  完整回归发现 390px API 表格撑开页面：首次工作区 998 passed/1 failed、隔离 951 passed/1 failed。
  修正 Grid 最小宽度和表格最小宽度，保留手机页宽与行高回归，失败未被跳过。
- 最终 `uv run --no-sync pytest -q --tb=short`：**工作区 999 passed，176.27s**；
  精确暂存源码导出 + 全新 Python 3.12.14 venv，锁文件离线装 99 包，**952 passed，169.61s，零跳过**。
  差额 47 项为并行未提交测试，未混入；本批新增 16 项领域 API/合同/隔离测试和 2 项架构负例。
  `channels/web_api` pyright **0 errors/0 warnings**，索引 --check 与文档链接检查通过。
- [重构记录与后续顺序](REFACTORING.md)、[验证摘要](evidence/web-api-refactor-2026-10-29.json)
  记录合同/索引/JUnit 指纹。src/tests/scripts 与复验目录逐文件核对（CRLF 规范化）。
  并行配对、设置、主前端和配置改动保留；只提交本批路由拆分。真实服务未重启，账号/设备/模型与 72 小时验收仍独立进行。

### 2026-10-29 · 模块归包、项目/API 索引与可视化工作台

- 基于 `f6c83d3` 核实源码、注册入口和 Web/终端/插件调用；没有按过时计划重复实现已有功能。
  工具实现归入 personal/skills/scheduling/delegation/system，个人能力按事实/档案/常驻/todo/
  摘要/时钟/refine 分文件；旧 builtin/*_tools 保留明确 re-export，bootstrap 改用正式包入口。
- 终端拆为 backend/session/pool/routes，保留 TerminalManager/TerminalSession 公共 import
  和 `/api/terminal/ws`，原鉴权与关闭语义不变。插件社区拆为 client/tools/server，
  保留旧 public import 和 `python -m omni.plugins.server`。这些是职责归位，不修改既有工具名/审批等级。
- `system/catalog` 静态解析源码和界面资源；33 职责模块、160 文件、96 HTTP/WS 声明、71 静态工具，
  关键调用链定位完整。输出 MODULES.md/API.md/project.json；脚本支持生成和 --check，CI 接入过期检查。
  索引不读取 config/data/.env，不导入待索引模块；Registry.describe 另提供真实注册状态的只读副本。
- 新增 `channels/workbench` APIRouter 和独立 `web_static/project` HTML/CSS/JS，顶栏“项目与能力”：
  模块/符号/内部依赖、API 请求字段/调用链、实时工具参数/审批等级、插件隔离/启用状态、限定文档阅读。
  元数据使用 textContent；查询不执行工具、不发现远端 MCP、不激活插件。所有 API 继承原工作台鉴权。
- 架构守卫补插件上下层边界、PluginHost/CommunityClient/SkillInbox 组装根约束；
  [PROJECT_INDEX.md](PROJECT_INDEX.md) 是导航，ARCHITECTURE 仍是依赖和职责唯一权威。
- 验证：工作区全量 **981 passed，155.94s**；精确暂存代码导出到独立目录，全新 Python 3.12.14 venv
  按锁文件离线安装 99 包，最终全量 **934 passed，141.71s，零失败/零跳过**。差额 **47 项**属于
  并行未提交测试，未混入本批；本批新增 11 项索引/API/真实 Chromium 验证和 3 项架构负例。
  导出目录 src/tests/scripts 逐文件与暂存代码核对一致（CRLF 规范化）。
- 首轮新测试出现 5 项断言失败：把既有 Host 拒绝错写成 400（实际 403），路由比对漏计 `/pair/{code}`；
  修正测试口径，没有放宽鉴权。类型检查发现终端后端可能未绑定 PtyProcess，补不可用分支后
  新 catalog/workbench/terminal/community/personal 目录检查 **0 errors / 0 warnings**；全仓旧类型基线仍单独保留。
- 真实 Chromium 验证模块搜索/详情、API 请求字段、工具规格、扩展状态、文档阅读及 390px 布局，
  没有 JS 异常或横向溢出。新增 API 未配对 401、恶意 Host 403；隔离插件查询不执行代码。
  [验证摘要](evidence/project-structure-2026-10-29.json) 保存源码树、索引/JUnit 指纹与范围。
- 并行配置、人设/密钥、配对及前端改动均保留；web.py 只提交终端/索引路由接线，index.html 只提交导航链接。
  实际工作台重启后生效。本批没有重启真实服务，也不代表 72 小时或真实模型任务质量验收完成。

### 2026-10-29 · 项目收尾批（重复定义扫描 / 类型检查基线 / 真机自检）

- **做了什么**（不依赖并行会话与外部资源的收尾三件套）：
  - **全仓重复定义 AST 扫描落地为常驻工具** `scripts/scan_duplicates.py`
    （历史教训登记项：同名函数/类在同一作用域静默覆盖，曾致 ActiveRuns
    snapshot 双定义、产物保引用静默失效）。扫描结果：**src/omni 全干净**；
    `tests/test_web_model_key.py` 有 3 处重复定义——该文件是并行前端批的
    未提交文件，按纪律不动，**留给该批修复**（条目已在交接总结中注明）。
  - **严格类型检查基线建立**（D26 渐进项启动）：`uvx pyright --pythonpath
    .venv\Scripts\python.exe src/omni`（uvx 临时环境，不碰被 omni-web 占用的
    .venv）；落地常驻工具 `scripts/run_pyright.py`（UTF-8 落盘，绕开
    PowerShell 重定向的 UTF-16 坑）。基线 84 errors → 修复后 **81 errors**，
    修的三处：`llm/budget.py` Ledger Protocol 桩补 `...`、`tools/qq/client.py`
    `_dispatch` 注解放宽为 `str | bytes`（json.loads 本就两者都收，注解写窄
    了）、`tools/shots.py` save_screenshot 补 None 守卫（AttributeError 崩溃
    → 一句可解释错误，新增回归测试）。**如实记录**：剩余 81 条全部为类型
    噪声而非运行缺陷——结构化鸭子类型（_NullRunStore/RunHandle/UsageLedger
    协议形态）、sqlite lastrowid Optional 的 `int()` 收窄、裸 Callable 类型
    不认关键字调用（services._spawn）、soak.py 同键相关性判断、动态属性
    （Agent.closing/RunContext._attached_image_ids 设计如此）。**不声称
    类型检查完成**；这 81 条是后续渐进收窄的诚实起点。
  - **真机运维自检**：`python -m omni.channels.doctor` → state.db（schema 13）
    与 memory.db 健康（42 消息/6 事实/3 档案行），napcat/omni-qq 未运行与
    adb 无设备为预期 warning；收尾前真实备份
    `data/backups/pre-wrapup-20261029`（5 项、行数与 sha256 校验通过、
    凭据不打包；MEMORY.md/history/file_history 本机尚不存在，如实跳过）。
- **新增 1 项测试**：`test_save_screenshot_without_artifacts_reports_cleanly`
  （产物库未注入时的干净报错）。
- `uv run --no-sync pytest tests/test_artifacts.py tests/test_usage.py -q` →
  **33 passed**；全量 `uv run --no-sync pytest -q` → **967 passed**
  （2026-10-29；= 966 + 本批 1，无失败与跳过）。

### 2026-10-29 · 新能力：插件社区（参考服务器 + 客户端，待所有者提供机器部署）

- **需求来源**：用户要求"做好插件系统与插件社区……社区服务器我到时候可以
  提供给你部署"。本批交付社区两端；部署待机器到位后按
  [PLUGIN_SYSTEM.md §5.1](PLUGIN_SYSTEM.md) 执行。
- **参考服务器 `plugins/server.py`**（FastAPI + 文件存储，单 owner）：
  - 存储 = `index.json` + `packages/<id>/<version>.zip` + `meta.json`（版本记录），
    无数据库，备份即复制目录；`python -m omni.plugins.server --root … --token …`；
  - API v1：health / 搜索（id/名称/描述/作者子串）/ 详情（版本列表+最新指纹）/
    下载（`X-Plugin-Sha256` 头；**存储指纹与索引不符时 503 拒发**）/ 发布
    （multipart+token，`hmac.compare_digest` 恒时比较；401 拒 token、
    400 拒坏包、**409 版本不可变**——同 id+version 重复发布拒绝，升级只能加版本号）；
  - 服务端只做**纯数据校验**（zip 内合法 manifest.json 且入口在包内）——
    深度校验仍在每台机器安装时做；索引写入带进程内锁 + 发布带跨进程文件锁。
- **客户端 `plugins/community.py`**：`CommunityClient` + 五个 agent 工具；
  本地配置存 `data/plugins/community.json`（server_url/token，None=不动、
  传值=设置、空串=清除）——**刻意不进 config.yaml**（config.py 被并行前端批
  持有，且社区接入点属于用户级偏好）：
  - `community_search` / `community_info`（SAFE，read_only）；
  - `community_install`（SAFE）：详情 → 下载（≤4 MiB）→ **sha256 与响应头
    校验**（不符即丢弃不落盘）→ inbox → 走既有隔离安装链（激活仍需人工确认，
    **社区不背书安全性**）；成功入隔离区后清理收件箱 zip；
  - `community_publish`（**CONFIRM+requires_human——"发布"是项目红线**）：
    `path=` 工作区插件目录自动打包（跳 `__pycache__`，≤4MiB/50 条目），
    或 `package=` inbox zip 原样上传；
  - bootstrap 接线（复用插件宿主的 workspace 目录解析）。
- **新增 6 项测试**（`tests/test_plugin_community.py`）：服务器全往返
  （token 门/manifest 校验/版本不可变/搜索详情下载/新版本 latest 切换/
  旧版本可取/**存储被篡改 503 拒发**/404）、客户端搜索-详情-安装-激活
  全链路（隔离区可见、激活前零注册、收件箱清理、重复安装拒绝）、
  篡改下载内容拒绝落盘、工作区目录发布→安装→激活全链路（__pycache__ 不进包）、
  未配置社区的如实报错、工具审批门（查询/安装 SAFE 无人值守可用、
  发布被 DenyApprover 拒绝）。
- **平台坑（新）**：httpx 0.28 起 `ASGITransport` 只实现
  `handle_async_request`（仅 AsyncClient 可用）——同步 `httpx.Client` 的
  测试需自建同步桥接（`SyncASGITransport`：专用事件循环线程 +
  `run_coroutine_threadsafe`）；生产客户端走真实 URL 不受影响。
- `uv run --no-sync pytest tests/test_plugin_community.py -q` → **6 passed**；
  `tests/test_arch.py + test_plugin_community.py -q` → **28 passed**（架构
  守卫确认新模块合规）；全量 `uv run --no-sync pytest -q` →
  **966 passed**（2026-10-29；= 956 + 本批 6 + 并行前端批未提交 web 测试
  同期新增 4 项，均绿）。

### 2026-10-29 · 新能力：插件系统核心（本地生命周期，agent/用户自开发功能接入）

- **需求来源**（用户原话）：做好插件系统与插件社区，让用户以及项目内的 agent
  可以自己开发功能自己扩展更多功能；社区服务器将由用户提供后部署。
  本批交付**本地核心**；社区（客户端+参考服务器）是下一批（job 15）。
- **实现（新顶层包 `plugins/`，与 channels 同级不设限，架构守卫通过）**：
  - `plugins/manifest.py`：manifest.json 纯数据校验（id/version/entry/
    permissions；v1 permissions 只允许 `["tools"]`），零执行；
  - `plugins/host.py::PluginHost`：生命周期 = inbox 收包（zip 或**工作区目录**
    ——agent 自开发闭环）→ `.quarantine/` 数据化校验（类型白名单：manifest +
    唯一顶层入口 .py + README + references 文本；zip-slip/盘符/ADS/保留名/
    重名全防；zip≤4MiB、解包≤8MiB、条目≤50、入口≤128KiB；支持"包一层顶层
    目录"的常见 zip 形态）→ **`plugin_activate`（CONFIRM+requires_human，
    激活即同意执行）**：激活前对隔离区逐文件 sha256 防篡改比对、跨进程文件
    锁、锁内复核同名碰撞 → 装载（`spec_from_file_location` 导入 entry，调用
    `register(ctx)`）→ 启停/卸载（反注册工具）→ 启动恢复（`load_installed`，
    装载失败只报 stderr 不阻断启动）；
  - **受限注册面 `PluginContext`**：工具名强制 `<id>-` 前缀（结构上不可能
    影子覆盖内置工具）、`toolset=plugin:<id>`（可归因；群聊作用域因许可名单
    机制自动禁用）、风险等级作者声明并照常走既有审批链（无人值守拒绝一切
    非 SAFE——含插件声明的）；插件私有数据目录 `ctx.data_dir`；
  - `plugins/tools.py`：六个 agent 工具（plugin_list read_only；install/enable/
    disable SAFE；**activate/remove CONFIRM+requires_human**——无人值守永远
    激活/卸载不了插件）；
  - `tools/registry.py` 新增 `unregister`（禁用/卸载反注册）；`bootstrap.py`
    接线（宿主 + workspace 目录解析 + 启动恢复）；状态存文件
    （`<id>.plugin.json` + FileLock），**刻意不进 state.db**——schema 版本
    常量在 config.py（并行前端批持有），且插件元数据天然与包文件同生共死。
- **信任模型（docs/PLUGIN_SYSTEM.md 全文，如实声明）**：校验零执行、
  **激活即同意执行**（人工确认就是"用户激活"这一步；Python 进程内无可靠
  沙箱，不假装有；不可信来源走 MCP）；内容指纹钉死激活字节，改过必须重走
  「卸载→安装→人工确认激活」链，enable 洗不了白；disable/remove 反注册工具
  但已导入模块代码无法从进程卸载（重启才彻底清除）——消息如实写明。
- **新增 20 项测试**（`tests/test_plugins.py`）：隔离安装零执行、包装目录 zip、
  9 类坏包整包拒绝（含 zip-slip/脚本附件/白名单外/入口不匹配/两个 py/未知
  能力）、manifest 字段校验、激活注册+派发、审批门（DenyApprover 拒激活与
  卸载、插件 CONFIRM 工具被拦）、启停往返、隔离区/已安装两侧防篡改、
  卸载删包删数据、导入即炸被关在插件内+正规恢复链、部分注册回滚、
  注册契约违例（无前缀/影子覆盖内置工具/未知风险）、工作区目录安装、
  重启恢复（停用不恢复）。
- **平台坑（新）**：①导入插件时解释器在包目录写 `__pycache__`，会把内容
  指纹打花——读包/指纹/防篡改一律跳过 `__pycache__`（工作区开发目录同理）；
  ②`relative import` 层级：plugins/tools.py 引 registry 是 `..tools.registry`
  不是 `.registry`（首跑 ModuleNotFoundError 抓出）。
- `uv run --no-sync pytest tests/test_plugins.py -q` → **20 passed**；
  `tests/test_arch.py + test_plugins + test_tools + test_skills -q` → **70 passed**
  （架构守卫确认新包合规）；全量 `uv run --no-sync pytest -q` →
  **956 passed**（2026-10-29；= 934 + 本批 20 + 并行前端批未提交 web 测试
  同期新增 2 项，均绿）。

### 2026-10-29 · 新能力：session_refine 手动触发记忆整理（Hermes /refine 对标）

- **需求来源**：Hermes 对标缺口之"/refine 手动整理"——用户说"整理一下这轮对话"
  时，模型此前没有可按的按钮，只能等通道的自动回合后 review。
- **实现（不新开执行路径）**：
  - `tools/builtin.py::register_refine_tool`：新工具 `session_refine`（SAFE）——
    调既有 `TurnEndReviewer.submit(sid)`，与自动 review 共用同一有界队列、
    同一消息区间去重、同一 review 模型账本（purpose=review 计费）；
    `submit` 的五种判定（queued/duplicate/full/closed/failed）逐条映射成
    如实的中立话术（排队深度满 = "宁可少整理一轮，不堆积待办"）；
  - **刻意不收 session_id 参数**：作用域取 SessionRef 的当前会话，模型不能
    指定整理别的会话；群聊作用域下与个人记忆工具一样被 GROUP 名单禁用；
  - `bootstrap.py`：仅在 `reviewer is not None`（config.review.enabled 且
    start_background_workers）时注册——review 关闭的实例不出现这颗按钮；
    注册发生在 reviewer 构造之后（registry 每轮动态取 specs，对已构造的
    Agent 依然可见）。
- **新增 7 项测试**（tests/test_review.py）：五种 submit 判定映射（含提交的
  必是当前会话）、缺会话上下文如实报错、真实链路端到端（工具提交 → 后台
  worker 真的跑完一次整理并落 review 日志）、工具旗标（SAFE/非
  read_only/无 session_id 参数/群聊名单禁用）。
- `uv run --no-sync pytest tests/test_review.py -q` → **11 passed**；
  全量 `uv run --no-sync pytest -q` → **934 passed**（2026-10-29；= 920 +
  本批 7 + 并行前端批在其未提交 web 测试里同期新增的 7 项，均绿）。
  过程记录：第一次全量出现 `test_system.py` 两项红（config apply 200≠400），
  单独复跑全绿、全量复跑全绿——两处失败点均在并行会话**正在重写**的
  `system/config_io.py` 校验区（该文件 233 行未提交改动、持续变动中），
  属其编辑中间态的瞬态，与本批无关（本批未触碰 system/web 域）。

### 2026-10-29 · D18 收口：外来技能包隔离安装

- **需求来源**：Hermes 对标缺口之"外来技能包安装"。此前刻意后置的原因是
  "无导入通道即无导入攻击面"；本批开放通道，同时把攻击面收在门口。
- **实现（既定边界：先隔离区 → 清单检查 → 用户激活，绝不执行脚本）**：
  - 新模块 `memory/skill_inbox.py::SkillInbox`（都在技能根的隐藏目录下，
    随 skills_root 自动隔离，不动 config/RuntimePaths）：
    - `.inbox/` 收 zip（用户手动放入）；`skill_import`（SAFE）数据化解包进
      `.quarantine/<name>/`——**没有任何 postinstall/脚本执行环节**，
      "安装"只是移动文本文件；支持"包一层顶层目录"与平铺两种打包格式
      （先剥公共顶层目录再校验形状，剥前先过名字安全检查）；
    - **类型白名单 = SKILL.md + references 文本**（与 SkillStore 阅读边界
      同源）：可执行/脚本/二进制在导入时就整包拒绝，而不是进来再扫；
      拒绝面：zip-slip（`..`/盘符/ADS `:`/Windows 保留名/绝对路径，
      Windows 与 POSIX 语法各查一遍）、条目重名、NUL 二进制伪装、
      非 UTF-8；上限：zip ≤4 MiB、解包总量 ≤16 MiB、条目 ≤200、
      SKILL.md ≤128 KiB、参考文件 ≤64 KiB、references ≤60 个；
    - SKILL.md 过与 skill_create **完全相同的 linter**（结构/name 规则/
      description ≤60/正文 50–24000），外来包与自建技能同一质量门槛；
    - 清单透明：导入结果与 `skill_pending`（SAFE，read_only）都列出
      逐文件大小与 sha256，用户激活前看得见包里到底有什么；
    - `skill_activate`（**CONFIRM + requires_human**——审批流里的人工确认
      就是"用户激活"这一步，无人值守 DenyApprover 永远激活不了）：
      激活前做**防篡改复核**（隔离区当前内容与导入清单逐文件 sha256 全等
      比对，改动/新增/缺失/清单损坏一律拒绝）；与技能编辑共用同一把
      跨进程文件锁（锁内复核同名碰撞）；激活把来源记入修订历史
      （`source=skill_import`，summary 带来源 zip 名）——provenance 可审计；
    - `skill_discard`（SAFE）只删隔离区，不碰正式技能库。
  - `memory/skills.py` 两个最小公共口：`root` 属性、`adopt()`（外来内容
    落位后的来源登记）；`tools/skill_tools.py` 新增
    `register_skill_inbox_tools`（4 工具）；`bootstrap.py` 在主注册表接线
    （review 白名单注册表不加——外来包激活不属于回合后整理）。
- **没做什么**：不扫描 SKILL.md/references 的**文字内容**里的"危险措辞"——
  技能正文本来就合法描述如何运行命令（Procedure 节），扫措辞只会误伤；
  安全边界靠类型白名单+不执行+人工确认三道闸。工作台收件箱面板未做
  （web.py 由并行前端批持有），当前经 agent 工具与目录操作可用。
- **新增 21 项测试**（`tests/test_skill_inbox.py`）：全流程导入→隔离→激活
  （隔离期 skills_list 不可见、来源入修订史）、平铺布局、package 参数
  路径攻击、9 类不安全/白名单外条目整包拒绝、重名条目、4 类坏 SKILL.md、
  二进制/非 UTF-8、体积与数量上限×4、激活防篡改（改动/新增/清单损坏）、
  同名碰撞（导入与激活两处）、丢弃与重复导入替换、工具层审批边界
  （DenyApprover 拒激活/AllowAllApprover 通过）、pending 双清单展示。
- `uv run --no-sync pytest tests/test_skill_inbox.py -q` → **21 passed**；
  `uv run --no-sync pytest tests/test_skills.py tests/test_arch.py
  tests/test_tools.py tests/test_skill_inbox.py tests/test_resident.py -q`
  → **80 passed**；全量 `uv run --no-sync pytest -q` → **920 passed**
  （2026-10-29；= 899 + 本批 21，无失败与跳过）。

### 2026-10-29 · 新能力：批内多工具并发执行（只读并发 + 写栅栏）

- **需求来源**：Hermes 对标缺口之"多工具并发"——模型一批发出多个只读调用
  （读三个文件 + 搜一条记忆 + 看网页）时逐个串行等待是主要时延浪费。
- **实现（顺序语义优先于速度）**：
  - `tools/registry.py`：`Tool` 新增显式 `read_only` 标记（默认 False=串行）。
    **SAFE ≠ read_only**：remember/todo/skill_create/cron_add/qq_send_message/
    delegate_task 这类"SAFE 但写状态"的工具不标注，同批"先写后读"的相对顺序
    不被并发打乱；browser 共用同一张活动页面（navigate/read/screenshot 有
    顺序语义），整个 toolset 刻意不标；android/desktop 驱动锁本就串行化、
    无并发收益，同样不标。
  - `core/agent.py`：工具循环改走 `_execute_tool_calls`——同批 `read_only`
    工具进线程池（上限 `MAX_CONCURRENT_TOOLS=4`，内核常量而非用户配置：
    它约束的是 agent 对外的瞬时并发，调大对本地单用户没有收益、只放大对
    远端 API 的突发压力，B站读取有拟人防风控考虑）；未标注工具
    （CONFIRM/requires_human/未知名/本回合禁用）是**栅栏**：先等在途并发
    结果落地再串行执行，交互式审批永远一次只弹一个；消息与事件严格按
    **原始调用顺序**回填（tool_call_id 不变量不因并发改变、事件流顺序确定）；
    worker 线程内按本回合重新绑定 SessionRef/RunRef（thread-local 不丢失，
    todo/草稿这类按会话取数的工具不会"会话上下文缺失"）；单个工具意外异常
    隔离成它自己的 fail 结果（副作用 unknown），不吞同批同伴、不打断回合；
    取消路径不变（串行调用前与段提交前 run.check、段内由 registry.execute
    的 cancel 检查兜底、下一轮模型调用前检查点拦住回合）。
  - 已标注 24 个只读工具：get_time/recall/search_history/daily_digest、
    skills_list/skill_view/skill_history、files 五读（list/search/read/diff/
    history）、web_read、bilibili 四读 + drafts、peek_command、cron_list、
    mcp_list_tools/mcp_read_tool、system_status。
  - 顺手修复：`tools/builtin.py` 里 remember/recall 的**重复注册块**（粘贴
    残留，两处逐字相同）——不删它，第二次 register 会把带 read_only 的定义
    覆盖回无标记版本，对本批是正确性问题而非单纯清理。
- **没做什么**：未对每个工具做线程安全逐一审计——进池工具按"纯读且无共享
  可变状态"挑选，共享驱动（browser/android/desktop/MCP 连接）全部留在串行侧；
  并发段的事件按调用顺序在段落地时批量产生，不展示实时完成交错（事件流
  顺序确定性优先于实时性）；并发上限暂不开放为配置。
- **新增 12 项测试**（`tests/test_parallel_tools.py`）：真并发+顺序回填、
  并发上限受约束、写栅栏（在途读先落地、写完成后读才提交）、SAFE-无标记
  工具保持串行、全写批次旧序、双层异常隔离（工具异常/execute 自身崩溃）、
  两种取消路径（并发段内取消在栅栏前拦截、批首取消后续调用不执行）、
  worker 的 SessionRef/RunRef 重绑定、未注入 SessionRef 如实报错不崩、
  群聊禁用名单与 read_only 的交互（禁用工具不进池、照常回拒绝结果）。
- `uv run --no-sync pytest tests/test_parallel_tools.py -q` → **12 passed**；
  全量 `uv run --no-sync pytest -q` → **899 passed**（2026-10-29；= 887 +
  本批 12，无失败与跳过；不含并行前端批未提交的 web 测试文件）。

### 2026-10-29 · 新能力：一次性定时触发（闹钟）

- **用户需求**：agent 本地"定闹钟"——指定时间自动触发一次（执行 prompt/给自己
  发消息）。核查现有 cron 只有周期模式（`every_minutes≥10` / `daily_at` 每日
  重复）——"10 分钟后提醒我"用 every_minutes=10 会永远重复，一次性原语缺失。
- **实现（零迁移方案）**：一次性任务 = `every_minutes` 与 `daily_at` 皆空 +
  触发时刻记在 `next_run_at`。刻意不加独立列：schema 版本常量在 config.py
  （当前由并行前端批持有该文件未提交改动，任何编辑都可能被其 git add 卷入
  提交），且既有校验下"无周期字段"只能由一次性入口产生，语义无歧义。
  - `memory/jobs.py`：validate 改为"周期字段至多一个；皆空必须有触发时刻
    （一次性）"；新增 `parse_run_at`（naive 按任务时区解释，支持
    YYYY-MM-DD HH:MM[:SS] 与带偏移 ISO）；`compute_next_run` 对一次性返回
    自身时刻——未感知一次性语义的旧调用方（工作台启停切换重算）不炸、行为不变。
  - `memory/state.py::claim_due`：一次性**领取即在同一事务停用**
    （at-most-once：执行器中途崩溃只留 unknown 账本，绝不重触发——与
    unknown 不重放纪律一致）；错过触发超过 10 分钟宽限（如停机期间）**不补发**，
    停用并留痕"错过触发时间…未执行已停用"（迟到太久的闹钟没有意义）；
    运行中到期不合并（一次性没有"下一次"）。
  - `tools/cron_tools.py`：新工具 `cron_once`（SAFE）——`run_at`（绝对时间，
    timezone 参数解释 naive）或 `delay_minutes`（1–10080）二选一 + prompt +
    可选 `deliver_to_qq`（走既有白名单/outbox 投递链路，掉线入队补投）；
    创建时校验未来性（≥30 秒后）。`cron_edit` 支持 `run_at` 改触发时刻
    （周期任务拒绝该参数）；一次性任务启停切换不参与周期重算；
    `cron_list` 展示"一次性 <时刻>"。
- **边界（如实记录）**：工作台表单未接线（web.py/index.html 由并行前端批持有）——
  一次性任务在 cron 列表显示"每天 None"、创建表单仍只有周期字段；启停切换与
  prompt 编辑可用（setattr 语义保留 next_run_at，重算返回原时刻）。
  agent 工具路径完整可用；触发仍需 omni-heartbeat 进程在运行（既有约束）。
- **新增 6 项测试**：validate/parse/compute 语义、领取即停用 at-most-once、
  错过宽限不补发、宽限内正常触发、工具契约（相对/绝对/过去拒绝/二选一/
  白名单/列表展示）、编辑路径与模式守卫。
- `uv run --no-sync pytest tests/test_cron.py tests/test_state.py tests/test_web.py -q`
  → **91 passed**；全量 `uv run --no-sync pytest -q` → **887 passed**（2026-10-29；
  = 877 + 本批 6 项 + 并行会话 web 测试新增 4 项，均绿、文件无交集）。

### 2026-10-29 · D11 收口：压缩阈值统一为 token 口径

- **关闭 D11 剩余限制 4**（原话"没有做基于 token 的精确预算，trigger_chars 仍是字符
  阈值（与 D10 的 estimate_tokens 未统一）"）：`compress.py` 新增 `_over_threshold`
  ——阈值比较改用 `llm.usage.estimate_tokens`（**与网关预留额度同一把尺**），
  `trigger_chars` 配置面不变（兼容既有配置），内部按 2 字符/token 折算为
  `trigger_chars // 2` 的 token 预算。`assemble_context`（持久摘要路径）与
  `compress_if_needed`（回合内临时压缩）两个决策点都切换；`context_chars`
  保留为诊断/测试的字符视图。
- **行为变化（有意，测试钉住）**：工具调用序列化、多模态文本块与每条消息的包装
  开销现在计入体量——短消息堆积（如 260 条 m0/m1…）在旧字符口径下"很小"不触发，
  token 口径下超预算触发压缩。既有量级（长内容远超/远低于阈值）行为不变，
  全部既有压缩测试零改动通过。
- **没做什么**：回合内临时压缩仍不落库（D11 既定设计——工具消息本不持久化，
  编造 id 会把覆盖区间变成假账）；摘要质量仍依赖摘要模型（需真实模型验收）。
- `uv run --no-sync pytest tests/test_compress.py tests/test_context.py tests/test_agent.py -q`
  → **40 passed**；全量 `uv run --no-sync pytest -q` → **877 passed**（2026-10-29，
  = 861 + 本两批 2 项 + 并行会话未提交 web 测试新增约 14 项，均绿、文件无交集）。

### 2026-10-29 · D15 收口：心跳挂过期产物周期清理

- **关闭 D15 剩余限制 3**（原话"清理只在回合结束触发（CLI/心跳不产生图像时，
  过期产物会在下次 Web 回合或重启时清）"）：新增 `core/cron.artifacts_sweep_loop`
  挂到心跳进程（每小时一轮）——`ArtifactStore.cleanup(keep_ids=活跃 run 的
  _attached_image_ids)`，与 `Agent._sweep_artifacts` 同一 keep 语义；单轮异常
  打印后下轮再试，不杀心跳循环。心跳/QQ 长期运行、不产生图像的部署不再积压
  过期产物。
- **新增 1 项测试**：过期未引用被清、活跃引用保留（解除引用后下一轮清走）、
  cleanup 首轮抛异常循环继续（Boom 计数 ≥3）。
- `uv run --no-sync pytest tests/test_cron.py -q` → **20 passed**；全量见提交信息。

### 2026-10-29 · D09 收口第三批：files 写路径结构化（收口完成）

- **关闭第 5 批登记的"第三批待做"**：`files_write`/`files_restore` 迁 `ToolResult`——
  写成功=applied（**回读指纹进 verification 字段**）；哈希冲突/前置校验失败/备份失败/
  写入失败=确定未写 none（写入失败依据 `system/atomic` 契约：失败删临时文件且不动
  原文件）；**回读校验不通过/回读失败/写后路径变更=side unknown**——磁盘可能已被
  写入或替换，这是本轮修掉的最后一个副作用误分类（旧推断曾记成 none，等于告诉
  模型"什么都没发生"）。回滚同理：整体失败分支的文案本身就在对冲"如已改动"，
  按未知处理；成功=applied 带指纹验证。
- **至本批，副作用敏感域（shell/QQ/系统/browser/MCP/files 写 + 此前已是结构化的
  android 写/desktop/bilibili 写）全部迁移完成**。剩余仍走 `from_legacy` 的是纯读与
  额度闸门内的内部记忆写（builtin/cron/skill/delegate/web/android 读/bilibili 读/
  files 读）——推断对这些域是**正确的**（读=ok/none、错=error/none，无未知歧义），
  `from_legacy` 保留为它们的文档化适配层；LOCAL_AGENT_PLAN §0.2 收口项 1 的
  "副作用不可信"风险面就此关闭。
- **新增 2 项测试**：写成功/哈希冲突/回读不一致三态断言（含磁盘内容字节级验证）；
  回滚成功/指纹冲突两态断言。既有 dispatch 字符串断言零改动（text 逐字不变）。
- `uv run --no-sync pytest tests/test_files.py -q` → **35 passed**；
  全量 `uv run --no-sync pytest -q` → **861 passed**（2026-10-29；含并行会话未提交的
  16 项 web 模型密钥测试，与本批文件无交集）。

### 2026-10-29 · 取消标记竞态补丁：cancel() 先翻转信号再落库

- 第 1 批修复（`phase()` 取消后 no-op + 写后复查自愈）留下了最后一个交错窗口：
  `ActiveRuns.cancel()` 原实现**先落库"cancelling"再翻转 token**——worker 的
  `phase()` 若恰在"库已写、token 未翻"的窗口里完成两次 token 检查，"model"
  会覆写"cancelling"且自愈不触发。第 5 批全量（858 passed / 1 failed）再次命中
  该用例（第 1 批修复前也发生过一次，均为高负载下偶发）。
- 修复：`cancel()` 改为**先翻转 token、再写库**（顺序是正确性要求）。按构造
  闭合全部交错：worker 在翻转前写 phase → cancel 的落库覆盖为"cancelling"；
  翻转后 → `phase()` 跳过不写；夹在中间 → `phase()` 写后复查自愈。
  返回值语义不变（首次翻转返回 True）。
- `uv run --no-sync pytest tests/test_runs.py -q` → **19 passed**；取消用例连跑
  3 次通过；全量与第 5 批同树验证（见下条提交）。

### 2026-10-29 · D09 收口第二批：browser/MCP 工具结构化

- **浏览器域（`tools/browser/tools.py`）**：五个工具迁 `ToolResult`——写成功=applied
  （**写后回读片段进 verification 字段**，与 ARCHITECTURE §3.6 的"写后回读"契约对齐）；
  `BrowserUnknown`（所有者线程超时）=unknown（动作可能仍在执行）；**动作内
  `BrowserError`=unknown**——"定位失败"与"点击后 load-state 等待失败"在异常层分不开，
  一律按副作用未知处理，不盲目重试（宁可保守，不假装知道）；`BrowserBusy`
  （入队前排队被拒）=可重试且确定未执行；预算耗尽=确定未执行。读操作错误=none。
- **MCP 域（`tools/mcp/`）**：`MCPError` 增加 `uncertain` 标记——在途超时（FutureTimeout）
  与"服务中途死亡时**正在执行**的调用"置 True；还在队列里、从未派发的排队项置 False。
  `MCPManager.call_outcome` 返回 (渲染文本, 远端是否 isError)，`call()` 保持字符串兼容。
  工具映射：uncertain=unknown（调用可能已到达服务器）/ 入队前拒绝（白名单、未授权、
  参数错、封闭连接）=确定未发出 / **远端 isError=已执行但报告失败**（是否部分生效
  客户端不可知，按 unknown 保守处理）→ 错误码 mcp_refused/mcp_uncertain/mcp_remote_error。
- **顺带修正的误分类**：远端 isError 的文本以"错误：外部工具执行失败"开头且无
  "超时/未知"字样——旧 `from_legacy` 推断成 side=none，等于告诉模型"没发生任何事，
  可以直接重试"；实际调用已在外部执行。
- **新增 3 项测试**（browser 1 项覆盖五类路径 / mcp 2 项：远端成功-applied、
  isError-unknown、入队前拒绝-none、在途超时-uncertain、封闭后调用-none、
  服务中途 os._exit-uncertain）。既有 dispatch 字符串断言零改动。
- **剩余（第三批待做）**：files_write/restore 的回读校验失败仍是字符串
  （"写入后回读校验不通过"被推断成 side=none——文件**可能已被写入**，是同类误分类）；
  builtin/cron/skill/delegate/web/android 读等低风险域仍走 `from_legacy`。
- `uv run --no-sync pytest tests/test_mcp.py tests/test_browser.py tests/test_bilibili_publishing.py -q`
  → **53 passed**；全量 `uv run --no-sync pytest -q` → **859 passed**（2026-10-29，含上条取消
  窗口补丁与并行会话未提交的 16 项 web 模型密钥测试；同树未打补丁时该用例
  858 passed / 1 failed 复现过一次竞态）。

### 2026-10-29 · D13 收口：文件工具支持带 BOM 的 UTF-16

- **关闭 D13 剩余限制 3**（原话"UTF-16 文本会被判为二进制（NUL 字节），需要时另加编码探测"）：
  记事本「Unicode」与 PowerShell 5.1 `Out-File` 的默认存档都是带 BOM 的 UTF-16，
  此前一律按二进制拒绝。现在 `tools/files/io.py` 编码识别链加入
  UTF-16 LE/BE——**只认显式 BOM**，且 LE/BE 分别成编码名（写回保持原字节序，
  不静默换端序）；BOM 由 io 层显式剥离/写回（`utf-16-le/be` 编码名不会自动吞 BOM）；
  `is_binary` 对带 BOM 的 UTF-16 放行（NUL 高频是编码本身），无 BOM 的 UTF-16
  **不猜**——与真二进制区分不开，维持 fail-closed。
- **换行风格**：`newline_of_bytes` 增加按 UTF-16 字节形态计数 CRLF/LF
  （LE=0D 00 0A 00，BE=00 0D 00 0A），UTF-16 文件的 CRLF 写回不再悄悄变 LF。
- **全链路自动生效**：files_read/files_write/files_diff/files_search 与历史快照共用
  `decode_bytes`/`encode_text`/`is_binary` 单点——写入按原编码/BOM/换行回写，
  回滚直接写原始字节快照；大文件流式读取路径（`read_window`）同样跳 BOM。
- **新增 4 项测试**：LE/BE 参数化读写往返（字节级断言原样回写）/ 无 BOM 含 NUL
  维持二进制拒绝 + 纯 CJK 无 NUL 走 GB18030 误读的**既有边界如实钉住** /
  search-diff-write 全链路不误判二进制。既有 29 项（UTF-8/GBK/BOM/二进制）零改动。
- **明确边界**：纯 CJK 内容的无 BOM UTF-16 恰好不含 NUL 字节（如「中文」= 2D 4E 87 65），
  仍按 GB18030 误读——这是改动前就存在的既有降级行为，不在本批宣称修复；
  无 BOM 场景不做 UTF-16 猜测正是为了避免把 GB18030 文本误判成 UTF-16。
- `uv run --no-sync pytest tests/test_files.py -q` → **33 passed**；
  全量 `uv run --no-sync pytest -q` → **855 passed**（2026-10-29；含并行会话未提交的
  16 项 web 模型密钥测试，与本批文件无交集）。

### 2026-10-29 · D19 第三批：失败服务有界自动重连

- **关闭 D19 剩余限制 3**（HANDOFF §6 原话"无自动退避重连——失败即 failed，等人点按钮，
  避免反复拉起坏服务"）：`MCPManager` 增加自动重连——**只在显式调用路径**
  （`list_tools` / `call`）触发；`status()` 保持纯派生（GET 状态零副作用不变）。
  节奏：失败后 5s 起指数退避（×2 封顶 80s）、**最多 5 次**，用尽即停手保持 failed 等
  人工；**成功一次就清零**；面板手动 `reset` 同样清零（人看过面板，重新给满预算）；
  `closed`（入口收尾）永不重连；**触发失败的那次调用错误照常上抛、不重放**——
  重连只惠及之后的显式调用，未知写不会被自动重试。
- **面板数据**：`status()` 行新增 `auto_reconnect_attempts` / `auto_reconnect_max` /
  `auto_reconnect_next_in_s`（未失败的服务不带这些字段）。前端展示待并行前端批接线
  （web.py/index.html 当前由另一会话持有，本批不动）。
- **新增 3 项测试**（真实 stdio 子进程 + marker 启动开关）：坏服务修好后到点自动重连
  并清零（含连接对象更换断言）/ 预算用尽停手 + 人工 reset 重新武装 / status 纯派生 +
  退避窗口内显式调用也不重建（默认 5s 间隔真实生效）。既有 20 项（含"失败不自动
  重试""无 retry 风暴"）零改动全绿。
- **没测什么**：真实生产 MCP 服务的网络断连恢复节奏（本地 stdio 子进程的启动失败
  与修好在语义上覆盖同一状态机）；`httpx`/远程传输（HTTP/OAuth 按既定边界后置）。
- `uv run --no-sync pytest tests/test_mcp.py -q` → **23 passed**；全量见提交信息。

### 2026-10-29 · D17 收口：outbox 终态滚动清理

- **关闭 D17 剩余限制 2**（原话"表本身不删行，需要时手工清理或等 D26 运维批"，D26
  已过未做）：`OutboxStore.prune_terminal(keep=200)`——按 `updated_at DESC, id DESC`
  保留最近 `PRUNE_KEEP` 条终态行（sent/discarded/dead），更旧的删除；
  **活跃行（pending/sending/unknown）绝不删**——它们是待补投/待人工处理的欠账。
- **接线**：`core/cron.py` 补投循环每轮**先**清理再补投，且与 QQ 连接状态无关
  （掉线期间表也不能无限增长）；清理失败只打印不杀循环（下轮再试），
  清理了行数>0 时打印一条日志。被清掉的行再人工重排安全返回 False（行已不存在）。
- **新增 3 项测试**：保留边界与活跃保护（keep=2 清最旧 1 条终态、pending/unknown
  不动）/ keep=0 全清且 retry 对已删行返回 False / 掉线状态下 drain 照样按保留
  条数清理（monkeypatch PRUNE_KEEP=1 验证）。
- **没测什么**：真实心跳进程长时间运行的清理节奏（逻辑是幂等 DELETE，无状态）；
  保留条数未做配置化（`PRUNE_KEEP` 类常量与 `MAX_LIVE`/`MAX_ATTEMPTS` 同风格，
  需要时再提配置）。
- `uv run --no-sync pytest tests/test_outbox.py -q` → **23 passed**；
  全量 `uv run --no-sync pytest -q` → **848 passed**（2026-10-29；含并行会话未提交
  的 16 项 web 模型密钥测试，与本批文件无交集）。

### 2026-10-29 · D09 收口第一批（副作用敏感工具结构化）+ 取消标记竞态修复

- **取消标记竞态（`core/run.py`，既有缺陷被本批并发跑测暴露）**：`ActiveRuns.cancel()`
  先写 `phase="cancelling"` 再翻转 token；若取消恰落在 `register → 首次
  phase("model")` 的组装窗口、或 phase 写入的瞬间，后续阶段写入会把"正在取消"
  覆写回"model"——并发负载下 `test_cancel_is_cooperative_and_does_not_fake_stopped`
  偶发红（单跑 5/5 通过、120 项同进程并发时复现一次）。修复：
  `RunContext.phase()` 在取消已请求后为 no-op；写入后复查 token，取消在写入期间
  落地则补写 `cancelling` 自愈。新增确定性回归 `test_phase_never_overwrites_cancelling_marker`
  （两种交错：取消先到零写入 / 写入期间取消落地补写自愈），不再依赖偶发时序。
- **副作用敏感工具迁移结构化 ToolResult（LOCAL_AGENT_PLAN §0.2 收口项 1 第一批）**：
  - `tools/shell/`：peek/run 不再靠 `from_legacy` 中文前缀推断——SAFE 观察完成即
    ok（**退出码是数据**，`git diff --exit-code` 有差异时退出码为 1，不是工具失败）；
    超时按"已确认回收（retryable，观察无副作用）/回收未知（unknown，可能有残留进程）"
    精确区分；run_command 完成=applied、超时=unknown 不可盲目重试；拒绝带稳定
    error_code（bad_arguments/safe_rejected/hard_blocked/command_timeout/dir_failed）。
  - `tools/qq/`：客户端拆出 `send_private_outcome`（返回回执 message_id 列表、
    失败抛类型化异常），`send_*_sync` 兼容入口文本逐字不变；工具按回执分三类——
    `已发送`=applied（data 带 message_ids）、`DeliveryUnknown`=unknown
    （**旧推断曾把"发送未确认"误判成 ok**——该文本不以"错误"开头，是本批发现的
    真实误分类）、`DeliveryNotStarted`=可重试错误（文案由"不要自动重试"修正为
    "未发出，可稍后重试或换方式"，与 D17 outbox 的 ConnectionError 分类语义对齐）、
    未连接=qq_not_connected。
  - `tools/system_tools.py`：启停成功=applied；`ServiceError` 新增 `uncertain`
    标记（进程已启动但身份不可核验 / 停止未确认 / 停止后仍存活或无法确认退出
    三处置 True）→ side_effect_state=unknown，其余服务错误（未安装/参数错等）
    =确定未生效 none。
  - `tools/registry.py`：`Tool.func` 类型标注改为 `str | ToolResult`（真实契约）。
  - 精确状态经 `result.brief()` 直接进入运行事件流，工作台运行进度/事件续读
    不再依赖文本猜测。
- **新增 12 项测试**：shell 结构化 8 项、qq 2 项（含兼容字符串钉死）、system 1 项、
  取消竞态回归 1 项；既有 dispatch() 字符串断言零改动（text 除 DeliveryNotStarted
  一处语义修正外逐字不变）。
- **剩余（第二批待做）**：builtin/cron/skill/delegate/files/browser/web/mcp/
  bilibili 读/android 读仍走 `from_legacy` 适配；`from_legacy` 本身保留给旧调用方，
  不在本批删除。
- `uv run --no-sync pytest -q` → **845 passed**（2026-10-29；= 817 基线 + 本批 12 项
  + 并行会话未提交的 `tests/test_web_model_key.py` 16 项。并行 WIP 改 web.py/config.py/
  config_io.py/index.html，与本批文件无交集）。

### 2026-10-28 · D27 副本恢复、常驻采样与验收闸门

- 在 D26 `56ce482` 基础上新增只读采样与发布材料检查，默认覆盖 QQ/心跳服务树；
  绑定源码、子进程确认的启动身份和 data 路径，哈希链发现证据改动；30 分钟预热后冻结阈值。
  72 小时不足、漏采/时钟跳变、身份失效、观察过的旧进程残留、资源超限、未知费用/投递、
  账本计数回退、没有实际任务增量均拒绝放行。检查不会启动任务、调用模型或发布。
- 真实数据副本恢复与两次打开通过。**实际源库已为 v13**（与早先只读 v9 证据不是同一时点），
  16 会话/42 消息/6 事实/3 档案逐行保持，两库完整性正常；未切换真实服务。
  v9→v13 的非空任务/执行/预算保留另外使用隔离 SQLite 用例验证。
- 第一轮定向测试为 61 通过/1 失败：脚本被同名外部 scripts 包遮蔽；改为按绝对路径载入。
  代码复查修正新插入函数造成的进程树返回值错位，增加原生只读检查，并复跑脚本自建父子进程
  的真实回收，两者退出确认。未将真实常驻服务用于终止测试。
- `uv run --no-sync pytest -q --tb=short --junitxml=.pytest_cache/d27-full.xml` → **817 passed，176.09s**。
  含新增 28 项验收/证据/源版本回归；模型与出网闸门仍启用。
- 2026-10-29 补验：精确暂存源码导出到独立目录、全新 Python 3.12.14 venv，按锁文件离线安装
  99 包；完整测试 **817 passed，148.60s，零跳过**，24 场景预热后 3 轮全部通过。
  [独立环境证据](evidence/clean-windows-d27.json) 记录源码树、JUnit/评测 SHA256 与环境。
  该源码不含并行前端修改；没有调用真实模型，也没有模拟长测后宣称 72 小时成功。
- 操作手册、15 项验收清单、阈值模板见 [ACCEPTANCE.md](ACCEPTANCE.md)。短时真实只读采样
  因时长、服务身份、预热、实际任务缺失而明确未通过。**72 小时与真实账号/设备/模型闭环未完成，
  没有发布稳定版**。并行 index.html 与 prototype-html 保留、不纳入本批提交。

### 2026-10-28 · D26 架构、离线回归与可观察性

- 扩展 AST 守卫：别名/模块属性/getattr 构造、常量动态导入、不可解析动态执行、工具域与平台层规则、
  重复定义；20 种绕过负例。新增 AndroidDriver/EventLog 组装根约束。
- pytest 默认阻断真实模型/embedding，Python 子进程继承回环网络闸门；捕获异常也不能掩盖违规。
  闸门发现 Web 测试的异步 review 会尝试真实网关，修正后台 worker 关闭契约与测试隔离。
- 冻结 24 个核心场景，预热后同配置三轮 24/24。仅为 scripted fixtures 契约验证，真实任务率/费用为 null。
- 关联事件 JSONL 与 QQ/心跳 Python 日志有大小/保留期/脱敏；doctor 聚合有界状态窗口与日志丢失。
  NapCat 外部日志仍由其自身管理，托管 Jenkins 未接通，严格全仓类型检查未启用。
- `uv run --no-sync pytest -q` → **789 passed**；独立源码导出+全新 Python 3.12.14 venv，
  `uv sync --frozen --offline` 安装 99 个锁定包，Windows CI 脚本复跑同样 **789 passed**。
- 第一次独立导出出现 1 个失败：真实 git 观察测试依赖源码目录的 .git；改为自行创建临时 Git 仓库后
  主环境与第二个独立环境都全绿。没有隐藏该失败或放宽 shell 白名单。
- 证据/入口：[VALIDATION.md](VALIDATION.md)、`docs/evidence/core-offline-v1.json`、
  `docs/evidence/clean-windows-v1.json`、`scripts/ci.ps1` 与 `Jenkinsfile`。

### 2026-10-28 · D16 全动作绑定补齐（待真实设备验收）

- 点击、滑动、按键、输入均使用会话/设备/完整 XML/方向尺寸绑定的随机单次令牌，120 秒过期，
  内存最多 32 条；磁盘只恢复展示，重启旧观察失效。设备文件锁覆盖读写，全动作写后复检。
- 坐标使用 screenshot/工作台锁定帧的像素指纹；修正 object-fit 黑边坐标，实时慢帧不能直接点击。
- 输入只允许唯一非密码焦点，Unicode 切换输入法后再次核验；切换失败也恢复，
  原本启用的 ADBKeyboard 不错误禁用，原输入法字符串先校验避免设备端命令注入。
- 全动作结构化回报 unknown（ADB 回码/树变化不等于业务成功），禁重复旧动作。
- `uv run --no-sync pytest -q` → **763 passed**，工作台 JS 语法通过。新增 22 项负例与闭环；
  覆盖设备/方向/焦点/像素变化、TTL/会话/取消、跨实例互斥、密码遮蔽、输入法异常和配对点屏重放。
- 未验：真机 ADB/多设备/旋转/MJPEG/输入法适配；ADB 两次调用之间仍可能被外部用户或应用改变界面。
  原 D16 历史“仅元素点击绑定”的剩余代码限制已由本批关闭。操作说明见 [ANDROID.md](ANDROID.md)。

### 2026-10-28 · D23 草稿与 B站 UI 写（待真实账号验收）

- bootstrap 注入 PublicationStore / BrowserForms / BilibiliPublisher，能力域无交叉 import。
- state v13 草稿、内容 hash、跨进程领取、同账号/目标/内容重复保护；全文人工审批，
  smart 与无人值守拒绝；页面变化/验证码/登录失效即停，超时晚到动作撤销。
- 填写与发送分别扣浏览器额度，回读新增稳定记录才报成功；unknown 人工核查，绝不自动重发。
- 工作台可编辑、丢弃和登记核查结果；账号凭据不进入草稿预览。修复桌面页签未参与切换的问题。
- 验证：`uv run --no-sync pytest -q` → 741 passed（Windows；同一既有 Pydantic 警告）。
  Chromium 本地回填评论/私信页面，核对最终点击前账号漂移、迟到任务、重复/冲突与回读丢失；
  真实浏览器验证桌面/草稿页签。工作台 JS 语法检查通过。
- 未验：真实 B站账号、路由/选择器、发送后平台审核；默认关闭，无任何真实发布。
  使用与限制见 [BILIBILI_WRITING.md](BILIBILI_WRITING.md)。

### 2026-10-28 · D25 运维与恢复（账号服务升级待真实验收）

- 接续 `81893be`：服务记录绑定 PID/FILETIME/exe/启动 nonce；启动操作跨进程锁，
  协作停止后才回退经核验的 Windows 保留句柄终止；旧 PID/端口不授权终止。
- doctor CLI/工作台只读、不组装运行态；显式 Runtime.close 统一取消审批/回合，
  并行有界等待 review/检索/MCP/浏览器；失败保留数据库与身份记录，终端追踪残留。
  关闭中的浏览器异常不再被吞为成功，ConPTY 阻塞读不再阻塞写。
- 备份覆盖注入记忆库/状态库、配置、常驻、技能/修订/文件历史/产物；默认配置凭据
  脱敏。拒绝覆盖、自覆盖、穿越/ADS/junction、重复条目、未来 schema；全体校验后
  恢复到新目录，进度标记与恢复清单区分未完成。工作台配对后可显式创建备份。
- `uv run --no-sync pytest -q` → **718 passed, 1 已知 warning，117.04s**；JS 语法检查通过。
  期间备份端点测试发现独立 db_path 漏备份，修正 RuntimePaths.memory 后 29 项定向
  及全量通过。早期全量 716 通过；新增端点/关闭测试后的最终基线是 718。
- 真机手动隔离脚本仅创建短寿命 Python 父子进程，两个都确认退出；证据
  `docs/evidence/process-tree-2026-10-28.json`。只读 doctor 证据确认 state v9 未迁移，
  旧 NapCat/QQ 身份未核验；没有停止真实服务、没有调用模型或发送消息。
- 在线备份为各库独立一致，并非跨库全局事务；严格业务时点应停写后备份。
  真实登录 profile/服务升级、多次启停、72 小时与用户场景仍留 D27。

### 2026-10-28 · D22 QQ 场景与受控主动性（待真实账号验收）

- 接续 `08c1566`；state v12 只登记事件身份/状态，四进程同事件仅一份领取；
  场景内短消息合并、有界队列与并行数、撤回墓碑、运行中授权撤销、续约及出站前复检。
- 群聊默认只许时间、公开网页和本会话 todo，新工具自动拒绝；关闭群聊个人 review；
  精确 CQ @、同群机器人回执引用；输出 CQ 转义，不能通过模型文本触发平台特殊动作。
- 主动提醒默认关闭，显式所有者/每日额度/时区/安静时段，工作台配置与一键停止；
  安静/额度等待不消耗网络重试，部分发送后撤销转 unknown；人工重排去重与容量检查。
- `uv run --no-sync pytest -q` → **692 passed, 1 已知 warning，125.94s**。
  定向首轮 100 通过、1 失败为撤销原因话术旧断言，调整原因后完整通过，保留此记录。
- 真机数据未迁移、没有真实 QQ 收发或群消息；必须扫码并小范围验证重连/撤回。
  已完成的工具动作和已发分段无法由撤回通知撤销，事件去重窗口为 30 天。

### 2026-10-28 · D20/D21 记事本桌面闭环（其余应用待真实验收）

- 接续 `bb05219`：原生 UIAutomation 后端与四个桌面工具，构造只在 bootstrap；
  应用绝对路径白名单、进程创建身份、会话绑定 observation、TTL/树指纹、窗口租约、
  独立写预算、逐次人工确认及精确回读；unknown 不自动重放，群聊禁止桌面能力。
- 工作台增加只读桌面页；截图接入既有产物/隐私链路。密码框、截断、最小化、越权、
  预览不一致、过期/跨会话、取消与竞争均覆盖负例。无 SendInput 或通用前台接管。
- 手动脚本只用临时内容：首次 System32 启动器被 Store 重定向而白名单拒绝，保留失败；
  核对实际路径后，Store 记事本 11.2607.14.0 / 96 DPI 的中文读取、ValuePattern 编辑、
  精确回读、重复观察拒绝、28303 字节 PNG、当前临时标签关闭通过。
  证据见 `docs/evidence/notepad-2026-10-28*.json`；未发送任何真实社交消息。
- `uv run --no-sync pytest -q` → **676 passed, 1 已知 warning，117.78s**。
- 第二个应用、多屏/混合 DPI、真实锁屏/管理员窗口及用户干预边界待真实验收。
  不把一个应用成功扩大为全部桌面支持，也不将 D20/D21 全部验收标完成。

### 2026-10-28 · D24 双 embedding 与混合检索（待真实模型验收）

- 接续 `0c59902`：本地 Ollama / LiteLLM API 两种出口、同一费用账本预留结算、云端显式
  开关与预算；sqlite-vec 增量索引、RRF 融合、模型版本/维度隔离、scope 过滤与同步遗忘。
- 数据路径注入原 memory.db；网络请求不占写事务；跨进程索引租约、有界批次、模型失败
  回退 FTS；默认不开启。工具、bootstrap、工作台状态、关闭生命周期均接线。
- `uv run --no-sync pytest -q` → **662 passed, 1 已知 warning，120.83s**。
  新用例覆盖真实回环 HTTP 协议、LiteLLM 参数/索引顺序、费用拒绝、扩展加载关闭、
  缺失扩展回退、非法向量、跨 scope、查询中遗忘、索引途中遗忘与版本切换续建。
- 冻结 20 个合成检索样本，三次重复，FTS Recall@5/MRR=0.7778，证据在
  `docs/evidence/retrieval-fts-v1.json`；评测命令与两种配置在 `docs/RETRIEVAL.md`。
- `Get-Command ollama` 未找到命令；未下载模型、未调用付费 API。真实模型混合检索收益
  尚待供应商/模型配置验证；接口与离线契约完成不等于质量评测或 Hermes 对照评测完成。

### 2026-10-28 · D17 调度与任务链收口

- 接续 `a52f0ec`，state v11：任务时区/源结果有效期、独立 cron_outputs（run_id、状态、结束时间、结果）。
- `cron.max_running` 默认 2，在共享数据库内计数；四个独立进程抢四个任务只领取两个。
  运行中再次到期合并，不覆盖旧 token、不在内存排队；其余积压留在库内，过期按既有错过窗口策略跳过。
  在途 claim 周期续约，收尾释放，关闭有界等待且报告残留。cron 回合接入会话租约。
- 依赖环在同一事务拒绝；context_from 必须指向最近一次成功 run，默认有效 1440 分钟。
  源失败/运行中/未执行/过期均停止下游，不再读取最后一条聊天文本；输入标出 run_id 与时间。
- IANA 时区贯通 Job/工具/API/工作台表单；旧任务 `local` 保持原行为。DST 不存在的时刻跳过，
  重复时刻只执行第一次。Windows 新增 tzdata（未重建或停止用户常驻服务）。
- 验证：`uv run --no-sync pytest -q` → **648 passed, 1 已知 warning，115.06s**。
  首次定向测试暴露 Windows 缺 tzdata，已声明依赖并安装后通过；没有用跳过测试掩盖。
- 剩余真实验收：NapCat 回执与掉线补投、长时间多进程常驻、实际时区变更；均未冒充已完成。

### 2026-10-28 续接核验：D17/D19 运行边界补齐

- 基线 HEAD `453485b`：635 passed；本批 `uv run --no-sync pytest -q`：**644 passed,
  1 已知第三方 warning，113.70s**。常驻服务环境按 HANDOFF 使用 no-sync，测试不再依赖 ADB PATH。
- D19：每次实际协议调用前核对目录和连接配置指纹；漂移/授权库缺失时撤销 SAFE。
  工作台必须提交当前显示的指纹确认，旧页面不能批准新的目录；逐次人工调用不授予全局权限。
- D17：state v10 增加 outbox 条件领取和发送身份；在线发送也先登记。四个独立进程竞争
  一条消息仅一个成功领取；旧发送者不能覆盖新状态；崩溃在途项转 unknown，禁止自动重放。
- OneBot 改为 echo 匹配回执，成功必须 status=ok/retcode=0；回执接收与消息处理分离，
  防止聊天等待发送回执造成死锁。回执超时/部分分段成功/写 socket 异常都按 unknown 处理。
- 修复旧迁移测试以新版库改版本号伪装 v2 的问题，改为从 v1/v2 脚本构造真实旧库。
  首轮全量有 1 个测试替身缺 outbox 字段（643 通过），补齐依赖后全绿；未隐去首次失败。
- 真机库未迁移，未发送真实 QQ 消息；NapCat 扫码闭环仍待用户参与。
  D17 的并行上限、成功 run 依赖、新鲜度和时区继续开发，不能把本批当成全部计划完成。
- 用户已选：桌面以记事本临时内容验收；D24 同时提供本地/API embedding，默认不调用未配置的付费服务。

| 项目 | 值 |
| --- | --- |
| HEAD | `5d4bddd` 内核：修复多工具回填与委派串话——完整调用批次和会话作用域还原（246 单测全绿） |
| 分支 | `master`，跟踪 `origin/master`，无本地领先/落后 |
| 全量测试 | `uv run pytest -q` → **246 passed, 1 warning, 57.24s**（2026-10-27） |
| 已有 warning | `tests/test_web.py::test_model_test_endpoint_rejects_bad_model` 触发 Pydantic `UserWarning: Item 'summary' on TypedDict class 'ChatCompletionReasoningItem' is using the ReadOnly qualifier`（第三方库，登记不掩盖） |
| 解释器 | Python 3.12 + uv（`.venv`） |
| 参考项目 | `xuexi/hermes-agent`（gitignore，不入库；只学契约与错误边界，不整仓复制） |

**基线时的工作区其他改动（不是本任务的，禁止混入提交）**：

- `M src/omni/channels/web_static/index.html` —— 另一项前端工作，未提交；
- `?? prototype-html/` —— 同上；
- `?? docs/LOCAL_AGENT_PLAN.md`、`?? docs/DEEPSEEK_DEVELOPMENT_OUTLINE.md` —— 本轮计划文档，由 D00 单独提交。

## 2 离线测试约定

1. 默认 `uv run pytest` 只跑**离线**套件：不访问真实模型、真实 QQ/NapCat、真实安卓设备、真实 MCP 生产服务，不启停真实常驻服务，不读写真实 `data/`。
2. 所有持久化路径经 `build_runtime(data_dir=tmp_path)` / `ServiceManager(run_dir=..., log_dir=...)` 注入；尚未完全注入的见 A08（D04 关闭）。
3. 执行类安全测试一律**假执行器**（`runner=` / 假 client / 假 sender）并断言"调用次数为零"，禁止把真实命令当靶子。
4. 需要设备/账号/付费模型才能跑的验收属**手动套件**，缺失时状态记"待真实验收"，不计入完成。
5. 复现证据单独放 `tests/test_r0_repro.py`，断言的是"问题存在"；修复时翻转为负例，不删除文件。

## 3 阻断项审核（A01–A20）

证据等级：**复现** = 用假执行器/假客户端实际触发，未执行真实写入；**源码** = 代码明确呈现，尚未写复现用例；
**待验证** = 设计风险，需专项复现。审核方式是对着当前 `5d4bddd` 代码逐条核对，不照抄计划结论。

| 编号 | 当前结论（D00 审核） | 证据等级 | 证据位置 | 关闭任务 | 状态 |
| --- | --- | --- | --- | --- | --- |
| A01 | `peek_command` 前缀正则 + `shell=True`，复合命令/重定向/`&&` 全部穿过 SAFE | 复现→已修复 | `test_a01_*` 已翻转为负例；`test_shell_tools.py` 另有 10 种复合/重定向形态 | D01 | 已修复（待真实验收：真实命令面） |
| A02 | 只读名单含写子命令与敏感文件：`git branch <name>`、`git remote add`、`type .env` 均到执行器 | 完成 | 命令面：`test_a02_*` 翻转 + 14 种 git 写参数/泄密参数负例 + 8 种敏感路径负例；`type`/`get-content` 已移出 SAFE（`b17cdc1`）。文件域：敏感名单收口到 `tools/pathpolicy.py` 由 shell 与 `files_*` 共用；`files_read/search/list` 在打开文件前拒绝 `.env`/`id_rsa`/`data/`，搜索不返回命中片段（`tests/test_files.py` 26 例） | D01、D13 | 已修复（命令面 + 文件域） |
| A03 | 私聊判断 `if cfg.allowed_users and ...`：空白名单 = 不限，任何人私聊都被回复 | 复现→已修复 | `test_a03_*` 翻转；`tests/test_qq_scope.py` 7 例（空白名单/名单外/正常私聊/撤销即时生效） | D02 | 已修复（真实 QQ 收发待验收） |
| A04 | QQ/cron 空白名单放行风险 | 已修复 | 统一空名单拒绝；a52f0ec 补投原子领取与 OneBot 回执；未知不重发 | D02、D17 | 代码关闭，真实掉线补投待验 |
| A05 | QQ 私聊与各群历史混合 | 已修复 | dm/group 分场景、群个人上下文与工具隔离，81893be 事件去重/撤回与租约 | D02、D06、D22 | 代码关闭，真实两群隔离待验 |
| A06 | Web 并发切换共享会话造成串话 | 已修复 | 请求固定 sid、会话闸门与数据库租约、review 显式 sid；D22 QQ 同样接入 | D06、D22 | 回归通过，真实多入口并发待验 |
| A07 | 全套 API 无身份校验、无 Origin/Host 校验；`/api/terminal/ws` 直接 `accept()`；`/api/system/config` 回显 `qq.access_token` 明文 | 源码→已修复 | `tests/test_web_auth.py` 31 例：12 个敏感 GET 未配对即 401、写接口/服务/审批/WS 全拒、跨站 POST 无副作用、Host 伪造、过期/篡改会话、密钥不回显与三态修改、非回环监听默认拒绝 | D03 | 已修复（真实浏览器跨站尝试与 PTY 会话待手动验收） |
| A08 | `data_dir` 隔离有遗漏：安卓截图写全局 `DATA_DIR/android_shots`；Web 的 Cookie/配置路由用全局 `BILI_COOKIES_PATH`/`CONFIG_PATH`；`bootstrap` 构造 `BiliAPI()` 未注入 Cookie 路径（测试会读真机 Cookie） | 源码→已修复 | `config.RuntimePaths` 统一声明 12 个路径；`tests/test_paths.py` 10 例：路径派生、安卓截图、Cookie 读取、Web 写配置/Cookie、隔离实例不探测/不启停真机进程、真实 `data/` 目录 canary 全量快照不变 | D04 | 已修复（真机目录 canary 用 mtime 快照验证；跨盘符/junction 场景留待 D13 文件域） |
| A09 | 共享 JSON 只用 `threading.Lock`（锁不跨进程）、固定 `.tmp` 名字、无原子替换 | 已修复 | 任务/账本/额度迁入 `data/state.db`（跨进程事务 + 唯一键 + 条件更新）；技能与常驻记忆的**文件**读改写改用 `system/filelock` 跨进程锁 + `system/atomic` 唯一临时文件原子替换；`tests/test_state.py`、`tests/test_skills_history.py` 各用真实子进程验证「不丢写」 | D05、D08 | 已修复 |
| A10 | 损坏/不可读状态一律当空数据：`WriteBudget._load` 返回 `{}` → 当日额度被重置；`JobStore` 读失败降级 | 已修复 | 迁移区分 缺失/合法空/0 字节/损坏；损坏且库内无可用数据时**停用能力**（budget 一律停用），预算显示满额而非 0；工作台返回 503 而不是空列表；心跳不启动调度器 | D05、D25 | 已修复（历史用量确实无法恢复时在 detail 里写明） |
| A11 | 同步 Playwright 实例跨线程复用，`RLock` 只串行化、不改变线程归属；同 profile 无跨进程占用协议 | 已修复 | 专用所有者线程 + 有界队列 + `BrowserUnknown` 超时语义；profile 走 `resource_leases`（v3）；`tests/test_browser_owner.py` 8 例，含真实 Chromium 多线程用例与跨进程租约 | D07 | 已修复（真实登录 profile 未用于测试，符合边界） |
| A12 | 旧观察在刷新后重解释 idx，可能误点；动作无复检 | 已修复 | `88d1c47` 全动作观察绑定、单次令牌、树/像素/设备复核和动作后观察；负例零执行 | D16 | 离线已关闭，真设备闭环待验 |
| A13 | 截图只回路径，模型收不到图 | 已修复 | D15 产物归属/指纹和图像消息链，隐私门未开启如实降级；605 项批次证据见历史 | D15 | 图像链已交付，真实视觉模型质量待验 |
| A14 | 技能读改写不原子、缺少可恢复历史 | 已修复 | 跨进程锁、CAS、修订与回滚；D18 工作台全文/历史/差异/待审阅队列已交付 | D08、D18 | 代码关闭，持续使用体验待验 |
| A15 | 档案编辑总额度与学习入口检查不一致 | 已修复 | MemoryGuard 共用额度/遗忘/输入检查；D18 技能修订候选审阅与差异已交付 | D12、D18 | 代码关闭；审阅不是提升工具权限的门禁 |
| A16 | 每次 review 起 daemon thread（无队列上限/去重/累计预算）；每个新连接重建 FTS | 已修复 | **索引**：FTS 改由触发器增量维护，只在「首次 / 分词器升级 / 旧库无状态」重建一次（`tests/test_context.py` 5 例）；**review**：单 worker + 有界队列 + 按消息区间认领去重，队列满返回 `full`（`test_review_queue_is_bounded`、`test_review_is_deduplicated_by_message_range`）；模型消耗计入 D10 账本（`purpose=review`） | D11、D12 | 已修复（真实多进程并发 review 未演练） |
| A17 | cron 并行无上限、领取/依赖/投递不可靠 | 已修复 | 0c59902 数据库上限 2、续约/积压合并、成功 run+TTL/时区；a52f0ec outbox 原子领取与回执 | D05、D17 | 代码关闭，真实调度重启/掉线补投待验 |
| A18 | 服务停止裸 PID 可能误杀复用进程 | 已修复 | D25 创建时间/exe/nonce、保留句柄终止；PID 变化假终止器零调用；临时父子进程真机退出证据 | D25 | 旧真实服务迁移待用户参与 |
| A19 | shell 超时只依赖 `subprocess.run` 对直接子进程的处理；Windows 后代进程与"副作用是否已发生"未区分 | 已修复 | 进程树回收 + "已确认回收/回收状态未知"话术；D09 起工具异常/超时统一记 `side_effect_state=unknown`（`tests/test_runs.py::test_execute_distinguishes_the_four_outcomes`） | D01、D09 | 已修复 |
| A20 | 无运行账本/取消/token 与费用硬预算/统一 `ToolResult` 状态（工具回的是字符串） | 已修复 | 账本/事件流/协作式取消/`ToolResult`（D09，`memory/runs.py`、`core/run.py`、`tools/result.py`）；费用：调用前预留 + 调用后结算，日额度与父子运行共享，未知计价记 NULL 而不是 0（D10，`llm/budget.py`、`memory/usage.py`、`tests/test_usage.py` 18 例）；流式 usage 与半途断连（`llm/gateway.py::stream`、`tests/test_gateway.py` 7 例） | D09、D10 | 已修复（真实供应商账单对账待真实验收） |

补充观察（D00 新增登记，尚未编号为 A 项，供后续任务核对）：

- `peek_command` 的 `_PEEK_RE` 允许 `python --version` / `uv --version`，但同样允许 `python -V;任意命令`——A01 的同一根因，随 D01 一起关闭；
- ~~`WriteBudget._save` 直接 `write_text` 覆盖正在被其它进程读的文件（A09 同源）~~ → D05 已迁 `state.db`；
- `JobStore`/`ExecutionLog` 默认路径仍写死 `DATA_DIR`，与 D04 的 `RuntimePaths` 一致处理。

## 4 任务台账

| 任务 | 状态 | 关闭的证据 | 改动/commit | 测试证据 | 真实验收 | 下一步/阻塞 |
| --- | --- | --- | --- | --- | --- | --- |
| D00 | 完成 | 本文件 + `tests/test_r0_repro.py`（A01–A05 复现） | `3fbf762` | `uv run pytest -q` → 254 passed（246 基线 + 8 复现） | 不适用 | 进入 D01 |
| D01 | 完成 | A01、A02（命令面）、A19（进程边界） | 见第 5 节 | `uv run pytest -q` → 297 passed | 真实命令面（中文输出、带空格路径）已由本机 `git status` 用例覆盖；真实危险命令不做验收 | 进入 D02 |
| D02 | 完成 | A03、A04、A05 | 见第 5 节 | `uv run pytest -q` → 308 passed | QQ 真实收发未验（需 NapCat 扫码登录） | 进入 D03 |
| D03 | 完成 | A07 | 见第 5 节 | `uv run pytest -q` → 339 passed | 真实浏览器跨站请求、真实 ConPTY 会话待手动验收；前端设置页对 `access_token` 的展示需跟随（见第 5 节遗留项） | 进入 D04 |
| D04 | 完成 | A08 | 见第 5 节 | `uv run pytest -q` → 349 passed | 真机 `data/` canary 已用快照验证未被写；跨设备/跨盘符路径与 junction 留待 D13 | R0 收口，进入 R1（D05） |
| D05 | 完成 | A09（任务/账本/额度）、A10、A17（领取） | 见第 5 节 | `uv run --no-sync pytest -q` → 380 passed | 真机旧 JSON 为 `[]`，重启后迁移为空操作 | 进入 D06 |
| D06 | 完成 | A06 | 见第 5 节 | `uv run pytest -q` → 394 passed | 真实浏览器两个标签页并发聊天待手动验收（自动用例用 ASGI 直连） | 进入 D07 |
| D07 | 完成 | A11 | 见第 5 节 | `uv run pytest -q` → 403 passed（含真实 Chromium 用例） | 真实登录 profile（B站）未用于自动测试；桌面/无头差异待 D23 实站验收 | 进入 D08 |
| D08 | 完成 | A09（技能/常驻文件）、A14 | 见第 5 节 | `uv run pytest -q` → 421 passed | 真实 `skills/` 库未用于写测试（一律临时根）；两进程并发是真实子进程，非 mock | 进入 D09 |
| D09 | 完成 | A19、A20（账本/取消/ToolResult） | 见第 5 节 | `uv run pytest -q` → 439 passed | 真实网关下的取消时延、跨进程取消（返回 409）待 D27 实测 | 进入 D10 |
| D10 | 完成 | A20（费用预算、流式 usage） | 见第 5 节 | `uv run pytest tests/test_gateway.py tests/test_usage.py -q` → 54 passed；`uv run pytest -q` → 501 passed | 真机 `data/state.db` 已 v2→v5 迁移，账本自检通过（演示流水已清理）；真实供应商 429/超时/流式断连与账单对账未验 | 进入 D11 |
| D11 | 完成 | A16（FTS 生命周期）、持久摘要 | 见第 5 节 | `uv run pytest tests/test_context.py -q` → 13 passed；`uv run pytest -q` → 514 passed | 真机 `data/memory.db` 一次性升级（legacy rebuild 0.04s），二次打开不再重建（0.02s），16 个会话与检索不变 | 进入 D12 |
| D12 | 完成 | A15、A16（review 队列/去重） | 见第 5 节 | `uv run pytest tests/test_memory_governance.py -q` → 18 passed；`uv run pytest -q` → 535 passed | 真机库未做写入压力测试；"候选队列/差异确认"按当前风险面评估未做（见第 5 节剩余限制） | 进入 R3（D13 结构化文件工具） |
| D13 | 完成 | A02（文件域）、路径与写入边界 | 见第 5 节 | `uv run pytest tests/test_files.py -q` → 29 passed；`uv run pytest -q` → 564 passed（无 F/E） | 真机仓库只读冒烟：`src/omni` 列表、`config.py` 前 3 行 + 指纹、`.env`/`data/` 被拒、`C:/Windows/win.ini` 越根被拒、`data/file_history` 未被创建；junction 逃逸与只读盘在临时目录验证；真实编辑器并发写入待 D27 | 进入 R3（D14 通用网页读取） |
| D14 | 完成 | 新能力：通用网页读取（无对应 A 项，防的是 SSRF/注入/凭据外带） | 见第 5 节 | `uv run pytest tests/test_web_fetch.py -q` → 25 passed；`uv run pytest -q` → 589 passed（无 F/E） | 真实公网：`https://example.com/` 200 + 正文提取；`https://www.baidu.com/` 返回空壳页并被如实说明（需 JS）；发往本机假服务的超大流/重定向/429/超时/登录页/注入用例全绿 | 进入 R3（D15 产物与视觉输入） |
| D15 | 完成 | A13 | 见第 5 节 | `uv run pytest tests/test_artifacts.py -q` → 13 passed；`uv run pytest -q` → **605 passed（无 F/E）** | 真实 Chromium 冒烟通过（见第 5 节真机证据）；真实安卓截图待 D16 插真机；真实视觉模型的看图效果需用户配置 `vision.send_to_cloud` 后实测 | 进入 D16（A12 离线修复） |
| D16 | 待真实验收 | A12 全动作观察绑定 | 88d1c47 | 763 passed；锁帧/焦点/输入法/重放/互斥回归 | 未连接真机，多设备/旋转/断连/MJPEG/输入法待验 | ACCEPTANCE 北极星 D |
| D17 | 待真实验收 | A04/A17 调度与可靠投递 | a52f0ec、0c59902 | 644/648 项批次全绿，含跨进程领取/回执/并行/时区/依赖 | 真实 QQ 掉线补投与重启未闭环 | ACCEPTANCE 北极星 C |
| D18 | 待真实验收 | A14 技能编辑/审阅 | 3a1c536、03ede8f | CAS/历史/回滚/差异和已读递进测试通过 | 真实 agent 修订后的审阅体验待验；无外部导入通道，隔离安装后置 | 监督使用并记录证据 |
| D19 | 待真实验收 | MCP stdio 管理/权限漂移 | a52f0ec 及前序批次 | 真实离线 stdio 子进程、漂移撤权/人工复核/重连回归 | 生产受信服务未提供；HTTP/OAuth 后置 | ACCEPTANCE 北极星 E |
| D20/D21 | 待真实验收 | R5 桌面 | 见最新桌面批次 | 676 passed；记事本真机读/写/截图通过 | 第二应用、多屏、锁屏、管理员窗口未验；无通用前台接管 | 按应用扩展验收 |
| D22 | 待真实验收 | QQ 场景/主动性 | 见最新 D22 批次 | 692 passed，含四进程去重与工作台停止 | 真实 QQ 登录、群/重连/撤回待验 | 用户扫码后验证 |
| D23 | 待真实验收 | B站 UI 写 | 见最新 D23 批次 | 741 passed；真实 Chromium 离线页面 | 默认关闭，实站账号/选择器/一次明确发布待用户参与 | 核验实站控件后单次人工批准 |
| D24 | 待真实验收 | 混合检索 | `bb05219` | 662 passed；20 样本 FTS 基线 | 双接口已交付，真实模型收益未验，默认关闭 | 配置模型后评测 |
| D25 | 待真实验收 | A10、A18、运维恢复 | 见最新 D25 批次 | 718 passed；临时父子进程真实停止；doctor 真机只读 | 旧服务身份迁移、登录 profile 与多次启停待验 | D27 验收 |
| D26 | 待真实验收 | 架构/CI/任务评测 | 见最新 D26 批次 | 789 passed；24×3 离线场景；独立环境复现 | 真实模型任务率、生产资源阈值和托管 CI 接入待验 | D27 采样与真实任务 |
| D27 | 待真实验收 | 副本恢复/常驻/证据/发布 | 见最新 D27 批次 | 817 passed；实际 v13 副本恢复、原生采样与临时进程回收 | 72 小时未跑满；15 项真实证据未齐；无发布授权 | 按 ACCEPTANCE.md 逐项验收 |

## 5 变更记录

### 2026-10-28 · 修复（重复定义清理：产物保引用曾静默失效）

- **发现方式**：D18 收尾时用 AST 扫同名重复定义（`tests/`+`src/`），共 7 处，全部是误粘贴的副本。
- **改了什么**：
  - **`core/run.py`（生产代码，真缺陷）**：`ActiveRuns` 曾有**两个 `snapshot()`**——旧的返回库内
    `Run` 记录（web 断线取消用），D15 新增的返回活 `RunContext`（产物保引用用），**后者覆盖前者**。
    结果：`_sweep_artifacts()` 拿到的每项都没有 `_attached_image_ids` → keep 集合恒空，
    「清理跳过仍在使用的产物」**静默失效**。修复：新增的改为 `contexts()`（docstring 写明与
    `snapshot()` 的形状区别），`agent._sweep_artifacts` 改调它；web 的 `snapshot()` 调用点不变。
  - 测试副本（逐字节相同，删除后者）：`tests/test_backup.py::_rows`、
    `tests/test_files.py::test_web_approval_detail_is_truncated_for_huge_writes`、
    `tests/test_web_auth.py` 的 4 个（pairing cookie/redirect、cross-site、expired rotation、
    websocket gate matrix）、`tests/test_state.py::test_v2_to_v3_migration_keeps_session_leases`。
- **补了什么**：`tests/test_artifacts.py` 新增回归用例——钉住 `contexts()`/`snapshot()` 两种形状，
  并**真正覆盖**保引用路径：过期产物被活跃 run 引用时清理不删、引用清掉后下回合收走。
- **跑了什么**：`uv run pytest tests/test_artifacts.py tests/test_state.py tests/test_backup.py tests/test_files.py tests/test_web_auth.py -q`
  → **109 passed**；重复定义扫描 → **0**；全量见提交信息。
- **行为变化**：产物清理真正遵守"活跃 run 仍引用的不删"；测试数量不变（删的都是从未执行过的影子副本）。

### 2026-10-28 · D18 第二批（候选变更审阅队列：agent 自动改技能可见可回滚）

- **改了什么**：
  - `memory/state.py` 升 **v9**：`skill_acks(skill, rev, acked_at)` + `SkillAckStore`——
    `ack()` 用 `MAX` 只前进（刷新页面不会把已读变回未读）、`acked()` / `acked_all()`；
  - `channels/web.py`（注意：`/api/skills/pending` 必须声明在 `/api/skills/{name}` 之前，
    否则 "pending" 会被当成技能名）：
    - `GET /api/skills/pending`：逐技能读最近 50 条修订，**author 不是 user（工作台编辑）
      也不是 external（磁盘外部改动快照）**且 rev 大于已读标记的才算待审阅；
      每条带 `base_rev`（有上一版才给"回滚这条"入口，历史被裁剪时为 null）；
    - `POST /api/skills/{name}/ack`（标到 rev）、`POST /api/skills/ack-all`（全部已读）；
    - `GET /api/skills/pending/{name}/diff?rev=`：修订 rev 相对**上一版**的 unified diff
      （没有上一版就从空开始）——审阅要看的是"这次改了什么"，不是"和现在差多少"；
  - 工作台技能页：待审阅时出现黄色横幅「⚠ N 条技能变更待审阅（小鱼自动改的）→ 审阅」；
    审阅弹窗列出每条变更（版本/摘要/时间/作者）、差异、回滚这条、知道了、
    全部标记已读；回滚走既有 CAS 端点（现取指纹，冲突即拒绝）成功后自动 ack 该条。
- **关闭了什么**：D12 转入的「候选变更队列/差异确认」（技能域）——agent/review 的自动改动
  第一次有了"待审阅"清单、单条 diff 与一键撤销；A14 的 UI 部分补全。
- **跑了什么**：`uv run pytest tests/test_web.py -k pending -q` → 2 passed；
  `uv run pytest tests/test_state.py -q` → 23 passed（新增 1 例：已读标记只前进、多技能独立）；
  关联批次 web+skills+skills_history+arch → 79 passed；全量见提交信息。
- **真机证据**：`data/state.db` 实际走过 v8→v9（迁移前备份 `data/backups/pre-d18-queue`）；
  迁移后 `degraded` 空、`skill_acks` 表可用、二次打开版本仍为 9；冒烟写入的行已清理。
- **没测什么**：真实 agent 自进化（review 改技能）后横幅出现→审阅→回滚的用户体验
  （接口契约已验，界面无自动化）；多标签页同时审阅（ack 只前进，天然幂等）。
- **行为变化**：
  1. 小鱼自动沉淀/修订技能后，技能页出现"待审阅"横幅；看过的可以标已读；
  2. 每条变更能看"这次改了什么"（相对上一版），不满意可一键回滚到变更前
     （产生新修订，可再撤销）；
  3. 用户自己的工作台编辑与磁盘外部改动不进队列——那是用户自己做的。
- **剩余限制（明确记录）**：
  1. 这是**可审阅**清单，不是审批门禁：agent 的技能改动仍立即生效（技能内容不改变风险等级/
     工具权限，这条底线由风险分级与审批器保证），队列负责"让人看见+可撤销"；
  2. 只看最近 50 条修订/技能（历史本身即保留 50 条）；被裁剪的旧修订不会出现在队列；
  3. references/ 附件的变更单独记录在修订清单里，队列只审主文 diff（附件不进 diff）；
  4. 没有通知推送（QQ/桌面提醒）——横幅在下次打开工作台时可见。
- **过程记录**：
  1. 待审阅判定排除 `external`（磁盘外部改动快照，由 `_snapshot_unversioned` 写入）——
     那是用户在记事本里改的，不该被当成"agent 的变更"；
  2. diff 取"上一版→这一版"而不是"这一版→当前"：后者会把之后的所有改动混进来，
     审阅时看不清"这次到底改了什么"；
  3. ack 用 `MAX` 而不是覆盖：多标签页/先后审阅时旧标记不能把已读退回去；
  4. 回滚入口依赖 `base_rev` 是否存在（历史裁剪后没有上一版内容，按钮就不出现）。

### 2026-10-28 · D19 第二批（MCP schema 漂移检测 + 面板手动重连）

- **改了什么**：
  - `memory/state.py` 升 **v8**：`mcp_schemas` 表（server/fingerprint/tool_count/
    confirmed_at/seen_at）+ `MCPSchemaStore`：`observe()` 首次登记即确认、指纹变化**清空
    confirmed_at 并返回旧指纹**、`confirm()` 只认当前指纹（指纹又变了就拒绝——确认的必须是
    看到的那份）、`status()`；
  - `tools/mcp/client.py`：`MCPManager.fingerprint(tools)`——名称 + `inputSchema` 规范化序列的
    短哈希；**description 不进指纹**（改文案不该触发授权复查，改参数形态才该）；`reset(server)`
    ——同一日志目录重建连接（失败连接已封闭，close 幂等），未配置服务拒绝；
  - `channels/web.py`：`GET /api/mcp/overview` 带 `schema_state`（absent / confirmed / drifted）
    与指纹；`GET /api/mcp/{server}/tools` 发现时登记指纹，漂移则在响应带 `drift` 提醒；
    新增 `POST /api/mcp/{server}/confirm-schema`（用户复查后确认）与
    `POST /api/mcp/{server}/reset`（手动重连，返回新状态）；
  - 工作台「MCP」页：指纹状态行（目录已确认 / ⚠ 工具目录已变化请复查只读授权 / 未登记）、
    漂移时出现「复查后确认」按钮、failed/closed 状态出现「重连」按钮、查看工具后自动刷新状态行。
- **关闭了什么**：大纲 D19 的「命令/参数/服务身份/schema 指纹变化后重新检查只读授权」条目
  ——指纹变化可见、必须人工复查确认后才清除提醒。
- **跑了什么**：`uv run pytest tests/test_mcp.py -q` → **18 passed**（新增 3 例：
  指纹存储状态机——首次登记即确认/未变化无提醒/漂移返回旧指纹且清空确认/旧指纹确认被拒；
  指纹规则——description 变化不触发、参数形态变化触发；真实 stdio 服务端到端——发现登记 →
  服务改 schema → 漂移提醒 → 确认清除 → 失败服务重连回 idle → 未配置服务 404）。
- **真机证据**：`data/state.db` 实际走过 v7→v8（迁移前备份 `data/backups/pre-d19-drift`）；
  迁移后 `health().degraded` 为空、outbox 与 mcp_schemas 表可用、二次打开版本仍为 8（幂等）。
- **没测什么**：真实受信 MCP 生产服务的联调（用户提供后按 `docs/MCP.md` 配置）；
  重连后的自动退避（本批只做手动重连）；
  服务端"同名工具语义变化但 schema 不变"的情况（指纹看不出来，只能靠人读描述——
  面板如实展示上级提醒文案，不假装能自动识别）。
- **行为变化**：
  1. 服务升级换工具参数形态后，面板出现「⚠ 工具目录已变化，请复查只读授权」，
     不再静默沿用旧授权判定；
  2. 用户复查确认后提醒清除；确认的指纹再变会重新出现提醒；
  3. 连接失败的服务可以在面板上点「重连」得到一次干净握手（配置不变）。
- **剩余限制（明确记录）**：
  1. 指纹只看 name + inputSchema——工具**语义**变化（同名同参但行为变了）无法检测；
  2. 漂移提醒不**阻断**只读调用（`mcp_read_tool` 仍按用户配置的白名单放行）——
     这是"提醒"不是"门禁"，与 D18 候选队列同性质；真正阻断需要用户把工具从白名单删掉；
  3. 重连没有自动退避重试（失败即 failed，等人点按钮），避免反复拉起坏服务；
  4. 没有 MCP resources/prompts 的指纹（本批只做 tools 目录）。
- **过程记录**：
  1. 漂移测试的服务端脚本按请求现读 marker 文件（而不是进程启动时读）——连接是长活的，
     启动时读一次会让"服务升级"永远测不出来；
  2. `MCPSchemaStore.observe()` 首次登记就写 `confirmed_at`：用户点「查看工具」看到目录
     这一刻就是复查动作，首次不该报警；
  3. 指纹取 `[:16]`（64 位）够用且面板可读；测试断言"指纹变了"而不是"等于某个固定值"，
     避免把实现细节钉死在测试里。

### 2026-10-28 · D17 第二批（投递 outbox：掉线不丢、恢复补投、unknown 不自动重发）

- **改了什么**：
  - `memory/state.py` 升 **v7**：`outbox` 表（job/target/content_hash/status/attempts/last_error/
    时间戳）；**去重只在活跃期生效**——`(target_qq, content_hash)` 在 pending/unknown 期间唯一
    （部分唯一索引），进入 sent/discarded/dead 终态后同内容可重新入队；`OutboxStore` 门面
    （bootstrap 构造）：enqueue（重复→None、超 50 条活跃→ValueError）/ list（活跃在前）/
    pending（attempts < 预算）/ mark（sent 带 sent_at）/ attempt / retry（unknown/dead/discarded
    → pending 且 attempts 清零）/ discard（sent 不可丢）；
  - `core/cron.py`：
    - `make_runtime_deliver` 投递分类——QQ 未连接 → **入队 pending**（"已入待投递队列"）；
      白名单撤销 → **不入队**（撤销是用户意图，恢复后也不许把被拒消息发出去）；
      发送中途抛异常 → **入队 unknown**（"可能已发出一部分，不自动重发"）；QQ 未启用 → 不入队；
      入队失败（状态库降级/队列满）如实说明，结果本身已落会话不丢；
    - `make_outbox_drainer` + `outbox_drain_loop`：补投只处理 pending——重查白名单
      （撤销→discarded）、`ConnectionError`（没写到 socket）→ 记尝试保持 pending、其它异常 →
      unknown 停止自动补投、尝试满 3 次 → dead；单轮异常不杀循环；
  - `channels/heartbeat.py`：心跳进程挂载 `outbox_drain_loop`（每 60s），启动横幅说明补投行为；
  - 工作台：`GET /api/system/cron/outbox`（活跃在前，正文只回 80 字预览）、
    `POST .../retry`（手动重排，unknown 也允许——风险在面板写明）、`POST .../discard`；
    设置中心「定时任务」新增「待投递」面板：状态徽章（待自动补投/结果未知/已投递/已丢弃/
    补投耗尽）、已试次数、错误、重新排队/丢弃按钮（confirm 里写明 unknown 的重复发送风险）。
- **关闭了什么**：A17 的投递部分（outbox、去重、unknown 不自动重发、授权频次独立判定——
  补投前逐条重查白名单）。D17 核心范围完成。
- **跑了什么**：`uv run pytest tests/test_outbox.py -q` → **12 passed**（活跃去重/终态释放名额/
  容量上限与腾位/尝试预算与死信/retry-discard 状态机/四类投递分类/补投 no-op-撤销-上限-
  unknown/工作台端点含 sent 保护与 404）；关联批次 cron+state+web+arch → 88 passed；全量见提交。
- **真机证据**：`data/state.db` 实际走过 v6→v7（迁移前备份 `data/backups/pre-d17-outbox`，
  含 state.db sha256）；迁移后 `health().degraded` 为空、outbox 表可用、二次打开版本仍为 7
  （幂等）。
- **没测什么**：真实 QQ 掉线→心跳补投的端到端（需 NapCat 扫码登录后人为断连实测）；
  长报告分片发送中途中断的部分重复场景（`send_private` 按片循环，第二片失败时第一片已发出——
  该场景归入 unknown 由人决定，未在真机复现）；两个心跳进程同时补投（与任务领取不同，
  outbox 补投没有跨进程互斥——见剩余限制 3）。
- **行为变化**：
  1. QQ 掉线期间心跳任务照跑，结果不再只躺在账本里"未投递"——进入待投递队列，
     QQ 恢复后一分钟内自动补投；
  2. 发送结果未知的消息**宁可漏发也不自动重发**（重复发送比漏发半条更糟），
     面板上由人决定；
  3. 白名单撤销对队列里的待投递同样生效（补投前逐条重查，撤销即丢弃）。
- **剩余限制（明确记录）**：
  1. 补投周期固定 60s，不做指数退避（本地单所有者场景，3 次上限已足够保守）；
  2. 队列只保留最近 50 条终态记录供查看（滚动视 `list(limit=50)` 而定，无清理任务——
     表本身不删行，需要时手工清理或等 D26 运维批）；
  3. **两个心跳进程同时跑时补投可能双发**（任务领取有 claim 互斥，outbox 补投没有：
     `pending()` 读取与 `mark(sent)` 之间没有条件更新护栏）——单心跳进程部署下不触发；
     多进程部署前需要把补投也改成条件领取。此为已知限制，登记待 D26；
  4. `context_from` 仍绑定"源任务绑定会话的最后一条 assistant 输出"，未绑定具体成功 run。
- **过程记录**：
  1. 去重键选 `(target_qq, content_hash)` 而不是 `(job, content_hash)`：同一条消息发两次
     的判定不该看它来自哪个任务——两个任务产出相同文本时，对方仍然只该收到一条；
  2. 补投的异常分类第一版想按异常类型细分（websockets 异常 vs 其它），但 core 层不该
     import websockets——改成语义分类：`ConnectionError`（client 自己的"没连接"前置检查）
     = 明确没发；其它 = unknown。简单且不引依赖；
  3. 测试第一版断言"不自动重发"失败——补投路径的文案写的是"已停止自动重发"。
     文案统一与否不影响行为，但断言逼着两处文案对齐了。

### 2026-10-28 · 工作台可视化批次（运行进度 + 用量面板 + D19 MCP 面板第一批）

> 用户要求：继续推进路线，并把能力做进可视化界面。本批把**已有后端但界面没露出**的
> 运行事件流与费用账本做成了界面，并交付 D19 第一批（MCP 管理面板）。

- **改了什么**：
  - **运行进度实时可视化（前端，消费 D09 端点）**：等待回复期间每秒轮询
    `GET /api/runs?session_id=` 找进行中的 run，续读 `GET /api/runs/{id}/events?after=seq`，
    把事件渲染进等待气泡——模型调用/工具执行/附图/压缩摘要/预算拦截/失败各有一行；
    附「停止本回合」按钮（`POST /api/runs/{id}/cancel`，取消语义如实提示
    "已在途的副作用不撤销"）；回复下方标 `cost_micro` 费用与 cancelled/failed 状态。
    轮询有防重入护栏（响应慢于 1s 不叠加请求），失败静默（不影响聊天本身）；
    首条消息会话 id 未知时不轮询（等响应回来才知道 sid，不猜）。
  - **模型用量可视化（前端，消费 D10 端点）**：顶栏新增「今日 $x / 上限」徽章
    （超限变红、不足 10% 变黄、未设上限如实写"只记账不拦截"）；设置中心新增「用量」页：
    今日花费/剩余额度、按用途汇总（次数/失败/未知价）、最近调用流水（模型/tokens/费用/重试）、
    模型能力声明表（视觉/工具/流式/上下文/单价）。
  - **D19 第一批：MCP 管理面板**：
    - `tools/mcp/client.py`：`MCPConnection.status()`——纯派生**不触发连接**
      （GET 状态不得有启动副作用）：idle/starting/ready/failed/closed 五态 +
      命令/超时/白名单计数/最近错误；连接跟踪 `_ready`（握手完成才算"已连接"，
      与"启动中"分开说）；`MCPManager.status()` 含 **enabled=false 的停用条目**
      （面板能看到"配置过但停了"，不是查无此人），保留全量配置不再只存活跃连接；
    - `channels/web.py`：`GET /api/mcp/overview`（纯只读）、`GET /api/mcp/{server}/tools`
      （**用户点击才触发**的真实 stdio 发现；未配置/停用 404，起不来 409）；
    - 前端设置中心新增「MCP」页：服务状态卡（状态/命令/白名单计数/错误）、
      「查看工具」按钮展开工具目录，🟢只读白名单 / 🟡需人工确认的授权标记
      **只来自用户配置**（服务自报 readOnlyHint 不参与授权，面板如实展示这一判定）。
- **关闭了什么**：D19 大纲的「列出配置/连接/健康/错误状态」与「工具数量与输出上限的
  可见面」部分；HANDOFF 用户要求的"功能进可视化界面"第一批（运行/费用/外部工具三面板）。
- **跑了什么**：`uv run pytest tests/test_mcp.py -q` → **15 passed**（新增 2 例：
  状态纯派生——idle 不起子进程、发现后 ready、坏命令 failed 且 last_error 可见、停用条目在列；
  面板端点——config.yaml 配三个服务走 build_runtime+TestClient 全链路，真实 stdio 发现
  demo 服务、409/404 路径、overview 前后状态变化）；关联批次
  `tests/test_web.py tests/test_arch.py tests/test_mcp.py` → 53 passed；全量见提交信息。
- **没测什么**：前端的轮询渲染、停止按钮、徽章配色（无浏览器测试基建——接口契约
  全部由既有端点测试覆盖，前端是纯消费者）；MCP 面板与真实生产 MCP 服务的联调
  （待用户提供受信服务）。
- **行为变化**：
  1. 发消息时能看到小鱼正在做什么（每一步模型调用与工具执行），并能中途叫停；
  2. 每条回复标出本回合花了多少钱；顶栏随时可见今日花费与剩余额度；
  3. 设置中心能看用量明细与模型能力；MCP 服务的状态/工具/授权第一次可见可查。
- **剩余限制（明确记录）**：
  1. D19 的 **schema 指纹漂移检测**未做（服务升级换 schema 后，旧只读授权应重新检查——
     目前面板只展示当前授权标记，不比对历史指纹）；
  2. MCP 启停仍是"入口生命周期"管理（工作台 lifespan 关闭时收尾），没有面板上的
     手动重连/重启按钮——连接失败后如实标 failed 并提示查日志，重连属 D19 剩余；
  3. Streamable HTTP / OAuth 未做（大纲允许独立后置）；
  4. 运行进度只在 Web 入口（CLI/QQ 无事件消费界面；心跳执行可在 cron 历史看结果）。
- **过程记录**：
  1. YAML 1.1 的坑：测试里裸写 `off:` 服务名被解析成**布尔键 False**，
     Pydantic 报 `servers.0.[key]` 不是字符串——服务名必须加引号 `"off":`；
  2. `MCPManager` 原来只保留 enabled 的连接——面板要展示停用条目就得留全量配置；
     顺手让 `status()` 的五态语义与连接生命周期对齐（thread 起了 ≠ 握手完成）；
  3. 用量徽章的颜色阈值（超限红/低于 10% 黄）是前端判断，账本数据仍是后端唯一权威
     ——徽章只是展示，不参与拦截。

### 2026-10-28 · D18 第一批（技能工作台编辑：保存 CAS / 历史 / 差异 / 回滚）

- **改了什么**：
  - `memory/skills.py` 新增 `save_raw(name, text, expected_hash, author, source)`：编辑器
    **全文**保存——先按 SKILL.md 结构解析拆 meta/正文（结构规则是存储层的私有知识，
    不上移到 Web 层），再走与 `rewrite` 完全相同的加锁-CAS-linter-修订路径；
  - `channels/web.py`：`GET /api/skills/{name}` 主文改走 `read()` 并返回**内容指纹**
    （编辑器 CAS 用）；新增 `POST /api/skills/{name}/save`（指纹冲突 409、结构/校验
    失败 400、成功返回新指纹）、`GET /api/skills/{name}/history`（修订元数据列表）、
    `GET /api/skills/{name}/revision/{rev}`（历史全文）、`GET /api/skills/{name}/diff?rev=`
    （历史 → 当前的 unified diff，400 行截断并标注 truncated）、`POST /api/skills/{name}/rollback`
    （带指纹 CAS，回滚产生**新修订**）；
  - 前端：技能列表加「查看/编辑」按钮；编辑器弹窗（全文 textarea、保存带指纹、
    冲突提示后可一键重新加载、历史列表、差异预览、回滚二次确认）。
- **关闭了什么**：HANDOFF §6 建议第 3 条（技能只能看不能改）；A14 的编辑 UI 部分
  （D08 只交付了仓储 API，本轮补上界面承载）。
- **跑了什么**：`uv run pytest tests/test_skills_history.py tests/test_skills.py tests/test_web.py tests/test_arch.py tests/test_agent.py -q`
  → **86 passed**（2026-10-28；新增 save_raw 单测 1 例 + 编辑器端到端 1 例：
  错指纹 409 不覆盖 → 正指纹保存 → 历史来源 workbench → diff 含增删行 → 回滚产生新修订）。
- **没测什么**：浏览器里的真实编辑器交互（无前端测试基建）；与 agent 的 skill_patch 在
  真实双进程下的同时编辑（临界区语义与 D08 的跨进程锁用例同源，未重演练）；
  超大技能文本（受 MAX_BODY 上限约束，未做 UI 端长度提示）。
- **行为变化**：
  1. 工作台「技能」页从只读变为可编辑：改动过 linter、产生修订、指纹冲突会拒绝；
  2. 技能的全部历史在面板可见（作者/来源/时间/摘要），可看任意两版差异、可回滚；
  3. 用户手改与 agent 修改在修订历史里可区分（author=user/source=workbench）。
- **剩余限制（明确记录）**：
  1. D12 转入的**候选变更/差异确认队列**未做（review/agent 的敏感技能改动仍直接生效，
     依赖额度与 linter 约束——治理增强需界面承载 diff 审批，留下一批）；
  2. **外来包隔离安装**未做（大纲 D18 的隔离区/清单检查/不执行 postinstall）——
     当前技能只能在本机创建/编辑，没有导入通道，也就没有导入攻击面；
  3. references/ 附件仍是只读（列表与按需文本读取），主文编辑不改动附件。
- **过程记录**：
  1. `save_raw` 先在临界区外解析、再进 `rewrite` 的锁内做真正 CAS——编辑器文本是
     权威内容，解析早一点没有竞态；若解析放进锁内，`rewrite` 还要再解析一遍纯浪费；
  2. `GET /api/skills/{name}` 从 `view()` 换成 `read()` 取主文——`view` 的按字节 `_remember`
     行为 `read` 也有，隐式基线语义不丢；references 分支维持 `view` 不变；
  3. 冲突与校验失败按存储层自己的确定性话术区分（"被改动过"→409）——这是代码生成的
     消息不是模型文本，与 D17 刚修掉的"前缀判成败"不是同一类问题。

### 2026-10-28 · D17 第一批（cron 任务编辑 + 成败判定去前缀化）

- **改了什么**：
  - `channels/web.py`：新增 `POST /api/system/cron/update`——编辑已存在任务，与创建同一套
    `Job.validate()`/白名单/context_from 源校验；字段语义按**是否出现在请求里**区分
    （没传 = 不改，显式 `null` = 清除，前端编辑表单把空输入框提交成 null 才能删掉
    deliver_to_qq/context_from）；`expected_revision` 乐观并发（旧表单 409，不静默覆盖）；
    调度字段或 enabled 变化自动重算 `next_run_at`；`GET /api/system/cron` 补 `revision`/
    `every_minutes`/`daily_at`/`skills` 字段（编辑表单回填要用）；
  - `tools/cron_tools.py`：新增 `cron_edit` 工具（SAFE，与 add/remove 同级）——只传要改的
    字段、显式 null 清除、白名单与校验同 create、调度变化才重算下次运行；
  - `core/cron.py`：**成败判定去前缀化**——`_run_job_safely` 的 `ok` 只看执行器是否抛异常，
    不再看 `result.startswith("执行失败")`（模型对"总结失败任务"类指令的正当回答会以这四个字
    开头，被误记失败）；`make_runtime_executor` 改用 `agent.run_turn` 的结构化状态：
    `status != done`（取消/预算拦截/失败）抛给调度器记失败，**失败回合不进 review 队列**；
  - `memory/state.py`：顺手删掉 `upsert_job` 里重复一行的 `self._require("jobs")`（无害赘行）；
  - 前端：任务行加「编辑」按钮，创建表单复用为编辑表单（回填全量字段、任务名锁定、
    保存带 revision、取消编辑还原）。
- **关闭了什么**：HANDOFF §6 建议第 4 条（cron 只能删了重建）；
  LOCAL_AGENT_PLAN §0 收口项 1 的"cron 按中文前缀判成功"。
- **跑了什么**：`uv run pytest tests/test_cron.py tests/test_web.py -q` → **51 passed**
  （2026-10-28；新增：编辑部分更新/换调度 null 清除/白名单拒绝不动定义/任务不存在/校验失败、
  答案以"执行失败"开头仍记成功、失败回合抛异常且不触发 review、Web 编辑 happy path + 409 + 404 + 400）。
- **没测什么**：真实心跳进程运行中通过工作台编辑任务、编辑与到期领取交错的实机行为；
  前端编辑表单（无浏览器测试基建，靠 TestClient 验接口契约）；投递 outbox（本批未做）。
- **行为变化**：
  1. 定时任务可以原地编辑（prompt/调度/投递/技能/链/启停），不用删了重建丢执行历史；
  2. 模型的回答文本不再参与成败判定——执行器正常返回就是成功，抛异常才失败；
  3. 回合被预算拦截或取消时，账本如实记失败（含 error_code），不再把"预算已拦截"的解释文本当成功结果。
- **剩余限制（明确记录）**：
  1. 大纲 D17 的**投递 outbox/去重、`context_from` 绑定具体成功 run、并行上限与积压策略**
     仍未做——本批只交付"编辑"与"判定收口"两个明确欠账，不冒充 D17 完成；
  2. 编辑与调度器推进并发：定义变更走 `expected_revision`，但**没有**"正在执行的任务用编辑前
     定义跑完"的快照语义（执行器拿的是领取时的 Job 对象，领取后编辑不影响本次执行——
     这是现状描述，不是承诺的原子性）；
  3. 时区仍是本地时区（`daily_at` 按机器时区），显式 timezone 属大纲 D17 剩余项。
- **过程记录**：
  1. Web 端点第一版用「值非 None」判断"字段是否提供"——前端清空输入框想删掉 deliver_to_qq
     时根本发不出"清除"语义。改成 `payload.model_fields_set`（Pydantic 记录哪些键真的出现在
     请求里），"没传"与"传 null"才分得开；
  2. 换调度方式（every_minutes → daily_at）必须同时把另一个字段显式置 null，否则触发
     "二选一"校验——测试第一版就漏了，正好证明字段级语义是必要的；
  3. 假 Agent 从 `chat` 换成 `run_turn` 后，失败回合测试顺手验证了"不进 review"——
     原实现失败时也会 spawn review，等于对半截对话做记忆整理。

### 2026-10-28 · D16（安卓观察—动作—验证：A12）

- **改了什么**：
  - `tools/android/driver.py`：新增**观察身份** `observation_id = sha256(设备序列号 + 树内容)[:8]`
    （时间不进哈希——同一界面两次观察 id 相同，点击前复核才有"界面没变"的可比性）；
    `refresh_tree()` 输出头部带 `观察 obs=<id>（设备 …，时间…）`；缓存持久化带上 observation_id；
    **删除了"新鲜缓存跳过 dump"的陈旧 TTL 机制**（`fresh_ttl_s`/`_cache_is_stale`）——
    缓存"新鲜"只说明我们最近 dump 过，不代表屏幕没变（弹窗/旋转/跳转不会通知我们），
    这个优化正是 A12 的根因；
  - `tap_element(idx, observation)` 重写为观察绑定点击：不带 observation 直接拒绝（零设备调用）；
    点击前**重新 dump**，内容哈希与模型引用的观察一致才点；不一致**绝不盲点**，
    返回新观察让模型重新选择；点击后**强制复检**并返回点击后的新观察（复检失败如实报
    "界面状态未知"，不假装完成）；
  - `tools/android/tools.py`：`android_tap_element` 增加 required 参数 `observation`；
    `android_screen_tree` 描述改为"把 obs 原样传给 tap"；模块 docstring 更新为观察绑定纪律；
  - 工作台 `/api/android/element` 同一套复核（面板传 observation，缺省用当前缓存身份）；
    `/api/android/tree` 响应补结构化 `observation` 字段。
- **关闭了什么**：A12（tap_element 在树刷新后用同一个 idx 找元素可能点到别的东西；
  动作后无强制复检）。
- **跑了什么**：`uv run pytest tests/test_android.py -q` → **29 passed**（旧契约 6 例重写 +
  新增 6 例：无观察拒绝且零设备调用 / 换屏拒绝返回新观察 / 点击前复核三次 dump /
  点击后复检失败报未知 / 观察身份绑定设备与内容 / 重启恢复后仍复核）；
  关联批次 `uv run pytest tests/test_web.py tests/test_arch.py tests/test_agent.py tests/test_cron.py -q`
  → 58 passed（2026-10-28）。
- **没测什么**：真实安卓设备的任何操作（FakeRunner 假执行器，断言"未点击时 input tap 调用次数为零"）；
  两台真机交错的实机行为（观察身份含序列号，行为上换设备必然 observation 不一致 → 拒绝，
  但未在真机复现）；旋转/弹窗的真机触发（离线用"cat 返回另一棵树"等价模拟）；
  `tap_coords`/`swipe`/`key`/`type_text` 的观察绑定（大纲 D16 全量绑定留待真机阶段，
  本轮按 A12 范围只做 idx 点击——裸坐标/全局键的风险面不同，且全部 CONFIRM 人工把关）。
- **行为变化**：
  1. 模型点击前必须先 `android_screen_tree` 拿到 obs；不带 obs 的点击直接被拒；
  2. 审批等待期间屏幕变了（换页/弹窗/锁屏）→ **不点**，返回新观察——多花一次 dump
    （0.5-2s），换掉的是"点错元素"这类不可逆错误；
  3. 点击后工具直接返回新界面观察，模型不必再调 screen_tree 核对；
  4. 工作台「UI 树」面板显示观察身份行。
- **剩余限制（明确记录）**：
  1. 每次点击固定三次 dump（观察/复核/复检），真机上比旧实现慢 1-4s——刻意的取舍；
  2. 观察身份只看**树内容**：纯视觉变化（视频帧、动画、同结构同文案的换图）不会改变 obs，
     复核不能发现——这类场景归截图工作流（D15 产物），不归 UI 树；
  3. `uiautomator dump` 本身可能超时/被设备拒绝，复检失败按"状态未知"处理，不自动重试写动作。
- **过程记录**：
  1. 旧测试 `test_tap_on_fresh_restored_cache_skips_dump` 断言的正是被移除的优化——
     重写为 `test_tap_on_restored_cache_still_reverifies`，注释写明"该优化是 A12 根因"；
  2. 复检失败用例需要"点击之后 cat 才开始坏"的时序：FakeRunner 按子串匹配没有调用计数，
     写了个数 taps 的 `FlakyCatRunner` 子类——第一次 `input tap` 之后 cat 返回坏 XML，
     正好模拟"点完瞬间设备掉线"；
  3. adb 的 shell/exec-out 都是**单串命令元素**（`["-s", serial, "shell", "input tap 200 230"]`），
     子类里检测用 `startswith("input tap")`，第一次写成了拆开的 argv 元素匹配。

### 2026-10-28 · D15（产物存储与视觉输入：A13）

- **改了什么**：
  - 新增 `memory/artifacts.py`：产物统一登记（`state.db` 升 **v6**，`artifacts` 表：sha256/大小/宽高/
    来源/归属会话/run/过期时间）。字节由代码写入 `data/artifacts/<id>.<ext>`，消费方拿到的永远是
    id 不是路径——想读文件只能走 `ArtifactStore`，而它只认自己登记过的行；读回校验指纹，磁盘上
    被换过的产物判损坏；跨会话读取拒绝；过期清理只删登记在案的文件且**跳过活跃 run 仍引用着的**；
    总量超上限拒绝新保存（老的还在）；配额报错里让用户去产物面板清理。
  - 新增 `core/vision.py`：产物字节 → 模型图像块。长边超 `max_edge_px` 先缩放、重编码 JPEG q88
    （截图场景 PNG 无意义）、超 `max_image_bytes` 不给；坐标换算说明放文本里（模型报坐标按原图口径）。
  - 新增 `tools/shots.py`：browser/android 截图共用桥——字节 → 登记 → 工具结果文本。
    文本带 `[artifact:<id>]` 标记；未开 `vision.send_to_cloud` 时明确写"模型看不到这张图"，
    不让模型误以为看过（A13 的另一半）。
  - `core/agent.py`：本批工具结果里的标记 → 图像块（`_attach_images`），门禁顺序为
    隐私开关 → vision 路由/能力 → 归属/指纹/过期 → 单回合附图数与单图大小，任一没过**如实降级**
    （标记留文本，附"未附加：原因"）；带图消息自动切 `models.vision`（chat 自带视觉也行，
    都没有就不发——供应商对带图请求的失败方式不可预测，"默默丢图继续答"最危险）；
    回合结束 `_sweep_artifacts()` 顺手清过期产物（活跃 run 引用的不删）。
  - `config.py` 新增 `vision`（`send_to_cloud` 默认**关**：纯本地存图 ≠ 云端上传许可、
    `max_image_bytes`/`max_images_per_turn`/`max_edge_px`）与 `artifacts`（保留天数/总量上限）两段；
    `RuntimePaths` 增加 `artifacts`。
  - `bootstrap`：构造 `ArtifactStore` 并注入 browser/android 工具与 Agent；`can_view` 只在
    "隐私开关开 且 有视觉模型可用"时为真。
  - `core/compress.py`：多部件消息按文本转写进压缩体量（base64 绝不进压缩 prompt）；
    `core/run.py`：`ActiveRuns.snapshot()`（清理保引用用）；`system/backup.py`：清单补记 `artifacts`。
  - 工作台：`GET /api/artifacts`（列表）、`GET /api/artifacts/{id}/image`（原图字节，缺失 404/
    过期 410/指纹不符 409）、`DELETE /api/artifacts/{id}`（手动清理）；右侧新增「产物」页签
    （缩略图网格 + 大图弹窗 + 删除）；聊天正文里的 `[artifact:id]` 渲染成内联图。
  - 顺带（前端微调，与 D15 同文件）：消息悬浮复制按钮、发送等待动画。
- **关闭了什么**：A13（截图只回路径、模型收不到图、路径即任意读后门）。
- **跑了什么**：`uv run pytest tests/test_artifacts.py -q` → **13 passed**；
  `uv run pytest tests/test_web.py -k artifact -q` → 3 passed；`uv run pytest -q` → **605 passed**（2026-10-28）。
- **真机证据**：真实 headless Chromium 冒烟——`browser_screenshot` 拿到 1280x800 真实 PNG
  （4714B），登记为 `[artifact:3cbe0a4e…]`；`get/read_bytes` 指纹校验通过；`list_all` 1 条；
  手动删除后文件与行都消失；浏览器正常关闭。产物截图归属会话为空（冒烟不在聊天回合内，符合设计）。
- **没测什么**：真实安卓设备截图（待 D16 真机）；真实视觉模型"看图答话"的效果（需用户开
  `vision.send_to_cloud` 配真实模型）；JPEG 体积与供应商收图上限的组合（4MB 默认值来自
  OpenAI 兼容接口的通行上限，未逐一验证各家）；GIF 动图（直接拒绝，未找真实动图样本）。
- **行为变化**：
  1. 截图不再落 `data/browser_shots/latest.png`（互相覆盖、无保留期），产物带 id、7 天保留、
     总量 500MB 上限；
  2. 默认配置下模型**仍看不到图**（隐私闸门关）——想让它看需在 config.yaml 开
     `vision.send_to_cloud` 且模型声明 vision 能力；
  3. 工作台多一个「产物」页签；聊天里模型提到产物标记会显示成内联图。
- **剩余限制（明确记录）**：
  1. `vision.send_to_cloud` 是全局开关，没有按应用/区域的细粒度选择（大纲 D15 的"按应用/区域"
     留待真机使用反馈后再细化）；
  2. 图像块以 user 消息附加在 tool 结果之后——OpenAI 兼容接口的最稳位置，个别严格供应商
    若拒绝该形态，表现为该回合模型报错（如实降级路径已有，不会假装成功）；
  3. 清理只在回合结束触发（CLI/心跳不产生图像时，过期产物会在下次 Web 回合或重启时清）；
  4. 手动删除产物后，聊天历史里的旧消息引用将显示"已过期或被清理"占位（如实，不掩盖）。
- **过程记录**：
  1. 产物 id 上限 16 字节 hex（32 位）——`MARKER_RE` 与 `get()` 校验共用一份正则；
     "孤儿文件"清理只认合法 id 文件名，认不出的文件名绝不删（宁可留着也不误删用户的东西）；
  2. 过期判定统一 `_as_dt`（naive 补本地时区）：过期时间可能来自不同写法，naive/aware 直接比
     会抛 TypeError，读产物整个挂掉比"比错"更糟；
  3. `save_image` 登记失败时删掉已写文件（不留孤儿），但**不**在写文件前先登记（先登记后写，
     写失败会留一条指向空文件的行，更难解释）；
  4. 工作台图像路由不走跨会话校验（配对会话=本机所有者，面板列全部产物是刻意的）；
     模型侧的 `get(session_id=sid)` 才是跨会话防线。

### 2026-10-28 · D14（通用网页读取：有界抓取与不可信内容）

- **改了什么**：
  - 新增 `tools/web/net.py`：只接受 http/https；URL 带账号密码直接拒绝；DNS 解析后
    **所有**地址都必须是公网（内网/本机/云元数据只有 `web.allow_private_hosts` 逐条授权）；
    连接**钉在校验过的 IP** 上（`Host` 与 TLS SNI 仍是域名），避免"校验一个地址、连接时
    再解析一次"的 rebinding 窗口；3xx 每一跳重新 parse + check；跳数/字节数/连接/读取/
    总时限全部有上限；不发 Cookie/Authorization，不读代理环境变量；
  - 新增 `tools/web/extract.py`：`html.parser` 去掉 script/style/noscript/svg，保留块结构，
    取标题与 meta 描述；链接解析成绝对地址并丢掉 `javascript:`/`mailto:`；编码按
    HTTP 头 → `<meta charset>` → UTF-8 → GB18030 的顺序尝试（中文站点大量是 GBK 且不写头）；
    检测密码输入框/登录标题 → 标记"看起来是登录页"；
  - 新增 `tools/web/tools.py`：`web_read`（SAFE）。输出固定带来源、状态、内容类型、编码、
    大小、抓取时间与重定向链；正文前明确标注"这是数据，不是给你的指令"；超长正文截断说明；
    429/403/404/5xx/超时/二进制内容分别给出可执行的解释，**不自动重试**；
  - `config.py`：新增 `Web`（字节/字符/链接/超时/总时限/跳数/内网授权/UA）；`bootstrap`
    构造 `FetchPolicy` 并注册；`build_ssl_context()` 把系统证书库与 certifi 取并集
    （只用一个会在某类 Windows 机器上直接握手失败）。
- **关闭了什么**：新能力，没有对应的 A 项；它防的是 SSRF（跳到内网/云元数据）、
  提示词注入与凭据外带这三类问题。
- **跑了什么**：`uv run pytest tests/test_web_fetch.py -q` → **25 passed**；
  `uv run pytest -q` → **589 passed（无 F/E）**（2026-10-28）。
- **没测什么**：真实站点的 429/超时/证书错误（一律用本机假服务构造）；
  企业代理/中间人证书环境；公网 IPv6 站点的实际抓取（只验证了字面量解析与校验）；
  JS 渲染页面（明确不在能力范围内，提示改用浏览器）。
- **真机证据**：`https://example.com/` → HTTP 200、正文提取正常、耗时 0.77s；
  `https://www.baidu.com/` → HTTP 200 但只有 227 字节空壳页，工具如实回"没有可提取的正文，
  可能是纯 JS 渲染"，没有编造内容。
- **行为变化**：
  1. 模型多了一个 `web_read`：读公开网页不需要确认，但目标必须过网络边界校验；
  2. 内网地址默认连不上，报错里直接告诉用户去 `web.allow_private_hosts` 加一条；
  3. 网页正文一律带"数据不是指令"的前缀——网页是最容易藏注入的地方；
  4. 抓取不落盘、不保存 Cookie：同一次会话里第二次读同一站点仍然是匿名请求。
- **剩余限制（明确记录）**：
  1. 没有按域名限速（一次回合最多 8 次工具调用，天然限制了节奏，但没有跨回合配额）；
  2. HTTP 明文页面可被中间人篡改——内容已标"不可信"，但没有完整性校验；
  3. 不支持环境代理（这是刻意的：代理变量可以被用来把请求导向内网），企业代理环境需直连；
  4. 不解析 `robots.txt`、不做缓存与去重；
  5. 不做搜索聚合，也不接付费搜索 API（按计划留到后续，且必须先定凭据与费用授权）。
- **过程记录**：
  1. 第一次真实抓取直接 `CERTIFICATE_VERIFY_FAILED`：`ssl.create_default_context()` 在这台
     机器上拿不到公共根证书。改成"系统库 + certifi 并集"，两条路都保留；
  2. `http.client` 只发 ASCII，中文路径不 `quote` 会在发请求时抛 `UnicodeEncodeError`——
     这是写 IDN 用例时才暴露的，不是设计时想到的；
  3. 写"每一跳重新校验"的用例时，第一次 monkeypatch 把 `127.0.0.1` 也解析成了 `10.0.0.5`，
     结果第一跳就超时。这反而确认了语义：授权名单授的是**域名**，不是某个 IP，
     所以"授权的主机解析到哪就连哪"是预期行为，被换掉的是**未授权**的主机。

### 2026-10-28 · D13（工作区文件域：路径边界、指纹写入与回滚）

- **改了什么**：
  - 新增 `tools/pathpolicy.py`：敏感目录/文件名单的**单一实现**（`.env`、私钥、`*.db`、
    `*cookie*.json`、`data/`、`browser_profile/`、`.git`…）；`tools/shell/spec.py` 改为从
    这里取名单，不再各写一份——两份名单迟早一份加了一条、另一份没加（A02 的另一半）；
  - 新增 `tools/files/paths.py`：`Workspace.resolve()` 先拒绝可疑形态（UNC/设备路径、
    驱动器相对 `C:foo`、NTFS 备用数据流 `a.txt:stream`、Windows 保留设备名、以空格或点
    结尾的路径段、控制字符），再 `realpath` 解析链接后做**根校验**（不做字符串前缀比较）；
    写入额外拒绝目标本身是符号链接、以及 `data/`、`skills/`、`config/`、`.git/`；
    允许的根只来自配置，工具入参里没有"根"这个字段；
  - 新增 `tools/files/io.py`：编码识别（UTF-8 / UTF-8+BOM / GB18030）、保留原换行风格、
    前 8KB 有 NUL 判为二进制并拒绝、按行窗口读取（带总行数与行号）、≤4MB 文件给出
    **原始字节**的 sha256 指纹、`fstat(fd)` 与 `stat(path)` 比对文件身份（检查后不变）；
  - 新增 `tools/files/search.py`：广度优先有界遍历；命中上限/文件数上限/时间上限三重约束；
    敏感文件在打开前跳过（不回显名字与片段）、二进制与超大文件跳过、不跟随符号链接；
  - 新增 `tools/files/history.py`：按路径哈希分目录存快照（允许中文文件名），保留最近 20 版，
    单版上限 4MB；超过上限直接拒绝写入（"改了但回不去"比"不让改"更糟）；
  - 新增 7 个工具：`files_list / files_search / files_read / files_diff`（SAFE）与
    `files_write / files_history / files_restore`（后两者中的写操作 CONFIRM +
    `requires_human`）。写入是 `expected_hash` 短前缀 CAS，读→比对→写在**同一把跨进程
    文件锁**内完成，先备份再 `os.replace`，写完回读比对指纹；
  - `config.py`：新增 `Files`（根/额外根/额外禁区/四类上限）与 `RuntimePaths.file_history`；
    `bootstrap` 构造 `Workspace` + `FileHistory` 并注册；`Runtime` 暴露 `workspace`、
    `file_history`；
  - `core/agent.py`：群聊作用域禁用全部 `files_*`（源码、笔记、配置都可能含个人信息）；
    `safety/gates.WebApprover` 对超长确认参数截断到 2 万字符并注明（2MB 内容塞进确认框，
    人只会点"同意"）。
- **关闭了什么**：A02 的文件域部分（命令面在 D01 已关闭）。
- **跑了什么**：`uv run pytest tests/test_files.py -q` → **29 passed**；
  `uv run pytest -q` → **564 passed, 1 warning**（2026-10-28；本机收尾清理临时目录被宿主
  安全钩子拦下，进程退出码为 1，与用例结果无关）。
- **没测什么**：真实编辑器与工具同时改同一文件（CAS 只在写入瞬间比对，无文件监听）；
  真实云盘/杀软在 `os.replace` 与回读之间插手的场景（用替换写入函数的用例模拟了回读不一致）；
  网络盘（SMB）上的锁语义；>4MB 文件的编辑需求（当前直接拒绝）。
- **真机证据**：对真实仓库做**只读**冒烟——`files_list src/omni` 正常（缓存目录已跳过）、
  `files_read src/omni/config.py` 显示 339 行与前 3 行 + 指纹、`.env` 与 `data/state.db`
  被拒并说明原因、`C:/Windows/win.ini` 越根被拒、`files_search` 命中
  `src/omni/memory/guard.py:43`；整个过程未创建 `data/file_history`（只读不写盘）。
- **行为变化**：
  1. 模型现在有一组结构化文件工具，读操作不需要确认，写操作一定弹确认；
  2. 群聊里完全看不到工作区文件（与个人记忆工具同等处理）；
  3. `data/`、`skills/`、`config/` 不能用文本工具改——它们各自的写入通道带额度/版本/校验；
  4. 搜索是广度优先：同样的文件数上限下，先看到浅层文件，而不是一头扎进某个大目录。
- **剩余限制（明确记录）**：
  1. 没有文件监听：并发修改只能靠写入瞬间的指纹比对发现，不阻止"读完之后别人改、我再改"；
  2. `expected_hash` 允许 8 位十六进制前缀（约 32 位），这是**防误覆盖**而不是防篡改——
     真正的权限边界是"写要人工确认 + 路径在根内"；
  3. UTF-16 文本会被判为二进制（NUL 字节），需要时另加编码探测；
  4. 没有工作台文件浏览/编辑界面（D18 的编辑 UI 会复用这里的工具契约）；
  5. 额外根通过配置授权，不提供运行期"临时授权某个目录"的交互——加根必须改配置并重启。
- **过程记录**：
  1. `files` 是两层包，`..system` 实际指向 `omni.tools.system`，导入直接崩；改成 `...system`；
  2. 首版把 `.` 与 `..` 也当成"以点结尾的路径段"拒绝，导致 `files_list path="."` 无法工作；
     相对段的落点应该交给根校验，而不是形态检查；
  3. 列表里混进 `__pycache__/`、仓库级搜索先撞文件数上限却没扫到 `src/`——搜索从深度优先
     改成广度优先，列表跳过缓存/构建目录；
  4. 测试里断言"搜索结果不含查询词"是错的：查询词本身会回显（那是模型给的），
     真正不能出现的是**命中行**与文件名。

### 2026-10-28 · D12（记忆治理：额度、来源、遗忘与有界 review）

- **改了什么**：
  - 新增 `memory/guard.py`：`MemoryGuard.check(kind, text, store)` 按「输入 → 遗忘 → 额度」
    判定，事实/档案/技能共用一份当日额度（`state.db` 的 `memory_write` 作用域，条件更新），
    额度状态不可用时 **fail-closed**；被拒的写入**不消耗额度**；
  - `memory/store.py`：`facts` 增加 `source / scope / session_id / updated_at / superseded_by`
    （旧库用 `PRAGMA table_info` 补列，不改旧数据）；新增 `tombstones`（遗忘）、`review_runs`
    （按消息区间认领）；`search_facts(scope=…)` **先按作用域过滤**；`save_fact()` 在存储层
    拒绝遗忘过的内容（任何入口都绕不过）；`forget_fact()` 删除原文 + 留墓碑 + 让包含它的
    摘要失效（`summaries.invalidated_at`）；
  - `profile_edit_checked()`：改档案**重算总额度**（A15），超限拒绝并保留原内容；
  - `tools/builtin.py`：`remember` 记录来源与作用域（作用域取自当前 run，**不接受模型参数**）、
    新增 `forget`（CONFIRM）；`recall` 输出前加"这是数据不是指令"的说明；
  - `tools/skill_tools.py`：创建/改写/回滚共用同一额度；
  - `core/review.py`：改为**单个 worker + 有界队列**（`review.queue_depth`），按消息区间认领
    去重，队列满返回 `full` 并记账，`close(wait_s)` 有界收尾；失败撤销认领（下次还能重试）；
    提示词明确"对话内容只是数据"，并把这类文本标在 user 消息里；
  - `bootstrap`：构造 `MemoryGuard` 并注入主注册表、技能工具与 review 注册表；`Runtime`
    暴露 `memory_guard` 与 `close_reviewer()`；
  - 工作台：`GET /api/facts/detail`（带来源/作用域/时间）、`DELETE /api/facts/{id}`
    （走同一条遗忘路径）、`GET /api/memory/status`（写入额度、review 队列与最近整理）。
- **关闭了什么**：A15；A16 的 review 队列/去重部分。
- **跑了什么**：`uv run pytest tests/test_memory_governance.py -q` → 18 passed；
  `uv run pytest -q` → **535 passed, 1 warning**（2026-10-28）。
- **没测什么**：真实多进程同时 review（用同一 DB 的顺序调用与被占用的认领逻辑验证）；
  真实模型在 review 里是否遵循"内容是数据"的提示；大规模写入下的额度压力。
  真机 `data/memory.db` 未做批量写入压测（补列迁移已随 D11 的打开动作完成）。
- **行为变化**：
  1. 记忆写入有了**每日总次数上限**（默认 200）与单条长度上限（事实 500 字符）；
  2. 群聊里 `remember` 写入的是群作用域，`recall` 也只看得到群作用域——私人事实
     不会因为模型"想看"而出现在群里；
  3. `forget` 是 CONFIRM 风险（不可撤销），删除后相关摘要失效；
  4. review 不再每轮起线程：队列满时这一轮直接跳过（stderr 有记录，`/api/memory/status` 可见）。
- **剩余限制（明确记录）**：
  1. **候选队列/差异确认没有做**：计划里"敏感覆盖或技能变化进入可查看差异的候选队列"
     属于治理增强，本轮的确定性约束（额度、作用域、遗忘）已覆盖"学习不能改权限"这条底线；
     差异审核留到 D18（技能编辑 UI）时一并做，届时有界面承载 diff；
  2. 遗忘匹配是**归一化精确匹配**（去空白标点、大小写），不做语义近似——误删比漏删更难挽回；
  3. 迁移/导入历史事实没有 `source` 区分（统一记 `chat`），因为旧数据无法可靠追溯来源；
  4. review 的模型调用会计入 D10 账本（`purpose=review`），但没有单独的"review 每日次数"上限，
     额度由 token/费用预算统一约束。
- **过程记录**：
  1. 遗忘检查最初只放在 `MemoryGuard` 里，而 `guard=None`（不限额的嵌入式用法）时整段跳过，
     测试立刻抓到"删了还能记回来"。改成存储层兜底 + 闸门预检两道；
  2. review 失败时必须**撤销认领**，否则这段消息永远"被整理过"，再也不会重试——
     这条是在写 `test_failed_review_releases_the_claim` 时才补上的；
  3. 测试里假模型第一轮调用工具、第二轮必须能看到工具结果，否则 `outcome` 永远是
     "无需变更"，断言看起来通过但什么都没验证。假网关改成回显最后一条 tool 消息。

### 2026-10-28 · D11（持久摘要与索引生命周期）

- **改了什么**：
  - `memory/store.py`：`memory.db` 增加 `summaries`（`session_id / from_msg_id / to_msg_id /
    content / source / model / version / created_at / superseded_by`，
    `UNIQUE(session_id, to_msg_id)`）与 `meta` 表；
  - FTS 生命周期重写：索引改由**触发器**增量维护（`facts_ai/ad/au`、`messages_ai/ad/au`），
    `meta.fts` 记 `version/tokenizer/triggers/built_at`；只有「首次建表 / 分词器升级 /
    旧库无状态记录」才 `rebuild` 一次——此前**每开一个连接**就重建全库（review 每轮一次、
    心跳每周期一次、工作台每请求一个连接）；
  - 关闭压缩时索引不再被反复重建；`fts_rebuilt / fts_reason / fts_tokenizer` 作为可观测字段暴露；
  - 新增 `recent_messages_ex()`（带消息 id）、`message_count()`、`active_summary()`、
    `save_summary()`（同一区间幂等）、`summary_history()`；
  - `core/compress.py` 新增 `assemble_context()`：先取当前有效摘要，再取**未被摘要覆盖**的消息，
    超阈值时只把更老的一段折进摘要，并把摘要连同区间落库；摘要失败原样返回、不写半截摘要；
  - `core/agent.py`：回合开始时走 `_assemble_body()`（摘要 + 未覆盖消息），
    产生新摘要时记 `context_summary` 事件；`compression.enabled=false` 时不再产生新摘要，
    但**已有摘要仍进上下文**（否则等于静默丢历史）；
  - `system/backup.py`：清单补记 `summaries` 行数。
- **关闭了什么**：A16 的索引部分（review 队列部分仍属 D12）。
- **跑了什么**：`uv run pytest tests/test_context.py -q` → 13 passed；
  `uv run pytest -q` → **514 passed, 1 warning**。
- **真机证据**：`data/memory.db` 首次打开按 `legacy` 一次性重建（0.04s，16 个会话保持），
  第二次打开 `fts_rebuilt=False`（0.02s）；`summaries` 表建好、为空（还没触发过压缩）。
- **没测什么**：真实长对话（上千条消息）的压缩质量与摘要成本；trigram 不可用的机器
  （只有 `unicode61` 路径的降级行为用代码分支覆盖，没有真机环境）；多进程同时首次升级
  （两个进程同时 `rebuild` 未演练，理论上后到者会重复重建一次）。
- **行为变化**：
  1. 历史消息的上下文不再每条都进 prompt：超出阈值后旧的一段被持久摘要替代，
     `recent_messages` 的调用方（检索、工作台列表）不受影响；
  2. 摘要以 user 消息并入保留窗第一条用户消息，维持严格角色交替；
  3. `memory.db` 首次升级会做一次全量索引重建（一次性成本）。
- **剩余限制（明确记录）**：
  1. **中途压缩仍只在内存里**：一次回合内工具输出把上下文顶爆时，`compress_if_needed`
     仍做无 id 的临时压缩，不落库（回合结束即失效）。理由是回合内的工具消息本来就不落库，
     给它们编造 id 只会让"摘要覆盖了哪些消息"变成假账；
  2. 摘要只保留最近 N 条（默认 20），更老的摘要被取代后仍可查但不进上下文；
  3. 摘要质量取决于摘要模型；本层只保证"不篡改事实、不丢区间、失败可降级"；
  4. 没有做基于 token 的精确预算，`trigger_chars` 仍是字符阈值（与 D10 的 `estimate_tokens` 未统一）。
- **过程记录**：
  1. 触发器与手写 `INSERT INTO fts(rowid)` 会**双写**（索引出现重复行）。改成"能建触发器就不手写"，
     老 SQLite 建不了触发器时才回退手写；
  2. 首次实现忘了 `summaries` 的 `UNIQUE(session_id, to_msg_id)` 会被重复压缩撞上，
     改成先查后插并返回 `created=False`——同一段历史重压一次不再花钱，也不会版本号乱跳；
  3. 测试里"2 字查询"最初的断言是「喝什么」应命中「用户喜欢喝拿铁」——这不是降级链的能力，
     是我想当然。改成断言真正存在的片段（拿铁/喜欢），并把不命中写成断言，避免以后有人
     为了让测试变绿去伪造检索结果。

### 2026-10-28 · D10（网关能力、流式、有界重试与费用预算）

- **改了什么**：
  - `config.py` 新增 `llm` 段：`request_timeout_s`、`max_output_tokens`（预留上界）、
    `retry`（次数 / 总时限 / 退避）、`budget`（日额度 / 单次运行额度）、
    `capabilities`（每模型 `tools`/`vision`/`stream`/`context_tokens`/单价）；
  - 新增 `llm/errors.py`：稳定错误码（auth/permission/rate_limit/timeout/connection/server/
    bad_request/context_length/unsupported_capability/budget_exhausted/price_unknown/
    cancelled/stream_interrupted/unknown）、按**异常类名 + HTTP 状态码**分类（不依赖 LiteLLM
    内部字段）、可重试判定、"可能已计费"标记、`sanitize()` 脱敏；
  - `llm/gateway.py` 重写：调用前能力校验（工具 / 视觉 / 上下文）→ 预留 → 调用 → 结算；
    结构化 timeout；有界重试（指数退避 + 总时限，`num_retries=0` 关掉 LiteLLM 自己的重试）；
    **重试永远打同一个 model**，不切换供应商；`Reply` 带 usage / 费用 / 尝试次数 / 不完整标记；
    新增 `stream()`（增量事件、工具调用分片按 index 累加、半途断连保留已收内容并保守记账）；
  - 新增 `llm/usage.py`：`Usage`（`reported` 标记）、`resolve_price`（配置 → LiteLLM 本地价格表 →
    本地免费 / 未知）、`estimate_tokens`、微美元整数换算；**导入 LiteLLM 前**
    设置 `LITELLM_LOCAL_MODEL_COST_MAP=True`；
  - 新增 `llm/budget.py`：`Quote` / `Reservation` / `Ledger` 协议 / `RunBudget`（父子共享
    **同一个实例**）/ `settle_amount`（有 usage 有单价=实付；有 usage 无单价=未知 NULL；
    无 usage 有预留=按预留保守保留）；
  - `memory/state.py` 升 **v5**：`llm_calls`（调用流水，`cost_micro` 可为 NULL）与
    `llm_budget`（当日 `reserved_micro` / `spent_micro`，整数微美元）；
  - 新增 `memory/usage.py`：`reserve`（`BEGIN IMMEDIATE` 里的条件更新，rowcount 即判据）/
    `settle`（跨天也不丢账）/ `record` / `recent` / `summary` / `prune`；
  - `core/run.py`：`RunContext.budget`、`TurnResult.cost_micro` 与 `budget`、`RunBudgetExceeded`；
  - `core/agent.py`：所有模型调用收口到 `_model_call()`（预算与取消的翻译只做一次）；
    压缩也带 run 与预算（不再绕开本轮上限）；`on_delta` 走流式；回合末记 `budget` 事件；
    预算拦截返回**可解释的 `status=failed`**（不是抛异常，也不是假装成功）；
  - 工作台：新增 `GET /api/llm/usage`（今日 / 按用途汇总 / 最近流水 / 模型能力），
    `POST /api/chat/stream`（SSE；浏览器断开即取消本会话运行）；`/api/chat`、`/api/runs`
    返回 `cost_micro` 与 `budget`；模型测试接口按错误码映射 HTTP 状态；
  - `system/backup.py`：清单补记 `llm_calls` / `llm_budget` 行数。
- **关闭了什么**：A20（token / 费用硬预算、流式 usage）。
- **跑了什么**：`uv run pytest tests/test_gateway.py tests/test_usage.py -q` → 54 passed；
  `uv run pytest -q` → **501 passed, 1 warning**（2026-10-28，基线 439）。
- **真机证据**：`data/state.db` 原本停在 v2（D06 之后没再启动过服务），本次逐级迁到 **v5**
  （v2→v3→v4→v5）；迁移前已备份到 `data/backups/pre-d10`（4 项，state.db sha256 `9dac6ee2…`）；
  迁移后 `health().degraded` 为空，`llm_calls`/`llm_budget` 建表成功；自检写入的演示流水已删除
  （两表 0 行），没有把测试数据混进真账。
- **没测什么**：真实供应商的 429 / 超时 / 认证失败（全部用假异常与假 `_completion`）；
  真实流式的首字时延与浏览器断连（TestClient 一次收完整个流，断连分支未走真实 socket）；
  跨进程同时抢额度的真实双进程演练（用同一 DB 的两个连接验证了条件更新）；
  单价快照与供应商真实账单是否一致（未对账）。
- **行为变化**：
  1. 带图片的请求若模型没声明 `vision`，**调用前**就拒绝（以前是把图片丢掉照常提问）；
  2. 未配置 `llm.budget` 时只记账不拦截，接口与事件里显示"未设上限"，不是"上限为 0"；
  3. 配了上限后超额会在**发请求前**被拦，界面得到的是解释而不是异常堆栈；
  4. `/api/chat` 响应多了 `cost_micro` / `budget`；模型测试接口的错误响应体变成
     `{code, message, hint}` 且状态码按错误类型区分。
- **剩余限制（明确记录）**：
  1. **默认不设上限**是现状延续（避免升级后突然中断用户正在做的事），代价是"忘记配置"时
     没有硬约束；这是取舍，不是已完成的安全能力；
  2. 单价来自 LiteLLM 随包快照，可能与供应商最新价目不同；配了上限时建议在
     `llm.capabilities` 里写死单价；
  3. 预留按 `max_output_tokens` 上界估算，实际用量小的时候只是**暂时占用**，结算即退回；
     极端并发下可能短暂拒绝；
  4. 供应商没报 usage 时按预留保守记账，账面会略高于真实花费（宁高不低）；
  5. 压缩 / review / 委派已计入同一账本，embedding 尚未接入（D24 时按同一账本接）；
  6. `stream()` 的用量只在供应商支持 `include_usage` 时才有；否则落"保守保留"。
- **过程记录**：
  1. LiteLLM 在 **import 期**就去 GitHub 拉 `model_prices_and_context_window.json`（失败还重试
     2 次，实测多花 2–3 秒）。离线测试因此会联网，改在导入前设 `LITELLM_LOCAL_MODEL_COST_MAP=True`。
  2. `cost_per_token` 对 ollama 返回 `(0, 0)`、对未知 provider 直接抛异常。把 0 一律当"免费"
     会把未知报价记成零费用，所以按 provider 区分 `local_free` 与 `unknown`。
  3. `num_retries` 最初只写在 `_completion` 内部：测试替换该函数后观察不到，且 LiteLLM 自己的
     3 次重试会和我们的 3 次叠成 9 次。移到调用点后既可断言也真正生效。
  4. `class Foo(Exception): __name__ = "Timeout"` 造异常无效（`type(x).__name__` 读的是类名，
     不是类属性），测试改用 `type("Timeout", (Exception,), {})`。
  5. **heredoc 再次把 `"persona:\n  name: x"` 写成 `"persona:/n  name: x"`**，YAML 报
     `mapping values are not allowed here`。含转义的文本一律用编辑工具写，不用 heredoc
     （这是第二次踩，已升级为硬规则）。

### 2026-10-27 · D09（运行、取消、ToolResult 与委派）

- **改了什么**：
  - `state.db` 升 **v4**：新增 `runs`（run_id/parent/session/channel/principal/owner/status/phase/
    时间戳/error_code/last_seq）与 `run_events`（`(run_id, seq)` 主键）；
  - 新增 `memory/runs.py`：`create / append_event（同一写事务里 last_seq+1）/ touch / finish /
    list_recent / events(after=…) / interrupt_stale(owner_kind, current_owner)`；
  - 新增 `core/run.py`：`RunIdentity`、`CancelToken`（父取消传染子）、`RunContext`（步数记账、
    事件、阶段、check）、`ActiveRuns`（本进程的取消入口）、`TurnResult`；
  - 新增 `tools/result.py`：`ToolResult`（status/error_code/retryable/side_effect_state/artifacts/
    verification）+ `from_legacy()`（旧字符串按前缀适配并标 `legacy=True`）；
  - `tools/registry.py`：新增 `execute() -> ToolResult`，`dispatch()` 改为 `execute().to_message()`
    （旧字符串逐字不变）；审批器签名用「所属类+方法名」探测是否支持 `cancel`；
  - `safety/gates.py` / `safety/broker.py`：审批链支持取消——等待期间轮询取消信号，
    取消即撤销待批准项（界面上不能再点「同意」）；
  - `core/agent.py`：新增 `run_turn()`（返回 `TurnResult`，`chat()` 退化为取 `.answer`）；
    检查点为「模型调用前 / 模型返回后 / 每次工具调用前」；工具事件记录结构化结果；
    取消时写一条「已取消」历史，不假装有答案；
  - `tools/delegate_tools.py`：子 run 记 `parent_run_id`、步数 `min(父剩余, 配置上限)`、
    父取消传染、作用域继承（群聊委派拿不到私人上下文）；
  - `bootstrap`：注入 `RunStore`/`ActiveRuns`/`RunRef`/`owner`；启动时 `interrupt_stale`；
    入口标注 `owner_kind`（web/qq/heartbeat/cli）；
  - 工作台新增 `POST /api/runs`、`GET /api/runs`、`GET /api/runs/{id}`、
    `GET /api/runs/{id}/events?after=`、`POST /api/runs/{id}/cancel`；`/api/chat` 响应带 `run_id`。
- **关闭了什么**：A19（副作用未知的表达）、A20 的账本/取消/ToolResult 部分。
- **跑了什么**：`uv run pytest tests/test_runs.py -q` → 18 passed；
  `uv run pytest -q` → **439 passed, 1 warning**。
- **没测什么**：真实网关下的取消时延（假网关是瞬时返回）；跨进程取消（设计上返回 409，未做真实双进程演练）；
  token/费用预算与流式 usage（D10）。
- **行为变化**：`/api/chat` 响应多了 `run_id` 与 `status`；取消是协作式的，接口返回「已请求取消」，
  真正停下要看 `GET /api/runs/{id}` 的终态。
- **过程记录**：
  1. 审批器签名探测最初用 `type(func)` 做缓存键——绑定方法的类型恒为 `method`，
     于是一个支持 `cancel` 的审批器会让**所有**审批器都被按「支持 cancel」调用，
     旧审批器直接 TypeError → 被判成「审批失败」。改成按 `(所属类, 方法名)` 缓存。
  2. 改写 `chat()` 时漏掉了 `session_ref` 绑定（只在 `run_turn` 里绑了 run_ref），
     被 `test_tool_calls_stay_in_the_request_session` 抓到：工具写到了别的会话。
     现在两者都在同一个 `ExitStack` 里绑定/恢复。

### 2026-10-27 · D08（技能历史、CAS 与常驻文件一致性）

- **改了什么**：
  - 新增 `system/atomic.py`：同目录唯一临时名 + `fsync` + `os.replace`，失败删临时文件且不碰原文件
    （旧各处固定 `.tmp` 名字，两个进程同时写会互相覆盖）；`system/config_io.safe_write` 也改走它；
  - 新增 `system/filelock.py`：Windows `msvcrt.locking` / POSIX `flock`，进程退出锁自动释放，
    超时把持有者写进错误消息；Windows 并发首次创建锁文件偶发 EACCES 时短暂重试；
  - 新增 `memory/revisions.py`：按 key 分目录的修订快照（`data/history/skills/<name>/<rev>-<sha8>.json`、
    `data/history/resident/MEMORY.md/…`），每条含作者/来源/时间/摘要/内容/版号/附件清单；按内容去重、保留最近 50 条；
  - `memory/skills.py` 重写写路径：`_locked()` 跨进程互斥覆盖整段读改写；`read()`/`view()` 输出内容指纹；
    `patch/rewrite/rollback` 支持 `expected_hash`（≥8 位前缀），缺省时用本进程上次 view 的指纹；
    写入前先把磁盘现状（可能是手工编辑）记入历史；落盘失败返回「未修改」；
    `rollback()` 取旧修订重新过 linter 后作为**新修订**写入，版号继续加；
    每个修订记录 `references/` 的 {路径: sha256}，`reference_drift()` 报附件漂移；
  - `memory/resident.py`：同样加跨进程锁 + 修订历史；按编号的 `replace/remove` 用 `_seen` 做 CAS
    （先记基线再读，否则读操作会刷新基线导致永远相等）；读不出文件时**拒绝写**（不再当空文件覆盖）；
  - 技能工具（`tools/skill_tools.py`）：`skill_view` 输出指纹，`skill_patch`/`skill_rewrite`/`skill_rollback` 接受 `expected_hash`，
    新增 `skill_history` / `skill_rollback` 两个 SAFE 工具；
  - `RuntimePaths` 增加 `resident`/`history`，`bootstrap` 注入；工作台写常驻记忆时来源记 `user/workbench`。
- **关闭了什么**：A14（技能原子性与历史）、A09 在文件类记忆上的残留。
- **跑了什么**：`uv run pytest tests/test_skills_history.py -q` → 18 passed（连跑 3 次稳定）；
  `uv run pytest -q` → **421 passed, 1 warning**。
- **没测什么**：真实 `skills/` 库的写入（测试一律临时根）；工作台/UI 的差异展示与回滚按钮（D18）；
  网络文件系统上的 flock 语义（不在范围）。
- **过程记录**：
  1. 第一版 `view()` 用 `read_text` 算指纹，而写路径用 `read_bytes`：Windows 上被记事本存成 CRLF 的
     技能会被永久误判「被改动过」。已统一为按字节算（`read_bytes`）。
  2. 起初 `_load_for_write` 先 `_read_entries()` 再比较 `_seen`，而 `_read_entries` 自己会刷新 `_seen`
     ——CAS 变成恒等。改成先取基线再读。
  3. 四个子进程并发 `add` 时出现过一次 `PermissionError: .MEMORY.md.lock`（Windows 并发首次创建锁文件
     的共享冲突），加有限重试；之后连跑多次未复现。

### 2026-10-27 · D07（浏览器单一所有者与 profile 租约）

- **改了什么**：
  - `tools/browser/driver.py` 重写：新增 `_OwnerThread`（专用线程 + 有界队列），
    启动/读/写/关闭全部经它执行；`_ensure_page()` 断言"当前线程就是所有者线程"（A11 回归会被立刻发现）；
  - 错误语义分三类：`BrowserBusy`（队列满/profile 被占）、`BrowserUnknown`（超时不等于取消）、
    `BrowserError`（一般失败）；`close(wait_s)` 有界等待，超时如实返回"可能仍在运行"；
  - `state.db` 升 **v3**：`session_leases` 合并进通用的 `resource_leases`（键 `session:<id>` /
    `browser_profile:<绝对路径>`），一套 `acquire/renew/release`；迁移在一个写事务里完成；
  - `core/session.py` 增加 `ResourceLeases`（driver 用的适配器，拿不到带持有者）；
  - `bootstrap` 给 `BrowserDriver` 注入租约与 owner；`Runtime.close_browser()`；工作台 `lifespan`
    退出时先关浏览器再关 MCP（有界 8s，超时打印残留提示）。
- **关闭了什么**：A11。
- **跑了什么**：`uv run pytest tests/test_browser_owner.py -q` → 8 passed（其中 `test_real_chromium_*`
  真的启动了本机 headless Chromium，多线程读全都成功且与启动同一线程）；
  `uv run pytest -q` → **403 passed, 1 warning**。
- **没测什么**：真实登录 profile（测试一律用临时 profile，符合"不使用真实登录 profile"的边界）；
  有头模式（headless 与有头在 Playwright 侧差异未单独验证）；B 站实站写流程属 D23。
- **顺带修掉的隐患**：迁移原来用 `executescript`（会先隐式 COMMIT），两个进程同时首次启动时
  "建表 + 搬数据 + 删旧表"可能被穿插。现在每一步迁移都在 `BEGIN IMMEDIATE` 里逐条执行，
  并重新检查版本；新增了 v2→v3 保数据用例。

### 2026-10-27 · D06（显式会话与有界队列）

- **改了什么**：
  - 新增 `core/session.py`：`SessionGate`（进程内按会话串行；等待队列上限可配，满了抛 `SessionBusy`；
    按 `(loop, session)` 分槽，测试里多个事件循环不互相污染）与 `SessionLeases`（`state.db` 跨进程租约，
    拿不到带持有者报错，`finally` 只释放自己的 token）；
  - `state.db` 升到 **v2**：新增 `session_leases` 表（`acquire/renew/release/session_lease`，
    过期按 TTL 自动可被接手）——这也是第一次真的走 `_MIGRATIONS` 的增量升级路径；
  - `/api/chat` 改成 `async`：会话 id 由请求固定，不再写 `agent.session_id`；回合包在
    `session_gate.turn(sid)` + `session_leases.hold(sid)` 里，阻塞的 `agent.chat` 走 `to_thread`；
    review 显式传 `sid`；排队满/被别的进程占着返回 **429**；
  - `get_or_create_binding` 改为 `BEGIN IMMEDIATE` + 唯一键（不再"先查后插"）；
  - `config.limits` 增加 `session_queue_depth`、`session_lease_ttl_s`；`Runtime` 增加
    `session_gate`/`session_leases`。
- **关闭了什么**：A06。
- **跑了什么**：`uv run pytest tests/test_sessions.py -q` → 14 passed；
  `uv run pytest -q` → **394 passed, 1 warning**。
- **没测什么**：真实浏览器两个标签页并发（自动用例是 ASGI 直连，不经过真实网络与前端）；
  QQ 侧跨进程租约（D22）；多机/多用户场景不在范围（本地单所有者）。
- **行为变化（前端需知）**：`/api/chat` 不再顺便把"当前会话"切到请求里的 `session_id`。
  需要切换请在会话列表里点选（`POST /api/sessions/{id}/select`），响应里仍带 `session_id`。
- **过程记录**：为让 `uv run` 能重建（用户已授权重启旧服务），先停掉了旧代码的 `omni-web`/`omni-qq`；
  之后所有测试回到普通 `uv run pytest`。真机 `data/state.db` 已实际走过 v1→v2 增量迁移
  （`schema_version=2`、`session_leases` 已建），不是只在测试里验过。
- **运维备注（交接必读）**：本会话结束前 `omni-web`/`omni-qq` 处于**停止**状态——从工具会话里
  拉起的分离进程会被回收，无法替用户常驻。请在终端自行启动：
  `uv run omni-web`（控制台会打印新的配对链接，旧的 15 分钟后失效）、`uv run omni-qq`
  （需 NapCat 在 3001 监听；本机 NapCat 当前未监听，启动后要扫码登录）。

### 2026-10-27 · D05（事务状态与可恢复迁移）

- **改了什么**：
  - 新增 `system/backup.py`（D25 提前子项）：`snapshot_sqlite` 用 `Connection.backup()` 取一致快照、
    对副本跑 `integrity_check`、记 sha256/字节数/关键表行数；`backup_runtime` 覆盖 `state.db`/`memory.db`/
    `config.yaml`/`MEMORY.md`/`skills/`，缺失项进 `skipped` 而不是假装备份；`restore_into` 只往新目录写、
    默认拒绝覆盖、恢复后再校验并比对校验和；CLI `python -m omni.system.backup`；
  - 新增 `memory/state.py`（`data/state.db`）：`meta.schema_version`（高于本程序版本直接拒绝打开）、
    `jobs`（含 revision）、`job_claims`、`executions`、`budgets(scope, day, used)`、`migrations`；
    提供 `claim_due()`（原子领取）、`consume_budget()`（条件更新）、`upsert_job(expected_revision=)`、
    `update_run_state()`（不动 revision）、`health()`；
  - `memory/jobs.py` 拆出模型与时间计算，`memory/cron.py` 退化为再导出（老导入路径不变）；
  - `JobStore`/`ExecutionLog`/`WriteBudget` 改为 `StateStore` 的薄门面；`bootstrap` 建一次 state 并在启动时
    执行 `migrate_legacy()`；`RuntimePaths` 增加 `state`；`Runtime` 增加 `state`/`close_state()`；
  - `core/cron.py` 的 tick 改走 `claim_due()`，执行完 `update_run_state()` 而不是整条 upsert；
  - 工作台 cron 接口在状态不可用时返回 503（新增 `GET /api/system/state` 自检）；心跳在 jobs 不可用时
    不启动调度器并打印原因。
- **关闭了什么**：A09（任务/账本/额度）、A10（损坏不再当空，额度 fail-closed）、A17 的领取部分。
- **跑了什么**：`uv run --no-sync pytest tests/test_state.py -q` → 21 passed（连跑 3 次稳定）；
  `uv run --no-sync pytest tests/test_backup.py -q` → 9 passed；
  `uv run --no-sync pytest -q` → **380 passed, 1 warning**。
- **没测什么**：真实 `data/` 的旧 JSON 迁移（常驻服务在跑旧代码，未在真机执行迁移）；
  跨盘符/junction 路径（D13）；`context_from` 绑定 run_id、投递 outbox（D17）。
- **过程记录**：
  1. 加 `omni-backup` 脚本入口失败——用户正在跑 `omni-qq`/`omni-web`，uv 重建会替换
     `Scripts/omni-*.exe` 而文件被占用。撤回脚本入口，改用 `python -m omni.system.backup`；
     因此本批测试用 `uv run --no-sync pytest`。
  2. 跨进程用例起初出现 `attempt to write a readonly database`：所有连接都退出时 SQLite 会删掉
     `-wal`/`-shm`，其余并发写入撞上 WAL 重建——这是测试造的假现场。改成父进程保持一个连接
     （模拟常驻服务），并在 `_begin()` 里只对 BEGIN 做有限重试（BEGIN 前没有任何语句执行过）。
  3. **顺手发现一处真实的测试泄漏**：`tests/test_resident.py` 的 `build_runtime` 只注入了
     `db_path`/`skills_root`，没有 `data_dir`——D05 之后它会直接在真机 `data/` 建 `state.db`
     （之前是往真 `data/` 写 cron JSON 的同源问题，只因为文件名叫 json 没被注意）。已补
     `data_dir` 与路径断言。真机上那次生成的 `data/state.db` 只含 3 条 `empty`/`missing`
     迁移记录，没有任何任务、账本或额度数据；未删除，下次启动会继续复用。

### 2026-10-27 · D04（全路径与外部依赖隔离）

- **改了什么**：
  - `config.RuntimePaths` 成为可写路径的唯一声明（data/config/cookies/skills/cache/android_shots/
    android_cache/browser_profile/browser_shots/run/logs/mcp_logs），`build_runtime` 造好后逐个注入；
  - 补齐遗漏：安卓截图（`AndroidDriver.shots_dir`）、B 站 Cookie（`BiliAPI(cookies_path=...)`）、
    Web 的配置读写与 Cookie 路由（改用 `runtime.paths`，不再引用模块级 `CONFIG_PATH`/`BILI_COOKIES_PATH`）；
    两个常驻入口的 pid 文件也改从 `runtime.paths.run` 取；
  - `ServiceManager` 的端口探测/终止/启动都改成可注入；**注入 `data_dir` 的隔离实例默认三件套全禁**
    （探测返回空、终止与启动抛 `RuntimeError`），避免测试把真机 NapCat 当残留杀掉；
  - `Runtime` 增加 `paths` 字段；测试改用 `runtime.paths` 断言，不再 monkeypatch 模块常量。
- **关闭了什么**：A08。
- **跑了什么**：`uv run pytest tests/test_paths.py -q` → 10 passed；
  `uv run pytest -q` → **349 passed, 1 warning**。
- **没测什么**：真机上其它盘符/junction 下的路径变体（D13 文件域会补）；ASR 临时音频仍走系统临时目录
  （自动清理、不属于持久化路径）。
- **过程记录**：写审计用例最初包含一次 `/api/chat` 调用，虽然测试进程不加载 `.env`（不会真的计费），
  但这属于"靠环境侥幸"，已替换为只读接口。后续 D26 会加"测试不得触达模型"的守卫。

### 2026-10-27 · D03（工作台与终端身份保护）

- **改了什么**：
  - 新增 `channels/web_auth.py`：配对码 → HttpOnly + SameSite=Strict 的 HMAC 签名会话
    （`sid.exp.sig`，密钥 `data/web_secret`）；15 分钟配对码、连续 5 次失败即更换并在控制台重打链接；
  - 新增 `GET /pair/<码>`：控制台打印这条链接，浏览器点开即完成配对并 303 跳回工作台——
    **前端零改动**（`index.html` 是另一项未提交的前端工作，本次一行未动）；
  - `channels/web.py` 加统一中间件：Host 必须是本地主机名（挡 DNS rebinding）→ 写方法校验
    Origin 与 `Sec-Fetch-Site`（挡 CSRF）→ 除 `/api/auth/*` 外一律要求会话；错误带机器码
    `host_rejected/csrf_rejected/auth_required`；
  - 终端 WS 在 `accept()` 之前校验 Host+Origin+会话，缺一即 `close(1008)`；
  - `/api/system/config` 不再回显 `qq.access_token`，改为 `access_token_configured` 布尔；
    保存接口三态：缺省/空串=保留、`null`=清除、非空=替换；含密钥字段的错误信息不回显值；
  - `_validate_bind_host`：非回环监听默认 `SystemExit`，需 `OMNI_WEB_ALLOW_REMOTE=1`；
  - `Runtime` 增加 `data_dir`（工作台密钥落 `data_dir`，测试用临时根）。
- **关闭了什么**：A07（身份、Origin/Host、CSRF、终端 WS、密钥回显、默认监听）。
- **跑了什么**：`uv run pytest tests/test_web_auth.py -q` → 31 passed；
  `uv run pytest -q` → **339 passed, 1 warning**。
- **没测什么**：真实浏览器发起的跨站尝试（用 TestClient 模拟 Origin/Sec-Fetch-Site，不是真浏览器）；
  真实 ConPTY 终端会话（只验了握手闸门矩阵，PTY 侧留待手动）；HTTPS/反代场景不在首版范围。
- **遗留（交给前端 WIP 的所有者）**：设置中心「QQ access_token」输入框现在会读到空值——
  这是刻意的（不回显明文）。空值保存 = 保留旧值，需要清除时请用 `null` 或专门的「清除」按钮。
- **下一任务**：D04（全路径与外部依赖隔离）。

### 2026-10-27 · D02（QQ/cron 身份与投递边界）

- **改了什么**：
  - `safety/gates.py` 新增 `private_target_allowed`：私聊/投递的**唯一**授权判定，空白名单 = 谁都不许；
    入站、`qq_send_message`、`cron_add`、cron 投递、Web `cron_create` 全部改走它（A03/A04 的根因是两处语义相反）；
  - `config.load_qq_policy()` + `Runtime.qq_policy()`：策略**读时**从配置源取，撤销白名单下一条消息就生效；
    解析失败 → 全空（fail-closed）；`build_runtime` 记住实际读取的 `config_path`，测试不再热读到真机配置；
  - `channels/qq.py` 重写路由：`dm:<qq>` / `group:<群号>` 分开绑定，旧键 `qq:<qq>` 只迁给私聊；
    群聊需白名单 + @ 双条件，未知事件类型与非法 ID 直接忽略；日志标出群号；
  - `core/agent.py`：`chat(personal_context=False)` 不注入档案/常驻笔记，并从**规格与派发两处**剔除
    `GROUP_DENIED_TOOLS`（模型硬编工具名也拿不到私人数据）；`ToolRegistry.specs/dispatch` 增加相应参数；
  - 测试改用临时 `config.yaml`（`test_web` 不再读真机 `config/config.yaml` 里的真实 QQ 白名单）。
- **关闭了什么**：A03（空白名单入站）、A04（空白名单投递 + 建任务校验）、A05（跨场景历史混用）。
- **跑了什么**：`uv run pytest tests/test_qq_scope.py tests/test_qq.py tests/test_cron.py tests/test_web.py -q` → 55 passed；
  `uv run pytest -q` → **308 passed, 1 warning**。
- **没测什么**：真实 QQ 收发与 NapCat 登录（缺外部前提）；D12/D22 的完整记忆作用域（当前是"关死"而非"按 scope 过滤"）。
- **下一任务**：D03（工作台与终端身份保护）。

### 2026-10-27 · D01（结构化 SAFE 命令与进程边界）

- **改了什么**：
  - 新增 `src/omni/tools/shell/`：`spec.py`（命令串 → 受控 argv；先拒 shell 元字符，再按"已知命令 + 有限参数"解析）、
    `process.py`（`shell=False` spawn、256 KiB/流有界读取、超时杀整棵进程树、SAFE 最小环境）、
    `danger.py`（硬黑名单原样迁入）、`tools.py`（工具契约与结果呈现）；
  - `tools/shell_tools.py` 退化为转发薄层（导入路径不变）；`bootstrap.py` 无需改动；
  - `dir` 改 Python 实现（遮蔽 `.env`/`*.db`/`cookies` 等条目，200 条上限）；`type`/`get-content` 整体移出 SAFE；
  - git 只做参数级校验：禁 `-c`/`-C`/`--exec-path`/`--upload-pack`/`--git-dir`/`--work-tree`、
    禁 `--ext-diff`/`--textconv`/`--no-index`/`--output`/`-O`、禁 `HEAD:.env` 这类带冒号的 rev；
    `branch` 只允许列举形态，`remote` 只允许 `-v`；
  - 超时话术区分"已确认终止进程树（可重试）"与"回收状态未知（不可自动重试，副作用未知）"。
- **关闭了什么**：A01（复合命令/管道/重定向/命令替换全部在解析阶段拒绝）、
  A02 的命令面（写参数与密钥路径）、A19（进程树回收 + 未知副作用话术）。
- **跑了什么**：`uv run pytest tests/test_shell_tools.py -q` → 52 passed；
  `uv run pytest -q` → **297 passed, 1 warning**（基线 246 + 8 复现 + 43 新增，含真实父子进程回收用例与真实 `git status` 用例）。
- **没测什么**：真实危险命令（不拿真机当靶子）；非 Windows 分支的 `killpg` 走查；`adb` 真机观察留待 D16。
- **下一任务**：D02（QQ/cron 身份与投递边界）。

### 2026-10-27 · D00（基线、证据、记录）

- **改了什么**：新增本文件与 `tests/test_r0_repro.py`；把 `docs/LOCAL_AGENT_PLAN.md`、`docs/DEEPSEEK_DEVELOPMENT_OUTLINE.md` 纳入版本库；HANDOFF 增加计划文档与状态表入口。
- **关闭了什么**：没有关闭任何阻断项（D00 只登记），但把 A01–A05 从"文档声称"升级为**可复现证据**。
- **跑了什么**：`uv run pytest -q` → 246 passed（改动前基线）；改动后 254 passed。
- **没测什么**：真实 QQ 收发、真实浏览器跨站、真机安卓、真实 MCP 服务——全部缺外部前提，保持待真实验收。
- **下一任务**：D01（结构化 SAFE 命令与进程边界）。

## 6 外部前提（缺它们不妨碍离线开发，但对应能力不能标记完成）

| 前提 | 用途 | 当前状态 |
| --- | --- | --- |
| QQ 登录（NapCat 扫码） | QQ 通道/北极星 A 实测 | 未提供 |
| B 站测试 Cookie | 字幕/音频流与 D23 写流程 | 未提供 |
| 安卓设备 + ADBKeyboard | D16 观察—动作—验证 | 未连接 |
| 受信 MCP 服务 | D19 生产联调 | 未提供 |
| 桌面目标应用 | D20/D21 桌面读写 | 用户已选记事本，首个真机闭环通过；第二应用待选 |
| 模型预算授权 | D10 付费推理上限 | 未提供（当前只用离线假模型跑测试） |

## 7 第一轮小结（D00–D04，2026-10-27）

### 7.1 关闭了什么

| 证据编号 | 问题 | 关闭方式 | 提交 |
| --- | --- | --- | --- |
| A01 | SAFE 前缀白名单可被复合命令穿过 | 结构化 argv + `shell=False`，先拒 shell 元字符 | `b17cdc1` |
| A02 | 只读命令的写参数与密钥文件 | git 参数级校验；`type`/`get-content` 移出 SAFE；路径限定工作区并遮蔽密钥 | `b17cdc1` |
| A19 | 超时只处理直接子进程 | 进程树回收 + "已确认回收/回收状态未知"话术 | `b17cdc1` |
| A03 | QQ 空白名单放行入站 | 统一判定 `private_target_allowed`，空白名单 = 谁都不许 | `42617ef` |
| A04 | QQ/cron 空白名单放行风险 | 已修复 | 统一空名单拒绝；a52f0ec 补投原子领取与 OneBot 回执；未知不重发 | D02、D17 | 代码关闭，真实掉线补投待验 |
| A05 | QQ 私聊与各群历史混合 | 已修复 | dm/group 分场景、群个人上下文与工具隔离，81893be 事件去重/撤回与租约 | D02、D06、D22 | 代码关闭，真实两群隔离待验 |
| A07 | 工作台无身份、密钥明文回显 | 配对 + HttpOnly 会话 + Host/Origin/CSRF + WS 握手鉴权 + 密钥三态 | `7742a61` |
| A08 | `data_dir` 隔离有遗漏 | `RuntimePaths` 统一声明 + 隔离实例不碰真机进程 | 见本次提交 |

测试从 246 → **349**（新增 103 条，其中安全负例 60+ 条；所有负例的判据是"假执行器调用次数为零"）。

### 7.2 仍未关闭（截至 D08）

A12（安卓旧 idx）、A13（截图只回路径）、A15/A16（review 治理与学习入口额度）、
A17（cron 跨进程领取/积压）、A18（pid 身份）、A20 的 token/费用预算部分（运行账本与 ToolResult 已在 D09 关闭）。

### 7.3 真实环境待验收（缺外部前提，不能算完成）

- QQ 真实收发与空白名单行为（需 NapCat 扫码登录）；
- 工作台在真实浏览器里的配对、跨站尝试、ConPTY 终端会话；
- 安卓真机截图/动作（需设备 + ADBKeyboard）；
- B 站 Cookie 生效与字幕/转写链路。

### 7.4 进入 D05–D08 的建议（D05 已按此执行）

1. ~~**D05 先做最小备份**~~：已完成（`system/backup.py`，见 §5 D05）。备份命令先于迁移可用，
   否则迁移失败就没有回头路；顺带把 `WriteBudget._load` 的"损坏当空"改成 fail-closed（A10）。
2. **D06 的会话路由**是 A06 的正解，也会让 D02 的 `personal_context` 变成"作用域策略"的一部分；
   建议先定 `SessionScope` 契约再动 Web/QQ/CLI。
3. **D07 浏览器所有权**独立性强，可与 D05/D06 并行设计，但必须先有 profile 跨进程租约协议。
4. ~~**D08 技能 CAS**~~：已完成（见 §5 D08）。与 D12 的 review 写入共享同一把锁语义，D08 就把
   `expected_hash + 历史快照` 做成仓储 API，D18 再做 UI。

## 2026-10-29 能力完善 C01

四个已复现问题已修复，精确提交源码在全新 venv 中 958 项全绿（188.30 秒），20 次真实隔离 ConPTY 开关验证通过；C02–C06 未完成。
工作区并行配对、模型密钥、配置和前端修改保留；没有操作真实手机或生产 MCP。

## 2026-10-29 能力完善 C02

独立页面 /terminal/：4 会话、单写者、配对身份归属、1MiB 回放、断开 TTL、Shell/目录校验、工作线程 I/O 与残留报告。
精确提交源码 964 项全绿（172.25 秒）；真实 Chromium 多标签/刷新/移动端，假 PTY 续读/越权/缓存缺口/空闲回收通过。
首次新测试使用 TestClient 默认 WS Host 被正确拒绝，已改成明确本机 WS URL，未放宽鉴权；C03–C06 仍未完成。
