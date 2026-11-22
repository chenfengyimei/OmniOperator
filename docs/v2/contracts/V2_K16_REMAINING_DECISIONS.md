# K16 剩余缺口决策包

2026-11-22 · status: decision_requested · 纯文档，无源码/测试/依赖/迁移改动。

适用范围：K16 媒体场景切点、黑边/增益、自适应宫格与 OCR。已完成子批与证据见 [V2_MEDIA_SCENES](V2_MEDIA_SCENES.md)、[V2_VIDEO_STORYBOARD_AUDIT](V2_VIDEO_STORYBOARD_AUDIT.md)。本文件只请求决策，不代替所有者/A 的批准。

## 0. 已核对的环境（本机隔离环境）

- `rapidocr`：**未安装**；`rapidocr_onnxruntime`：**未安装**；`paddleocr`：**未安装**。
- `onnxruntime`：**1.31.1 已安装**（现有依赖间接提供；不是 OCR 合同）。
- `agent/pyproject.toml` / `uv.lock`：**无** rapidocr/paddleocr 依赖声明；本仓库当前没有 OCR 模型缓存或下载配置。
- 结论：OCR 不是“缺一个 import”；需要**依赖包 + 模型文件 + 离线 provisioning + 精确错误码 + 测试注入**的独立合同。按审计结论（约 250MB Python 依赖、未捆绑），本会话不擅自下载/安装。

## 1. 决策项

### D-OCR：是否引入帧内 OCR / 字幕带定位

| 选项 | 内容 | 影响 |
|---|---|---|
| A（推荐暂缓） | 保持无 OCR，不新增依赖/模型；继续把 OCR 列为 blocked_dependency | 无供应链/包体/离线缓存风险；K16 的 OCR 缺口保持未验 |
| B | 新增 `rapidocr_onnxruntime` 固定版本 + 离线模型缓存（instance 数据目录，逐字节校验），`media_component_missing` 精确错误，禁止自动下载；用 fake backend 做单测 + 真实帧验收 | 需所有者批准依赖与模型来源，A 协调打包/CI 缓存；包体约数百 MB |
| C | 用现有 onnxruntime 自研推理层 | 需模型来源/预处理/后处理合同，工作量大于 B，不建议 |

若选 B，最小批必须包含：依赖 pin、模型校验/路径、缺模型错误码、有界帧数/超时/内存、失败不崩溃、离线测试注入、真实字幕/帧验收与回滚。

### D-AUTO：参考 `auto/auto-low` 分位阈值

- 现状：固定阈值 + 渐变合并 + 黑边 + 尖峰 + 暗部增益已在 32 项 scenes 与 422 项 K16 入口聚合通过；**没有稳定固定素材能证明 auto 阈值收益**（合成噪声多被尖峰守卫压掉；真实长片未验）。
- 选项：A 暂不实现（推荐）；B 先做真实长片标注与评测（precision/recall/预算），达标后再实现且默认关闭；C 直接实现（不建议，可能回归 32+422）。
- 若走 B，需所有者提供或批准真实长片样本与标注口径；本轮不盲调阈值。

### D-GAP：自适应 minGap

- 固定 `SCENE_MIN_GAP_S=0.35` 已小于 2fps 采样间隔（0.5s），实际由采样网格主导；无固定失败样本。建议**不做**，保留现状与边界说明。

### D-GRID：自适应宫格/DP

- 见 [V2_ADAPTIVE_GRID](V2_ADAPTIVE_GRID.md) 的 D0–D6；需所有者与 A 先定 **D0（是否做）**、**D2（列数规则）**、**D4（接口：默认/新增 grid 参数/仅 scenes）**。
- D4 若新增 HTTP 参数，属共享接口，需 A 协调；未批准前不改默认宫格。

### D-REAL：真实长片与真实来源验收

- 现状：60s 合成素材仅验证采样上限与切点预算；真实 >10min、真实运动/压缩、真实 B 站/上传未验。
- 验收清单：240 采样上限、切点质量（人工标注）、端到端耗时/内存、宫格字节/像素、取消/删除、工具图像预算、失败不产出。
- 需账号/网络/真实素材，按另验流程执行。

## 2. 建议顺序（无决策时的默认动作）

1. 所有者先回复 **D-OCR** 与 **D-GRID 的 D0/D2/D4**；A 确认共享 HTTP/CI/缓存责任。
2. 若 D-OCR=A、D-GRID=D0 未批：K16 场景算法保持现状，不再引入无失败样本的算法改动；可转真实长片/真实来源验收准备。
3. 若 D-OCR=B：按上述最小批实现（依赖/缓存/错误码/测试注入）。
4. 若 D-GRID=D0 批：按合同最小批实现纯函数 + 尺寸矩阵 + identity/cells 对齐；D4 接口由 A 拍板。

## 3. 明确不做（本决策包）

- 不安装 rapidocr/paddleocr、不下载模型、不改 `pyproject`/`uv.lock`。
- 不实现 auto/auto-low、minGap、宫格或 OCR 代码。
- 不改 HTTP、迁移、CI、生产默认行为。
