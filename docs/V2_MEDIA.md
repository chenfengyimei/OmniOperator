# V2 媒体与视频理解（D6.media）

本文是 V2 媒体子系统的设计权威：来源真值、有界处理、时间锚点、模型可见文本、取消与恢复、HTTP/工作台契约。实现批次证据见 `docs/evidence/v2-media.json`。

## 1. 生产路径与真值

```
Bilibili 视频读取（复用 bilibili_read_authorized 凭据/配置/修订授权）
  → media_sources 登记（bvid/aid/cid/title/duration 绑定配置修订，唯一键 agent+bvid）
  → 作业 queued → running → done/failed/cancelled/unknown（数据库触发器状态机）
      subtitle：B站在线字幕（无可靠时间轴 → 单行正文）
      transcribe：受管子进程 ASR（faster-whisper 离线缓存，无网络）
      frames：受管子进程等距抽帧拼图（av 解码 + PIL 拼接）
  → 产物：media_transcripts / media_frames（不可变证据行，对象正文只存一份）
  → 模型只见 media_list / media_transcript 工具的实际返回文本（带 [MM:SS-MM:SS] 锚点）
```

- 原始音频/视频字节只在每作业暂存目录（`<home>/run/media-staging/<job_id>`）短暂存在，终态后删除；不进数据库。
- 转写/帧拼图正文以对象发布（`ObjectService.publish`），行表只存 `object_id` 与度量；对象 GC 由既有引用/固定保护。
- 删除传播：`media_source_delete` 级联该来源的作业与产物行；已发布对象按对象合同回收（所有者驱动）。

## 2. 数据模型（schema v2_0036）

- `media_sources`：`kind='bilibili_video'`、`UniqueConstraint(agent_id, bvid)`、`CHECK` 钉死 BV 格式与时长 1..7200；身份列不可变（触发器），`config_id/config_revision_id` 外键绑定登记时的配置修订。
- `media_jobs`：`operation IN (subtitle, transcribe, frames)`、`state IN (queued, running, done, failed, cancelled, unknown)`；准入触发器限制每来源活跃（queued+running）作业 ≤ 8；转移触发器只允许 queued→running/cancelled、running→done/failed/cancelled/unknown；终态不可变；`done` 必须已有产物行（`media_jobs_done_has_artifact`）；终态必须带 `finished_at`。
- `media_transcripts`：`UniqueConstraint(job_id)`、`kind IN (subtitle, asr)`、`bytes 1..4MiB`、对象外键；行不可更新（触发器）。
- `media_frames`：`UniqueConstraint(job_id)`、`frame_count 1..12`、尺寸上限与对象外键；行不可更新。

## 3. 领域上限（domain/media.py）

下载 ≤256MiB、下载预算 180s；转写 ≤4MiB/24000 字/4000 段/每段非空且时间有序；抽帧 1..12 帧、帧宽 320..1280、拼图 ≤8MiB；ASR 计划 model/device(cpu|auto)/compute_type 白名单；工具单窗 ≤12000 字符；同时活跃作业 2（服务信号量），排队 8（数据库准入）。

字幕没有可靠时间轴：载荷为单行 `[0.0, 0.0, 全文]`，读取不伪造锚点；ASR 每段 `[start, end, text]`，正文行格式 `[MM:SS-MM:SS] text`。

## 4. 受管子进程（adapters/workers/media.py）

- `python -m omni.adapters.workers.media`，一行 JSON 请求/一行 JSON 响应，单请求串行；`MediaProcessor`（adapters/media.py）经 `OwnedProcess` 持有，首请求才拉起，取消/断连/崩溃即终止进程，下一请求重新拉起。
- 重型库（av/faster-whisper/PIL/numpy）懒加载；缺组件返回 `media_component_missing`（工作台明确提示安装 `asr` extra），不静默降级、不隐式安装。
- ASR 只从本地离线缓存加载模型（`HF_HUB_OFFLINE=1`）；模型缺失返回 `media_asr_model_missing`。
- ASR 收集每个非空段时立即检查4000段/24000字符上限，超限即停止读取并返回 `media_transcript_unusable`，不写部分产物，也不触发无VAD重试；仅空结果保留原无VAD回退。支持 `close()` 的转写迭代器在正常或异常退出时显式关闭，关闭失败继续暴露。该边界限制已收集转写，不证明模型内部或音频解码内存的上界。
- 超时：probe 20s、抽帧 180s、转写 900s；超时归类 `unknown`（in-flight 请求随进程销毁，结果不可知）。

## 5. 服务与生命周期（application/media.py）

- 并发：`asyncio.Semaphore(2)` 限制活跃处理；作业任务进 `_tasks`，关闭时先 `stop_accepting` 拒绝新收，再等待收尾（默认 30s），处理器进程最后关闭。
- 取消：queued 作业由数据库状态机直接 cancelled；running 作业取消 asyncio 任务，终态写入 cancelled；终态幂等（取消与完成竞态时后到方观察到已完成状态，不报错）。
- 失败分类：`media_processing_timeout/media_processing_failed/media_worker_crashed` → `unknown`；其余业务错误 → `failed` + 精确错误码。
- 重启恢复：实例启动 `media.recover()` 把上一进程的 queued→cancelled(`media_restart_interrupted`)、running→unknown(`media_restart_unknown`)；恢复只写状态机合法转移，不重放任何处理。
- 配置漂移：作业执行前核对来源绑定的 `config_revision_id`；配置被修订后作业失败（`bilibili_configuration_revoked`），重新登记来源即重绑新修订。

## 6. HTTP 契约（/api/v1，Bearer 所有者）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/agents/{id}/media/sources` | 登记来源（config_id + BV），201 |
| GET | `/agents/{id}/media/sources` | 分页（`after` 游标、limit ≤100） |
| GET/DELETE | `/agents/{id}/media/sources/{sid}` | 详情视图 / 级联删除 |
| POST | `/agents/{id}/media/sources/{sid}/jobs` | 提交作业（operation + 计划参数），201 |
| GET | `/agents/{id}/media/sources/{sid}/jobs` | 作业分页 |
| GET | `/agents/{id}/media/jobs/{jid}` | 作业状态 |
| POST | `/agents/{id}/media/jobs/{jid}/cancel` | 取消 |
| GET | `/agents/{id}/media/transcripts/{tid}` | 转写全文（segments） |
| GET | `/agents/{id}/media/transcripts/{tid}/window` | 有界窗口（start/length ≤12000） |
| GET | `/agents/{id}/media/frames/{fid}` | 帧拼图信息（图片经既有 objects content 端点取回） |
| GET | `/agents/{id}/media/processor` | 处理器健康（不拉起进程） |
| POST | `/agents/{id}/media/processor/probe` | 一次有界组件探测（拉起一次进程，缓存结果） |

## 7. 模型工具（只读）

- `media_list`：来源清单（id · bvid · 时长 · 最新转写/帧表指针）。
- `media_transcript`：`source_id`（必填）+ 可选 `transcript_id` + `start/length`；返回头行（transcript_id/kind/language/range）+ 窗口正文。ASR 行带 `[MM:SS-MM:SS]` 锚点；字幕为纯正文。`resource="media"` 共享锁；`writes=False`（失败即错误，不产生 unknown 副作用）。

## 8. 工作台分区（web/ui/v2/media.js）

登记表单（配置 UUID + BV，客户端预校验）→ 来源列表/详情（最新转写/帧表/活跃作业）→ 作业表单（操作切换参数组）→ 作业列表/详情/取消 → 转写窗口阅读（前后翻页、窗口长度）→ 帧拼图预览（经鉴权 fetch blob，非持久缓存）→ 组件探测与缺失提示 → 来源删除（确认对话框，级联提示）。刷新保留当前选择；选中项消失才清空。

## 9. 边界与剩余

- 不信任模型提供的任意下载地址或本机路径；来源只经 B站配置读取授权。
- 未做：上传媒体文件作为来源（V2 对象上传已存在，接入处理管线属后续）、真实 ASR 模型质量与真实 B站视频端到端验收、视频内容理解（帧拼图只是模型输入材料，理解质量取决于模型）。
- 真实供应商/B站/平台编解码验收另记；本批仅隔离假网关与假处理器。
