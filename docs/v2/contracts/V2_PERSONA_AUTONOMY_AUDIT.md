# V2 人格/遗忘巩固/自主行为审计（K15）

## 结论与范围

K15 是**逐项审计**（非新增引擎批次）：把参考 `xuexi/surfcore`（固定提交 `59ec3066f7d42a0452fae01f170c9a8484d5dbe5`，v0.8.6，DSH 插件 + 独立 QQ 桥双进程，人格/记忆/目的引擎为**纯逻辑零 I/O** 模块）的行为闭环，与 V2 people/memory/scheduling/deliveries/QQ/B 站/K08 回合后学习逐项对照，分三类：**已对等**、**需账号/实机验收**、**可离线补齐缺口**。已有 people/memory/QQ/B 站/schedule **不重复实现**；**不宣称完整自主行为对等**（对标矩阵明示「surfcore 的完整自主作息/动机不应标已完成」）。

参考核心 IP 是离线纯逻辑三件套——`memory-card.js`（衰减/巩固）、`purpose-engine.js`（需求→目标→驱动→情绪）、`night-schedule.mjs`（质量反馈作息）——外包统一分层人格与薄渠道适配器。V2 现有：`domain/people.py`（persona/人物身份）、`memory_search/remember/forget` 模型工具、`memory_semantics`（FTS+向量）、`memory_organization`（**显式**整理，不调模型）、来源删除屏障、K08 回合后 reviewer/curator（待审建议进 remember/forget + 技能审核）、`SchedulingService`（interval/cron，所有者侧）、`DeliveryService` outbox、QQ（OneBot）/B 站读写。

## 组 1 — 跨渠道一致人格

| 参考能力 | V2 对应 | 分类 |
| --- | --- | --- |
| 分层人格 `composePersonaLayers`（identity-base + core + tone + **scene** + daily；scene 按渠道 qq-group/qq-private/bili/pet；预算裁剪 daily→scene 保护 core） | V2 settings.persona（单一 persona 串）+ 运行中系统前缀冻结（K01） | **部分对等**：V2 有 persona 配置与冻结前缀；分层 scene/daily 组合与预算裁剪缺 |
| Markdown 角色卡 `roles/<name>.md`（persona vs simulation 两层）；`selectRoleText` 一代/二代模式过滤 | V2 persona 串 + 技能指令 | **部分对等**：角色卡分层/模式过滤缺 |
| 统一人设卡注入 QQ + B 站（`sanitizePersonaForBili` 剥离 QQ 号/昵称段，banner「QQ和B站是同一个人」）；pet 同卡 | V2 QQ/B 站渠道 + persona | **可离线缺口**：跨渠道统一人格卡注入 + 渠道 sanitize（纯逻辑）；live 注入需账号 |
| 角色包 `character-persona.js`（persona + 衰减/全量记忆 + bili 账本，`epoch` 原子隔离切换） | V2 people 身份 + agent 隔离 | **可离线缺口**：角色包 epoch 原子切换 |
| 身份对齐 `alignments.json {identities:{qq,bili,tieba}}`；跨平台 self-anchor（bili 认出自己 QQ profile）；admin 双身份 | V2 people/accounts 绑定 | **部分对等**：V2 有 people 绑定；跨平台身份对齐/self-anchor 缺 |

## 组 2 — 遗忘 / 巩固学习

| 参考能力（`memory-card.js`） | V2 对应 | 分类 |
| --- | --- | --- |
| 双卡：衰减卡（注入、预算裁剪）+ 全量卡（append-only、永不注入、仅归档） | V2 memory（live）+ 来源文档/审计 | **部分对等**：V2 有 live memory 与来源；衰减/全量双卡分离缺 |
| **时间衰减遗忘**：4 级 vivid→clear→fuzzy→trace→deleted，每日最多 1 步（dailyHour=4）；retention=importance+recallCount×2+recency−timePenalty；forgetAfterDays=60 且 importance<50 遗忘；漏日补追（≤30 天） | V2 memory_forget（**显式**按 id）+ memory_organization（显式整理） | **可离线缺口（重大）**：V2 无时间衰减/自动遗忘层级；纯衰减/scoring 逻辑 + 定时 tick 可离线 |
| 重要性启发式 `scoreMemory`（owner+42、emotion+0–24、first-meet+30…）、`targetClarity` | V2 memory remember（quote 来源） | **可离线缺口**：重要性启发式评分 |
| **复习强化**：再提及 bump recallCount/lastSeenAt → 可回升层级 | V2 memory_search（召回不改强度） | **可离线缺口**：召回强化回路 |
| 保护：pinned/first-meet 恒 vivid；owner importance≥65 永不忘 | V2 来源删除屏障（保护来源） | **部分对等**：V2 有来源保护；记忆级保护规则缺 |
| 巩固：合并记录为事件记忆，botReply 折叠进 owner 记录；容量 80/90/100% 告警 + auto-compact→90% | V2 memory_organization（显式合并）+ K08 curator | **部分对等**：V2 有显式整理与回合后 curator；容量告警/auto-compact 缺 |
| 隐私 scope qq/bili/shared/pet，includeQqInBili=false | V2 agent 隔离 + 来源删除 | **部分对等**：V2 有 agent 隔离；渠道级记忆隐私 scope 缺 |
| 维护 tick（+8s 后每 10min：ingest inbox+bili→归档全量→每日遗忘→auto-compact→告警） | V2 maintenance + K08 后台批次 | **部分对等**：V2 有后台维护框架；记忆衰减 tick 缺 |

## 组 3 — 主动动机 / 自主行为（K15 命名缺口）

| 参考能力（`purpose-engine.js`，零导入零 I/O） | V2 对应 | 分类 |
| --- | --- | --- |
| 管线：attention → 9 需求（play/install/learn/connect/care/share/identity/create/consume）→ **目标假设**（归因 self/owner/member/media + confidence/value/urgency/risk）→ 10 情绪评估 → 动作（share/ask/invite/confide/curiosity/follow） | 无（V2 schedule 所有者发起；K08 回合后复盘非主动动机） | **可离线缺口（重大）**：主动动机/需求/目标/情绪引擎（纯逻辑）；话语生成+投递需模型/账号 |
| 驱动 9 维 0–1 向量，半衰期 8h 向默认衰减；connect 随 owner 沉默上升；analyzePersona 人格偏置；发送满足需求（×0.45） | 无 | **可离线缺口**：驱动衰减/人格偏置 |
| 观察账本防「一次浏览=永久欲望」；反馈扫描 owner 回复→confirmed/rejected/expired；状态 per-character purpose-outreach.json | 无 | **可离线缺口**：观察账本/反馈回路 |
| `purposeTick` QQ 载体 60s tick，12 硬门（reserved2/whitelist/quiet/dailyMax=4/idle/token/friend）；pet 载体同引擎+状态，carrier 互斥 + petFallbackToQQ | V2 scheduling tick + deliveries 限额 | **部分对等**：V2 有调度/限额框架；主动外联硬门与载体互斥缺（需账号） |
| `action-planner.js`/`conversation-closure.js`（动作规划、对话闭合带「下次开场」） | 无 | **可离线缺口**：动作规划/对话闭合 |

## 组 4 — 作息反馈闭环

| 参考能力 | V2 对应 | 分类 |
| --- | --- | --- |
| `night-schedule.mjs` 窗口（start/end/wakes，跨午夜）、每日上限 12、minGapMin=20、状态 pending→fired/skipped/missed、漏窗不补 | V2 SchedulingService（interval/cron） | **部分对等**：V2 有调度；夜间窗口/wakes/上限语义缺 |
| **质量反馈** `qualityByWindow`（按近 30 天窗口内容质量均分分配额外 wakes）→ 作息自适应 | 无 | **可离线缺口**：内容质量反馈作息分配（纯逻辑） |
| QQ sleep/wake（socialV2.wake diving 模式、preSleepWaitMs=300000 睡前观察、推荐睡眠 5–120min、@/名字/关键词/问题/poke 触发）；`wake-policy.js` 硬直接原因绕过日配额 | 无 | **可离线缺口**：睡前观察/唤醒策略（纯逻辑）；tick+唤醒提示需账号 |
| `greeting-schedule.js` 早安问候纯逻辑（scheduled/catchup/retry/tomorrow，3h grace，08:00） | 无 | **可离线缺口**：早安问候规划 |
| `bili-share-scheduler.js` 8 固定时刻分享 bili 精华到群 | V2 scheduling + deliveries | **部分对等**：V2 有调度/投递；固定分享时刻表缺（需账号） |

## 组 5 — 渠道集成

| 参考渠道 | V2 对应 | 分类 |
| --- | --- | --- |
| QQ（SnowLuma2 OneBot WS/HTTP，39 qq_* 工具，3 MCP，chat/closed-agent/reserved/reserved2 模式） | V2 QQ（OneBotTransport）+ 路由档位 | **部分对等**（需实机账号验收）：V2 QQ 链路对等；SnowLuma 第三方运行时不捆绑 |
| B 站（watch/score/comment/dynamic/DM/live，34 命令） | V2 bilibili_read/prepare_publication + 写审批/送达核查 | **部分对等**（需实机账号验收）：V2 B 站读写对等；观看筛选/双日记/直播等见 K17/K18 |
| 桌宠 pet（外部 surfpet Electron，loopback+一次性 token，文本/语音气泡，focus 模式） | 无 | **需实机验收**：Electron 桌宠载体 |
| 贴吧 tieba（PlatformRegistry status:planned，UI 可见不可用）；微博已移除 | 无 | **占位**（参考亦未实现） |
| 语音 voice（外部 surfvoicelocal/cloud） | V2 media（ASR/transcript） | **部分对等**（需引擎/模型）：V2 有 ASR；TTS 语音气泡缺 |
| 平台适配器接口 `{id,status,capabilities,readMemory,writeMemory,promptBlock}` + `privacy.js` 隐私门 | V2 DeliveryPort 注入 + agent 隔离 | **部分对等**：V2 有多端口注入；统一平台适配器/渠道隐私门缺 |

## 可离线补齐缺口清单（后续实现批次，非 K15 实现）

按价值排序（均为纯逻辑引擎，可离线桩测；live 投递/话语需模型/账号）：

1. **时间衰减遗忘/巩固引擎**（参考 memory-card）：衰减层级 + 重要性启发式 + 召回强化 + 每日遗忘 tick + 容量告警/auto-compact + 记忆级保护。基于 V2 memory + maintenance 框架，独立批次（先持久化/授权合同）。
2. **主动动机引擎**（参考 purpose-engine）：需求→目标假设→驱动衰减→情绪评估→动作规划 + 观察账本/反馈回路 + 外联硬门。基于 V2 scheduling + K04 委派 + 模型调用，独立批次（重大，先授权/预算/quiet 合同）。
3. **分层人格组合 + 跨渠道统一卡**（参考 composePersonaLayers/sanitize）：scene/daily 分层 + 预算裁剪 + 渠道 sanitize + 角色包 epoch 切换。纯逻辑，独立批次。
4. **作息质量反馈 + 睡前观察/唤醒 + 早安问候**（参考 night-schedule/wake-policy/greeting-schedule）：质量反馈 wakes 分配 + 唤醒策略 + 问候规划。纯逻辑，独立批次。

## 需账号/实机验收清单（不可离线宣称对等）

- QQ/B 站真实送达质量、SnowLuma 运行时、主动外联实际投递。
- 桌宠 Electron 载体、语音 TTS 引擎。
- 渠道级记忆隐私 scope 的实际跨渠道隔离效果。
- 真实模型话语生成质量与人格一致性。

## 完成条件与边界

K15 交付本逐项审计记录（五组分类 + 可离线缺口清单 + 账号验收清单），证据为参考固定提交引擎清单与 V2 people/memory/scheduling/K08 对照。**V2 已有 persona/people、memory CRUD+语义+显式整理+来源删除屏障、K08 回合后学习、调度/投递框架、QQ/B 站链路**；**时间衰减遗忘、主动动机/自主行为、分层跨渠道人格、作息质量反馈闭环**仍有重大缺口（参考核心 IP 为离线纯逻辑三件套）。**审计完成不等于完整自主行为对等**：可离线缺口与账号验收项保留为后续任务，`whole_core_parity_complete` 与 `whole_reference_feature_audit_complete` 仍 false（K16… 领域参考审计未核完）。本批不新增引擎/迁移/路由，不改运行中服务与数据库。