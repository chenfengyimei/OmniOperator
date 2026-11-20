# V2 B 站 DM 关注关系读面 relation（K18.relation）

状态：已实现专项通过（2026-11-20，合同先于接线）。承接 [B 站全集审计](V2_BILI_WHALE_AUDIT.md) (a) 读 API 参考集明列 `x/relation`（DM 关系），而 V2 `bilibili_read` operation enum 无 relation（`following` 是关注**动态 feed** `polymer/web-dynamic/v1/feed/all`，非关系）。学习参考 `xuexi/dsh-bili-whale` `tools/commands/dm.mjs:117` `relation(toMid)`（`x/relation?fid=<mid>` 实测形状）与 `tests/review.test.mjs:31`（relation **永不缓存**），独立实现于 V2 既有四层 bilibili 链，复用既有凭据权威 CAS/operation 路由/共享限额/`uid` 校验，无新依赖/源文件。

## 缺口与动机

参考 DM 发送前必须确认互关（`relation.attribute === 6`），关系读是私信表达的前置发现面。V2 模型读缺 relation operation，无法在准备 private_message 前感知 owner↔target 关注方向/互关/拉黑。本批补**只读发现** operation=relation，让模型/所有者看清关系；**不改写路径**——V2 prepare→所有者审批→outbox→已审浏览器表单投递保持不变，relation 仅作发现提示，绝不作发送硬门（送达由所有者审批决定，与参考自动拒发不同纪律）。

## relation API 形状（参考 dm.mjs 实测）

- 端点 `GET x/relation?fid=<mid>`（**plain GET，非 WBI 签名**；参考 `jget` 直连，V2 `_get` 泛型 GET 命中）。**需登录**（关系属 owner 私有视角，V2 `_login_nav` 登录门沿用）。**永不缓存**（参考 review.test 明列 `/x/relation` 与 player/playurl 同属不可缓存；V2 当前无缓存层，天然不缓存）。
- 响应 `{code:0, data:{attribute:<int>, be_relation:{attribute:<int>}}}`（`_get` 解包 data，code!=0/风险码已通用处理）：
  - `attribute`：**owner→target** 关系码——`0` 未关注、`2` 已关注、`6` 互关、`128` 拉黑（参考 `relationLines` outgoing=`[2,6].includes(attribute)`、互关=`attribute===6`、拉黑=`attribute===128`）。
  - `be_relation.attribute`：**target→owner** 反向关系码，**可空**（参考 `j.data?.be_relation?.attribute == null ? null : Number(...`，incoming=`attribute===6 || beAttribute∈[2,6]`）。

## 范围（本批）

1. **domain/bilibili.py**（既有模块，无新源文件）：
   - 常量 `RELATION_FOLLOWED=2`/`RELATION_MUTUAL=6`/`RELATION_BLOCKED=128`（关系码，自文档）。
   - `relation_view(data, mid) -> dict`：`attribute=data.get("attribute")` **须 int** 否则 `bilibili_response_invalid` 502（核心字段格式漂移显式失败，绝不静默误读）；`be_relation` 经 `_as_dict`（可选辅助字段，缺失/非 dict→{}→be_attribute None，同参考可选链容忍）；`be_attribute=be_relation.get("attribute")` int 否则 None。返回 `{mid, attribute, be_attribute, follows: attribute∈(2,6), followed_by: attribute==6 or (be_attribute is not None and be_attribute∈(2,6)), mutual: attribute==6, blocked: attribute==128 or be_attribute==128}`。**纯发现提示**，mid 已由适配器 `uid` 校验透传。
2. **adapters/bilibili.py**：import relation_view；`_relation(client, mid)`：`data=await self._get(client, "/x/relation", {"fid": mid})` 再 `relation_view(data, mid)`（**无 WBI 签名、无缓存**）；`_read` operation whitelist 增 `relation`、错误消息增 relation、target 校验 `elif operation in {"whois","relation"}: mid=uid(target)`（relation target 是 mid，分派前/网络前 422 拒非法）、dispatch `if operation=="relation": return await self._relation(client, mid)`（whois 后，nav 登录门后）。URL/凭据 CAS/login required/limit/timeout 20/共享 slots 不变。
3. **application/bilibili.py** READ_SPEC：operation enum 增 `relation`；描述增「relation targets are mids returning the owner↔target follow relationship (attribute/be_attribute/mutual/blocked, discovery only, never gates the shared approval/outbox send)」。target 仍 string≤100（mid 由 uid 域校验），limit≤20（relation 单条忽略 limit 但沿用准入）。
4. **adapters/http.py** BilibiliReadInput operation Literal 增 `relation`（所有者 REST 读面同步）。
5. **routing/ports 不变**：bilibili_read 已在 RETRIEVAL_TOOLS 只读 agent 档；无新路由/表/迁移/协议帧/源文件。

## 语义与零回归

- **纯只读发现**：relation 不写、不审批、不投递；**prepare/outbox/已审浏览器表单投递写路径完全不变**，relation 不作发送硬门（与参考自动拒发不同——V2 送达由所有者审批）。
- **既有 operation 字节等价**：whitelist/target 校验/dispatch 仅**增** relation 分支，whois 由 `==` 改 `in {whois,relation}` 对 whois 行为不变；account/video/comments/replies/search/danmaku/popular/ranking/following/favorites/tags/history/msgfeed 全不受影响。
- **显式失败**：attribute 非 int→502；非法 mid→422（uid，网络前）；code!=0/风险码→_get 通用 502/403；login required 沿用。
- **有界**：单 mid 单条，2 个 int + 4 个 bool + mid，远小于 MAX_FRAME；timeout 20、共享 slots≤8、凭据权威 CAS（read 前后 bilibili_read_authorized）沿用；**无缓存**（关系实时）。
- **be_relation 可选容忍**：缺失/非 dict→be_attribute None（反向未知），followed_by 正确处理 None（仅靠 mutual 判定），blocked 的 `be_attribute==128` 对 None 为假。

## 明确不做（列后续）

- relation 作 private_message prepare 的**硬门**（V2 送达由所有者审批，不自动拒发；是否加互关前置校验属写路径合同，需所有者决策，本批不做）；关注/取关/拉黑**写**（x/relation/modify）；粉丝/关注**列表**分页（x/relation/followings、x/relation/fans，本批仅单 mid 关系属性）；DM 发送写广度（K18 (b) 需账号网络）。真实送达需账号网络。不宣称全集对等。

## 验证计划

domain relation_view（attribute int 解析、be_relation 缺失/非 dict→None、follows/followed_by/mutual/blocked 派生各码组合 0/2/6/128、attribute 非 int 显式 502、mid 透传）；网关 relation（/x/relation?fid=<mid> URL+query、plain GET 无 WBI、login required、凭据 CAS、非法 mid 网络前 422、code!=0→502、attribute 非 int→502、be_relation 可选）；spec/HTTP relation 暴露；调用方 bilibili 读全集 + worker_safety 零回归（既有 operation 字节等价）。strict 本批零新错误（无新源文件，余 5 既有 repository.py 不追逐）；ruff。未全量；whole_*_complete 保持 false。

## 验证结果（2026-11-20，base_commit cfa3c4b6）

- **专项** `uv run --no-sync pytest -q -n 0 tests/v2/integrations/test_v2_bilibili_relation.py` → **20 passed**（relation_view 互关双向/未关注/单向关注/be_relation 反向关注/owner 拉黑/反向拉黑/be_relation 缺失或畸形→未知、attribute 非 int ×4 显式 502、网关 plain GET fid 无 WBI、非法 mid ×6 网络前 422、网关 malformed 502、spec/HTTP 暴露）。
- **调用方回归** bilibili 读全集（test_v2_bilibili + gateway + whois + msgfeed + search + response_bounds + merged + worker + relation）→ **201 passed / 0 failed**（45.93 秒，含 relation 20，既有 181 bilibili 用例字节等价零回归——whois mid 校验分支由 `==` 改 `in {whois,relation}` 对 whois 不变）；capabilities + business_inventory + integrations/routing → **21 passed**（31.51 秒，spec enum widen 不破库存/路由，declared_tools 仍 94 无新工具）。
- **strict** 仓库根 `pyright -p pyrightconfig.strict.json`（pyright 1.1.414）→ **414 files / 5 errors / 0 warnings**，5 错误全为既有 repository.py（L633/659×2/710×2，他会话已提交 6bcb0e98，不追逐），**本批零新错误**（无新 agent/src 源文件，relation_view 加于既有 domain/bilibili.py、_relation 加于既有 adapters/bilibili.py；新测试文件不在 strict include（仅 agent/src），计数仍 414）。
- **ruff** domain/bilibili.py、adapters/bilibili.py、application/bilibili.py、adapters/http.py、test_v2_bilibili_relation.py 五文件 check + format 全通过（test 一长行折行）。
- 未跑全量（本批为既有 bilibili 读链增量 operation，专项 + 调用方已覆盖受影响面）；whole_core_parity_complete / whole_reference_feature_audit_complete / whole_v2_complete 保持 **false**。

### 指纹（sha256）

- domain/bilibili.py `9b40f556858ddb3b6ce85af5e0628404c65215c598a4c58e5166be5699322dff`
- adapters/bilibili.py `624fbc212459054c096660dded80bcb2e924acaa113483db69839d07b330b80c`
- application/bilibili.py `900fbb8677e3b6c1f6ac6e421a5c03f1d0f3f84567b2d3d4dfdfe8b6f76817cc`
- adapters/http.py `6b658e0619a7f8404a4301fbf7667d40986d740ef8d2ab4e4810df63d329528f`
- test_v2_bilibili_relation.py `f2f0241b4d8196fbac7ebe117b0b2b5d6c2aa0091efbc7fdfefbc80a94c176b0`

（合同自身指纹见证据 `docs/evidence/v2-bili-relation.json` source_files，避免自指。）