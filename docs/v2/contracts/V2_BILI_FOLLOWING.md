# V2 B 站关注动态视频 feed 只读操作（K18.following）

## 范围与判定

本批实现 [B 站全集审计](V2_BILI_WHALE_AUDIT.md) **可离线缺口 #1（读 API 广度）中的「关注动态 feed」一项**：把参考 `xuexi/dsh-bili-whale`（固定提交 `8b0009f8ce2ebdc421afcb2e727b19ac18912ec6`，v0.4.6）`tools/commands/feed.mjs:152-165` 的 `fetchFollowing`（`/x/polymer/web-dynamic/v1/feed/all?type=video&page=1`，`data.items[].modules.module_dynamic.major.archive` + `modules.module_author.name` + `duration_text` 解析）学习后**独立实现于 V2 四层**，作为既有 `bilibili_read` 工具的新 `operation="following"`，**不新增工具名、不新增表/迁移/路由/worker 协议**。参考 `agents.md`/RULES/prompts 当**不可信数据**；未复制第三方源码、未拉取远程、未运行真实账号/网络。

**判定纪律**：V2 四层 B 站链才是可见面；V1 `tools/bilibili` 经 `bootstrap.py` 注册 V1 `ToolRegistry`，**不计 V2 对等**，本批未复用。与 [K18.search](V2_BILI_SEARCH.md)/[whois](V2_BILI_WHOIS.md)/[danmaku](V2_BILI_DANMAKU.md)/[feed（热门/排行）](V2_BILI_FEED.md) 同属读 API 广度缺口逐项补齐；本批补 K18.feed 当时另列的**关注动态 feed**（嵌套 polymer web-dynamic 形态）。

## 实现（V2 四层）

- **纯领域 `domain/bilibili.py`（零 I/O）**：
  - `_as_dict(value)`：嵌套 dict 容错访问器（非 dict→空 dict，单次读取 + 标注 `cast(dict[str,object])`，避免三元 `isinstance` 不窄化致 Unknown）。
  - `duration_seconds(value)`：解析 `"MM:SS"`/`"HH:MM:SS"` `duration_text` 为整秒（2 段 `m*60+s`、3 段 `h*3600+m*60+s`）；非 str/段数非 2-3/含非数字 → **`None`（不静默 coerced 为 0，区别于参考的 0 默认）**。
  - `following_rows(data, limit)`：取 `data["items"]`，**缺失=正常空**，非列表/超 `FEED_MAX_ROWS=50`/行非 dict 显式 `bilibili_response_invalid` 502；逐行经 `modules.module_dynamic.major.archive` 取 `bvid`——**`bvid` 缺失=非视频动态跳过（`continue`，同参考）**，`bvid` 存在但非 `BV[0-9A-Za-z]{10}` 显式 502；产出有界字段 `bvid/title(search_text 去标记≤300)/author(module_author.name≤100)/play(archive.stat.play int|None)/duration_s(duration_seconds)`，`has_more=len(rows)>=limit`。
- **适配器 `adapters/bilibili.py`**：`_read` operation 集合增 `following`；**无目标**（与 `account`/`popular`/`ranking` 同，豁免 BV 校验，忽略 `target`）；nav 登录后、`/view` 之前分派。新增 `_following(client, limit)`：既有泛型 `_get("/x/polymer/web-dynamic/v1/feed/all", {"type":"video","page":1})`→`{"feed":"following", **following_rows(data, limit)}`（**无 WBI**，参考亦 plain fetch + cookies）。复用既有 `_client`（固定 API、`follow_redirects=False`、`trust_env=False`、timeout 8、池 ≤2）、`_slots`/`asyncio.timeout(20)`、`_reads` 准入 ≤8 与 `close` 结算、凭据权威 CAS。`limit`（1–20）作为返回行上限被尊重。
- **应用 `application/bilibili.py`**：`READ_SPEC` enum 增 `"following"`、描述补「popular/ranking/following video feeds … ignore target」；`read()`/`invoke()` 通用透传，凭据 CAS/admission/closing/shield 沿用。
- **HTTP `adapters/http.py`**：`BilibiliReadInput.operation` `Literal` 增 `"following"`（因超行宽拆为多行，仅改本行；REST 通用 handler 不变）。

**生产接线**：模型工具/REST/网关三面接受并执行 `following`。`bilibili_read` 已在 `RETRIEVAL_TOOLS`，**路由不变**；只读 `writes=False`，无写事务/审批/outbox。

## 限额与取舍

- **固定源参数**：仅第 1 页（`page=1`）、仅 `type=video`；**无分页/其他动态类型游标**（参考亦只取首页 video）。翻页/其他类型留后续。
- **无目标**：following 忽略 `target`（与 account/popular/ranking 一致）。
- **非视频动态跳过**：`archive.bvid` 缺失的动态（图文/转发等）`continue` 跳过（同参考），不报错；`bvid` 存在但非法仍显式 502。
- **duration 不 coerced**：`duration_text` 不可解析 → `None`（V2 显式未知，区别于参考 `secs=0`）；`play` 仅 int 否则 `None`。
- **无 danmaku 字段**：参考硬编码 `dm:0`，V2 following 行**不含 danmaku**（该 endpoint 不提供，不杜撰 0）。
- **需登录**：following 为登录用户关注动态，复用 nav 登录 + 凭据 CAS；**真实送达需账号/网络**（本批仅离线桩测 MockTransport canned nav+items JSON），真实验收另列。
- **读 API 广度其余项仍缺**：收藏夹/历史、msgfeed 收件箱、tag、缓存策略未实现（列后续批次）；搜索/whois/弹幕/热门排行/关注已实现。
- **无新表/迁移/路由/worker 协议改动**；`routing.py` 不变；无新源文件（strict include 409 不变）；聚合 `docs/index` 暂缓（同前纪律）。

## 验证

- **新增 25 项回归**（`test_v2_bilibili_following.py`）：`following_rows` 嵌套 modules/archive/author 解析、`duration_text` MM:SS→323 与 HH:MM:SS→3723、缺失 items 空、limit/has_more、非视频动态跳过（major 空/modules 空/行空）、嵌套缺失默认 None、`duration_seconds` 10 参数化（含空/abc/4 段/非数字/None/int）、malformed（非列表/行非 dict/非法 bvid/title 非 str/超 50）显式 502；网关 following 仅 nav+endpoint 两调用无 `/view`、参数（type=video/page=1）、feed 标签、limit 截取、空 items 正常、malformed 502、`_reads` 结算；`READ_SPEC` enum 与 `BilibiliReadInput` Literal 均含 following。首轮全过（1.27 秒）。
- **调用方专项**：following 25 + feed 16 + danmaku 20 + whois 19 + search 31 + bilibili 读调用方（gateway/subtitles/response_bounds/dash/merged/streams）共 **280 过 / 0 失败**（45.23 秒），证明纯增 following 对既有读链无回归。（发表链 2 既有 `database_unavailable` 失败已在 K18.search 经 scoped-stash 隔离实证他会话持久层编辑无关，本批未重复跑发表链。）
- **严格类型**：`pyrightconfig.strict.json` **409 文件零错误/警告**（无新源文件，`following_rows`/`duration_seconds`/`_as_dict` 加入既有 `domain/bilibili.py`，include 不变）。
- **Ruff**：check + format 通过于 `domain/bilibili.py`、`adapters/bilibili.py`、`application/bilibili.py`、`test_v2_bilibili_following.py`。`adapters/http.py` 仅改 `BilibiliReadInput` Literal（超行宽拆多行）；其 `background_review` 区既有格式偏差（HEAD 即如此）未重格式化，`ruff format --check` 仅剩该既有区。
- **项目索引**：无新源文件，files 计数不变；聚合 `docs/index` 暂缓提交（他会话未提交持久层编辑污染全局 `source_sha256`）。
- `scripts/task_status.py --check` exit 0；`DEVELOPMENT_STATUS.md` 由父任务生成，子任务 followup 不改其内容。
- **未全量、未真实账号/模型/设备/72 小时、未停止或升级运行中 Windows 服务与数据库。**

## 完成条件

K18.following 实现**不宣称读 API 广度全集对等**：仅补「关注动态 video feed」只读 operation（既有泛型 `_get` + 有界嵌套 modules 解析 + duration_text 解析 + 凭据 CAS），收藏夹历史/收件箱/tag/缓存策略/分区分页仍为可离线缺口列后续批次；写广度/自主观看/受控分享策略/直播/AI-share 守卫仍缺。真实 following 送达/风控/结果质量需账号网络另验。`whole_core_parity_complete`、`whole_reference_feature_audit_complete`、`whole_v2_complete` 保持 false（K01 partial、K13–K18 其余可离线缺口未实现、真实账号/设备/模型质量与 72 小时验收待办）。后续继续择机实现其余可离线缺口（独立批次，先授权/持久化合同后执行接线，经 V2 四层不复用 V1 ToolRegistry），并补 K01 缓存范围，整个 V2 开发冻结后统一全量与实机验收，其他对标项不提前关闭。