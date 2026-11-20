# V2 B 站收件箱「收到的赞」msgfeed like（K18.msgfeed_like）

状态：已实现专项通过（2026-11-20，合同先于接线）。承接 [B 站收件箱 msgfeed](V2_BILI_MSGFEED.md)（K18.msgfeed，已实现 reply/at 两种 kind；[K18 审计](V2_BILI_WHALE_AUDIT.md) 读 API 广度明列 msgfeed `reply|at|like`）。学习参考 `xuexi/dsh-bili-whale` `tools/commands/likes.mjs`（`x/msgfeed/like` 实测形状）与 `tools/lib/msgfeed.mjs`，独立实现于 V2 既有四层 bilibili 链，复用既有凭据权威 CAS/operation 路由/共享限额，无新依赖。

## 缺口与动机

V2 `bilibili_read` operation=msgfeed 的 `msgfeed_kind` 仅接受 `reply`（回复我的）/`at`（@我的），缺参考第三种 `like`（收到的赞）。like 通知流给出**评论区点赞名单拿不到、但通知流里给的那份**点赞者（`users[]`），是所有者感知互动的重要只读发现面。

## like API 形状（与 reply/at 显著不同，参考 likes.mjs 实测 2026-10-18）

- 端点 `GET x/msgfeed/like?build=0&mobi_app=web`（V2 适配器 `_msgfeed` 已泛化 `/x/msgfeed/{kind}` + `platform=web&build=0&mobi_app=web`，kind=like 自动命中）。
- 结构 `{ latest:{items}, total:{cursor,items} }`——**不是** reply/at 的 `data.items`；参考合并 `latest.items + total.items + data.items` 按 `String(id)` 去重。
- 每条 `{ id, users:[{mid,nickname}], item:{...}, counts, like_time, notice_state }`：
  - `like_time`（非 reply_time/at_time）；`counts` 总赞数；`users[]` 是**点赞者数组**（非 reply/at 的单个 `user`）。
  - `item` 同 reply/at 的发现提示（title/source_content/business_id/subject_id/root_id/source_id/uri），**title/source_content 不可靠仅提示**，权威正文经 replies/视频读，绝不从通知推断。

## 正确性修订（2026-11-20）

`latest`/`total` 容器缺失或 null 仍表示该分支未提供；若提供数组、字符串、数值或布尔而非对象，必须明确返回 `bilibili_response_invalid` 502，不能借 `_as_dict` 静默折为空收件箱。首批验证与指纹保留为历史快照，修订验证另记。

## 范围（首批历史）

1. **domain/bilibili.py**（既有模块，无新源文件）：
   - `msgfeed_kind` 接受 `reply`/`at`/`like`（其余 ValueError→BusinessError `bilibili_read_invalid` 422，分派前/网络前拒）。
   - 常量 `MSGFEED_LIKE_MAX_USERS = 20`（单条点赞者样本上限，有界）。
   - `msgfeed_like_rows(data, limit) -> dict`：合并 `data.latest.items`+`data.total.items`+`data.items`（各分支 None 跳过、非 list→`bilibili_response_invalid` 502），逐条须 dict 且 `id` 须 int（否则 502），按 id **去重保序**（latest 先）；合并去重后 `len > FEED_MAX_ROWS(50)`→502。逐条 `[:limit]`：`time`=like_time(int 否则 None)、`counts`(int 否则 None)、`users`（None→[]、非 list→502、截断 ≤`MSGFEED_LIKE_MAX_USERS`、每个须 dict 否则 502，`{mid:str(int 否则""), nickname:search_text(...,100)}`）、`item` 发现提示同 msgfeed_rows（business_id int 否则 None、subject_id/root_id/source_id str(int 否则"")、uri/title/source_content search_text 300）。返回 `{items, has_more: len(merged)>=limit}`。
2. **adapters/bilibili.py** `_msgfeed`：`rows = msgfeed_like_rows(data, limit) if kind == "like" else msgfeed_rows(data, limit)`；返回 `{feed:"msgfeed", kind, **rows}`；import msgfeed_like_rows；docstring reply/at→reply/at/like。URL/凭据 CAS/login required/limit/timeout 不变。
3. **application/bilibili.py** READ_SPEC 描述 `msgfeed targets are reply/at`→`reply/at/like`（operation enum 已含 msgfeed，**like 是 target kind 非 operation，enum/schema 不变**）；target 仍 string≤100，kind 由 msgfeed_kind 分派前校验。
4. **routing/ports/http 不变**：bilibili_read 已在 RETRIEVAL_TOOLS 只读 agent 档；BilibiliReadInput target 自由串（kind 域校验）；无新 operation/路由/表/迁移/协议帧。

## 语义与零回归

- **reply/at 完全不变**：`msgfeed_rows`/`_msgfeed` 非 like 分支字节等价；既有 reply/at 域/网关/spec 测试零回归。
- **like 独立解析器**：因形状根本不同（latest/total 嵌套、users[] 数组、like_time、counts），不复用 msgfeed_rows（避免污染 reply/at 合同）；独立 `msgfeed_like_rows` 显式失败（502）于格式漂移，绝不静默误读。
- **发现提示非权威**：like 的 title/source_content 仅提示，users 是点赞者样本（counts 为总数），权威内容经其他读操作；与 reply/at 同纪律。
- **有界**：合并去重 ≤FEED_MAX_ROWS(50)、users ≤20、limit≤20、search_text 100/300、timeout 20/25；凭据权威 CAS（read 前后 bilibili_read_authorized）+ login required + 共享 slots 沿用。

## 明确不做（列后续）

- like 的 cursor 分页（参考 total.cursor；本批首页有界合并，has_more 标示）；notice_state 过滤；自主点赞反馈检测/偏好学习（K18 (d) 自主缺口，需账号+模型）；写广度（fav/dynamic/danmaku-send/comment-del）；直播。真实送达需账号网络。不宣称全集对等。

## 验证计划

domain msgfeed_kind 接受 like/拒其余；msgfeed_like_rows（latest+total 合并去重保序、like_time/counts/users 数组解析、users 截断 ≤20、item 发现提示 markup 剥离、absent→空、limit/has_more、 malformed 分支非 list/非 dict/id 非 int/users 非 list/超 FEED_MAX_ROWS 显式 502）；网关 like（/x/msgfeed/like URL+query、kind=like、凭据 CAS、login required、invalid kind 网络前拒、empty 正常、malformed 502）；spec/HTTP msgfeed 暴露不变；调用方 msgfeed+bilibili 读+worker_safety 零回归。strict 本批零新错误（无新源文件，余 5 既有 repository.py 不追逐）；ruff。未全量；whole_*_complete 保持 false。

## 验证结果（2026-11-20，base_commit 5fa88d0f）

- **专项** `uv run --no-sync pytest -q -n 0 tests/v2/integrations/test_v2_bilibili_msgfeed.py` → **37 passed**（22 既有 reply/at + 15 新 like：kind 接受 like、like_rows 解析 latest/total 去重保序、absent 空、flat items 回退、users 截断 ≤20、缺字段默认、limit/has_more、malformed ×7 显式 502、网关 like 收件箱 URL/query/CAS、网关 like malformed 502）。
- **调用方回归** msgfeed + test_v2_bilibili + gateway + worker + response_bounds + merged → **131 passed**（reply/at 字节等价零回归；strict 类型收窄重构后重跑仍 131）。
- **strict** 仓库根 `pyright -p pyrightconfig.strict.json`（pyright 1.1.414）→ **414 files / 5 errors / 0 warnings**，5 错误全为既有 repository.py（L633/659×2/710×2，他会话已提交 6bcb0e98，不追逐），**本批零新错误**（无新源文件，未改 include 列表）。
- **ruff** domain/adapters/application/test 四文件 check + format 全通过。
- 未跑全量（本批为既有 bilibili 读链增量 kind，专项 + 调用方已覆盖受影响面）；whole_core_parity_complete / whole_reference_feature_audit_complete / whole_v2_complete 保持 **false**。

### 指纹（sha256）

- domain/bilibili.py `6976c44ed8a0849c908ffe022b0d591d6aec074ae5902ccfdebe15535e490a20`
- adapters/bilibili.py `b4ffd7301709e0f9cadcf62da54a43c2b7589917e09f6c461d1d041dda1550e8`
- application/bilibili.py `42940ba28de202f4af8c1ddd1f3ae5e9dd33f26cea1e4d88c6025d2d1f297600`
- test_v2_bilibili_msgfeed.py `84d1c3f394c0ce1a9c11061e6ef93d797d3d879ab4e3a511125f1c5e86dcef89`

（合同自身指纹见证据 `docs/evidence/v2-bili-msgfeed-like.json` source_files，避免自指。）