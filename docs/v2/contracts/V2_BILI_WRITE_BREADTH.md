# V2 B站写扩面设计合同（K18 缺口 #2：comment-del / fav / dynamic，决策 D4）

状态：**设计合同（design_contract_recorded，纯文档，无源码/测试/迁移/路由/worker 改动，2026-11-22）**。**未实现**。承接 [K18 审计](V2_BILI_WHALE_AUDIT.md) (b)「写广度」与 [剩余差异决策登记](V2_REMAINING_DECISIONS.md) D4；本册把每个候选动作落到 V2 四层的表单/审批/配额/去重/unknown/恢复/验证面，并记录一项新发现（现有已授权写路径**没有配额**）。`danmaku-send` 在参考里是直播 API（`api.live.bilibili.com/msg/send`），归 D7 直播批，不在本册。

## 现有已授权写路径核对（comment / private_message）

| 保证 | 现状（基 `0793f254`） |
|---|---|
| 审批 | `bilibili_prepare_publication` 只准备（从已保存 assistant 消息取正文，`PREPARE_SPEC` 明言 never send/approve）；`bilibili_prepare` → `delivery_create` 落 outbox intent；所有者 `POST /agents/{agent_id}/deliveries/{delivery_id}/review` → `DeliveryService.decide`/`delivery_decide` 批准；投递前 `_authorize`/`delivery_claim` 复核；`health.publication_approval_required=True`。 |
| 去重 | `bilibili_publications.key` 每 agent 唯一（`idempotency_conflict`）；`body_hash` 绑定正文；`delivery_lookup`/`delivery_already_exists`/`duplicate_ack_required`/`delivery_review_required`；delivery fingerprint + reviewed profile revision/fingerprint。 |
| unknown | `delivery_settle` 仅把 `not_sent + retryable` 放回 `retry_wait`（`2**seq` 秒退避，`MAX_ATTEMPTS=3`）；`unknown` 保持 unknown、不进 `delivery_candidates`（**不重发**）；再次尝试须父 intent 已 review 且 `acknowledge_duplicate`。 |
| 恢复 | `delivery_recover`/`delivery_release_boot` 按 boot 释放；`delivery_cancel_run`；configuration/credential 变更经 `delivery_credential_changed`/revoke；启动恢复不重放 unknown。 |
| 凭据/表单 | 凭据权威 CAS；已审 headless 表单（pydantic strict、选择器 ≤300、mention 三字段同验、payload ≤32768）；target 仅 `www.bilibili.com/video/BV…` 与 `message.bilibili.com/#/whisper/mid…`；投递走已审浏览器表单，**非直接 API POST**。 |
| **配额** | **缺**：V2 无通用投递日/小时限额或动作冷却；只有 `MAX_ATTEMPTS=3` 退避与 **QQ 专属** `cooldown_s`；B 站读/写均无参考的 600/h、12/h、120s、share≤3/日、30 日/视频等配额。K18 审计原文「共享限额/冷却」应更正为「重试上限/退避 + QQ 专属冷却」。 |

**结论**：审批/去重/unknown/恢复四保证成立且可复用；**配额是 D4 的 P0 先决项**，也是当前 comment/private_message 路径相对任务要求（已授权写路径须覆盖配额）的实际缺口。

## 动作逐个设计（均复用现有 prepared→owner approval→outbox→已审表单链）

### A. comment_delete（参考 `comment-del`）

- **参考**：`x/v2/reply/del`（csrf）。V2 **不走 API POST**，走已审 UI：记录行 → 删除菜单 → 原生 confirm → 列表刷新。
- **输入**：`message_id`（删除理由不需要，正文不重发）、`target`（`BV:reply_id` 或记录 URL，复用既有 target 校验）、`record_id`/`author_uid`（与记录作者一致）、`expected_url` CAS。
- **表单新增字段**：记录行定位、删除入口、confirm 复用既有 `browser_dialog`（accept）；选择器沿用 ≤300 与 pydantic strict。
- **验证**：同一 records 选择器下记录消失 + dialog 已响应（`verification=page.url`）；不宣称远端删除成功之外的状态。
- **去重/状态**：按 `record_id` 幂等；目标态删除（已不存在 → 明确 `comment_already_absent` 还是 unknown 需决策）；**unknown**：confirm 已派发但列表未变/页面丢失 → unknown，不重放。
- **配额**：建议不设日限（删除减少外部影响），每记录一次由幂等键约束；是否沿用参考「评论 30 日/视频」删除配额见 Q2。

### B. favorite_add（参考 `fav`）

- **参考**：`x/v3/fav/resource/deal`。V2 走视频页收藏面板 UI。
- **输入**：`target` BV、`folder_id` + `folder_title`（必须来自只读 `favorites` 的返回，模型/所有者显式选择，不臆造）、`expected_url` CAS。
- **表单新增字段**：收藏按钮、面板、目标文件夹行（标题+属性）、确认按钮；验证：面板显示已收藏且目标夹选中，并**回读目标夹内容**确认记录出现。
- **读侧前置**：现有 `favorites` 只列文件夹，**没有文件夹内容读取** → 需新只读 operation `fav_resources`（`x/v3/fav/resource/list`，folder id → 有界资源行）作为验证面；属只读扩展（同 K18 读广度模式），可先批交付。
- **配额/去重**：参考 reason≥6/≤3 日/30 日每 BV/kind≥2 需 `--better`；V2 建议最小集：`(account, bvid, folder_id)` 幂等、同一 BV 跨夹重复需 `favorite_duplicate_ack` 显式确认、日限可选（Q1/Q2 决策）。
- **unknown**：面板提交已派发但面板/列表未确认 → unknown，不重放。

### C. dynamic_post / dynamic_delete / dynamic_edit（含图片单独子批）

- **参考**：动态发布/删除/编辑/图片（`dynamic` 命令组）。
- **输入**：正文 ≤2000（复用 `MAX_CONTENT` 与 control-byte 规则）；`dynamic_id`（删除/编辑）+ `expected_body_hash` CAS；图片需先经 `ImageService` 注册 image object，再由受管路径 `set_input_files` 上传（worker 新增能力，约束：仅注册对象、≤16 MiB、类型 png/jpeg/webp、路径不可由模型任意指定）。
- **表单新增字段**：正文编辑器、发布按钮、删除菜单+confirm（复用 `browser_dialog`）、编辑态、图片 input 与处理等待。
- **验证**：发布/编辑后经新只读 `dynamic_list`（空间动态首屏/按 id）确认；删除后确认消失。
- **读侧前置**：`dynamic_list`（参考 `polymer/web-dynamic/v1/feed/space`）只读 operation，为验证面。
- **配额**：参考无明确 dynamic 配额；V2 建议 owner 审批 + 每 message 一个 dynamic intent（幂等），日限可选（Q1/Q2）。
- **图片上传**是跨层新面（本地文件→页面），安全影响最大，建议独立子批 + 所有者确认路径约束后再做。

## 读侧前置（只读，建议先批 D4-B0）

- `fav_resources`：收藏夹内容（`x/v3/fav/resource/list`，bounded rows），验证收藏。
- `dynamic_list`：空间动态（`space_history`，已按本批 [V2_BILI_DYNAMIC](V2_BILI_DYNAMIC.md) 交付：固定 VC host、严格 id/尽力文本、≤50 卡、不缓存），验证发布/删除/编辑。
- 两者与 K18 已交付读广度同模式（domain 解析 + READ_SPEC enum + 网关 `_get` + 凭据 CAS + 有界），**只读、无写授权、无迁移**；`dynamic_list` 已交付，`fav_resources` 仍待 D4-B0。

## 配额机制（P0 决策）

- **现状缺口**：现有 comment/dm 无任何配额（见上表），参考的 share≤3/日、评论 30 日/视频、fav 3 日/BV、dm 1/日、读 600/h 等全部未实现。
- **选项**：
  - **Q1（零迁移）**：仅 owner 审批 + 幂等 + `MAX_ATTEMPTS`/退避。适合低频人工批准；无法满足自主行为/高频场景的硬上限与 7 日/30 日去重窗口。
  - **Q2（推荐若接自主行为）**：通用 delivery/action 配额账本（新表：agent/driver/account/action/bucket + 窗口计数 + 冷却；迁移由 A 分配编号），统一承载 share≤3/日、评论 30 日、fav 3 日/BV、读预算等；实现 `try_consume` 语义与审计。
  - **Q3**：适配器内存限速——重启丢失，不满足安全要求，不采用。
- **决策**：先定 Q1/Q2；若 Q2，需 A 协调迁移号与表设计（本册给出字段面建议，不在本批建表）。

## 分批建议与依赖

1. **D4-B0（只读前置）**：`fav_resources` 只读 operation（`x/v3/fav/resource/list`，无授权面、无迁移）；`dynamic_list` 已交付（[V2_BILI_DYNAMIC](V2_BILI_DYNAMIC.md)）。
2. **D4-B1**：`comment_delete`（最小写面，复用 browser_dialog；含配额 Q1/Q2 决策结果）。
3. **D4-B2**：`favorite_add`（含 `favorite_duplicate_ack`）。
4. **D4-B3**：`dynamic_post`/`dynamic_delete`（文本）。
5. **D4-B4**：`dynamic_edit`/图片上传（独立安全审查）。
6. **D7**：直播 danmaku-send（`api.live.bilibili.com/msg/send`）不并入本册。

每批独立合同→实现→专项；共享接口/迁移/公共 CI 由 A 协调。

## 不变量（每批必须保持）

- 准备≠发送、审批≠成功；**绝不直接 API POST**；已审浏览器表单/操作；owner-only 审批。
- 幂等 `key`/`body_hash`/fingerprint；unknown **不重发**；恢复不重放未复核 unknown；已批准 intent 不因新动作追溯失效。
- 凭据权威 CAS；表单/选择器审核；target/正文/mention 有界；跨 agent/config 隔离。
- 无新授权机制；无新表除非配额 Q2 获批准（A 迁移）；无第二内核/状态库。

## 测试计划

- 每动作（真实隔离 SQLite + fake worker + 已审表单）：审批四态（approved/denied/revoked/expired）、幂等 key 冲突、`body_hash`/正文变更、target 失效、unknown 不重放、取消/重启恢复、credential rotation、配额（若 Q2）、跨 agent/config 隔离。
- 真实 Chromium 本地夹具（假评论记录/收藏面板/动态编辑器页面）验证选择器、confirm、回读验证链（复用 browser_dialog 既有 9 项模式）。
- 真实 B 站账号发送/删除/收藏/动态另验（不可离线宣称送达）。

## 明确不做

- 未实现任何动作；like/coin/follow/gift/charge **参考亦未实现**，不列。
- 受控分享策略（share ≤3/日/同 BV 7 日/notify 后端/AI-share 守卫）→ D6。
- 直播（含 danmaku-send/WS/gift 只读）→ D7。
- 主动观看 → D5；前缀重构 → D1；代码沙箱/CDP → D2/D3。
- 现有 comment/private_message 行为不变（除 Q2 批准后统一加配额）。
- 真实账号验收、全量、72 小时不在本批。

## 关联

- K18 审计：[V2_BILI_WHALE_AUDIT](V2_BILI_WHALE_AUDIT.md) (b)。
- 决策登记：[V2_REMAINING_DECISIONS](V2_REMAINING_DECISIONS.md) D4（本册为其设计合同）。
- 既有写合同：[V2_BILIBILI](V2_BILIBILI.md)；审批/投递：`V2_CAPABILITIES`、K13 投递框架。
- 证据：[v2-bili-write-breadth-design.json](../../evidence/v2-bili-write-breadth-design.json)。
