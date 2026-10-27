# 开发状态记录

> 本文件是 R0–R7 的执行台账：谁在哪个 commit 关了哪个问题、证据是什么、还有哪些**真实环境**没验。
> 规则（来自 [开发大纲](DEEPSEEK_DEVELOPMENT_OUTLINE.md) §11）：
> 状态只能用 **待开始 / 开发中 / 待真实验收 / 完成 / 阻塞**；测试证据必须写命令、结果、日期和 HEAD；
> 不得把"测试跳过""仅 mock 通过""接口已存在"记成完成；历史失败不覆写成仿佛没发生。

## 1 基线（D00，2026-10-27）

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
| A02 | 只读名单含写子命令与敏感文件：`git branch <name>`、`git remote add`、`type .env` 均到执行器 | 复现→已修复（命令面） | `test_a02_*` 翻转 + 14 种 git 写参数/泄密参数负例 + 8 种敏感路径负例；`type`/`get-content` 已移出 SAFE | D01、D13 | 命令面已修复；文件域仍待 D13 |
| A03 | 私聊判断 `if cfg.allowed_users and ...`：空白名单 = 不限，任何人私聊都被回复 | 复现→已修复 | `test_a03_*` 翻转；`tests/test_qq_scope.py` 7 例（空白名单/名单外/正常私聊/撤销即时生效） | D02 | 已修复（真实 QQ 收发待验收） |
| A04 | `make_runtime_deliver` 用 `if allowed and ...`：空白名单 = 不限，cron 可发给任意 QQ；工作台建任务 `cron_create` 同款判断 | 复现→已修复 | `test_a04_*` 翻转；`test_cron.py::test_runtime_deliver_guards` 覆盖空白名单/撤销；`test_web.py` 覆盖建任务校验与撤销 | D02、D17 | 已修复；投递 outbox 与去重仍属 D17 |
| A05 | 私聊与各群共用 `get_or_create_binding("qq", str(user_id))`，历史跨场景混合 | 复现→已修复 | 绑定键改为 `dm:<qq>` / `group:<群号>`；`test_a05_*`；旧键只迁给私聊；群聊 `personal_context=False` 并有 `GROUP_DENIED_TOOLS` | D02、D06、D22 | 已修复（记忆作用域完整策略仍待 D12/D22） |
| A06 | Web `/api/chat` 先改共享 `runtime.agent.session_id` 再 `chat()`，响应与 review 也读共享值；并发请求会串话 | 源码 | `channels/web.py:210-220` | D06 | 源码发现 |
| A07 | 全套 API 无身份校验、无 Origin/Host 校验；`/api/terminal/ws` 直接 `accept()`；`/api/system/config` 回显 `qq.access_token` 明文 | 源码→已修复 | `tests/test_web_auth.py` 31 例：12 个敏感 GET 未配对即 401、写接口/服务/审批/WS 全拒、跨站 POST 无副作用、Host 伪造、过期/篡改会话、密钥不回显与三态修改、非回环监听默认拒绝 | D03 | 已修复（真实浏览器跨站尝试与 PTY 会话待手动验收） |
| A08 | `data_dir` 隔离有遗漏：安卓截图写全局 `DATA_DIR/android_shots`；Web 的 Cookie/配置路由用全局 `BILI_COOKIES_PATH`/`CONFIG_PATH`；`bootstrap` 构造 `BiliAPI()` 未注入 Cookie 路径（测试会读真机 Cookie） | 源码→已修复 | `config.RuntimePaths` 统一声明 12 个路径；`tests/test_paths.py` 10 例：路径派生、安卓截图、Cookie 读取、Web 写配置/Cookie、隔离实例不探测/不启停真机进程、真实 `data/` 目录 canary 全量快照不变 | D04 | 已修复（真机目录 canary 用 mtime 快照验证；跨盘符/junction 场景留待 D13 文件域） |
| A09 | 共享 JSON 只用 `threading.Lock`（锁不跨进程）、固定 `.tmp` 名字、无原子替换 | 已修复（任务/账本/额度） | 三者全部迁入 `data/state.db`（`memory/state.py`）：跨进程事务 + 唯一键 + 条件更新；`tests/test_state.py` 用 4 个真实子进程验证「最后一份额度只成功一次」「并发写任务不丢数据」 | D05、D08 | 已修复；`ResidentMemory`（MEMORY.md 文件）仍属 D08 |
| A10 | 损坏/不可读状态一律当空数据：`WriteBudget._load` 返回 `{}` → 当日额度被重置；`JobStore` 读失败降级 | 已修复 | 迁移区分 缺失/合法空/0 字节/损坏；损坏且库内无可用数据时**停用能力**（budget 一律停用），预算显示满额而非 0；工作台返回 503 而不是空列表；心跳不启动调度器 | D05、D25 | 已修复（历史用量确实无法恢复时在 detail 里写明） |
| A11 | 同步 Playwright 实例跨线程复用，`RLock` 只串行化、不改变线程归属；同 profile 无跨进程占用协议 | 源码 | `tools/browser/driver.py` | D07 | 源码发现 |
| A12 | `tap_element` 在陈旧/换设备时先 `refresh_tree()` 再用**同一个 idx** 在新树里找元素 → 可能点到别的东西；动作后无强制复检 | 源码 | `tools/android/driver.py:168-185` | D16 | 源码发现 |
| A13 | 截图工具只回路径字符串，`Agent` 未构造图像消息块，模型收不到图 | 源码 | `tools/browser/tools.py`、`tools/android/tools.py`、`core/agent.py` | D15 | 源码发现 |
| A14 | 技能更新锁只覆盖最终 `write`，读改写不原子；版本号递增但无历史快照 | 源码 | `memory/skills.py:patch/rewrite/_write_versioned` | D08、D18 | 源码发现 |
| A15 | `profile_edit` 未重跑总额度校验；常驻防注入规则未覆盖 review/skill 等全部学习入口 | 源码 | `memory/store.py::profile_edit`、`core/review.py` | D12 | 源码发现 |
| A16 | 每次 review 起 daemon thread（无队列上限/去重/累计预算）；每个新连接重建 FTS | 源码 | `core/review.py`、`memory/store.py::_init_fts` | D11、D12 | 源码发现 |
| A17 | 每个到期任务 `create_task` 无上限；同任务锁只让任务排队；多调度进程无统一领取；`context_from` 取"最后一条 assistant 文本"且可陈旧 | 部分修复 | `claim_due()` 在单个写事务里推进 `next_run_at` + 写 `job_claims`；4 个真实进程同时 tick 只派发一次（`tests/test_state.py::test_claim_due_is_atomic_across_processes`） | D05、D17 | 领取已修复；并行上限、投递 outbox/去重、`context_from` 绑定 run_id 仍属 D17 |
| A18 | 服务停止只比对 pid 文件与进程存活，无创建时间/命令行身份绑定，pid 重用有误杀风险 | 源码 | `system/services.py` | D25 | 源码发现 |
| A19 | shell 超时只依赖 `subprocess.run` 对直接子进程的处理；Windows 后代进程与"副作用是否已发生"未区分 | 复现→已修复 | `test_process_boundary_reclaims_child_process_tree`（真实父子进程，孙进程 pid 已消失）；超时话术区分"已确认回收/回收状态未知" | D01、D09 | 进程边界已修复；统一 ToolResult 在 D09 |
| A20 | 无运行账本/取消/token 与费用硬预算/统一 `ToolResult` 状态（工具回的是字符串） | 源码 | `core/agent.py`、`llm/gateway.py`、`tools/registry.py` | D09、D10 | 源码发现 |

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
| D05 | 完成 | A09（任务/账本/额度）、A10、A17（领取） | 见第 5 节 | `uv run --no-sync pytest -q` → 380 passed | 常驻服务仍在跑旧代码，重启后才用新库；真机 `data/` 的旧 JSON 会在下次启动时迁移（未在真机执行） | 进入 D06 |
| D06–D08 | 待开始 | A06、A14 | — | — | — | 依赖 D05 |
| D09–D12 | 待开始 | A06、A15、A16、A19、A20 | — | — | — | 依赖 R1 |
| D13–D16 | 待开始 | A02（文件域）、A12、A13 | — | — | 安卓真机待插设备 | — |
| D17–D19 | 待开始 | A04、A17、A14 | — | — | 受信 MCP 生产服务待用户提供 | — |
| D20–D23 | 待开始 | R5 能力 | — | — | 桌面目标应用/QQ 群/B站测试账号均待用户参与 | — |
| D24–D27 | 待开始 | A10、A18、R6/R7 | — | — | 72 小时常驻待发布前执行 | — |

## 5 变更记录

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
| 桌面目标应用 | D20/D21 桌面读写 | 未提供 |
| 模型预算授权 | D10 付费推理上限 | 未提供（当前只用离线假模型跑测试） |

## 7 第一轮小结（D00–D04，2026-10-27）

### 7.1 关闭了什么

| 证据编号 | 问题 | 关闭方式 | 提交 |
| --- | --- | --- | --- |
| A01 | SAFE 前缀白名单可被复合命令穿过 | 结构化 argv + `shell=False`，先拒 shell 元字符 | `b17cdc1` |
| A02 | 只读命令的写参数与密钥文件 | git 参数级校验；`type`/`get-content` 移出 SAFE；路径限定工作区并遮蔽密钥 | `b17cdc1` |
| A19 | 超时只处理直接子进程 | 进程树回收 + "已确认回收/回收状态未知"话术 | `b17cdc1` |
| A03 | QQ 空白名单放行入站 | 统一判定 `private_target_allowed`，空白名单 = 谁都不许 | `42617ef` |
| A04 | cron/建任务空白名单放行投递 | 同上，四条路径共用；策略热读，撤销立即生效 | `42617ef` |
| A05 | 私聊/群聊共用绑定键 | `dm:` / `group:` 分场景绑定，旧键只迁私聊；群聊不带私人上下文 | `42617ef` |
| A07 | 工作台无身份、密钥明文回显 | 配对 + HttpOnly 会话 + Host/Origin/CSRF + WS 握手鉴权 + 密钥三态 | `7742a61` |
| A08 | `data_dir` 隔离有遗漏 | `RuntimePaths` 统一声明 + 隔离实例不碰真机进程 | 见本次提交 |

测试从 246 → **349**（新增 103 条，其中安全负例 60+ 条；所有负例的判据是"假执行器调用次数为零"）。

### 7.2 仍未关闭（R0 之外，按计划顺序）

A06（Web 共享 `agent.session_id` 串话）、A09/A10（JSON 锁与损坏降级）、A11（Playwright 线程归属）、
A12（安卓旧 idx）、A13（截图只回路径）、A14/A15/A16（技能原子性与 review 治理）、
A17（cron 跨进程领取/积压）、A18（pid 身份）、A20（Run/ToolResult/预算）。

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
4. **D08 技能 CAS** 与 D12 的 review 写入共享同一把锁语义，建议 D08 就把
   `expected_hash + 历史快照` 做成仓储 API，D18 再做 UI。
