# V2 媒体格号寻址 media_cells（K16 可离线缺口 #2 读面实现）

## 目的与参考

实现 [K16 视频分镜审计](V2_VIDEO_STORYBOARD_AUDIT.md) 记录的**可离线缺口 #2：格号可寻址索引（cell→绝对时间亚秒锚点）的模型侧读面**。参考 `xuexi/dsh-video-storyboard`（`e124ae6f`）`cells.js lookupCell:67`（cellId→绝对时间）与 `format.js formatManifest:384`（`cell N` 亚秒锚点）。V2 已有**有界等距抽帧宫格**（固定 2 列、`timestamps` 亚秒锚点已持久化于 `media_frames`），但模型此前**无工具读取每格的格号→行列→绝对时间映射**（`MediaService.invoke` 仅暴露 `media_list`/`media_transcript`/`media_locate`，帧表只能经所有者侧 `frames()` 读）。本批补一个**只读 `media_cells` 工具**，让模型按格号直接命中亚秒时间锚点。

**不含** zoom 高清重取帧（缺口 #3）、HSV 切点检测（#1）、Laplacian 清晰度选帧（#5）、帧内 OCR（需 rapidocr 模型，另验）。**不新增格号持久化表**——V2 已在 `media_frames.timestamps` 持久化亚秒锚点，本工具只读派生格号索引。

## 实现（V2 四层）

- **纯领域 `domain/media.py`**（零 I/O，既有模块加函数，**无新源文件**）：`CELL_COLUMNS=2`；`normalize_cell`（None→全格、0-based int 且 `0<=cell<frame_count`、拒非 int/越界/bool→`media_cell_invalid`）；`cell_anchors`（`timestamps`→`[{cell,row=index//2,column=index%2,time_s}]`，保留亚秒）；`render_cells`（有界文本 `frames_id/source/cells/columns` 头 + 每格 `[cell N] row= col= t=0.000s`，`selected` 仅渲染该格）。
- **`application/media.py`**：`CELLS_SPEC`（`media_cells`，**只读 writes=False**，`source_id` 必填、`frames_id` 可选、`cell` 可选 ≥0，timeout 10，resource media）；`cells/_cells` 用例**复用既有 `media_frames_get`**（agent 作用域，缺失/删除→`media_frames_not_found` 404）+ `frames_id` 缺省经 `media_source_get.latest_frames_id` 回退（无帧→404）+ 源配对（`row.source_id != source_id`→404）+ `_run_bounded`（closing 准入守卫、持有任务到结算）；**只读 DB 元数据单读，无对象 body 取**（故无 locate 式「加载后删除」竞态，源删除已级联删帧表）；`invoke` 新增 `media_cells` 分支返回文本 + 结构化 cells。
- **`composition/instance.py`**：`Capability(MEDIA_CELLS, media)` 注册进生产 agent 能力（紧随 `MEDIA_LOCATE`）。**agent 档**只读媒体工具，与既有 media 工具一致**不纳入 `RETRIEVAL_TOOLS`**（`domain/routing.py` 无改动）。

## 保持的合同

只读无写事务；Agent/源隔离与跨 Agent 404；closing 准入在任何查找前拒绝；有界（`cell<frame_count<=12`、格数 ≤12）；锚点只来自源帧表**已核验 `timestamps`**（亚秒），**绝不臆造时间码**；`_run_bounded` 持有任务、取消/关闭等待结算（与既有 media 读一致）。**无新表/迁移/HTTP 路由/worker 协议/routing/持久层改动**（复用既有 `media_frames_get`/`media_source_get`，不触他会话 `repository.py`/`schema.py`/`v0041.py`）。

## 验证

- 新增 `agent/tests/v2/integrations/test_v2_media_cells.py` **12 项**（3 领域 + 4 真实实例 FakeProcessor frames 锚点/latest 回退/单格选择/生产注册只读 + 5 stub 守卫：源配对 404/跨 Agent 404/closing 拒绝/latest 无帧 404/越界格），首轮全过（8.06 秒），format 后复跑 12 过（8.46 秒）。
- 媒体调用方专项 **113 过 / 1 既有失败**（`test_v2_media.py::test_v36_upgrade_registers_sources_and_bounds_jobs` 断言 `schema_version=="v2_0039" and len(tables)==141`，工作树迁移 head 已 `v2_0044`，他会话 `schema.py`/`v0041.py`/`repository.py` 未提交所致；本批**零迁移/零表/零持久层改动**，与之无关）。media/locate/frames/anchors/authority 调用方用例全过，证明加性改动无回归。
- V2 strict `pyrightconfig.strict.json` **410 文件零错误/警告**（无新源文件，`domain/media.py` 既有计入，410 不变）；ruff check/format 通过（仅折叠本批新增多行签名）。
- `project_index` 运行（`declared_tools` 索引不含 application 层 media ToolSpec，与 `media_list`/`transcript`/`locate` 同）；**聚合 `docs/index` 暂缓提交**（他会话未提交持久层编辑会污染全局 `source_sha256`），再生推迟到工作树洁净。

## limits 与后续

格号索引为**只读派生自既有 `media_frames.timestamps`**（亚秒已持久化），非参考 `cells.json` 独立持久化层；固定 2 列宫格（V1 有意拒绝变尺寸档位省视觉 token）；无 zoom 高清重取（#3）、无 HSV 切点（#1）、无 Laplacian 选帧（#5）、无帧内 OCR。`media_cells` 随 media 工具在 agent 档常备（非 opt-in）。本批仅实现 K16 缺口 #2 读面，其余 K13–K18 可离线缺口仍列后续独立批次。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。