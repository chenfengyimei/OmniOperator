# OmniOperator V2：AI 接续开发完整交接

交接日期：2026-11-10（用户时区 Asia/Shanghai）。本文是交接时点的快照；后续开发以 `docs/tasks/v2.json`、当前源码及相应证据共同核验。不要根据文件修改时间推断功能已经完成。

## 1. 接手时先明确的结论

- **整个 V2 未完成，默认 `omni`、`python -m omni`、`omni-web` 等正式入口仍为 V1。** V2 使用独立开发入口，不能提前切换或删除 V1。
- 当前开发断点为 **D6.mcp：MCP stdio/Streamable HTTP/OAuth 接入**，不是重新开发媒体、对象存储、人物、摘要、调度或文本技能。
- 最新完成的是 D6 媒体/视频理解的选定生产链路，数据库 **129 张业务表 / `v2_0036`**，前端资源标识 **`20261110v48`**。资源标识是版本串，不能仅因日期不同而改写。
- 最新选定范围 **133 个不同测试节点的最新结果通过**，其中有 75 例浏览器测试（媒体面板 2 例跨 390/1024 视口，工作台全量回归 73 例）；13 份受影响生产 Python 文件通过默认级别 Pyright，Ruff 与相关排版检查通过。**这不是全量测试、全仓严格类型或三平台验收通过。**
- 按用户要求，媒体批次及其前累计的 V2 成果已提交并推送 gitee；接手时以 `git log`/`git status` 对账，仍不要 reset/clean 或删除未跟踪文件。
- Windows 的部分隔离原生样例已执行；前台真实键鼠验收被用户明确安排为“稍后”。macOS 源码待 Mac mini 编译及验收；Linux 实机环境尚未提供。不要把 Windows 注入测试称为 Mac/Linux 支持证据。
- 用户要求 **开发优先，先打通完整生产链路，再集中验证相关模块与调用方；不要频繁全量测试，不要每次只推进文档或一个很小的断点。每完成一个节点，git 提交推送一次。**

## 2. 用户已确认的产品方向与授权边界

目标是单实例、单所有者、多个 Agent 空间的个人智能体，可部署于 Windows、macOS、Linux 桌面和无桌面服务器。核心是模块化单体、一个主进程、单一 SQLite 应用库、受控文件对象和受管能力子进程。当前不扩展多人共享、跨机器设备节点或分布式集群。

V2 不需要兼容 V1 的数据库、配置、HTTP、工具名或导入路径；旧数据不迁入新模型，也不双写。这个授权允许重构，并不允许丢弃当前未提交成果、私人数据或尚未退役的生产入口。

沿用现有视觉风格与原生 ES 模块。功能、配置、权限和状态各有单一真值，禁止通过复制一份状态、旁路授权或根据中文结果字符串推断成功来“接通”。

已经授权的普通代码开发、可逆修改、离线验证和文档更新可继续进行，不要反复索取许可。真实支付、外发消息、发布、提权、安装系统服务、设备输入与公开部署，仍需对应具体方案及既有明确授权；用户此前已延后前台实测，不要继续弹测试窗口要求配合。

旧内容可在确认退役后归档到 `temp/archive`；私人运行数据及恢复材料归 `temp/private`，可再生构建/测试产物归 `temp/build`。不能仅因文件位于 temp 就删除；活跃 SQLite/WAL/SHM 不单独搬动。

## 3. 必读顺序与文档权威

1. 仓库根 `AGENTS.md`、`docs/HANDOFF.md`、`docs/ARCHITECTURE.md`、`CONTRIBUTING.md`。
2. 本文、`docs/tasks/v2.json`、`docs/DEVELOPMENT_STATUS.md`、`docs/V2_REFACTOR.md` **历史实施记录标题之前**的当前摘要。
3. 最新证据 `docs/evidence/v2-media.json`；需要修改的能力再读该能力合同及专项报告。
4. 数据规则 `docs/DATA_MODEL.md`，界面规则 `docs/UI_DESIGN_SYSTEM.md`，测试规则 `docs/TESTING.md`。
5. 下一批重点读 `docs/V2_MEDIA.md`、`docs/V2_BILIBILI.md`、`docs/V2_SKILLS.md`、`docs/MCP.md`（如有）及相应应用服务与端口。

`ARCHITECTURE.md` 是架构职责权威；任务状态以 `tasks/v2.json` 为入口，`DEVELOPMENT_STATUS.md` 由脚本生成。领域合同解释具体行为，原始报告只证明所属源码版本。

**文档存在历史残留：** `CONTRIBUTING.md` 的 Windows 定位、旧分层及兼容要求主要描述 V1；`deploy/v2/README.md` 某些前段仍有“不截图”“尚无本地向量”等早期限制；`V2_REFACTOR.md` 历史段落保留旧“下一断点”。遇到冲突要核对最新台账、领域合同及源码，不按历史句子重新开发，也不批量改写历史证据。

## 4. 工作区与可恢复基线

| 项目 | 本次核验结果 |
| --- | --- |
| 工作区 | `D:\xm\OmniOperator`，PowerShell |
| 当前分支 | `codex/architecture-v2` |
| Git HEAD | 媒体批次提交（接手时以 `git log --oneline -5` 对账；台账 `checkpoint_commit` 记录该批代码提交哈希） |
| 最初重构基线提交 | `076c0c22fa8ecb90d3ce9a64562c90a23a129e00` |
| 工作区状态 | 媒体批及其前累计 V2 成果已按用户要求提交推送；接手时以 `git status` 对账，仍不 reset/clean |
| 最新固定源码指纹 | `b505d4b2a9a251c57cf357cbdc0b3731ff0e109165e15f59e001f71490d51b21` |
| 冻结文件 | 1165 份，见 `temp/build/v2-media-validation-snapshot.json` |
| 最近恢复材料 | `temp/private/v2-media-source-20261110-b505d4b2a9a2/manifest.json` |
| 源码备份 | 同目录 `source-and-evidence.zip`：1435 份源码/文档、7 份验证材料 |
| 备份 ZIP SHA256 | `fe045494ff872c22c0e52e978ab6a147a8d324f572f71d451add185a055129cd` |

本次媒体批封存时，恢复 manifest 为 complete 且 ZIP SHA 匹配。**complete 仅表示可恢复，不表示 V2 完成。** 上一技能批次的 corrected 恢复材料（`temp/private/v2-skills-source-20261110-corrected-712a15f2b732`）与未修正版（`...-58cccddafd86`）保留为溯源材料，不要删除。

不要 `git reset --hard`、`git clean`、批量覆盖、批量暂存或重写历史。不要把所有未跟踪文件当临时垃圾；构建/验证产物在 `temp/build`、私人材料在 `temp/private`，均不入库。

## 5. 目录与主要接线位置

| 路径 | 用途 |
| --- | --- |
| `agent/src/omni/domain/` | V2 纯领域模型、状态机、约束、协议数据 |
| `agent/src/omni/application/` | V2 用例、编排与端口；`ports.py` 为主要依赖合同 |
| `agent/src/omni/adapters/` | V2 HTTP、模型、数据库、对象、平台、原生及扩展适配器 |
| `agent/src/omni/composition/` | V2 组装、独立 CLI、HTTP 及生命周期 |
| `agent/src/omni/adapters/persistence/` | SQLAlchemy、仓储、维护和 Alembic 迁移 |
| `agent/src/omni/adapters/workers/` | 受管辅助进程；macOS Swift/C 源码也在此树下 |
| `agent/src/omni/adapters/platform/` | 路径、实例锁、权限与进程管理 |
| `web/ui/v2/` | V2 工作台原生 ES 模块 |
| `agent/tests/test_v2_*.py` | V2 回归与架构守卫 |
| `deploy/v2/` | 开发部署、容器与平台模板，存在不等于已验收 |
| `docs/evidence/` | 各批次版本、范围、失败与修复证据 |
| `docs/tasks/v2.json` | 当前任务真值及下一断点 |
| `temp/build/` | 原始专项报告、指纹、验证脚本与构建产物 |

根目录 `src`、`tests` 是本机兼容联接，不要从它们再创建一套源码。V1 `core/`、`tools/`、`llm/`、`memory/`、`channels/` 与 V2 并存，只可参考，V2 不能 import V1 Runtime、旧业务库或原生脚本。

本次静态计数四层 Python 文件为 domain 50、application 43、adapters 200、composition 13。该计数包括各层基础设施文件，不能当完成功能比例。

## 6. 必须保持的架构与数据合同

- 入口适配器 → 应用服务 → 领域与端口；基础设施实现端口；构造及生命周期集中于 composition。领域无网络、文件、数据库、平台 API 或全局运行对象。
- 一个主事件循环、一个实例主进程、一个 Web worker；实例目录由独占运行锁保护。维护命令必须取得相应独占锁，不能为验证停止用户正在使用的服务。
- 默认最多 4 个活跃回合、64 个排队回合；同会话顺序。后台任务、线程执行、原生/插件子进程、队列及 IPC 有界，并具有实际所有者、取消与关闭状态。
- V2 真值为 `<home>/data/omni.db`，文件对象在 `<home>/objects`，日志在 `<home>/logs`。显式 `--home` 优先，其次 `OMNI_HOME`，否则平台标准 V2 目录。工作目录单独配置，不默认为源码目录。
- SQLAlchemy 连接、事务与 Alembic 由持久适配器管理；统一短事务写入执行器，网络、模型、人工等待和原生调用不进入数据库事务。不要在仓储或业务模块另行连接、建表。
- UUID、UTC、Agent 作用域组合约束、编辑 revision、事件 sequence、稳定游标、分页上限必须在服务端与数据库维持；JSON 不代替可筛选/关联/唯一的结构化字段。
- 入站去重/消息/运行/工作项原子入队；动作先登记意图及审批再派发；输出消息/终态/投递/必要审计共同提交；费用按调用身份一次结算。
- **unknown 不等于失败，不能自动重发、重复输入或释放未核清费用。** 实际进程/线程/连接/文件未回收时保留归属，不能只取消 await 就报告已关闭。
- 对象先完成临时写入、校验与发布，再建立引用；引用/固定保护 GC，备份使用一致快照及完整引用对象，恢复进入新目录。
- 删除传播必须覆盖摘要、检索、派生记忆、模型来源和对象引用；FTS/向量是可重建索引，不是另一份正文真值。
- 审计、业务与诊断分开。结构化诊断默认不含聊天原文、完整工具参数/结果、Cookie、凭据、原生 handle 或异常局部变量；关联编号串联调用。
- HTTP 收口 `/api/v1`；工具使用结构化状态、错误码、副作用与验证结果；界面显示实际能力、版本差异、未知及降级原因。
- 凭据只通过引用与专用存储。无桌面加密库主密钥由外部文件提供，不和密文一同自动生成保存。插件进程隔离是故障隔离，不能宣称操作系统沙箱。

## 7. 八个交付包的真实状态

这里的 D1–D8 是交付包；原计划 R0–R7 是架构实施阶段，编号不可直接互换。父阶段未收口不会因为某个子批次通过而自动完成。

| 包 | 开发与离线状态 | 仍须完成 |
| --- | --- | --- |
| D1 Windows 原生 | 选定 UIA、截图、语义动作、显式接管开发及离线完成；有隔离原生样例 | 用户延后的前台键鼠、完整权限/焦点/睡眠/恢复和实际平台验收 |
| D2 macOS | 固定 helper、AX/截图/输入暂停、PTY/Keychain、构建交接及 Python 接线源码已有；仍开发中 | Mac mini 编译 Swift/C、执行原生测试、签名/权限/输入及运行反馈 |
| D3 Linux | logind/总线、AT-SPI/X11 读取、截图、Portal 单帧和部分语义动作已有源码/注入验证 | 持续前台控制、Wayland RemoteDesktop 身份/暂停证明、PTY/凭据实测与桌面验收 |
| D4 平台运维 | 终端核查、注册/控制、不可变 release、服务交换/隔离救援、旧注册清理、分阶段管理员和跨 schema 源码及专项已有 | 真正管理器、root/账号/文件/ABI/崩溃恢复、三平台长期运行与剩余缺陷 |
| D5 业务通道 | 选定 QQ 文本、B站基础读取/发布、安卓所有者/模型范围/Unicode 恢复开发及离线完成 | 真实账号和设备验收；附件、视频等在 D6，不能据此称所有业务迁移完成 |
| D6 模型与扩展 | 流式、低清图片、ONNX 文档、本地记忆语义、档位、文本技能和媒体的选定链路完成 | **MCP stdio/HTTP/OAuth → 插件回调/产物/市场完整流程**，以及真实质量 |
| D7 产品切换 | 待开始 | 令牌管理、统一产品工作台/能力、全仓严格类型、三平台包/容器/安装恢复，达到门槛后切换正式入口及归档 V1 |
| D8 完整验收 | 待开始 | 固定构建全量、浏览器、故障/负载、真实功能及每平台 72 小时 |

本次不重新给出百分比。不能根据 35 个历史里程碑或测试数量推断整个产品已经接近完成。

## 8. 已有能力、边界与应复用的位置

| 能力 | 已有实现与边界 | 接手入口 |
| --- | --- | --- |
| 核心数据/运行 | 单库、审批、费用、任务、投递、对象、人物、常驻、摘要/删除、配置与来源文档 | application 各用例、persistence 仓储；`DATA_MODEL.md` |
| 日志/维护 | 结构化日志、关联、脱敏、诊断、指标、保留预览/计划/备份/执行/取消/恢复 | `adapters/diagnostics.py`、维护合同与既有证据；不因交接再扩展清理系统 |
| QQ | 独立人物/会话/人设、明确@群聊、重连、冷却、共享审批投递；仅文本，不注入所有者私人记忆及工具 | `V2_QQ.md`、`application/qq.py` |
| B站 | 基础读取、评论/回复/私信、真实 mention、一次放行与新增记录核验，共用 outbox | `V2_BILIBILI.md`、`application/bilibili.py` |
| 安卓 | 明确设备及运行范围、观察/截图、审批输入、unknown 核查、Unicode IME 与恢复义务；APK 编译不等于安装验收 | `V2_ANDROID*.md`、`application/android.py` |
| 模型流式 | 本地假 SSE 经真实锁定 SDK 验证，完整结束/用量/工具才结算或派发；预览不保存第二份消息 | `V2_MODEL_STREAMING.md`、`application/model_calls.py` |
| 图片 | 原子入队、受控对象、低清实际 SDK 载荷与预算；累计附图次数独立于当前上下文/压缩 | `V2_IMAGES.md`、`application/images.py`、`adapters/images.py` |
| 本地向量 | 可选离线 ONNX CPU worker、模型指纹/前缀/分窗、文档及记忆混合召回；实际合成图通过，未下载预训练模型验证质量 | `V2_LOCAL_VECTORS.md`、`V2_MEMORY_SEMANTICS.md` |
| 记忆整理 | 事实/档案/常驻共用条目与来源；相关事实仅明确人工 related 关系，不按相似度自动合并/遗忘/抑制 | `application/memory_semantics.py` |
| 档位 | 入队捕获模型/预留/策略、工具交集、一次固定模型升档；升档不换模型、不代替审批 | `V2_ROUTING.md`、`domain/routing.py` |
| Python 扩展 | 可信目录封存、显式授权、进程外工具；首批所有工具按写处理；未完成宿主回调/文件产物/市场 | deploy 文档扩展节、`adapters/extensions/`、扩展用例 |
| 文本技能 | 文本包导入/导出、修订/参考文件、审阅启用、回合封存/披露、审批编辑与撤权恢复 | `V2_SKILLS.md`、`application/skills.py` |
| 媒体/视频理解 | B站来源登记（配置修订绑定）、在线字幕/受管 ASR/等距抽帧、数据库触发器状态机、时间锚点转写对象与只读模型工具、取消/恢复/删除传播、工作台媒体分区 | `V2_MEDIA.md`、`application/media.py`、`adapters/media.py`、`adapters/workers/media.py` |

## 9. 最近一批：媒体/视频理解的关键事实与易错点

主要新增文件：`domain/media.py`、`application/media.py`、`adapters/media.py`、`adapters/workers/media.py`、`adapters/persistence/media_repository.py`、`migrations/versions/v0036.py`、`web/ui/v2/media.js`，及 `test_v2_media.py`、`test_v2_media_ui.py`；接线改动 `adapters/bilibili.py`（subtitle/streams 读取与有界下载）、`adapters/http.py`、`composition/instance.py`、`composition/http.py`、`application/ports.py`、`schema.py`、`repository.py`。

生产路径：B站视频经既有配置/凭据/修订授权登记为 `media_sources`（唯一键 agent+bvid，身份列不可变，绑定登记时的配置修订）→ 作业 `subtitle/transcribe/frames`（数据库触发器状态机：准入 ≤8 活跃/来源、转移白名单、终态不可变、done 必须带产物、终态必须带 finished_at）→ 在线字幕或受管子进程 ASR/抽帧 → 转写对象带 `[MM:SS-MM:SS]` 锚点（字幕无可靠时间轴，不伪造锚点）、帧拼图复用既有对象+图片校验链 → 模型只经 `media_list`/`media_transcript` 只读工具读实际落库文本。

本批要点与踩坑：

- **模型读的是 `ActionResult.text`**：工具头行带 transcript_id/kind/language/range，正文为锚点窗口；缺失行如实报错，不发明文本。
- 作业行没有嵌套来源：`media_jobs` 行只带 source_id 坐标，`_execute` 在 `media_job_start` **之后**显式读来源行，保证失败落在合法转移上（此前 `job["source"]` 直接 KeyError、作业卡死 queued——测试抓出后修复）。
- ASR 分支必须显式把 worker 写出的 transcript.json 作为发布载荷（`body` 此前未赋值，NameError 归类 unknown——测试抓出后修复）。
- 终态幂等是刻意设计：取消与完成竞态时后到方观察到已完成状态并返回，不报错。
- 排队作业只能经取消离开；失败/unknown 只描述 running 工作。超时/崩溃类错误归 unknown，业务错误归 failed。
- 配置修订后来源被搁浅（`bilibili_configuration_revoked`），重新登记即重绑新修订——不是缺陷，是绑定语义。
- 分页游标与比较键必须同为 `id`（此前 created_at 比较配 id 游标，翻页必空——对齐 B 站仓储样板后修复）。
- 工作台刷新不得无条件清空当前选择；所选项消失才清（否则登记后详情全 undefined）。
- 浏览器测试等作业出现要轮询服务端真值，状态文本在两次提交间相同，不能用 UI 文本当同步点。
- WBI 签名（纯算法）在领域层，`wts` 由调用方注入；worker 懒加载重型库，缺组件返回 `media_component_missing`，ASR 只用离线缓存。

## 10. 下一批 D6.mcp 的可执行开发合同

以下是**接续开发要求，不是声称已经存在的接口**。数据层已有 MCP 配置表；运行适配器（stdio/HTTP/OAuth/指纹/撤权）尚未完成，先按源码核清已有部分再实现。

### 10.1 先做有界对账，随后立即实现

核清 V1 的 `tools/mcp/`（客户端、发现、授权、漂移撤权与子进程生命周期）及 MCP 配置表、概念文档；V2 只参考不 import。对照 V2 的受管进程（OwnedProcess/MediaProcessor 先例）、凭据引用（Credentials/CredentialService）、审批与费用合同，列出 stdio/HTTP/OAuth 各自的接入位置，然后写代码。

### 10.2 传输与外部进程

- stdio 子进程沿 OwnedProcess 合同：有界、可取消、崩溃即销毁、下一调用重拉；in-flight 调用随进程销毁归类 unknown，不自动重放写操作。
- Streamable HTTP 客户端有界超时、显式取消与连接复用；断连/超时区分失败与 unknown。
- OAuth 走显式授权流，凭据只入专用存储并按引用使用；模型与工具结果永不含凭据本体；授权失败/过期如实上报，不静默降级。

### 10.3 目录、指纹与授权

- 工具目录按服务显式发现；name+inputSchema 指纹（不含 description）变化触发撤权与所有者显式重新确认；未授权工具对模型不可见。
- 模型可见文本保留来源服务与资源标识；MCP 工具副作用沿统一审批链，不旁路。
- 配置、授权、漂移、unknown 与重连全部进工作台分区与审计；服务启停显式，不自动后台重连风暴。

### 10.4 退出条件

至少形成“显式配置 → 受管 stdio/HTTP 连接 → 工具发现与授权 → 模型经工具实际调用 → 指纹漂移撤权 → 工作台可见”的端到端生产路径；离线假服务覆盖超时/取消/崩溃/unknown/撤权/跨 Agent。真实外部 MCP 服务器与真实 OAuth 供应商另行验收；仅有配置表不等于完成。

## 11. 后续固定顺序与不能遗失的欠账

1. D6.mcp 收口：stdio、Streamable HTTP、显式 OAuth、凭据引用、目录指纹、授权漂移撤销、超时/取消、unknown 与工作台。仅有 MCP 配置表不等于运行适配器完成。
2. 完整扩展链：进程外宿主回调、产物协议、版本/授权、市场更新提示与实际安装流程，复用对象/审批/费用；不能把文本技能包等同代码插件或市场。
3. 收口 D2/D3/D4 的源码欠账及各平台反馈：尤其 Linux 持续控制与 Wayland 证明、Mac 编译/权限/原生 ABI/文件保护、真实系统管理器与恢复。没有环境时记录“待真实验收”，继续其他已授权开发。
4. D7 产品门槛：所有者令牌管理、工作台统一与能力报告、业务清单、全仓严格类型、打包/容器/安装更新恢复。只在明确门槛满足后切换 `omni` 默认入口，移除生产对 V1 的引用并可恢复归档。
5. D8 固定构建验收：全量、四视口双主题、故障注入、参考负载、真实模型/QQ/B站/安卓/远程 MCP，以及每平台 72 小时。

这些是固定交付单元，不再无限扩展已经具备能力的清理/维护基础。发现真实缺陷或新接入必须修改时，限定影响范围并保留证据。

## 12. 最新验证、已知问题及证据口径

最新 `docs/evidence/v2-skills.json` 汇总八份原始报告的**同节点最新结果**，共 78 个不同节点全部通过；不是八份报告数量简单相加。最初 62 项报告有 10 项失败，原报告保留，后续针对性修复/调用方/模型闭环复核记录也保留。

16 例浏览器为技能和调用方流式界面，各覆盖 390/760/1024/1440px、深浅主题。16 份默认类型检查不等于全仓严格类型。当前代码没有全量通过、主包重建、远程 CI、Mac/Linux、真实账号设备、负载或 72 小时通过结论。

开放问题 `UI.exact_organization_refresh_timing`：旧精确整理 UI 曾一次刷新显示 queued；同源码单节点独立复核通过，**尚未认定修复**。原报告在 `temp/build/v2-memory-semantics-final.xml` 和 `...-repair.xml`，见台账 `open_validation_findings`。不要删掉该项或用一次重跑通过替代根因结论。

较早的全量 2606 通过及后续 2659 通过/1 失败都只属于当时源码，不能用于证明当前全仓全绿。旧批次“未实现”的限制也不自动变为当前待办。

## 13. 开发与验证执行方法

优先完成同一生产链路的关联开发。开发中只跑受影响模块和直接调用方，少量测试使用单进程：

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/test_<相关模块>.py
uv run --no-sync pytest -q -n 0 agent/tests/test_<相关模块>.py::test_<失败或新增行为>
# 只使用缓存中的失败，不因缓存为空启动全仓：
uv run --no-sync pytest -q -n 0 --lf --last-failed-no-failures=none
```

不要为了提速跳过断言、缩短被测超时、去掉网络闸门或使用用户真实数据。UI 同文件优先共享 `ui_browser` 进程，每个用例独立 BrowserContext 和临时数据库；重型 Pyright、浏览器、打包顺序执行。

完整任务收尾/提交前按仓库要求执行一次 `uv run --no-sync pytest -q`，同一固定版本可复用有效结果；不要在本次纯文档交接或每次状态更新后跑全量，不并发多个全量。当前用户偏好开发优先，不代表可以虚报尚未运行的门禁。

新增/删除文件后更新并核查索引；台账变更后生成摘要：

```powershell
uv run --no-sync python scripts/project_index.py
uv run --no-sync python scripts/project_index.py --check
uv run --no-sync python scripts/task_status.py
uv run --no-sync python scripts/task_status.py --check
```

锁定依赖内的 Ruff 与格式检查按改动文件运行；前端沿 `web/package-lock.json` 的现有 Prettier，避免重建用户环境。既有验证脚本可参考 `temp/build/validate_v2_skills.py`、`revalidate_v2_skills.py`；**seal 脚本是特定批次的一次性状态变更工具，不能直接重跑**，否则可能覆盖证据或重复修改记录。

## 14. V2 的隔离运行与平台交接

开发入口为 `python -m omni.composition.cli`，不要用 `omni` 来证明 V2 已运行。Windows 手动示例：

```powershell
# 仅供需要启动隔离实例时使用；这不是每个新会话必须执行的动作。
$v2DevHome = Join-Path $env:TEMP ("omni-v2-dev-" + [guid]::NewGuid().ToString("N"))
uv run --no-sync python -m omni.composition.cli --home $v2DevHome init
uv run --no-sync python -m omni.composition.cli --home $v2DevHome serve
```

默认访问 `http://127.0.0.1:8766`。端口已占用时先看 `serve --help` 选择隔离端口，不停止已有服务。`auth` 是同前缀的所有者本机命令；令牌只能给所有者在本机界面使用，不写入 AI 工具输出、交接、URL 或日志。未配置真实模型时不要为验证擅自填写真实凭据或发付费请求。

新机器/新 checkout 才按锁文件准备独立环境；当前工作区用 `--no-sync`，不执行 `uv sync`。工作目录、数据库和运行文件都显式隔离。

macOS 请先看 `docs/V2_MACOS_HANDOFF.md`，系统服务另看 `V2_MACOS_SYSTEM_SERVICES.md` 与 `V2_ADMIN_HANDOVER.md`；向 Mac mini 交接源码、锁文件、构建/签名/权限及证据要求，返回原始编译与原生测试结果。固定 helper Bundle ID 为 `org.omnioperator.desktophelper`，实际安装/签名仍须按合同核验。

Linux 看 `docs/V2_LINUX_HANDOFF.md`；无桌面容器只能证明核心部署，不替代 X11、AT-SPI 或 Wayland 交互验证。无法证明身份/暂停条件时不开放相应前台动作。

## 15. 换会话、换机器时如何交付材料

- **同一机器且 AI 能访问工作区：**发送配套 `docs/V2_AI_CONTINUATION_PROMPT.md` 全文，并要求先读本文和当前台账。继续使用当前工作树，保留全部在途修改。
- **只有聊天附件能力：**至少提供本文、提示词、台账和最新证据；要实际开发还必须提供源码，AI 无法仅靠这些说明重建未提交成果。
- **换机器或新的 checkout：**在全新目录准备记录的 Git HEAD，再核验并叠加 corrected 源码包的 `source/` 内容；31 份报告在 `validation/`，其路径语义对应原 `temp/build/`。恢复只针对新目录，不能覆盖有他人修改的工作区。
- 同时提供原 manifest。按 manifest 校验 ZIP SHA、每个成员指纹和路径；仅“能解压”不足以证明恢复成功。1400 份源码快照不包含当前新交接文档，需额外复制本文和提示词。
- 源码恢复包不包含运行数据库正文、凭据、主密钥、浏览器登录态或已安装系统服务。源码包含项目实现，应仅交给获授权的开发环境，不自动上传公开仓库。

如提供本次专用交接 ZIP，优先阅读 ZIP 根 `START_HERE.md`，它包含本文/提示词的新版本与原封存源码/manifest，按其清单校验。专用包不改变原技能批次证据指纹。

## 16. 下一会话的交付要求与最终完成门槛

接手后先给出简短事实核对，随后实际开发 D6.media。一个会话的收尾需要：说明完成的生产路径、修改文件、真实限制；记录相关测试/类型/排版的实际结果与原始报告；保持开放问题；更新当前台账、生成摘要、HANDOFF 当前段、领域合同与索引；为该固定源码保留可恢复副本。

开发完成、离线验证、真实平台验收分别记录。文档或提示词更新不产生“新业务验证通过”，也不应改写旧封存指纹。若当前源码和这里的版本不一致，先对账，不自动覆盖为旧快照。

**整个 V2 的完成条件：**既定业务迁移与扩展开发闭环、正式入口切换、V1 生产引用退役、三平台能力如实可用、安装/包/容器/更新/恢复可复现、当前固定构建必要测试通过，以及对应真实账号/设备/三平台各 72 小时证据。缺环境、设备或授权应准确登记，不能用“模拟通过”替代，也不要因为这些验收暂缺就停止其他可完成开发。
