# V2 B站观看历史读取 bilibili_read operation=history（K18 可离线缺口读广度实现）

## 目的与参考

实现 [K18 B站审计](V2_BILI_WHALE_AUDIT.md) 记录的**读 API 广度缺口：观看历史（watch history）只读读取**。参考 `xuexi/dsh-bili-humanism`（固定提交 `2ec50417`）`lib/writer.mjs:13,301-304` 经 `get(routes.history, {type:"archive", ps:30})` 取 `data.list[].history.oid/cid` 与 `progress`（-1=看完）；`xuexi/surfcore` `tools/lib/pref-scan.mjs:369-388` 经 `x/web-interface/history/cursor?ps=30` 取 `data.list[].title/author_mid/tag_name/history.business`。V2 已有 `bilibili_read` account/video/comments/replies/search/whois/danmaku/popular/ranking/following/favorites/tags，本批补 **history 只读 operation**，让模型读取所有者观看历史（标题/作者/进度/视频身份）。

**不含**历史进度上报写（`/x/v2/history/report`，属写广度需审批另批）、msgfeed 收件箱（另批）。

## 实现（V2 四层）

- **纯领域 `domain/bilibili.py`**（既有模块加函数，**无新源文件**）：`history_rows(data, limit)`——`data.list` 缺失=正常空、非列表/超 `FEED_MAX_ROWS=50`/行非 dict 显式 502（`bilibili_response_invalid`）不静默丢弃；有界字段 `title`/`author_name` 复用 `search_text` 去标记 ≤300/≤100、`author_mid` int→str、`progress` int|None（保留 -1=看完语义）、嵌套 `history.business`(str|None)/`history.oid`→aid(int|None)/`history.cid`→cid(int|None)、`history.bvid` 仅在合法 BV 时取值否则 None（**不臆造身份**）；`has_more=len(rows)>=limit`。
- **`adapters/bilibili.py`**：`_history(client, limit)` 复用泛型 `_get`（无 WBI）GET `/x/web-interface/history/cursor` `{type:"archive", ps:20}`（首页 archive，mirrors writer）→`{"feed":"history", **history_rows}`；`_read` op 集合增 history、**无目标豁免 BV**（同 account/favorites，所有者自身历史）、nav 后 `/view` 前分派；凭据权威 CAS/admission≤8/`_slots`/`asyncio.timeout(20)`/closing 守卫沿用。
- **`application/bilibili.py`**：`READ_SPEC` enum+desc 增 history（`read`/`invoke` 通用透传）。
- **`adapters/http.py`**：`BilibiliReadInput.operation` Literal 增 history（REST 通用 handler 不变；本批仅改该 Literal 块，`@@ -2282` background_review 区既有格式偏差非本批引入未重格式化）。

## 保持的合同

模型工具/REST/网关三面接线；`bilibili_read` 已在 `RETRIEVAL_TOOLS` **路由不变**；只读无写事务（`no_delivery` 守卫）；登录必需（nav）；Agent/凭据隔离与 CAS；有界（limit 1–20、行 ≤50、title ≤300）；非法响应显式 502 不臆造；视频身份只来自源 `history` 嵌套（aid/cid 确证，bvid 合法才取）。**无新表/迁移/路由/worker/持久层改动**；`_get`/`_get_list` 未改。

## 验证

- 新增 `agent/tests/v2/integrations/test_v2_bilibili_history.py` **14 项**（5 领域 + 4 malformed 参数化 + 4 网关 MockTransport + 1 spec/http 暴露）首轮全过（1.13 秒，format 后复跑 14 过 1.15 秒）。
- bilibili 读调用方专项 **234 过 / 0 失败**（history/tags/favorites/following/feed/search/whois/danmaku/gateway/response_bounds/merged，14.61 秒）；发表链测试不在本批读侧范围（其既有 `database_unavailable` 由他会话未提交持久层编辑引起，前批已 scoped-stash 实证）。
- V2 strict `pyrightconfig.strict.json` **410 文件零错误/警告**（无新源文件 include 不变）；ruff check/format 通过于本批 4 文件（http.py 仅改 Literal 块，既有偏差未触）。
- `project_index` 运行（`declared_tools` 索引不变）；**聚合 `docs/index` 暂缓提交**（他会话未提交持久层编辑污染全局 `source_sha256`）。

## limits 与后续

仅所有者 archive 观看历史首页（type=archive ps=20），不含历史进度上报写/msgfeed 收件箱；cursor 翻页未实现（`has_more=len>=limit` 首页有界）；bvid 仅在源 `history.bvid` 合法时返回否则 None（aid/cid 确证身份）；真实历史送达需账号另验。本批仅实现 K18 读广度历史缺口，其余 K13–K18 可离线缺口仍列后续独立批次。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。