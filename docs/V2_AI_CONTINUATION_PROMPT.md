# 可直接复制给下一 AI 的接续提示词

以下正文可以整段复制。默认下一 AI 能访问本地工作区；若换机器，请同时提供 `V2_AI_HANDOFF.md`、源码恢复包及 manifest，并将工作区路径改为实际位置。

---

你接手的是 OmniOperator V2 的持续重构开发。请实际阅读仓库、接着实现，不要只回复计划，也不要重做已经完成的基础系统。

工作区：`D:\xm\OmniOperator`，Windows / PowerShell。当前分支 `codex/architecture-v2`。媒体批次及其前累计的 V2 成果已按用户要求提交并推送 gitee（origin）；接手时先 `git log --oneline -5` 与 `git status` 对账工作区状态，保留现状，不 reset/clean/批量覆盖或删除未跟踪文件。

**先按顺序阅读：**

1. `AGENTS.md`、`docs/HANDOFF.md`、`docs/ARCHITECTURE.md`、`CONTRIBUTING.md`。
2. `docs/V2_AI_HANDOFF.md`（本次完整交接）、`docs/tasks/v2.json`、`docs/DEVELOPMENT_STATUS.md`。
3. `docs/V2_REFACTOR.md` 当前摘要与 `docs/evidence/v2-media.json`；历史实施记录不作为当前待办。
4. 按本批需要读 `docs/DATA_MODEL.md`、`docs/UI_DESIGN_SYSTEM.md`、`docs/TESTING.md`、`docs/V2_MEDIA.md`、`docs/V2_BILIBILI.md`、`docs/V2_SKILLS.md`。

先核验以下事实，发现不一致时与当前源码/台账对账，不自动恢复旧文件：

- **V2 未完成，默认正式入口仍为 V1**；独立 V2 入口是 `uv run --no-sync python -m omni.composition.cli --home <全新绝对目录> serve`。现在不要切换默认入口或归档仍在使用的 V1。
- 当前结构 **129 表 / `v2_0036`**，前端 **`20261110v48`**。
- 最新媒体批次固定源码指纹 `b505d4b2a9a251c57cf357cbdc0b3731ff0e109165e15f59e001f71490d51b21`，1165 份冻结文件；最新证据是 `docs/evidence/v2-media.json`。
- 媒体批次 133 个不同专项节点的最新结果通过，含 75 例浏览器（媒体面板 2 例 + 工作台全量回归 73 例）；13 份生产文件默认 Pyright 及相关 Ruff/排版通过。**没有当前全量、全仓严格类型、主包重建、三平台或 72 小时通过结论。**
- 可恢复源码：`temp/private/v2-media-source-20261110-b505d4b2a9a2/manifest.json`，同目录 `source-and-evidence.zip`。备份 complete 不代表产品 complete。
- 旧开放问题 `UI.exact_organization_refresh_timing` 仍待复核，不能因单节点重跑通过就标为已修复。
- Windows 前台实测用户已说“稍后验收，继续开发”；Mac 待 Mac mini 编译，Linux 尚无实机。不要再次要求用户配合前台操作来阻塞当前开发。

**本轮首要任务：继续 D6.mcp，外部 MCP 能力接入。** 媒体/视频理解已经打通 B站来源登记、在线字幕/受管 ASR 转写/等距抽帧、时间锚点转写对象、只读模型工具（media_list/media_transcript）、取消/失败分类/重启恢复/删除传播与工作台媒体分区，不再把它当下一断点；真实 B站视频端到端与真实 ASR 模型质量属真实验收，不阻塞开发。

MCP 批次的既有基础只有数据层的 MCP 配置表与概念文档；运行适配器尚未完成。按 `docs/tasks/v2.json` 的 D6.mcp 范围实现：**stdio 与 Streamable HTTP 传输、显式 OAuth、凭据引用（不经模型）、目录/工具指纹与授权漂移撤销、超时/取消/unknown 分类、工作台配置与状态。** 具体新接口/表/工具名在源码对账后确定，交接中的设计建议不是既有实现。

实现要求：

- MCP 是外部进程/HTTP 依赖：stdio 子进程沿 OwnedProcess/受管进程合同；HTTP 客户端有界超时与取消；崩溃/断连的 in-flight 调用归类 unknown，不自动重放写操作。
- 凭据（OAuth token、API key）只走凭据引用与专用存储；模型永远拿不到凭据本体。
- 工具发现/授权/漂移：工具清单指纹变化触发撤权与显式重新确认；未授权工具对模型不可见，不静默放行。
- 模型读的是 `ActionResult.text`：MCP 工具结果进入模型可见文本时保留来源服务与资源标识。
- 保持四层：入口适配器 → 应用服务 → 领域与端口；基础设施实现端口；composition 统一构造与关闭。领域无 I/O；仓储不自行连接/建表。
- V2 是单实例、单所有者、多 Agent、单一 `omni.db`；不迁 V1 数据，不双写，不保留旧接口兼容层。
- 沿用现有视觉风格、原生 ES 模块与公共组件；新能力同时接服务、工具/HTTP、工作台、取消与审计，避免只写底层端口。
- 每完成一个节点，按用户要求 git 提交并推送一次。

**执行偏好：开发优先，持续推进同一交付包。** 不要每个小步骤都跑全量、重打包或三目标类型，不重新展开已完成的清理/恢复基础。完成一条生产链后集中跑受影响模块和调用方：

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/test_<相关模块>.py
# 失败优先具体 nodeid；只用缓存失败时禁止空缓存自动全量：
uv run --no-sync pytest -q -n 0 --lf --last-failed-no-failures=none
```

不重建用户环境，不执行 `uv sync`，不为测试停止服务。所有测试离线、临时数据库、隔离文件和假账号/假设备，不跳过闸门、不缩短被测超时、不放宽断言。UI 每用例独立 BrowserContext/数据库；重型类型、浏览器、打包顺序执行。

完整任务收尾/提交前按仓库要求执行一次全量，同一固定版本复用有效证据；不要因新会话初始化、文档或状态更新运行全量。专项不能写成全量，默认类型不能写成严格类型，注入不能写成平台支持。不要直接重跑历史 seal 脚本。

后续保持固定范围：D6.mcp → 完整插件回调/产物/市场；收口 Mac/Linux/运维源码与平台反馈；D7 令牌/工作台/类型/包/容器/安装更新恢复及正式入口；D8 固定构建全量、真实功能、负载、三平台各 72 小时。环境暂缺时记录待真实验收，继续其他可开发内容。

真实付费、外发、设备输入、提权、系统安装、公开发布或部署仍按已有授权规则；普通已授权开发不反复询问许可。不要因为需要后续真实验收就停止当前可完成工作。

每批收口：更新任务台账的开发/离线/实测三个状态、HANDOFF 当前段、领域合同及证据，记录实际源码指纹和限制；运行索引及任务摘要生成/检查，为当前成果保留恢复副本。保留历史报告及开放问题，不把小批次完成写成完整 D6/V2 完成。

先用一段简短说明报告你核对出的当前断点，然后开始写代码。只有用户要求重新规划、出现无法绕开的缺失信息或需要具体真实操作授权时才停下来询问。
