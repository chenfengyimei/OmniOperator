# V2 视频理解分镜工具链逐项审计（K16）

## 范围与判定原则

本批是**只读逐项审计**，不改源码、不新增工具/表/迁移/路由。对照固定提交参考 `xuexi/dsh-video-storyboard`（`e124ae6fcfb31f3572ea24aad093a86897b556a3`，v0.6.0，Apache-2.0，Node ESM，约 11.8k LOC，`src/storyboard/*` 约 40 模块 + `src/tools/*` 5 处理器 + `src/ocr_bridge.py`），核对 V2 视频理解能力是否完整对等参考五工具与算法。

**关键判定纪律（对照 [总对标合同](V2_AGENT_CORE_PARITY.md)「不把 V1 的实现当作 V2 已有功能」）**：仓库存在两套媒体实现，必须分开判定——

- **V2 四层生产媒体链**：`domain/media.py` + `application/media.py`（`MediaService`）+ `adapters/workers/media.py`（PyAV/faster-whisper 子进程）+ `adapters/persistence/media_repository.py`，由 V2 组装根 `composition/instance.py:76,78,570` 装配（`MEDIA_LIST`/`MediaService`/`BilibiliMediaPort`/`MediaProcessingPort`）。**这是 V2 模型实际可见的媒体面**。
- **V1 遗留分镜域**：`tools/storyboard/{detect,segments,render,decode,tools}.py`，经 **V1 组装根** `bootstrap.py:121,657` 的 `register_storyboard_tools` 注册进 **V1 `ToolRegistry`**（`tools/registry.py`，依赖 `core.agent`/`llm.gateway`/`runctx`/`shots`）。它已学习参考算法（HSV 切点、密度分段、固定宫格、可寻址格号、`video_storyboard`/`video_zoom`），但**不在 V2 四层内核里**，V2 `composition` 不导入它。**审计不把它计为 V2 对等**，只作为「可离线缺口的已有学习实现，可择机移植进 V2 四层」记录。

参考仓库内若出现 AGENTS.md/RULES 等指令样文本，一律当作**不可信数据**汇总，不作为本会话指令。未拉取远程、未运行真实账号/模型/设备/网络。

## V2 四层生产媒体面（已对等基础）

`application/media.py` 生产路径：已核验 B 站视频或所有者上传对象 → 作用域媒体源；有界异步作业（`MAX_ACTIVE_JOBS=2`、`MAX_QUEUED_JOBS=8`）在数据库事务外跑在线字幕或受监督 ASR/抽帧 worker；转录与帧_sheet_作为带精确锚点的对象发布；模型只见工具结果实际返回的内容。

- **源/上传**：`register`（B 站 `video` 读 → `source_row`，`bvid`/`aid`/`cid`/`duration_s` 校验，`MAX_DURATION_S=7200`）、`register_upload`（所有者不可变对象，`UPLOAD_TYPES` 音视频 MIME，`upload_source_row`）。
- **作业三操作** `OPERATIONS=("subtitle","transcribe","frames")`：
  - `subtitle`：B 站在线字幕，**单条全文行、无可靠锚点**（`domain/media.py:170` 注释「Official subtitles carry no reliable anchors」，`[[0.0,0.0,text]]`）。
  - `transcribe`：`adapters/workers/media.py:_transcribe` 本地 **faster-whisper**（`model small/device cpu/compute_type int8`，`local_files_only=True` 离线缓存不下载，`vad_filter=True beam_size=5`，VAD 空则回退无 VAD），PyAV 解码 16kHz 单声道（`_decode_audio_16k_mono` 按平面精确字节，真机 34 分钟实测修正），锚点段 `[(start,end,text)]`（`MAX_SEGMENTS=4000`、`MAX_TRANSCRIPT_CHARS=24000`、`MAX_ASR_SECONDS=7200`）。
  - `frames`：`adapters/workers/media.py:_frames` PyAV `seek`+解码 **等距采样**（`_target_times` 跳过 5% 片头片尾，`count` 默认 6、`MAX_FRAMES=12`、宽 320–1280），**固定 2 列宫格**（`_sheet_size`），烧 `MM:SS` 标签（`_format_time` 整秒），`timestamps` 锚点（round 3）+ `sha256` + 尺寸/字节限额（`MAX_SHEET_BYTES=8MB`、`MAX_SHEET_PIXELS=16e6`），发布为已核验 image 对象（`_register_sheet` 经 `ImageService.prepare` 复核尺寸/格式）。
- **模型工具**：`media_list`（`LIST_SPEC`，列源 + 最新转录/帧 id）、`media_transcript`（`TRANSCRIPT_SPEC`，有界字符窗 `start/length≤12000`，ASR 行带 `[MM:SS-MM:SS]` 锚点，返回 `total_chars/start/end`）。**仅这两个模型工具**；帧_sheet_经所有者作业产出后作为 image 对象由模型读图，无独立 `media_frames` 模型工具。
- **授权/删除/失败/取消/恢复**：B 站凭据权威 CAS（`bilibili_read_authorized` + `_authorized` 比对 `authority`/`revision_id`）、源绑定 CAS（`config_id`/`config_revision_id`）、上传源对象一致性、删除屏障（`_read_transcript` 加载后重读防并发删除发布）、`UNKNOWN_CODES` 未知结算不重放、`cancel` 等待真实作业任务结算（`asyncio.shield`）、`recover` 启动把上进程作业落 unknown/interrupted、`close` 排空持有任务。

**结论**：V2 已有「上传/B 站来源 + 在线字幕 + 本地离线 ASR（锚点）+ 有界等距抽帧宫格（时间戳锚点）+ 模型有界转录窗 + 完整授权/删除/未知/取消/恢复合同」。这是真实生产接线，但**抽帧是等距采样、宫格固定 2 列、无切点检测、无格号寻址、无 zoom、无字幕带 OCR、无文本定位**。

## 五工具逐项对照

### (a) 自适应分镜 / 镜头切点密度

- **参考**：`scan.js:525 scanShots` 用 ffmpeg `scale=64:36:flags=area,format=rgb24` rawvideo，逐像素 **HSV 加权绝对差**（`scorePair:96`，环形 hue、saturation 降权），**非** ffmpeg scdet/SSIM；`normalizeScores:161` 亮度增益（`REF_V=160`、`GAIN_MAX=3`）、`barFraction:133` 黑边补偿；`decideCuts:399`→`autoThreshold:330` 速率阈值（floor `MIN_CUT_SCORE=12`）、`cutBudget:293` ≤25% 帧、`detectGradual:831` 0.4s 滞后溶解、`refineCutsByWindow:720` 2s 窗、`spikeCuts:241` 局部中位数（静态 PPT）；密度→档位 `tiers.js levelCurve:93` 卷积（`INTEGRAL_FRAMES=100`、阈值 `[0.0085,0.02,0.03,0.04,0.06,0.08,0.1]`、`SMOOTH_FRAMES=50`、5% 迟滞）→ **8 档 side=1/k**；`integralMarks:263`（`equivSeconds=6`）长镜头补假切点。
- **V2 生产**：`_frames` 等距采样，**无切点检测、无密度档位**。
- **V1 遗留（不计 V2 对等）**：`tools/storyboard/detect.py` 已实现 HSV 三通道差（`CUT_SCORE=35`，七合成场景实测全对）、`segments.py` 密度分段（学习自 `density.js/segments.js`，独立实现）、`decode.py` PyAV 抽稀（`MAX_SCAN_FRAMES=30000`、`MIN_SCAN_FPS=2`、`effective_scan_fps`）。
- **判定**：**可离线缺口**。纯算法（HSV 评分/阈值/密度），V2 worker 已有 PyAV 解码，无需新外部二进制；V1 已有学习实现可移植进 V2 四层（经 V2 授权/锚点/有界作业，不复用 V1 ToolRegistry）。

### (b) 自适应宫格

- **参考**：`pack.js:139 solve` 归一化方形内**精确 DP 行打包**（`UNIT=840`=lcm 1..8），目标 Σarea − bins×`binCost`（`tiers.js BIN_COST:222` full .55/standard 1.02/compact 1.5/skim 2.5），档界 `[1/(k+1),1/(k-1)]`、stretch 0.2；`fitCanvas` maxSide **800**、budget **640000**；`composePacked:155` 零图像库 raw-RGB blit、1px 缝、5×7 位图编号徽章、header、音频条；`encodePngBuffer:484` 经 ffmpeg；sheet 数是输出（`fillGaps:469` 补平方数、`trimTail:427`）。
- **V2 生产**：`_sheet_size` **固定 2 列**、宽随帧、`LABEL_HEIGHT=20` 烧整秒标签；无 DP 打包、无变尺寸档位、无音频条。
- **V1 遗留**：`render.py render_storyboard` 固定宫格（`DEFAULT_CELLS=12`、`MAX_CELLS=16`），烧格号 + 时间码 + **台词带**（`_draw_transcript_band`，按 ASR 段对齐，非音频包络）；`__init__.py:19` **明确不做变尺寸格子（1/1…1/8 档位）**——「一张图固定占 token，把格子缩小只丢信息不省 token，这笔账参考项目自己也算过」。
- **判定**：**可离线缺口（带设计取舍）**。V2 无自适应宫格/DP 打包/档位。注意 V1 与参考在「变尺寸档位」上有意分歧（固定宫格省视觉 token 的论证）；移植时应保留 V2 有界像素/字节限额与「如实报最小格尺寸」纪律，是否引入档位需独立合同决策，不盲目照抄 8 档。

### (c) 局部 zoom / 放大

- **参考**：`tools/zoom.js:20` 复用 `runStoryboard`；`cellId`→绝对时间经 `cells.js lookupCell:67`（`cells.json`，`CELLS_VERSION=2`）；`board.js:118` zoom `uniformTier='auto'`→`ceil(sqrt(n))`、maxFrames 9、maxSheets 1、strategy full、minFrames 4；重读高清签名直链（`direct-url.js`，zoomHeight 1080）。
- **V2 生产**：**无 zoom 工具、无格号→时间寻址索引**。模型只能再提交一次 frames 作业（等距），无法「放大第 5 格那 3 秒」。
- **V1 遗留**：`tools/storyboard/tools.py` `video_zoom`（`ZOOM_WIDTH=1280`，按索引时间码回原流取一帧大图）+ 可寻址格号索引（`video_storyboard` 返回机器可读索引）。
- **判定**：**可离线缺口**。依赖 (a)/(b) 的格号索引；纯算法 + PyAV 重取帧，V1 已有学习实现可移植。需 V2 四层独立合同（格号索引持久化/锚点/授权/有界）。

### (d) 字幕带提取

- **参考**：`tools/subtitles.js:23`→`subtitle-strip.js:173`；带定位 `ocr.js detectSubtitleBand:152`→`ocr_bridge.py detect:108`：**rapidocr** 框过滤（`w/width≥0.18`、`conf≥0.55`、`h/height≤0.15`，偏好下半屏，`pad=(y1-y0)*0.6+8`），回退 `DEFAULT_BAND=[0.82,0.98]`；ffmpeg `crop=iw:ih*(y1-y0):0:ih*y0,scale=1280:105` **串行**抓帧、一次 `vstack` JPEG q4；`planStripTimes:61` 等距（maxStrips 24、stepSec 3）。**纯视觉、无 ASR 对齐**。
- **V2 生产**：`subtitle` 操作读 **B 站在线字幕文本**（无锚点），**无烧录字幕的视觉带定位/裁剪/OCR**。
- **V1 遗留**：`render.py _draw_transcript_band` 把 **ASR 文本**按时间轴画成台词带（非 OCR 烧录字幕）。
- **判定**：**可离线缺口 + 需 OCR 模型/二进制**。带定位/裁剪/vstack 是纯算法（V2 有 PyAV/PIL），但 OCR 引擎 **rapidocr/rapidocr_onnxruntime（ONNX，约 250MB Python 依赖，未捆绑）** 是真实依赖决策；参考用 ffmpeg，V2 用 PyAV 可零新增解码依赖。引入 OCR 需独立合同（模型 provisioning/离线缓存/精确错误而非崩溃，参照 worker `media_component_missing` 纪律）。

### (e) 文本定位

- **参考**：两义——`video_locate`（`tools/locate.js:12`）= **转录文本搜索**（`locate.js matchSegments:44` LCS/query-len + 0.25 精确加分，`minScore 0.34`，无帧/图）；**帧内 OCR 定位** = `ocr_bridge.py` 框 `[[x,y]*4]+text+conf`、`ocr.js readBand/detectAndRead`、`ocr-subs.js` 用 OCR 校正 ASR（`shouldReplace:238`，`conf≥0.65`）。引擎 rapidocr ONNX。
- **V2 生产**：`media_transcript` 只给**有界字符窗读取**（`start/length`），**无转录搜索/定位工具**（模型须手动翻页找台词），**无帧内 OCR 定位**。
- **V1 遗留**：分镜索引挂每格台词前 `MAX_CELL_TEXT=120` 字（格级文本，非全文 LCS 搜索）。
- **判定**：转录 LCS 定位 = **可离线缺口**（纯算法，V2 已有锚点转录，加一个只读 locate 工具即可）；帧内 OCR 定位/ASR 校正 = **需 OCR 模型**（同 (d)）。

## 取流 / ASR / 抽帧 / 锚点对照

- **取流**：参考 `fetch.js resolveInput:530` 本地直通；http→**yt-dlp**（未捆绑，`ytdlp.js discoverYtdlp`）低清副本（scanHeight 360/480/720）、**默认需 cookies.txt**、DASH 音频自合并（`ffmpeg -c copy`）、缓存。**V2**：B 站 **WBI 直链**（已核验 `video`/`streams` 读 + 凭据 CAS）或所有者上传对象，有界下载（`MAX_DOWNLOAD_BYTES=256MB`、`DOWNLOAD_BUDGET_S=180`、`before_dispatch=authorize`）。**判定：V2 对等或更严**（不引 yt-dlp/cookies、不碰用户登录态文件）；真实 B 站送达/凭据质量属需账号验收。
- **ASR**：参考 `asr.js` 三级（内嵌字幕轨→OpenAI/SiliconFlow 云 SenseVoiceSmall→null），16kHz 单声道 WAV、≤45s 按静音切块、verbose_json、音乐幻觉过滤。**V2**：本地 **faster-whisper** 离线（`local_files_only`、VAD、beam 5、锚点段、语言≤16 字符）。**判定：V2 对等离线本地 ASR**（引擎不同：whisper vs SenseVoice；V2 有意离线-only 无云 key）；真实音频 ASR 质量与模型缓存 provisioning 属需实机验收。
- **抽帧**：参考 `frames.js extractFramesBatched:189` 一次 ffmpeg `select='eq(n,k)'`（span≤20s、≤40 帧）、`boxResize` cover、**清晰度 Laplacian 方差 3 候选选帧**、thumb=ffmpeg thumbnail、并发 0.75×cores≤12。**V2**：PyAV `seek`+解码**等距** ≤12 帧、无清晰度选帧。**判定：可离线缺口**（自适应/清晰度选帧；V1 `detect.py SHARPNESS_EDGE=96` Laplacian 已有学习实现）。
- **锚点/返回**：参考 `format.js formatManifest:384` `cell N (image M)=mm:ss.ss-mm:ss.ss k=tier`（**0.01s 精度**、时间码不烧只编号徽章）、24000 字符上限、格边界截断 + `cellRange`/`textOnly` 分页、SEQUENCE `next:from=`、`{text,images[]}`→`ctx.attachments.saveImage` PNG（缺则降级文本）、`evidence.json` 旁车、`VISION_TOKENS=384`、800×800 cap。**V2**：转录 `[MM:SS-MM:SS]`（**整秒精度**）、帧 `timestamps`（round 3=毫秒）、`media_transcript` 窗 ≤12000 字符 + `total_chars/start/end`、sheet 作已核验 image 对象。**判定：V2 锚点 + 有界窗 + image 对象对等基础**；缺口 = 格号可寻址索引 + 亚秒时间码精度 + 档位标签 + 格边界分页（可离线）。

## 可离线缺口清单（列后续批次，K16 不实现）

纯算法/已有 PyAV 解码即可在 V2 四层实现，V1 `tools/storyboard` 已有学习实现可移植（**必须经 V2 domain/application/adapters/composition + V2 授权/锚点/有界作业/删除屏障，不复用 V1 ToolRegistry/bootstrap**）：

1. HSV 切点检测 + 密度分段（替代等距采样），含抽稀帧预算与黑边/亮度归一化。
2. 格号可寻址索引（cell→绝对时间，持久化 + 锚点 + 授权），亚秒时间码精度。
3. `video_zoom` 等价的 V2 模型工具（按格号/时间码回原流取高清单帧，有界）。
4. 转录文本 LCS 定位只读工具（`media_locate` 等价，锚点段搜索，有界返回）。
5. 清晰度（Laplacian）选帧替代纯等距。
6. 自适应宫格/DP 打包**需独立设计合同**（V1 与参考在变尺寸档位上有意分歧；保留 V2 像素/字节限额与最小格尺寸如实上报，不盲目照抄 8 档）。

## 需 OCR 模型 / 外部二进制 / 账号 / 设备 / 网络验收（另验）

- **OCR 模型依赖**：字幕带定位 (d) 与帧内 OCR 定位/ASR 校正 (e) 需 rapidocr/rapidocr_onnxruntime（ONNX，约 250MB Python 依赖，未捆绑）；引入需独立合同（离线缓存 provisioning、`media_component_missing` 式精确错误、不崩溃、不下载）。参考用 ffmpeg，V2 用 PyAV 可零新增解码依赖。
- **需账号/网络**：真实 B 站送达/凭据质量、登录态分辨率（V2 走 WBI 直链不引 cookies）；参考云 ASR（V2 有意离线-only，不对等云 key 路径）。
- **需实机/设备**：真实视频视觉质量、长片抽帧/扫描耗时与内存（V1 注释真机 34 分钟实测）、GPU hwaccel（参考可选 cuda/d3d11va，V2 worker CPU）、真实音频 ASR 质量与 whisper 模型缓存。

## 完成条件

K16 审计完成**不宣称五工具/算法全集对等**：V2 四层生产媒体面已有上传/B 站源 + 在线字幕 + 离线 ASR（锚点）+ 有界等距抽帧宫格 + 模型有界转录窗 + 完整授权/删除/未知/取消/恢复合同；切点检测/自适应宫格/格号寻址/zoom/字幕带 OCR/文本定位/清晰度选帧/亚秒锚点为**可离线缺口或需 OCR 模型**，分别列后续批次与另验。V1 `tools/storyboard` 的学习实现**不计为 V2 对等**。`whole_core_parity_complete`、`whole_reference_feature_audit_complete`、`whole_v2_complete` 保持 false。后续按 [K17…任务](../../tasks/v2-agent-core-parity.json) 推进 bili-humanism 审计，并择机实现 K13/K14/K15/K16 记录的可离线缺口（独立批次，先授权/持久化合同后执行接线）。

## 实现进展（2026-11-22，K16 分支 `k16-storyboard`，基线 `0793f254`）

上文“抽帧/锚点”等段落是 2026-11-20 的**审计快照**；以下后续子批已在独立分支实现并通过专项+调用方，历史“无切点/无清晰度/格号/zoom 未实现”的描述**不再代表当前 v2**：

| 子批 | 内容 | 证据 |
|---|---|---|
| K16.scene_frames | HSV 切点 + 密度分段 + Laplacian 选帧（固定 2 列） | [v2-media-scenes.json](../evidence/v2-media-scenes.json) |
| K16.scene_gradual | 2s 渐隐 4→1 切点（亮度单调运行合并） | [v2-media-scenes-gradual.json](../evidence/v2-media-scenes-gradual.json) |
| K16.scene_bars | 上下黑边裁剪（bars16 0→1） | [v2-media-scenes-bars.json](../evidence/v2-media-scenes-bars.json) |
| K16.scene_bars_side | 左右黑边四轴裁剪（pillar20 0→1） | [v2-media-scenes-bars-side.json](../evidence/v2-media-scenes-bars-side.json) |
| K16.scene_spike | 邻域尖峰守卫（暗噪声假切点 2→0） | [v2-media-scenes-spike.json](../evidence/v2-media-scenes-spike.json) |
| K16.scene_gain | 暗部亮度增益（极暗 cut 0→1） | [v2-media-scenes-gain.json](../evidence/v2-media-scenes-gain.json) |
| K16.scene_quality | mixed/大黑边/暗蒙太奇/cells 映射回归 | [v2-media-scenes-quality.json](../evidence/v2-media-scenes-quality.json) |
| K16.scene_resource | 采样上限 600s→240 与 60s 素材 | [v2-media-scenes-resource.json](../evidence/v2-media-scenes-resource.json) |
| K16.adaptive_grid_design | 自适应宫格/DP 设计合同（未实现） | [v2-adaptive-grid-design.json](../evidence/v2-adaptive-grid-design.json) |

- 缺口 #2 格号寻址：`K16.cell_anchor`（只读派生）已实现；#3 zoom/工具图像：`K16.zoom_foundation` + tool_image 协议/持久化/引擎/zoom/e2e 已实现（所有者批准方案 B）；#4 文本定位：`K16.locate` 已实现；#5 Laplacian：随 scene_frames 实现。
- 仍未实现/决策：参考 `auto/auto-low` 分位阈值、自适应 minGap；**自适应宫格/DP 的 D0/D2/D4** 待所有者与 A；OCR 需 rapidocr（依赖/模型来源未定）；真实长片（>10min）、真实 B 站、真实运动/压缩复杂度另验。
- 当前分支聚合：scenes 32 项 8.95 秒；23 个 K16 生产入口文件 422 项 225.35 秒；strict 428 文件 0 错误；索引 65/849/464/94。子批提交 `d5691940`→`36779499`→`469ec591`→`cfc7753f`→`84053b8c`→`a16cace9`→`f64539ca`→`2806fa29`。
