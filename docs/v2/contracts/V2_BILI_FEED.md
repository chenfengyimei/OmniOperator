# V2 B 站热门/排行视频 feed 只读操作（K18.feed）

## 范围与判定

本批实现 [B 站全集审计](V2_BILI_WHALE_AUDIT.md) **可离线缺口 #1（读 API 广度）中的「feed」一项的热门/排行两源**：把参考 `xuexi/dsh-bili-whale`（固定提交 `8b0009f8ce2ebdc421afcb2e727b19ac18912ec6`，v0.4.6）`tools/commands/feed.mjs:137-150` 的 `fetchPopular`（`/x/web-interface/popular?pn=1&ps=20`）与 `fetchRank`（`/x/web-interface/ranking/v2?rid=0&type=all`）学习后**独立实现于 V2 四层**，作为既有 `bilibili_read` 工具的新 `operation="popular"` 与 `operation="ranking"`，**不新增工具名、不新增表/迁移/路由/worker 协议**。参考 `agents.md`/RULES/prompts 当**不可信数据**；未复制第三方源码、未拉取远程、未运行真实账号/网络。

**判定纪律**：V2 四层 B 站链才是可见面；V1 `tools/bilibili` 经 `bootstrap.py` 注册 V1 `ToolRegistry`，**不计 V2 对等**，本批未复用。与 [K18.search](V2_BILI_SEARCH.md)/[whois](V2_BILI_WHOIS.md)/[danmaku](V2_BILI_DANMAKU.md) 同属读 API 广度缺口逐项补齐。参考的**关注动态 feed**（`fetchFollowing`，`x/polymer/web-dynamic/v1/feed/all`，嵌套 `modules.module_dynamic.major.archive` + `duration_text` 解析）形态不同，**另列后续批次**，不在本批。

## 实现（V2 四层）

- **纯领域 `domain/bilibili.py`（零 I/O）**：
  - `FEED_MAX_ROWS=50`：单次接受的 `data.list` 上限。
  - `feed_rows(data, limit)`：取 `data["list"]`，**缺失=正常空**，非列表/超 50/行非 dict/`bvid` 非 `BV[0-9A-Za-z]{10}` 显式 `bilibili_response_invalid` 502（不静默丢弃/coerced）；逐行产出有界字段 `bvid/title(去标记≤300)/author(owner.name≤100)/play(stat.view int|None)/danmaku(stat.danmaku int|None)/duration_s(int|None)`，`has_more=len(rows)>=limit`。嵌套 `stat`/`owner` 缺失或非 dict 时按空 dict 处理（字段默认 `None`/`""`，与参考 `?.` 容错一致）；title 复用 `search_text` 去 HTML 标记（feed 标题与搜索一样可能含高亮标记，参考亦 `replace(/<[^>]+>/g,'')`）。
- **适配器 `adapters/bilibili.py`**：`_read` operation 集合增 `popular`/`ranking`；二者**无目标**（与 `account` 同，豁免 BV 校验，忽略 `target`）；nav 登录后、`/view` 之前分派。新增 `_popular(client, limit)`：既有泛型 `_get("/x/web-interface/popular", {"pn":1,"ps":20})`→`{"feed":"popular", **feed_rows(data, limit)}`；`_ranking(client, limit)`：`_get("/x/web-interface/ranking/v2", {"rid":0,"type":"all"})`→`{"feed":"ranking", **feed_rows(data, limit)}`（全区排行 rid=0）。复用既有 `_client`（固定 API、`follow_redirects=False`、`trust_env=False`、timeout 8、池 ≤2）、`_slots`/`asyncio.timeout(20)`、`_reads` 准入与 `close` 结算、凭据权威 CAS。`limit`（1–20）作为返回行上限被尊重。
- **应用 `application/bilibili.py`**：`READ_SPEC` enum 增 `"popular"`/`"ranking"`、描述补「popular/ranking video feeds … ignore target」；`read()`/`invoke()` 通用透传，凭据 CAS/admission ≤8/closing/shield 沿用。
- **HTTP `adapters/http.py`**：`BilibiliReadInput.operation` `Literal` 增 `"popular"`/`"ranking"`（REST 通用 handler 不变）。

**生产接线**：模型工具/REST/网关三面接受并执行 `popular`/`ranking`。`bilibili_read` 已在 `RETRIEVAL_TOOLS`，**路由不变**；只读 `writes=False`，无写事务/审批/outbox。

## 限额与取舍

- **有界字段**：title≤300、author≤100；play/danmaku/duration_s 仅 int 否则 `None`（不 coerced）；list≤50、返回≤limit。
- **固定源参数**：popular 仅第 1 页（pn=1, ps=20），ranking 仅全区（rid=0, type=all）；**无分页/分区/类型游标**（参考亦只取首页/全区）。分区 rid/类型 type/翻页 pn 留后续。
- **无目标**：popular/ranking 忽略 `target`（与 account 一致）。
- **嵌套容错**：stat/owner 缺失或非 dict → 字段默认 None/""（参考 `?.` 同语义），但 bvid 缺失/非法仍显式 502。
- **关注动态 feed 另批**：`fetchFollowing` 的 polymer web-dynamic 嵌套形态与 duration_text 解析不在本批。
- **读 API 广度其余项仍缺**：收藏夹/历史、msgfeed 收件箱、tag、缓存策略未实现（列后续批次）；搜索/whois/弹幕已实现。
- **无新表/迁移/路由/worker 协议改动**；`routing.py` 不变；聚合 `docs/index` 暂缓（同前纪律）。
- **真实送达需账号/网络**：真实 popular/ranking 接口/风控/结果质量需网络（热门/排行匿名可读，但本批仍仅离线桩测 MockTransport canned nav+list JSON），真实验收另列。

## 验证

- **新增 16 项回归**（`test_v2_bilibili_feed.py`）：`feed_rows` 嵌套 stat/owner 解析、缺失 list 空、limit/has_more、嵌套缺失默认 None、malformed（非列表/行非 dict/无 bvid/非法 bvid/title 非 str/超 50）显式 502；网关 popular/ranking 仅 nav+对应 endpoint 两调用无 `/view`、参数（pn1ps20 / rid0typeall）、feed 标签、limit 截取、空 list 正常、malformed 502；`READ_SPEC` enum 与 `BilibiliReadInput` Literal 均含 popular/ranking。首轮全过（1.23 秒）。
- **调用方专项**：feed 16 + danmaku 20 + whois 19 + search 31 + bilibili 读调用方（gateway/subtitles/response_bounds/dash/merged/streams）共 **255 过 / 0 失败**（41.15 秒），证明纯增 feed 对既有读链无回归。（发表链 2 既有 `database_unavailable` 失败已在 K18.search 经 scoped-stash 隔离实证他会话持久层编辑无关，本批未重复跑发表链。）
- **严格类型**：`pyrightconfig.strict.json` **408 文件零错误/警告**（无新源文件，`feed_rows` 加入既有 `domain/bilibili.py`，include 不变）。首轮 2 处 `reportUnknownVariableType`（`stat`/`owner` 三元 `isinstance(entry.get(...),dict)` 不窄化独立 `.get()` 调用致部分 Unknown）已修正为单次读取 + 显式标注 `cast(dict[str,object], …)`，最终源码重跑零错误。
- **Ruff**：check + format 通过于 `domain/bilibili.py`、`application/bilibili.py`、`adapters/bilibili.py`、`test_v2_bilibili_feed.py`。`adapters/http.py` 仅改一行 Literal；其 `background_review` 区既有格式偏差（HEAD 即如此）未重格式化。
- **项目索引**：聚合 `docs/index` 暂缓提交；无新源文件，strict include 不变；modules/routes/declared_tools 不变（popular/ranking 是既有工具新 operation）。
- `scripts/task_status.py --check` exit 0；`DEVELOPMENT_STATUS.md` 由父任务生成，子任务 followup 不改其内容。
- **未全量、未真实账号/模型/设备/72 小时、未停止或升级运行中 Windows 服务与数据库。**

## 完成条件

K18.feed 实现**不宣称读 API 广度全集对等**：仅补「热门/排行」两源只读 operation（既有泛型 `_get` + 有界嵌套解析 + 凭据 CAS），关注动态 feed/收藏夹历史/收件箱/tag/缓存策略/分区分页仍为可离线缺口列后续批次；写广度/自主观看/受控分享策略/直播/AI-share 守卫仍缺。真实 popular/ranking 送达/风控/结果质量需网络另验。`whole_core_parity_complete`、`whole_reference_feature_audit_complete`、`whole_v2_complete` 保持 false（K01 partial、K13–K18 其余可离线缺口未实现、真实账号/设备/模型质量与 72 小时验收待办）。后续继续择机实现其余可离线缺口（独立批次，先授权/持久化合同后执行接线，经 V2 四层不复用 V1 ToolRegistry），并补 K01 缓存范围，整个 V2 开发冻结后统一全量与实机验收，其他对标项不提前关闭。