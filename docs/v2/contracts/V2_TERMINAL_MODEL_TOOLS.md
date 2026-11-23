# V2 模型侧持久终端与进程控制设计合同（K13 组 1 残余，T1–T5）

状态：**设计合同（design_contract_recorded，纯文档，无源码/测试/迁移/路由/worker 改动，2026-11-23）**。**未实现**。承接 [K13 审计](V2_TOOL_CHANNEL_AUDIT.md) 组 1「模型侧一次性命令对等；持久会话模型工具缺」「后台进程模型控制工具缺」与桌面 UI 工具缺口；本册给出 V2 的 T1–T5 选项。写扩面/沙箱/CDP 不并入。

## 参考事实（固定 `9976f485`，仅作设计数据）

- **`terminal`**：持久 shell 会话（cwd/env 跨调用保留），模型可连续执行命令；参考同时提供 `process_manage`（list/poll/log/wait/kill/write/submit/close/handoff）控制后台进程。
- **`desktop_ui` toolset**：`read_terminal`/`close_terminal`/`read_window_below` 等 GUI 会话专用工具（Electron 桌面 pane）。
- 参考把持久 shell 与后台进程控制视为 terminal toolset 的一部分；工具调用本身是模型自由执行面。

## V2 现状

- `shell_execute`：**一次性已审 argv**（owner 逐次批准，`composition/capabilities.py:105`），worker 执行、有界输出；无持久 shell、无 stdin 交互、无后台进程管理。
- `TerminalService`（`application/terminals.py`）：**所有者侧**受管终端，`open/attach/detach/output/input/resize/end`；单一写者、意图指纹回执、权限代次/工作目录指纹/boot/lease 身份、默认 ≤4 个终端、失败回收保守、来源证明与审计（[V2_TERMINALS](V2_TERMINALS.md)）。**无模型工具入口**。
- 进程：`runtime/processes.py` 的 `OwnedProcess` 仅内部使用（平台辅助进程回收）；无模型侧 process 工具。
- 桌面：`desktop_semantic` 为所有者能力；无模型侧 `read_terminal/close_terminal/read_window_below`。

## 设计选项（需所有者选定）

### T1 不移植（推荐）

模型继续只用一次性已审 `shell_execute`；持久会话与进程管理留在所有者侧 `TerminalService`/工作台。理由：参考的持久 shell 让模型**不经逐次 argv 审批**连续执行任意命令，与 V2「每次命令显式批准」纪律直接冲突；V2 所有者侧终端已覆盖交互需求。审计登记「参考特有、不移植」，不宣称组 1 对等。

### T2 只读观察（可选，低风险）

模型新增**只读**观察工具（`writes=False`、agent 档、resource=terminal）：

- `terminal_list`：列出本 agent 的受管终端（id/状态/cwd 指纹/启动时间/最后活动），只读投影；
- `terminal_read`：读取指定终端的有界输出窗口（`after`/`limit`，≤64 KB，沿用 `TerminalService.output` 的既有边界与只读回执）；
- `process_list` / `process_read`：列出/读取本 agent 的后台进程（PID+启动时间身份、状态、有界日志）；不含 stdin、kill、close、handoff。
- 价值：模型能看到自己/所有者终端状态与日志而不获得写能力；风险低（只读、owner 数据面已存在）。无迁移；能力注册 + 路由只读档。
- 决策：是否值得新增模型面（owner 终端的输出可能含敏感信息，需确认是否允许模型读取所有者终端，或仅限本 agent 自己打开的终端）。

### T3 逐命令审批的持久终端（中风险）

在 T2 之上加 `terminal_input`（`writes=True`）：模型每次输入/命令都走既有写审批门（approved/denied/revoked/expired），复用 `TerminalService.input` 的单写者/意图回执/权限代次；模型可获得 cwd/env 持久性但**每条命令仍需所有者批准**。风险：审批疲劳、交互式 TUI 无法逐步批准；不提供 stdin 原始流（仅整行命令）。无迁移。
- 决策：是否接受「每条命令审批」的交互成本；是否仅限现有 owner 终端。

### T4 完整持久模型 shell / process_manage（拒绝）

参考式模型自由持久 shell + kill/write/submit/close/handoff：可绕过逐命令审批执行任意命令/终止进程；`kill` 若按 PID 无启动时间身份还会误杀复用 PID。**明确拒绝**。

### T5 kill/handoff 的审批子集（若需要）

仅 `process_kill` + `process_handoff` 走审批，身份必须 `(pid, started_at)` 双因子（禁止 argv 子串判定），且仅限本 agent 拥有/租约内进程；`handoff` 语义对齐参考（子进程所有权转父），V2 需 K04 委派生命周期配合。风险仍高（误杀/权限），需独立安全评估。

## 决策问题

1. T1（不移植）还是 T2（只读观察）？T3 的逐命令审批是否可接受？
2. 若 T2：模型可读**所有者终端**还是仅本 agent 打开的终端？输出上限/脱敏要求（是否需 Redactor 处理终端输出）。
3. 若 T5：`(pid, started_at)` 身份、租约归属、与 K04 委派子进程 `handoff` 的边界；是否只做 kill 不做 handoff。
4. 桌面 `read_terminal/close_terminal/read_window_below` 是否移植（依赖平台权限与 GUI pane，属实机/平台项，建议不移植）。

## 实现要点（若 T2/T3 获批）

- 能力：`terminal_list`/`terminal_read`（只读）与可选 `terminal_input`（写=审批门）；`resource="terminal"`；能力注册在 `composition/capabilities.py`，路由纳入只读档（T2）或写档（T3）。
- 领域：终端投影/窗口参数校验（纯）；输出脱敏沿用 Redactor（终端输出可能含秘密）。
- 应用：薄封装既有 `TerminalService.page/get/output/input`，不建第二终端内核、不改会话表；无迁移、无 worker 协议改动。
- 审计：读取事件（只读）与输入审批事件沿用既有终端审计。
- 回退：摘注册后新增用例全红；既有 `shell_execute`/TerminalService 零回归。

## 测试计划

- T2：只读投影边界/分页/输出上限、跨 agent 隔离（不能读他人终端）、已关闭/未知终端 404、脱敏样例、路由只读档、无写回执。
- T3：审批四态、意图回执去重、单写者冲突、权限代次/工作目录指纹不匹配拒绝、取消/关闭等待。
- T5：`(pid, started_at)` 身份、非本 agent/租约外拒绝、复用 PID 不误杀、handoff 边界。
- 调用方：terminals/desktop/engine/架构/类型守卫；真实终端与桌面 pane 另验；未实现前不宣称组 1 对等。

## 明确不做

- T4 完整持久 shell/process_manage；桌面 pane 工具；跨平台终端后端（docker/ssh/modal 等）；真实终端交互质量；全量；72 小时。

## 关联

- K13 审计：[V2_TOOL_CHANNEL_AUDIT](V2_TOOL_CHANNEL_AUDIT.md) 组 1。
- 决策登记：[V2_REMAINING_DECISIONS](V2_REMAINING_DECISIONS.md)「K13 组 1 残余」。
- 相关合同：[V2_TERMINALS](V2_TERMINALS.md)、K04 [V2_DELEGATION](V2_DELEGATION.md)、[V2_CODE_SANDBOX](V2_CODE_SANDBOX.md)（D2）。
- 证据：[v2-terminal-model-tools-design.json](../../evidence/v2-terminal-model-tools-design.json)。
