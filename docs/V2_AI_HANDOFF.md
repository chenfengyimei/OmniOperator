# OmniOperator V2：AI 接续开发完整交接

交接日期：2026-11-10（Asia/Shanghai）。本文是交接时点的快照；后续以 `docs/tasks/v2.json`、当前源码和 `docs/evidence/` 共同核验。不要用文件修改时间推断功能完成度。

## 1. 接手时先明确的结论

- **整个 V2 未完成；`omni`、`python -m omni`、`omni-web` 等正式入口仍是 V1。** V2 用独立开发入口 `python -m omni.composition.cli --home <新目录> serve`，默认 `127.0.0.1:8766`。不要提前切换或删除 V1。
- 当前断点：**D7 产品切换进行中**，能力报告、令牌管理、业务清单、导航收敛、strict 类型一百批（application 43/44、adapters 103/215）、Windows 打包验证、安装收据/产物/暂存/解包/切换计划/更新备份/回滚演练/状态/冒烟、**切换执行（switch/install/current）、迁移执行（migrate+重 seal）、候选卸载（uninstall）、离线环境（environment）、venv 运行选择、live 回滚（代码指针）、状态投影与显式清理（--purge-data）**已完成；剩余完整 V2 安装器/更新器（停服/启动、数据库备份恢复）、容器镜像、三平台构建与干净机器端到端、正式入口切换。D6 扩展链离线节点已全部完成，不要重做已收口的扩展/MCP/媒体/对象/人物/摘要/调度/文本技能部分。
- 最新完成节点是 **D7.typecheck.batch_100（strict 第一百批）**：strict **199 文件**（**53/53 domain** + 43 application + 103 adapters）0 errors；本批守卫 + 服务交换/清理文件调用方 **61 项全过**。第五十四批后全量（-n 2）**4654 项：4630 passed / 24 failed**（22 项为进行中工作台改版 UI 用例、2 项并行时序隔离通过）；第五十五至一百批改源码后尚未重跑全量，**不是全量全绿、全仓严格类型或三平台验收**；证据 `docs/evidence/v2-typecheck.json`。
- 严格类型基线：strict 199 文件、0 errors（domain 53/53 完成、application 43/44、adapters 103/215）；一百批类型守卫 + 调用方回归全过。
- 安装器链后全量复核：**4654 项：4652 passed / 2 failed / 0 errors**（debug_sink 并行时序 + 已修复的文档守卫；隔离 13 项全过）；证据 `docs/evidence/v2-installer-full-verification.json`。
- strict 批次后全量复核：**4654 项：4653 passed / 1 failed / 0 errors**（`debug_sink` 并行时序，隔离通过）；证据 `temp/build/v2-typecheck-batches-53-full-n2.xml`。
- 已提交并推送：`8ad22cf`…`44890f0`（产物协议）、`adc528d`（产物工作台）、`5f3b46e`（基线修复）、`965588e`（市场扫描）、`7ffda3d`（市场工作台）、`e74572d`（宿主回调）、`1eaf6a9`（能力报告）、`ed5912a`（令牌管理）、`560387d`（令牌工作台）、`9133001`（业务清单）、`1a18fae`（导航收敛）、`6679edf`（类型起步）、`a3aa0e4`（类型第二批）、`cfa9d1d`（类型第三批）、`800e719`（类型第四批）、`7a5568f`（打包验证）、`ac752a1`（类型第五批）、`bd3efe4`（类型第六批）、`dd84b04`（类型第七批）、`3ee417a`（类型第八批）、`0a90cec`（类型第九批）、`0951001`（类型第十批）、`f6f0e2e`（安装收据/预检）、`701f3f4`（运行时收据校验）、`fd0eb0b`（wheel 产物校验）、`5b28d41`（wheel 产物暂存）、`b0bafae`（wheel 候选解包）、`a6dd48e`（切换计划）、`caa08d4`（更新备份步）、`1a13f3c`（分层修复）、`fe0d7b2`（回滚演练）、`6121831`（安装状态汇总）、`26fc9d1`（候选冒烟）、`1bd8472`（类型第十一批）、`e8a5431`（类型第十二批）、`febe5df`（类型第十三批）、`95d4524`（类型第十四批）、`6e5e64c`（类型第十五批）、`b05d937`（类型第十六批）、`3cb1116`（类型第十七批）、`45121bf`（类型第十八批）、`9efa947`（类型第十九批）、`9d9153d`（类型第二十批）、`91c8c6f`（类型第二十一批）、`5640983`（类型第二十二批）、`fc3495f`（二十二批收口）、`af4a347`（切换执行）、`b6f34cf`（迁移执行）、`a325e72`（候选卸载）、`ae5d4fb`（离线环境）、`c291234`（运行选择）、`805f2bb`（live 回滚）、`dcb1f5b`（状态投影）、`4042f66`（显式清理）、`1914acf`（类型第二十三批）、`5b6a78e`（类型第二十四批）、`7b3f535`（类型第二十五批）、`12c9fe0`（类型第二十六批）、`a67715b`（类型第二十七批）、`6e21875`（类型第二十八批）、`a2de461`（类型第二十九批）、`5833664`（类型第三十批）、`91a9e74`（类型第三十一批）、`5777280`（类型第三十二批）、`982ad6d`（类型第三十三批）、`1475e64`（类型第三十四批）、`f094c37`（类型第三十五批）、`e5ce437`（类型第三十六批）、`727e5c6`（类型第三十七批）、`56aacde`（类型第三十八批）、`1eeb60f`（类型第三十九批）、`219f646`（类型第四十批）、`1e67fb8`（类型回归修复）、`e67d43d`（类型第四十一批）、`df32bac`（类型第四十二批）、`57e5f60`（类型第四十三批）、`05ceb36`（类型第四十四批）、`ef1e1cb`（类型第四十五批）、`ff8397f`（类型第四十六批）、`581bc5b`（类型第四十七批）、`99bc23a`（类型第四十八批）、`85cb820`（类型第四十九批）、`54a9248`（类型第五十批）、`3b65358`（回归修复）、`6161c79`（类型第五十一批）、`1b66a3b`（类型第五十二批）、`11042ce`（类型第五十三批）、`bf07460`（UI 设计系统修复）、`b3e27b3`（类型第五十四批，domain 完成）、`19b77e7`（类型第五十五批，application 起步）、`72ebc58`（类型第五十六批）、`b699fa8`（类型第五十七批）、`81a048d`（类型第五十八批）、`1f96ed2`（类型第五十九批）、`0d140c9`（类型第六十批）、`4bec606`（类型第六十一批）、`8c1eddf`（类型第六十二批）、`49ae7cf`（类型第六十三批 + 索引）、`b17fe6c`（类型第六十四批）、`cfd93c4`（类型第六十五批）、`0fd9465`（类型第六十六批）、`853bf26`（类型第六十七批）、`98f4bf5`（类型第六十八批）、`c297eed`（类型第六十九批）、`56a3e22`（类型第七十批）、`c6f1b72`（类型第七十一批）、`b265be6`（类型第七十二批）、`e3c11a9`（类型第七十三批）、`ae4ed1c`（类型第七十四批）、`ca2945f`（类型第七十五批）、`2fc7428`（类型第七十六批）、`5a20b24`（类型第七十七批）、`0ddc6f6`（类型第七十八批）、`fa4f3f1`（类型第七十九批）、`4d29c27`（类型第八十批）、`9814b14`（类型第八十一批）、`3546fe8`（类型第八十二批）、`a00ba37`（类型第八十三批）、`921d3b6`（类型第八十四批）、`8f9a88c`（类型第八十五批）、`2b836d9`（类型第八十六批）、`46005d5`（类型第八十七批）、`6bfe2f9`（类型第八十八批）、`39cf976`（类型第八十九批）、`6a83bbb`（类型第九十批）、`0b5028f`（类型第九十一批）、`dec708a`（类型第九十二批）、`649ab15`（类型第九十三批）、`c97ebe1`（类型第九十四批）、`20f2ed7`（类型第九十五批）、`c60731a`（类型第九十六批）、`1ca7ff5`（类型第九十七批）、`b425f7d`（类型第九十八批）、`44eaa19`（类型第九十九批）、`5689cb5`（类型第一百批）；分支 `codex/architecture-v2` 已设上游 `origin/codex/architecture-v2`；`master` 未变。
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
| MCP 与扩展批代码提交 | `8ad22cf`→…→`b17fe6c`→`cfd93c4`→`0fd9465`→`853bf26`→`98f4bf5`→`c297eed`→`56a3e22`→`c6f1b72`→`b265be6`→`e3c11a9`→`ae4ed1c`→`ca2945f`→`2fc7428`→`5a20b24`→`0ddc6f6`→`fa4f3f1`→`4d29c27`→`9814b14`→`3546fe8`→`a00ba37`→`921d3b6`→`8f9a88c`→`2b836d9`→`46005d5`→`6bfe2f9`→`39cf976`→`6a83bbb`→`0b5028f`→`dec708a`→`649ab15`→`c97ebe1`→`20f2ed7`→`c60731a`→`1ca7ff5`→`b425f7d`→`44eaa19`→`5689cb5`；台账 `checkpoint_commit=5689cb5`，本文档提交再前进一个 |
| 最初重构基线 | `076c0c22fa8ecb90d3ce9a64562c90a23a129e00` |
| 交接时工作区 | 本会话工作区干净；另一会话的工作台 UI 改版已提交（`b7043ee`…`75c4219`，资源标识 20261112v56），保留其成果 |
| 最新固定源码指纹 | `b157aa69f31a1b65a4f28d2d3e15ca37bc4d059ef4337e611a929609fbaddf8c` |
| 源码文件 | 1200 份（`agent/`、`web/`、`scripts/` 的 git blob 集合） |
| 恢复材料 | `temp/private/v2-installer-status-source-20261111-3301628964ee/manifest.json`（purge 节点复用该恢复包并叠加 `4042f66`） |
| 源码备份 | 同目录 `source-and-evidence.zip`（1476 份源码/文档 + 2 份验证材料） |
| ZIP SHA256 | `6fa595ec2f5f4e24b16cbe8df658af66940a53d56e45e07259f152a4e27820e5` |

封存时 manifest 为 `complete` 且 ZIP SHA 匹配；**complete 只表示可恢复，不表示 V2 完成**。本批证据 JSON 与本文档晚于该封存，恢复时需一并复制。MCP/媒体/技能批的恢复包保留为溯源材料，不删除。

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
| D6 模型与扩展 | 流式、低清图片、ONNX 文档、记忆语义、档位、文本技能、媒体、**MCP 运行适配器**与**扩展产物协议（声明式媒体类型/子进程有界目录/宿主二次校验/对象发布）**离线完成 | 进程外宿主回调、市场更新提示与安装、产物工作台展示；真实外部 MCP/OAuth 与真实第三方插件验收；真实质量 |
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
| Python 扩展 | 可信目录封存、显式授权、进程外工具、指纹漂移撤权、按回合披露；**声明式产物协议（≤4 媒体类型、子进程有界目录、宿主二次校验、对象发布与动作引用、文本仅类型/字节数）** | `V2_EXTENSIONS.md`、`application/extensions.py`、`adapters/extensions/` |
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
2. **扩展链离线节点已完成**：产物协议/工作台、本地市场扫描/工作台、宿主回调（context/log）、版本/授权回归均已提交；剩余真实市场源、真实第三方插件、三平台原生 ABI 等真实环境验收，无环境则继续其他开发。
3. 收口 D2/D3/D4 源码欠账与平台反馈（Linux 持续控制与 Wayland 证明、Mac 编译/权限/原生 ABI、真实系统管理器与恢复）；无环境就记"待真实验收"，继续其他开发。
4. ~~处理基线遗留失败（history/event-pruning、schema-release、service 启动效果、extensions UI 等）~~ 已完成：`5f3b46e` 测试-only 修复 26 项，证据 `docs/evidence/v2-baseline-repair.json`；仅两处并行时序抖动在观察。
5. D7 门槛：令牌管理、统一工作台与能力报告、业务清单、全仓严格类型、打包/容器/安装更新恢复；门槛满足后才切 `omni` 默认入口并归档 V1。
6. D8 固定构建验收：全量、四视口双主题、故障注入、参考负载、真实模型/QQ/B站/安卓/远程 MCP、每平台 72 小时。

## 12. 验证口径与已知问题

扩展产物节点 `docs/evidence/v2-extension-artifacts.json`：产物协议 146 项 + 工作台/API 18 项专项全过，与 MCP 证据合并 **228 个不同节点**最新全过（含 4 例 390/1024 浏览器）：

| 报告 | 用例 | 说明 |
| --- | --- | --- |
| `v2-extension-artifacts.xml` | 146 | 产物协议 + 调用方/守卫 |
| `v2-extension-workbench.xml` | 18 | 动作证据 API + 产物预览（390/1024）+ MCP UI 版本断言 |
| `v2-extension-market.xml` | 62 | 本地市场只读扫描：服务/HTTP + 包/宿主/架构调用方 |
| `v2-extension-market-ui.xml` | 25 | 市场工作台 390/1024 更新确认与重新授权 + UI/服务/HTTP 回归 |
| `v2-extension-callbacks.xml` | 93 | 宿主回调 context/log、预算/超长 UNKNOWN + 扩展/包/宿主/worker/架构回归 |
| `v2-capabilities.xml` | 17 | D7 能力报告：域投影边界/attention、HTTP 鉴权与泄漏检查、390/1024 页面 |
| `v2-owner-token.xml` | 38 | D7 令牌清单/轮换/重启/CLI + HTTP/架构/服务运行时调用方 |
| `v2-owner-token-ui.xml` | 16 | D7 令牌工作台 390/1024 轮换与令牌交接 + 令牌/HTTP/架构调用方 |
| `v2-business-inventory.xml` | 2 | D7 业务清单：导航/页面/清单双向一致与状态词表守卫 |
| `v2-navigation.xml` | 11 | D7 导航收敛：7 分组/22 入口/逐页可达 390/1024 + 清单/UI 调用方 |
| `v2-typecheck.xml` | 48 | D7 strict 起步：类型范围守卫 + 能力/对象/人物/凭据调用方 |
| `v2-typecheck-2.xml` | 74 | D7 strict 第二批：metering/release_fence + 配置/上下文/清理/恢复调用方 |
| `v2-typecheck-3.xml` | 44 | D7 strict 第三批：memory_semantics/foreground + 前台/记忆语义调用方 |
| `v2-typecheck-4.xml` | 50 | D7 strict 第四批：context/recovery_authority + 上下文/发布/恢复调用方 |
| `v2-typecheck-5.xml` | 34 | D7 strict 第五批：model_stream + 模型流/worker 调用方 |
| `v2-typecheck-6.xml` | 111 | D7 strict 第六批：service_control + 服务 drain/运行时/清理/交换恢复/发布/恢复守卫 |
| `v2-typecheck-7.xml` | 64 | D7 strict 第七批：application/adapters 7 模块 + 路由/发布/交换/本地向量调用方 |
| `v2-typecheck-8.xml` | 87 | D7 strict 第八批：service_deployment/exchange_recovery/metrics + 部署/运行时/交换/排空/指标调用方 |
| `v2-typecheck-9.xml` | 41 | D7 strict 第九批：service_cleanup/terminal_review + 服务清理/终端核查调用方 |
| `v2-typecheck-10.xml` | 32 | D7 strict 第十批：model_output + 模型流/预览调用方 |
| `v2-installation.xml` | 6 | D7 安装收据/预检：4 项安装测试 + 2 项打包守卫 |
| `v2-installation-runtime.xml` | 7 | D7 收据接入运行时：5 项安装/运行时测试 + 2 项打包守卫 |
| `v2-wheel-artifact.xml` | 10 | D7 wheel 产物只读校验：3 项产物 + 5 项安装/运行时 + 2 项打包守卫 |
| `v2-wheel-stage.xml` | 14 | D7 wheel 产物暂存：4 项暂存 + 3 项产物 + 5 项安装/运行时 + 2 项打包守卫 |
| `v2-wheel-prepare.xml` | 17 | D7 wheel 候选解包：3 项解包 + 4 项暂存 + 3 项产物 + 5 项安装/运行时 + 2 项打包守卫 |
| `v2-wheel-switch.xml` | 21 | D7 切换计划：4 项计划 + 3 项解包 + 4 项暂存 + 3 项产物 + 5 项安装/运行时 + 2 项打包守卫 |
| `v2-wheel-backup.xml` | 25 | D7 更新备份：4 项备份 + 4 项计划 + 3 项解包 + 4 项暂存 + 3 项产物 + 5 项安装/运行时 + 2 项打包守卫 |
| `v2-installer-full-fixed.xml` | 4624 | 安装器节点后全量：4623 passed / 1 failed（debug_sink 并行时序）；隔离 11 项全过 |
| `v2-wheel-rollback.xml` | 28 | D7 回滚演练：3 项演练 + 4 项备份 + 4 项计划 + 3 项解包 + 4 项暂存 + 3 项产物 + 5 项安装/运行时 + 2 项打包守卫 |
| `v2-wheel-status.xml` | 31 | D7 安装状态：3 项状态 + 3 项演练 + 4 项备份 + 4 项计划 + 3 项解包 + 4 项暂存 + 3 项产物 + 5 项安装/运行时 + 2 项打包守卫 |
| `v2-wheel-smoke.xml` | 34 | D7 候选冒烟：3 项冒烟 + 3 项状态 + 3 项演练 + 4 项备份 + 4 项计划 + 3 项解包 + 4 项暂存 + 3 项产物 + 5 项安装/运行时 + 2 项打包守卫 |
| `v2-typecheck-11.xml` | 11 | D7 strict 第十一批：people 类型守卫 + 人物调用方 |
| `v2-typecheck-12.xml` | 24 | D7 strict 第十二批：memory_tools + 上下文/人物调用方 |
| `v2-typecheck-13.xml` | 15 | D7 strict 第十三批：action_review + 动作复核调用方 |
| `v2-typecheck-14.xml` | 25 | D7 strict 第十四批：operations + 运维/HTTP/CLI 调用方 |
| `v2-typecheck-15.xml` | 27 | D7 strict 第十五批：debugging/terminal_review + 调试/终端核查调用方 |
| `v2-typecheck-16.xml` | 25 | D7 strict 第十六批：objects + 对象/捕获调用方 |
| `v2-typecheck-17.xml` | 39 | D7 strict 第十七批：memory_organization + 记忆整理调用方 |
| `v2-typecheck-18.xml` | 27 | D7 strict 第十八批：exchange_steps + 交换恢复/发布调用方 |
| `v2-typecheck-19.xml` | 40 | D7 strict 第十九批：credentials + 凭据/授权调用方 |
| `v2-typecheck-20.xml` | 32 | D7 strict 第二十批：desktop_actions + 桌面动作/传输/HTTP 调用方 |
| `v2-typecheck-21.xml` | 41 | D7 strict 第二十一批：scheduling + 调度/故障/HTTP 调用方 |
| `v2-typecheck-22.xml` | 62 | D7 strict 第二十二批：delivery/installation/maintenance + 对应调用方 |
| `v2-strict-batches-full.xml` | 4633 | 二十二批后全量：4632 passed / 1 failed（debug_sink 并行时序）；`v2-strict-isolation.xml` 11 项全过 |
| `v2-installer-switch.xml` | 23 | D7 切换执行：switch/install/current + 既有安装器/切换计划/状态调用方 |
| `v2-installer-migrate.xml` | 74 | D7 迁移执行：migrate/switch/install/current + 安装器/切换计划/状态/维护合同调用方 |
| `v2-installer-uninstall.xml` | 21 | D7 候选卸载：uninstall + 安装器/架构调用方 |
| `v2-installer-environment.xml` | 24 | D7 离线环境：environment + 安装器/卸载/切换/架构调用方 |
| `v2-installer-venv-launch.xml` | 20 | D7 运行选择：venv 启动选择 + environment/安装器/卸载/架构调用方 |
| `v2-installer-live-rollback.xml` | 24 | D7 live 回滚：rollback + 安装器/卸载/切换/架构调用方 |
| `v2-installer-status.xml` | 19 | D7 状态投影：status + 安装器/架构调用方 |
| `v2-installer-purge.xml` | 22 | D7 显式清理：purge-data + 安装器/架构调用方 |
| `v2-installer-final-full.xml` | 4654 | 安装器链后全量：4652 passed / 2 failed（debug_sink 并行时序 + 已修复文档守卫）；`v2-installer-final-isolation.xml` 13 项全过 |
| `v2-typecheck-23.xml` | 168 | D7 strict 第二十三批：reference_release + 引用释放/保留调用方 |
| `v2-typecheck-24.xml` | 73 | D7 strict 第二十四批：service_activation + 服务控制/部署/激活调用方 |
| `v2-typecheck-25.xml` | 7 | D7 strict 第二十五批：terminals + 终端 HTTP/生命周期调用方 |
| `v2-typecheck-26.xml` | 27 | D7 strict 第二十六批：skills + 技能调用方 |
| `v2-typecheck-27.xml` | 13 | D7 strict 第二十七批：schema_upgrade + schema release 调用方 |
| `v2-typecheck-28.xml` | 18 | D7 strict 第二十八批：routing/settings + 路由调用方 |
| `v2-typecheck-29.xml` | 23 | D7 strict 第二十九批：retention/memory_organization + 对应调用方 |
| `v2-typecheck-30.xml` | 35 | D7 strict 第三十批：service_isolation + 服务交换/控制调用方 |
| `v2-typecheck-31.xml` | 27 | D7 strict 第三十一批：documents + 文档/故障调用方 |
| `v2-typecheck-32.xml` | 25 | D7 strict 第三十二批：android_unicode + Android Unicode 调用方 |
| `v2-typecheck-33.xml` | 45 | D7 strict 第三十三批：credential_ipc + 原生凭据调用方 |
| `v2-typecheck-34.xml` | 45 | D7 strict 第三十四批：contracts/history + 历史合同/调用方 |
| `v2-typecheck-35.xml` | 16 | D7 strict 第三十五批：local_vectors + 本地向量调用方 |
| `v2-typecheck-36.xml` | 17 | D7 strict 第三十六批：service_operations + 服务操作调用方 |
| `v2-typecheck-37.xml` | 19 | D7 strict 第三十七批：configuration + 配置调用方 |
| `v2-typecheck-38.xml` | 27 | D7 strict 第三十八批：service_cleanup + 服务清理调用方 |
| `v2-typecheck-39.xml` | 13 | D7 strict 第三十九批：action_review + 动作复核调用方 |
| `v2-typecheck-40.xml` | 20 | D7 strict 第四十批：qq + OneBot 调用方 |
| `v2-typecheck-41.xml` | 21 | D7 strict 第四十一批：images + 图像上传/附接/预算调用方 |
| `v2-typecheck-42.xml` | 89 | D7 strict 第四十二批：audit/event retention + 合同/HTTP/UI 调用方 |
| `v2-typecheck-43.xml` | 45 | D7 strict 第四十三批：bilibili + 域/HTTP/UI/worker/WBI 调用方 |
| `v2-typecheck-44.xml` | 109 | D7 strict 第四十四批：service_exchange + 交换/恢复/清理调用方 |
| `v2-typecheck-45.xml` | 54 | D7 strict 第四十五批：desktop_actions + 桌面/前台/语义调用方 |
| `v2-typecheck-46.xml` | 26 | D7 strict 第四十六批：media + 媒体/Bilibili 调用方 |
| `v2-typecheck-batches-46-full.xml` | 4654 | 四十六批后全量：4651 passed / 3 failed（并行时序）；`v2-typecheck-batches-46-isolation.xml` 3 项全过 |
| `v2-typecheck-batches-50-full-fixed.xml` | 4654 | 五十批后全量：4652 passed / 2 failed（MCP 工作台并行时序）；`v2-typecheck-batches-50-mcp-isolation.xml` 2 项全过 |
| `v2-typecheck-47.xml` | 54 | D7 strict 第四十七批：desktop_portal + portal 主机/原生/服务/帧/UI 调用方 |
| `v2-typecheck-48.xml` | 97 | D7 strict 第四十八批：releases + 发布/交换/迁移调用方 |
| `v2-typecheck-49.xml` | 101 | D7 strict 第四十九批：service_admin + 管理员/交接/所有权调用方 |
| `v2-typecheck-50.xml` | 72 | D7 strict 第五十批：android + Android HTTP/模型/UI/Unicode 调用方 |
| `v2-typecheck-51.xml` | 24 | D7 strict 第五十一批：mcp + MCP 核心/流程/OAuth 调用方（UI 2 例另过） |
| `v2-typecheck-52.xml` | 92 | D7 strict 第五十二批：extensions + 扩展合同/HTTP/包/仓库/宿主/崩溃调用方 |
| `v2-typecheck-53.xml` | 116 | D7 strict 第五十三批：service_deployment + 部署/文件/控制/注册/身份调用方 |
| `v2-typecheck-54.xml` | 141 | D7 strict 第五十四批：desktop + 桌面/合同/捕获/原生/观察/HTTP/前台调用方（domain 53/53） |
| `v2-typecheck-55.xml` | 66 | D7 strict 第五十五批：application/service_exchange + 交换/系统/达林/恢复调用方 |
| `v2-typecheck-56.xml` | 20 | D7 strict 第五十六批：application/memory_semantics + 记忆语义/HTTP/迁移调用方 |
| `v2-typecheck-57.xml` | 33 | D7 strict 第五十七批：application/bilibili + B 站 HTTP/worker/gateway 调用方 |
| `v2-typecheck-58.xml` | 21 | D7 strict 第五十八批：application/images + 图像/HTTP/迁移调用方 |
| `v2-typecheck-59.xml` | 24 | D7 strict 第五十九批：application/configuration + 配置/HTTP/故障调用方 |
| `v2-typecheck-60.xml` | 19 | D7 strict 第六十批：application/qq + QQ/HTTP 调用方（含 parse_event 回归修复） |
| `v2-typecheck-61.xml` | 20 | D7 strict 第六十一批：application/maintenance + 维护/HTTP/迁移调用方 |
| `v2-typecheck-62.xml` | 65 | D7 strict 第六十二批：application/model_calls + 模型流/上下文/路由/执行调用方 |
| `v2-typecheck-63.xml` | 28 | D7 strict 第六十三批：application/documents + 文档/HTTP/故障调用方 |
| `v2-typecheck-64.xml` | 20 | D7 strict 第六十四批：application/context + 上下文/故障调用方 |
| `v2-typecheck-65.xml` | 67 | D7 strict 第六十五批：application/service_control + 控制/排空/启动调用方 |
| `v2-typecheck-66.xml` | 43 | D7 strict 第六十六批：application/releases + service_operations + 发布/操作调用方 |
| `v2-typecheck-67.xml` | 26 | D7 strict 第六十七批：application/mcp + MCP 核心/流程/OAuth/架构调用方 |
| `v2-typecheck-68.xml` | 108 | D7 strict 第六十八批：application/extensions + 扩展合同/HTTP/包/仓库/宿主/崩溃调用方 |
| `v2-typecheck-69.xml` | 33 | D7 strict 第六十九批：application/delivery + 投递准入/发送/结算/故障调用方 |
| `v2-typecheck-70.xml` | 45 | D7 strict 第七十批：application/terminals + 终端生命周期/HTTP/存储/原生/核查调用方 |
| `v2-typecheck-71.xml` | 103 | D7 strict 第七十一批：application/engine + 运行/路由/上下文/模型流/运维调用方 |
| `v2-typecheck-72.xml` | 96 | D7 strict 第七十二批：application/desktop + 桌面观察/目标/验证/捕获/动作调用方 |
| `v2-typecheck-73.xml` | 40 | D7 strict 第七十三批：application/desktop_portal + 窗口共享授权/会话/捕获调用方 |
| `v2-typecheck-74.xml` | 15 | D7 strict 第七十四批：application/media + 媒体源/任务/转写/帧表调用方 |
| `v2-typecheck-75.xml` | 36 | D7 strict 第七十五批：application/skills + 技能导入/编辑/启用/回滚/披露调用方 |
| `v2-typecheck-76.xml` | 76 | D7 strict 第七十六批：application/ports + B 站/Android/Unicode/架构调用方 |
| `v2-typecheck-77.xml` | 29 | D7 strict 第七十七批：六个 adapters 模块 + 适配器/存储/架构调用方 |
| `v2-typecheck-78.xml` | 64 | D7 strict 第七十八批：token_counter 与三个持久化适配器 + token/投递/存储调用方 |
| `v2-typecheck-79.xml` | 29 | D7 strict 第七十九批：四十个零错误 adapters 模块（含冻结迁移）+ 架构/存储调用方 |
| `v2-typecheck-80.xml` | 59 | D7 strict 第八十批：十一个小型 adapters 模块 + wheel/历史/运维/存储调用方 |
| `v2-typecheck-81.xml` | 68 | D7 strict 第八十一批：repository_base 与 wheel 准备/备份/回滚/卸载 + 安装链调用方 |
| `v2-typecheck-82.xml` | 119 | D7 strict 第八十二批：保留/离线计划/捕获/macOS 辅助适配器 + 捕获/保留/维护调用方 |
| `v2-typecheck-83.xml` | 99 | D7 strict 第八十三批：paths/管理员工件/macOS 服务查询 + macOS/管理员/架构调用方 |
| `v2-typecheck-84.xml` | 21 | D7 strict 第八十四批：终端 worker 与历史保护 + 终端/历史调用方 |
| `v2-typecheck-85.xml` | 96 | D7 strict 第八十五批：SQLAlchemy 访问/X11 授权/桌面 worker + 存储/X11/桌面调用方 |
| `v2-typecheck-86.xml` | 29 | D7 strict 第八十六批：执行保留计划与 ConPTY 启动门 + 终端/保留/架构调用方 |
| `v2-typecheck-87.xml` | 181 | D7 strict 第八十七批：对象清单与引用释放计划 + 引用释放/对象/存储调用方 |
| `v2-typecheck-88.xml` | 25 | D7 strict 第八十八批：图像编解码适配器 + 图像/架构调用方 |
| `v2-typecheck-89.xml` | 46 | D7 strict 第八十九批：系统凭据适配器 + 凭据/授权/架构调用方 |
| `v2-typecheck-90.xml` | 81 | D7 strict 第九十批：launchd 状态比较 + macOS 服务/管理员工件/架构调用方 |
| `v2-typecheck-91.xml` | 34 | D7 strict 第九十一批：管理员交接适配器 + 服务交换/架构调用方 |
| `v2-typecheck-92.xml` | 46 | D7 strict 第九十二批：凭据读写适配器 + 凭据/授权/架构调用方 |
| `v2-typecheck-93.xml` | 33 | D7 strict 第九十三批：技能包解析适配器 + 技能/架构调用方 |
| `v2-typecheck-94.xml` | 30 | D7 strict 第九十四批：服务控制日志适配器 + 服务控制/操作/架构调用方 |
| `v2-typecheck-95.xml` | 30 | D7 strict 第九十五批：一次性启动预留 + 服务控制/操作/架构调用方 |
| `v2-typecheck-97.xml` | 63 | D7 strict 第九十七批：进程所有权 + 桌面辅助/服务查询调用方 |
| `v2-typecheck-98.xml` | 112 | D7 strict 第九十八批：离线审计/事件裁剪 + 计划契约调用方 |
| `v2-typecheck-99.xml` | 51 | D7 strict 第九十九批：工作进程客户端 + 宿主回调调用方 |
| `v2-typecheck-100.xml` | 61 | D7 strict 第一百批：服务交换/清理文件边界 + 调用方 |
| `v2-typecheck-96.xml` | 8 | D7 strict 第九十六批：冻结迁移 v0003 + 迁移冻结/架构调用方 |
| `v2-typecheck-batches-53-full-n2.xml` | 4654 | 五十三批后全量（-n 2）：4653 passed / 1 failed（debug_sink 并行时序）；`v2-typecheck-batches-53-debug-isolation.xml` 1 项通过 |
| `v2-typecheck-batches-full-fixed.xml` | 4654 | strict 批次后全量：4653 passed / 1 failed（debug_sink 并行时序）；`v2-typecheck-batches-isolation.xml` 11 项全过 |
| `v2-installer-full.xml` | 4624 | 首次全量：4621 passed / 3 failed（架构违规 + debug_sink + desktop observation），架构已在 1a13f3c 修复 |
| `v2-packaging-guard.xml` | 2 | D7 打包：构建门禁 V2 检查项与文档边界守卫 |
| `v2-packaging-callers.xml` | 57 | D7 打包：发布/部署/drain/schema 恢复调用方 |
| `v2-d7-full.xml` | 4601 | D7 节点后全量：4599 passed / 2 failed（并行时序）；`v2-d7-isolation.xml` 2 项全过 |
| `v2-mcp-final.xml` + `-ui` + `-callers` + `-guards` | 150 | 上一批 MCP，共享节点以扩展报告的重跑结果为准 |

门禁 `temp/build/v2-owner-token-ui-gates.json`（ruff / format / prettier / pyright / 项目索引 / 任务摘要 全 exit 0，pyright 5 文件 0 错误）。令牌工作台 16 项节点回归。

安装器节点后全量 `temp/build/v2-installer-full-fixed.xml`：**4624 项，4623 passed / 1 failed / 0 errors**（`debug_sink` 并行时序）；同源码隔离 `temp/build/v2-installer-isolation.xml` 11 项全过。首次全量 `v2-installer-full.xml` 的 3 项失败中，`test_v2_architecture` 为真实分层违规（sqlite3 在 persistence 之外），已在 `1a13f3c` 修复并在第二次全量确认；另两项隔离通过。**没有全量全绿结论**：没有主包重建、远程 CI、Mac/Linux、真实账号设备、真实第三方插件、真实市场源、负载或 72 小时结论。

二十二批后全量 `temp/build/v2-strict-batches-full.xml`：**4633 项，4632 passed / 1 failed / 0 errors**（仍为 `debug_sink` 并行时序）；同源码隔离 `temp/build/v2-strict-isolation.xml` 11 项全过；strict 门禁 41 文件 0 errors。证据 `docs/evidence/v2-strict-batches-verification.json`。**没有全量全绿结论**，未跑主包重建、远程 CI、Mac/Linux、真实账号设备、真实第三方插件或 72 小时。

基线修复批 `docs/evidence/v2-baseline-repair.json`（测试-only，`5f3b46e`）：26 项历史失败全部修复，126 项专项全过；修复当时全量 4574 passed / 2 failed，最终全量如上报 4580 passed / 3 failed，均为并行时序并隔离通过。产品代码未改、未放宽断言。**没有全量全绿结论**：没有主包重建、远程 CI、Mac/Linux、真实账号设备、真实第三方插件、负载或 72 小时结论。

开放问题（不要因一次重跑通过就标记修复）：

- `UI.exact_organization_refresh_timing`：旧精确整理 UI 一次刷新显示 queued；单节点复核通过，未判定稳定。报告 `temp/build/v2-memory-semantics-final.xml`、`-repair.xml`。
- `tests.parallel_run_timing_flakes`：全量并行下 `test_v2_service_runtime::test_identity_tracks_real_boot_and_drains_before_releasing_lock`、`test_v2_debug_sink`、`test_v2_portal_ui` 各一处时序失败；同源码隔离运行 **36 项全过**，未判定产品缺陷，不以放宽断言处理。报告 `temp/build/v2-callbacks-final-full.xml`、`-isolation.xml`。
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
- 期望：索引 `modules=65 files=717 routes=430 declared_tools=94`，`stale=` 为空。
- 期望：`docs/tasks/v2.json` 的 `next_checkpoint.id == "D7.product_switch"`（D6.extensions 离线节点已完成，D7 能力报告、令牌管理、业务清单、导航收敛、strict 十九批、Windows 打包验证、全量复核与安装收据/运行时/产物/暂存/解包/切换计划/更新备份/回滚演练/状态汇总/候选冒烟已完成），`current_validation.targeted_unique_passed == 228`、`includes_browser_cases == 10`、`current_full_passed == false`（安装器节点后全量 4623 passed / 1 failed，debug_sink 并行时序隔离 11 项全过；基线 26 项已修复，见 `docs/evidence/v2-baseline-repair.json`；D7 证据 `docs/evidence/v2-capabilities.json`、`docs/evidence/v2-owner-token.json`、`docs/evidence/v2-business-inventory.json`、`docs/evidence/v2-typecheck.json`、`docs/evidence/v2-packaging.json`、`docs/evidence/v2-d7-verification.json`、`docs/evidence/v2-installation.json`、`docs/evidence/v2-installer-verification.json`）。
- 若数字不同：先判断是否有并行会话提交，再决定是否重算索引/摘要；不要改历史证据。







