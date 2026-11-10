# OmniOperator V2：AI 接续开发完整交接

交接日期：2026-11-10（Asia/Shanghai）。本文是交接时点的快照；后续以 `docs/tasks/v2.json`、当前源码和 `docs/evidence/` 共同核验。不要用文件修改时间推断功能完成度。

## 1. 接手时先明确的结论

- **整个 V2 未完成；`omni`、`python -m omni`、`omni-web` 等正式入口仍是 V1。** V2 用独立开发入口 `python -m omni.composition.cli --home <新目录> serve`，默认 `127.0.0.1:8766`。不要提前切换或删除 V1。
- 当前断点是 **D6.extensions：完整扩展链（进程外宿主回调、产物协议、版本/授权、市场更新提示与实际安装）**。D6.mcp 的离线生产链已收口，不要重做 MCP 传输/授权/工作台，也不要重做媒体、对象存储、人物、摘要、调度、文本技能或扩展包校验。
- 最新完成批次是 **D6.mcp（MCP 运行适配器）**：数据库 **136 张业务表 / `v2_0037`**，前端资源标识 **`20261110v49`**，静态索引 **65 模块 / 715 文件 / 427 路由 / 94 声明工具**。合同见 `docs/V2_MCP.md`，证据见 `docs/evidence/v2-mcp.json`。
- 该批 **150 个不同测试节点的最新结果通过**，其中 **2 例浏览器**（MCP 工作台跨 390/1024 视口）；同源码另跑一次全量：**4541 passed / 16 failed / 10 errors**，26 个失败/错误节点均为基线 `be58739` 遗留（history/event-pruning、schema-release 固定旧 schema、service 启动效果、extensions UI 假 hosts 等），本批另修复其中 3 个（媒体 worker 动态导入与两处迁移时间线）。**不是全量通过、不是全仓严格类型、不是三平台验收。**
- MCP 批及此前累计的 V2 成果已提交并推送：`8ad22cf`（数据层/传输/宿主/服务）、`bf5f91f`（显式 OAuth）、`564f0db`（工作台）、`8590144`+`320549f`（基线守卫修复与测试清理）；分支 `codex/architecture-v2` 已设上游 `origin/codex/architecture-v2`；`master` 未变。
- Windows 的隔离原生样例已执行；前台真实键鼠验收被用户明确安排为“稍后”。macOS 待 Mac mini 编译，Linux 无实机。不要把 Windows 注入测试当作 Mac/Linux 证据。
- 用户要求：**开发优先，打通完整生产链后再集中验证受影响模块与调用方；不要频繁全量；每完成一个节点 git 提交并推送一次。**

## 2. 产品方向与授权边界

单实例、单所有者、多 Agent 空间的个人智能体，可部署 Windows / macOS / Linux 桌面与无桌面服务器。模块化单体、一个主进程、单一 SQLite 应用库、受控文件对象、受管能力子进程。当前不做多人共享、跨机器设备节点或分布式集群。

V2 **不兼容** V1 的数据库、配置、HTTP、工具名或导入路径；不迁 V1 数据、不双写、不保留旧接口兼容层。该授权允许重构，不允许丢弃未提交成果、私人数据或仍在使用的生产入口。

功能、配置、权限、状态各有单一真值；禁止复制状态、旁路授权，或用中文结果字符串推断成功。

已授权的普通开发、可逆修改、离线验证、文档更新直接进行，不反复索取许可。真实支付、外发消息、公开发布、提权、安装系统服务、设备输入与公开部署仍需具体方案与既有授权；用户已延后前台实测，不要再弹测试窗口要求配合。

归档区：`temp/archive`（退役内容）、`temp/private`（私人数据与恢复材料）、`temp/build`（可再生构建/测试产物）。三者都不入库，但不能仅因位于 temp 就删除；活跃 SQLite/WAL/SHM 不单独搬动。

## 3. 必读顺序与文档权威

1. `AGENTS.md`、`docs/HANDOFF.md`、`docs/ARCHITECTURE.md`、`CONTRIBUTING.md`。
2. 本文、`docs/tasks/v2.json`、`docs/DEVELOPMENT_STATUS.md`、`docs/V2_REFACTOR.md` 的**当前摘要段**（历史实施记录标题之前）。
3. 最新证据 `docs/evidence/v2-media.json`；改哪个能力就读哪个能力的合同与报告。
4. `docs/DATA_MODEL.md`（数据规则）、`docs/UI_DESIGN_SYSTEM.md`（界面规则）、`docs/TESTING.md`（测试规则）。
5. 本批重点：`docs/V2_MEDIA.md`、`docs/V2_BILIBILI.md`、`docs/V2_SKILLS.md`，以及 **`docs/MCP.md`**——它是 **V1 的 MCP 行为先例**（指纹撤权、只读白名单、未知不重放、有界重连、OAuth 回调约束），只读参考，**V2 不得 import V1 代码**。

权威关系：`ARCHITECTURE.md` 管职责，`tasks/v2.json` 管任务状态（`DEVELOPMENT_STATUS.md` 由脚本生成），领域合同管行为，原始报告只证明其所属源码版本。

**已知历史残留：** `CONTRIBUTING.md` 的 Windows 定位与旧分层主要描述 V1；`deploy/v2/README.md` 部分早期限制已过期；`V2_REFACTOR.md` 历史段落里的“下一断点”可能落后。冲突时以最新台账、领域合同、源码为准，不按历史句子重做，也不批量改写历史证据。

## 4. 工作区与可恢复基线

| 项目 | 本次核验结果 |
| --- | --- |
| 工作区 | `D:\xm\OmniOperator`，PowerShell |
| 分支 | `codex/architecture-v2`，上游 `origin/codex/architecture-v2`（gitee） |
| MCP 批代码提交 | `8ad22cf`→`bf5f91f`→`564f0db`→`8590144`→`320549f`；台账 `checkpoint_commit=320549f`，本文档提交再前进一个 |
| 最初重构基线 | `076c0c22fa8ecb90d3ce9a64562c90a23a129e00` |
| 交接时工作区 | 干净（`git status` 空） |
| 最新固定源码指纹 | `474c67827a9fe8a0eef1136db59685bf305838d9d89c93a18b8922fa9c61cced` |
| 源码文件 | 1162 份（`agent/`、`web/`、`scripts/` 的 git blob 集合） |
| 恢复材料 | `temp/private/v2-mcp-source-20261111-474c67827a9f/manifest.json` |
| 源码备份 | 同目录 `source-and-evidence.zip`（1411 份源码/文档 + 7 份验证材料） |
| ZIP SHA256 | `51a7fc10b38e8a5b4073e63ddf87b7490521965892a11cd32d70247577123ede` |

封存时 manifest 为 `complete` 且 ZIP SHA 匹配；**complete 只表示可恢复，不表示 V2 完成**。MCP 证据 JSON 与本文档晚于该封存，恢复时需一并复制。上一媒体批与技能批的恢复包保留为溯源材料，不删除。

不要 `git reset --hard`、`git clean`、批量覆盖或重写历史；不要把未跟踪文件当垃圾。

## 5. 目录与主要接线位置

| 路径 | 用途 |
| --- | --- |
| `agent/src/omni/domain/` | 纯领域模型、状态机、约束、协议数据（51 个 .py） |
| `agent/src/omni/application/` | 用例、编排与端口；`ports.py` 是主要依赖合同（44 个 .py） |
| `agent/src/omni/adapters/` | HTTP、模型、数据库、对象、平台、原生、扩展与 worker（204 个 .py） |
| `agent/src/omni/composition/` | 组装、独立 CLI、HTTP 运行时与生命周期（13 个 .py） |
| `agent/src/omni/adapters/persistence/` | SQLAlchemy 元数据、仓储、维护、Alembic 迁移 |
| `agent/src/omni/adapters/workers/` | 受管辅助进程（含 macOS Swift/C 源码） |
| `agent/src/omni/adapters/platform/` | 路径、实例锁、权限、`OwnedProcess` 进程管理 |
| `web/ui/v2/` | V2 工作台原生 ES 模块（`main.js` / `index.html` / 各分区） |
| `agent/tests/test_v2_*.py` | V2 回归、HTTP 契约、迁移冻结、架构与工作台守卫 |
| `deploy/v2/` | 开发部署、容器与平台模板（存在不等于验收） |
| `docs/evidence/` | 各批版本、范围、失败与修复证据 |
| `docs/tasks/v2.json` | 任务真值、下一断点、开放问题 |

根目录 `src`、`tests` 是本机兼容联接，不要从它们再建一套源码。V1 `core/`、`tools/`、`llm/`、`memory/`、`channels/` 只可参考。

## 6. 必须保持的架构与数据合同

- 入口适配器 → 应用服务 → 领域与端口；基础设施实现端口；构造与生命周期集中在 composition。领域无网络/文件/数据库/平台 API/全局运行对象。
- 一个主事件循环、一个实例主进程、一个 Web worker；实例目录由独占运行锁保护。维护命令取得相应独占锁，不为验证停止用户正在使用的服务。
- 默认最多 4 个活跃回合、64 个排队回合；同会话顺序。后台任务、线程、子进程、队列、IPC 全部有界，并有实际所有者、取消与关闭状态。
- 真值 `<home>/data/omni.db`，对象 `<home>/objects`，日志 `<home>/logs`。`--home` 优先，其次 `OMNI_HOME`，否则平台标准目录；工作目录单独配置。
- SQLAlchemy 连接/事务/Alembic 由持久适配器管理；统一短事务写入；网络、模型、人工等待、原生调用不进数据库事务。仓储不自建连接或建表。
- UUID、UTC、Agent 作用域组合约束、revision、事件 sequence、稳定游标、分页上限在服务端与数据库同时维持；JSON 不替代可筛选/关联/唯一的结构化列。
- 入站去重/消息/运行/工作项原子入队；动作先登记意图与审批再派发；输出/终态/投递/必要审计共同提交；费用按调用身份一次结算。
- **unknown ≠ 失败**：不自动重发、不重复输入、不释放未核清费用。进程/线程/连接/文件未实际回收时保留归属，不能只取消 await 就报告已关闭。
- 对象先临时写入、校验、发布，再建引用；引用/固定保护 GC；备份用一致快照与完整引用对象，恢复进新目录。
- 删除传播覆盖摘要、检索、派生记忆、模型来源与对象引用；FTS/向量是可重建索引，不是第二份正文真值。
- 审计/业务/诊断分离；结构化诊断默认不含聊天原文、完整工具参数与结果、Cookie、凭据、原生 handle、异常局部变量；用关联编号串联。
- HTTP 收口 `/api/v1`；工具用结构化状态、错误码、副作用与验证结果；界面如实显示能力、版本差异、未知与降级。
- 凭据只经引用与专用存储（`AsyncSecretPort` / `FileVault`，主密钥外部提供）；模型与工具文本永不含凭据本体。插件进程隔离是故障隔离，不是操作系统沙箱。

## 7. 八个交付包的真实状态

D1–D8 是交付包；R0–R7 是架构阶段，编号不可互换。父阶段不因某个子批通过而自动完成。

| 包 | 开发与离线 | 仍须完成 |
| --- | --- | --- |
| D1 Windows 原生 | UIA、截图、语义动作、显式接管开发及离线完成，有隔离原生样例 | 用户延后的前台键鼠、完整权限/焦点/睡眠恢复与平台验收 |
| D2 macOS | helper、AX/截图/输入暂停、PTY/Keychain、构建交接与 Python 接线源码已有 | Mac mini 编译、原生测试、签名/权限/输入反馈 |
| D3 Linux | logind/总线、AT-SPI/X11 读取、截图、Portal 单帧与部分语义动作源码/注入验证 | 持续前台控制、Wayland RemoteDesktop 身份/暂停证明、PTY/凭据实测 |
| D4 平台运维 | 终端核查、注册/控制、不可变 release、服务交换/隔离救援、分阶段管理员与跨 schema 源码及专项 | 真实管理器、root/账号/文件/ABI/崩溃恢复、三平台长期运行 |
| D5 业务通道 | 选定 QQ 文本、B站读取/发布、安卓所有者/模型范围/Unicode 恢复开发及离线完成 | 真实账号与设备验收；附件/视频属 D6 |
| D6 模型与扩展 | 流式、低清图片、ONNX 文档、记忆语义、档位、文本技能、媒体选定链路完成；**MCP 运行适配器（stdio/Streamable HTTP、显式 OAuth、目录指纹、逐工具授权、工作台）离线完成** | 完整插件链（进程外宿主回调、产物协议、市场更新与安装）；真实外部 MCP 服务与真实 OAuth 供应商验收；真实质量 |
| D7 产品切换 | 待开始 | 令牌管理、统一工作台与能力报告、业务清单、全仓严格类型、三平台包/容器/安装更新恢复 |
| D8 完整验收 | 待开始 | 固定构建全量、四视口双主题、故障/负载、真实功能、每平台 72 小时 |

不根据历史里程碑数或测试数推断接近完成；本次不给百分比。

## 8. 已有能力与应复用的位置

| 能力 | 已有实现与边界 | 入口 |
| --- | --- | --- |
| 核心数据/运行 | 单库、审批、费用、任务、投递、对象、人物、常驻、摘要/删除、配置与来源文档 | `application/*`、`persistence/*`；`DATA_MODEL.md` |
| 日志/维护 | 结构化日志、关联、脱敏、诊断、指标、保留预览/计划/备份/执行/取消/恢复 | `adapters/diagnostics.py`；不因交接再扩展清理系统 |
| QQ | 独立人物/会话/人设、明确 @、重连、冷却、共享审批；仅文本 | `V2_QQ.md`、`application/qq.py` |
| B站 | 基础读取、评论/回复/私信、真实 mention、一次放行、共用 outbox | `V2_BILIBILI.md`、`application/bilibili.py` |
| 安卓 | 设备与运行范围、观察/截图、审批输入、unknown 核查、Unicode IME 与恢复 | `V2_ANDROID*.md`、`application/android.py` |
| 模型流式 | 假 SSE 经真实锁定 SDK 验证；结束/用量/工具才结算或派发 | `V2_MODEL_STREAMING.md`、`application/model_calls.py` |
| 图片 | 原子入队、受控对象、低清实际载荷与预算 | `V2_IMAGES.md`、`application/images.py` |
| 本地向量 | 可选离线 ONNX CPU worker、模型指纹/前缀/分窗、文档与记忆混合召回 | `V2_LOCAL_VECTORS.md`、`V2_MEMORY_SEMANTICS.md` |
| 记忆整理 | 事实/档案/常驻共用条目与来源；只保留显式人工 related | `application/memory_semantics.py` |
| 档位 | 入队捕获模型/预留/策略、工具交集、一次固定模型升档 | `V2_ROUTING.md`、`domain/routing.py` |
| Python 扩展 | 可信目录封存、显式授权、进程外工具、指纹漂移撤权、按回合披露 | `V2_EXTENSIONS` 相关合同、`application/extensions.py`、`adapters/extensions/` |
| 文本技能 | 文本包导入/导出、修订/参考文件、审阅启用、回合封存/披露、撤权恢复 | `V2_SKILLS.md`、`application/skills.py` |
| 媒体/视频理解 | B站来源登记、字幕/ASR/抽帧、触发器状态机、锚点转写、只读工具、取消/恢复/删除、工作台分区 | `V2_MEDIA.md`、`application/media.py`、`adapters/media.py`、`adapters/workers/media.py` |
| **MCP 运行适配器** | 配置修订即服务身份；stdio JSON-RPC 子进程与 Streamable HTTP（JSON/SSE）；显式 OAuth（PKCE/state/回环回调/vault+refresh）；目录与工具指纹、漂移撤权、逐工具授权、每 run 封存；超时/取消/崩溃 unknown 不重放；脱敏调用记录与工作台分区 | `V2_MCP.md`、`domain/mcp.py`、`application/mcp.py`、`adapters/mcp/`、`persistence/mcp_repository.py`、`web/ui/v2/mcp.js` |

## 9. 最近一批：D6.mcp 运行适配器（已收口，勿重做）

新增：`domain/mcp.py`、`application/mcp.py`、`adapters/mcp/{stdio,http,oauth,hosts}.py`、`persistence/mcp_repository.py`、`migrations/versions/v0037.py`、`web/ui/v2/mcp.js`、`agent/tests/test_v2_mcp{,_flow,_oauth,_ui}.py`；接线：`application/{engine,ports}.py`、`adapters/http.py`、`adapters/persistence/{schema,repository,extension_repository}.py`、`composition/{instance,http}.py`、`web/ui/v2/{index.html,main.js}`（资源版本 v49）。

生产链：配置修订（kind=mcp）→ 所有者显式 connect（必要时递增代际、撤销旧授权）→ stdio/HTTP 握手 → `tools/list` 分页 → 目录快照与指纹（name+inputSchema，不含 description）→ 逐工具授权（read_only 显式、审阅审计外键）→ 每 run 封存 `mcp_run_tools` → 模型调用 `mcp__<server>__<tool>` → 动作绑定与 before_send 二次校验 → 传输 → `mcp_calls`（done/failed/unknown 分类）。漂移即撤权并要求重新确认；旧指纹确认 409；OAuth 完成撤旧授权并要求重连。

本批教训：运行封存与授权校验必须同时存在（封存决定模型可见集合，派发前校验决定能否真的发出）；已发出请求的超时/崩溃只能是 unknown，未发出才是 failed；外部 `isError` 是结果错误，不断连；目录指纹不能包含 description，否则文案变化会误撤权。

## 9.1 上一批：媒体/视频理解（已收口，勿重做）

新增：`domain/media.py`、`application/media.py`、`adapters/media.py`、`adapters/workers/media.py`、`persistence/media_repository.py`、`migrations/versions/v0036.py`、`web/ui/v2/media.js`、`agent/tests/test_v2_media.py`、`test_v2_media_ui.py`；接线：`adapters/bilibili.py`、`adapters/http.py`、`composition/instance.py`、`composition/http.py`、`application/ports.py`、`schema.py`、`repository.py`。

生产链：B站视频经配置/凭据/修订授权登记为 `media_sources`（唯一 agent+bvid，身份列不可变，绑定登记时的配置修订）→ 作业 `subtitle/transcribe/frames`（DB 触发器：每来源 ≤8 活跃、转移白名单、终态不可变、`done` 必须有产物行、终态必须 `finished_at`）→ 在线字幕 / 受管子进程 ASR / 等距抽帧 → 转写对象带 `[MM:SS-MM:SS]` 锚点（字幕没有可靠时间轴就不伪造）→ 模型只经 `media_list` / `media_transcript` 读实际落库文本。

本批教训（写 MCP 时同样适用）：

- 模型读的是 `ActionResult.text`：头行带 id/kind/language/range，正文是窗口；缺数据如实报错。
- 作业行不含嵌套来源；`_execute` 在 `media_job_start` **之后**显式读来源行（此前 KeyError 导致卡 queued）。
- 后台分支必须显式赋值发布载荷（ASR 的 `body` 曾未赋值，NameError 被归 unknown）。
- 终态幂等是刻意设计：取消与完成竞态时后到方返回已完成，不报错。
- 超时/崩溃→unknown，业务拒收→failed；排队作业只能经取消离开。
- 配置修订后来源搁浅（`bilibili_configuration_revoked`），重新登记重绑——设计语义，不是缺陷。
- 分页游标与比较键必须同列（曾 created_at 比较配 id 游标，第二页必空）。
- 工作台刷新只在所选项消失时清空选择。
- 浏览器测试同步点用服务端真值（轮询 job id），不要用重复的 UI 状态文本。
- 服务上限：2 并发、下载 256 MiB/180 s、转写 4 MiB/900 s、抽帧 180 s、工具文本 ≤12000 字符。

## 10. 刚刚完成的 D6.mcp 合同（实现已交付，保留为设计与验收依据）

**状态（2026-11-11）：本合同已实现并提交（`8ad22cf`、`bf5f91f`、`564f0db`、`8590144`、`320549f`），离线验收见 `docs/evidence/v2-mcp.json` 与 `docs/V2_MCP.md`。** 以下起点描述保留为当时的现状快照；真实外部 MCP 服务与真实 OAuth 供应商仍属“待真实验收”，完整插件链是下一批。不要重做已交付部分，也不要把离线验收当作真实服务完成。

### 10.1 已核实的起点（2026-11-10）

- `agent/src/omni/adapters/persistence/schema.py` 的 `mcp_options`：主键 `(revision_id, agent_id, kind='mcp')`，外键到 `configuration_revisions`；列 `transport('stdio'|'streamable_http')`、`command`、`arguments`(JSON)、`endpoint`、`auth_mode('none'|'bearer'|'oauth')`、`credential_ref`，并有 transport 形状约束。**MCP 服务是"配置修订"这一等公民，不是新增独立配置体系。**
- `agent/src/omni/domain/configuration.py` 的 `MCPOptions` 已校验：stdio 不得带 endpoint/auth；HTTP 不得带 command/arguments 且必须 endpoint；bearer 必须 credential_ref；arguments 里禁止 `--api-key/--token/--password/--cookie/--authorization` 之类内联凭据。
- `agent/src/omni/adapters/http.py` 已有 `kind: Literal["model","channel","mcp","preset"]` 的配置 CRUD 端点，`HttpRuntime` **没有** mcp 字段；`composition/instance.py` 也**没有** MCP 构造。
- `agent/src/omni/domain|application|adapters` 下**没有**任何 MCP 运行代码。V1 的 `tools/mcp/`、`channels/web_api/mcp.py`、`memory/mcp_schemas.py` 只能读来理解行为，**不得 import**。
- 先例（强烈建议照抄结构，不照抄业务）：
  - `application/extensions.py`：`authorize` / `disable` / `tools_for_run` / `invoke` / `review_quiescent` / `health`；invoke 前比对 `arguments_sha256`，`before_dispatch` 二次校验，漂移码 `extension_snapshot_drift`，产物协议不支持时返回 UNKNOWN。
  - `adapters/extensions/hosts.py`：`MAX_HOSTS=4`、`MAX_PENDING=64`、`revoke(agent_id, id, authority_epoch, timeout_s)`、`health()`、`stop_accepting/close`。
  - `adapters/workers/client.py`：`spawn` 用 `OwnedProcess`、stderr 排空、`invoke` 有界、`health()`、`review_quiescent()`、`close()`。
  - `adapters/platform/processes.py`：`OwnedProcess.spawn/terminate`，Windows Job 对象。
  - `application/engine.py` 第 224–230 行：每回合把 `tools_for_run` 的 spec 合并进 registry；`Capability(spec, adapter)`；`ToolSpec(writes, timeout_s, resource, binding_id)`。
  - `domain/contracts.py`：`ActionResult(status, text, effect, error_code, retryable, artifacts, verification, data)`、`InvocationContext(agent_id, run_id, session_id, policy_revision, workspace)`。
  - 凭据：`AsyncSecretPort.get(reference)` 只在适配器里取用；引用字符集 `[a-zA-Z0-9_.:/-]{1,200}`；`FileVault` 主密钥必须在目录外。

### 10.2 建议的新建与接线

| 位置 | 内容 |
| --- | --- |
| `domain/mcp.py` | 传输/认证枚举、目录与工具指纹（**name + inputSchema**，不含 description）、错误分类（failed / unknown / denied）、授权与漂移判定、结果文本上限；纯函数，无 I/O |
| `application/mcp.py` | `MCPService`：install/save（走既有 ConfigurationService 语义）、connect、discover(分页)、authorize/revoke、`tools_for_run`、`invoke`、cancel、recover、health、close；端口 `MCPTransportPort` |
| `adapters/mcp/`（或单文件 `adapters/mcp.py`） | `stdio.py`（JSON-RPC over 子进程，复用 `OwnedProcess`）、`http.py`（Streamable HTTP，有界超时/取消）、`oauth.py`（显式授权流 + PKCE/state）、`catalog.py`（分页发现与指纹） |
| `persistence/mcp_repository.py` + `migrations/versions/v0037.py` | 服务运行态与代际、目录快照/指纹、逐工具授权（含 owner 审阅证据）、调用记录与 unknown 标记；沿用触发器风格：状态转移白名单、终态不可变、`done` 必须有结果行 |
| `composition/instance.py` / `composition/http.py` | 构造、`lifetime.own("mcp", ...)`、`health()["mcp"]`、启动 `recover()`；`HttpRuntime.mcp` |
| `adapters/http.py` | `/agents/{id}/mcp/servers`（列表/详情/连接/断开/重置）、`.../catalog`（分页）、`.../tools/{tool}/authorization`、`.../oauth/start`（显式）、`.../health` |
| `web/ui/v2/mcp.js` + `index.html` + `main.js` | 新分区：服务列表、被动状态、目录逐工具展开与确认、连接/断开/重置（先确认目标）、OAuth 显式发起、脱敏日志入口 |
| `agent/tests/test_v2_mcp*.py` | 见 10.8 |

不要新建第二套配置体系、不要新增 `config.yaml` 式旁路、不要把 MCP 服务塞进 `settings` 表。

### 10.3 传输与进程

- stdio：每服务一个持久会话 + 所有者线程/进程；同一服务调用串行；`MAX_*` 与等待上限显式；握手/发现/调用共用请求总预算。
- 进程退出/崩溃：in-flight 调用归 **unknown**，不重放；显式请求才允许有界重连（V1 先例 5/10/20/40/80 秒，5 次用尽须人工 reset），本批可自定但必须写进合同与证据。
- HTTP：连接有界、超时与显式取消；断连与超时区分 failed/unknown；不允许无限重试；凭据只从引用取，不回显。
- OAuth：用户显式发起才注册客户端与打开授权页；PKCE/state 限本次请求（V1 先例 120 秒）；回调来源白名单；令牌入 vault；换账号/机器需重新授权。遇到 401 不自动开授权。
- 关闭顺序：`stop_accepting()` → 取消等待 → 回收子进程/连接 → `close(timeout)`；实际未回收时 `health()` 如实报。

### 10.4 目录、指纹与授权

- 目录按服务显式发现，分页有上限（V1 先例 16 页 / 256 项）；指纹 = 工具名 + `inputSchema` 的稳定序列化哈希。
- 指纹变化 → 撤销该服务既有授权并置"需重新确认"；未授权工具对模型不可见；旧页面不能批准新指纹。
- 只读白名单只能由所有者显式声明（不得依据服务自报 `readOnlyHint`），且必须是已授权工具的子集；未声明则按写处理（走审批）。
- 外部描述与返回值是不可信数据：不授予权限、不自动访问链接、不转发 base64 媒体。
- 服务器 `isError` 呈现为错误，不等同传输失败，不断开连接。

### 10.5 工具暴露与模型文本

- 用 `ToolSpec(..., writes=?, timeout_s=?, resource="mcp")`；名称前缀服务标识防撞（例如 `mcp_<server>_<tool>` 或保留原始名 + binding_id，二选一并写进合同）。
- 每回合按 run 封存可用工具（`tools_for_run` 先例），而不是全局常驻；运行中撤权后不得再用旧授权派发。
- 模型可见文本：来源服务 + 工具名 + 结果摘要，≤12000 字符；错误码结构化；不得回传凭据、完整异常或原始外部载荷。
- `Outcome.UNKNOWN` 只在"可能已执行"时使用，`Effect.UNKNOWN` 同步标注；不要用 failed 掩盖未知。

### 10.6 数据与迁移

- 新迁移 **v0037**；不改写 v0036。表至少覆盖：服务运行态与代际（authority epoch）、目录快照与指纹、逐工具授权、调用与 unknown 记录。
- 触发器/约束：状态转移白名单、同一服务不能同时两个活会话、终态不可变、授权必须带当前指纹、跨 Agent 隔离（组合外键 `(id, agent_id)`）。
- 服务身份绑定配置修订；修订变更后旧授权失效并要求确认（与媒体 `bilibili_configuration_revoked` 同类语义）。

### 10.7 工作台

- 新分区挂在 `web/ui/v2/index.html` 的 `nav` 与页面容器，`main.js` 注册 mount/load/switch（参照 `page === 'extensions'` 的写法）；版本串**整组原子递增**（当前 `20261110v48`）。
- 状态必须被动可读（不启动进程），连接/断开/重置先展示目标并防重复点击，失败不自动重放。
- 目录逐工具展开、显示完整参数 schema、当前指纹与授权状态；390/1024 两个视口都要能用。

### 10.8 退出条件与测试矩阵

至少形成端到端：**显式配置 → 受管 stdio/HTTP 连接 → 目录发现与逐工具授权 → 模型经工具实际调用 → 指纹漂移撤权 → 工作台可见**。

- 假 stdio JSON-RPC 子进程（真实进程，非 mock）与假 HTTP 服务；覆盖中文、分页、会话复用、并发串行。
- 故障：握手超时、调用超时、崩溃、断连、取消、`isError`、未知不重放、重连上限用尽。
- 授权：未授权不可见、漂移撤权、旧指纹不能确认、只读白名单子集校验、跨 Agent 不可见。
- 凭据：模型文本/HTTP/日志/审计中均无凭据；引用不存在时失败明确。
- 守卫：`test_v2_architecture.py`（依赖方向）、worker 文件范围与进程所有权、迁移冻结、`test_v2_http.py`（OpenAPI 与端点目录一致）。
- 浏览器：新分区 390/1024 流程 + 一次全工作台回归；报告与哈希按既有证据格式记录。

真实外部 MCP 服务与真实 OAuth 供应商另列"待真实验收"；只有配置表或只有假服务都不算完成。

### 10.9 不得做的事

- 不 import V1 `tools/mcp`、不复制其 YAML 配置或 DPAPI 凭据目录。
- 不在模型上下文、`ActionResult.data`、HTTP 响应或日志中暴露 token。
- 不因为"连不上"就自动安装/下载外部服务，或修改用户 system 服务。
- 不把 unknown 当 failed 后自动重试写操作；不静默放行未授权工具。
- 不新增第二套迁移头、不改写历史迁移、不手工改 `schema.py` 之外的真值。

## 11. 后续固定顺序

1. ~~D6.mcp 收口（上一批，已完成离线链）~~；仍待真实外部 MCP 服务与真实 OAuth 供应商验收、自动重连策略。
2. **完整扩展链（当前断点）**：进程外宿主回调、产物协议、版本/授权、市场更新提示与实际安装；复用对象/审批/费用。
3. 收口 D2/D3/D4 源码欠账与平台反馈（Linux 持续控制与 Wayland 证明、Mac 编译/权限/原生 ABI、真实系统管理器与恢复）；无环境就记"待真实验收"，继续其他开发。
4. 处理基线遗留失败（`tests.baseline_failures_pre_mcp`：history/event-pruning、schema-release、service 启动效果、extensions UI 等）——按所属批次修复，不用 MCP 批兜底。
5. D7 门槛：令牌管理、统一工作台与能力报告、业务清单、全仓严格类型、打包/容器/安装更新恢复；门槛满足后才切 `omni` 默认入口并归档 V1。
6. D8 固定构建验收：全量、四视口双主题、故障注入、参考负载、真实模型/QQ/B站/安卓/远程 MCP、每平台 72 小时。

## 12. 验证口径与已知问题

MCP 批 `docs/evidence/v2-mcp.json` 汇总四份报告的**同节点最新结果**，共 150 个不同节点全过（不是报告数相加）：

| 报告 | 用例 | 用时 | SHA256 前缀 |
| --- | --- | --- | --- |
| `v2-mcp-final.xml` | 20 | 见证据 | 见证据 |
| `v2-mcp-ui.xml` | 2 | 见证据 | 见证据 |
| `v2-mcp-callers.xml` | 54 | 见证据 | 见证据 |
| `v2-mcp-guards.xml` | 74 | 见证据 | 见证据 |

门禁 `temp/build/v2-mcp-gates.json`（ruff / format / pyright / prettier / 项目索引 / 任务摘要 全 exit 0，pyright 0 错误）。**没有全量通过结论**：同源码全量 4541 passed / 16 failed / 10 errors，26 个失败/错误节点均为基线 `be58739` 遗留，已逐类在基线复现并写入证据；本批另修复其中 3 个（媒体 worker 动态导入、两处迁移时间线）。全量内的工作台/主题 73 例浏览器用例通过。没有主包重建、远程 CI、Mac/Linux、真实账号设备、负载或 72 小时结论。

开放问题（不要因一次重跑通过就标记修复）：

- `UI.exact_organization_refresh_timing`：旧精确整理 UI 一次刷新显示 queued；单节点复核通过，未判定稳定。报告 `temp/build/v2-memory-semantics-final.xml`、`-repair.xml`。
- `tests.baseline_failures_pre_mcp`：基线已有 16 failed / 10 errors（history/event-pruning、schema-release 固定旧 schema、service 启动效果、extensions UI 假 hosts 等）；与 MCP 无关，需由所属批次处置。报告 `temp/build/v2-mcp-full.xml`。
- `security.sensitive_history_services_acceptance`：敏感审计 2/2 为历史命中，但工作区 `docs/evidence/services-acceptance-2026-11-01.json`（`ad2ae05` 引入，未改动）仍含 1 条已确认字面量；MCP 批新增文件零命中。处置（改写历史或替换该文件）需所有者决定。审计产物在 `D:\omni-private-evidence\sensitive-audit\v2-media-20261110.json\`。

## 13. 开发与验证执行方法

开发中只跑受影响模块与直接调用方，单进程：

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/test_<相关模块>.py
uv run --no-sync pytest -q -n 0 agent/tests/test_<相关模块>.py::test_<行为>
uv run --no-sync pytest -q -n 0 --lf --last-failed-no-failures=none   # 只用缓存失败，缓存空不启动全仓
```

`agent/pyproject.toml` 的 addopts 已含 `-n auto --dist=loadfile`；不要加 `-p no:xdist`，`-n 0` 用于单文件调试。重型 Pyright、浏览器、打包顺序执行；UI 同文件共享 `ui_browser` 进程，每用例独立 BrowserContext 与临时数据库。

门禁命令（按改动文件）：

```powershell
.venv\Scripts\python.exe -m ruff check <files>
.venv\Scripts\python.exe -m ruff format --check <files>
uv run --no-sync pyright <files>            # 必须 --no-sync
node web/node_modules/prettier/bin/prettier.cjs --check <files>
uv run --no-sync python scripts/project_index.py [--check]
uv run --no-sync python scripts/task_status.py [--check]
```

完整任务收尾/提交前按仓库要求跑一次全量；同一固定版本可复用结果；不在纯文档或状态更新后跑全量，不并发多个全量。不要为提速跳过断言、缩短被测超时、去掉网络闸门或用真实用户数据；不要 `uv sync`（会重建入口并锁文件）。

`temp/build` 下的 seal 脚本是特定批次的一次性状态变更工具，**不能直接重跑**。

## 14. 隔离运行与平台交接

```powershell
$v2DevHome = Join-Path $env:TEMP ("omni-v2-dev-" + [guid]::NewGuid().ToString("N"))
uv run --no-sync python -m omni.composition.cli --home $v2DevHome init
uv run --no-sync python -m omni.composition.cli --home $v2DevHome serve
```

默认 `http://127.0.0.1:8766`；端口占用时看 `serve --help` 换隔离端口，不停已有服务。`auth` 是所有者本机命令；令牌只给所有者在本地界面使用，不写进 AI 工具输出、交接、URL 或日志。未配置真实模型时不要填真实凭据或发付费请求。

macOS 先看 `docs/V2_MACOS_HANDOFF.md`、`V2_MACOS_SYSTEM_SERVICES.md`、`V2_ADMIN_HANDOVER.md`；Bundle ID `org.omnioperator.desktophelper`，安装/签名仍须按合同核验。Linux 看 `docs/V2_LINUX_HANDOFF.md`；无桌面容器不能替代 X11/AT-SPI/Wayland 交互验证。

## 15. 换会话、换机器时如何交付

- **同机且能访问工作区**：发送 `docs/V2_AI_CONTINUATION_PROMPT.md` 全文，要求先读本文与台账；继续用当前工作树，保留在途修改。
- **只有聊天附件**：至少给本文、提示词、台账、最新证据；要实际开发必须给源码，AI 无法只靠说明重建。
- **换机器/新 checkout**：准备记录的 Git HEAD，再叠加 `source-and-evidence.zip` 的 `source/`；`validation/` 对应 `temp/build/` 语义。按 manifest 校验 ZIP SHA 与逐成员指纹，仅"能解压"不算恢复成功。
- 恢复包不含运行库正文、凭据、主密钥、浏览器登录态或已安装服务；本次文档晚于封存，需单独复制本文与提示词。

## 16. 下一会话的交付要求与最终完成门槛

接手后先给一段事实核对，然后**实际开发 D6.mcp**。每个节点收尾需要：说明完成的生产路径、修改文件、真实限制；记录测试/类型/排版的实际结果与原始报告；保留开放问题；更新台账、生成摘要、更新本文当前段、领域合同与索引；为该固定源码保留可恢复副本；提交并推送。

开发完成、离线验证、真实平台验收分别记录；文档更新不产生"新业务验证通过"，不改写旧封存指纹。源码与本文不一致时先对账，不自动覆盖为旧快照。

**整个 V2 的完成条件：**既定业务迁移与扩展开发闭环、正式入口切换、V1 生产引用退役、三平台能力如实可用、安装/包/容器/更新/恢复可复现、固定构建必要测试通过，以及对应真实账号/设备/三平台各 72 小时证据。缺环境就如实登记，不用"模拟通过"替代，也不因此停止其他可完成开发。

## 17. 接手当天核对清单

```powershell
git log --oneline -5
git status --porcelain
git ls-remote origin refs/heads/codex/architecture-v2
git rev-parse HEAD
uv run --no-sync python scripts/project_index.py --check
uv run --no-sync python scripts/task_status.py --check
```

- 期望：分支 `codex/architecture-v2`，远端与本地一致（或本地领先本批文档提交），工作区干净。
- 期望：索引 `modules=65 files=715 routes=427 declared_tools=94`，`stale=` 为空。
- 期望：`docs/tasks/v2.json` 的 `next_checkpoint.id == "D6.extensions"`（`D6.mcp` 已完成离线链），`current_validation.targeted_unique_passed == 150`、`includes_browser_cases == 2`、`current_full_passed == false`（全量 16 failed / 10 errors 为基线遗留，见证据 `known_baseline_failures`）。
- 若数字不同：先判断是否有并行会话提交，再决定是否重算索引/摘要；不要改历史证据。
