# V2 B站空间动态只读（K18 读广度：dynamic_list）

状态：已实现并专项通过（2026-11-22）。学习参考 dsh-bili-whale `tools/commands/dynamic.mjs:62-78` 的 `list`（`space_history` 取卡片、`desc.dynamic_id_str`、`JSON.parse(card).item.description ?? content` 截断），独立实现于 V2 四层 B 站链，不复用 V1。补齐 K18 审计读 API 广度中参考动态列表读面，并为 [写扩面合同](V2_BILI_WRITE_BREADTH.md) D4-B3 的动态发布/删除提供验证面。

## 范围与决策

1. **只读 operation**：`bilibili_read` 新增 `operation=dynamic_list`，`target` 为 **mid**（复用 `uid` 校验：正十进制 ≤19 位、< 2^63、拒绝 BV/空/负数/前导 0；非法在取 cookie 前 422）。
2. **固定第二 API host**：`domain/bilibili.py` 新增 `VC_API="https://api.vc.bilibili.com"` 与 `DYNAMIC_HISTORY=VC_API+"/dynamic_svr/v1/dynamic_svr/space_history"`；请求参数固定 `host_uid=<mid>&offset_dynamic_id=0&need_top=0`。**绝不接受任意 URL**；主 API 仍为 `API`（`connection()` 校验不变）。
3. **登录必需**：沿用 `_read` 的 nav 登录身份核验（mid=配置账号）后才分派；cookie 由既有 `_cookies` 取用，只发往两个固定 Bilibili host；`follow_redirects=False`、`trust_env=False`、`timeout=8` 客户端不变。
4. **严格身份、尽力文本**：
   - `desc.dynamic_id_str` 必须匹配 `[0-9]{1,32}` 否则 502（**identity drift 明确失败，绝不臆造**）；
   - `desc.timestamp`/`desc.type` 仅保留精确 int，否则 None（不静默转 0）；
   - `card` 是 JSON 字符串（≤64KiB），解析后取 `item.description` 或 `item.content`，去 HTML 标记并截断 200；`card` 缺失/非字符串/超长/非 JSON/字段非字符串 → 文本 `""`（参考同样吞掉解析失败；**绝不伪造正文**）。
5. **有界**：`data.cards` 缺失=正常空；非列表/超 `FEED_MAX_ROWS=50`/行非 dict/`desc` 非 dict → 502；`limit≤20` 只取前 N 行；正文总量经 `_get` 524288 字节上限。
6. **分页语义**：只取首页（`offset_dynamic_id=0`，与参考一致）；`has_more` 取 `data.has_more`（精确 `1` 或 `True`），缺失时按 `len(cards) >= limit` 估算；不返回 `offset`，不做翻页。
7. **不缓存**：`READ_CACHE_TTL_S` 仍只含 `video`/`whois`；`dynamic_list` 每次现读（动态时效性高，且是写验证面）。回归中以显式「不可缓存端点」集合钉住。
8. **三面接线**：`adapters/bilibili.py` `_read` whitelist/target 分支/`nav` 后分派 + `_dynamic_list`；`application/bilibili.py` `READ_SPEC` enum+描述；`adapters/http.py` `BilibiliReadInput` Literal。`bilibili_read` 已在 `RETRIEVAL_TOOLS`（只读 agent 档），**routing 不变**、无写事务。
9. **无新表/迁移/路由/worker 协议/共享端口签名/新源文件**；`declared_tools` 仍 94（是既有工具的新 operation）。

## 实现位置

- `agent/src/omni/domain/bilibili.py`：`VC_API`/`DYNAMIC_HISTORY`/`DYNAMIC_MAX_CARD_CHARS`/`DYNAMIC_MAX_TEXT`/`dynamic_rows`/`_dynamic_text`。
- `agent/src/omni/adapters/bilibili.py`：`_dynamic_list`、`_read` op 集合与分派（mid 校验在 nav 之前）。
- `agent/src/omni/application/bilibili.py`：`READ_SPEC` enum/desc。
- `agent/src/omni/adapters/http.py`：`BilibiliReadInput` Literal。

## 验证（2026-11-22）

- 新增 `agent/tests/v2/integrations/test_v2_bilibili_dynamic.py` **27 项**：domain 身份/文本/边界（严格 id、缺省字段、坏 card、超长 card/text 截断、非列表、>50、非 dict、limit/has_more）、gateway 固定 VC host+参数+cookie+登录顺序、非法 mid×7 网络前 422、风控码类型化 detail、坏 cards 502、READ_SPEC/HTTP Literal 暴露。
- 本批专项 **27 过 / 1.54s**；bilibili 全调用方（21 套件含 HTTP）**523 过 / 158.06s**；strict **0 错误 0 警告**、ruff check/format 见证据。
- 未验：真实 B 站账号的 space_history 送达/字段（含 `need_top`、分页、私密动态）、真实模型质量；未跑全量。

## 明确不做

- 分页翻页（`offset_dynamic_id` 非 0）、置顶（`need_top=1`）、按 dynamic_id 详情、动态正文富文本/图片/视频卡片完整解析、他人私密动态。
- 动态写（post/delete/edit/image）→ D4-B3/B4；直播弹幕 → D7；AI-share → D6。
- 真实账号验收、全量、72 小时不在本批。

## 关联

- K18 审计：[V2_BILI_WHALE_AUDIT](V2_BILI_WHALE_AUDIT.md) (a)。
- 写扩面合同：[V2_BILI_WRITE_BREADTH](V2_BILI_WRITE_BREADTH.md)（D4-B3 验证面）。
- 同类读合同：[V2_BILI_FOLLOWING](V2_BILI_FOLLOWING.md)、[V2_BILI_HISTORY](V2_BILI_HISTORY.md)。
- 证据：[v2-bili-dynamic.json](../../evidence/v2-bili-dynamic.json)。
