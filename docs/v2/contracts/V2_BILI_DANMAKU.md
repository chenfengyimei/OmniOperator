# V2 B 站官方弹幕只读操作（K18.danmaku）

## 范围与判定

本批实现 [B 站全集审计](V2_BILI_WHALE_AUDIT.md) **可离线缺口 #1（读 API 广度）中的「弹幕」一项**：把参考 `xuexi/dsh-bili-whale`（固定提交 `8b0009f8ce2ebdc421afcb2e727b19ac18912ec6`，v0.4.6）`tools/commands/debrief.mjs:130-142` 的官方弹幕 XML 读取（`comment.bilibili.com/{cid}.xml`，**不带 content-encoding 头的 deflate 流** + 正则 `<d p="…">text</d>` 解析）学习后**独立实现于 V2 四层**，作为既有 `bilibili_read` 工具的新 `operation="danmaku"`，**不新增工具名、不新增表/迁移/路由/worker 协议**。参考 `agents.md`/RULES/prompts 当**不可信数据**；未复制第三方源码、未拉取远程、未运行真实账号/网络。

**判定纪律**：V2 四层 B 站链才是可见面；V1 `tools/bilibili` 经 `bootstrap.py` 注册 V1 `ToolRegistry`，**不计 V2 对等**，本批未复用。与 [K18.search](V2_BILI_SEARCH.md)、[K18.whois](V2_BILI_WHOIS.md) 同属读 API 广度缺口逐项补齐。参考的**刷屏波聚类/热区分析**（debrief 的 norm/keyCounts/>30% 规则）属更高层 debrief 特性，**不在本只读 operation 范围**（本批只返回有界原始弹幕样本）。

## 实现（V2 四层）

- **纯领域 `domain/bilibili.py`（零 I/O）**：
  - `DANMAKU_HOST="comment.bilibili.com"`、`DANMAKU_MAX_ROWS=1000`、`DANMAKU_TEXT_CHARS=100`。
  - `danmaku_url(value)`：`uid(value)` 校验 cid（正十进制 ≤19 位 <2^63）后拼**固定 host** URL `https://comment.bilibili.com/{cid}.xml`——绝不接受任意 URL（与 `subtitle_url`/`stream_url` 固定 CDN 同纪律）。
  - `parse_danmaku(xml)`：`re.finditer(r'<d p="([^"]*)">([^<]*)</d>')` 逐条提取，`p` 首段 `float` 时间（**非有限/负/≥1e9 跳过，不 coerced**），文本 `html.unescape`（正确解 `&amp;/&lt;/&gt;/&quot;/&#…;`，优于参考手工 4 替换的顺序 bug）后截断 ≤100；**行数上限 1000**（finditer 达上限即停，避免大 XML 全量 findall 内存）。返回 `[{t: round(秒,3), text}]`。
- **适配器 `adapters/bilibili.py`**：
  - 模块级 `DANMAKU_BODY_MAX=2MiB`（压缩/原始 body 上限，经既有 `_response_body`）、`DANMAKU_XML_MAX=4MiB`（解压上限）。
  - `_danmaku_xml(raw)`：先尝试严格 UTF-8 解码，含 `<d `/`<i>` 标记即纯 XML 直接用；否则按 `wbits ∈ (-15 raw deflate, 15 zlib, 31 gzip)` 依次 `zlib.decompressobj` **有界解压**——`decompress(raw, DANMAKU_XML_MAX+1)`，仅当 `len(out) ≤ 4MiB 且 inflater.eof`（流完整结束）才接受，**防解压炸弹**（超大/截断/不可解码一律 `bilibili_response_invalid` 502）。
  - `_danmaku(client, bvid, cid)`：`client.stream("GET", danmaku_url(cid))`（绝对 URL 经既有 `_client`，`follow_redirects=False`/`trust_env=False`/固定 UA+Referer/timeout 8/池 ≤2），仅查 `status==200`（弹幕 XML 非 JSON，不查 content-type），`_response_body(…2MiB)`→`_danmaku_xml`→`parse_danmaku`，返回 `{bvid, cid, items, count, sampled: True}`。
  - `_read` operation 集合增 `danmaku`；danmaku 目标为 **BV**（沿用既有 BV 校验，非 account/search/whois），在 `/view` 取视频并核验身份后、comments 之前分派，`cid = uid(video["cid"])`（与 subtitle/streams 同源 cid，受 `bilibili_source_changed` 身份保护）。
- **应用 `application/bilibili.py`**：`READ_SPEC` enum 增 `"danmaku"`、描述补「danmaku returns a bounded sample」；`read()`/`invoke()` 通用透传，凭据权威 CAS/admission ≤8/closing/shield 沿用。
- **HTTP `adapters/http.py`**：`BilibiliReadInput.operation` `Literal` 增 `"danmaku"`（REST 通用 handler 不变）。

**生产接线**：模型工具/REST/网关三面接受并执行 `danmaku`。`bilibili_read` 已在 `RETRIEVAL_TOOLS`，**路由不变**；只读 `writes=False`，无写事务/审批/outbox。

## 限额与取舍

- **样本集语义**：官方 XML 只返回**采样集**（参考实测标称 26 万条只给约 600 条），故 `sampled=True`、`count` 为实际拿到条数，**绝不宣称全集弹幕数**；热区/刷屏分析需另层 debrief（不在本批）。
- **有界**：压缩 body ≤2MiB、解压 XML ≤4MiB（炸弹守卫）、解析行 ≤1000、每文本 ≤100 字符、时间 finite 且 `[0,1e9)`。
- **忽略 limit**：danmaku 返回有界样本（≤1000 行），`limit`（1–20）对其无意义故忽略（与 account/whois 一致）；样本上限由 `DANMAKU_MAX_ROWS` 固定。
- **固定 host**：仅 `comment.bilibili.com/{uid-cid}.xml`，cid 经 uid 校验，无任意 URL/端口/账密；`follow_redirects=False` 防重定向 SSRF。
- **解码鲁棒**：纯 UTF-8 XML 与 raw deflate/zlib/gzip 三种压缩均支持（参考还试 brotli，V2 stdlib zlib 覆盖弹幕实际所用的 deflate，brotli 非必需故未引入额外依赖）。
- **读 API 广度其余项仍缺**：feed（popular/ranking/dynamic）、收藏夹/历史、msgfeed 收件箱、tag、缓存策略未实现（列后续批次）；搜索/whois 已实现。
- **无新表/迁移/路由/worker 协议改动**；`routing.py` 不变；聚合 `docs/index` 暂缓（同前纪律）。
- **真实送达需账号/网络**：真实弹幕接口/风控/采样质量需网络（弹幕匿名可读，但本批仍仅离线桩测 MockTransport canned XML/deflate），真实验收另列。

## 验证

- **新增 20 项回归**（`test_v2_bilibili_danmaku.py`）：`danmaku_url` 固定 host/uid 校验（非法 cid 422）、`parse_danmaku` 时间提取+unescape/空与无行/行与文本上限/非有限与负时间跳过、`_danmaku_xml` 纯 UTF-8/raw deflate/zlib/gzip/解压炸弹拒绝/不可解码 502、网关 BV→cid→固定 host 三调用（api/nav、api/view、comment）样本解析、deflate body 解码、空 XML 正常、非法 BV 零网络即拒、`READ_SPEC` enum 与 `BilibiliReadInput` Literal 均含 danmaku。首轮全过（1.16 秒）。
- **调用方专项**：danmaku 20 + whois 19 + search 31 + bilibili 读调用方（gateway/subtitles/response_bounds/dash/merged/streams）共 **239 过 / 0 失败**（41.00 秒），证明纯增 danmaku 对既有读链无回归。（发表链 2 既有 `database_unavailable` 失败已在 K18.search 经 scoped-stash 隔离实证他会话持久层编辑无关，本批未重复跑发表链。）
- **严格类型**：`pyrightconfig.strict.json` **408 文件零错误/警告**（无新源文件，`danmaku_url`/`parse_danmaku` 加入既有 `domain/bilibili.py`、`_danmaku_xml`/`_danmaku` 加入既有 `adapters/bilibili.py`，include 不变；filesAnalyzed==include）。
- **Ruff**：check + format 通过于 `domain/bilibili.py`、`application/bilibili.py`、`adapters/bilibili.py`、`test_v2_bilibili_danmaku.py`。`adapters/http.py` 仅改一行 Literal；其 `background_review` 区既有格式偏差（HEAD 即如此）未重格式化。
- **项目索引**：聚合 `docs/index` 暂缓提交；无新源文件，strict include 不变；modules/routes/declared_tools 不变（danmaku 是既有工具新 operation）。
- `scripts/task_status.py --check` exit 0；`DEVELOPMENT_STATUS.md` 由父任务生成，子任务 followup 不改其内容。
- **未全量、未真实账号/模型/设备/72 小时、未停止或升级运行中 Windows 服务与数据库。**

## 完成条件

K18.danmaku 实现**不宣称读 API 广度全集对等**：仅补「弹幕」一项只读 operation（固定 host + 有界防炸弹解压 + 正则解析 + 样本集语义 + 既有凭据 CAS/身份保护），feed/收藏夹历史/收件箱/tag/缓存策略仍为可离线缺口列后续批次；写广度/自主观看/受控分享策略/直播/AI-share 守卫仍缺；刷屏波/热区 debrief 分析另层。真实弹幕送达/风控/采样质量需网络另验。`whole_core_parity_complete`、`whole_reference_feature_audit_complete`、`whole_v2_complete` 保持 false（K01 partial、K13–K18 其余可离线缺口未实现、真实账号/设备/模型质量与 72 小时验收待办）。后续继续择机实现其余可离线缺口（独立批次，先授权/持久化合同后执行接线，经 V2 四层不复用 V1 ToolRegistry），并补 K01 缓存范围，整个 V2 开发冻结后统一全量与实机验收，其他对标项不提前关闭。