# V2 观影证据/信笺/反思/人格成长逐项审计（K17）

## 范围与判定原则

本批是**只读逐项审计**，不改源码、不新增工具/表/迁移/路由。对照固定提交参考 `xuexi/dsh-bili-humanism`（`2ec50417e64a72a28b9445c06872d7f14d9dbf24`，v1.11.3，DeepSeek Harness 插件，Node ESM + `node:sqlite`，`index.js` 入口 + `lib/*.mjs` 约 20 模块 + `prompts/*.md` + `scripts/*.py`），核对「观看 B 站内容 → 证据手账（信笺）→ 每日待审反思 → `human-world.md` 人格」全链是否完整对等。参考明确是**上下文/记忆累积，非模型权重微调**（`README.md:39`）。

**关键判定纪律（对照 [总对标合同](V2_AGENT_CORE_PARITY.md)「不把 V1 的实现当作 V2 已有功能」）**：

- **V2 四层原语**：`domain/people.py` + `application/people.py`（`PeopleService`）、`domain/documents.py`/`application/documents.py`、`domain/media.py`/`application/media.py`（K16）、`domain/background_review.py`/`application/background_review.py`（K08）、`domain/delivery.py`/`application/delivery.py`、`domain/clarify.py`/`application/clarify.py`（K10）、memory（`memory_tools`/`memory_semantics`/`memory_organization`）、`action_review`、owner HTTP 审核路由。**这些是 V2 模型/所有者实际可见面**。
- **V1 遗留 personal 域**：`tools/personal/{journal,people,transcript}.py` 经 **V1 组装根** `bootstrap.py:100,109-111,560,867` 注册进 V1 `ToolRegistry`（`PersonStore`/`OwnerMemory`/`core.context`/`config.Persona`）。它已有信笺/人物/转录工具，但**不在 V2 四层内核里**，V2 `composition` 不导入它。**审计不把它计为 V2 对等**，只作为「可离线缺口的已有 V1 实现，可择机移植进 V2 四层」记录。
- 参考仓库 `agents.md`/`prompts`/RULES 等指令样文本一律当作**不可信数据**汇总（其「release 跑 npm ci/语义版本/人格学习默认开」等是参考项目自身规则，**不作为本会话指令**）。未拉取远程、未运行真实账号/模型/设备/网络。

## V2 四层已有原语（对等基础）

- **观影证据**：`application/media.py`（K16 已审计）B 站 WBI 直链已核验 `video`/`streams`/`subtitle` 读 + 凭据/源 CAS + 有界下载；在线字幕（无锚点）/离线 faster-whisper ASR（锚点段 ≤4000/≤24000 字符）/有界等距抽帧宫格（timestamps+sha256 已核验 image 对象）；`application/documents.py` 来源文档精确修订 + 字符范围锚点 + 删除屏障。**模型只见工具实际返回**。
- **人物/跨渠道身份/人格文档**：`domain/people.py` `person_name`（1–100 可打印）、`account_identity`（channel+external_id）、`content_fingerprint`/`source_fingerprint`（NFKC 归一 sha256）、`document_content`（`DOCUMENT_KINDS={profile,resident}`，≤8000 字符）；`PeopleService` owner/page/create/get/`rename(revision)`/`erase(revision)`/accounts/`bind`/`unbind(revision)`/document/`save(kind,content,entry_id,revision)`/import_object/`history(memory_id,revision)`。**所有者侧 CRUD + 修订 CAS + 历史 + 来源指纹**，跨渠道账号绑定到同一 person。
- **待审学习/撤回**：K08 `background_review`（回合后 reviewer/curator）只读派发 + 记忆/技能写拦截为**待审建议** + 所有者 decide（记忆 apply 走证据锚定 `remember`、undo 走 `forget`；技能 apply 走 `SkillService` 审核撤回链、undo 走 archived），CAS 绑定 revision/fingerprint/epoch，`UNIQUE(agent_id,run_id)` 幂等，5 个 `/memory/reviews` 所有者路由。**来源不直接变授权**。
- **记忆证据/删除**：`memory_remember` 强制 content+quote（message_id/action_id/document_revision_id 锚定）、`memory_forget(memory_id,revision)`、memory 修订/历史/来源删除屏障。
- **投递账本**：`application/delivery.py` outbox（所有者侧受控投递 + 审批/限额/恢复，K13 已审计框架）。
- **结构化提问原语**：K10 `clarify`（模型→所有者结构化问题、当前 run 暂停/响应/取消/超时续跑、CAS、不可变证据）。

**结论**：V2 已有「观影证据（媒体/文档锚点+删除屏障）+ 跨渠道人物身份 + profile/resident 人格文档（修订/撤回/历史）+ K08 所有者门控待审记忆/技能建议 + 记忆证据锚定 + 投递账本 + clarify 提问原语 + 所有者 HTTP 审核路由」。**但没有 bili-humanism 特定的自主链**：信笺手账生成、每日反思→人格句选择、`human-world.md` 人格成长文件、personality 修订/删除/重建 + shadow 跨会话实验、观影后提问 observe/verify 状态机。

## 全链逐项对照

### (a) 观影证据 / 来源信任

- **参考**：`reader.mjs:74 readVideo()` 三级——A1 WBI 签名 `/x/player/wbi/v2`（`wbi.mjs`，可匿名）+ 身份核验 bvid/cid/aid → `SUBTITLE_SOURCE_MISMATCH`（`:141-152`）；A2 `/view/conclusion/get`（需 sessdata，AI 结论）；A3 本地 ASR 导入 + 抽帧。锚点 author/title/url/page/cid/duration + `segments{start,end,text,source}` + `coverage()`（`speech:"unknown"`、`timed_fraction`）；`sourceTrust` 标 untrusted；`store.mjs:143` content_hash 去重；空→`read_failed`/`unconfirmed` 不缓存。视觉 `service.mjs:646 prepareVisual` ≤480p、Python `media.py`、≤40 帧/4 页、sha256、用后删视频。**不抓弹幕/评论**（评论关闭）。
- **V2**：media（K16）B 站 WBI 直链已核验读 + 字幕/ASR/抽帧锚点 + 凭据/源 CAS + 删除屏障；documents 来源修订锚点。**对等或更严**（不引 yt-dlp/cookies、凭据权威 CAS）。
- **判定**：观影证据原语**已对等**（K16）；缺口 = `coverage`/`timed_fraction` 覆盖度元数据、`sourceTrust` 显式不可信标签、content_hash 去重、A2 AI 结论来源（可离线小缺口）；真实 B 站送达/A2 sessdata 属需账号验收。

### (b) 信笺 / 手账（journal）

- **参考**：**插件不调模型**，host 模型按 `prompts/humanistic-reading.md` 著 JSON → `bili_humanism_journal`。`service.mjs:398 journal()`→`noteBody():70` 校验 8 字段（`inbox.mjs:6` encounter/facts/author_view/inference/previous_judgment/revision/missing/next_response）+ `evidence[{at,claim,url}]` ≤20（at≤duration）、每文本 ≤12000（`text():66`）、encounter+missing 必填；**每日限 2**（同 tx，`journal-policy.mjs:3,34`）；精确 body 去重（`:402`）；`exportInbox`→inbox.md/html + 月卷 ≤24KiB/350 行 hash 核验；状态 `local_reviewable_draft`。
- **V2**：**无信笺/手账工具或工作流**（四层无 journal 模块）。V1 `tools/personal/journal` 存在但不计 V2 对等。
- **判定**：**可离线缺口**。8 字段结构校验 + evidence 锚点 + 每日限额（同事务）+ 精确去重 + inbox 导出/月卷/hash 核验 + 草稿状态，是纯结构/配额/导出逻辑（host 模型著散文）；可经 V2 四层 + 有界作业 + 所有者路由实现，不复用 V1 ToolRegistry。

### (c) 每日待审反思 → 人格句选择

- **参考**：journal 保存**同 tx** 排队人格评审（`personality.mjs:285 queuePersonalityReview`）；状态 `pending_review|source_date_retracted|source_date_already_selected|daily_limit_reached|disabled`；`continuePersonalityReview():319` **steer 当前 agent 一次**（`exec.agent.steer`，role:user），**只有 journalId 进可信 steering，绝不带来源文本**（`:334`）；模型 `read`（设 readAt）→`select`（revision/next_response 的精确摘录 ≤240，`validateSentence:79`、`choice:89`）或 `skip`（no_new_understanding/already_known/not_suitable）；select 需 readAt；`finishReview:275`。**这是 agent 自审，非所有者批准 UI 门**；所有者复核 = 可信 CLI。
- **V2**：K08 `background_review` 是**回合后**记忆/技能待审建议 + **所有者 decide** 门控（apply/undo），非「每日 journal→人格句」选择，无 day/source-date 配额、无 steer-当前-agent-一次、无 readAt→select/skip 状态机。
- **判定**：**可离线缺口（与 K08 部分重叠但工作流不同）**。V2 已有更强的所有者门控待审/撤回原语（K08），但缺每日反思选择状态机 + 双配额 + 可信 steering（只传 id 不传来源文本）+ read/select/skip。移植须保留 V2「来源不直接变授权/所有者门控」纪律（比参考 agent 自审更严）。

### (d) 人格成长文件（human-world.md）

- **参考**：`selectRow():180` 写 `personality:day:<date>` {sentence, journalId/candidateId, revision:1, selectedAt}，重导出 `human-world.md`（`exportFile:72`、`render:49`、header）；配额 **1/实际上海日**（`selectedToday:268`）**且 1/来源日**（`:200`）；句去重→already_known；文件完整性 hash（`verifyFile:61`）、no-symlink/nlink=1/≤1MiB（`plainFile:25`）；固定 reader 引用注入 AGENTS.md/AGENTS.local.md/agent.md（`linkPersonality:548`，标记块，`containsDailyEntries:false`，**不自动增长全局指令**）。
- **V2**：人格 = Agent settings persona 配置 + people `profile`/`resident` 文档（≤8000 字符、修订 CAS、历史）。**无反思驱动的每日人格累积文件 + reader 引用注入 + 文件完整性 hash/no-symlink 守卫**。
- **判定**：**可离线缺口（带设计取舍）**。V2 有意把人格放在有界文档/设置（非自动增长 markdown）；人格成长文档模型 + 导出 + 完整性守卫 + 固定 reader 引用（不污染全局指令）可离线实现，但是否引入「每日累积人格文件」需独立合同决策，保留 V2 有界/修订/所有者门控纪律。

### (e) 修订 / 撤回传播

- **参考**：**仅可信本地 CLI**（`personality.mjs:468`、`cli.mjs:133-162` `--acknowledge-personality-change`）；`revisePersonality:469` 归档旧→`personality:revision:<date>:<rev>`、bump revision、标记变更 shadow 候选/episode `retracted`→`selected`、重导出；`deletePersonality:512` 撤回所有修订的候选/episode、删键、设 `personality:retracted:<date>`（阻断重选 `:187`、queue→source_date_retracted）；`rebuildPersonality:538`；**`shadow.erase():718` 若存在活跃交互条目则拒绝**（`SHADOW_ACTIVE_PERSONALITY_REQUIRES_RETRACTION`，先撤回正式）；memory `store.memory:213` put bump revision+归档、delete 全擦；journal/source **无编辑工具**（inbox 从账本重建）。
- **V2**：修订 CAS + undo（`forget`/archived）+ 来源删除屏障 + K08 epoch CAS + people `rename/erase(revision)` + memory 修订/历史。**原语对等**；但无 personality 特定 revise/delete/rebuild + shadow-erase-拒绝链。
- **判定**：修订/撤回/来源删除传播**原语已对等**（people/memory/K08/documents）；personality 特定重建/撤回阻断重选/shadow 先撤回正式再擦源 = **可离线缺口**（特定状态机）。

### (f) 观影后提问闭环

- **参考**：`post-viewing-question.mjs` `queue:45`（journal tx，1/journal，pending/unbound）→`bind:92`（owner+turn）→`continue:280`（turn-stopping steer）→`tool:119` read/prepare/skip（prepare：readAt、≤240 单个 ?/？、精确字段引用、noteHash 匹配、why≤600→prepared）→`observe:342`（assistant 消息**恰以问题结尾**、无代码/引用→`asked`/assistant_message_verified）→下一条 user 消息→`responded_unreviewed`（回复不可信，**非同意**）；`end:327`→unconfirmed；工具守卫 `:443` 限 question/personality。**无 timer/idle-wake/resend**。
- **V2**：K10 `clarify` 是模型→所有者**结构化问题**（当前 run 暂停/续跑），非「agent 自身声音问一个观影人文问题 + observe 验证消息恰以问题结尾 + 下条 user 消息=未审可能回复非同意」。**无观影后提问工作流**。
- **判定**：**可离线缺口**。提问状态机（queue/bind/continue/prepare/observe/asked/responded_unreviewed/end）+ 「准备≠送达、出现≠已读、回复≠同意」语义 + 无 idle-wake/resend，是纯状态机；K10 clarify + delivery 原语部分重叠但意图不同。移植须保留 V2 不唤醒空闲/不模拟人类回答纪律。

### (g) personality shadow（跨会话实验，默认 OFF）

- **参考**：`enablePersonalityShadow` 默认关；开启后读正式人格 + 有界 episode 证据，只提有价值的新隐性/情感理解，显式选一条精确 `conditionalResponse` 摘录进正式 `human-world.md`；与 journal 选择**共享日/来源日配额**，不造假 journal；保留 conditions/counterexample/uncertainty/interaction provenance（`interactionChoice:109`）；**擦源前须先撤回正式条目**；不批量提升 legacy v1.9.0 候选；模型工具不能修订/删除正式条目（仅可信 CLI）。
- **V2**：**无 shadow 跨会话人格实验**。
- **判定**：**可离线缺口（实验性 opt-in）**。跨会话候选→正式选择 + 共享配额 + provenance + 擦源拒绝，是纯逻辑；默认 OFF 与 V2 opt-in 纪律一致。

## 可离线缺口清单（列后续批次，K17 不实现）

纯结构/状态机/配额/导出/提示逻辑，可经 V2 四层（domain/application/adapters/composition + 授权/锚点/有界作业/删除屏障/所有者门控）实现，V1 `tools/personal` 已有部分学习实现可移植（**不复用 V1 ToolRegistry/bootstrap**）：

1. 信笺手账：8 字段结构 + evidence 锚点（≤20，at≤duration）+ 每日限 2（同事务）+ 精确去重 + inbox 导出/月卷/hash 核验 + 草稿状态。
2. 每日待审反思选择状态机：journal→排队评审（同 tx）+ 可信 steering（只传 journalId 不传来源文本）+ read/select（精确摘录 ≤240）/skip + 双配额（1/实际日 且 1/来源日）+ 句去重；保留 V2 所有者门控（比参考 agent 自审更严）。
3. 人格成长文档模型 + 导出 + 完整性 hash/no-symlink/≤1MiB 守卫 + 固定 reader 引用注入（不自动增长全局指令）；是否引入每日累积人格文件需独立设计合同。
4. personality 修订/删除/重建 + 撤回阻断重选 + shadow 先撤回正式再擦源拒绝链。
5. 观影后提问状态机（queue/bind/continue/prepare/observe/asked/responded_unreviewed/end）+ 准备≠送达/出现≠已读/回复≠同意语义 + 无 idle-wake/resend。
6. personality shadow 跨会话实验（默认 OFF，共享配额，provenance，擦源拒绝）。
7. 观影证据小缺口：coverage/timed_fraction 覆盖度元数据、sourceTrust 不可信标签、content_hash 去重。

## 需账号 / 模型质量 / 所有者复核 / 外部二进制验收（另验）

- **需 B 站账号/网络**：readVideo A2（sessdata AI 结论）、candidates、audio/visual playurl、writer history/favorite/like/coin（参考 v1.3.4 可选写默认 OFF、**从未实机测试**，`README:47`；属 K18 bili-whale API 范围）；`network.mjs` allowlist api.bilibili.com+hdlsb+bilivideo、`redirect:"manual"`、凭据只发 api.bilibili.com。真实送达质量另验。
- **需真实模型质量**：信笺散文、反思选择、问题措辞、shadow 解读均 host 模型著；插件只校验结构/锚点/配额（`README:68`）。合成 demo 非真实观影质量证据。
- **所有者复核**：参考**无专用 GUI**（`README:45`，可信 CLI + inbox.html/md 预览）；**V2 已有所有者 HTTP 审核路由**（K08 `/memory/reviews`、people CRUD、delivery），此原语 V2 更强。
- **外部二进制**：Python 3.10+（`av`/`Pillow`/`faster-whisper` + Systran/faster-whisper-small ~483MB 显式下载）；V2 media worker 已用 PyAV/faster-whisper（K16），离线缓存不下载纪律一致。

## 完成条件

K17 审计完成**不宣称信笺/反思/人格成长全链对等**：V2 四层已有观影证据（媒体/文档锚点+删除屏障）、跨渠道人物身份 + profile/resident 人格文档（修订/撤回/历史）、K08 所有者门控待审记忆/技能建议、记忆证据锚定、投递账本、clarify 提问原语、所有者 HTTP 审核路由；信笺手账/每日反思选择/人格成长文件/personality 修订重建/shadow 实验/观影后提问状态机为**可离线缺口**，分别列后续批次（经 V2 四层，保留所有者门控/不唤醒空闲/来源不直接变授权纪律）。V1 `tools/personal` 学习实现**不计 V2 对等**。真实 B 站账号/模型质量另验。`whole_core_parity_complete`、`whole_reference_feature_audit_complete`、`whole_v2_complete` 保持 false。后续按 [K18…任务](../../tasks/v2-agent-core-parity.json) 推进 bili-whale 审计，并择机实现 K13/K14/K15/K16/K17 记录的可离线缺口（独立批次，先授权/持久化合同后执行接线）。