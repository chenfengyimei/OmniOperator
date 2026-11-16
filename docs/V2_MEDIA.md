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

下载仅接受identity编码的200全文或具有已知总长的单段206，按[HTTP范围语义](https://www.rfc-editor.org/rfc/rfc9110.html#section-14.4)核验Content-Range起点、终点、总长及Content-Length；本实现不支持总长未知或multipart续传。错误范围在读正文/追加前拒绝；合法短响应及未完整的分段沿原三次请求/同一180秒预算继续，未完整不能报告成功，200忽略Range时从头写。已知总长和强ETag在续传时保持，发送If-Range；改变或缺失原强ETag拒绝拼接，弱/非法ETag不作If-Range。无强校验器时不证明远端内容身份。声明超限在读正文前拒绝，逐块在写入前检查上限，已有超限部分不截断、不请求网络。路径准备失败也释放读任务归属；每次请求仍先执行授权回调，错误不记录Cookie或CDN地址。

流清单中的`durl`只有单项时才可作为整段媒体候选，多项不能取首段冒充完整内容。音频仍优先最低码率DASH；合并视频为多段时回退完整DASH视频。没有任何完整候选且遇到多段清单，以`bilibili_stream_unsupported`（422）拒绝；只有另一种媒体可用时保留该项，所需流缺失的作业沿既有`media_stream_missing`失败。普通空清单仍为`bilibili_read_rejected`。本批不拼接多段，不扩大下载或处理预算；不证明远端单项URL与声明内容一致。

DASH从实际可用的URL候选中选择最低码率，缺少或为空的主/备用URL不会阻止后续可用轨道。保留`baseUrl/base_url`、`backupUrl/backup_url`别名；缺省大小为未知0，缺省或零码率沿原排序默认值，非负整数与十进制数字字符串保持支持。DASH对象/列表/记录及所用URL、备用列表、码率/大小类型变化（含布尔、负数和小数）以`bilibili_response_invalid`（502）拒绝，不转成unknown或静默丢弃坏记录。选中URL仍须通过既有CDN白名单，单段合并流优先级、来源身份与下载预算保持。

单段合并流同样核验`durl`列表和记录类型；单段大小复用DASH的无损非负整数解析，缺省为未知0，坏大小不记成unknown。主`url`缺失或为空时，从`backup_url/backupUrl`中选首个非空字符串；所用备用列表与记录类型必须合法，地址继续通过同一CDN白名单。坏字段统一为`bilibili_response_invalid`，空候选仍可回退DASH。多段标记与禁止首段冒充整段的行为保持；这里只选择一个地址，不自动轮换地址或增加下载重试。

宿主读取worker产物时也执行字节边界：转写单次最多读取4MiB加1字节，超限在解析和对象发布前以`media_transcript_unreadable`拒绝；ASR产物的kind必须为asr，不能以字幕正文发布成ASR记录。拼图先校验计数/尺寸/时间戳/指纹/字节元数据，再按已验证的声明大小（≤8MiB）加1字节读取，超出声明、长度或SHA256不符均以`media_sheet_invalid`拒绝且不发布对象/图像。合法文件不截断、不重新编码，读取句柄在成功或异常时关闭。此处限制的是产物读取缓冲，不是原生解码、模型或整进程内存峰值证明。

转写解析入口在解码前再次执行4MiB边界，只接受无BOM的UTF-8 JSON；拒绝重复字段（含转义后的同名键）、NaN/Infinity、浮点溢出和过深JSON。version必须为整数1，language最多16字符且可编码为UTF-8，正文也不能包含孤立代理码位；字幕时间锚点须为数值零，不能用布尔值冒充。ASR及拼图时间戳中的极大整数、非有限值统一为现有422业务错误，不泄漏OverflowError到HTTP或把已知坏产物记成unknown。正常ASR/字幕行、24000字符/4000段及精确字节边界保持；读取坏的已存产物不改写其历史作业终态。

字幕没有可靠时间轴：载荷为单行 `[0.0, 0.0, 全文]`，读取不伪造锚点；ASR 每段 `[start, end, text]`，正文行格式 `[MM:SS-MM:SS] text`。

## 4. 受管子进程（adapters/workers/media.py）

- `python -m omni.adapters.workers.media`，一行 JSON 请求/一行 JSON 响应，单请求串行；`MediaProcessor`（adapters/media.py）经 `OwnedProcess` 持有，首请求才拉起，取消/断连/崩溃即终止进程，下一请求重新拉起。
- 重型库（av/faster-whisper/PIL/numpy）懒加载；缺组件返回 `media_component_missing`（工作台明确提示安装 `asr` extra），不静默降级、不隐式安装。
- 抽帧在每次转换/缩放前按预计帧尺寸与已选数量计算拼图，宽高≤8192且总像素≤16000000；超限在分配下一张图前以`media_sheet_invalid`拒绝。PNG编码缓冲每次写入前检查8MiB总上限，超限不写产物文件。被缩放的原图立即关闭，保留的缩放图、无需缩放的原图及最终拼图在成功和Python异常路径关闭，编码缓冲也显式关闭；关闭异常保留上下文并继续尝试其余图像的关闭。正常选帧、标签与两列布局保持，缩放高度采用整数比例。此处约束Python侧转换/拼图与编码缓冲，不证明原生解码帧、Pillow编码器内部或整个进程的峰值内存，也不处理原生崩溃的进程内清理。
- 帧时间锚点必须来自解码帧本身的数值时间戳，有限且在0..7200秒内；缺失、布尔/非数值、非有限及越界时间戳以`media_frame_timestamp_unusable`拒绝，作业为failed。按目标时间查找最近帧时先校验所读帧；有/无时长元数据的产物生成都在转换图像前再校验，不以零秒或抽样目标补造锚点。真实零秒和7200秒保留，合法帧的最近点选择、毫秒精度与标签格式保持。失败不发布帧产物，不重放原作业；后续显式请求可复用健康worker。
- ASR 只从本地离线缓存加载模型（`HF_HUB_OFFLINE=1`）；模型缺失返回 `media_asr_model_missing`。
- 离线模型缺失识别按当前异常逐层沿显式cause或context查找，cause优先；包含顶层在内最多检查6个异常节点，重复节点即停止。保留现有缺失类型/文本线索，未映射的ASR `ValueError`包装也参与识别；已知业务错误、缺组件及非ASR `ValueError`仍优先保留原分类。该改动只恢复精确缺失码和现有离线缓存准备提示，不下载模型、不重放失败作业，健康worker可用于后续显式请求。
- ASR解码按实际16kHz单声道输出累计样本，最多115200000样本（7200秒、s16 PCM 230400000字节），不信任来源或容器的时长元数据。普通重采样输出与排空尾部共用预算；首次超限在读取该PCM平面前报 `media_audio_duration_out_of_range`，不调用转写推理、不发布部分产物，作业为failed；后续显式新作业仍可运行。输入容器和PCM缓冲区在正常/异常退出时关闭，浮点转换后原地归一化，避免额外的浮点结果副本。正常结束以[PyAV的resample(None)](https://pyav.org/docs/stable/api/audio.html#audio-resamplers)排空延迟样本，并兼容直通模式返回的空尾项。该限制只约束已累计PCM与返回数组，不证明解码器/重采样器内部、单个原生帧、模型或整个worker的峰值内存上界，也不为旧系统提交额度故障定因。
- ASR 收集每个非空段时立即检查4000段/24000字符上限，超限即停止读取并返回 `media_transcript_unusable`，不写部分产物，也不触发无VAD重试；仅空结果保留原无VAD回退。支持 `close()` 的转写迭代器在正常或异常退出时显式关闭，关闭失败继续暴露。该边界限制已收集转写，不证明模型内部或音频解码内存的上界。
- ASR非空段的起止时间不再缺省为零；缺失/None、布尔/文本、非有限、负值及超过7200秒的时间以`media_transcript_unusable`拒绝，极大整数溢出也归该错误。每段在追加前核验起点≤终点，并沿宿主相同规则允许最多1毫秒重叠；倒序或超量重叠立即停止后续推理、关闭迭代器，不写部分转写或触发无VAD重试。真实零秒、7200秒边界及原有空段跳过/空结果回退保持；原有数值转换或推理异常保留原错误，关闭失败保留异常上下文。此处只验证模型已返回的段，不证明真实ASR对源音频的时间识别准确度。
- ASR段正文须为真实字符串；缺失/None、布尔、数值、容器或字节不能字符串化成转写。每个非空正文在既有字符限额和锚点检查后、追加前核验UTF-8，首个坏段即停、关闭迭代器，以`media_transcript_unusable`结束，不发布前缀或额外触发无VAD重试。语言字段缺失/None仍为未知空字符串，其余须为字符串，截取的16字符输出须可编码为UTF-8；在创建产物目录前拒绝坏语言。合法空白段跳过、正文修整、Unicode、语言截取及原空结果回退保持，健康worker可处理后续显式新作业。
- 超时：probe 20s、抽帧 180s、转写 900s；超时归类 `unknown`（in-flight 请求随进程销毁，结果不可知）。
- 上述处理预算在开始写入stdin时建立，覆盖write/drain/read等待，不能等写入完成后为读取重开预算；排队、启动和旧归属回收仍沿各自既有边界。回包须为≤8MiB且以换行结束的JSON，版本必须为整数1、请求ID匹配、result为对象且ok为布尔值；重复字段（含嵌套）、NaN/Infinity及浮点溢出均拒绝。读流超限、坏帧或过深JSON归`media_processing_failed`，处理超时仍为`media_processing_timeout`；两者先回收旧归属，应用保留unknown，不自动重放。合法业务失败仍保留精确错误码及可复用worker；不能把协议错误当业务失败或成功。
- `ok=false`必须携带明确错误码：1..80字符，首字符小写ASCII字母，后续仅小写字母/数字/下划线。缺失、空值、容器、布尔、数值、非法字符或超长码均为坏回包，不字符串化、不补造业务码；沿已有协议失败路径先回收worker，作业保留`unknown/media_processing_failed`。实际回收未完成时继续保留原归属并阻止替换或重放；合法精确错误码和成功回包保持原行为，只有显式新请求才可启动新worker。
- 媒体进程只有在`OwnedProcess.terminate()`确认实际回收后才释放引用；取消、回收异常或返回false均保留同一归属并显示`cleanup_pending`，期间`available=false`。后续显式请求先尝试回收旧归属，未完成则以`media_cleanup_pending`在派发前拒绝；主进程退出不等于整个进程组回收，不能直接覆盖旧引用。`stop_accepting`之后，锁内排队请求以及回收/启动途中返回的请求均再次核验关闭状态，已有执行不被关闭等待取消。并发`close`共用请求锁串行完成实际回收，并沿既有关闭预算返回，预算不足或取消仍保留归属；不重放旧请求、不提前声称关闭成功。
- 进程创建由独立`media-spawn`任务持有，空Context避免继承请求上下文；请求取消只结束等待，不取消实际创建。尚未交付的创建结果保留为`starting`，取消后显示`cleanup_pending`并阻止直接重启。关闭或后续显式请求先在回收预算内等候实际结果，再用剩余预算回收迟到进程；不能为两个阶段各开完整预算。预算不足、关闭被取消或创建结果无法确认时保留归属，不报告关闭完成。正常创建失败保留原异常；取消后迟到失败只记固定事件，不记录原生异常正文，仍回收已发表的进程引用。原生`OwnedProcess.spawn`本身的实现与平台保证未改变。
- stderr由独立`media-stderr`任务持续以最多4KiB读取后丢弃，任务与完成回调使用空Context；只保留饱和于`2**53-1`的累计字节计数，不缓存或记录诊断正文。正常EOF仅记录一次固定事件和本次字节数；读取失败或意外取消显示`stderr_failed`与`cleanup_pending`，下一次派发前必须先回收。旧读取任务的迟到回调不能污染新worker。实际进程回收后还须在原关闭预算的剩余时间内等待读取任务结束，才能释放两者引用；超时或关闭被取消保留归属，不强行取消读取任务来宣称完成。读取途中发生的故障不推翻已经收到的合法业务结果，后续请求仍先核查回收。

## 5. 服务与生命周期（application/media.py）

- 并发：`asyncio.Semaphore(2)` 限制活跃处理；作业任务进 `_tasks`，关闭时先 `stop_accepting` 拒绝新收，再等待收尾（默认 30s），处理器进程最后关闭。
- ASR提交在任何I/O及入队前用原始参数类型执行领域校验；只有缺失字段使用`small/cpu/int8`默认值。显式`null`、布尔、数值、空字符串/容器不能被字符串化或替换成合法默认计划；模型字段错误为`media_asr_model_invalid`，设备/计算类型错误为`media_asr_plan_invalid`（422）。合法字符串模型名、设备与计算类型逐项保留，HTTP既有严格校验保持；无效计划不消耗作业准入，不创建失败作业行。
- 登记来源、创建作业与组件探测也在首次I/O前纳入实际任务归属；调用方取消只结束等待，不取消已准入的事务/网络处理，也不自动重放。关闭后上述入口统一以`media_closing`拒绝且不调用端口。作业创建结果交付前仍持有提交任务，交付后新增的后台作业也须排空；不能只等关闭开始时的一份任务快照。关闭超时或调用方取消不取消这些任务，数据库、对象与实例锁仍由生命周期协调器保留。任务排空与处理器回收共用一次关闭预算，回收只获剩余时间，后续关闭可继续核查实际结果。
- 转写全文、按来源读取窗口（指定或最新转写）及按转写ID读取窗口，均在首次查询前持有完整读取任务；关闭后入口在I/O前以`media_closing`拒绝。已准入的窗口在排空期间继续完成后续正文与元数据读取，内部读取不重复走新任务准入；请求或关闭等待方取消不取消实际工作，生命周期仍保留数据库和对象服务。来源配对/Agent校验、对象解析和返回前的转写存在性复核保持，因此并发删除仍拒绝已读正文。窗口格式、范围错误与只读语义不变。
- 作业先从queued进入running，确认未被排队取消后才创建暂存目录；目录创建失败归`failed/media_staging_unavailable`，不下载、不调用处理器、不发布产物。已取消的排队作业不重复结算，也不创建目录。终态写入即使抛出异常仍尝试清理暂存目录，原结算错误继续暴露，不能伪称持久终态已提交。目录已不存在视为完成；权限等删除错误或仅子文件消失仍记录固定`media.staging_left`与目录，不记录异常正文。诊断不代表删除成功，本批不引入自动删除重试。
- 取消：queued 作业由数据库状态机直接 cancelled，并回收仍在等待处理槽位的后台任务；无需等待其他运行作业释放槽位。重复取消已为cancelled的作业也会回收尚存的队列等待任务。队列归属在取得槽位时移除，任务完成回调负责最终释放，因此首次执行前被取消也不会留下归属；健康信息`queued_jobs`只计尚未取得槽位的作业。running 作业取消 asyncio 任务，终态写入 cancelled；终态幂等（取消与完成竞态时后到方观察到已完成状态，不报错）。
- queued取消写入若发现作业已进入running，继续取消实际所有者，不能把原queued快照当作取消成功。有实际队列等待任务时，服务持有从取消写入到任务回收的处理；请求取消只结束等待，不丢失已提交取消的队列回收，关闭仍等待该处理。running所有者只发送一次任务取消；重复请求等待同一清理，不再次打断回收或结算。取消等待方自身被取消时向调用方传播，后台作业继续由服务持有；终态作业保留持久状态，不打断已取得槽位后仍在完成收尾的任务。找不到实际所有者时保留running真值，不伪造cancelled。
- 失败分类：`media_processing_timeout/media_processing_failed/media_worker_crashed` → `unknown`；其余业务错误 → `failed` + 精确错误码。
- 重启恢复：实例启动 `media.recover()` 把上一进程的 queued→cancelled(`media_restart_interrupted`)、running→unknown(`media_restart_unknown`)；恢复只写状态机合法转移，不重放任何处理。
- 配置漂移：作业执行前核对来源绑定的 `config_revision_id`；配置被修订后作业失败（`bilibili_configuration_revoked`），重新登记来源即重绑新修订。
- 登记来源的最终写事务重新核对原配置修订与凭据authority；处理中在字幕/流地址返回后、下载派发及重试前、下载和worker返回后、对象/图像登记前复核，并核对来源仍绑定同一配置修订。凭据代次改变保留`bilibili_credential_changed`，配置撤销或来源重绑保留`bilibili_configuration_revoked`；不重试旧作业。媒体产物完成与最后一次授权检查在同一数据库写事务，不能用事务前的异步检查替代。已发出的读请求、已完成的对象/图像登记不回滚；晚到撤销只阻止后续阶段及媒体产物行提交，既有对象/图像按原引用与GC规则管理。恢复后须显式重新登记来源和提交新作业。
- 字幕和流地址通过独立`read_source`端口读取登记来源，网关在首次I/O前复制BV/AID/CID身份；登录及视频元数据返回后，在字幕或playurl请求前核对三者。换稿或身份不匹配以`bilibili_source_changed`结束作业，不派发字幕/流地址、下载或处理器，也不向旧来源发布新内容；原来源记录和旧作业不自动改写或重放。普通所有者视频查询仍返回当前元数据。该检查绑定所请求的内容坐标，不替代远端API/CDN本身的真实性证明。
- 官方字幕响应在流式读取中限制解码后正文为2MiB，API JSON为512KiB；超限块在追加到正文缓冲前拒绝，不继续读取剩余响应。状态/类型不符不读取正文，正常、超限、中断和取消均由响应上下文关闭；超限保持`bilibili_response_too_large`，不发布部分转写。沿用既有读取总预算、并发和来源身份核验；此处只约束宿主保留的正文，不证明HTTP解压器内部或整个进程的峰值内存。
- 官方字幕列表及行字段按真实类型校验；列表坏项、错误语言类型、选中字幕缺URL、缺失或非字符串正文，以及不能编码为UTF-8的输出均以`bilibili_response_invalid`拒绝，不跳过坏行或把对象/数字转成正文。未提供字幕或空列表仍返回无字幕；中文优先、首项回退、空白修整、60000字符上限和语言16字符截取保持，不从官方字幕行猜造时间锚点。

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
