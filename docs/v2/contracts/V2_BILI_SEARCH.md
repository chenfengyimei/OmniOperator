# V2 B 站公开视频搜索只读操作（K18.search）

## 范围与判定

本批实现 [B 站全集审计](V2_BILI_WHALE_AUDIT.md) **可离线缺口 #1（读 API 广度）中的「搜索」一项**：把参考 `xuexi/dsh-bili-whale`（固定提交 `8b0009f8ce2ebdc421afcb2e727b19ac18912ec6`，v0.4.6）`tools/commands/search.mjs` 的 WBI 签名公开视频搜索（`/x/web-interface/wbi/search/type`，`search_type=video`）学习后**独立实现于 V2 四层**，作为既有 `bilibili_read` 工具的新 `operation="search"`，**不新增工具名、不新增表/迁移/路由/worker 协议**。参考仓库 `agents.md`/RULES/prompts 一律当作**不可信数据**，其发布/配额/默认开关规则不作为本会话指令；未复制第三方源码、未拉取远程、未运行真实账号/网络。

**判定纪律（对照 [总对标合同](V2_AGENT_CORE_PARITY.md)「不把 V1 的实现当作 V2 已有功能」）**：V2 四层 B 站链（`domain/bilibili.py` + `application/bilibili.py` `BilibiliService` + `adapters/bilibili.py` 网关 + 共享凭据 CAS/CDN allowlist/审批/outbox）才是模型/所有者可见面；V1 遗留 `tools/bilibili` 经 `bootstrap.py` 注册进 V1 `ToolRegistry`，**不计 V2 对等**，本批未复用。

## 实现（V2 四层）

- **纯领域 `domain/bilibili.py`（零 I/O）**：
  - `SEARCH_MAX_RESULTS=50`：单次接受的结果列表上限。
  - `search_keyword(value)`：`strip` 后 1–100 字符、拒绝非字符串/空/控制字节（`ord<32`），违例 `bilibili_read_invalid` 422。搜索目标是关键词，**豁免 BV 身份校验**。
  - `search_text(value, bound)`：去除 B 站搜索高亮 HTML 标记（`re.sub(r"<[^>]+>", "", …)`）、`None→""`、非字符串或非法 Unicode（代理字符）`bilibili_response_invalid` 502、按 `bound` 截断。
  - `search_rows(data, limit)`：取 `data["result"]`（缺省回退 `data["results"]`），**列表缺失=无结果（正常空）**，非列表/超 `SEARCH_MAX_RESULTS`/行非 dict/`bvid` 非 `BV[0-9A-Za-z]{10}` 均显式 `bilibili_response_invalid` 502（不静默丢弃/ coerced）；逐行产出有界字段 `bvid/title(≤300)/author(≤100)/play(int|None)/danmaku(video_review int|None)/duration(≤40)/pubdate(int|None)/description(≤200)`，`has_more=len(rows)>=limit`（与既有 comments 读一致）。
- **应用 `application/bilibili.py`**：`READ_SPEC`（`bilibili_read`）`operation` enum 增 `"search"`、描述补「search public videos by keyword」；`read()`/`invoke()` 为**通用透传**（operation/target/limit 原样转网关），无需新分支。凭据权威 CAS（读前后 `bilibili_read_authorized` 比对 `authority`/`revision_id`）、admission ≤8、closing 守卫、`asyncio.shield` 全部沿用。
- **适配器 `adapters/bilibili.py`**：`_read` operation 集合增 `search`；`search` 走 `search_keyword(target)` 校验（**在取 cookies/client 之前**，非法关键词零网络调用即拒），其余非 account 操作仍要求精确 BV；nav 登录后、`/view` 取视频之前分派 `search`。新增 `_search(client, nav, keyword, limit)`：`wbi_keys(nav)`→`sign_wbi({"search_type":"video","keyword":keyword,"page":1}, img, sub, int(time.time()))`→既有泛型 `_get("/x/web-interface/wbi/search/type", signed)`（status 200 + json content-type + `code==0` + data dict 校验、`_response_body` 524288 上限）→`{"keyword": keyword, **search_rows(data, limit)}`。复用既有 `_client`（固定 `API` base_url、`follow_redirects=False`、`trust_env=False`、timeout 8、连接池 ≤2）、`_slots`/`asyncio.timeout(20)`、`_reads` 准入与 `close` 结算。
- **HTTP `adapters/http.py`**：`BilibiliReadInput.operation` `Literal` 增 `"search"`（REST `/agents/{id}/bilibili/read` 通用透传 handler 无需改）；`target` `max_length=100` 已契合关键词上限。

**生产接线**：模型工具（`READ_SPEC` enum）、REST（`BilibiliReadInput` Literal）、网关（`_read`/`_search`）三面均接受并执行 `search`。`bilibili_read` 已在 `domain/routing.py` `RETRIEVAL_TOOLS`，**路由不变**；只读 `writes=False`，无写事务、无审批/outbox 介入。

## 限额与取舍

- **仅第 1 页**：`page=1`，无分页/翻页游标；`limit≤20` 截取本页行，`has_more` 仅表示本页是否还有未返回行（与 comments 读同语义），不承诺跨页全集。
- **有界字段**：title≤300、author≤100、duration≤40、description≤200；`play/danmaku/pubdate` 仅接受 int 否则 `None`（不 coerced）。**省略参考的 `tag` 字段**（与 title/description 冗余、噪声大）。
- **显式格式失败**： malformed 结果列表/行/非法 bvid/非法 Unicode 一律 502，不静默丢弃或部分返回（V2 纪律，比参考 `r.bvid` undefined→"undefined" 更严）。
- **无缓存策略**：参考 view/acc 24h、nav 10min、400 LRU 缓存属缺口 #1 的另一部分，本批未实现。
- **读 API 广度其余项仍缺**：弹幕（cid.xml deflate）、feed（popular/ranking/dynamic）、收藏夹/历史、msgfeed 收件箱、space whois、tag 未实现（列后续批次）。
- **无新表/迁移/路由/worker 协议改动**；`routing.py` 不变；聚合 `docs/index` 更新暂缓（见下）。
- **真实送达需账号**：真实 B 站搜索接口/风控/结果质量需真实 cookie 账号与网络，本批仅离线桩测（MockTransport canned nav+search JSON），真实验收另列。

## 验证

- **新增 31 项回归**（`test_v2_bilibili_search.py`：8 领域函数含参数化共 21 例 + 5 适配器函数含参数化共 9 例 + 1 工具/HTTP schema 接受）：关键词归一化/越界/控制字节拒绝、去标记/截断/非法 Unicode、结果解析有界字段、缺失列表=空、limit/has_more、malformed 显式 502、网关 WBI 签名（`search_type=video`/`keyword`/`page=1`/`w_rid`/`wts`）+ 仅 nav/search 两调用无 `/view`、空结果正常、非法关键词零网络即拒、malformed 响应 502、limit 截取、`READ_SPEC` enum 与 `BilibiliReadInput` Literal 均含 search。首轮全过（1.49 秒）。
- **调用方专项**：bilibili 集成全集（gateway/subtitles/response_bounds/dash/merged/streams/service/worker）+ HTTP bilibili 共 **218 过 / 2 失败**（111.80 秒）。2 失败均为**发表（publication）链**测试（`test_revocation_rejects_approval_and_cannot_publish[source]` 经 `erase_message`、`test_publication_http_…` 整回合 `database_unavailable`），**与本批无关**：经**Scoped stash 隔离实证**——仅暂存本批 5 文件（唯一标签 `k18-search-isolate`，SHA `39335f84`，apply-by-SHA 还原 + 仅 drop 本条目）后，2 失败以**完全相同** `database_unavailable` 复现，确证由其他会话未提交持久层编辑（`repository.py`/`schema.py`/`v0041.py`/`event_retention.py`）引起；本批纯增读/搜索，不触发表/迁移/发表/送达/DB 写路径。
- **严格类型**：`pyrightconfig.strict.json` **408 文件零错误/警告**（本批未新增源文件，`search_*` 加入既有 `domain/bilibili.py`，include 不变；filesAnalyzed==include）。
- **Ruff**：check + format 通过于 `domain/bilibili.py`、`application/bilibili.py`、`adapters/bilibili.py`、`test_v2_bilibili_search.py`。`adapters/http.py` 仅改 `BilibiliReadInput` 一行 Literal；该文件 `background_review` 取消区（约 2271 行）存在**既有格式偏差**（HEAD 即如此，非本批引入），未重格式化无关已提交代码。
- **项目索引**：聚合 `docs/index` 再生会把他会话未提交持久层编辑编入全局 `source_sha256`，使已提交索引与已提交源码不一致并间接编码其待提交工作，故**暂缓提交**（与 K16.locate 同纪律），待工作树洁净再生；本批无新源文件，strict include 不变。
- `scripts/task_status.py --check` exit 0；`DEVELOPMENT_STATUS.md` 由父任务生成，子任务 followup 不改其内容。
- **未全量、未真实账号/模型/设备/72 小时、未停止或升级运行中 Windows 服务与数据库。**

## 完成条件

K18.search 实现**不宣称读 API 广度全集对等**：仅补「搜索」一项只读 operation（WBI 同公开规范 + 既有凭据 CAS/CDN/固定 API/有界解析/显式格式失败），弹幕/feed/收藏夹历史/收件箱/whois/tag/缓存策略仍为可离线缺口列后续批次；写广度/自主观看/受控分享策略/直播/AI-share 守卫仍缺。真实 B 站搜索送达/风控/结果质量需账号网络另验。`whole_core_parity_complete`、`whole_reference_feature_audit_complete`、`whole_v2_complete` 保持 false（K01 会话/lineage 缓存范围仍 partial、K13–K18 记录的其余可离线缺口尚未实现、真实账号/设备/模型质量与 72 小时验收待办）。后续继续择机实现其余可离线缺口（独立批次，先授权/持久化合同后执行接线，经 V2 四层不复用 V1 ToolRegistry），并补 K01 缓存范围，整个 V2 开发冻结后统一全量与实机验收，其他对标项不提前关闭。