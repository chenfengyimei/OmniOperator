# V2 媒体场景切点抽帧与清晰度选帧（K16.scene_frames）

状态：已实现并专项通过（2026-11-20）。学习参考 dsh-video-storyboard `src/storyboard/scan.js`（HSV 加权切点，PySceneDetect ContentDetector 思路）与 `frames.js extractSharpest`（Laplacian 清晰度选帧），独立实现于 V2 四层媒体链，不复用 V1 tools/storyboard。合并实现 K16 缺口 #1（HSV 切点+密度分段）与 #5（Laplacian 清晰度选帧）的**保守子集**。

## 范围

frames 作业新增可选 `select="scenes"`（默认 `equidistant` 字节等价零回归；与显式 `timestamps` 互斥，组合即 `media_frames_invalid` 422）：

1. **有界 seek 采样**：`min(SCENE_MAX_SAMPLES=240, max(2, duration*SCENE_SAMPLE_FPS=2))` 个等距目标经既有 `_frame_at` seek 取帧（不整片顺序解码，长片成本有界）；每帧缩到 `SCENE_ANALYSIS_WIDTH=64` 宽分析缩略图，转 HSV 与灰度。
2. **HSV 加权切点**（`_hsv_score`，继承参考两条关键经验）：色相**环形差** `min(dh,255-dh)`（红→品红跨 0° 不算巨差）；色相项按两帧**较低饱和度**降权（近灰像素色相无意义，防编码噪声假切点）；S/V _plain 绝对差；三项均值 0-255。PIL C 级运算（ImageChops.difference/darker/multiply + point + ImageStat），无 numpy 依赖（frames 路径不要求 asr/vectors extra）。
3. **固定阈值 + minGap 抑制**（`scene_cuts` 纯领域）：`SCENE_CUT_THRESHOLD=12`（参考 MIN_CUT_SCORE 原始刻度），相邻切点 <`SCENE_MIN_GAP_S=0.35` 保留更高分者。**不含**参考自适应分位数阈值/亮度归一增益/黑边补偿/自适应 gap（列后续）。
4. **密度分配**（`scene_segments`/`scene_slots` 纯领域）：切点分段；count 槽位按段时长比例分配（最大余数法，每保留段 ≥1 槽）；段多于槽保留最长段（平局稳定取先序）；每段均分为其槽位窗口。
5. **Laplacian 清晰度选帧**：每窗口内候选采样帧取 3×3 Laplacian 核（offset 128 防负值裁剪）响应**方差最大**者（平局取最早），其时间戳经既有 `_frame_at` 全分辨率重取进宫格；窗口无候选取中点。
6. **无切点回退**：单场景/采样不足 → 原等距目标（零意外）；`duration<=0` 降级首帧分支不变。
7. 响应仅 scenes 模式增 `select`/`scene_cuts`（round 3）键；默认响应形状不变。时间戳仍只来自真实解码帧时间（`_frame_time`），绝不臆造。

## 实现位置

- `domain/media.py`：`SCENE_*` 常量、`frames_plan` 第四参 select、`scene_cuts`/`scene_segments`/`scene_slots` 纯规则（无新源文件）。
- `adapters/workers/media.py`：`_circular_hue`/`_hsv_score`/`_scene_targets`（资源经 ExitStack 登记关闭）、`_frames` select 校验/分支/响应键；既有 pick/宫格/编码/`_sheet_size` 防线全复用。
- `application/media.py`：处理器 Protocol/submit frames_plan/perform 透传 select；`adapters/media.py` MediaProcessor.frames 第六参；`adapters/http.py` `MediaJobInput.select` Literal + handler（scenes+timestamps 由 frames_plan 422 拒）。
- 调用方适配：`omni_test_support/v2/media.py` FakeProcessor 与 `test_v2_media_sheet_identity.py` Processor 子类增 select 默认参（生产签名变更合法适配，zoom_foundation 先例；`*args` 覆盖无需改）。

**无新表/迁移/操作/模型工具/路由/worker 协议帧改动**（select 为作业参数加性可选字段，同 timestamps 先例）；admission/staging/授权 CAS/删除屏障/未知结算/取消等待/`_register_sheet` 图像核验全复用。

## 验证

- 新增 14 项回归（plan select 默认/键/非法×5/与 timestamps 互斥、scene_cuts 阈值/minGap/空、segments、slots 比例/最长/平局/空、**真实合成视频**：三场景切点≈4/8 且每段有帧、棋盘 vs 纯灰 Laplacian 选锐帧、无切点等距回退、默认响应形状不变、fake 夹具非法 select/scenes+timestamps 解码前拒、真实 MediaProcessor 子进程透传、真实作业 submit→settle done→cells 可寻址+非法 plan 422、REST select 字段）。
- 调用方：scenes 14 + frames/zoom/plans/sheet_identity/cells/worker/decoding/duration/anchors 合 **221 过 0 失败**（44.56 秒；lambda→具名函数类型修正后终源复跑确认）。
- strict（pyrightconfig.strict.json，仓库根）：本批文件零错误（首轮 point lambda 未知类型 3 错误改具名 `_circular_hue`）；余 5 错误均既有他会话已提交 repository.py（6bcb0e98），不追逐。ruff check/format 通过。

## 明确不做

参考自适应分位数阈值（auto/auto-low）/REF_V 亮度增益/黑边补偿/自适应 minGap/快剪事件合并；自适应宫格/DP 打包（需独立设计合同）；模型侧自动 zoom（K16.zoom_model_design 待所有者批准协议选型）；帧内 OCR（需 rapidocr）；真实长片切点质量/耗时内存另验。