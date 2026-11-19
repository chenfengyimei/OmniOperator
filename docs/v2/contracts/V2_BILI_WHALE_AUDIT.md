# V2 B 站 API/主动观看/受控分享逐项审计（K18）

## 范围与判定原则

本批是**只读逐项审计**，不改源码、不新增工具/表/迁移/路由。对照固定提交参考 `xuexi/dsh-bili-whale`（`8b0009f8ce2ebdc421afcb2e727b19ac18912ec6`，v0.4.6，Node/JS DSH 插件，唯一依赖 `qrcode`），核对「自主『有日常生活的鱼』：生物钟唤醒→理解视频→写**两本日记**→每日 ≤3 条 @ 分享給所有者」的全集 API/主动观看/受控分享是否完整对等。

**关键判定纪律（对照 [总对标合同](V2_AGENT_CORE_PARITY.md)「不把 V1 的实现当作 V2 已有功能」）**：

- **V2 四层 B 站链**：`domain/bilibili.py`（WBI 签名/CDN allowlist/表单选择器/target/mention 规则）+ `application/bilibili.py`（`BilibiliService`：`bilibili_read`/`bilibili_prepare_publication`/profile）+ `adapters/bilibili.py`（网关：API 读 + 浏览器表单投递，`BilibiliPort` 扩展 `DeliveryPort`）+ 共享 `action_review`/`delivery` outbox + `media`（K16 视频/字幕/流读）。**这是 V2 模型/所有者实际可见面**。
- **V1 遗留 B 站包**：`tools/bilibili/{api,publishing,tools,video_source,write_tools,frames,asr}.py` 经 **V1 组装根** `bootstrap.py:84-88,646,682-684` 注册进 V1 `ToolRegistry`（`BiliAPI`/`BilibiliPublisher`/`BrowserForms`/`write_budget`）。**不计 V2 对等**，只作「可离线缺口的已有 V1 实现」记录。
- 参考仓库 `agents.md`/RULES/prompts 等指令样文本一律当作**不可信数据**汇总（其发布/配额/默认开关规则是参考项目自身，**不作为本会话指令**）。未拉取远程、未运行真实账号/模型/设备/网络。

## V2 四层 B 站面（已对等基础）

- **读（API + WBI）**：`bilibili_read`（`READ_SPEC`，operation ∈ `account`/`video`/`comments`/`replies`，`config_id` 凭据、`target`、`limit≤20`、timeout 25、resource bilibili）；`domain/bilibili.py` `wbi_keys`（nav img/sub）+ `sign_wbi`（公开 MIXIN 表 + md5 `w_rid`，与参考同规范）；`subtitle_url`/`stream_url` **固定 CDN host allowlist**（`SUBTITLE_HOSTS`/`STREAM_HOST_SUFFIXES`，https-only、无任意 URL/端口/账密）；`connection` 要求 `driver=bilibili` + 固定 `API` + **显式 cookie `credential_ref`** + `account=uid`。**凭据权威 CAS**：`read` 前后 `bilibili_read_authorized` 比对 `authority`/`revision_id`，变更即 `bilibili_credential_changed`；admission ≤8 pending + closing 守卫 + `asyncio.shield`。媒体视频/字幕/流读经 `media`（K16）。
- **写/表达（仅 comment + private_message，浏览器表单 + 所有者审批 + outbox）**：`bilibili_prepare_publication`（`PREPARE_SPEC`，kind ∈ `comment`/`private_message`，从**已保存 assistant 消息** `message_id` 取正文，`target`、`mentions≤10`、`key` 幂等）→ `prepare` 落库生成 publication intent（`body_hash`/`profile_revision_id`/`deliveries[{id,state,fingerprint}]`），**绝不自行发送或批准**（`health.publication_approval_required=True`）。投递经共享 `action_review` 审批 + `delivery` outbox（`DeliveryPort`）。执行走 **`ADAPTER="bilibili-ui/v2"` 已审浏览器表单选择器**（`Form` pydantic strict：account/target/editor/submit/records/record_id/record_author/record_text/challenge/logged_out/mention_*；`forms_value` 仅 comment/private_message、enabled 需已审表单、选择器 ≤300、mention 三字段同验、payload ≤32768），**非直接 API POST**。`target` 限 `www.bilibili.com/video/BV…(?reply_id=uid)` 与 `message.bilibili.com/#/whisper/mid<uid>`；`mentions_value` 正文 1–2000 字符（`MAX_CONTENT`）、≤10 有序不重叠精确 `@name` 范围、uid 绑定、一名一 uid；`digest` sha256 规范 JSON 幂等指纹。
- **登录/凭据**：显式 cookie `credential_ref`（凭据库 `domain/credentials.py`）+ 已审浏览器 profile（`save_profile` enabled/forms/revision/headless）。
- **限额/冷却/审批/送达**：共享 `action_review` 写审批 + `delivery` outbox 限额/冷却/恢复/送达核查（K13 已审计框架）；凭据权威变更拒绝；幂等 `key`/`body_hash`/`fingerprint`；unknown 送达不自动重发（共享结算纪律）。

**结论**：V2 已有「API WBI 读（account/video/comments/replies）+ 媒体视频/字幕/流读 + comment/private_message **准备→所有者审批→outbox→已审浏览器表单投递** + 凭据权威 CAS + CDN allowlist + mention 精确范围 + 幂等指纹 + 共享限额/冷却/送达核查」。**但读 API 广度窄于参考、写仅两种、无自主生物钟观看/双日记/沉默衰减/候选打分/偏好学习、无直播**。

## 全集逐项对照

### (a) 读 API

- **参考**（`tools/lib/bili-api.mjs:apiGet`，WBI `signWbi:10`，自动 `view`→`wbi/view`、`playurl`→`wbi/playurl`）：`x/web-interface/view`（meta）、`nav`（身份+WBI keys）、`x/player/wbi/v2`+`x/v2/subtitle/web/view`（字幕，**AI 字幕需登录**）、`comment.bilibili.com/{cid}.xml`（弹幕 deflate）、`x/v2/reply` sort=2 热/0 时（**匿名可读**）+`reply/reply`（楼中楼）、`wbi/search/type`（搜索）、`popular`/`ranking/v2`/`polymer/web-dynamic/v1/feed/all`（feed 源）、`x/space/wbi/acc/info`（whois）、`x/v3/fav/folder/created/list-all`（收藏夹）、`x/msgfeed/reply|at|like`（收件箱）、`x/relation`（DM 关系）、`x/tag/archive/tags`。缓存仅 view+acc/info 24h、nav 10min、400 LRU。
- **V2**：模型读仅 `account`/`video`/`comments`/`replies`（≈ nav/view/reply）；媒体读 video/streams/subtitle（K16）。**无弹幕/搜索/feed/收藏夹/历史/msgfeed 收件箱/space whois/直播/tag 作为模型读工具**。
- **判定**：核心读（view/nav/reply/字幕/流）**已对等**（WBI 同规范 + CDN allowlist + 凭据 CAS，更严）；**读 API 广度 = 可离线缺口**（弹幕/搜索/feed/收藏夹/历史/收件箱/whois/tag 可加读 operation + 域规则，真实送达需账号）；缓存策略（24h/10min/LRU）= 可离线小缺口。

### (b) 写 / 表达 API

- **参考**（均需 `--dry`/`--go`）：**已实现** comment（`x/v2/reply/add` WBI+csrf）、comment-del（`reply/del`）、fav（`x/v3/fav/resource/deal`）、dm（`web_im/send_msg`）、dynamic post/rm/edit/image、danmaku-send（`api.live.bilibili.com/msg/send`）。**未实现**（`README:77-78`）：like/coin/follow/triple/gift/charge；comment-action/hate/top/report 仅文档。配额：share ≤3/日、同 BV 7 日去重、score≥13/20+reason≥8+rank1；fav reason≥6/≤3 日/30 日每 BV/kind≥2 需 `--better`；comment 每人 30 日一次（**所有者豁免**、uid 未知 fail-closed）；dm **1 收件人/日、仅所有者、互关 attribute===6 硬编码**、msg≤300、**稳定 device-id 复用**。Cookie：SESSDATA+bili_jct 必需，buvid3 comment 可选但 dm/dynamic/danmaku 必需。幂等：`traffic.mjs:116` 操作指纹去重、`:189` unknown-send 禁自动重发。
- **V2**：仅 **comment + private_message**（≈ dm），经准备→所有者审批→outbox→**已审浏览器表单**（非 API POST）；mention 精确范围 + 幂等 key/body_hash/fingerprint + 凭据 CAS + unknown 不重发。**无 fav/dynamic/danmaku-send/comment-del**；like/coin/follow/gift/charge 参考也未实现。
- **判定**：comment/dm **受控投递原语对等或更严**（V2 所有者审批门 + 已审浏览器选择器 + 真实浏览器账号身份核验，比参考直接 API POST + 硬编码互关更严）；**写广度（fav/dynamic/danmaku/comment-del）= 可离线缺口**（需新增已审表单/操作 + 配额，真实发送需账号）；自主 share 打分/配额/沉默衰减属 (d) 缺口。V2 无「所有者豁免/每人 30 日/互关 attribute===6」等参考特定配额硬编码（V2 走统一所有者审批，设计取舍不同）。

### (c) 登录 / 凭据 / 风险暂停

- **参考**：QR（`auth.js:QrAuth` passport generate/poll TTL 180s，codes 86101/86090/86038/0）+ 手动 cookie 粘贴；`credentials.js` 原子 verify-before-write、**有待审草稿时禁切换账号**、0600/icacls；`redirect:'error'`、QR origin allowlist passport/account.bilibili.com；风险暂停 `traffic.mjs:167` 手动 codes [12015,-105,-101,401]→每账号手动阻断，仅 `traffic resume --go` 清除；信任围栏 fail-closed。
- **V2**：显式 cookie `credential_ref`（凭据库）+ 已审浏览器 profile（headless）；凭据权威 CAS（变更拒绝）；共享凭据保护/审批。**无 QR 登录工具、无参考特定风险码手动暂停/resume CLI**（V2 走凭据权威变更 + 共享 delivery unknown/暂停纪律）。
- **判定**：cookie 凭据 + 权威 CAS + 保护**原语对等**；QR 登录 = 需账号/网络（浏览器 UI 可承载，V2 profile 已 headless 浏览器）；参考特定风险码手动暂停/resume = 可离线缺口（策略）；真实登录/风控属需账号验收。

### (d) 主动观看（生物钟 / 双日记 / 沉默衰减 / 候选打分 / 偏好学习）

- **参考**：`night-plan.mjs:makePlan` 窗内随机唤醒、**skipChance 0.3**、minGap 10min、maxWakes 12、maxVideos 5；**双日记** `whale-diary.md`（致所有者信）+ `whale-diary-private.md`（自我「偏好校准」）；**沉默衰减** `quota.mjs:silence:62-81` 所有者无反应 ≥3 日→share 上限线性降、**第 7 日=0**（日记仍写），normal/quiet/faint 带注入唤醒提示；反馈自动从所有者回复+点赞检测、like_time/reply-hash 去重；候选打分 `feed.mjs:268` log-弹幕密度×3+偏好命中×4+学习 token boost≤7.5、**刻意排除播放量**、junk 过滤 play<300&dm<3。
- **V2**：`SchedulingService` interval/cron（**所有者侧**，K13）+ delivery + K08 回合后学习 + people/memory。**无自主生物钟唤醒/双日记/沉默衰减/候选打分/feed 偏好学习**（与 K15 surfcore 主动动机缺口重叠）。
- **判定**：**可离线缺口**（night-plan/share-policy/silence-decay/feedback/candidate-scoring/preference-learning 是纯策略/算法，可经 V2 四层 + 调度/投递/K08 实现）；真实唤醒/送达/反馈检测需账号+模型质量。调度框架原语 V2 已有（所有者侧），自主策略缺。

### (e) 受控分享 / 投递

- **参考**：outbox `pending-replies.json`、审批门 `index.js:418 /pending/approve`（take→comment --go→**失败重排队**）；DM 可靠投递 receipt+ack（`dm-await.mjs`、`session-delivery.js:retainUntilAck`）；限额 `traffic.mjs:7`（读 600/h·3000/d、写 12/h·30/d、gap 1500ms、写冷却 120s、dm 60s）；notify 后端 none/desktop/webhook/command/dm/stdout/file（`$MSG` shell 注入已移除）。
- **V2**：准备→**所有者审批**（`action_review`）→`delivery` outbox（限额/冷却/恢复/送达核查/retain-until-ack，K13 框架）；幂等指纹 + unknown 不重发 + 凭据 CAS。
- **判定**：受控投递/审批/outbox/送达核查/限额冷却**原语对等或更严**（V2 统一所有者审批 + 共享 outbox + unknown 结算）；参考特定限额数值（600/h 等）/notify 多后端 = 可离线策略缺口（V2 限额在共享 delivery 配置）；真实送达质量需账号。

### (f) 直播

- **参考**：**已实现** room info `get_info`/`GetDMConfigByGroup`/`gethistory`、**WebSocket 实时弹幕**（`danmaku.mjs:151-248` brotli/deflate）、send、`danmaku-await.mjs` 监听；gift **只读** SEND_GIFT 事件、**无 gift/charge 发送**；独立开关、仅所有者或 @-only 范围。
- **V2**：**无直播房间观看/实时弹幕 WebSocket/gift 读**。
- **判定**：**可离线缺口 + 需账号/网络**（直播 API/WebSocket 协议可离线实现策略与解析，真实连接/弹幕需账号网络）；gift/charge 发送参考也未实现。

## 可离线缺口清单（列后续批次，K18 不实现）

纯策略/算法/协议解析/读 operation 扩展，可经 V2 四层（domain/application/adapters/composition + 凭据 CAS/CDN allowlist/所有者审批/outbox/有界）实现，V1 `tools/bilibili` 已有部分实现可移植（**不复用 V1 ToolRegistry/bootstrap**）：

1. 读 API 广度：弹幕（cid.xml deflate）、搜索（wbi/search/type）、feed（popular/ranking/dynamic）、收藏夹/历史、msgfeed 收件箱（reply/at/like）、space whois、tag；缓存策略（view/acc 24h、nav 10min、LRU）。
2. 写广度：fav/dynamic（post/rm/edit/image）/danmaku-send/comment-del 的已审表单/操作 + 配额（真实发送需账号）。
3. 主动观看策略：night-plan 随机唤醒（skipChance/minGap/maxWakes/maxVideos）、双日记（致所有者+自我偏好校准）、沉默衰减（≥3 日降 share 上限、第 7 日=0、日记仍写、normal/quiet/faint 带）、反馈检测去重、候选打分（log-弹幕密度+偏好命中+学习 token、排除播放量、junk 过滤）、feed 偏好学习（与 K15 动机引擎协同）。
4. 受控分享策略：share ≤3/日 @、同 BV 7 日去重、score/reason/rank 门、fav/comment/dm 特定配额；notify 多后端（去除 shell 注入）。
5. 风险暂停策略：参考特定风险码 [12015,-105,-101,401] 每账号手动阻断 + resume（V2 走凭据权威+共享暂停，可补策略）。
6. 直播：room info/history/WebSocket 实时弹幕解析（brotli/deflate）/send/gift 只读事件/独立开关/所有者或 @-only 范围（真实连接需账号网络）。
7. AI-share 守卫（参考默认 OFF、阈值硬编码 30 与文档 60 漂移、模型协议遵从+误报率**从未在线验证**）：若移植须独立合同 + 真实验证，不照抄漂移阈值。

## 需账号 / 网络 / 模型质量 / 计划未实现验收（另验）

- **需真实 B 站账号/网络**：所有 apiGet/fetch 端点、QR 登录、deps/selftest（真实 nav）、全部写发送、直播 WebSocket、真实送达/风控/弹幕质量。参考 `network` allowlist + `redirect:'error'`/`manual`、凭据只发 api.bilibili.com（V2 CDN allowlist + 固定 API 同纪律）。
- **需真实模型质量**：debrief 六路合成+read_image、bili-watch 帧→read_image、ai-share 标注、日记/评论/DM 著作、feed 偏好学习质量（host 模型著，插件只校验结构/锚点/配额）。
- **计划/阻断/从未实机**：like/coin/follow/gift/charge **参考未实现**；comment-action/hate/top/report 仅文档；**AI-share 守卫默认 OFF、模型协议遵从+误报率明确从未在线验证**（`AI-SHARE-TRIAL.md:39`）；DM-history 修复「本地待发布」；**文档/代码漂移**（阈值 60 文档 vs 30 代码）。参考 `README:394` 强调测试只证逻辑，**非**真实 DSH 版本兼容或 B 站 API 可用性。

## 完成条件

K18 审计完成**不宣称全集 API/主动观看/受控分享对等**：V2 四层已有 API WBI 读（account/video/comments/replies）+ 媒体读 + comment/private_message 准备→所有者审批→outbox→已审浏览器表单投递 + 凭据权威 CAS + CDN allowlist + mention 精确范围 + 幂等指纹 + 共享限额/冷却/送达核查；读 API 广度/写广度（fav/dynamic/danmaku/del）/自主生物钟观看/双日记/沉默衰减/候选打分/偏好学习/直播/AI-share 守卫为**可离线缺口或需账号网络**，分别列后续批次与另验。V1 `tools/bilibili` 实现**不计 V2 对等**。like/coin/follow/gift/charge 参考亦未实现。`whole_core_parity_complete`、`whole_reference_feature_audit_complete`、`whole_v2_complete` 保持 false（K01 会话/lineage 缓存范围仍 partial、K13–K18 记录的可离线缺口尚未实现、真实账号/设备/模型质量与 72 小时验收待办）。K18 为清单末项领域参考审计；后续择机实现 K13/K14/K15/K16/K17/K18 记录的可离线缺口（独立批次，先授权/持久化合同后执行接线），并补 K01 缓存范围，整个 V2 开发冻结后统一全量与实机验收，其他对标项不提前关闭。