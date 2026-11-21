# V2 工具结果图像协议实现合同（K16 方案 B 独立合同，冻结图像协议扩展）

## 状态

**设计合同（design_contract_recorded）→ 所有者已批准 D5（2026-11-21），1a 持久化批已落地。** 所有者先于 2026-11-21 在 [V2_TOOL_IMAGE_VISIBILITY.md](V2_TOOL_IMAGE_VISIBILITY.md) 批准方案 B（同步工具结果图像可见，完整对标参考 zoom），随后批准本合同 D5 持久化重设计（冻结触发器重建 + run 终态释放语义）。该草案明言「触及冻结图像协议 + 计量 + 策略，需独立合同 + 既有图像协议全量零回归」——本合同即该独立合同。**1a 已实现**：迁移 v0050（账本表重建 message_id 可空 + scope 双分支 + run 终态释放触发器）、schema.py 同步、`model_images_prepare` tool 分支、5 专项（回退 4/5 红）+ 147 调用方零回归 + strict 418 文件 0 错误。余 1b 协议+引擎 / 2 media_zoom / 3 端到端三批。

**关键勘定：草案「持久层无需改」前提被源码证伪。** 工具转录从不持久化（事实 1），图像账本 `model_call_images.message_id` 非空外键（事实 3），冻结触发器 `call_image_scope` 要求完整 message-part 链（事实 3），`message_image_insert` 只允许所有者 user 输入消息带图像（事实 4）。方案 B 因此**必须**包含迁移 v0050（重建账本表 + 双分支 scope 触发器 + run 终态释放），触及冻结安全触发器面并引入新的生命周期语义（D5）——超出草案描述，按「先授权/持久化/安全合同后执行接线」纪律提请所有者批准后方可实现。

`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。

## 勘定事实（实读源码，2026-11-21）

1. **工具转录仅存内存**：`engine.py:340` `messages = prepared["request"]`（每 run 一次经 `contexts.prepare` 从持久化行重建）；`:419`（并行）/`:443`（顺序）追加 `{"role":"tool","tool_call_id":…,"content":result.text}` 纯文本；run 内后续模型调用直接复用该内存列表（`:360-366`），**不**每步重建；只有 `finish`（`repository.py:556`）持久化 assistant 终稿。持久层无任何 `role='tool'` 消息行（grep 证实）。
2. **canonical 请求与图像流**：`ContextService.prepare`（`context.py:92-254`）→ `ImageService.context`（`images.py:65-81`）→ `with_images`（`domain/images.py:162-207`）从 DB `message_parts` 附着图像并做「全上下文最后 per_call_limit 个胜出」选择；`references()` 每次调用校验（`model_calls.py:132`）；`model_images_prepare`（`image_repository.py:168-236`）按**最新快照**的 message parts 逐 ref 核验并写账本；`wire`（`images.py:91-132`）每次调用重新取对象字节并完整性校验。
3. **账本表与冻结触发器**（`schema.py:2945-2966`、`v0032.py:15-38`）：`model_call_images.message_id VARCHAR(36) NOT NULL` + scoped FK→messages；`call_image_scope`（v0032:26）INSERT 前要求 call reserved+purpose turn、快照属本 run、policy 属 run 模型修订且 openai_low、part 经 context_messages→message_parts→messages→objects 全链 join 且 sha 匹配、state prepared、未擦除；`call_image_budget`（:27）每 call ≤per_call_limit 且 run 累计 ≤run_budget；`call_image_state`（:28）call 结算→attached/unknown/not_sent；`call_image_erasure`（:29）消息 deleted_at→账本 object_id=NULL+erased_at；`call_image_update`（:30）身份列不可变、object_id 置空仅允许「匹配消息已按该 erased_at 删除」的擦除模式、state 跃迁仅 prepared→dispatched→attached/unknown/not_sent；`call_image_delete`（:38）账本不可删。
4. **message_image_insert**（v0032:23）：image part 只能落在 `role='user' AND run_id IS NULL AND deleted_at IS NULL` 的消息上，每消息 ≤4，且对象已注册。
5. **image_parts 角色无关**（`image_repository.py:97-144`，join messages 仅过滤 deleted_at）——草案此点核验正确；但工具消息无行可 join。
6. **对象生命周期**：`OBJECT_REFERENCES` 自动派生自所有指向 objects.id 的 FK（`schema.py:3523-3528`）→ 账本 object_id 计入引用；`object_remove`（`object_repository.py:150-162`）要求引用=0+已过期+未 pin；对象 TTL 1 天（`objects.py:60`）。今日唯一释放路径是消息擦除触发器（事实 3）。工具图像若无释放路径，其对象**永久**不可收集（存储泄漏）。
7. **references() 现状**（`domain/images.py:109-159`）：omni_image 强制 `role=="user"`，严格键集/detail=low/UUID/ImageInfo/token_cost∈LOW_MODELS 值集/part_id 唯一/总 ≤MAX_IMAGES=4；返回 part 字典平铺列表。消费方：`model_calls.py:132,196`（refs→prepare）、`images.py:75,100`（真值/wire 字段访问）。既有测试按字段访问（`test_v2_images.py:269,547`），**附加 role 键不破坏**。
8. **with_images 只处理持久化行**——工具消息永不经过它；草案「with_images/context 支持 tool 消息」落实为**引擎侧附着 + prepare 侧 tool 分支核验**（D3/D4），with_images/context 本体不改。
9. **计量与压缩兼容**：`serialized_request`（`metering.py:37-40`）泛型 JSON，list content 安全；`bounded_tool_summary_sources`（`context.py:87-106`）对 list content 做 json.dumps——原始 part 元数据会进摘要 prompt（D7 处理）。
10. **ImageInfo 依赖方向**：`domain/images.py` 已 import `.contracts`（BusinessError）；ImageRef 若放 contracts 并复用 ImageInfo 校验则成环。ImageInfo 被 4 个 src 模块自 domain.images 导入（`adapters/images.py:16`、`image_repository.py:11`、`ports.py:15`、`application/images.py:10`）。
11. **测试面**：`integrations/test_v2_images.py`（12 例）、`http/test_v2_images_http.py`（1 例）、`integrations/test_v2_images_migration.py`（v0031→v0032 升级不伪造账本）、`omni_test_support/v2/images.py`；媒体 `_register_sheet`（`media.py:444-483`）publish+prepare 帧_sheet 对象（zoom 作业产物即此路径）。

## 设计决策

### D1 工具转录保持内存态

不为工具结果新增 messages 行。历史/UI/保留/擦除语义零改动；避免 context_plan 过滤、seq 排序、孤儿 tool 行等大面积回归。工具图像的身份与计量由账本 tool 行（message_id=NULL）承载。

### D2 域协议（domain/contracts.py + domain/images.py）

- **ImageInfo 与 MAX_IMAGES 移入 `domain/contracts.py`**（纯值合同归位，解 ImageRef 依赖环）；`domain/images.py` 改自 `.contracts` 导入；同步更新事实 10 所列 4 个 src 导入点与测试导入点（共享签名变更，覆盖调用方）。不留 re-export。
- **ImageRef**（contracts.py，frozen dataclass）：`object_id/media_type/width/height/sha256/bytes`；`__post_init__` 校验 object_id 规范 UUID + 委托 `ImageInfo(...).validate()`（复用既有校验，不复制规则）。
- **ActionResult.images: tuple[ImageRef, ...] = ()**：`__post_init__` 校验元素类型 + `len ≤ MAX_IMAGES`；缺省空元组使全部既有构造字节等价。
- **references()**：omni_image 接受 `role ∈ {"user","tool"}`，part 键集/字段校验**逐字不变**；返回 `{**item, "role": message_role}`（附加键，消费方字段访问不受影响）；user 分支语义与错误码不变；总 ≤MAX_IMAGES 硬顶不变。

### D3 引擎附着（engine.py 两处追加 site 共用 helper）

- `result.images` 空 → `content=result.text`（字节等价，零回归）。
- 非空 → 读 `run_image_policy`（一次 DB read）+ settings model：
  - `protocol=="disabled"` → content=[text part] + 每图一条 `[Image registered but not attached: image_capability_disabled; do not infer its content.]`（镜像 with_images 措辞）。
  - `openai_low` → 本结果至多附着 `per_call_limit` 个 omni_image part（part_id 新 uuid、policy_id=config["id"]、token_cost=model_tokens(model)、detail="low"、字段取 ImageRef），超出者附 `context_image_limit` 通知。
  - **全局最后限额胜出**：附着前若内存 messages 中既有 omni_image part 总数 + 新附数 > per_call_limit，把最旧的超额 part 降级为 `context_image_limit` 文本通知（镜像 with_images 的全上下文选择语义；已 dispatched 的历史账本事实不改）。
- run_budget **不**在引擎预检：唯一执行点仍是 `model_images_prepare`/`call_image_budget`（超限 → image_budget_exceeded，run 失败——与 user 图像既有语义一致）。工具批（批次 2）的能力可在产出前查预算避免浪费作业。
- 远程 run registry={} 语义不改；协议对工具来源无关（扩展/MCP 返回 images 同样适用）。

### D4 model_images_prepare tool 分支（image_repository.py）

- ref 按 `ref.get("role","user")` 分流（缺省 user，既有直调测试不受影响）。
- **user 分支逐字不变**（快照 source 匹配、policy/token_cost/字段核验）。
- **tool 分支**：`part_id` 不得存在于 message_parts（防冒充消息 part）；按 object_id 查 agent 域 `image_objects` join `objects`，核验 sha256/bytes/width/height/media_type 与 ref 一致；policy_id==config["id"]、token_cost==model_tokens(settings model)；账本行 `message_id=NULL`。
- spent 计数/per_call_limit/run_budget 检查对两类 ref 一致适用（不改）。快照存在性要求不变（tool 行引用本 run 最新快照）。

### D5 迁移 v0050：账本表重建 + 触发器重设计（**需所有者批准**）

重建 `model_call_images`（SQLite 无法 ALTER 掉 NOT NULL/FK）：`message_id VARCHAR(36) NULL`，FK/其余列/CHECK/唯一约束逐字保留；**表数不变（145），无新表**。复制旧行、drop、rename；重建全部触发器：

- `call_image_scope` 双分支：
  - **message 分支（message_id NOT NULL）：v0032 原 SQL 逐字保留**——user 图像守卫零弱化。
  - **tool 分支（message_id IS NULL）**：call reserved+purpose turn、快照属本 run、policy 属 run 模型修订且 openai_low、对象经 image_objects+objects 注册且 sha 匹配、`part_id` 不在 message_parts、state prepared、erased_at NULL；任一不满足 RAISE(ABORT,'image call source or policy scope mismatch')。
- `call_image_budget`/`call_image_state`/`call_image_erasure`/`call_image_delete`：**逐字重建**（消息擦除路径不变；tool 行 message_id NULL 天然不被消息擦除触发器命中）。
- `call_image_update`：身份列（含 message_id）不可变逐字保留；object_id/erased_at  clause 扩展**仅**允许 tool 行释放模式：`old.message_id IS NULL AND new.message_id IS NULL AND new.object_id IS NULL AND old.object_id IS NOT NULL AND new.erased_at IS old.erased_at AND EXISTS(经 call→run 终态)`；其余跃迁规则不变。
- **新增 `call_image_tool_release`**：`AFTER UPDATE OF state ON runs WHEN new.state IN ('succeeded','failed','cancelled','interrupted')` → 把本 run 各 call 的 tool 账本行 `object_id=NULL`（erased_at 保持 NULL——**释放≠擦除**；sha256/part_id/state 保留，账本不可删不变）。
  - **语义理由**：run 内每次模型调用都重新附着并 re-wire 同一批 part（预算按 call 累计），对象字节必须活到 run 终态；终态后转录消亡、不可能再发送 → 释放引用 → TTL 到期后 object_remove 可收集（堵事实 6 的存储泄漏）。unknown 结算同样释放（unknown 绝不重放）。
  - CHECK `(erased_at IS NULL OR object_id IS NULL)` 允许 object_id NULL + erased_at NULL，无需改 CHECK。
- `schema.py` model_call_images.message_id 改 nullable；`OBJECT_REFERENCES` 自动派生不变。
- 同步所有覆盖 model_call_images 的 DDL/版本 pin（实现时 grep 勘定，含 v2_0049 head pin 测试链）；新增 v0050 迁移测试（幂等升级保留旧行、触发器全等、downgrade 拒绝）。

### D6 零回归保证

user 图像全链语义字节等价：message 分支触发器原 SQL、references user 校验、with_images/context/wire/image_admit/summary_sources/report 本体不改；既有 12+1 图像用例与媒体 sheet 用例不改断言通过（references 附加 role 键经字段访问核验安全）；`model_images_report` 的 parts 驱动 unattached 逻辑不受 tool 行影响（tool 行出现在 requests 账本，sha 保留）。

### D7 压缩摘要源渲染

`bounded_tool_summary_sources`（domain/context.py）把 omni_image part 渲染为有界文本标记 `[image attached to tool result]` 而非原始 part JSON——摘要 prompt 不泄漏 part_id/object_id/sha 元数据，字节永不入摘要；K06 窗口/预算逻辑不变。

### D8 批次划分

1. **1a 持久化批**：v0050 + schema + D4 prepare tool 分支 + 仓库级专项（tool ref 接受/拒绝矩阵、user 不变、释放/擦除/身份触发器、对象收集阻塞与放行）+ 图像/媒体调用方全量零回归。
2. **1b 协议+引擎批**：D2/D3/D7 + 专项（ImageRef/ActionResult 校验、references tool 分支、引擎附着 disabled/限额/降级/字节等价、wire tool part、压缩标记）+ 回退验证 + 调用方。
3. **2 media_zoom 工具批**：按草案决策 2/3（writes=True 审批门、frames 作业复用、ImageService.prepare 注册、ActionResult.images 返回）。
4. **3 端到端批**：按草案步骤 3。

每批：小步 `-n 0` 专项、新增用例回退验证、strict 仓库根、ruff、证据/HANDOFF/任务 JSON、精确提交推送。

## 有界与安全

per_call_limit≤4/run_budget≤64/MAX_IMAGES=4/detail=low/LOW_MODELS 计量全部不变；工具图像只能来自已注册已校验对象（ImageService.prepare 路径），任意 URL/data URL 仍拒；wire 完整性校验不变；协议批不新增工具，media_zoom 写动作经所有者审批门；unknown 绝不重放；账本不可删不变；释放仅 run 终态；远程 run 不暴露本地工具语义不变。

## 测试计划（要点）

- **1a**：v0050 幂等/旧行保留/触发器全等/downgrade 拒绝；tool ref：注册对象接受、未注册/sha 不符/part_id 冒充/policy 不符/预算耗尽拒绝；user ref 既有行为不变；释放触发器仅终态+仅 tool 行；身份触发器仍拒篡改；run 存活期 object_remove 被阻、终态+TTL 后可收集。
- **1b**：ImageRef/ActionResult 校验矩阵；references tool 严格键集+role 标签+user 不变；引擎：无图字节等价/disabled 通知/per_call_limit 截断/全局降级/并行批多图；wire tool part 物化；压缩标记；回退探针（摘附着/摘 tool 分支 → 新增用例全红，静态合同钉留绿）。
- **2/3**：见草案步骤 2-3（审批拒绝/取消/过期、凭据/源 CAS、删除屏障、未知不重放、取消等待真实清理）。

## 边界

本段为设计批时点快照（当时纯合同，无源码/测试/迁移改动）。D5 已获所有者批准（2026-11-21）并由 1a 持久化批落地（v0050 + schema + prepare tool 分支 + 专项/调用方/strict 全绿，见 docs/evidence/v2-tool-image-persistence.json）；1b/2/3 批待实现。K16 HSV 切点（#1）/Laplacian 选帧（#5）已 K16.scene_frames 落地，帧内 OCR 仍后续；真实视频视觉质量另验。`whole_*_complete` 保持 false，不提前关闭任何对标项。