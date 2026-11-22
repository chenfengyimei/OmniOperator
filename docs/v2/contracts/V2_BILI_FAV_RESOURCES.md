# V2 B站收藏夹内容只读（K18 读广度：fav_resources）

状态：已实现并专项通过（2026-11-23）。参考 dsh-bili-whale 只有收藏夹列表（`tools/commands/fav.mjs:87` `folder/created/list-all`）与收藏写入（`fav/resource/deal`），**没有文件夹内容读取**；V2 本批按公开社区规范实现 `x/v3/fav/resource/list` 的只读面，一方面补读广度，一方面作为 [写扩面合同](V2_BILI_WRITE_BREADTH.md) D4-B2 `favorite_add` 的**验证面**（收藏后回读目标夹确认记录出现）。独立实现于 V2 四层，不复用 V1。

## 范围与决策

1. **只读 operation**：`bilibili_read` 新增 `operation=fav_resources`，`target` 为**收藏夹 id**（`fav_folder_id`：正十进制 ≤19 位、< 2^63、拒绝空/0/负数/前导 0/BV/非串；非法在取 cookie 前 422 `bilibili_identity_invalid`）。
2. **固定端点**：`GET /x/v3/fav/resource/list`，参数固定 `media_id=<folder>&pn=1&ps=20&keyword=&order=mtime&type=0&tid=0&platform=web`；主 API 仍为 `API`（`connection()` 校验不变），**不接受任意 URL**。
3. **登录必需**：沿用 `_read` 的 nav 登录身份核验（mid=配置账号）后才分派；cookie 只发固定 host；`follow_redirects=False`/`trust_env=False`/`timeout=8` 不变。
4. **行规则**：只保留 **type-2 视频行且 bvid 合法**；非视频媒体（音频/合集/专栏）与失效条目（`bvid` 缺失或空串）**跳过**（不臆造身份）；`bvid` 存在但非字符串/非 `BV[0-9A-Za-z]{10}` → 502；行非 dict → 502。
5. **字段**：`bvid`、`title`（去标记 ≤300）、`author`（`upper.name` 去标记 ≤100）、`duration_s`（精确 int 否则 None）、`fav_time`（精确 int 否则 None）；`info.title` ≤300、`info.media_count` 精确 int 否则 None（info 缺失/非 dict 容错为空，辅助字段不阻塞）。
6. **有界**：`medias` 缺失=正常空；非列表/超 `FEED_MAX_ROWS=50` → 502；`limit≤20` 是**视频行**上限（先过滤后取页，避免首几行非视频挤掉目标）；`has_more` 取 `data.has_more` 精确 `1`/`True`，缺失按 `len(medias) > limit` 估算；正文经 `_get` 524288 字节上限。
7. **不缓存**：`READ_CACHE_TTL_S` 仍只含 `video`/`whois`；`fav_resources` 每次现读（写验证面），缓存 allowlist 回归同步。
8. **三面接线**：adapter `_fav_resources` + `_read` op/target/dispatch；application `READ_SPEC` enum+desc；http `BilibiliReadInput` Literal。`bilibili_read` 已在 `RETRIEVAL_TOOLS`（只读 agent 档），**routing 不变**、无写事务。
9. **无新表/迁移/路由/worker 协议/共享端口签名/新源文件**；`declared_tools` 仍 94。

## 实现位置

- `agent/src/omni/domain/bilibili.py`：`fav_folder_id`、`fav_resource_rows`。
- `agent/src/omni/adapters/bilibili.py`：`_fav_resources`、`_read` op 集合/target 分支/分派。
- `agent/src/omni/application/bilibili.py`：`READ_SPEC` enum/desc。
- `agent/src/omni/adapters/http.py`：`BilibiliReadInput` Literal。

## 验证（2026-11-23）

- 新增 `agent/tests/v2/integrations/test_v2_bilibili_fav_resources.py` **23 项**：domain 行形状/跳过非视频与失效条目/坏 bvid 与坏形状 502/>50 行/可选字段/limit+has_more/folder id 归一与拒绝；gateway 固定端点+全部参数+cookie+登录顺序；非法 folder×7 网络前 422；风控码类型化 detail；坏 medias 502；spec/HTTP 暴露。
- 本批专项 **23 过 / 1.34s**；bilibili 全调用方（22 套件含 HTTP）**546 过 / 142.79s**；strict **0 错误 0 警告**、ruff check/format 见证据。
- 未验：真实账号的私密/公开收藏夹送达、`pn` 翻页、分区 `tid`/排序变体、真实模型质量；未跑全量。

## 明确不做

- 翻页（`pn>1`）、分区/排序过滤、收藏夹信息页、非视频媒体详情。
- 收藏写入（`resource/deal`）→ D4-B2（本 op 是其验证面）；取消收藏、收藏夹管理写。
- 动态写 → D4-B3/B4；直播 → D7；AI-share → D6。
- 真实账号验收、全量、72 小时不在本批。

## 关联

- K18 审计：[V2_BILI_WHALE_AUDIT](V2_BILI_WHALE_AUDIT.md) (a)。
- 写扩面合同：[V2_BILI_WRITE_BREADTH](V2_BILI_WRITE_BREADTH.md)（D4-B0 完成、D4-B2 验证面）。
- 同类读合同：[V2_BILI_FAVORITES](V2_BILI_FAVORITES.md)（文件夹列表）、[V2_BILI_DYNAMIC](V2_BILI_DYNAMIC.md)。
- 证据：[v2-bili-fav-resources.json](../../evidence/v2-bili-fav-resources.json)。

