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

### 1.1 写入落点：调度与记忆不得互相复制

| 信息类型 | 唯一/优先落点 | 是否每轮注入 |
|---|---|---|
| 一次性闹钟、到点提醒 | `cron_once`（state.db 任务定义） | 否；`cron_list` 或工作台按需查看 |
| 周期任务 | `cron_add`（state.db 任务定义） | 否；`cron_list` 或工作台按需查看 |
| 跨会话持续有效的约定、重要教训、当前阻断 | `resident_memory` | 是，因此必须少而精，结束后移除 |
| 稳定且以后值得检索的事实 | `remember`，之后 `recall` | 否 |
| 有时效的人物背景（生日、出差等） | `person_event`，之后 `person_lookup` | 所有者的临时事件不自动注入；非所有者人物卡仍按 §4 的现有压缩卡协议处理 |
| 可复用的多步查询/排障流程 | 技能 | 否；按需 `skills_list` / `skill_view` |
| 当前会话执行清单 | `todo` | 仅当前会话使用，不写常驻 |
| 服务状态、一次查询结果、任务创建/执行回执、普通过程流水 | 原对话、全量记录或日/周/月笔记 | 否；不要复制进常驻 |

定时任务**只写调度模块**，不再额外写入 `resident_memory`、`person_event` 或 `remember`。
`person_event` 只是背景资料，不会触发任何动作；凡是"到点要做某事"，必须使用 cron。
主对话固定提示、`resident_memory`/人物/cron 工具说明、回合后 review 与默认日记整理规则
都执行同一分类口径。提示词负责引导模型选择，定时触发与持久化由调度存储负责，不靠记忆文本驱动。

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

## 5.1 事实记忆的清晰度阶梯（P5-a）

事实语料不止"记着/没记着"两态，还分**清楚程度**。抄 surfcore `memory-card.js`
的**判定逻辑**，不抄形态（它用一张 JSON 卡片 + `importance` 分数；我们用 SQLite
一列 `clarity` + 复用既有心跳整理循环 + 既有 `hits` 强化信号）。

| 档 | 含义 | 检索表现 |
|---|---|---|
| `vivid` | 清楚（刚写入或刚被用过） | 正常召回，不降权 |
| `clear` | 还记得 | 正常召回，不降权 |
| `fuzzy` | 开始模糊 | 仍可召回，排序略微后移，输出标"有点模糊了" |
| `trace` | 只剩印象 | 仍可召回，明显后移，输出标"很久没被提起，只剩印象" |
| `forgotten` | 忘了 | 仍可召回，排到末尾，输出标"很久没被提起，可能已经过时" |

- **判定层是纯函数**（`memory/clarity.py`）：`target_clarity` 算"这条记忆现在该有
  多清楚"（按**距上次被用**算年龄，近因比远因重要），`apply_daily_decay` 执行衰减，
  `review_effect` 执行复习升档。全部可离线单测，不碰数据库。
- **每天最多降一格**：一次从 `clear` 跳到 `forgotten` 等于"一夜失忆"。
- **复习即回升**：被召回的记忆升一格，形成"越用越牢"正反馈。
- **降权有界**：`RECALL_CLARITY_STEP=0.4` 每档一格链位，惩罚上限 `RECALL_CLARITY_CAP=2.0`
  个链位——陈旧不等于该被埋掉。**全部记忆清晰度相同时惩罚恒为 0**，排序与纯链序
  逐字节一致，冻结检索评测（`retrieval_v1.json`）数字不受影响。
- **与墓碑并存**：墓碑=用户明确要求删（硬删 + 永不可复活）；阶梯=没人用只是变模糊。
  **阶梯绝不写墓碑、绝不删行**——`docs/MEMORY_SYSTEM.md` 的硬约定是"事实语料负责
  查得到"，静默丢弃会让 `recall` 谎报"没有找到"。
- **两道每日闸门**（方向对称，一天最多降一格、最多升一格）：
  1. 衰减当天只跑一次（`meta.clarity_decay.day`）——整理循环每 5 分钟一轮，
     没有这道闸门，"每天一格"会退化成"每 5 分钟一格"；
  2. 升档当天只升一次（`clarity_reviewed_at`）——一轮对话里模型可能连着 recall
     同一批事实多次，每次都升档会把阶梯"刷"出来。

## 5.2 沉淀条目的原文锚定（P5-b）

后台写入器（review 回合后整理 / journal 夜间整理）会把一段对话压缩成一条
**每轮都注入提示词**的常驻/档案/人物条目。这条记忆此后每轮都在影响判断，
但此前**没有任何依据**：模型编造的成本是零，用户也无从核对"这是我真说过的话，
还是它自己写的"。

学自 dsh-bili-humanism（`lib/personality.mjs` 的 `PERSONALITY_REFLECTION_REQUIRED`）：
人格札记里的每句话都必须逐字出现在它引用的信笺原文里，否则拒绝落库；札记行末
还带"来源手账：<id> · 修订 N"，让人能回查。**抄机制，不抄形态**（我们不建
`human-world.md`，也不要求"整句逐字"）。

### 机制

| 环节 | 做法 |
|---|---|
| 判定 | `memory/anchor.py` 的 `verify_evidence`：摘录必须**逐字**出现在来源材料里（忽略空白后子串包含）。确定性比对，**不做**语义相似度判断——那是"让模型判断模型" |
| 触发 | 只对**后台写入器**强制（`anchor_scope` 上下文绑定）。对话中用户亲口说的内容不需要出处，逼它交出处只会把正常记忆写入变成负担 |
| 落库 | `person_entries` 增两列：`source_ref`（来源指针：`session:42` / `journal:daily:2026-11-01`）与 `evidence`（原文摘录） |
| 核对面 | `resident_memory view` / `profile_manage view` / `person_lookup` 末尾多一段"出处"（条目 → 来源 + 原文摘录） |

### 不变式

1. **落库的摘录一定经过逐字校验，且带得上可回查的来源指针**。对话路径给了摘录
   也不落库——没有可核的来源，留着就是"看着有依据，其实没核对过"。
2. **出处与内容绑定**：后台改写条目必须重新给出处（没给就拒绝）；对话路径改写
   内容时，内容变了就清掉旧出处（旧摘录未必还支撑新内容）。**清掉比留错诚实**。
3. **锚定在存储层收口**（`PersonStore.add_entry` / `edit_entry`），不在工具层——
   任何写入路径都走这里，后台整理绕不过去。

### 与参考实现的三处不同（为什么不照抄）

1. **锚的对象不同**：参考实现锚的是 *agent 自己的人格反思*；我们锚的是
   *关于用户的沉淀条目*。语义不同，所以只搬"必须可核对"这件事，不搬落点。
2. **严度不同**：参考实现要求"整句逐字"；我们写的是**概括**（原文里根本没有
   这句话），强制整句会误伤正常记忆。改成**摘录支撑**——条目可以概括，但必须
   附一段逐字原文，且系统校验得出处。
3. **形态不同**：参考实现把来源写在 `human-world.md` 的行末（每轮注入）；
   我们的证据**不进注入块**——常驻 2200 / 档案 2000 字符是硬上限，注入体积
   纪律优先。出处放在核对面（view/lookup）与工作台。

### 比对口径的取舍

- 比对**忽略全部空白**（不是"折叠成单空格"）：来源材料里一句话中间可能带换行
  （多行消息、粘贴的代码块），折叠成单空格会在中间塞进一个空格，模型逐字抄的
  那句就对不上了——**假阴性比略松的比对更糟**（合法的长期记忆写不进去）。
- 跨消息拼接**拼不起来**：来源材料每条消息都带角色/时间前缀
  （`[user] `、`[10:23] user: `），拼接处的前缀挡在那里。

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
- 清晰度阶梯的阈值（recent/fuzzy/trace/forget 天数）目前是 `memory/clarity.py`
  里的 `ClarityConfig` 默认值，**未接入 config.yaml**（暂不接受配置，避免为一个
  锦上添花的排序信号新增配置面）；接线方式已留好（显式参数注入，可离线单测）。
- 清晰度阶梯**未在真机跑过**：真实 memory.db 的存量事实从 `clear` 起步、
  每日降档的实际观感（陈旧标注措辞是否合适）待真实验收。
- 原文锚定**只覆盖人物条目**（常驻/档案/人物），**事实记忆（`remember`）暂未锚定**：
  facts 表在 `memory.db` 的另一张表上，要另开一列与一条校验路径，本批未做。
  事实的现有防线是"未知不是 0"（`cost_micro=NULL`）+ 遗忘墓碑，不含出处。
- 原文锚定**未在真机跑过**：真实 review / journal 模型能否稳定抄出逐字原文、
  抄不过时是否会照着报错改对再来，都待真实验收。离线测试只验机制不验模型行为。
- 周/月记的出处是**链式**的：月记的素材是周记、周记的素材是日记、日记的素材
  才是对话。指向"周记 2026-W44"能回查到那一期，但它本身就是压缩产物——
  比指向原始对话弱一档。
