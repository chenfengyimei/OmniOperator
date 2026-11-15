# 可直接复制给下一 AI 的接续提示词

> 静态质量接续（2026-11-15）：源码`92fbf78a`：既有15项I001、39个Python格式文件、11个前端格式文件已清零；53份Python业务AST/导入绑定及26份前端格式/版本变更已核对。Ruff/Prettier全范围、strict332零错误、85模块语法、61专项及wheel/sdist隔离导入通过，资源v129。当前固定源码的第78次全量（-n 2，artifact218）运行中，结果待收割。10项观察仍开放，整个V2未验收、默认V1。见[静态质量证据](evidence/v2-static-quality.json)。

> 稳定性历史记录（第77次全量）：源码`e5910a95`：模型请求头类型回归2→0（strict332文件），崩溃后文件调度已永久修复；模型47项、并行29项专项通过。全量第77次：4680 passed, 10 warnings in 2790.27s (0:46:30)；JUnit 0 failure / 0 error，4680个唯一节点；标准调度完成，无手动恢复。10项观察开放；当时有15项I001、39个Python与11个前端格式欠账，已在后续静态质量批次清理。整个V2未验收、默认V1。见[稳定性证据](evidence/v2-test-stability.json)。

> UI v128冻结记录（第76次全量）：手动连接接管、旧流程屏障与后台恢复已修复，89项专项通过。固定源码`4b6c0795`，pytest原始汇总4665 passed / 9 failed，4278.50s；JUnit分为6 failure和3 worker error，4671项收集/4674条记录含3次崩溃重放；调度恢复后完成。11项观察开放（原8项保留）；332文件类型收口不重做，V2未整体验收、默认V1。详见[UI证据](evidence/v2-ui.json)。

> **当前版本请用 `docs/V2_HANDOFF_PROMPT_2026-11-14.md`**（2026-11-15，332文件类型收口已完成，继续UI稳定性与真实验收）。
> 本文保留作历史与 D6/D7 全景参考，数字已同步但结构较冗长。

以下正文可整段复制。默认下一 AI 能访问本机工作区；换机器时同时提供 `V2_AI_HANDOFF.md`、源码恢复包与 manifest，并把路径改为实际位置。

---

你接手的是 OmniOperator V2 的持续重构开发。请实际读代码并接着实现，不要只回复计划，也不要重做已完成的基础系统。

工作区：`D:\xm\OmniOperator`，Windows / PowerShell。分支 `codex/architecture-v2`（上游 `origin/codex/architecture-v2`，gitee）。MCP/扩展链、基线修复、D7 能力报告/令牌管理/业务清单已提交推送（`8ad22cf`…`e74572d`、`1eaf6a9`、`ed5912a`、`560387d`、`9133001`、`9efa947`、`9d9153d`、`91c8c6f`、`5640983`、`fc3495f`、`af4a347`、`b6f34cf`、`a325e72`、`ae5d4fb`、`c291234`、`805f2bb`、`dcb1f5b`、`4042f66`、`1914acf`、`5b6a78e`、`7b3f535`、`12c9fe0`、`a67715b`、`6e21875`、`a2de461`、`5833664`、`91a9e74`、`5777280`、`982ad6d`、`1475e64`、`f094c37`、`e5ce437`、`727e5c6`、`56aacde`、`1eeb60f`、`219f646`、`1e67fb8`）；接手先 `git log --oneline -5`、`git status --porcelain`、`git ls-remote origin refs/heads/codex/architecture-v2` 对账，保留现状，不 reset/clean/批量覆盖或删除未跟踪文件。

**按顺序读：**

1. `AGENTS.md`、`docs/HANDOFF.md`、`docs/ARCHITECTURE.md`、`CONTRIBUTING.md`。
2. **`docs/V2_HANDOFF_2026-11-14.md`（strict 任务当前权威完整交接：已核验事实、24 模块实测路线图、施工规范、类型化实战要点、验收口径、红线）**、`docs/V2_AI_HANDOFF.md`（架构合同、目录地图、扩展/MCP 先例锚点）、`docs/tasks/v2.json`、`docs/DEVELOPMENT_STATUS.md`。
3. `docs/V2_REFACTOR.md` 的当前摘要段、`docs/evidence/v2-extension-artifacts.json`；`V2_REFACTOR.md` 历史段落只作历史。
4. 本批再读 `docs/V2_BUSINESS_INVENTORY.md`、`docs/V2_OWNER_TOKENS.md`、`docs/V2_CAPABILITIES.md`、`docs/V2_EXTENSIONS.md`、`docs/V2_MCP.md`、`docs/MCP.md`（**V1 行为先例，只读参考，禁止 import**）、`docs/DATA_MODEL.md`、`docs/TESTING.md`、`docs/UI_DESIGN_SYSTEM.md`。

**先核对这些事实，不一致时以源码与台账为准：**

- V2 未完成，默认正式入口仍是 V1；独立入口 `uv run --no-sync python -m omni.composition.cli --home <全新绝对目录> serve`，默认 `127.0.0.1:8766`。
- 结构 **136 表 / `v2_0037`**，前端资源标识 **`20261115v129`**（V2 工作台直接复用 V1 界面：原样复制 V1 三份 CSS 到 `web/ui/v2/v1/`，结构改用 V1 类名，自创样式降为桥接层），索引 **65 模块 / 736 文件 / 434 路由 / 94 声明工具**。副本除页面标识与资源路径外，还必须把 `#input`→`#message`、`#send`→`#send-btn`（V1 的 `#send` 是按钮、V2 是 form，不映射会让按钮渐变涂满输入区）。对话区只留消息与输入框：调用链挂助手正文上方且只列真实工具步骤，档位/模型计量与保留操作在右栏「运行」页签（页签共 5 个，必须 `flex:0 0 auto`）。工具条分 `.dock-picks`（两个 `.pick-group`）+ `.dock-actions` 两层。窄屏左右两栏都是抽屉，断点统一 900，页头承载两个入口。详见 `docs/V2_UI_PROMPT.md` §4 的 19 条踩坑清单与量取方法教训。**本机启动即用**：`GET /api/v1/owner-token/local` 注册在 app 上（router 带全局鉴权会自锁），三重闸门=装配开关+回环IP+同源、拒代理头；前端 bootstrapLocal 自动连接、令牌仅存内存、用户手动操作时让位。其余 API 鉴权不变。**设置中心**：非对话模式工作区 static+flex 满高、settings-content 独立内滚、分类导航吸顶（不再用 fixed 覆盖层）。**不要 display:none 左栏**——#agents 在左栏、two_agents 测试任意页面切 Agent；宽屏保留左栏两列、窄屏左栏 display:contents + #agents 用 grid-row 排顶部独立行（不要 fixed 浮层，会拦截内容点击）。两条硬约束：① 复制 V1 CSS 时**不要抄 frame.css 的 Embed 降级层**（全站唯一性契约）；② 切页入口必须用 `nav[aria-label="工作台"] [data-page="x"]`，裸选择器会匹配到 body。
- 最新证据 `docs/evidence/v2-installer-verification.json`（安装器节点后全量 4624 项：4623 passed / 1 failed，debug_sink 隔离 11 项全过；架构违规已由 1a13f3c 修复）、`docs/evidence/v2-typecheck-batches-verification.json`（旧 strict 批次后全量 4654 项：4653 passed / 1 failed；ToolSpec.binding_id 回归已由 1e67fb8 修复）、`temp/build/v2-typecheck-batches-46-full.xml`（四十六批后全量 4654 项：4651 passed / 3 failed，隔离 3 项全过）、`docs/evidence/v2-installer-purge.json`（显式清理 22 项）、`docs/evidence/v2-installer-status.json`（状态投影 19 项）、`docs/evidence/v2-installer-live-rollback.json`（live 回滚 24 项）、`docs/evidence/v2-installer-venv-launch.json`（venv 运行选择 20 项）、`docs/evidence/v2-installer-environment.json`（离线 venv 24 项）、`docs/evidence/v2-installer-uninstall.json`（候选卸载 21 项）、`docs/evidence/v2-installer-migrate.json`（migrate+重 seal 74 项）、`docs/evidence/v2-installer-switch.json`（switch/install/current 切换执行 23 项）、`docs/evidence/v2-installation.json`（verify/seal + 运行时收据 + wheel 产物校验/暂存/解包/切换计划/更新备份/回滚演练/状态/候选冒烟 34 项）、`docs/evidence/v2-d7-verification.json`（D7 节点后全量 4601 项：4599 passed / 2 failed，隔离 2 项全过）、`docs/evidence/v2-strict-batches-verification.json`（二十二批后全量 4633 项：4632 passed / 1 failed，debug_sink 隔离 11 项全过）、`docs/evidence/v2-typecheck.json`（**当前 strict 308 文件 0 错**：domain 53/53、application 44/44、adapters 198/222、composition 13/13；以下为该文件保留的历史批次记录：strict 257 文件时（domain 53/53、application 43/44、adapters 160/222、composition 1/13）+ 一百三十八批 31 项调用方；全量 4654 项 4629 passed / 25 failed / 0 errors（24 项 UI 根因已修并复跑 154 项全过，最新全量 4650 passed / 4 failed / 0 errors（1 项并行未提交 CSS、3 项负载抖动隔离通过）），含 application 43 / adapters 160 / composition 1 模块）、`docs/evidence/v2-packaging.json`（Windows wheel/sdist 验证 + 57 项安装/更新/恢复调用方）、`docs/evidence/v2-business-inventory.json`（业务清单 3 项守卫 + 导航收敛 11 项）、`docs/evidence/v2-owner-token.json`（令牌管理 38 项 + 令牌工作台 16 项）、`docs/evidence/v2-capabilities.json`（能力报告 17 项）、`docs/evidence/v2-extension-callbacks.json`（宿主回调 93 项 + 最终全量/隔离）、`docs/evidence/v2-extension-market.json`（市场扫描 62 + 工作台 25）、`docs/evidence/v2-extension-artifacts.json`（产物协议 146 + 工作台/API 18）与 `docs/evidence/v2-baseline-repair.json`（26 项基线失败已测试-only修复）；与 MCP 批合并 **228 个不同节点**最新全过（含 10 例浏览器）。**这不是全量全绿、全仓严格类型或三平台验收。**
- 源码恢复：`temp/private/v2-installer-status-source-20261111-3301628964ee/manifest.json` 与同目录 ZIP（ZIP SHA `6fa595ec…`），purge 节点叠加 `4042f66`；strict 第一百三十八批后源码为 `3405c64`。complete 只表示可恢复，文档晚于封存。
- 开放问题不得自行销账：`UI.exact_organization_refresh_timing` 待复核；`tests.parallel_run_timing_flakes` 观察中；`security.sensitive_history_services_acceptance` 待所有者处置（工作区 `docs/evidence/services-acceptance-2026-11-01.json` 仍含 1 条历史字面量，本批新增文件零命中）。
- Windows 前台实测用户已安排“稍后”，Mac 待 Mac mini 编译，Linux 无实机；不要用弹测试窗口阻塞开发。

**D7 当前进度：能力报告、令牌管理（API/CLI/工作台）、业务清单、导航收敛、strict 类型一百八十四批（**308 文件**，application **44/44**、adapters **198/222**、composition **13/13**，P3 持久化收官）、Windows 打包验证/全量复核、安装收据/运行时/产物/暂存/解包/切换计划/更新备份/回滚演练/状态汇总/候选冒烟与切换执行（switch/install/current，运行指针+启动包装）、迁移执行（migrate+重 seal）、候选卸载（uninstall）、离线环境（environment）、venv 运行选择（switch 优先 mode=venv）与 live 回滚（代码指针，不恢复数据库）与 status 运行指针投影与显式清理（uninstall --purge-data）已完成，安装器链后全量 4654 项（4652 passed / 2 failed，隔离 13 项全过）；下一节点为完整安装器/更新器（停服/启动、数据库备份恢复）。** 安装器节点后全量 4624 项：4623 passed / 1 failed（debug_sink 并行时序，隔离 11 项全过）；strict 批次后全量 4654 项：4653 passed / 1 failed（同一并行时序，隔离 11 项全过）；strict 308 文件 0 errors；installation verify/seal、运行时收据、wheel 校验/暂存/解包、只读切换计划、更新备份、回滚演练、只读状态、候选冒烟与切换执行均 fail-closed。剩余 D7 门槛：完整安装/更新/卸载执行链、容器镜像、macOS/Linux 构建安装、干净机器端到端与正式入口切换；真实市场源/第三方插件/MCP 真实服务/三平台原生仍记待真实验收。

已核实的起点（不要再重复调研成结论）：D6 扩展链（产物协议/工作台、市场扫描/工作台、宿主回调）、基线修复、D7 能力报告与令牌管理（API/CLI/工作台）均已提交；`domain/capabilities.py` + `GET /api/v1/capabilities`、`composition/http.py` 的令牌轮换闭包、`web/ui/v2/{capabilities,tokens}.js` 可直接作为 D7 其余节点的结构先例。

按 D7 剩余范围实现：**统一工作台收敛与业务清单（如实标注可用/需配置/未验收）、全仓严格类型（分模块推进并记录例外）、三平台包/容器/安装更新恢复（缺环境记待真实验收）**；每批只改该任务所需内容，专项覆盖调用方，收尾再跑全量。

严格类型的当前断点（2026-11-14 实测，勿重复摸底）：strict **308 文件 0 errors / 0 warnings**（domain 53/53、application **44/44 全层**、adapters **198/222**、composition **13/13 全层**）。**P3 持久化 34 模块已全部纳入（收官，第 184 批 aa294ef）**；下一步 P4 业务大适配器，第 45 节点全量待跑。P2 Android 七个模块全部纳入；P3 持久化已纳入 schema、credential、lease、configuration、repository（24 mixin 聚合根）、delivery、history_pruning_cycle、object_gc、image、context、document、event_retention、memory_organization_predicates、migrations/v0005、maintenance、terminal、memory、bilibili、execution_retention、action_review、media、extension、maintenance_repository、mcp（P3 最大分片 315 错）、qq、schedule、terminal_review、skill、routing、reference_release、memory_semantic、memory_organization，**P3 持久化 34 模块全部纳入（收官）**；剩余 **24 个 adapters，2026-11-14 实测合计 2763 错**，规模已探明（动手前仍应 probe 复测）：**P4 业务大适配器 10 模块 1396 错** = bilibili 350(拆3) / workers/bilibili 300(拆2) / http 177(拆2) / workers/media 99 / onebot 90 / diagnostic_bundle 90 / local_vectors 79 / model_stream 76 / extensions/packages 73 / workers/local_vectors 62；**P5 platform 11 模块 1077 错** = platform/portal 202(拆2) / linux_semantic 159(拆2) / service_registration 116 / linux_atspi 110 / runtime_identity 92 / linux_x11 86 / service_operation_files 84 / workers/portal 71 / service_templates 64 / deployment_files 47 / workers/linux_desktop 46；**P6 MCP 3 模块 290 错** = mcp/oauth 136 / mcp/http 87 / mcp/stdio 67。建议先小后大：P4 从 workers/local_vectors(62) 起，P5 从 workers/linux_desktop(46) 起，P6 从 stdio(67) 起。逐模块建议顺序、每节点施工九步、协议逆变等 13 类实战坑与验收四禁，见 `docs/V2_HANDOFF_2026-11-14.md` §5–§8。**下一全量节点是第 50 节点（第 189 批）**。每节点同步三处：`pyrightconfig.strict.json` include、`docs/TYPE_CHECKING.md` 表格行与 §3 计数、`docs/evidence/v2-typecheck.json`；守卫 `test_v2_typecheck_scope.py` 双向校验配置与文档必须一致。**第 45 节点（P3 收官）全量已收割验收**（-n 2，2819.15s）：4658 项 **4652 passed / 6 failed / 0 errors**，6 败全部归并行会话未提交的「模型接入/连接测试」改版——串行 5 过 1 败、隔离该项通过、干净 HEAD 代表对照 4/4 通过；其中 HTTP 路由契约与导航计数两项串行转绿是**他会中途补齐代码**（非抖动），已如实记录。四份报告留档 `v2-full-184{,-serial,-ui-isolation,-clean-head}.xml`。本批（181～184）无功能回归，并行口径仍记 6 failed。上一节点（第 40 节点）4658 项 4656 passed / 2 failed：第 35 节点的 14 项 `#agents` 与 3 项 CSS 守卫**已全部转绿**（UI 会话修复，两项开放项关闭）；剩 2 项均为抖动——投递归档串行与干净 HEAD 均过，`test_v2_portal_ui` 授权属**既有 flaky**（开放项 `test.portal_ui_flaky_on_clean_head`，主工作区复跑 2 次 + 干净 HEAD 3 次全过）。

可直接照抄结构的先例（不照抄业务）：
- `application/extensions.py`：`authorize` / `disable` / `tools_for_run` / `invoke` / `review_quiescent`；invoke 前比对 `arguments_sha256`，`before_dispatch` 二次校验，漂移码 `extension_snapshot_drift`。
- `adapters/extensions/hosts.py`：`MAX_HOSTS=4`、`MAX_PENDING=64`、`revoke(..., authority_epoch, timeout_s)`、`health()`、`stop_accepting/close`。
- `adapters/workers/client.py` + `adapters/platform/processes.py`：`OwnedProcess.spawn/terminate`，stderr 排空，有界 invoke 与 `review_quiescent`。
- `application/engine.py` 第 224–230 行：每回合合并 `tools_for_run` 的 spec；`Capability(spec, adapter)`、`ToolSpec(writes, timeout_s, resource, binding_id)`。
- 凭据：`AsyncSecretPort.get(reference)` 只在适配器取用，引用字符集 `[a-zA-Z0-9_.:/-]{1,200}`；`FileVault` 主密钥必须在目录外。

硬性要求：
- stdio 子进程有界、可取消、崩溃即销毁；in-flight 调用归 **unknown**，不自动重放写操作。HTTP 有界超时与取消，区分 failed/unknown，不无限重试。
- OAuth 只在用户显式发起时进行（PKCE/state 限本次请求、回调来源白名单、令牌入 vault）；遇到 401 不自动开授权；换账号/机器需重新授权。
- 目录指纹 = 工具名 + `inputSchema` 的稳定哈希（不含 description）；变化即撤销旧授权并要求显式确认；未授权工具对模型不可见；旧页面不能批准新指纹。
- 只读白名单只能由所有者显式声明（不得依据服务自报 `readOnlyHint`），且是已授权工具的子集；未声明按写处理、走审批。
- 模型只读 `ActionResult.text`：带来源服务与工具名，≤12000 字符；不回传凭据、完整异常或原始外部载荷；外部描述/结果都是不可信数据，不授予权限、不自动打开链接。
- 每回合按 run 封存可用工具，运行中撤权后不得用旧授权派发。
- 数据层用**新迁移 v0037**（不改 v0036）：服务运行态与代际、目录快照/指纹、逐工具授权、调用与 unknown 记录；沿用触发器风格（状态转移白名单、终态不可变、跨 Agent 组合外键）。
- 新分区接 `web/ui/v2/index.html` 的 nav 与页面容器、`main.js` 的 mount/load/switch，版本串**整组原子递增**；状态被动可读，连接/断开/重置先确认目标，失败不自动重放，390/1024 可用。
- 保持四层：入口适配器 → 应用服务 → 领域与端口；基础设施实现端口；composition 统一构造与关闭。领域无 I/O；仓储不建表不自连。
- V2 是单实例、单所有者、多 Agent、单一 `omni.db`；不迁 V1 数据、不双写、不加兼容层。

**执行偏好：开发优先，持续推进同一交付包。** 不每步跑全量、不重打包、不跑三目标类型；完成一条生产链后集中验证受影响模块与调用方：

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/test_<相关模块>.py
uv run --no-sync pytest -q -n 0 agent/tests/test_<相关模块>.py::test_<行为>
uv run --no-sync pytest -q -n 0 --lf --last-failed-no-failures=none
```

`addopts` 已含 `-n auto --dist=loadfile`，不要加 `-p no:xdist`；`-n 0` 仅用于单文件调试。门禁按改动文件跑 ruff check/format、`uv run --no-sync pyright`、`node web/node_modules/prettier/bin/prettier.cjs --check`；改文件后跑 `scripts/project_index.py` 与 `scripts/task_status.py` 的生成与 `--check`。不 `uv sync`，不为测试停止服务，不用真实账号/设备数据，不跳过闸门或缩短被测超时。UI 每用例独立 BrowserContext/数据库；重型类型、浏览器、打包顺序执行。

扩展链下一节点至少覆盖：受控宿主回调（插件请求宿主能力仍受授权/预算约束，失败归unknown不重放）；版本更新提示与实际安装（真实来源状态如实显示、显式确认、失败不自动重试、旧授权不复活）；产物在工作台可见（列表/类型/大小/读取入口，模型文本仍只含类型与字节数）；`test_v2_architecture.py`、worker 进程守卫、迁移冻结、`test_v2_http.py` 契约；相关分区 390/1024 浏览器流程 + 一次工作台回归。真实市场源、真实第三方扩展与三平台原生 ABI 另记“待真实验收”。

**每完成一个节点，按用户要求 git 提交并推送一次**（先 `git status` 核对范围，临时/私人材料不入库）。每批收口：更新台账三个状态、本文当前段与领域合同、写入 `docs/evidence/v2-<批次>.json`（报告 + SHA + 门禁 + 限制）、保留恢复副本、记录实际源码指纹；保留历史报告与开放问题，不把小批完成写成完整 D6/V2 完成。基线遗留失败按所属批次修复并在证据中分立记录，不要算进本批通过数。

真实付费、外发、发布、提权、设备输入、系统安装或部署仍按既有授权规则；普通已授权开发不反复询问。不要因为后续需要真实验收就停下当前可完成的工作。

先用一段简短说明报告你核对出的当前断点与工作区状态，然后开始写代码。只有用户要求重新规划、出现无法绕开的缺失信息或需要具体真实操作授权时才停下来询问。

