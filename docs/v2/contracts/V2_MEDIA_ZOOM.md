# V2 媒体显式时间戳抽帧 media frames timestamps（K16 可离线缺口 #3 zoom 高清重取基础）

## 目的与参考

实现 [K16 视频分镜审计](V2_VIDEO_STORYBOARD_AUDIT.md) 记录的**可离线缺口 #3（局部 zoom 高清重取）的所有者侧作业基础**。参考 `xuexi/dsh-video-storyboard`（`e124ae6f`）`zoom.js:20`（复用 `runStoryboard`，格号→绝对时间经 `cells.js lookupCell`，重读高清签名直链 `direct-url.js zoomHeight1080`）。V2 已有**有界等距抽帧宫格作业**（`_target_times` 等距采样 + `_frame_at` 任意时间 seek + 固定 2 列宫格 + `media_frames` 亚秒 `timestamps` 持久化）与 **`media_cells` 格号→时间读面**（K16.cell_anchor）。本批给 frames 作业补**可选显式时间戳模式**：所有者可在指定时刻（如某格号对应时间）以高分辨率（width 至 1280）重取单帧或多帧，**完整复用既有 frames 作业管线**（准入/staging/授权/`_register_sheet`/`media_job_complete_frames`→`media_frames` 记录），**无新表/新操作/新模型工具**。

**本批不含模型侧自动 zoom 工具**：参考 zoom 是模型发起的高清单帧重取并让模型看见图像。V2 媒体模型工具（`media_list`/`media_transcript`/`media_locate`/`media_cells`）全为**只读文本**，frames 由**所有者侧异步作业**产出 image 对象，`ActionResult` 无图像字段。模型发起 zoom 需先决两项设计（见下「后续」），故本批先落地所有者侧高清重取基础，模型侧 zoom 仍列缺口。亦不含 HSV 切点（#1）、Laplacian 选帧（#5）、帧内 OCR（需 rapidocr 模型）。

## 实现（V2 四层）

- **纯领域 `domain/media.py`**（零 I/O，既有模块加参数，**无新源文件**）：`frames_plan(count, width, timestamps=None)` 增可选第三参；`timestamps` 非 None 时须为 list/tuple 且 `len==count`（每锚点一格），逐元素须 `type in (int,float)`（拒 bool/str/None）、有限、`0<=t<=MAX_DURATION_S`，归一为 float 列表写入 `plan["timestamps"]`；越界/非有限/数量不符/非列表→`media_frames_invalid` 422。**缺省 None 时 plan 形状不变**（`{count,width}`，无 timestamps 键），既有等距行为零回归。
- **worker `adapters/workers/media.py`**：`_frames` 读 `request.get("timestamps")`，独立边界校验（list、`len==count`、逐元素 int/float、有限、`0<=t<=MAX_VIDEO_SECONDS`，OverflowError 守卫）→`targets`；采样分支 `targets is not None` 时逐锚点 `_frame_at(container, stream, target)` seek 重取（无论 duration 元数据有无），**否则保持原 `duration>0` 等距 / `duration==0` 首帧降级两分支不变**。pick/宫格/编码/资源关闭链完全复用。
- **`adapters/media.py`**：`MediaProcessor.frames(video, sheet_output, count, width, timestamps=None)` 增可选参，经 `frames_plan` 校验后透传 worker 请求。
- **`application/media.py`**：处理器 Protocol `frames` 签名同步增 `timestamps`；`submit` frames 分支 `frames_plan(count, width, params.get("timestamps"))`；作业 perform 透传 `plan["timestamps"]`（无则 None）。**复用既有 frames 作业管线**（admission≤2/staging/authorize CAS/删除屏障/未知结算/取消等待/`_register_sheet` 图像核验/`media_frames` 持久化），无新结算路径。
- **`adapters/http.py`**：`MediaJobInput` 增 `timestamps: list[float] | None`（max_length=12）；frames 提交 handler 当 timestamps 非 None 时 `count=len(timestamps)` 派生并透传（`cast(Record, …)` 满足 JSON 不变性）。**仅改 MediaJobInput 字段与 frames handler**，不触既有 background_review 格式偏差。

## 保持的合同

所有者侧异步作业（非模型只读工具）；Agent/源隔离与凭据/源 CAS、删除屏障、未知绝不重放、取消等待真实清理、启动恢复全部复用既有 frames 管线；有界（`count<=12`、`width∈[320,1280]`、`timestamps` 每元素有限 `∈[0,7200]`、`len==count`、admission≤2 活跃作业）；锚点为所有者显式给定时刻，worker 经 seek 取**真实解码帧时间**（`_frame_time` 校验），宫格 `timestamps` 仍来自核验帧、绝不臆造；图像对象经 `_register_sheet` 核验后发布。**无新表/迁移/操作/模型工具/routing/持久层改动**（复用 `media_frames`，不触他会话 `repository.py`/`schema.py`/`v0041.py`）。worker 协议仅**加性可选字段**（默认等距请求形状不变）。

## 验证

- 新增 `agent/tests/v2/integrations/test_v2_media_zoom.py` **33 项**：领域 `frames_plan`（缺省形状无 timestamps 键/显式每锚点一格/int 归一 float/范围边界 0 与 7200 接受/12 非法参数化拒 `media_frames_invalid`）；worker（显式 targets 转发至 `_frame_at` 记录==[5.0,9.0]/缺省仍等距 [2.0,5.0,8.0]/11 非法显式锚点解码前拒 `Invalid frame plan` 且容器未开）；**真实视频** `MediaProcessor.frames(…, [0.5,2.0])` 实际 seek（frame_count2、timestamps 接近请求、PNG 640×260）；**真实作业** submit frames timestamps→settle done→`latest_frames_id`→`media_cells` frame_count2/cells2/columns2；REST `MediaJobInput` 可选 timestamps 接受/缺省 None/长度 13 拒。
- 既有 `test_v2_media_sheet_identity.py` 的 `Processor(FakeProcessor)` 子类 `frames` 覆盖与 `omni_test_support/v2/media.py` `FakeProcessor.frames` 同步增可选 `timestamps` 参（生产签名变更的合法调用方适配；`*args` 覆盖的 authority/cancel 无需改）。
- 媒体调用方全组 `test_v2_media*.py` **719 过 / 8 失败**：其中 3 个 sheet_identity 失败系本批签名变更所致、已适配修复（复跑 44 过含 zoom33+sheet_identity11）；其余 **5 个既有失败他会话所致与本批无关**（staging×2 字幕结算分类 `unknown/media_processing_failed` vs 期望 `failed/media_subtitle_missing`、v36_upgrade×1 `schema_version=="v2_0039" and len(tables)==141` 工作树 head v2_0044、upload_migration×2 alembic DDL 回滚 pin v2_0038；他会话 `schema.py`/`v0041.py`/`repository.py` 未提交编辑，本批零迁移/零表/零持久层/零字幕改动）。zoom+sheet_identity+frames+plans 复跑 **100 过**。
- V2 strict `pyrightconfig.strict.json` **410 文件零错误/警告**（无新源文件；本批 12 处类型问题已修：domain/worker 对 object 时间戳列表 cast、处理器 Protocol frames 签名同步、http `cast(Record,…)` JSON 不变性）；ruff check 全过、format 仅折叠本批新增多行签名，http 仅余既有 background_review 偏差（未触）。
- `project_index` 运行（无新源文件，`declared_tools` 不含 application 层 media ToolSpec）；**聚合 `docs/index` 暂缓提交**（他会话未提交持久层编辑污染全局 `source_sha256`），再生推迟到工作树洁净。

## limits 与后续

本批为**所有者侧显式时间戳高清重取作业基础**（frames 作业可选 timestamps，复用既有管线与 `media_frames` 持久化），**非模型侧自动 zoom 工具**。模型侧 zoom（缺口 #3 完整形态）仍需先决设计合同：(1) **模型发起媒体处理的授权**（当前无模型工具提交作业，需经 `action_review` 所有者审批门或档位限制，属写动作）；(2) **图像对模型可见路径**（`ActionResult` 无图像字段，建议经既有 `ImageService._wire`/run message image references 而非协议扩展）。HSV 切点（#1）、Laplacian 选帧（#5）、帧内 OCR（需 rapidocr 模型）仍后续。固定 2 列宫格（V1 有意拒绝变尺寸档位省视觉 token）。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false；不提前关闭任何对标项。