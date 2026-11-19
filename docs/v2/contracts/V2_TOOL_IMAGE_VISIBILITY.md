# V2 工具结果图像可见协议与模型侧 media_zoom 设计合同（K16 缺口 #3 完整形态实现前合同）

## 状态

**设计合同（design_contract_recorded），非实现。** K16 缺口 #3「模型侧自动 zoom」仍未实现；本批交付实现前必须的**工具结果图像可见协议 + 模型发起媒体处理授权**设计（按总对标纪律「先授权/持久化合同后执行接线」）。已实现的 [K16.zoom_foundation](V2_MEDIA_ZOOM.md)（所有者侧 frames 显式时间戳高清重取）是其作业基础。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。

## 参考与 V2 现状（实读源码）

参考 `xuexi/dsh-video-storyboard`（`e124ae6f`）`zoom.js:20`：模型发起格号→绝对时间高清重取，**并让模型看见该帧**。V2 已有原语：
- `media_cells`（K16.cell_anchor，cell→亚秒时间）、frames 作业显式 `timestamps`（K16.zoom_foundation，`count=1,width=1280,timestamps=[t]` 单帧高清）。
- `ImageService`（`application/images.py`：`upload`/`prepare`/`context`/`wire`）、`image_objects`/`message_parts(kind=image)` 持久化、`run_image_policy`（`protocol disabled|openai_low`、`per_call_limit≤4`、`run_budget≤64`）、`domain/images.py` `references`/`with_images`/`ImageInfo` 校验、低detail计量 `LOW_MODELS`。
- 写工具所有者审批门（`application/engine.py:772-792`：`prepare_action`→等待 `approved/denied/cancelled`（`approval_state pending`+`approval_expires_at`）→`authorize_action`→资源锁→`invoke`）。

**关键阻塞（工具结果同步图像可见无现成机制）：**
1. `ActionResult`（`domain/contracts.py:68-77`）字段 `status/text/effect/error_code/retryable/artifacts/verification/data`，**无图像字段**（`artifacts: tuple[str,...]` 未被 engine 用于图像附着）。
2. `domain/images.py:119-122` `references()` 强制 `omni_image` 部分 `message.role=="user"`；**tool 消息（role=="tool"）不能携带 omni_image**。
3. `application/engine.py:404,428` 工具结果按 `{"role":"tool","tool_call_id":…,"content":result.text}` **纯文本**追加。
4. `adapters/persistence/image_repository.py:97 image_parts` 经 `message_parts(kind=image)` join `image_objects`/`objects`/`messages`（按 agent_id+message_id+`deleted_at IS NULL` 过滤，**role 不限——持久层已兼容 tool 消息 image part**），`with_images`/`context` 仅对含 image part 的消息（当前仅 user）附着。
5. 故 V2 桌面/安卓截图亦**非经工具结果同步可见**（V1 `tools/shots.py` 不计 V2 对等）；模型侧 zoom 需先决图像可见协议。

## 设计决策（需所有者批准）

### 决策 1：图像可见路径（二选一）

**方案 A（少协议面，非同步）**：zoom 工具注册 image 对象（复用 `ImageService.prepare`/`image_register`），返回 `frames_id`/`object_id` + 文本；由所有者/引擎在**后续 user 消息**附着 image part（复用既有 user-message 图像协议，零协议改动）。
- 优点：不触冻结图像协议，复用既有 `references`/`with_images`/`wire`/计量。
- 缺点：模型**下一回合**才看见帧，与参考 zoom「同回合看见」语义有差距；需所有者/引擎主动把 object 转为 user image part（新增附着触发逻辑）。

**方案 B（同步，协议扩展，推荐完整对标）**：
- `ActionResult` 增 `images: tuple[ImageRef, ...] = ()`（`ImageRef`：`object_id/sha256/bytes/width/height/media_type`，复用 `ImageInfo` 校验）。
- `engine.py:404,428` 工具结果追加时，若 `result.images` 非空，把 tool 消息 `content` 构造为 `[{"type":"text","text":result.text}, *omni_image parts]`（part_id 新生成、policy_id 取 run_image_policy、token_cost 按模型）。
- `domain/images.py references()` 放宽：允许 `role=="tool"` 的 `omni_image`（新增 tool-image 校验分支，保持 user-image 既有严格校验不变）；`with_images`/`context` 支持 tool 消息图像选择（per_call_limit/run_budget 一致计量）。
- `adapters/persistence/image_repository.py:97 image_parts`（已核验 role 不限，持久层无需改）/`:168 model_images_prepare` 兼容 tool 消息 image part（需核验 prepare 的 `purpose=="turn"` 与 tool 消息快照兼容）。
- 优点：同回合可见，完整对标参考 zoom，且**通用惠及桌面/安卓视觉工具**。
- 缺点：触及**冻结图像协议 + 计量 + 策略**，需独立合同 + 既有图像协议全量零回归。

**建议方案 B**（完整对标 + 通用视觉基础），但图像协议是冻结合同，**需所有者明确批准协议扩展**后方可接线。

### 决策 2：模型发起媒体处理授权

zoom 是**写动作**（发起处理作业）。当前无模型工具提交作业。`media_zoom` 工具 `writes=True`，**复用既有写工具审批门**（`engine.py:774-792` `prepare_action`→所有者 `approved`→`authorize_action`→资源锁），与 `files_patch` 同链；拒绝/取消/过期沿用 `approval_denied`/`action_revoked`/`approval_expired`。无需新授权机制。

### 决策 3：zoom 作业复用

`media_zoom` 经 `MediaService.submit("frames", {"count":1,"width":1280,"timestamps":[t]})` 复用 K16.zoom_foundation 显式时间戳管线（admission/staging/authorize CAS/删除屏障/未知结算/取消等待/`_register_sheet`/`media_frames`），**无新表/操作**。输入 `source_id` + `cell`（经 `media_cells` 时间映射）或 `timestamp` + 可选 `width`；作业 done 后取 `media_frames.object_id` 经 `ImageService.prepare` 注册图像，返回 `frames_id`/`object_id` + 图像（方案 B 同步可见 / 方案 A 后续 user 附着）。

## 实现步骤（待批准后，独立批次）

1. **协议扩展（方案 B）**：`ActionResult.images` + `ImageRef` 校验 + `domain/images` tool-image 分支 + `engine` tool 消息图像附着 + `image_repository`/`with_images`/`context` tool 消息支持 + 计量/策略一致；**既有 user-image 协议全量零回归**。
2. **media_zoom 工具**：`application/media.py` `ZOOM_SPEC`（`writes=True`、`source_id` 必填、`cell|timestamp`、`width≤1280`、timeout、resource media）+ 审批门复用 + submit frames timestamps + 作业结算后图像注册 + 返回图像；`composition/instance.py` 注册（写工具 agent 档）；`domain/routing.py` 写工具不入 RETRIEVAL_TOOLS。
3. **端到端回归**：真实视频 zoom→审批→作业 done→模型可见 image part（方案 B）/后续 user 附着（方案 A）；审批拒绝/取消/过期、凭据/源 CAS、删除屏障、未知不重放、取消等待真实清理。
4. strict + ruff + 既有图像协议/媒体调用方零回归；真实视频视觉质量另验。

## 有界与安全

单帧 `width≤1280`、`timestamp∈[0,7200]`、`count=1`；image `≤MAX_BYTES=16MiB`、`ImageInfo` 尺寸/像素/SHA 校验；`per_call_limit≤4`/`run_budget≤64` 计量、`openai_low` detail；凭据/源 CAS + 删除屏障 + 未知绝不重放 + 取消等待真实清理；模型发起处理经所有者审批门（写动作）。

## 边界

本批**仅设计合同，无源码/测试/迁移改动**；模型侧 zoom 未实现；K16 HSV 切点（#1）/Laplacian 选帧（#5）/帧内 OCR（需 rapidocr）仍后续。方案 B 协议扩展需所有者批准冻结图像合同变更。`whole_*_complete` 保持 false；不提前关闭任何对标项。