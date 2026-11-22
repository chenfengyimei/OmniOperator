# K16 真实素材验收计划

2026-11-22 · status: plan_recorded · 纯文档，未执行真实网络/账号/长片验收。

本计划只覆盖**真实长片与真实 B 站/上传来源**的验收；合成 600s 已由 [v2-media-scenes-long](../evidence/v2-media-scenes-long.json) 覆盖资源上限与切点预算，不替代本计划。决策项见 [V2_K16_REMAINING_DECISIONS](V2_K16_REMAINING_DECISIONS.md)。

## 0. 当前阻塞条件（已核对）

- 本机隔离环境**无** `BILI*`/`BILIBILI*`/`COOKIE*`/`SESSDATA*` 环境变量；仓库内无真实凭据。
- 无所有者提供的真实 >10min 长片或可回放 B 站账号；不能在测试中发起真实网络下载。
- 因此本计划只准备步骤、指标与证据模板，**不执行**真实来源。

## 1. 前置条件（所有者/A 提供）

| 项 | 要求 | 责任 |
|---|---|---|
| 隔离实例 | 独立临时 DB/storage/端口；不得写入主实例；记录实际启动命令 | C 执行，A 确认 |
| B 站来源 | 所有者批准的测试账号或公开可回放视频；凭据只经隔离配置注入，不写仓库/日志/证据 | 所有者 |
| 或上传来源 | 所有者提供 ≥10min 视频文件（含运动/硬切/黑边/低照度），SHA-256 记录 | 所有者 |
| 素材权利 | 允许本地分析、不二次分发 | 所有者 |
| 标注 | 人工切点列表（秒级）或抽检片段；容差 ±1.5s（2fps 采样） | 所有者/C |
| 环境 | `uv run --no-sync` 已同步；离线模型缓存齐备 | C |

## 2. 素材矩阵（建议最少 2 条）

1. **长片 A**：≥10min，真实运动、硬切 ≥10 个、含 1 段渐变/黑边；用于切点质量与资源。
2. **长片 B**：≥10min，低照度/噪声/竖屏或 letterbox；用于增益/四轴黑边。
3. 可选：真实 B 站公开视频（有字幕/多分P）验证下载/时长/凭据失效路径；上传来源验证本地直通。

## 3. 执行步骤（每条素材重复）

1. 在隔离实例创建 channel/source；记录 source id、duration、SHA-256/指纹。
2. 提交 `frames` 作业：`select=scenes`、`count=12`、`width=640`（再补一次 `width=320` 边界）；记录提交/开始/完成时间。
3. 采样期间记录进程 RSS 峰值（OS 工具），记录 worker 退出码/错误码；禁止读取/打印凭据。
4. 校验结果：
   - `scene_cuts` 与人工标注对比（precision/recall，容差 ±1.5s）；
   - `timestamps` 12 个、升序、唯一、落在 0..duration；
   - `media_cells`：cell/row/column row-major，`time_s` 与 timestamps 一致；
   - 宫格宽高/字节 ≤ `MAX_SHEET_SIDE=8192`/`MAX_SHEET_PIXELS=16M`/`MAX_SHEET_BYTES=8MiB`；
   - 采样上限 ≤240（2fps，长片不线性增长）。
5. 失败/生命周期路径：运行中删除源（`media_source_busy`）→ 取消 → 删除后无 `media_frames`；无效 select/timestamps 拒绝；源过期/下载超时（B 站）错误码精确、无产物。
6. 工具图像预算：对完成的 frames 作业执行 zoom 工具/审批/预算路径（沿用既有 e2e 用例的隔离注入），确认 per_call/run_budget 与账本释放。
7. 记录耗时（端到端、scenes、编码）、内存峰值、素材指纹、构建 SHA、OS/依赖版本。

## 4. 指标与放行

| 指标 | 目标 |
|---|---|
| 切点召回/精确率 | 人工标注下 recall ≥0.8、precision ≥0.8（容差 ±1.5s；仅作参考，不代替人工判断） |
| 锚点 | 12 个唯一升序、0..duration、来自真实解码帧 |
| 资源 | ≤240 采样、≤12 帧、宫格三限额不越界 |
| 失败路径 | 取消/删除/无效输入错误码精确且无产物 |
| 稳定性 | 无未捕获异常；worker 退出干净；重复作业可复现 |

未达标时记录差异与最小复现，回到固定素材补测；不直接调阈值/宫格。

## 5. 证据模板

```json
{
  "schema": 1,
  "batch": "K16.real_acceptance",
  "build_commit": "<SHA>",
  "os": "...",
  "materials": [{"kind": "bilibili|upload", "duration_s": 0, "sha256": "...", "labels": "..."}],
  "runs": [{"width": 640, "count": 12, "scenes_s": 0, "total_s": 0, "rss_peak_mb": 0, "cuts": 0, "precision": 0, "recall": 0}],
  "anchors": {"unique": true, "sorted": true, "within_duration": true},
  "limits": {"samples_max": 240, "frames_max": 12, "sheet_bytes_max": 8388608},
  "lifecycle": {"delete_while_running": "media_source_busy", "cancel": "cancelled", "frames_after_delete": 0},
  "not_executed": [],
  "notes": "..."
}
```

## 6. 安全与边界

- 凭据只注入隔离实例；证据只记录 source id/标题摘要/SHA，不记录 cookie/token/完整 URL 查询参数。
- 不向主库/主目录写入；验收结束关闭实例并清理临时材料（保留证据）。
- 本计划**不**宣称 OCR、auto/auto-low、格/档位已验；这些仍按决策包处理。

## 7. 完成条件

两条真实长片（或一条长片 + 一条真实 B 站/上传）跑完第 3 节并产出第 5 节证据，失败路径通过，才可写「真实长片验收通过」；否则保持未验。
