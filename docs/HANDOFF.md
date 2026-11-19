# OmniOperator 当前交接入口

## 当前状态与工作方式

- 开发分支 `v2`，跟踪 `origin/v2`。默认入口是 V2，显式 V1 回退保留。
- 当前工作：目录整理已完成；按 [Agent 内核与学习项目对标](v2/contracts/V2_AGENT_CORE_PARITY.md)持续补齐。K03 [修订化任务清单](v2/contracts/V2_TODOS.md)、K04 [单层父子 run 委派](v2/contracts/V2_DELEGATION.md)、K05 [模型批次工具并行](v2/contracts/V2_TOOL_BATCH.md)已接生产；下一项 K06 run 内工具上下文压缩仍未实现。
- Windows 主体开发及旧离线候选已完成；当前源码未统一全量，整个 V2 仍在开发。真实账号/模型质量/设备、干净机器及 72 小时验收分别待办。
- Mac mini 为 Apple Silicon，所有者提供 macOS 27，实际版本在 Mac 用 `sw_vers` 核对。Mac 做主开发机，现有 Windows 保留运行和原生验收环境，两端同步同一分支。
- 当前 Windows 服务、实例、凭据和其他会话的 `PPT/`、`docs/competition/` 保留。根 `src`/`tests` 是本机兼容联接，正式代码与测试分别在 `agent/src`、`agent/tests`。

## 接手顺序

1. 读 [目录规则](REPOSITORY_STRUCTURE.md)、[架构](ARCHITECTURE.md) 和 [测试纪律](TESTING.md)。
2. 当前任务以 [持续开发任务](tasks/v2-development.json) 为准，内核对标子任务见 [缺口清单](tasks/v2-agent-core-parity.json)，Windows 阶段见 [候选任务](tasks/v2-windows-candidate.json)；[状态摘要](DEVELOPMENT_STATUS.md)由当前任务记录生成。
3. 按 [V2 导航](v2/README.md) 找合同与平台断点；迁移到 Mac 后直接粘贴 [接续提示词](V2_MACMINI_RESUME_PROMPT.md)，环境步骤见 [迁移说明](V2_MACMINI_MIGRATION.md)。

## 已完成断点与证据

- Linux Enter 控件状态批 `f539f21e`：194 专项、3 个运行文件 strict 通过，真实 Linux 未验。[证据](evidence/v2-linux-key-state.json)。
- Windows 样例修正 `fbfc16b8`、人工复验 `73970d20`：标准顶层窗口下点击点与遮挡拒绝通过，36 专项。自有窗口完整回归最终文本 `PREFIX-ONLY-OWNED-TEXT`、按钮 1 次，四次生产回执仍为 unknown、不重发。原 STATIC 样例导致跨进程命中，生产拒绝正确，矩形中心候选未保留。[复核证据](evidence/v2-windows-click-fixture.json)。
- 历史 5998 全量仅覆盖 `7e64d348` 固定候选。后续修改、其他会话提交及本次目录迁移均不能复用为当前源码全量通过。

## 本次目录整理验证

108 个平台实现按职责归组，378 个测试/既有助手文件迁移，108 个共享提供方提取；V1 实现及原生资源位置保留。原 7354 个测试节点全部保留，新增 7 个回归后收集 7361 个，无重复。分阶段 397 个不同专项节点通过，386 文件 strict 零错误/警告，Ruff 与格式检查通过。直接 wheel、sdist→wheel、124 个界面资产及隔离 V2 导入/关闭通过；原服务包数量失败保留并已精确修正验证。

Windows 自有窗口点位与遮挡、ConPTY 原生专项已执行；未重复前台系统输入，未在 Mac 编译或执行，未全量。279 份既有证据/原生资源/迁移文件内容保持；并行 UI 提交 `2bdab425` 更新其自身证据，已保留，补 6 项现有界面/目录节点与新资源包通过。详见 [整理证据](evidence/v2-layout-reorganization.json)和 [旧新路径清单](tasks/v2-layout-migration.json)。

## 下一开发断点

所有者新增完整对标要求；K03 已完成读写/合并、修订冲突、层级/容量限制、压缩/重启恢复与来源删除屏障，见 [清单证据](evidence/v2-todos.json)。完成标记不授予权限、不证明外部动作成功。当前没有清单编辑 UI。

K04 已接 `delegate_task` 单任务/批量生产链：每父生命周期最多 4 子、深度 1、独立会话/上下文/租约、单独 4 子池；父占满根并发仍可推进。继承实际父模型调用的固定模型、已审工具/技能/MCP/扩展捕获，预算父子合计，写动作独立审批，取消/关闭等真实孩子清理后才结束父任务；unknown 不重发。生成的委派输入不能直接变成长期记忆证据，父子来源删除阻断后续发送与摘要投影。迁移至 `v2_0042`，仅测试隔离库，真实 Windows 服务数据库未升级。没有跨层嵌套、自选模型或异步委派管理界面。

本批新增 30 项回归，冻结源码 121 项集成专项全过（189.42 秒），覆盖委派、K03、执行/FIFO/关闭、上下文/迁移、路由、技能、MCP、来源记忆与架构。最终 394 文件 strict 零错误/警告（16.35 秒）；测试候选后只有一处本地变量类型标注修正，无运行逻辑变化，指纹和首次失败见 [委派证据](evidence/v2-delegation.json)。未全量、未真实模型/原生/72 小时验收。

K05 已接模型批次工具并行：纯领域 `domain/tool_batch.py` 分段规划器把批次切成有序 parallel/serial 段，严格保持调用顺序；只有本回合已提供的只读能力按两两不同资源键（`ToolSpec.resource or name`）并发，`request_mode`/`todo_list`/`delegate_task` 控制、所有写动作（独占 run 审批状态机）、档位外与未知能力一律串行屏障。并行经 `Application._execute_parallel` 的 `asyncio.gather` 派发，每项仍走原 `_execute` 的 schema/prepare/资源锁/authorize/观察/逐项超时/settle 生命周期；结果按原调用顺序写回（以模型第二轮实际输入断言），段内失败或 unknown 时兄弟动作先结算再按序抛错，unknown 不重放；run 取消时 gather 取消整组并等待每个已启动动作自身结算后才走父清理，无 executing 残留、实例可正常关闭。无数据库迁移、无第二内核/状态库。写动作并行需改造审批状态机，冲突分类基于显式资源键而非 Hermes 路径启发式，均不在本项范围。

本批新增 15 项回归（8 规划器 + 7 引擎集成），最终源码 144 项专项全过（223.69 秒），覆盖 K05、执行/FIFO/审批/取消、K04 委派、K03 清单、内核/生命周期/能力、上下文/压缩与故障、动作审核执行、执行裁剪与四层架构守卫。V2 strict 用 `pyrightconfig.strict.json` 直接检查 395 文件零错误/警告（17.46 秒，新模块已加入 include，394→395）；注意 `scripts/run_pyright.py` 只覆盖 4 个 V1 收口文件，不能当作 V2 类型通过。Ruff 与格式检查通过，项目索引已更新。首轮两处断言误用 `app.messages`/`app.context` 读取未持久化的中间 tool 行、一处多余 cast 均已修正并在最终源码重跑绿。未全量、未真实模型/原生/72 小时验收，未停止或升级运行中的 Windows 服务与数据库。详见 [批次并行证据](evidence/v2-tool-batch.json)。

下一批推进 G6.core_parity / K06 run 内工具上下文压缩。已核实的架构起点：run 内工具转录只存在于内存 `messages`（持久化 `s.messages` 仅由 `_message` 在 submit 写 user、finish 写最终 assistant，中间 assistant tool_calls/tool 结果不落消息行，仅进 context_snapshots）；`ModelCalls.invoke` 在 `serialized_request(messages, tools) > CONTEXT_CHARS`（48000）时硬失败 `context_budget_exceeded`，长 run 多轮工具会撞限而非压缩。现有 `ContextService.prepare` 压缩作用于持久化整回合（`domain/context.py` 的 `turns` 按 run_id 分组，最小单位整回合），在 run 起点执行。K06 需对内存 live 转录的**旧完整工具轮**（assistant tool_calls + 其全部匹配 tool 结果为一轮，绝不拆对）做有界可续压缩：保留 `TurnPrompt` 冻结的 messages[0] 系统前缀（K01）、保留近若干轮与 todo 尾部通知、复用 `summary_request`/`reserve_call(purpose="summary")`/`settle_call` 计费、保持来源删除屏障（context_invalidated）。参考 `xuexi/hermes-agent/agent/context_engine.py` 的 `prune_tool_results_only`/`compress`。之后继续 K07 迭代预算/重复退化、K08 回合后学习、K11 停止前验核及后续任务，其他对标项不提前关闭。

K03 新增 34 项回归；首轮冻结源码 114 项集成专项通过（182.82 秒），收尾补强 Agent 禁用检查后最终 34 项清单/HTTP 模块专项通过（39.09 秒），另 7 项目录/状态守卫通过。最终 391 文件 strict 零错误/警告，Ruff 与格式检查通过，未全量。每轮源码指纹与失败均保留，不把首轮结果写为最终源码全量。此前 K01/K02 批 `c4e205d8` 的结果见 [固定证据](evidence/v2-agent-core-parity.json)。测试只用隔离数据库与冻结源码，当前 Windows 服务和真实数据库未停止或由测试升级。

会话级缓存稳定、run 内工具压缩、停止前验核和回合后学习仍待实现；未全量、未真实模型效果验收。

目录迁移、专项和打包已完成；Mac 接手还需实际编译 Swift/C，再推进 G5 平台/交付，G3 Linux、G4 Wayland 缺口继续按任务推进。G7 统一全量与最终实机验收留整个 V2 开发冻结后。

## 追溯入口

- [旧交接记录](history/pre-layout-2026-11-18/HANDOFF.md)、[旧重构记录](history/pre-layout-2026-11-18/V2_REFACTOR.md)只证明所属版本，不作为当前待办。
- [目录迁移清单](tasks/v2-layout-migration.json)记录旧新路径与基线指纹；原始 Windows 大产物仍在 `C:/v2-acceptance`，Git 只包含注明的汇总证据。
