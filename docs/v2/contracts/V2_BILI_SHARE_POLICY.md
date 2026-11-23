# V2 B站受控分享策略设计合同（K18 缺口 #4：share 配额/去重/门控/AI-share 守卫，决策 D6）

状态：**设计合同（design_contract_recorded，纯文档，无源码/测试/迁移/路由/worker 改动，2026-11-23）**。**未实现**。承接 [K18 审计](V2_BILI_WHALE_AUDIT.md) (e) 与 [剩余差异决策登记](V2_REMAINING_DECISIONS.md) D6；本册把参考 `dsh-bili-whale` 的分享资格门（`lib/share-policy.mjs`）、每日/去重/沉默衰减（`commands/quota.mjs`）、通知后端（`commands/notify.mjs`）与 AI-share 守卫（`lib/ai-share.mjs`、`AI-SHARE-TRIAL.md`）映射到 V2，并明确哪些**不移植**（避免不实对等）。主动观看属 D5，直播属 D7，写广度属 D4。

## 参考事实（固定 `8b0009f8`）

- **资格门**（`lib/share-policy.mjs`）：`share.enabled` 未关闭；`reason` 至少 8 字；`score` 必填 0–20 且 `>= minScore`（默认 13）；`rank` 必须为 1（只分享排名第一候选）；否则拒发。
- **配额/去重**（`commands/quota.mjs`）：每日分享 `shareDaily` 默认 3；**同一 BV 7 天内只能分享一次**；有效上限 = `min(shareDaily, silence.cap)`（沉默衰减见 D5）；分享型**不看白名单**；正文必须**以 `@主人` 开头**（否则拒发）；`--share` 必须与 `--auto` 同传（自主决定）；拦截发生在发送与额度记账之前；额度账本按本地日统计。
- **通知**（`commands/notify.mjs`）：`none/desktop/webhook/command/dm`（兼容旧 stdout/file）；`$MSG` 走 shell 的占位已于 2026-10-18 弃用（**命令注入风险**，来源是陌生评论/弹幕）；command 后端用 `execFileSync(c, [...argv, text])` **不走 shell**；webhook 按域名适配飞书/钉钉/企业微信/QQ/WorkBuddy；推送失败不记成功。
- **AI-share 守卫**（`lib/ai-share.mjs` + `AI-SHARE-TRIAL.md`）：默认关闭；只拦**评论区 @ 分享**（`--share --auto`、无 parent、type=1、`share.via=comment`）；先复用 comments 缓存，否则热评+最新各≤20 条、按 ID 去重交替采样，首次交模型 ≤20 条（每条 ≤400 字）；疑似比例距阈值 ≤10 个百分点时要求扩样到 ≤40；样本 TTL 6 小时、按本地登录会话+视频隔离；模型逐条给 `suspected/other/uncertain`，`suspected` 必须附 ≥8 字理由，**代码核对 sampleId/ID/分类完整性并自行计算比例**，不接受模型直接“通过”；样本 <20 条=不足则允许；样本/评估缺失、达到阈值、`uncertain` 足以影响结论 → 暂缓；暂缓发生在发送与额度记账之前；同视频同会话 6 小时通知去重，推送失败可重试；**阈值漂移**：文档/设置写 60%，代码 2026-10-24 主人定阈值写死 30%（`AI_SUSPECT_THRESHOLD=30`，注释称“不再做成设置项”）；**模型协议遵从与误报率从未在线验证**。

## V2 现状与映射

| 参考 | V2 原语 | 缺口 |
|---|---|---|
| 分享=评论区 @ 主人 | `bilibili_prepare_publication(kind=comment, target=BV…, mentions=[@owner])` → 所有者审批 → outbox → 已审表单（非 API POST） | 无分享资格门/配额/同 BV 去重/正文必须 @owner 的分享专用校验 |
| 每日额度/7 日去重 | `delivery` outbox（重试/unknown/恢复）+ 计划中的通用配额账本（D4-Q2） | 无 share 账本/窗口计数；B 站无任何配额 |
| 沉默衰减 | D5 合同 W4（纯领域 `silence_cap`） | 未实现 |
| 通知后端 | owner HTTP/工作台事件 + `delivery` outbox（QQ/渠道）；V2 **无** webhook/desktop/command 后端 | 无多后端；webhook/command 属新出网/执行面 |
| AI-share 守卫 | 无 | 整个能力缺失 |
| 回复每人 30 日/私信 1 收件人 | V2 统一所有者审批；无参考特定数值配额 | 不打算照搬数值（见下） |

## 设计决策（需所有者选定）

### S1 分享资格门（纯领域，先做）

`share_gate(enabled, reason, score, min_score=13, rank, share_daily=3, silence_cap, ledger, target_bv, now)`：

1. `share.enabled` 未开 → `share_disabled`；2. `reason.strip()` <8 字 → `share_reason_required`；3. `score` 必须精确 int 0–20 且 `>= min_score` → `share_score_low`；4. `rank` 必须精确 1 → `share_rank_required`；5. 当日已分享 ≥ `min(share_daily, silence_cap)` → `share_daily_exhausted`（cap=0 时明确“日记照写、分享停止”）；6. 同 `target_bv` 7 日内已分享 → `share_duplicate_bv`；7. 正文必须**以 `@owner` 开头**且 mention 范围精确绑定 owner uid（V2 既有 `mentions_value` 语义）→ 否则 `share_mention_required`。
- 全部**在 prepare 之前**判定（拒发不记账）；账本只记真正进入投递的分享。

### S2 分享执行路径（授权边界）

- **选项 A（推荐先做）**：分享仍走「准备 → 所有者审批 → outbox → 已审表单」，S1 是其前置门。自主 agent 只能**准备**分享，所有者逐条批准；额度/去重/衰减防止刷屏。零授权机制改动，满足「准备≠发送、审批≠成功」。
- **选项 B（自主分享）**：所有者显式开启「自主分享 ≤N/日」并接受 S1 全部门控后，允许 run 内准备并由系统在预算内**自动批准**（仍走 outbox/已审表单/unknown 不重发）。这改变逐条审批纪律，属独立授权合同 + 所有者批准 + 真实送达验收，**不建议与 A 混批**。
- **选项 C**：不分享，只写日记/候选（放弃 (e) 对等，不宣称）。

### S3 账本与配额落库

- 需要 `bilibili_share_ledger`（agent+account+bv+shared_at+delivery/publication 引用+status），用于 7 日去重与按本地日计数；或者并入 D4-Q2 通用配额账本（若 Q2 批准）。**迁移号由 A 分配**；冻结 SQL/identity/keep 触发器按既有纪律。
- 计数只认**已进入投递**的分享；`unknown` 保守计入（避免重复分享），所有者 review 后可修正；不把被拒/被撤回的算入。
- 本地日按所有者时区（V2 既有 `Timezones`/`Clock`）。

### S4 通知映射

- 首版：**owner 可见事件 + 工作台/HTTP 状态 + 可选既有 `delivery` 到已绑定所有者渠道**；不新增 webhook/desktop/command 后端。
- 若要 webhook/command：独立安全合同（webhook 域名 allowlist + 禁内网/元数据地址 + 模板不插值；command 必须 `execFileSync` 语义、参数数组、**绝不经 shell**、无 `$MSG`）。参考已因注入风险弃用 shell 占位，V2 不引入。
- 推送失败不记成功；通知去重键（同分享/同视频/窗口）。

### S5 AI-share 守卫

- 参考默认 OFF、只拦评论区 @ 分享、阈值 30/60 漂移、**从未在线验证**。V2 选项：
  - **S5-A（推荐：暂不移植）**：本批不实现，审计明确登记「参考特有、未验证、阈值漂移」，**不宣称该子项对等**；待所有者决定是否投入真实验证。
  - **S5-B**：移植为 owner 侧两步检查——`comments` 读采样（热/最新各≤20、ID 去重、每条≤400 字）→ 模型逐条标注 → **代码**核对完整性并算比例 → 达阈值/不确定/缺样暂缓 `bilibili_prepare`；默认 OFF、固定阈值（所有者定一个数，不照抄 30/60）；需独立合同 + 真实评论样本误报率验收。
  - **S5-C**：可配置阈值（与参考“产品原则不开放微调”相反，需所有者明确选择）。
- 若移植：样本按「登录会话+BV」隔离、TTL 6 小时、不落原文到长期记忆、暂缓发生在发送与记账之前、通知去重。

### S6 不照搬的参考数值

- `comment --once-per-person 30 日`（回复防拉锯，仅对“回复某人”生效，主人豁免、uid 未知 fail-closed）：V2 走统一所有者审批，**不移植**数值规则。
- 私信 `1 收件人/日、仅 owner、互关 attribute===6、≤300 字`：V2 `private_message` 已限 target 为 `message.bilibili.com/#/whisper/mid<uid>` 与正文 ≤2000；是否加 1/日与互关门属产品决策（参考是硬编码；V2 建议由所有者审批 + 可选配额账本，不硬编码互关）。
- 读 600/h·3000/d、写 12/h·30/d、gap 1500ms、冷却 120s：与 D4 的 Q2 配额账本一并评估，不散落硬编码。

## 安全与不变量

- 准备≠发送、审批≠成功；始终经所有者审批（S2-B 例外需独立批准）；**绝不直接 API POST**；已审浏览器表单。
- unknown 不重发；分享账本对 unknown 保守计数；恢复/撤销/凭据换代语义沿用既有 outbox/CAS。
- 正文必须 @owner 精确范围；mention uid 绑定；正文 ≤2000 既有上限；target BV 校验。
- 不新增授权机制（S2-A）；不新增出网/执行后端（S4 首版）；无新表除非 S3 账本获批（A 迁移）。
- 时间/去重窗口用所有者时区，注入 Clock；跨 agent/account 隔离。

## 测试计划（实现批）

- 纯领域：S1 每个拒绝码/边界（reason 7 vs 8、score 12/13/20/越界/布尔、rank 1 vs 2、日上限 min(shareDaily,silence)、7 日去重边界、cap=0、@owner 开头与 mention uid 绑定）；账本计数只认已投递、unknown 保守、跨日/时区边界。
- 应用集成（真实隔离 SQLite + fake worker）：prepare 前置拒绝零 outbox；审批四态；outbox unknown 不重发且账本保守；并发分享日上限不超发（事务/唯一键）；沉默 cap 联动；owner 可见事件。
- 若 S5-B：样本去重/不足/阈值/扩样/不确定/过期/评估校验/暂缓不发不扣额度/通知失败重试与去重。
- 真实账号送达与误报率另验；未实现前不宣称 (e) 对等。

## 明确不做

- AI-share 守卫（S5-A 推荐）与阈值 30/60 的照抄；webhook/desktop/command 通知后端；自主分享（S2-B）在首批；回复 30 日/私信 1 日等参考硬编码数值；主动观看（D5）、直播（D7）、写广度（D4）。

## 关联

- K18 审计：[V2_BILI_WHALE_AUDIT](V2_BILI_WHALE_AUDIT.md) (e)。
- 决策登记：[V2_REMAINING_DECISIONS](V2_REMAINING_DECISIONS.md) D6。
- 相关合同：[V2_BILI_WRITE_BREADTH](V2_BILI_WRITE_BREADTH.md)（写链/配额 Q2）、[V2_BILI_PROACTIVE_WATCH](V2_BILI_PROACTIVE_WATCH.md)（沉默衰减 W4）、[V2_BILIBILI](V2_BILIBILI.md)（既有发布链）。
- 证据：[v2-bili-share-policy-design.json](../../evidence/v2-bili-share-policy-design.json)。
