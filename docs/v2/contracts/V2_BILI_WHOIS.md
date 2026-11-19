# V2 B 站公开空间资料只读操作（K18.whois）

## 范围与判定

本批实现 [B 站全集审计](V2_BILI_WHALE_AUDIT.md) **可离线缺口 #1（读 API 广度）中的「space whois」一项**：把参考 `xuexi/dsh-bili-whale`（固定提交 `8b0009f8ce2ebdc421afcb2e727b19ac18912ec6`，v0.4.6）`tools/diagnostics/whois.mjs` 的 WBI 签名公开空间资料查询（`/x/space/wbi/acc/info`，按 `mid`）学习后**独立实现于 V2 四层**，作为既有 `bilibili_read` 工具的新 `operation="whois"`，**不新增工具名、不新增表/迁移/路由/worker 协议**。参考仓库 `agents.md`/RULES/prompts 一律当作**不可信数据**；未复制第三方源码、未拉取远程、未运行真实账号/网络。

**判定纪律**：V2 四层 B 站链才是模型/所有者可见面；V1 遗留 `tools/bilibili` 经 `bootstrap.py` 注册进 V1 `ToolRegistry`，**不计 V2 对等**，本批未复用。本批与 [K18.search 搜索](V2_BILI_SEARCH.md) 同属读 API 广度缺口的逐项补齐。

## 实现（V2 四层）

- **纯领域 `domain/bilibili.py`（零 I/O）**：
  - `bounded_text(value, bound)`：校验并截断**纯文本**字段——`None→""`、非字符串或非法 Unicode（代理字符）`bilibili_response_invalid` 502、按 `bound` 截断。**与 `search_text` 的关键区别：不去 HTML 标记**。acc/info 的 `name`/`sex`/`sign` 是纯文本（非搜索高亮 HTML），若去标记会损坏合法尖括号内容（如签名 `签名<保留>文本`），故 whois 用 `bounded_text` 保留原文。
  - `whois_profile(data, mid)`：**身份必须匹配**——`uid(data["mid"]) != mid` 即 `bilibili_source_changed`（与 video 读 `bvid != target` 同纪律）；产出有界字段 `mid/name(≤100)/sex(≤20)/sign(≤200)/level(int|None)`。`mid` 目标复用既有 `uid` 校验（正十进制 ≤19 位且 <2^63），无需新验证器。
- **应用 `application/bilibili.py`**：`READ_SPEC`（`bilibili_read`）`operation` enum 增 `"whois"`、描述补「look up a public space profile by mid」；`read()`/`invoke()` 通用透传，凭据权威 CAS/admission ≤8/closing/shield 沿用。
- **适配器 `adapters/bilibili.py`**：`_read` operation 集合增 `whois`；`whois` 走 `mid = uid(target)` 校验（**取 cookies/client 之前**，非法 mid 零网络即拒 `bilibili_identity_invalid` 422），豁免 BV 身份；nav 登录后、`/view` 之前分派 `whois`。新增 `_whois(client, nav, mid)`：`wbi_keys(nav)`→`sign_wbi({"mid": mid}, img, sub, int(time.time()))`→既有泛型 `_get("/x/space/wbi/acc/info", signed)`→`whois_profile(data, mid)`。复用既有 `_client`（固定 API、`follow_redirects=False`、`trust_env=False`、timeout 8、池 ≤2）、`_slots`/`asyncio.timeout(20)`、`_reads` 准入与 `close` 结算。`whois` 为单资料读，忽略 `limit`（与 `account` 一致）。
- **HTTP `adapters/http.py`**：`BilibiliReadInput.operation` `Literal` 增 `"whois"`（REST 通用透传 handler 不变）；`target` `max_length=100` 契合 mid。

**生产接线**：模型工具（`READ_SPEC` enum）、REST（`BilibiliReadInput` Literal）、网关（`_read`/`_whois`）三面接受并执行 `whois`。`bilibili_read` 已在 `RETRIEVAL_TOOLS`，**路由不变**；只读 `writes=False`，无写事务/审批/outbox。

## 限额与取舍

- **有界纯文本字段**：name≤100、sex≤20、sign≤200；`level` 仅 int 否则 `None`（不 coerced）；**不去 HTML 标记**（区别于 search，保护合法尖括号）。
- **省略字段**：参考 whois 本身只读 `name`+`sign(≤60)`；V2 补 `mid/sex/level` 标准身份标量。**省略 `face`（头像 URL）/`official`/`vip`/`pendant`/`nameplate`/`fans`/`following`/`birthday`/`coins`/`tags` 等**（嵌套或隐私/噪声字段，bounded scope；fans/following 计数属 `x/relation/stat` 另一接口，不在 acc/info）。
- **身份匹配**：响应 `mid` 必须等于请求 `mid`，否则 `bilibili_source_changed`（不返回错配资料）。
- **单资料无分页**：whois 返回单个 mid 的资料，忽略 limit。
- **读 API 广度其余项仍缺**：弹幕（cid.xml deflate）、feed（popular/ranking/dynamic）、收藏夹/历史、msgfeed 收件箱、tag、缓存策略未实现（列后续批次）；搜索已在 K18.search 实现。
- **无新表/迁移/路由/worker 协议改动**；`routing.py` 不变；聚合 `docs/index` 更新暂缓（同 K16.locate/K18.search 纪律）。
- **真实送达需账号**：真实 acc/info 接口/风控/资料质量需真实 cookie 账号与网络，本批仅离线桩测（MockTransport canned nav+acc/info JSON），真实验收另列。

## 验证

- **新增 19 项回归**（`test_v2_bilibili_whois.py`：6 领域函数含参数化共 10 例 + 4 适配器函数含参数化共 9 例 + 1 工具/HTTP schema 接受）：`bounded_text` 保留标记/截断/None/非法 Unicode、`whois_profile` 有界字段/身份匹配/level 可选与文本默认/malformed 502、网关 WBI `mid` 签名 + 仅 nav/acc/info 两调用无 `/view`、非法 mid（空/非数字/0/负/20 位/含字母）零网络即拒 422、身份变更 `source_changed`、malformed 响应 502、`READ_SPEC` enum 与 `BilibiliReadInput` Literal 均含 whois。首轮全过。
- **调用方专项**：whois 19 + search 31 + bilibili 读调用方（gateway/subtitles/response_bounds/dash/merged/streams）共 **219 过 / 0 失败**（42.94 秒），证明纯增 whois 对既有读链无回归。（发表链 publication 测试的 2 个既有 `database_unavailable` 失败由其他会话未提交持久层编辑引起，已在 K18.search 经 scoped-stash 隔离实证与本读链无关，本批未重复跑发表链。）
- **严格类型**：`pyrightconfig.strict.json` **408 文件零错误/警告**（无新源文件，`bounded_text`/`whois_profile` 加入既有 `domain/bilibili.py`，include 不变；filesAnalyzed==include）。
- **Ruff**：check + format 通过于 `domain/bilibili.py`、`application/bilibili.py`、`adapters/bilibili.py`、`test_v2_bilibili_whois.py`。`adapters/http.py` 仅改 `BilibiliReadInput` 一行 Literal；其 `background_review` 区既有格式偏差（HEAD 即如此，非本批引入）未重格式化。
- **项目索引**：聚合 `docs/index` 暂缓提交（再生会编入他会话未提交持久层编辑的全局 source_sha256）；无新源文件，strict include 不变；modules/routes/declared_tools 不变（whois 是既有工具的新 operation）。
- `scripts/task_status.py --check` exit 0；`DEVELOPMENT_STATUS.md` 由父任务生成，子任务 followup 不改其内容。
- **未全量、未真实账号/模型/设备/72 小时、未停止或升级运行中 Windows 服务与数据库。**

## 完成条件

K18.whois 实现**不宣称读 API 广度全集对等**：仅补「space whois」一项只读 operation（WBI 同公开规范 + 既有凭据 CAS/CDN/固定 API/身份匹配/有界纯文本/显式格式失败），弹幕/feed/收藏夹历史/收件箱/tag/缓存策略仍为可离线缺口列后续批次；写广度/自主观看/受控分享策略/直播/AI-share 守卫仍缺。真实 acc/info 送达/风控/资料质量需账号网络另验。`whole_core_parity_complete`、`whole_reference_feature_audit_complete`、`whole_v2_complete` 保持 false（K01 partial、K13–K18 其余可离线缺口未实现、真实账号/设备/模型质量与 72 小时验收待办）。后续继续择机实现其余可离线缺口（独立批次，先授权/持久化合同后执行接线，经 V2 四层不复用 V1 ToolRegistry），并补 K01 缓存范围，整个 V2 开发冻结后统一全量与实机验收，其他对标项不提前关闭。