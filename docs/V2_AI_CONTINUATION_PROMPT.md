# 可直接复制给下一 AI 的接续提示词

以下正文可整段复制。默认下一 AI 能访问本机工作区；换机器时同时提供 `V2_AI_HANDOFF.md`、源码恢复包与 manifest，并把路径改为实际位置。

---

你接手的是 OmniOperator V2 的持续重构开发。请实际读代码并接着实现，不要只回复计划，也不要重做已完成的基础系统。

工作区：`D:\xm\OmniOperator`，Windows / PowerShell。分支 `codex/architecture-v2`（上游 `origin/codex/architecture-v2`，gitee）。MCP 批及其前累计成果已提交推送（MCP 代码提交 `8ad22cf`、`bf5f91f`、`564f0db`、`8590144`、`320549f`）；接手先 `git log --oneline -5`、`git status --porcelain`、`git ls-remote origin refs/heads/codex/architecture-v2` 对账，保留现状，不 reset/clean/批量覆盖或删除未跟踪文件。

**按顺序读：**

1. `AGENTS.md`、`docs/HANDOFF.md`、`docs/ARCHITECTURE.md`、`CONTRIBUTING.md`。
2. `docs/V2_AI_HANDOFF.md`（完整交接，含架构合同、目录地图、MCP 先例锚点）、`docs/tasks/v2.json`、`docs/DEVELOPMENT_STATUS.md`。
3. `docs/V2_REFACTOR.md` 的当前摘要段、`docs/evidence/v2-mcp.json`；`V2_REFACTOR.md` 历史段落只作历史。
4. 本批再读 `docs/V2_MCP.md`、`docs/MCP.md`（**V1 行为先例，只读参考，禁止 import**）、`docs/V2_MEDIA.md`、`docs/V2_SKILLS.md`、`docs/DATA_MODEL.md`、`docs/TESTING.md`、`docs/UI_DESIGN_SYSTEM.md`。

**先核对这些事实，不一致时以源码与台账为准：**

- V2 未完成，默认正式入口仍是 V1；独立入口 `uv run --no-sync python -m omni.composition.cli --home <全新绝对目录> serve`，默认 `127.0.0.1:8766`。
- 结构 **136 表 / `v2_0037`**，前端资源标识 **`20261110v49`**，索引 **65 模块 / 715 文件 / 427 路由 / 94 声明工具**。
- 最新证据 `docs/evidence/v2-mcp.json`：150 个专项节点最新全过（MCP 专项 20 + 390/1024 浏览器 2 + 调用方 54 + 守卫 74），Pyright 0 错；同源码另跑全量 **4541 passed / 16 failed / 10 errors**，26 个失败/错误均为基线 `be58739` 遗留并已复现记录。**这不是全量通过、全仓严格类型或三平台验收。**
- 源码恢复：`temp/private/v2-mcp-source-20261111-474c67827a9f/manifest.json` 与同目录 ZIP（ZIP SHA `51a7fc10…`）。complete 只表示可恢复，文档晚于封存。
- 开放问题不得自行销账：`UI.exact_organization_refresh_timing` 待复核；`tests.baseline_failures_pre_mcp` 待所属批次处置（history/event-pruning、schema-release、service 启动效果、extensions UI 等）；`security.sensitive_history_services_acceptance` 待所有者处置（工作区 `docs/evidence/services-acceptance-2026-11-01.json` 仍含 1 条历史字面量，MCP 批新增文件零命中）。
- Windows 前台实测用户已安排“稍后”，Mac 待 Mac mini 编译，Linux 无实机；不要用弹测试窗口阻塞开发。

**本轮首要任务：D6.extensions —— 完整扩展链（进程外宿主回调、产物协议、版本/授权、市场更新提示与实际安装）。** D6.mcp 运行适配器离线链已收口（stdio/Streamable HTTP、显式 OAuth、目录指纹、逐工具授权、工作台），不再当断点；真实外部 MCP 服务与真实 OAuth 供应商仍记“待真实验收”。

已核实的起点（不要再重复调研成结论）：MCP 已交付 `domain/mcp.py`、`application/mcp.py`、`adapters/mcp/`、`persistence/mcp_repository.py`、`v0037`、工作台分区与 11 个 HTTP 端点；扩展侧已有可信目录封存、显式授权、进程外工具、指纹漂移撤权与按回合披露（`application/extensions.py`、`adapters/extensions/`、`V2_EXTENSIONS` 合同），但**尚无产物协议、宿主回调、版本升级与市场安装链**。

按此范围实现：**扩展产物协议（受控对象/校验/清理）、进程外宿主回调与生命周期、版本与授权升级、市场更新提示与实际安装（明确确认、失败不自动重试）；复用既有对象/审批/费用与 Agent 隔离。**

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

扩展链本批至少覆盖：产物协议（受控对象、校验、引用/GC、删除传播）真实链路；进程外宿主回调与崩溃/超时/取消/unknown；版本升级与授权再确认、旧授权不复活；市场更新提示只作提示，实际安装要显式确认、失败不自动重放；凭据不出现在文本/HTTP/日志/审计；`test_v2_architecture.py`、worker 进程守卫、迁移冻结、`test_v2_http.py` 契约；新分区 390/1024 浏览器流程 + 一次工作台回归。真实市场源与真实第三方扩展另记“待真实验收”。

**每完成一个节点，按用户要求 git 提交并推送一次**（先 `git status` 核对范围，临时/私人材料不入库）。每批收口：更新台账三个状态、本文当前段与领域合同、写入 `docs/evidence/v2-<批次>.json`（报告 + SHA + 门禁 + 限制）、保留恢复副本、记录实际源码指纹；保留历史报告与开放问题，不把小批完成写成完整 D6/V2 完成。基线遗留失败按所属批次修复并在证据中分立记录，不要算进本批通过数。

真实付费、外发、发布、提权、设备输入、系统安装或部署仍按既有授权规则；普通已授权开发不反复询问。不要因为后续需要真实验收就停下当前可完成的工作。

先用一段简短说明报告你核对出的当前断点与工作区状态，然后开始写代码。只有用户要求重新规划、出现无法绕开的缺失信息或需要具体真实操作授权时才停下来询问。
