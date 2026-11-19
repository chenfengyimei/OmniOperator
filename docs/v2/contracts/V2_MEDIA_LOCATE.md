# V2 媒体转录定位 media_locate（K16 可离线缺口 #4 实现）

## 目的与参考

实现 [K16 视频分镜审计](V2_VIDEO_STORYBOARD_AUDIT.md) 记录的**可离线缺口 #4：转录文本 LCS 定位只读工具**。参考 `xuexi/dsh-video-storyboard`（`e124ae6f`）`tools/locate.js:12 video_locate` → `locate.js matchSegments:44`：对转录段做 LCS/query-len 评分 + 0.25 精确子串加分、`minScore 0.34` 过滤、返回带时间锚点的最佳匹配（无帧/图）。V2 已有 `media_transcript` 有界字符窗**读取**，但模型须手动翻页找台词；本批补一个**只读定位工具**，让模型按 query 直接命中锚点段。

**不含**帧内 OCR 定位（K16 缺口 #e 的另一义，需 rapidocr 模型，属另验，不在本批）。

## 实现（V2 四层）

- **纯领域 `domain/media_locate.py`**（零 I/O）：`MAX_QUERY_CHARS=200`、`MAX_LOCATE_RESULTS=8`、`DEFAULT_LOCATE_RESULTS=5`、`MIN_SCORE=0.34`、`EXACT_BONUS=0.25`、`MAX_SNIPPET_CHARS=240`。`normalize_query`（strip、1–200、拒控制字符/非 str）、`normalize_limit`（None→5、1–8、拒非 int/越界）、`score_segment`（casefold；精确子串→`1.0+EXACT_BONUS`，否则滚动 1D LCS 覆盖 `lcs/len(query)`，内存随 query 有界）、`match_segments`（≥MIN_SCORE 过滤、按 `(-score, source_index)` 排序、取 limit、保留 `start/end` 锚点 + `segment_index` + `score`(round4) + snippet≤240 + `truncated` 标志）、`render_locate`（asr→`[MM:SS-MM:SS]`、subtitle→`[untimed]`、无匹配明确说明）。
- **`application/media.py`**：`LOCATE_SPEC`（`media_locate`，**只读 writes=False**，`source_id` 必填、`transcript_id` 可选、`query` 1–200、`limit` 1–8，timeout 10，resource media）；`locate/_locate` 用例**复用既有 `_read_transcript`**（删除屏障：加载后重读）+ 源配对（`row.source_id != source_id`→404）+ Agent 隔离（`media_transcript_get`/`media_transcript_latest` agent 作用域，跨 Agent 404）+ `_run_bounded`（closing 准入守卫、持有任务到结算）；`invoke` 新增 `media_locate` 分支返回 `ActionResult` 文本 + 结构化 matches。
- **`composition/instance.py`**：`Capability(MEDIA_LOCATE, media)` 注册进生产 agent 能力（紧随 `MEDIA_LIST`/`MEDIA_TRANSCRIPT`）。**agent 档**只读媒体工具，与既有 media 工具一致**不纳入 `RETRIEVAL_TOOLS`**（`domain/routing.py` 无改动）；非 K08 后台评审白名单（`READ_TOOLS` 不含）。

## 保持的合同

只读无写事务；Agent/源隔离与跨 Agent 404；删除屏障（并发删源拒绝已加载 body）；closing 准入在任何查找前拒绝；有界（query≤200、limit≤8、snippet≤240、结果≤8）；锚点只来自源转录（asr 段时间戳，subtitle 单行无锚点标 untimed），**绝不臆造时间码**；`_run_bounded` 持有任务、取消/关闭等待结算（与既有 media 读一致）。无新表/迁移/HTTP 路由/worker 协议改动；无 routing 改动。

## 验证

- 新增 `agent/tests/v2/integrations/test_v2_media_locate.py` **15 项**（7 领域 + 4 真实实例 FakeProcessor ASR 锚点/subtitle untimed/latest/生产注册只读 + 4 stub 守卫：源配对 404/跨 Agent 404/closing 拒绝/删除屏障），首轮全过（8.85 秒）。
- 媒体调用方专项 **146 过 / 1 既有失败**（`test_v2_media.py::test_v36_upgrade_registers_sources_and_bounds_jobs` 断言 `schema_version=="v2_0039" and len(tables)==141`，是 pin 旧版本迁移滞后，K08 已记录 v36 pin 债务；本批**零迁移/零表改动**，与之无关；其他会话正在改 schema.py/v0041.py 亦可能影响 head 计数）。reads/authority/model_errors/anchors/media 服务用例全过，证明加性改动无回归。
- V2 strict `pyrightconfig.strict.json` **408 文件零错误/警告**（新增 `domain/media_locate.py`，407→408，filesAnalyzed==include）；ruff check/format 通过（修正一处测试未用变量）。
- `project_index` 运行确认 files **823→824**（新 domain 模块计入），modules=65 routes=454 declared_tools=94（media_locate 为 application 层 ToolSpec，与 media_list/transcript 同不计入 declared_tools 索引）。**聚合索引 `docs/index` 更新本批暂缓提交**：再生会把他会话未提交持久层编辑（repository.py/schema.py/event_retention.py/v0041.py）编入全局 `source_sha256`，使已提交索引与已提交源码不一致并间接编码其待提交工作；为保留其工作且保持提交历史一致，索引再生推迟到工作树洁净时（新模块已计入 strict include 408）。本批仅提交本任务源文件/测试/文档/strict 配置，**其他会话源文件与 `docs/index` 保持未暂存**。

##  limits 与后续

LCS 为字符级（非参考 token/LCS 全部变体）；精确子串短路评分；subtitle 单行 untimed；结果有界 ≤8 无翻页（超出按分取前 N）；不含帧内 OCR 定位（需 rapidocr 模型，另验）。`media_locate` 随 media 工具在 agent 档常备（非 opt-in）。本批仅实现 K16 缺口 #4，其余 K13–K18 可离线缺口仍列后续独立批次。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。