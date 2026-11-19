# V2 B站收藏夹读取 bilibili_read operation=favorites（K18 可离线缺口读广度实现）

## 目的与参考

实现 [K18 B站审计](V2_BILI_WHALE_AUDIT.md) 记录的**读 API 广度缺口：收藏夹（owner-created favorite folders）只读读取**。参考 `xuexi/dsh-bili-whale`（固定提交 `8b0009f8`）`tools/commands/fav.mjs:87 defaultFavId` 经 `apiGet` `x/v3/fav/folder/created/list-all?up_mid=selfMid` 取 `data.list[].id/title`。V2 已有 `bilibili_read` account/video/comments/replies/search/whois/danmaku/popular/ranking/following，本批补 **favorites 只读 operation**，让模型列出所有者创建的收藏夹（id/标题/媒体数）。

**不含**收藏写（`fav.mjs:165 resource/deal`，属写广度需所有者审批另批）、收藏夹内资源列表（`resource/list` 另批）、历史/msgfeed 收件箱/tag。

## 实现（V2 四层）

- **纯领域 `domain/bilibili.py`**（既有模块加函数，**无新源文件**）：`fav_folder_rows(data, limit)`——`data.list` 缺失=正常空、非列表/超 `FEED_MAX_ROWS=50`/行非 dict/`id` 非 int 显式 502（`bilibili_response_invalid`）不静默丢弃；有界字段 `id`(int)/`title` 复用 `search_text` 去 HTML 标记 ≤300/`media_count` int|None；`has_more=len(rows)>limit`（list-all 为完整列表，超出 limit 才有更多）。
- **`adapters/bilibili.py`**：`_favorites(client, mid, limit)` 复用泛型 `_get`（无 WBI）GET `/x/v3/fav/folder/created/list-all` `{up_mid: mid}`→`{"feed":"favorites", **fav_folder_rows}`；`_read` op 集合增 favorites、**无目标豁免 BV**（同 account/popular/ranking/following，`up_mid` 取 `uid(value["account"])` 所有者自身）、nav 后 `/view` 前分派；凭据权威 CAS/admission≤8/`_slots`/`asyncio.timeout(20)`/closing 守卫沿用。
- **`application/bilibili.py`**：`READ_SPEC` enum+desc 增 favorites（`read`/`invoke` 通用透传）。
- **`adapters/http.py`**：`BilibiliReadInput.operation` Literal 增 favorites（REST 通用 handler 不变；本批仅改该 Literal 块，`@@ -2280` background_review 区既有格式偏差非本批引入未重格式化）。

## 保持的合同

模型工具/REST/网关三面接线；`bilibili_read` 已在 `RETRIEVAL_TOOLS` **路由不变**；只读无写事务（`no_delivery` 守卫）；登录必需（nav）；Agent/凭据隔离与 CAS；有界（limit 1–20、行 ≤50、title ≤300）；非法响应显式 502 不臆造。**无新表/迁移/路由/worker/持久层改动**。

## 验证

- 新增 `agent/tests/v2/integrations/test_v2_bilibili_favorites.py` **16 项**（5 领域 + 6 malformed 参数化 + 4 网关 MockTransport + 1 spec/http 暴露）首轮全过（1.22 秒，format 后复跑 16 过 1.17 秒）。
- bilibili 读调用方专项 **203 过 / 0 失败**（favorites/following/feed/search/whois/danmaku/gateway/response_bounds/merged，14.82 秒）；发表链测试不在本批读侧范围（其既有 `database_unavailable` 由他会话未提交持久层编辑引起，前批已 scoped-stash 实证）。
- V2 strict `pyrightconfig.strict.json` **410 文件零错误/警告**（无新源文件 include 不变）；ruff check/format 通过于本批 4 文件（http.py 仅改 Literal 块，既有偏差未触）。
- `project_index` 运行（`declared_tools` 索引不变，`bilibili_read` 为既有工具增 operation）；**聚合 `docs/index` 暂缓提交**（他会话未提交持久层编辑污染全局 `source_sha256`）。

## limits 与后续

仅所有者创建收藏夹列表（list-all），不含收藏夹内资源列表/收藏写（`resource/deal` 需审批另批）/历史/msgfeed 收件箱/tag；完整列表无分页（`has_more=len>limit`）；真实收藏夹送达需账号另验。本批仅实现 K18 读广度收藏夹缺口，其余 K13–K18 可离线缺口仍列后续独立批次。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。