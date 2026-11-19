# V2 B站收件箱读取 bilibili_read operation=msgfeed（K18 可离线缺口读广度实现）

## 目的与参考

实现 [K18 B站审计](V2_BILI_WHALE_AUDIT.md) 记录的**读 API 广度缺口：收件箱（reply「回复我的」/ at「@我的」）只读读取**。参考 `xuexi/dsh-bili-whale`（固定提交 `8b0009f8`）`tools/lib/msgfeed.mjs:26-61 pullFeed(kind)` 经 `/x/msgfeed/{kind}?platform=web&build=0&mobi_app=web` 取 `data.items[]`（`id`/`reply_time`|`at_time`/`user.nickname`|`mid`/`item.title`|`source_content`|`business_id`|`subject_id`|`root_id`|`source_id`|`uri`）。V2 已有 `bilibili_read` account/video/comments/replies/search/whois/danmaku/popular/ranking/following/favorites/tags/history，本批补 **msgfeed 只读 operation**，让模型发现「有人找我们」（回复/@ 通知）。

**关键参考经验（msgfeed.mjs:5-10）**：官方收件箱 `item.title` **不总是**新回复正文（语义无文档），收件箱只用于**发现**，权威正文须回读楼层。V2 据此把 `title`/`source_content` 仅作**有界发现提示**暴露，合同明示权威回复正文须经 `replies` operation 回读楼层，**绝不把收件箱字段当作权威正文**。

**不含**cursor 翻页/去重（首页有界）、回复写、resolveLatestReply 楼层回读（V2 已有 `replies` operation 承担）。

## 实现（V2 四层）

- **纯领域 `domain/bilibili.py`**（既有模块加函数，**无新源文件**）：`msgfeed_kind(value)`（仅 reply/at，否则 `bilibili_read_invalid` 422）；`msgfeed_rows(data, limit)`——`data.items` 缺失=正常空、非列表/超 `FEED_MAX_ROWS=50`/行非 dict/`id` 非 int 显式 502（`bilibili_response_invalid`）不静默丢弃；有界字段 `id`(int)/`time`（`reply_time` int 优先否则 `at_time` int 否则 None）/`nickname` 去标记 ≤100/`mid` int→str/`business_id` int|None/`subject_id`|`root_id`|`source_id` int→str/`uri`|`title`|`source_content` 去标记 ≤300（发现提示）；`has_more=len(rows)>=limit`。
- **`adapters/bilibili.py`**：`_msgfeed(client, kind, limit)` 复用泛型 `_get`（无 WBI）GET `/x/msgfeed/{kind}` `{platform:"web", build:0, mobi_app:"web"}`→`{"feed":"msgfeed","kind":kind, **msgfeed_rows}`；`_read` op 集合增 msgfeed、**target=kind**（reply/at，经 `msgfeed_kind` 校验，非 BV 豁免也非 BV 必填，独立 elif 分支）、nav 后 `/view` 前分派；凭据权威 CAS/admission≤8/`_slots`/`asyncio.timeout(20)`/closing 守卫沿用。
- **`application/bilibili.py`**：`READ_SPEC` enum+desc 增 msgfeed（desc 明示发现提示/权威正文经 replies；`read`/`invoke` 通用透传）。
- **`adapters/http.py`**：`BilibiliReadInput.operation` Literal 增 msgfeed（REST 通用 handler 不变；本批仅改该 Literal 块，`@@ -2283` background_review 区既有格式偏差非本批引入未重格式化）。

## 保持的合同

模型工具/REST/网关三面接线；`bilibili_read` 已在 `RETRIEVAL_TOOLS` **路由不变**；只读无写事务（`no_delivery` 守卫）；登录必需（nav）；Agent/凭据隔离与 CAS；有界（limit 1–20、行 ≤50、nickname ≤100、uri/title/source ≤300）；非法响应显式 502 不臆造；**收件箱字段仅发现提示，权威正文经 replies 回读**（继承参考经验，V2 锚点纪律）。**无新表/迁移/路由/worker/持久层改动**；`_get`/`_get_list` 未改。

## 验证

- 新增 `agent/tests/v2/integrations/test_v2_bilibili_msgfeed.py` **22 项**（2 kind + 6 kind 拒绝参数化 + 4 领域 + 5 malformed 参数化 + 5 网关 MockTransport：reply/at/非法 kind 零网络/空/坏响应 + 1 spec/http 暴露）首轮全过（1.23 秒，format 后复跑 22 过 1.14 秒）。
- bilibili 读调用方专项 **256 过 / 0 失败**（msgfeed/history/tags/favorites/following/feed/search/whois/danmaku/gateway/response_bounds/merged，14.85 秒）；发表链测试不在本批读侧范围（其既有 `database_unavailable` 由他会话未提交持久层编辑引起，前批已 scoped-stash 实证）。
- V2 strict `pyrightconfig.strict.json` **410 文件零错误/警告**（无新源文件 include 不变）；ruff check/format 通过于本批 4 文件（http.py 仅改 Literal 块，既有偏差未触）。
- `project_index` 运行（`declared_tools` 索引不变）；**聚合 `docs/index` 暂缓提交**（他会话未提交持久层编辑污染全局 `source_sha256`）。

## limits 与后续

仅 reply/at 收件箱首页（无 cursor 翻页/重叠去重，参考 pullFeed 多页去重未移植）；`title`/`source_content` 为**不可靠发现提示**，权威回复正文须经 `replies` operation 回读楼层（V2 已有）；不含回复写/resolveLatestReply 专用链；真实收件箱送达需账号另验。本批仅实现 K18 读广度收件箱缺口，**K18 读 API 广度缺口（搜索/whois/弹幕/feed/关注/收藏夹/标签/历史/收件箱）已全部实现**；其余 K13–K18 可离线缺口（写广度/自主观看/受控分享/K16 zoom/HSV/K13 browser_dialog/cron/K15 遗忘/K17 信笺）仍列后续独立批次。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。