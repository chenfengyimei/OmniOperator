# V2 B站视频标签读取 bilibili_read operation=tags（K18 可离线缺口读广度实现）

## 目的与参考

实现 [K18 B站审计](V2_BILI_WHALE_AUDIT.md) 记录的**读 API 广度缺口：视频标签（public video tags）只读读取**。参考 `xuexi/dsh-bili-whale`（固定提交 `8b0009f8`）`tools/commands/video-content.mjs:38` 经 `get` `x/tag/archive/tags?bvid=BVID` 取 `tag.data.map(t => t.tag_name)`（**data 直接是数组**）。V2 已有 `bilibili_read` account/video/comments/replies/search/whois/danmaku/popular/ranking/following/favorites，本批补 **tags 只读 operation**，让模型按 BV 读取视频公开标签（tag_id/标签名）。

**不含**标签订阅/关注写、历史/msgfeed 收件箱（另批）。

## 实现（V2 四层）

- **纯领域 `domain/bilibili.py`**（既有模块加函数，**无新源文件**）：`tag_rows(data, limit)`——`data` 为标签数组，None/空=正常空、非列表/超 `FEED_MAX_ROWS=50`/行非 dict/`tag_id` 非 int 显式 502（`bilibili_response_invalid`）不静默丢弃；有界字段 `tag_id`(int)/`tag_name` 复用 `search_text` 去 HTML 标记 ≤100；`has_more=len(rows)>limit`。
- **`adapters/bilibili.py`**：新增**隔离的 `_get_list`** 列表形 data 读取器（与 `_get` 同样的传输/状态/content-type/524288 字节上限/JSON/code==0 校验，但要求 `data` 为 **list** 而非 dict）——**`_get` 的 dict-only 合同保持严格不变**（既有所有读操作零回归风险）；`_tags(client, bvid, limit)` 经 `_get_list` GET `/x/tag/archive/tags` `{bvid}`→`{"bvid": bvid, **tag_rows}`；`_read` op 集合增 tags、**target 必填 BV**（同 video/comments，非豁免）、nav 后 `/view` 前分派（标签只需 bvid）；凭据权威 CAS/admission≤8/`_slots`/`asyncio.timeout(20)`/closing 守卫沿用。
- **`application/bilibili.py`**：`READ_SPEC` enum+desc 增 tags（`read`/`invoke` 通用透传）。
- **`adapters/http.py`**：`BilibiliReadInput.operation` Literal 增 tags（REST 通用 handler 不变；本批仅改该 Literal 块，`@@ -2281` background_review 区既有格式偏差非本批引入未重格式化）。

## 保持的合同

模型工具/REST/网关三面接线；`bilibili_read` 已在 `RETRIEVAL_TOOLS` **路由不变**；只读无写事务（`no_delivery` 守卫）；登录必需（nav）；Agent/凭据隔离与 CAS；有界（limit 1–20、行 ≤50、tag_name ≤100）；非法响应显式 502 不臆造；列表形 data 经 `_get_list` 严格校验（dict-data 拒 `bilibili_read_rejected`）。**无新表/迁移/路由/worker/持久层改动**；`_get` 未改。

## 验证

- 新增 `agent/tests/v2/integrations/test_v2_bilibili_tags.py` **17 项**（4 领域 + 6 malformed 参数化 + 6 网关 MockTransport：数组读取/limit/空数组/坏行 502/dict-data 拒/BV target 必填 + 1 spec/http 暴露）首轮全过（1.20 秒，format 后复跑 17 过 1.17 秒）。
- bilibili 读调用方专项 **220 过 / 0 失败**（tags/favorites/following/feed/search/whois/danmaku/gateway/response_bounds/merged，14.47 秒）；发表链测试不在本批读侧范围（其既有 `database_unavailable` 由他会话未提交持久层编辑引起，前批已 scoped-stash 实证）。
- V2 strict `pyrightconfig.strict.json` **410 文件零错误/警告**（无新源文件 include 不变）；ruff check/format 通过于本批 4 文件（http.py 仅改 Literal 块，既有偏差未触）。
- `project_index` 运行（`declared_tools` 索引不变，`bilibili_read` 为既有工具增 operation）；**聚合 `docs/index` 暂缓提交**（他会话未提交持久层编辑污染全局 `source_sha256`）。

## limits 与后续

仅公开视频标签列表（单 BV），不含标签订阅写/历史/msgfeed 收件箱；完整列表无分页（`has_more=len>limit`）；真实标签送达需账号另验。`_get_list` 为列表形端点的隔离读取器，未抽象重构 `_get`（保其 dict-only 安全合同与既有读零回归）。本批仅实现 K18 读广度标签缺口，其余 K13–K18 可离线缺口仍列后续独立批次。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。