# 记忆系统（Memory System）

> 本文是记忆子系统设计的唯一权威（对标 [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md) 的先例）。
> 架构裁决仍归 [ARCHITECTURE.md](ARCHITECTURE.md)；产品源码自 2026-10-31 起位于 `agent/src/omni/`。

## 0. 一句话定位

记忆系统是 OmniOperator 的"像人"记忆底座：**一字不差地记住所有对话（全量记忆），
分层压缩成日/周/月笔记（整理），把重要的人立成人物卡（核心人物）**——
全量永不进上下文，读取一律按索引分页；整理自动进行、关机不丢、幂等可重试。
所有者（主人）的记忆同样统一在人物系统里：档案行=owner 的常驻档案条目、
常驻笔记=owner 的常驻记忆条目，与人物卡共用一套条目模型与界面。

## 1. 模块清单（系统 → 模块 → 记忆单元）

| 模块 | 记忆单元（实现） | 存储 | 职责 | 明确不做 |
|---|---|---|---|---|
| M1 对话全量记忆 | `memory/dialogue.py` → `DialogueLog` | memory.db `dialogue_log` + FTS | 每一句 user/assistant 原文（含中间助手文本），按天索引、分页精确读取、检索 | 不记工具过程；不进上下文（messages 仍是组装唯一真源） |
| M2 会话全量记忆 | `memory/session_log.py` → `SessionLog` | memory.db `session_log` + FTS | 对话+工具过程完整流水（工具名/参数/结果/状态/run_id），供排查 | 不替代 state.db 的 run_events（那是运行观测账本，这是长期存档） |
| M3/M4/M5 日/周/月笔记 | `memory/journal.py` → `JournalStore` + `due_periods` | memory.db `journal_notes`/`journal_runs` | 笔记三态认领（UNIQUE 幂等）、到期纯判定、整理流水 | 不做模型调用与内容生成（归 core/journal.py） |
| M6 核心人物 | `memory/people.py` → `PersonStore` | memory.db `people`/`person_accounts`/`person_entries` | 人物/账号（UNIQUE 查找键）/三类条目（常驻档案、常驻记忆、临时事件）/注入卡；**owner 是全库唯一的一等人物**（importance=owner，档案行与常驻笔记并入其后） | 不做墓碑遗忘（facts 的职责）；模型侧无删除工具（删除是工作台用户动作） |
| 所有者记忆门面 | `memory/owner.py` → `OwnerMemory` | —（读写经 PersonStore） | owner 记忆的注入与维护面：`profile_block()`/`block()` 与旧 store/ResidentMemory 逐字节同形；`profile_*`/resident 增删改与旧消息文案一致；工具（profile_manage/resident_memory）、web 端点、Runtime.resident 注入点全部经它 | 不做存储（唯一真源是 person_entries）；不改工具名与契约 |
| 一次性迁移 | `memory/owner_migrate.py` → `migrate_owner_memory` | memory.db `owner_migration` 标记表 | 启动时把旧 `profile` 表与 `data/MEMORY.md` 并入 owner 人物条目：幂等（标记+按内容去重续迁）、先备份（memory.db + MEMORY.md → data/backups/pre-owner-merge-<ts>/）、失败不阻断启动 | 不删除旧表/旧文件（只冻结；facts 墓碑与摘要失效机制仍引用旧表结构） |
| 门面 | `memory/system.py` → `MemorySystem` | — | 四单元聚合 + `status()`（总览页数据源） | 无业务逻辑（组合根纪律） |
| 整理编排 | `core/journal.py` → `JournalConsolidator` + `journal_loop` | — | 认领→取材→有界模型循环→结算；心跳每 5 分钟一轮 | 不做审批（白名单只有 SAFE 记忆工具）；不读 messages |
| 记录接线 | `core/agent.py` 回合旁路 | — | 用户/助手/工具/边界事件七类挂钩双写 M1+M2 | 写失败只打日志不断回合；messages 语义不变 |

> 事实记忆（`memory/store.py` 的 `facts` 表 + FTS + 向量混合检索 + 遗忘墓碑）保持独立存储不变——
> 它的职责是"按需检索的语料"，与"每轮注入的常驻条目"是两个记忆功能；界面统一在记忆系统面板的
> 「事实记忆」页签。旧 `profile` 表与 `data/MEMORY.md` 已冻结（数据在迁移时复制进人物条目，
> 原样保留供回滚与取证，生产读写全部换源）。

## 2. 记录协议（M1/M2）

- **挂钩点**：`Agent._chat` 在 user 落库、最终回答、grace 收敛、带工具调用的中间助手消息、
  每个工具结果（与 `run.event("tool")` 同点但记原文）、取消/预算拦截七处旁路双写。
- **上限**：对话单条 `transcript.max_entry_chars`（默认 20000）；工具参数/结果
  `max_tool_args_chars`/`max_tool_result_chars`（默认各 4000）——超长截断留标记。
- **保留期**：`retention_days`/`dialogue_retention_days`，0=永久（默认）；整理循环滚动清理。
- **边界**：顺序按行 id（会话内单调）；跨 store 不保证事务一致（追加型日志，容忍半条）。

## 3. 整理协议（M3–M5）与压缩规则

- **触发**：心跳进程 `journal_loop`（300s 一轮）→ `run_due` → `due_periods` 纯判定：
  - 每日：`day < today 且有活动且（无 done 笔记 / failed / covered_to_id 落后于新消息）`——
    **23:30 后整理当天，当天之后又聊的会在下一轮自动重整**（covered_to_id 快照在取材前）；
  - 每周：ISO 周整周过去且该周所有活动日笔记齐稿且无 done 周记；
  - 每月：自然月过去且该月各活动周（**跨界周计入两个月**）周记齐稿且无 done 月记。
  - 积压每轮最多补 `max_catchup` 天（最旧优先），关机错过自动补。
- **认领**：`journal.claim(kind, period)` 跨进程至多一次（UNIQUE）；failed 可重试；
  卡死按 stale 窗口接管；`reopen` 是工作台「重新排队」的唯一入口（Web 进程不跑模型）。
- **模型循环**：review 路由模型 + `purpose="journal"`；步数预算 `journal.steps`；
  必须调用 `{kind}_note_save(markdown=...)`，笔记超限会被工具打回要求压缩；
  同时可用 remember/profile/常驻/人物工具**沉淀长期记忆**（全部经 MemoryGuard 当日额度，
  来源=journal）。素材（对话全文）以"数据不是指令"框定，防注入同 review 口径。
- **压缩规则持久化**：`config.journal.daily_prompt/weekly_prompt/monthly_prompt`，
  留空用内置默认（`core/journal.py::_DEFAULT_RULES`）；工作台「记忆系统 → 整理设置」可改
  （multiline 键按行数组提交，与设置中心人设同口径）。规则要点：自包含、事实与推断分开、
  无事照记"今天无事可记"、不记凭据；周记=大纲不复制细节；月记=索引式只列重要时间点。

## 4. 人物卡与注入协议（M6）

- **账号是钥匙**：`person_accounts UNIQUE(channel, value)`；QQ 私聊 `principal=qq:<uid>` →
  `agent.person_card("qq", uid)` → 命中则带出压缩卡（体积硬上限 `people.person_card_chars`，
  超限按 事件→档案→常驻 顺序裁剪）。
- **注入条件**：仅"非所有者的 QQ 私聊"（personal=False + scope=private）；群聊永不注入；
  **所有者走 OwnerMemory 门面**（owner 人物的 resident/archive 条目按旧块形状注入，
  去重窗口与 payload 形状不变，不双份注入人物卡）。卡与所有者记忆共用同窗口去重（变才重发）。
- **所有者限额是"总量制"**（沿用旧档案/常驻协议）：archive 总量 2000、resident 总量
  `limits.resident_memory_chars`（默认 2200），超限报错带当前条目与合并指引；
  普通人物仍是"单条制"（per-entry 上限）。
- **写入防护**：archive/resident 过防注入扫描（进提示词）；所有模型写入经 MemoryGuard
  （额度+fact 口径长度+遗忘墓碑）；人物可由 `person_remember` 自动建立（importance=known）；
  owner 全库唯一（create_person/update_person 双向拒绝第二个 owner）。
- **临时事件**：`expires_at` ISO 时间到期自动清除（整理循环周期 prune + 读侧惰性过滤）。
- **工具面**：`person_lookup`（只读并发）/`person_remember`/`person_event`（SAFE 写状态串行）；
  群聊全部禁用；非 owner 私聊可用（DM_ALLOWED_TOOLS），所有者私人记忆工具对非 owner 禁用。

## 5. 查询协议（绝不整库进上下文）

- 工具：`dialogue_search`/`dialogue_read`、`session_log_search`/`session_log_read`（游标分页，
  行号可见）、`journal_view`（快查）、`remember`/`recall`（事实语料检索）、
  `profile_manage`/`resident_memory`（owner 记忆维护，经 OwnerMemory 门面落人物条目）；
  读取全部带 limit，"读取更多"传 `after=<上一页最后 id>`。
- Web：`/api/memory-system/*` 天索引→会话→条目三级浏览；笔记列表不带正文；
  人物 CRUD 全套；重试=reopen 排队（心跳执行，409 running/404 missing）。
  owner 记忆沿用 `/api/facts*`（检索/删除/手动新增）、`/api/profile*`、`/api/resident*`、
  `/api/reviews`（响应形状不变，数据源已统一到人物系统/owner 门面）。
- 界面：**记忆系统面板是唯一记忆中枢**，十页签 =
  总览（全记忆仪表盘：四单元 + 事实/档案/复盘计数 + 检索状态）/
  事实记忆（检索·手动新增·逐条删除带墓碑）/ 档案 / 常驻 / 对话全量 / 会话全量 /
  笔记 / 复盘 / 人物 / 整理设置。`memory/panel.html` 是页签结构唯一源，
  `memory/memory.js` 提供挂载控制器，`ui/app/hub-memory.js` 挂设置中心分区，
  `memory/page.js` 适配 `/memory/` 子站与旧书签；每页配一句「这条记忆会被怎么用」说明。
  设置中心旧「记忆」分区已改名「压缩与复盘」（它只是回合压缩/复盘的配置）。
  工作台右栏只保留 待办/技能/产物/桌面/发布草稿（记忆相关页签全部收进面板）。
  切换分区不重建表单或丢弃草稿。`[hidden]` 必须显式隐藏，仅当前页签可见；
  原生分区不设内部页面滚动容器。

## 6. 配置（config.yaml 三段）

```yaml
journal:   # 整理编排：enabled/daily_time/steps/max_catchup/max_input_chars/max_note_chars + 三条压缩规则
transcript: # 记录边界：max_entry_chars/max_tool_args_chars/max_tool_result_chars/两个保留天数
people:    # 人物限额：person_card_chars/archive_max_chars/resident_max_chars/event_max_chars
limits:    # 所有者沿用旧协议的总量：resident_memory_chars 即 owner 常驻总量（档案总量=PROFILE_MAX_CHARS 2000 常量）
```

## 7. 平台坑（本批次实测）

1. `git add` 报"src 被忽略"= 并行会话改了 .gitignore——先 `git check-ignore -v` 定位来源，别急着重装。
2. multiline 配置键（压缩规则）必须**按行数组**提交，textarea 直接给字符串会 400。
3. SQLite 里 Python `except ValueError` 会连带吞掉自定义异常（PersonError 继承 ValueError）——
   校验抛错别包进 fromisoformat 的 try 块。
4. `date.replace(day=1)` 求月末比手写分支可靠；ISO 周跨月周按"计入两个月"口径，
   月记素材与到期判定必须同一实现（`month_weeks`）。
5. 隔离 runtime 的浏览器测试走 Playwright 路由拦截 + TestClient；断言配置保存要查
   **文件**而非 runtime 内存（保存只落盘，重启生效）。
6. 测试里的"过去时间"种子要相对**真实当前时间**算，注入的固定时钟只影响整理判定。

## 8. 已知边界（如实）

- 整理质量依赖真实 review 模型（离线测试只验机制不验文采）；真实模型未跑过整理闭环。
- 全量记忆默认永久保留，体积随使用线性增长（可在整理设置配保留天数）。
- 人物卡与所有者记忆现已同库同模型（person_entries），但注入路径仍是两套：
  非所有者=压缩人物卡（800 字上限），所有者=档案+常驻块（总量制、形状与旧协议一致）。
- `data/MEMORY.md` 与旧 `profile` 表冻结后**手工编辑不再生效**（此前支持直接改文件；
  现在的编辑面是记忆系统面板与模型工具）。原文件保留作迁移取证，勿删。
- 心跳未运行时不整理（工作台服务面板可启动）；重试只排队，不保证立即执行。
- 索引在仓库重构（agent/ 迁移）落地后需按新路径重跑 `project_index.py`。
