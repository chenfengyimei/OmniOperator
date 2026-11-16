# 测试运行与提速

## 日常开发

按所有者最新要求，持续开发优先实现与受影响模块及调用方的专项验证，不为每个小模块或每次提交运行全量；多个模块集成收尾或所有者明确要求时再运行一次全量。专项通过只证明该范围，每批明确当前源码是否已被全量覆盖。

```powershell
# 小范围验证：串行避免 worker 启动、重复导入/收集的开销
uv run --no-sync pytest -q -n 0 agent/tests/test_memory.py agent/tests/test_owner_memory.py

# 重跑失败：没有失败记录时直接结束，不退回全量
uv run --no-sync pytest -q -n 0 --lf --last-failed-no-failures=none

# 多模块集成或明确要求时全量：默认最多 4 个进程，按文件调度
uv run --no-sync pytest -q

# 并发问题诊断 / 调整并行度
uv run --no-sync pytest -q -n 0
uv run --no-sync pytest -q -n 8
```

同一份源码、测试、依赖、测试配置已全量通过时复用该次结果；其中任一变化或其他会话改了代码，都要重新判断受影响范围与最终验证。不要并发启动多个完整套件。只使用规范路径 `agent/tests/`，避免本机兼容联接 `tests/` 带来不同 nodeid 和失败缓存。

V2 文档维护的开发反馈先选 `test_v2_maintenance.py`、`test_v2_maintenance_http.py`；所有权、迁移或恢复发生变化时再增加对应的 `ownership`、`crash`、`migration` 文件。界面改动先选 `test_v2_maintenance_ui.py` 和受影响的具体 nodeid，整份 `test_v2_ui.py` 的多视口/主题业务矩阵留到跨界面验收。该矩阵保留完整场景、独立上下文与超时，不用缩短业务等待换取提速；开发反馈通过减少无关重复范围获得。

修改 `docs/V2_PACKAGING.md` 的安装/更新交付状态或构建检查入口时，先运行 `uv run --no-sync pytest -q -n 0 agent/tests/test_v2_packaging_contract.py`。该专项检查已验证构建链与未交付平台/安装器边界，通常不需要等完整套件才能发现文档合同回归。源码链已实现和独立安装器已完成交付验收应分别表述。

记忆整理开发使用 `test_v2_memory_organization.py`、`test_v2_memory_organization_http.py`；关系/召回变更加入 `repository`，迁移/恢复加入 `migration`，事务中断加入 `crash`，界面仅先跑 `test_v2_memory_organization_ui.py`。必须验证逐字原文与规范化相同的差别、同作用域、审批不改记忆真值、可撤销召回、真实来源删除传播、恢复后旧核查不可复用，以及所有者CAS和迟到界面隔离。默认不调用模型或创建费用账；不得通过遗忘重复项实现普通整理。

可信插件开发先跑 `test_v2_extensions.py`、`test_v2_extensions_http.py`；清单使用 `test_v2_extensions_contract.py`，仓储/中断使用 `test_v2_extension_repository.py` 与 `test_v2_extension_crash.py`，迁移/恢复使用 `test_v2_extensions_migration.py`。包和进程变更加入 `test_v2_extension_packages.py`、`test_v2_extension_hosts.py` 及已有worker调用方；界面先选新增 `test_v2_extensions_ui.py`，不每次运行旧工作台全矩阵。所有源码、进程、模型和写文件夹具隔离；插件必须先保持登记/授权零执行，再验证真实审批后代码、派发中断unknown及取消回收，不能通过模拟结果宣称实机能力。

历史迁移夹具仅在旧schema造数时使用 `v2_legacy_seed.LegacySeedRepositories`，不把新版扩展表查询或运行目录捕获加到旧库；升级后必须使用真实Repositories。新增capture列由新迁移回归单独核验，旧字段继续精确比较，不在生产加入旧schema兼容层。

## 并行与浏览器隔离

根目录与 `agent/` 的 pytest 配置一致：`-n auto --dist=loadfile --durations=10`，`conftest.py` 将自动 worker 数限制为 `min(4, CPU 数)`。`pytest-xdist` 属于开发依赖并锁定在 `uv.lock`。`-n 0` 关闭并行，显式 `-n 8` 或 `PYTEST_XDIST_AUTO_NUM_WORKERS` 可覆盖自动 worker 数。

`loadfile` 保证同一文件在同一个 worker 中执行，复用模块级 fixture。`ui_browser` 在一个 UI 测试文件内仅启动一次 Chromium；每个用例仍创建独立 BrowserContext、页面、localStorage、cookie 和临时数据库。该 fixture 不跨模块存活，防止同步 Playwright 的运行中事件循环干扰其他 asyncio 测试。需要特别启动参数或浏览器生命周期断言的测试保留自己的浏览器。

`conftest.py` 仅为 `loadfile` 装配 `CrashSafeLoadFileScheduling`：worker 崩溃重排后清除队列中已完成的空工作单元，并为替换 worker 补足执行当前用例所需的下一项或正常关闭标记。全部未完成用例及文件归属保留，原崩溃仍记失败；其他显式调度模式继续使用 xdist 默认实现。回归见 `test_pytest_scheduler.py`，不调整 worker 数、断言、超时或离线闸门。

`failure_reports.py` 在普通失败报告渲染抛出 `MemoryError` 时，保留原节点、阶段、失败状态、用时、捕获输出和用户属性，生成有界诊断；诊断读取原异常的文本参数和有限栈帧，不再加载源码/AST，也不调用任意异常参数的字符串方法。原调用没有异常、显式跳过或报告生成抛出其他错误时继续暴露内部错误。普通失败会立即输出节点与阶段，结束时仍保留标准汇总。该兜底不修复潜在内存增长，也不把失败或错误改成通过；真实 pytest/xdist 子进程回归见 `test_pytest_failure_reports.py`。

本机进程退出诊断可加 `--worker-diagnostics=temp/build/本轮新目录`。`worker_diagnostics.py` 默认不创建文件或采样；启用时要求全新目录，为控制器和每个 worker 分别保存阶段/结果、PID、系统提交内存及退出码，每个 JSONL 最多16 MiB，截断或采样失败明确标记。Windows 高位状态保存十六进制，不根据状态码自行判定根因；其他平台提交指标可为空。已有 pytest faulthandler 的独立文件描述符改接持久 `.fault.txt`，保留致命异常时的Python栈和既有超时栈，原超时及退出设置不改。正常关闭由 pytest 回收描述符。Python3.12的该输出只含Python栈位置，不提供C层回溯；参见[Python文档](https://docs.python.org/3.12/library/faulthandler.html)。

控制器在通道关闭时只做非阻塞退出码查询，再在 xdist 原有回收完成后记录最终查询结果；后者可能受 xdist 回收影响，不能当成未经干预的原生故障码。日志保存pytest节点标识（含参数ID）、阶段、固定指标和受限的通道状态，不额外序列化测试参数值、局部变量或普通测试异常正文。诊断不会关闭用户服务、修改页面文件或把失败改成通过；系统提交额度不足应单独记录，物理可用内存和 RSS 不能替代提交额度。回归见 `test_pytest_worker_diagnostics.py`；受控高位终止不是实际访问冲突复现。

运行身份的短预算关闭可能在 `draining` 发布完成前返回。`test_v2_service_runtime.py` 先在最多5秒内等待该实例拥有的实际发布任务，再验证私有文件；原关闭预算、锁归属和就绪断言保持不变。另有原子替换回归明确要求读取中的身份变化仍报 `runtime_identity_changed`，生产读取检查不增加重试或放宽。

合成媒体的5个PyAV输出点（分镜、抽帧、工具下载样片、AAC和长视频）在`finally`中关闭已取得的容器。流创建、帧转换、编码、封装或排空抛出Python异常时仍执行关闭；关闭成功保留原异常，关闭失败继续报错并保留原异常上下文。`test_storyboard_fixture.py` 的40项回归使用真实原生容器与包，在边界注入异常，另核对正常产物能解码；原有帧数、尺寸、编码、长视频长度和业务断言保持。该修复不保证原生进程终止时执行Python清理，也不证明全局提交额度耗尽或旧worker退出已解决。

V2 ASR worker的收集预算由`test_v2_media_worker.py`验证：首次超出4000段/24000字符即停止读取，正常与无VAD回退路径均覆盖；精确上限、Unicode、空白段和时间锚点保持。强引用生成器验证异常时真实关闭，关闭失败保留上下文；隔离子进程通过真实`MediaProcessor`帧协议回传`media_transcript_unusable`，随后在同一worker完成正常请求。模型与音频解码均注入，不访问真实模型缓存、设备或网络；媒体应用、UI与打包调用方另验。

ASR输入解码的`test_v2_media_audio.py`使用隔离WAV和真实PyAV覆盖8/16/44.1/48kHz、单/双声道、完整尾部与精确预算。控制样本数只缩小新用例的输入预算，不缩短业务超时；生产预算与既有7200秒领域合同对齐。受控原生边界证明首个超限平面未读取、后续帧未消费且容器关闭，另核对s16全范围归一化和对齐填充排除。真实子进程仅注入ASR模型，保留实际解码与帧协议，验证精确错误、零推理/零部分文件和同PID后续成功；应用用例另验failed持久化、暂存清理及下一作业可完成。默认套件不加载真实模型或访问网络。

媒体传输生命周期由`test_v2_media_transport.py`覆盖：事件屏障控制关闭、回收和启动交错，验证未回收引用不丢失、无旧进程派发、并发关闭串行及已准入请求继续完成。真实子进程在首次回收返回false后保留原请求，下一请求必须先回收整个旧归属再启动新worker；核对协议记录的实际worker PID，不将Windows venv启动器PID当成worker。应用调用方验证派发前的cleanup/closing拒绝为failed且无产物，后续显式新作业可完成；不改900/180/20秒处理预算，不重放旧工作。

`test_v2_media_protocol.py`覆盖完整回包、精确字节上限、版本/结果的严格类型、重复字段、非有限数值、读流超限及过深JSON。独立事件循环注入可前进的时钟，证明stdin写入等待已经耗尽预算后，不能给读取重开时限；不缩短生产20/180/900秒配置，也不靠真实长时间休眠。三项真实stdio子进程分别输出超长、无换行和错误版本回包，验证旧进程组回收、后续新worker身份及不重放；合法成功和明确业务错误仍复用同一worker。与媒体应用、已有ASR/音频及生命周期专项一起验证，不替代集成全量。

`test_v2_media_startup.py`以创建结果交付屏障覆盖请求取消、关闭取消、迟到成功/失败及上下文隔离；注入时钟验证待启动阻塞后续派发且等待与回收共用预算。真实`OwnedProcess.spawn`先创建隔离stdio子进程，再暂停结果交付，核对取消后零预算关闭不得报完成、迟到结果仍由处理器回收；夹具最终另行回收所有已知测试进程。用例不加载真实模型、不产生设备输入，也不缩短生产超时。

`test_v2_media_stderr.py`覆盖4KiB分块、饱和字节计数、诊断正文不入日志、读取失败/取消的准入检查，以及进程已回收而读取尚未结束时的关闭超时/取消归属。真实隔离stdio子进程连续两次各写256KiB stderr后回包；只在夹具内降低流缓冲界限，以5秒观察进度，生产20秒请求预算保持。旧源码对照若阻塞，夹具在失败后的清理阶段单独排空其测试管道并回收进程，原始失败仍保留。专项连同既有启动/协议/回收、媒体应用、ASR/音频及生命周期运行，不代替全仓集成。

`test_v2_media_admission.py`在登记来源、作业创建结果交付和组件探测端口设置独立屏障，验证停止准入后零端口调用、请求/关闭取消仍保留实际任务，以及关闭开始后由已准入提交产生的子任务也必须排空。独立事件循环时钟验证30秒关闭预算只给回收剩余时间，超支时钳为零，不缩短生产处理超时。真实数据库夹具在作业已提交但结果尚未交付时取消HTTP调用方等价的等待，核对生命周期保留数据库、随后只执行一次字幕处理并发布关联产物；网关与处理器仍使用隔离注入。只运行该模块与媒体/生命周期调用方，不能将专项写成全仓通过。

`test_v2_media_staging.py`覆盖目录创建异常、三类已排队取消的作业、结算失败时仍清理文件及保留原错误，以及目录已不存在/删除受阻/仅子文件消失的诊断区别。三项真实数据库用例分别验证`failed/media_staging_unavailable`、取消不重复结算、终态写入回滚后仍清理；各例还显式提交新的字幕作业，确认新作业成功且旧状态未被重写。网关/处理器隔离，注入仅限当前作业路径或该媒体服务的数据库端口，不改生产超时、不调用真实账号/模型。

`test_v2_media_output.py`观察实际read参数与句柄关闭，验证拼图元数据非法时零读取、声明大小加1的读取上限、空/截断/多余字节/错误指纹零发布，以及合法8MiB边界不截断。四项真实数据库用例覆盖正常/恰好4MiB/超限/错误kind的ASR输出，核对超限或错误kind在对象发布前失败、正常产物字节保持、暂存清理和显式新作业可继续。仅测试产物读取与发布边界，不以此声称模型内存或完整JSON格式收口；网关和处理器均离线注入。

`test_v2_media_codec.py`覆盖重复/转义字段、非有限数字、极大整数、版本与锚点类型、编码/Unicode、语言长度及解码前字节边界，并保留正常行与字符/段数/字节上限。两项真实数据库ASR用例核对坏JSON或数值溢出在对象发布前失败且新作业可继续；两项HTTP用例将坏内容作为指纹与字节元数据一致的真实对象登记，验证全文/窗口返回422、无正文泄漏且不改写旧done作业，后续正常产物仍返回200。使用独立数据库和离线端口，不运行真实模型、平台或全仓测试来代替这些明确边界。

`test_v2_media_authority.py`在登记写事务、字幕/流地址返回、下载派发/完成、worker返回、对象/图像登记及产物提交前注入配置/凭据变化，核对准确失败代码、停止后续调用、零媒体产物行和暂存清理；另覆盖来源重绑及正常三类作业。真实Bilibili下载器使用MockTransport，首个503后改变authority，确认重试派发前拒绝且零真实网络。三项真实数据库用例包括对象已发布后、实际完成事务排队前的修订变化，验证媒体产物不提交、旧作业failed且显式重绑/新作业可继续。已完成对象/图像不因测试失败而隐式删除。相关调用方重点选媒体服务、准入/暂存/产物、网关与生命周期；本批不重跑未改动的原生解码、ASR模型和协议专项。

`test_v2_media_cancel.py`用独立工作/清理屏障验证重复取消只发送一次、等待方取消向外传播且实际任务仍归属服务、四类终态不打断收尾、queued启动竞态继续取消running所有者，并保留跨Agent拒绝与无所有者时的running真值。三项真实数据库用例分别覆盖ASR/抽帧清理等待及queued取消写入前实际启动，核对原所有者、持久cancelled、暂存删除及数据库保留。测试不缩短生产超时，不以取消Task的请求替代实际收尾完成；调用方范围含准入、暂存、媒体、进程取消和生命周期，不扩展到未改动的模型或格式专项。

`test_v2_media_queue.py`验证两个处理槽位占满时取消三类排队作业并及时释放任务归属、24轮提交/取消不累积等待者、首次执行前取消、已提交cancelled的重试回收，以及普通/eager任务工厂下队列计数和槽位转交。取消写入结果延迟交付时，请求中断仍保留服务处理及关闭义务，交付后完成回收。三项真实数据库用例验证12轮取消不触发下载/暂存且显式新作业可完成、取消第三个作业不打断前两个运行作业、取消提交后请求中断仍完成队列回收。只覆盖排队与取消及其准入/暂存/媒体/生命周期调用方；默认全量、设备与模型闸门保持。

按所有者2026-11-16最新要求，持续开发阶段优先相关模块与调用方的`-n 0`专项，全量留到多个模块集成收尾；每批记录全量是否覆盖该源码，不能用旧全量通过替代本批验证。

`test_v2_media_frames.py`覆盖有/无时长元数据及有/无缩放的成功路径，核对正文、尺寸、时间戳、SHA256及逐图关闭；在解码、转换、缩放、画布分配、粘贴、标签、编码和文件写入处注入异常，确保原错误保留、已分配图像和缓冲区关闭。几何预算在下一次转换前拒绝，并保留8192边长、16000000像素和8MiB编码的精确边界；缩放原图须在下一帧转换前关闭。真实stdio子进程验证精确业务错误及同worker后续合法PNG，真实数据库验证failed、零帧产物、暂存清理和显式新作业。专项覆盖worker、ASR/音频、宿主产物校验、协议、HTTP与生命周期调用方，不据此声明原生内存或整项目验收完成。

`test_v2_media_anchors.py`覆盖取样时缺失/布尔/非数值、NaN/Infinity、负值、超时长与巨大整数时间戳，首个坏锚点即停止解码；两种元数据路径的生成验证在转换下一帧前拒绝、关闭已有图像且不创建文件。保留真实零秒、7200秒边界和最近帧选择。真实stdio与PyAV测试只在隔离worker中清除首请求的帧PTS，核对`media_frame_timestamp_unusable`及同进程后续合法PNG；真实数据库核对failed、零帧产物、暂存清理、显式新作业成功且旧终态不变。不接触真实视频、网络或用户进程。

投递刷新与归档交错由`test_v2_delivery_ui.py`控制真实列表响应的交付顺序，不伪造HTTP成功或业务数据。两轮复用原始投递场景的全部断言与超时，验证列表刷新不再让旧详情开启归档确认；另在390/1440视口覆盖刷新时清空详情/禁用实际控件、其他所有者请求归档后移除旧列表项，以及较早响应不能覆盖新的包含归档选择。`fieldset`检查真实`disabled`属性与内部控件，不以Playwright对fieldset本体的enabled判断代替完成屏障。每例仍有独立数据库和BrowserContext，模型与投递端口均隔离，默认不产生真实外发。

Python 网络/模型闸门仍在每个 worker 启用；子进程继承离线环境。并行不改变测试数量、断言、业务超时或真实设备验收边界。

Portal 的原生共享状态与应用授权审计分两步完成。`test_v2_portal_service.py` 的就绪帮助函数要求 active 且 authorized；`test_v2_portal_ui.py` 通过真实作用域 API 观察同一 grant 的授权完成，最多等待5秒，再点击显式查询按钮；原界面断言的超时、截图、像素及回收检查保持不变。新增受控审计夹具覆盖确认中、提交后刷新和失败回收；在应用异步边界暂停，不阻塞数据库线程、不伪造 HTTP 成功，也不要求生产界面增加轮询。每例仍独立数据库和 BrowserContext。

## 环境与 CI

全新开发环境使用 `uv sync --frozen`，安装浏览器用 `uv run --no-sync python -m playwright install chromium`。常驻服务正在使用 `.venv` 时不要重建环境或停止服务；测试使用 `uv run --no-sync`。

CI执行Ruff、格式检查、已收口领域Pyright、固定版本Prettier、ES模块语法、UI接口合同、重复定义、
任务摘要、索引检查、直接wheel/sdist→wheel及隔离导入校验，然后运行全部测试和离线评测。
前端开发环境需要Node 24和`npm ci --prefix web`。可用`scripts/ci.ps1 -Workers 0`排查串行行为，
默认`-Workers 4`。`-SkipSync`的含义不变，不能在用户服务运行中重建`.venv`。

## 耗时证据

`--durations` 区分 setup、call、teardown。需要保留报告时使用：

```powershell
uv run --no-sync pytest -q --junitxml=.pytest_cache/test-speed/full.xml
```

前后对比必须选择相同用例范围、机器与环境，并标明串行或 worker 数；历史报告的结果不能冒充当前源码的新实测。专项提速也不能按比例推算整个套件。

2026-10-31 同机实测：相同的 55 项 UI/上下文测试，优化前串行 **139.39 秒**，优化后 4 worker + 浏览器复用 **55.31 秒**，本次观察到约 **2.52 倍**提速、耗时减少 **60.32%**。这只是单次测量。

本轮并行全量共 1411 项，**1408 通过、3 失败，207.06 秒**。运行期间发生其他 UI 源码修改，不能作为固定源码快照全绿证据。3 个失败串行复核后，2 个资源版本检查已恢复，1 个 UI 契约检查仍因 `memory:resident-add` 未注册失败；串行同样复现。详细范围与原始报告摘要见 [提速验证记录](evidence/test-speed-2026-10-31.json)。

2026-11-04 的单次串行冷进程观察：相同 node `agent/tests/test_v2_reference_release_contract.py::test_valid_plan_has_canonical_fingerprint_is_immutable_and_allows_shared_objects` 的命令报告从 **3.82 秒降到 0.38 秒**。改动仅将 `llm/usage.py` 的 LiteLLM 导入推迟到实际发现 SDK 价格时，配置价格及纯领域测试不再为未使用的 SDK 支付导入成本；离线价格表、未知费用与故障语义保持。相关定价、Gateway、检索和隔离闸门专项 **84 项通过**，不等于全量通过。这不是重复采样的基准，也不能推算全仓提速比例；开发专项仍用 `-n 0`，完整任务收尾仍按前述纪律运行一次全量。

## V2 历史游标与后续审计保留

R3.retention_audit.cursors 的专项分为领域游标校验、持久分配/清理修订、迁移/恢复、HTTP调用方及工作台。浏览器专项优先使用 `ui_browser`，每例独立 BrowserContext 和数据库；最小新增用例覆盖两类审计分页/410清空重载、未知种类通用展示，以及跨Agent迟到响应不恢复旧视图。UI故障注入前先检查真实新API响应合同，不能用模拟分页掩盖尚未接线的服务端。

开发中按改动范围选择 `test_v2_history_contract.py`、历史仓储/迁移测试及 `test_v2_history_ui.py`，使用 `-n 0`；跨模块变动还须覆盖普通事件/审计、凭据实例审计、维护直接SQL和恢复调用方。删除尾部/全部行后仍保持分配高水位，中间缺口使原游标明确失效；事务回滚同时撤销删除和清理修订。HTTP覆盖错作用域/筛选、未来位置、旧after拒绝及恢复epoch变化，不跳过断言或缩短被测超时。

本批游标基础的测试不证明180天候选、计划、备份执行与取消已经实现，也不证明三平台运行或72小时验收。上述清理门槛仍按[审计保留设计](V2_AUDIT_RETENTION_DESIGN.md)独立推进；专项结果和最终固定源码全量结果分别记录，不互相替代。

R3.retention_audit.preview 新增领域、仓储保护、HTTP/CLI 与 UI 专项（`test_v2_audit_retention*.py`）。仓储用一次真实关闭后的裁剪模板复制到各例独立目录，避免重复创建同样的验证备份；每例仍独占数据库与锁，绝不复制活跃数据库。SQL 观察断言预览不读取审计/动作/事件正文，页内相同运行只判定一次。覆盖完整/部分/取消批次、作者 FK、遗忘后的实际关系释放、晚到费用/审批/租约/来源、人工核查后的 unknown 投递、固定时间/上界、180 天微秒边界与410。共享保护抽取需连同正文/引用/历史调用方验证。

按本轮开发优先要求，阶段内只运行受影响专项；当前源码全仓复验继续待合并执行，不把专项或旧版全量写作当前全绿，不提交/发布候选版。

R3.retention_audit.apply 先完成生产计划、DDL/仓储、离线命令、恢复和运维接线，再集中验证。计划、迁移、执行和恢复专项为 `test_v2_audit_plan_contract.py`、`test_v2_audit_plan_migration.py`、`test_v2_audit_pruning.py`、`test_v2_audit_pruning_crash.py`、`test_v2_audit_recovery.py`；运行均用 `-n 0`。真实子进程退出覆盖删行审计前/后、下一目标开始和完成前，不能用抛异常代替崩溃证据。迁移断点覆盖新表、索引、最后触发器并验证DDL与原数据完整回滚。

共享 `offline_plans` 新增重复JSON字段拒绝；调用方验证仍含原正文/引用维护、历史游标、默认诊断、schema升级和恢复。每份报告标明范围；后续当前源码全仓复验仍是独立门槛。交互平台、真实账号及72小时不能由本地故障注入代替。

R3.retention_audit.events 在生产接线完成后集中运行 `test_v2_event_retention_contract.py`、`test_v2_event_pruning.py`、`test_v2_event_retention_http.py`、`test_v2_event_plan_migration.py`、`test_v2_event_pruning_crash.py` 与工作台专项。共同执行核心抽取必须覆盖既有审计、正文、引用、历史与诊断调用方，不能只跑新事件用例。验证空事件表仍按原last_seq继续分配、只使对应Agent的events游标过期、恢复保留deleted证据且撤销pending、原引用计划过期及两类维护互相保护。

迁移约束对照需同时收集 table.constraints 和每列 column.constraints 中的 CHECK，不能漏掉 ident() 的列级UUID长度约束；对实际约束仍作完整集合相等比较。失败原报告与指定nodeid修正报告分别保留，不以重跑专项宣称全仓通过。

Windows前台输入离线回归为test_v2_foreground.py、test_v2_foreground_transport.py、test_v2_foreground_worker.py和test_v2_foreground_ui.py；worker使用真实固定帧/UIA与注入输入接口，不发送系统键鼠事件。真实验收仅通过agent/scripts/accept_foreground.py显式运行，要求所有者配合，保存至全新证据目录；本次用户已要求稍后执行，不把未运行写成通过。

macOS宿主与协议离线回归为test_v2_macos_helper.py；包链检查包含全部Swift/C/H字节，部署捕获及启动指纹同样包含原生源码。Swift编译、AX/ScreenCaptureKit真实行为和输入暂停需按V2_MACOS_HANDOFF.md在Mac mini执行；Windows上的注入测试和通用wheel不证明原生macOS已支持。

D2.macos.control 的Python离线范围包含test_v2_foreground.py、test_v2_foreground_transport.py、test_v2_macos_helper.py及test_v2_macos_acceptance.py。共用宿主测试同时注入Windows/Mac身份，覆盖二次放行、撤权、错误绑定、CRLF、提前成功及错误后置证明；Mac验收脚本测试使用注入凭据和管道，不调用系统Keychain。源码索引变动同时覆盖项目API调用方。

macos/Core中9个XCTest只测试端口编排，不加载AppKit或发送系统输入；Mac构建脚本和独立CI任务负责实际编译执行。当前Windows环境未执行这些Swift用例。accept_macos_desktop.py的--foreground-owned-fixture及accept_macos_keychain.py的--write-owned-item均为显式实机验收，默认pytest不触发。未知凭据写入不重试，清理标识在执行前保存；已有条目不覆盖或删除，回传错误使用固定代码，不回传原始诊断正文。
