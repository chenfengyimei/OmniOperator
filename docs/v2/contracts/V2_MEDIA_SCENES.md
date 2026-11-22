# V2 媒体场景切点抽帧与清晰度选帧（K16.scene_frames）

状态：已实现并专项通过（2026-11-20）；2026-11-22 增补场景渐变合并（K16.scene_gradual）、上下黑边补偿（K16.scene_bars）、左右黑边补偿（K16.scene_bars_side）、邻域尖峰守卫（K16.scene_spike）、暗部增益（K16.scene_gain）、组合质量回归（K16.scene_quality）与采样上限/60s 素材（K16.scene_resource），固定素材复核见证据。学习参考 dsh-video-storyboard `src/storyboard/scan.js`（HSV 加权切点，PySceneDetect ContentDetector 思路）与 `frames.js extractSharpest`（Laplacian 清晰度选帧），独立实现于 V2 四层媒体链，不复用 V1 tools/storyboard。合并实现 K16 缺口 #1（HSV 切点+密度分段）与 #5（Laplacian 清晰度选帧）的**保守子集**。

## 子批与证据（2026-11-22）

| 子批 | 关键变化 | 证据 |
|---|---|---|
| K16.scene_frames | HSV 切点、密度分段、Laplacian 选帧、固定 2 列 | [v2-media-scenes.json](../evidence/v2-media-scenes.json) |
| K16.scene_gradual | 亮度单调运行合并为单一中点（2s 渐隐 4→1） | [v2-media-scenes-gradual.json](../evidence/v2-media-scenes-gradual.json) |
| K16.scene_bars | 上下黑边裁剪（bars16 0→1） | [v2-media-scenes-bars.json](../evidence/v2-media-scenes-bars.json) |
| K16.scene_bars_side | 四轴黑边裁剪（pillar20 0→1） | [v2-media-scenes-bars-side.json](../evidence/v2-media-scenes-bars-side.json) |
| K16.scene_spike | 邻域尖峰守卫（暗噪声假切点 2→0） | [v2-media-scenes-spike.json](../evidence/v2-media-scenes-spike.json) |
| K16.scene_gain | 暗部增益（极暗 cut 0→1） | [v2-media-scenes-gain.json](../evidence/v2-media-scenes-gain.json) |
| K16.scene_quality | mixed/大黑边/暗蒙太奇/cells 映射回归 | [v2-media-scenes-quality.json](../evidence/v2-media-scenes-quality.json) |
| K16.scene_resource | 600s→240 次采样与 60s 素材 | [v2-media-scenes-resource.json](../evidence/v2-media-scenes-resource.json) |
| K16.adaptive_grid_design | 宫格/DP 设计合同（D0/D2/D4 待决，未实现） | [v2-adaptive-grid-design.json](../evidence/v2-adaptive-grid-design.json) |

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

参考自适应分位数阈值（auto/auto-low）/REF_V 亮度增益/自适应 minGap（渐变合并与黑边补偿已实现，见下）；快剪事件合并；自适应宫格/DP 打包（需独立设计合同）；模型侧自动 zoom（K16.zoom_model_design 待所有者批准协议选型）；帧内 OCR（需 rapidocr）；真实长片切点质量/耗时内存另验。

## K16.scene_gradual（自适应渐变合并，2026-11-22）

**固定阈值的具体失败（固定合成素材，8s/25fps/64×48/2fps 采样）**：2s 0→255 渐隐的逐样本 HSV 分数为 18.3/28.0/23.0/22.0/11.0，固定阈值 12 + minGap 0.35 输出 4 个切点（3.24/3.76/4.24/4.76），把一个渐变拆成 4 段；基线三色场景（2 切点）、±12 逐帧噪声（最高 8，0 切点）、0.2s 快剪（1 切点）证明旧规则在这三类上无误判。

**最小可验批次**：

- `domain/media.py` 抽出 `scene_sample_count(duration)`（`min(240,max(2,int(duration*2)))`），worker 复用；600s/7200s→240、0.4s→2 可单测。
- `scene_cuts(times, scores, brightness=None)`：提供逐样本亮度时，把**连续超阈值样本**中亮度**单调变化**、跨度 ≥`SCENE_GRADUAL_WINDOW_S=0.4` 的运行合并为单一中点切点（分数取运行最大）；亮度首末差 ≤`SCENE_GRADUAL_TOLERANCE=1` 或非单调不合并；`brightness=None` 时输出与旧实现逐字节一致（既有 14 项零回归）。
- worker `_scene_targets` 记录每样本 HSV V 均值并传入；seek/Laplacian 选帧/宫格/像素字节限额/取消删除语义全部不变。

**验证**：渐变素材切点 4→1（≈4.0s），锚点仍来自真实解码帧且跨越切点两侧；三色基线仍 2 切点；噪声、无切点等距回退、0.2s 快剪（≤1 切点）有界；取消→删除后无帧行；工具图像预算与 frames/zoom/cells 调用方回归。固定素材指纹见证据（两次生成 SHA-256 一致）。

**边界**：2fps 采样（<0.5s 快剪不可分辨，合并为一个切点）；亮度单调的快速蒙太奇可能被误合并（保守性低于参考逐帧 0.4s lag 渐变检测）；不含参考 auto/auto-low 分位数阈值、亮度增益、黑边补偿、DP 宫格（仍需独立合同）；真实长片切点质量/耗时内存与帧内 OCR 未验。


## K16.scene_bars（黑边补偿，2026-11-22）

**固定素材失败**：同一 subtle cut（(100,60,50)→(60,100,90)）在全幅 64×48 上分数 22.3、检出 4.24s；加 16px 上下黑边（内容仅 16px 高）后分数降到 7.8，固定阈值漏检；12px 黑边还会因编码边缘噪声在 1.0s 产生假切点。

**最小可验批次**：

- `domain/media.py` 新增 `SCENE_BAR_V_MAX=24.0`/`SCENE_BAR_LIMIT=0.4` 与纯规则 `bar_rows(rows, threshold, limit)`：从一侧连续 `V<=24` 的行计为黑边，单侧最多 40% 且至少保留 1 行；无 I/O、可单测。
- worker `_bar_crop`：对每个采样 HSV 缩略图逐行求 V 均值，取**所有样本的最小** top/bottom（保守共享，只裁所有样本都黑的边），裁后再算 HSV 分数与亮度；Laplacian 清晰度与宫格仍用全帧，时间锚点不变。
- 固定素材结果：bars16 由 0→1 切点（4.24s）、bars12 由 `[1.0,4.5]`→`[4.24]`、bars0 保持 `[4.24]`、bars20（内容 8px）检出 4.5s；全黑源 0 切点、3 帧宫格正常。
- 调用方：scenes 22 项；frames/scene 调用方 281 项（含取消/删除、工具图像预算、zoom、结构守卫）。

**边界**：只识别与画面边缘对齐的均匀近黑边；不规则/图形黑边、像素级渐隐边不处理；单侧 40% 上限避免暗场景被裁空；暗场景仍可能被裁掉一部分分析行；真实长片黑边/质量/耗时内存与 OCR 未验。


## K16.scene_spike（邻域尖峰守卫，2026-11-22）

**固定素材失败**：暗场景 ±8 逐帧噪声在固定阈值下产生 2 个假切点（1.5s/3.5s，分数 13/16）；两样本亮度单调（33→21）还会被旧渐变规则当作渐变合并，掩盖噪声。

**最小可验批次**：

- `domain/media.py` 新增 `SCENE_SPIKE_RADIUS=1`/`SCENE_SPIKE_FACTOR=2.0`/`SCENE_SPIKE_MARGIN=3.0`；`scene_cuts` 在提供亮度时只保留**相对邻域**（±1 个样本、排除自身）满足 `score >= 2*max(neighbors)+3` 的候选；渐变合并结果标记 trusted，跳过尖峰检查。
- 渐变证据收紧为**至少 3 个连续超阈值样本**（`SCENE_GRADUAL_MIN_SAMPLES=3`），2 样本单调噪声不再合并；2s 渐隐仍为 4 样本，行为不变。
- 结果：暗噪声假切点 2→0；1s 蒙太奇保留 ≥5 个切点；三色基线、2s 渐隐、黑边 bars0/12/16/20、0.2s 快剪、全黑源等既有 25 项不变；`brightness=None` 仍与旧实现逐字节等价。

**边界**：连续高变化（<0.5s 快剪或强运动）可能被整体抑制为无切点（2fps 采样限制）；极暗 cut（分数 10.3）仍低于固定阈值，需要亮度增益另批；不是参考 auto/auto-low 分位阈值；真实长片未验。


## K16.scene_gain（暗部亮度增益，2026-11-22）

**固定素材失败**：极暗 hard cut `(8,8,16)→(18,18,36)` 的 HSV 分数 10.3，低于固定阈值 12，全幅与无黑边素材均漏检；同一素材在暗噪声 ±8 下已被尖峰守卫压掉，说明增益必须与尖峰守卫联合使用。

**最小可验批次**：

- `domain/media.py` 新增 `SCENE_GAIN_REF_V=160.0`/`SCENE_GAIN_MAX=3.0`；`scene_cuts` 在提供亮度时，对每个相邻样本对按 `mean(V)` 计算 `gain=clamp(160/mean(V),1,3)`，以 `score*gain` 作为阈值/渐变/尖峰判断的有效分数；输出时间点不变。
- 顺序保持：增益 → 阈值 → 渐变合并（trusted）→ 尖峰守卫 → minGap；`brightness=None` 仍与旧实现逐字节等价。
- 结果：极暗 cut 由 0→1（4.24s）；暗噪声 ±8 仍 0 假切点；三色基线、2s 渐隐、bars0/12/16/20、0.2s 快剪、1s 蒙太奇、全黑源既有 27 项不变。

**边界**：增益上限 3（更暗场景不再提升）；真实暗部噪声/压缩伪影可能被放大后仍靠尖峰守卫压制，但合成素材覆盖有限；不是参考 auto/auto-low 分位阈值；真实长片、真实 B 站来源与 OCR 未验。


## K16.scene_bars_side（左右黑边补偿，2026-11-22）

**固定素材失败**：同一 subtle cut 在 64×48 全幅分数 22.3、检出 4.24s；左右各 20px 黑边（内容仅 24/64 宽）分数被稀释到阈值以下，**0 切点**；左右各 8px 黑边还会因编码边缘噪声把切点偏移到 4.74s。

**最小可验批次**：

- `_bar_crop` 从只处理上下扩展到**四个轴**：逐行与逐列求 HSV V 均值，`bar_rows` 分别从 top/bottom/left/right 计数，取所有采样样本的最小值；每轴最多 40% 并至少保留 1 行/列；某轴裁剪会导致退化时该轴回退为 0，另一轴仍可独立裁剪；仍只裁 HSV 分析缩略图。
- 结果：pillar0 保持 `[4.24]`；pillar8 `4.74`→`4.24`；pillar20 `[]`→`[4.74]`（1 切点，滞后 1 个采样，约 0.5s 内）；竖屏/窄内容左右大黑边可被识别。
- 既有上下黑边、全黑、暗噪声、极暗 cut、渐变、快剪用例全部保持。

**边界**：只识别与画面边缘对齐的均匀近黑边；不规则/渐变边、非黑色遮挡不处理；单轴 40% 上限避免内容被裁空；真实长片/真实竖屏视频质量未验。


## K16.scene_quality（组合质量回归，2026-11-22）

固定素材组合补齐（无源码改动）：

- **mixed 黑边**：64×48，上下各 6px + 左右各 8px，同一 subtle cut → 1 切点（4.24s）。
- **大 letterbox**：64×96，上下各 32px（内容 32px），subtle cut → 1 切点（4.24s）。
- **暗色蒙太奇**：6 个 1s 暗色镜头（V≈30–90），在暗部增益 + 尖峰守卫下保留 ≥4 切点，锚点升序。
- **生产入口映射**：真实 scenes 作业 settle 后 `media_cells` 的 cell/row/column 为 row-major（0,0)(0,1)(1,0)，`time_s` 升序且落在 0..duration；与既有 3 网格一致。
- 既有空片段/全黑源/0.2s 快剪/运行中删除→取消→无帧/工具图像预算回归保持；本批只扩测试与证据，生产字节未变。

**边界**：组合黑边只覆盖均匀近黑边；暗色蒙太奇为合成色块，无真实运动/压缩复杂度；真实长片与 OCR 未验。


## K16.scene_resource（采样上限与 60s 素材，2026-11-22）

仅测试/证据，无生产改动：

- **采样调用上限**：把 `worker._frame_at` 替换为计数桩后直接调用真实 `_scene_targets`——600s 只发起 **240** 次 seek 采样（`SCENE_MAX_SAMPLES`），0.4s 发起 **2** 次；`_frame_at` 不会因长片线性增长，帧选择仍用既有 count 上限。
- **60s/12 场景固定合成素材**：2fps 采样共 120 样本，检出 **11 个切点（允许 10–11）**，count=12 产出 12 个唯一升序锚点（0..60s）与宫格文件；真实解码耗时低，作为「真实长片」的离线替代，不宣称真实长片验收。
- 固定素材指纹 `long.mp4=b3021098…0c686`（8744B，两次生成一致）。
- 既有 32 项 scenes 与 K16 生产入口（locate/cells/zoom/frames/worker/cancel/工具图像预算）聚合套件见证据。

**边界**：60s 为合成色块素材，不含真实运动/压缩复杂度、无真实长片内存/耗时曲线；OCR 与真实 B 站来源未验。
