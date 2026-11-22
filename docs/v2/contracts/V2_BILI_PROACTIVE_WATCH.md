# V2 B站主动观看设计合同（K18 缺口 #3：生物钟/双日记/沉默衰减/候选打分/偏好学习，决策 D5）

状态：**设计合同（design_contract_recorded，纯文档，无源码/测试/迁移/路由/worker 改动，2026-11-23）**。**未实现**。承接 [K18 审计](V2_BILI_WHALE_AUDIT.md) (d) 与 [剩余差异决策登记](V2_REMAINING_DECISIONS.md) D5；本册把参考 `dsh-bili-whale` 的夜间计划/双日记/沉默衰减/反馈回流/候选打分落到 V2 四层与既有调度/投递/学习原语，并列出**接主动行为前必须与 D 对齐**的接口缝与所有者授权门。分享策略（share 配额/notify/AI-share 守卫）属 D6，直播属 D7，不并入本册。

## 参考事实（固定 `8b0009f8`，仅作设计数据）

- **夜间计划**（`tools/lib/night-plan.mjs` + `night-config.mjs`）：窗口列表 `windows[{start,end,wakes}]`，默认 `20:00–24:00 × 1`；`skipChance=0.3`、`minGapMin=10`、`maxVideos=5`、`maxWakes≤12`（窗口次数和，单窗口上限 12）。计划在 `[max(now,start,lastWoke+gap), end-30s)` 内均匀随机取 `atMs`，先掷 `skip`（但仍写日记、**消耗一个 wake 位**）；`pending` 计划持久化（重启不重掷）；`history` 按「日期|窗口」记 `usedCount` 与 `wakes`，保留 3 天；窗口挤不下就少醒（硬下限，不自动压缩间隔）。
- **双日记**（`tools/commands/diary.mjs` + README）：`whale-diary.md`（叙事：看过/想法池/谁回过她，给人读）+ `whale-diary-private.md`（自我偏好校准）；另有 `whale-log.jsonl` 结构化事件流。日记总开关；「想说但还没说」是分享候选池。
- **沉默衰减**（`tools/commands/quota.mjs:61-80`）：最后一次反馈（或首次分享）起算沉默天数；`<3` 天 `normal` 全额、`>=7` 天 `faint` 且 share 上限 0（**日记照写**）、中间 `quiet` 线性降 `max(1, floor(base*(7-days)/4))`；`band` 注入唤醒提示语。
- **反馈回流**（`tools/lib/feedback.mjs`、`feed.mjs:218-268`）：点赞/回复按 `kind:id` 与 `lastLikeTime`/`lastReplyTime` 水位去重（保留 ≤1000 id）；被赞/被收藏作品的标题 token 进入 `learnedTokens`，`boost=min(7.5, learnedHits*2.5)`；偏好来自配置或日记「偏好」行。
- **候选打分**（`feed.mjs:250-300`）：`score = log10(1 + dm/max(30,d))*3 + prefHits*4 + boost`；**刻意排除播放量**；`looksLikeJunk = play<300 && dm<3`（≥2 偏好命中保留）；排除已看/已评/已赞/已收藏/自己的视频；同「命中和类」≤2（`PER`），无命中≤3；候选来源为偏好关键词搜索 + feed。

## V2 现状与映射

| 参考能力 | V2 原语 | 缺口 |
|---|---|---|
| 定时/唤醒 | `SchedulingService`（owner interval/cron、occurrence/blocker、失败不重放）+ `MaintenanceService.tick`（K08 评审、K15 衰减每日遍先例） | 无随机窗口计划、无 skip/minGap/maxWakes/maxVideos |
| 双日记 | `documents`（owner 可见、revision/provenance、删除屏障，K17 信笺/反思在用）+ 受管文件（persona growth 加密缓存先例）+ `maintenance` | 无观看日记模型/所有者读面 |
| 沉默衰减 | 纯领域 + delivery 配额（当前**无通用配额**，见 D4 合同 F1） | 无反馈水位、无衰减曲线、无 share 上限耦合 |
| 反馈回流 | `bilibili_read` msgfeed like/reply（已交付，`has_more` 无翻页）、`people` 所有者身份 | 无 owner 反馈判定/去重水位/学习 token |
| 候选打分 | `bilibili_read` search/popular/ranking/following/danmaku/video（均已交付） | 无打分器/去重集合/多样性 |
| 偏好学习 | K15 记忆/衰减（`memory_entries` importance/clarity）、K08 待审建议 | 无 diary→偏好、learned token 回流 |
| 动机 | K15 审计记录「主动动机引擎」缺口（未实现） | 需与 D 对齐接缝 |

## 设计决策（需所有者选定）

### W1 唤醒计划与触发路径

- **选项 A（推荐）**：新增所有者侧 `BilibiliWatchService` + 两张表（`bilibili_watch_plans` 每 agent/日窗口与已用次数；`bilibili_watch_events` 每次 wake/skip/watch/share 引用）驱动 **`MaintenanceService` 或独立 tick**；到时经既有 `submit_turn` 生成**普通 run**（会话可见、逐次模型预算、可取消），run 的 toolset 为只读 B 站读 + 日记写。计划状态持久化、重启不重掷、窗口/次数历史保留 3 天。
- **选项 B**：把每次 wake 物化成 `schedules` 的一次性 occurrence（复用 schedule  blocker/恢复），但随机/跳过/多窗口语义要外挂状态，复杂度高。
- **选项 C**：不自动唤醒，只给所有者「立即看一个」按钮（放弃生物钟对等，保留候选打分/日记）。
- 需决定：A/B/C；tick 频率（参考按分钟级检查 `atMs`；V2 可 30–60s）；睡眠/关机错过的计划是否补（参考 `misfire=latest` 语义，建议**不补**，就地下一次窗口）。

### W2 双日记存储与所有者可见面

- **选项 A（推荐）**：两份**受管 Markdown**（`<instance>/bilibili/watch/owner-letter.md`、`self-notes.md`），原子写、逐字节校验、权限 0600、无符号链接（复用 persona growth 受管缓存先例）；所有者只读 HTTP/工作台页；擦除来源时 fail-closed 失效（引用存在删除屏障）。
- **选项 B**：存 `documents`（revision/来源/删除屏障现成，但每日长文 revision 膨胀，需新 kind 或专用入口）。
- **选项 C**：直接写 agent workspace 文件（`files_write`），无所有者只读面、擦除不可控，不推荐。
- 需决定：存储；日记写入者（见 W3）；是否保留结构化事件流（`watch_events` 已覆盖）与「想说但没说」区（分享候选池，D6）。

### W3 唤醒 run 的输出与日记分工

- **选项 A（推荐）**：run 用只读工具看视频/弹幕/评论并产出**结构化 debrief**（工具结果 + 最终文本），应用层按固定模板渲染两本日记（叙事/偏好候选），模型不直接写日记文件（避免任意文件写、便于确定性校验与擦除失效）。日记正文仍是模型文本，但落盘由应用层受管写。
- **选项 B**：给模型一个受限 `watch_diary_append` 能力（写=审批门，但自主 run 无人审批 → 需自动批准预算，违背逐次审批纪律）。
- 需决定：A/B；debrief 结构（看过 BV/标题/想法/引用锚点）；单日多次 wake 的日记合并规则。

### W4 沉默衰减与分享上限耦合

- **纯领域** `silence_cap(last_feedback_at, first_share_at, share_daily, after=3, stop=7, now)` → `{days, cap, band}`；`band ∈ normal/quiet/faint` 注入唤醒提示；**只影响 D6 的 share 上限，不阻止日记/观看**。
- 需决定：`share_daily` 默认 3 是否采用；`last_feedback_at` 的判定（W5）；是否对「从未分享过」用首次分享起算（参考语义）；衰减是否也影响候选分数（建议否）。

### W5 反馈判定与去重

- 用已交付 `bilibili_read` 的 `msgfeed like/reply`（like 含 `users[]` 与 `like_time`）与 `replies` 回读；`people` 所有者 mid 映射；水位按 `kind:id` 与 `lastLikeTime/lastReplyTime`（≤1000 id），**只认真正的新事件**（不把整窗口摘要当计数）。
- 需决定：什么算「主人表态」（点赞分享/回复 DM/评论回复）；是否需要通知（reference 约定自主发言后必须通知主人）；反馈水位持久化位置（`watch_events` 或新列）。

### W6 候选打分与偏好学习

- 纯领域 `watch_candidates(candidates, prefs, learned, seen, liked, favs, self_name, limit, max_videos)`：
  - `density = log10(1 + dm/max(30, duration_s))`；`score = density*3 + prefHits*4 + min(7.5, learnedHits*2.5)`；**排除播放量**；
  - `junk = play<300 && dm<3`（≥2 偏好命中保留）；排除 seen/liked/fav/self；同「命中和类」≤2、无命中≤3；
  - 偏好来源：所有者在配置/日记「偏好」行；learned tokens 来自被赞/被收藏作品标题（纯领域 token 切分：分隔符 + 连接词 + ≥4 字中文双字滑窗，仅用于命中判断）。
- 需决定：候选来源组合（search 关键词≤6 + popular/ranking/following 首屏？）、每次 wake 最多看 `maxVideos=5` 的资源上限、偏好配置面（复用 settings 还是新 owner 页）。

### W7 安全、预算与默认值

- **默认 OFF**；所有者逐 agent 启用 + 全局 kill switch；启用前需 D 对齐（见下）与所有者授权；不 idle-wake（无窗口/超次数/预算不足即不动）；不自动外联（share 归 D6，未授权前只写日记/候选）；run 走既有模型预算/取消/租约/事件保留；不新增第二内核；日记/事件可被所有者删除或归档。
- 需决定：每日模型调用/费用上限（参考无硬上限，V2 建议以 `daily_budget_micro` + 每 wake `maxCalls` 双层）；静默时段；启用前是否要求「试运行仅生成计划不唤醒」。

### W8 与 K15 动机引擎的接缝

- K15 审计记录「需求→目标→驱动→情绪→动作规划」缺口未实现；D5 必须可**独立**工作（无动机引擎时按窗口/打分唤醒），同时留出可选 `motivation_signal`（need/drive 0–1）作为候选加分或唤醒紧迫度，**不复制动机引擎**。
- **接主动行为前与 D 对齐**（任务纪律）：D 负责的主动行为边界/授权语义确认后才能接线；本册只给接缝与不变量。

## 持久化与共享接口（A 协调）

- 预计新表：`bilibili_watch_plans`（agent+day+window 唯一、used/wakes、last_woke_at）、`bilibili_watch_events`（wake/skip/watch/share 事件、引用 BV、debrief 引用、owner 可见）；迁移编号由 A 分配；冻结 SQL、identity/keep 触发器、downgrade 拒绝按既有纪律。
- 若选 W2-B（documents）可能不改表；若选 W2-A 则新增文件路径与受管写（`InstancePaths` 加性）。
- 无共享端口签名改动；`BilibiliWatchService` 组合根装配，`capabilities is None` 生产路径启用（K08/decay 先例），隔离测试默认禁用。
- 模型侧**不新增写工具**；若需 owner 操作（立即看/暂停/查看日记）走 owner HTTP + 工作台只读页。

## 测试计划（实现批）

- 纯领域（冻结时钟/注入随机）：窗口边界/跨午夜/DST、`maxWakes`/`minGap`/`skipChance` 分布（固定 seed）、pending 持久不重掷、历史 3 天、挤不下少醒；`silence_cap` 3/7 天边界与线性档、band 注入；反馈去重（id/水位/未来时间拒绝/≤1000）；候选打分（密度/pref/boost 上限/排除播放量/junk 与 ≥2 命中/去重 seen-liked-fav-self/多样性）；debrief→日记渲染确定性、擦除失效。
- 应用集成（真实隔离 SQLite、fake 模型、冻结时钟）：启用后计划落库→tick 到点→普通 run（只读工具面、预算/取消/事件）→日记受管写→owner 只读；skip 仍写日记并消耗次数；禁用/无窗口/超预算/取消不唤醒；重启不重掷/不重复唤醒；多 agent 隔离；擦除来源后日记/事件失效或标注。
- 调用方：护持 maintenance/scheduling/delivery/engine/kernel/lifecycle/事件保留/架构/类型守卫。
- 真实账号/模型/72 小时另验；**未实现前不宣称对等**。

## 明确不做

- 分享策略/配额/notify/AI-share 守卫（D6）；直播（D7）；写扩面（D4）；K15 动机引擎本体；前缀重构（D1）；代码沙箱/CDP（D2/D3）。
- 真实 B 站账号、真实模型质量、72 小时不在本批。

## 关联

- K18 审计：[V2_BILI_WHALE_AUDIT](V2_BILI_WHALE_AUDIT.md) (d)。
- 决策登记：[V2_REMAINING_DECISIONS](V2_REMAINING_DECISIONS.md) D5。
- 相关合同：[V2_BILI_WRITE_BREADTH](V2_BILI_WRITE_BREADTH.md)（分享/写前置）、K15 `V2_PERSONA_AUTONOMY_AUDIT`、`V2_MEMORY_DECAY*`、`V2_BACKGROUND_REVIEW`。
- 证据：[v2-bili-proactive-watch-design.json](../../evidence/v2-bili-proactive-watch-design.json)。
