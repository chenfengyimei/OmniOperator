# K16 自适应宫格/打包设计合同

2026-11-22 · design_contract_recorded · 纯文档，无源码/测试/迁移改动。

本文件只决策「已完成抽帧后如何排版宫格」，不改变切点、选帧、时间锚点、工具图像预算或模型协议。审计记录见 [V2_VIDEO_STORYBOARD_AUDIT](V2_VIDEO_STORYBOARD_AUDIT.md) §(b)，现有实现见 [V2_MEDIA_SCENES](V2_MEDIA_SCENES.md)、[V2_MEDIA_CELLS](V2_MEDIA_CELLS.md)。

## 0. 现状与参考

- V2 生产：`adapters/workers/media.py::_sheet_size` **固定 2 列**（count=1 时 1 列），每格同宽同高，格高含 `LABEL_HEIGHT=20` 时间标签；上限 `MAX_SHEET_SIDE=8192`、`MAX_SHEET_PIXELS=16_000_000`、`MAX_SHEET_BYTES=8 MiB`、`MAX_FRAMES=12`、宽 320–1280。格号始终 row-major，`media_cells` 按 2 列映射（`CELL_COLUMNS=2`）。
- 参考：`pack.js solve` 在归一化方形内做**精确 DP 行打包**（`UNIT=840=lcm(1..8)`），目标 `Σarea − bins×BIN_COST`；`tiers.js` 8 档 side=1/k、`BIN_COST` full .55/standard 1.02/compact 1.5/skim 2.5；`fitCanvas` maxSide 800、budget 640000；变尺寸格子、1px 缝、编号徽章。
- V1 遗留：固定宫格（`DEFAULT_CELLS=12`/`MAX_CELLS=16`），明确**不做变尺寸档位**——「一张图固定占 token，把格子缩小只丢信息不省 token，这笔账参考项目自己也算过」。V1/V2 与参考在档位上有意分歧。

## 1. 目标与非目标

**目标**：在**不改变每格像素、总字节/像素上限、格号语义、时间锚点**的前提下，让列数随数量/尺寸自适应，减少尾部空白行与超宽图；为将来是否需要 DP/档位提供可核对的决策。

**非目标**：不引入变尺寸格子/8 档 side；不改 `media_cells` 持久化列数（除非 D4 另行批准）；不改 `timestamps`/scenes/zoom/locate 行为；不改模型协议、工具图像预算、迁移。

## 2. 决策门（D0–D6，未决前不接线）

| 决策 | 问题 | 选项 | 建议 |
|---|---|---|---|
| D0 | 本版是否做自适应宫格 | 做／不做 | 做**固定格尺寸的最小自适应列数**；DP/档位不做 |
| D1 | 格子尺寸 | (a) 固定格尺寸（保留 token 论证）／(b) 变尺寸档位 | (a)；变尺寸需另立评估合同 |
| D2 | 列数规则 | (a) 固定 2 列不变／(b) `ceil(sqrt(count))` 上限约束／(c) DP 行打包 | (b) 最小实现：`columns = clamp(ceil(sqrt(n)), 1, 受 SIDE/PIXELS 约束的最大列数)`，`rows = ceil(n/columns)`，放不下回退 2 列 |
| D3 | 空格与顺序 | (a) 尾部留空格／(b) 拉伸／(c) 重排 | (a) row-major、尾部空格；不重排 `timestamps`；格号仍 `index//columns, index%columns` |
| D4 | 接口暴露 | (a) 直接改默认／(b) 新增作业参数 `grid`／(c) 仅 scenes 启用 | 待所有者与 A 选择；(a) 影响所有 frames 响应尺寸与既有测试；(b) 触共享 HTTP 输入 schema，需 A 协调；建议 (a) 仅在批准后整批切换并保留 `equidistant`/`scenes`/`timestamps` 语义 |
| D5 | 兼容与回滚 | 是否保持旧 2 列可选 | 建议保留 `grid=2`（若 D4=b）或旧提交回滚；`media_cells` 的 `columns` 必须与实际 sheet 一致 |
| D6 | DP/档位后续 | 何时做 | 仅当 D1 选 (b) 或测量证明固定格尺寸浪费显著时；需 bin cost/评测指标与所有者批准 |

## 3. 最小可验批次（D0/D2/D4 批准后）

1. 纯领域函数 `sheet_columns(width, height, count, cell_height)`：从 `ceil(sqrt(count))` 起向下试探，满足 `width*cols ≤ MAX_SHEET_SIDE` 且 `width*cols*(cell_height+LABEL_HEIGHT)*rows ≤ MAX_SHEET_PIXELS`，`rows=ceil(count/cols)`；无解回退 1（count≤1）或 2；不引入浮点误差（整数）。
2. `_sheet_size` 与粘贴循环共用该函数；`media_cells` 的列数由实际 sheet 列数派生（若 D4 不改持久化，需保证 `CELL_COLUMNS` 与列数一致性检查）。
3. 不改变：每格宽高、`LABEL_HEIGHT`、编码/字节限额、`_register_sheet` 元数据核验、`timestamps` 顺序、scenes/zoom/locate。
4. 放行用例：count=1..12 × width=320/640/1280 的列/行/尺寸矩阵；`MAX_SHEET_SIDE/PIXELS` 精确边界（现测 `(320,8172)`、`(1280,6230)` 仍接受，超 1 像素拒绝）；宫格像素与格号映射一致；sheet identity（宽高与真实 PNG 一致）；空帧/取消/删除/工具图像预算回归；旧 2 列结果对照（若 D4=b）。
5. 验证命令：`uv run --no-sync pytest -q -n 0 agent/tests/v2/integrations/test_v2_media_frames.py agent/tests/v2/integrations/test_v2_media_cells.py agent/tests/v2/integrations/test_v2_media_scenes.py ...`，另跑 frames/sheet_identity/zoom 调用方；strict/ruff/索引/证据。

## 4. 风险与边界

- 现有 `media_cells`/UI/模型工具按 **2 列**理解格号；自适应列数会改变「格号→行列」语义，必须同步 `CELL_COLUMNS` 或持久化实际列数（D4 决策），否则 `media_cells`/zoom 会错位。
- 固定格尺寸下自适应列数**不节省 token**（总像素可能不变或增加尾部空格），收益是减少超宽/空白与更均衡画幅；若目标是省视觉 token，只能走 D1(b) 变尺寸档位，需独立评估。
- 参考 DP 的 `BIN_COST`/档位与 V1 的固定格论证冲突，不在本批擅自取舍。
- 真实长片/大 count 的编码耗时、GPU/内存、UI 可读性另验。
- OCR 与模型来源仍未确定，与本合同无关。

## 5. 明确不做（本设计批）

- 不实现变尺寸格子、8 档 side、DP 打包、1px 缝/编号徽章/音频条/header。
- 不改 `MAX_SHEET_*`、`MAX_FRAMES`、`MAX_CELLS` 或任何资源上限。
- 不改 HTTP/作业 schema、持久化、迁移、模型工具或 UI。
- 不在 D0/D4 批准前修改默认宫格。
