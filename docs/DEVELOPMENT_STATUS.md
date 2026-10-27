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
| A03 | 私聊判断 `if cfg.allowed_users and ...`：空白名单 = 不限，任何人私聊都被回复 | 复现 | `test_a03_*`（假 agent 被调用、假 client 已发送） | D02 | 已复现 |
| A04 | `make_runtime_deliver` 用 `if allowed and ...`：空白名单 = 不限，cron 可发给任意 QQ；工作台建任务 `cron_create` 同款判断 | 复现 | `test_a04_*`；`core/cron.py:170`、`channels/web.py:476` | D02、D17 | 已复现 |
| A05 | 私聊与各群共用 `get_or_create_binding("qq", str(user_id))`，历史跨场景混合 | 复现 | `test_a05_*`（三场景同一键 `10001`） | D02、D06、D22 | 已复现 |
| A06 | Web `/api/chat` 先改共享 `runtime.agent.session_id` 再 `chat()`，响应与 review 也读共享值；并发请求会串话 | 源码 | `channels/web.py:210-220` | D06 | 源码发现 |
| A07 | 全套 API 无身份校验、无 Origin/Host 校验；`/api/terminal/ws` 直接 `accept()`；`/api/system/config` 回显 `qq.access_token` 明文 | 源码 | `channels/web.py:358-398`、`626-628`、`712`（`OMNI_WEB_HOST` 可任意） | D03 | 源码发现 |
| A08 | `data_dir` 隔离有遗漏：安卓截图写全局 `DATA_DIR/android_shots`；Web 的 Cookie/配置路由用全局 `BILI_COOKIES_PATH`/`CONFIG_PATH`；`bootstrap` 构造 `BiliAPI()` 未注入 Cookie 路径（测试会读真机 Cookie） | 源码 | `tools/android/driver.py:136`、`channels/web.py:28,403,419,439`、`bootstrap.py:131` | D04 | 源码发现 |
| A09 | 共享 JSON 只用 `threading.Lock`（锁不跨进程）、固定 `.tmp` 名字、无原子替换：`JobStore`/`ExecutionLog`/`WriteBudget`/`ResidentMemory` | 源码 | `memory/cron.py`、`tools/browser/budget.py:33-58` | D05、D08 | 源码发现 |
| A10 | 损坏/不可读状态一律当空数据：`WriteBudget._load` 返回 `{}` → 当日额度被重置；`JobStore` 读失败降级 | 源码 | `tools/browser/budget.py:44-50` | D05、D25 | 源码发现 |
| A11 | 同步 Playwright 实例跨线程复用，`RLock` 只串行化、不改变线程归属；同 profile 无跨进程占用协议 | 源码 | `tools/browser/driver.py` | D07 | 源码发现 |
| A12 | `tap_element` 在陈旧/换设备时先 `refresh_tree()` 再用**同一个 idx** 在新树里找元素 → 可能点到别的东西；动作后无强制复检 | 源码 | `tools/android/driver.py:168-185` | D16 | 源码发现 |
| A13 | 截图工具只回路径字符串，`Agent` 未构造图像消息块，模型收不到图 | 源码 | `tools/browser/tools.py`、`tools/android/tools.py`、`core/agent.py` | D15 | 源码发现 |
| A14 | 技能更新锁只覆盖最终 `write`，读改写不原子；版本号递增但无历史快照 | 源码 | `memory/skills.py:patch/rewrite/_write_versioned` | D08、D18 | 源码发现 |
| A15 | `profile_edit` 未重跑总额度校验；常驻防注入规则未覆盖 review/skill 等全部学习入口 | 源码 | `memory/store.py::profile_edit`、`core/review.py` | D12 | 源码发现 |
| A16 | 每次 review 起 daemon thread（无队列上限/去重/累计预算）；每个新连接重建 FTS | 源码 | `core/review.py`、`memory/store.py::_init_fts` | D11、D12 | 源码发现 |
| A17 | 每个到期任务 `create_task` 无上限；同任务锁只让任务排队；多调度进程无统一领取；`context_from` 取"最后一条 assistant 文本"且可陈旧 | 源码 | `core/cron.py:76`、`_latest_source_output` | D05、D17 | 源码发现 |
| A18 | 服务停止只比对 pid 文件与进程存活，无创建时间/命令行身份绑定，pid 重用有误杀风险 | 源码 | `system/services.py` | D25 | 源码发现 |
| A19 | shell 超时只依赖 `subprocess.run` 对直接子进程的处理；Windows 后代进程与"副作用是否已发生"未区分 | 复现→已修复 | `test_process_boundary_reclaims_child_process_tree`（真实父子进程，孙进程 pid 已消失）；超时话术区分"已确认回收/回收状态未知" | D01、D09 | 进程边界已修复；统一 ToolResult 在 D09 |
| A20 | 无运行账本/取消/token 与费用硬预算/统一 `ToolResult` 状态（工具回的是字符串） | 源码 | `core/agent.py`、`llm/gateway.py`、`tools/registry.py` | D09、D10 | 源码发现 |

补充观察（D00 新增登记，尚未编号为 A 项，供后续任务核对）：

- `peek_command` 的 `_PEEK_RE` 允许 `python --version` / `uv --version`，但同样允许 `python -V;任意命令`——A01 的同一根因，随 D01 一起关闭；
- `WriteBudget._save` 直接 `write_text` 覆盖正在被其它进程读的文件（A09 同源），D05 统一迁 `state.db`；
- `JobStore`/`ExecutionLog` 默认路径仍写死 `DATA_DIR`，与 D04 的 `RuntimePaths` 一致处理。

## 4 任务台账

| 任务 | 状态 | 关闭的证据 | 改动/commit | 测试证据 | 真实验收 | 下一步/阻塞 |
| --- | --- | --- | --- | --- | --- | --- |
| D00 | 完成 | 本文件 + `tests/test_r0_repro.py`（A01–A05 复现） | `3fbf762` | `uv run pytest -q` → 254 passed（246 基线 + 8 复现） | 不适用 | 进入 D01 |
| D01 | 完成 | A01、A02（命令面）、A19（进程边界） | 见第 5 节 | `uv run pytest -q` → 297 passed | 真实命令面（中文输出、带空格路径）已由本机 `git status` 用例覆盖；真实危险命令不做验收 | 进入 D02 |
| D02 | 待开始 | A03、A04、A05 | — | — | QQ 真实收发待用户登录后可验 | — |
| D03 | 待开始 | A07 | — | — | 浏览器真实跨站尝试待验 | — |
| D04 | 待开始 | A08 | — | — | 真机目录 canary 待验 | — |
| D05–D08 | 待开始 | A09、A10、A14、A17 | — | — | — | 依赖 D04 |
| D09–D12 | 待开始 | A06、A15、A16、A19、A20 | — | — | — | 依赖 R1 |
| D13–D16 | 待开始 | A02（文件域）、A12、A13 | — | — | 安卓真机待插设备 | — |
| D17–D19 | 待开始 | A04、A17、A14 | — | — | 受信 MCP 生产服务待用户提供 | — |
| D20–D23 | 待开始 | R5 能力 | — | — | 桌面目标应用/QQ 群/B站测试账号均待用户参与 | — |
| D24–D27 | 待开始 | A10、A18、R6/R7 | — | — | 72 小时常驻待发布前执行 | — |

## 5 变更记录

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
