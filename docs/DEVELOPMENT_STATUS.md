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
| A06 | Web `/api/chat` 先改共享 `runtime.agent.session_id` 再 `chat()`，响应与 review 也读共享值；并发请求会串话 | 已修复 | 会话 id 由请求固定；进程内闸门（同会话串行/异会话并行/队列满 429）+ `state.db` 跨进程租约；review 显式带 sid；`tests/test_sessions.py` 14 例（含两个请求真并行、同会话不交错、工具写入正确会话、异常回合释放自己的租约） | D06 | 已修复（QQ 侧接入租约留 D22） |
| A07 | 全套 API 无身份校验、无 Origin/Host 校验；`/api/terminal/ws` 直接 `accept()`；`/api/system/config` 回显 `qq.access_token` 明文 | 源码→已修复 | `tests/test_web_auth.py` 31 例：12 个敏感 GET 未配对即 401、写接口/服务/审批/WS 全拒、跨站 POST 无副作用、Host 伪造、过期/篡改会话、密钥不回显与三态修改、非回环监听默认拒绝 | D03 | 已修复（真实浏览器跨站尝试与 PTY 会话待手动验收） |
| A08 | `data_dir` 隔离有遗漏：安卓截图写全局 `DATA_DIR/android_shots`；Web 的 Cookie/配置路由用全局 `BILI_COOKIES_PATH`/`CONFIG_PATH`；`bootstrap` 构造 `BiliAPI()` 未注入 Cookie 路径（测试会读真机 Cookie） | 源码→已修复 | `config.RuntimePaths` 统一声明 12 个路径；`tests/test_paths.py` 10 例：路径派生、安卓截图、Cookie 读取、Web 写配置/Cookie、隔离实例不探测/不启停真机进程、真实 `data/` 目录 canary 全量快照不变 | D04 | 已修复（真机目录 canary 用 mtime 快照验证；跨盘符/junction 场景留待 D13 文件域） |
| A09 | 共享 JSON 只用 `threading.Lock`（锁不跨进程）、固定 `.tmp` 名字、无原子替换 | 已修复 | 任务/账本/额度迁入 `data/state.db`（跨进程事务 + 唯一键 + 条件更新）；技能与常驻记忆的**文件**读改写改用 `system/filelock` 跨进程锁 + `system/atomic` 唯一临时文件原子替换；`tests/test_state.py`、`tests/test_skills_history.py` 各用真实子进程验证「不丢写」 | D05、D08 | 已修复 |
| A10 | 损坏/不可读状态一律当空数据：`WriteBudget._load` 返回 `{}` → 当日额度被重置；`JobStore` 读失败降级 | 已修复 | 迁移区分 缺失/合法空/0 字节/损坏；损坏且库内无可用数据时**停用能力**（budget 一律停用），预算显示满额而非 0；工作台返回 503 而不是空列表；心跳不启动调度器 | D05、D25 | 已修复（历史用量确实无法恢复时在 detail 里写明） |
| A11 | 同步 Playwright 实例跨线程复用，`RLock` 只串行化、不改变线程归属；同 profile 无跨进程占用协议 | 已修复 | 专用所有者线程 + 有界队列 + `BrowserUnknown` 超时语义；profile 走 `resource_leases`（v3）；`tests/test_browser_owner.py` 8 例，含真实 Chromium 多线程用例与跨进程租约 | D07 | 已修复（真实登录 profile 未用于测试，符合边界） |
| A12 | `tap_element` 在陈旧/换设备时先 `refresh_tree()` 再用**同一个 idx** 在新树里找元素 → 可能点到别的东西；动作后无强制复检 | 源码 | `tools/android/driver.py:168-185` | D16 | 源码发现 |
| A13 | 截图工具只回路径字符串，`Agent` 未构造图像消息块，模型收不到图 | 源码 | `tools/browser/tools.py`、`tools/android/tools.py`、`core/agent.py` | D15 | 源码发现 |
| A14 | 技能更新锁只覆盖最终 `write`，读改写不原子；版本号递增但无历史快照 | 已修复 | 整段读改写进跨进程锁；`expected_hash`/`_seen` 做 CAS；`memory/revisions.py` 保存每版内容（作者/来源/时间/摘要/附件清单）；回滚产生新修订；`tests/test_skills_history.py` 18 例（含两进程同指纹只有一个成功、落盘失败原文件不变、junction 写入被拒） | D08 | 已修复；编辑 UI 与差异展示留 D18 |
| A15 | `profile_edit` 未重跑总额度校验；常驻防注入规则未覆盖 review/skill 等全部学习入口 | 已修复 | `profile_edit_checked` 重算总额并拒绝超限（`test_profile_edit_cannot_exceed_total`）；事实/档案/技能/回滚统一走 `MemoryGuard`（输入检查 + 共用当日额度 + 遗忘检查），review 与手动写入同一闸门（`tests/test_memory_governance.py` 18 例） | D12 | 已修复（"候选队列/差异确认"未做，见第 5 节剩余限制） |
| A16 | 每次 review 起 daemon thread（无队列上限/去重/累计预算）；每个新连接重建 FTS | 已修复 | **索引**：FTS 改由触发器增量维护，只在「首次 / 分词器升级 / 旧库无状态」重建一次（`tests/test_context.py` 5 例）；**review**：单 worker + 有界队列 + 按消息区间认领去重，队列满返回 `full`（`test_review_queue_is_bounded`、`test_review_is_deduplicated_by_message_range`）；模型消耗计入 D10 账本（`purpose=review`） | D11、D12 | 已修复（真实多进程并发 review 未演练） |
| A17 | 每个到期任务 `create_task` 无上限；同任务锁只让任务排队；多调度进程无统一领取；`context_from` 取"最后一条 assistant 文本"且可陈旧 | 部分修复 | `claim_due()` 在单个写事务里推进 `next_run_at` + 写 `job_claims`；4 个真实进程同时 tick 只派发一次（`tests/test_state.py::test_claim_due_is_atomic_across_processes`） | D05、D17 | 领取已修复；并行上限、投递 outbox/去重、`context_from` 绑定 run_id 仍属 D17 |
| A18 | 服务停止只比对 pid 文件与进程存活，无创建时间/命令行身份绑定，pid 重用有误杀风险 | 源码 | `system/services.py` | D25 | 源码发现 |
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
| D10–D12 | 待开始 | A15、A16、A20（token/费用预算） | — | — | — | 依赖 D09 |
| D13–D16 | 待开始 | A02（文件域）、A12、A13 | — | — | 安卓真机待插设备 | — |
| D17–D19 | 待开始 | A04、A17、A14 | — | — | 受信 MCP 生产服务待用户提供 | — |
| D20–D23 | 待开始 | R5 能力 | — | — | 桌面目标应用/QQ 群/B站测试账号均待用户参与 | — |
| D24–D27 | 待开始 | A10、A18、R6/R7 | — | — | 72 小时常驻待发布前执行 | — |

## 5 变更记录

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
