# V2 Android explore_app 状态图设计合同（K14 可离线缺口）

**状态：`offline_core_implemented_not_wired`（2026-11-22）。** 离线核心（结构 skeleton、屏幕/边去重、上限与摘要）已实现并专项通过；D0–D4 仍未决，**没有**模型工具、HTTP 路由、持久化、真实设备探索或自动动作。

## 1. 范围

- 输入：同一授权会话内、按所有者/agent 显式触发的多次 Android 观察（窗口/应用身份、`tree_sha256`、节点结构、可用动作）。
- 处理：从结构化 UI 树构造**有界屏幕状态图**（稳定 skeleton 哈希去重、动作边、应用/活动边界），不执行动作、不自动探索。
- 输出：只读状态图/路径摘要（屏幕数、边数、可达路径、未探索动作），供模型/所有者后续显式决策；不自动点击/输入。
- 不做：真实设备自动遍历、模型规划、任意动作执行、跨 Agent 共享、长期记忆/人格写入、OCR/视觉理解。

## 2. 参考语义（固定提交）

参考 `xuexi/android-agent` 固定提交 `7c09f6cd` 的 `explore_app`：以 XML skeleton hash 归并屏幕、记录动作边，形成应用状态图；探索本身仍是显式/有界动作，不是后台自动操作。V2 只继承「skeleton 去重 + 动作边 + 有界图」语义，不复制其存储格式/探索策略/供应商行为。

## 3. 待所有者/ A 决策

| ID | 决策 | 选项 | 未决时 |
|---|---|---|---|
| D0 | 持久化 | 不落库（仅本次调用内存）／所有者可读 JSON 文件／新表（迁移由 A） | 仅内存，不落库 |
| D1 | 触发方式 | 仅所有者显式请求／agent 在已授权会话内显式调用／不提供 | 仅离线核心，不接工具 |
| D2 | 图上限 | 屏幕/边/路径/节点数上限与截断语义 | 保守固定上限，超限显式 truncated |
| D3 | 可见面 | 仅离线 adapter／所有者 HTTP／模型工具（需 A 协调能力 schema） | 仅离线 adapter |
| D4 | 失败语义 | 身份变化/树超限/无动作 → 显式错误或空图 | 显式 `android_explore_*`，不伪造边 |

**D0 未决不落库/不迁移；D1/D3 未决不新增模型工具/HTTP。** 共享接口与迁移由 A 协调。

## 4. 合同边界

- 观察必须来自同一 grant/session/用户/boot；窗口或应用身份变化即关闭当前图，不跨设备/跨 Agent 合并。
- skeleton 哈希只基于结构字段（应用/活动、节点 class/resource-id/actions/bounds 的相对结构），**不包含文本/描述/密码值**；同一结构不同文案应归并，文案变化不得生成新屏。
- 屏幕节点必须有界（建议 ≤200），边必须来自已观察到且当时可用的动作；同一 `(screen, action)` 只保留一条，目标屏不在观察集时不臆造。
- 路径/摘要只返回屏幕标识、动作种类/节点序号、边数与截断标记；不返回节点文本、密码字段、object_id 或路径。
- 不自动执行探索；每次扩展仍需既有逐动作审批/观察绑定。取消/关闭等待实际任务清理。
- 失败：身份变化、树超限、密码节点、无动作或损坏观察 → 显式错误/空图，不伪造边、不重试未知设备动作。
- 审计只记有界元数据（屏幕/边计数、状态、错误码），不记 UI 正文。

## 5. 测试计划（实现时）

1. 领域：skeleton 哈希稳定/文案变化归并、应用/活动边界、屏幕/边/路径上限、重复动作去重、截断、非法/损坏观察拒绝。
2. 应用：同 grant/session 约束、身份变化、跨 Agent、取消/关闭等待、空图/失败语义；不自动动作。
3. 持久化（若 D0）：重启恢复、来源擦除、迁移/表数；未决不做。
4. 调用方：Android observe/capture、HTTP（若 D3）、架构/strict/ruff。
5. 回退：摘图/哈希 → 新增用例红；恢复后绿。

## 6. 明确不做与未验证

- 不做真实设备自动探索、模型规划、OCR/视觉、自动点击/输入、长期记忆/人格。
- 不宣称 explore_app 对等；`whole_core_parity_complete`、`whole_v2_complete` 保持 false。
- 真实设备探索/72h 未验。

## 7. 离线核心实现记录（2026-11-22）

- `domain/android_explore.py`：`skeleton_hash(app, activity, nodes)` 只哈希结构字段（class/resource-id/排序 actions），**text/description/password 值不进入**，文案变化归并同一屏；密码节点跳过；屏幕 id 为 `sha256(app|activity|skeleton)[:24]`；`ExploreGraph` 上限 200 屏/1000 边，重复边去重、自环/缺失目标不造边，超限 `truncated=true`；`summary` 只返回屏/边/应用/截断；`build_graph` 从观察与已观察 transition 构图。
- 测试：`agent/tests/v2/core/test_v2_android_explore.py` **6 passed / 0.33s**（文案归并/结构变化分屏/密码排除/非法输入/200 屏/1000 边/去重/缺失目标/摘要/build_graph）。
- 无持久化、模型工具、HTTP、对象发布或自动动作；D0–D4 未决。证据：[v2-android-explore-core.json](../../evidence/v2-android-explore-core.json)。
