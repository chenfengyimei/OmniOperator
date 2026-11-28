# UI 重构 Phase 2 实施记录：Agent 核心交互闭环

日期：2026-11-27 ｜ 依据：`docs/v/design/UI_AUDIT_2026-11-27.md`、`docs/v/design/UI_REFACTOR_PHASE01_2026-11-27.md`
范围：仅 `web/ui/v2` 前端 + 本文档；**未修改后端 API 协议、数据库、Agent 内核与 Worker 调度**。
状态：审批、停止、失败恢复、重试、本地输入队列、执行时间线均已实现并通过 39 项隔离实例 UI 验证。

## 1. 现有能力检查（先查后端，不按 UI 需求推测）

### 1.1 分类结论

| 能力 | 分类 | 后端事实 |
| --- | --- | --- |
| 审批列表 / 允许 / 拒绝 | ✅ API 直接支持 | `GET /agents/{a}/approvals`、`POST /agents/{a}/approvals/{id}`（body: `allowed` + `policy_revision`）；审批有稳定 `id`、`action_id`、`run_id`、`expires_at`、`policy_revision` |
| 审批字段（原因/风险/副作用/证据哈希） | ⚠️ 后端未返回 | `pending_approvals` 只 join `actions.tool/arguments/run_id/policy_revision`；无 reason/risk/writes/effect/arguments_sha256 |
| 审批延后 | ❌ 当前架构不支持 | 只有 pending→approved/denied/expired/cancelled；无延后状态与 API |
| 停止运行 | ✅ API 支持 | `POST /agents/{a}/runs/{run_id}/cancel`；`application.cancel` → `repository.cancel`：先 `omniweave_cancel`（标记根 Ticket 下 OWA 执行），再取消 run/子运行；终态 run 调用仍会先标记 OWA 取消 |
| Stop 的状态反馈 | ✅ API 支持 | run 状态机含 `cancelling`；`run.cancel_requested` 事件 |
| Resume（手动续跑） | ❌ 当前架构不支持（仅后台自动恢复） | 有 `run_checkpoint`/`recover()`/`run.resume_queued` 仅用于进程重启后的自动恢复扫描；无所有者触发的 resume API |
| Retry（run 级重试） | ❌ 无 retry API；⚠️ 可用新 Turn 等价 | 没有 run/turn retry 端点；`POST /agents/{a}/turns` 可用同一文本创建**新 run**（幂等 key 机制） |
| 输入队列（服务端） | ⚠️ 有排队能力但无队列管理 API | `accept_turn` 对每次提交创建 `state=queued` 的 run + `work_items(queue_sequence)` FIFO；但没有“列出/编辑/删除/重排/取消队列条目”的 API |
| 输入发送限制 | ⚠️ 仅前端限制 | `submit_turn` 接受并发提交（服务端串行 claim 执行）；当前 UI 在 `run` 存在时拒绝发送 |
| 执行时间线 | ✅ 事件/费用/委派 API 支持（部分字段缺失） | `/runs/{id}`（状态/error_code/ended_at/completion/continuation）、`/runs/{id}/events`（真实时间戳；`action.completed` 带 status/effect/elapsed_ms/error_code/excerpt）、`/runs/{id}/routing`（已知费用/实测 token）、`/runs/{id}/delegations`（Worker 状态/摘要/已知费用） |
| 退出码 / 工具内部结果 | ⚠️ 未暴露 | 事件里只有 excerpt 文本与 error_code，没有结构化 exit code |
| Run 开始时间 | ⚠️ 未暴露 | runs 表只有 created_at/ended_at；执行开始时间由 `run.running`/`model.started` 事件时间推断 |

### 1.2 因此产生的实现策略

- 能用 API 的：审批对象/提交、真实取消、新 Turn 重试、事件时间线、费用/token 展示。
- 前端实现并明确语义：审批可读化、单一提交路径、停止/取消中状态、终态 run 控制恢复、本地输入队列、时间线聚合。
- 需要后端补充（本阶段暂停，不改协议）：见 §6。
- 当前架构不支持：手动 Resume、审批延后 —— UI 明确不提供假按钮。

## 2. 状态机与交互契约

### 2.1 统一生命周期（`web/ui/v2/lifecycle.js`）

```
idle → queued → running → waiting_approval → running → succeeded
                     ↘ cancelling → cancelled
                     ↘ failed / interrupted（终态）
succeeded + completion=partial → partial（部分成功）
未知状态 → unknown（显示"状态未知"，不推断成功）
```

- 单一来源：`run.state` + `completion.state` + `continuation` → `derivePhase()`；UI 不再用多个布尔量拼状态。
- 停止：仅活跃状态（queued/running/waiting_approval）可取消；`cancelling` 时按钮禁用并显示"等待服务端确认…"。
- 重新执行：仅终态且能恢复原始输入时显示；通过新 Turn 执行，**不是同一 run、不是续跑**，确认框明确重复副作用风险。
- 续跑：不提供按钮；`interrupted` 时展示事实说明（自动恢复扫描，无手动 Resume API）。
- 本地队列：运行中发送 → 入本地队列；失败条目保留并阻塞后续；显式「发送下一条」才创建 Turn。

### 2.2 审批交互契约

| 契约 | 实现 |
| --- | --- |
| 稳定 ID / 绑定 Turn | 以 `approval.id`、`action_id`、`run_id` 为键；对话内联按 `run_id` 锚定消息，右栏待办与浮层同源 |
| 单一提交路径 | `approvals.js` 单一 `resolve()`；每个 id 有 inflight 守卫；三处 DOM 共享 controller 状态，重复点击/并发点击只发一次 POST |
| 不每秒重建 DOM | 以 `[id,state,expires_at,policy_revision,tool]` 签名差异重建；倒计时只改文本节点 |
| 提交中 / 失败 | 提交中禁用并显示"正在提交…"；失败显示服务端原因（`approval_expired`/`policy_changed`/`run_not_waiting`）并允许重试；成功后显示"已提交允许/拒绝"等下一次刷新 |
| 字段可读化 | 操作名称、来源（Agent/run）、目标（从参数提取，无法提取标"未知"）、原因（服务端未登记）、影响范围、风险等级（投影未返回 → 未知）、外部副作用（未知）、权限时长（一次性 + 到期时间）、证据（approval/action id + 策略修订 + 运行时间线入口）、详细参数折叠 |
| 延后 | 按钮禁用并说明"服务端没有延后 API" |
| 切换会话恢复 | 审批列表来自 Agent 级接口，切走再回仍显示；内联锚点找不到对应消息时只在浮层/待办展示 |

### 2.3 停止 / 关闭抽屉 / 队列的相互作用

- 停止调用真实 cancel；界面只显示"正在提交取消…"/"等待服务端确认…"，服务端 `cancelled` 后才显示"已取消"。
- 停止或切页不会清空本地队列；队列按 `agent|session` 存 localStorage，刷新/重连后可恢复。
- 队列发送失败：条目标记"发送失败"，头部按钮变"队列已阻塞"，后续条目不自动跳过；用户可重试/编辑/删除。
- 发送成功后面板刷新失败不推翻发送结果（避免误判为队列失败，真实 bug 已在验证中发现并修复）。

## 3. API 复用与缺口

### 3.1 复用（未改协议）

| 端点 | 用途 |
| --- | --- |
| `GET /agents/{a}/approvals` | 审批列表（唯一来源；不再两处各自解释） |
| `POST /agents/{a}/approvals/{id}` | 允许/拒绝（单一提交路径） |
| `POST /agents/{a}/turns` | 直接发送、队列发送、重新执行（新 run） |
| `POST /agents/{a}/runs/{id}/cancel` | 停止（真实后端取消） |
| `GET /agents/{a}/runs/{id}` | 生命周期状态、error_code、completion、continuation、ended_at |
| `GET /agents/{a}/runs/{id}/events` | 时间线事件（排队/执行/模型/工具/终态）与 action 耗时 |
| `GET /agents/{a}/runs/{id}/routing` | 已知费用、实测 token、待结清调用数 |
| `GET /agents/{a}/runs/{id}/delegations` | Worker 子运行状态/摘要/已知费用（Phase 1 复用） |

### 3.2 缺口（暂停，未擅自改协议）

| # | 缺口 | 影响 | 当前降级 |
| --- | --- | --- | --- |
| A1 | 审批投影缺 reason / risk / writes / effect / arguments_sha256 | 无法显示真实风险等级、外部副作用、原因、参数指纹 | 逐项显示"未知/未提供"并说明原因；参数 JSON 可查 |
| A2 | 无审批延后 API | 无法稍后处理 | 按钮禁用 + 原因 |
| A3 | 无所有者手动 Resume API（只有后台自动恢复） | 中断运行不能续跑 | 不提供 Resume；说明 + 重新执行新 Turn |
| A4 | 无 run 级 Retry API | 只能重新执行同一输入 | 明确"重新执行（新 run）"并提示重复副作用 |
| A5 | 服务端有 FIFO 排队但无队列管理 API | 不能服务端列出/编辑/重排/取消队列 | 本地草稿队列（localStorage），显式发送；不宣称服务端队列 |
| A6 | 事件缺结构化解退码/工具结果；runs 无 started_at | 退出码显示未知；开始时间取首个执行事件 | 显示"未知"；跨度标注"按服务端事件时间" |
| A7 | 无“停止后保留位置/草稿”的服务端语义（前端自行保存） | 位置/草稿仅本机浏览器 | 明确本地语义 |

## 4. 修改文件清单

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `web/ui/v2/approvals.js` | 新增（~13 KB） | 审批 controller：差异重建、单一提交、字段可读化、内联/浮层/待办同源、错误恢复 |
| `web/ui/v2/lifecycle.js` | 新增（~4 KB） | 统一状态模型、停止/重试/续跑能力声明与文案 |
| `web/ui/v2/queue.js` | 新增（~8 KB） | 本地输入队列：持久化、编辑/删除/重试、失败阻塞、显式发送 |
| `web/ui/v2/timeline.js` | 新增（~9 KB） | 执行时间线：run/事件/费用/Worker 聚合，缺失字段显示未知 |
| `web/ui/v2/main.js` | 修改 | 移除旧审批渲染（两处独立 DOM）；接入 4 个模块；send 支持队列；停止状态机；终态控制恢复；时间线加载；会话/重置联动 |
| `web/ui/v2/index.html` | 修改 | 审批/队列/重试/续跑说明/时间线容器；`?v=` 版本串 |
| `web/ui/v2/workbench.css` | 修改（+约 7 KB） | 审批字段表、队列、时间线、run-actions 样式；沿用既有 token |
| `docs/v/index/*` | 再生 | 新增 JS 资产后按仓库纪律更新索引 |
| `docs/v/design/UI_REFACTOR_PHASE02_2026-11-27.md` | 新增 | 本文档 |

未触碰：`agent/src/**`、数据库 schema、`docs/v/reference/TYPE_CHECKING.md` 及其他会话未提交文件。

## 5. Playwright 测试报告

隔离实例（`--home %TEMP%\omni-phase2-verify`）+ 真实 V2 前端 + 路由 mock；不写真实用户数据、不调用模型。
脚本：`output/playwright/phase2-verify/verify.mjs`；报告：`output/playwright/phase2-verify/p2-report.json`。

**结果：39/39 断言通过，0 页面脚本错误，0 意外控制台错误。**

覆盖（对应任务要求的 15 项）：

| # | 场景 | 结果 |
| --- | --- | --- |
| 1 | 正常审批（字段、允许、单一 POST） | PASS |
| 2 | 拒绝审批 | PASS |
| 3 | 重复提交（并发点击只发一次） | PASS |
| 4 | 审批失败（策略变更；不停留提交中、显示原因、可重试） | PASS |
| 5 | 取消运行中任务（真实 cancel 调用） | PASS |
| 6 | 取消请求失败（错误可见、按钮恢复） | PASS |
| 7 | 已停止任务（显示已取消、消息保留、可重新执行） | PASS |
| 8 | 不支持恢复（只给事实说明、无假 Resume 按钮） | PASS |
| 9 | 重试失败任务（二次确认 + 重复副作用提示 + 新 Turn + 复用原输入） | PASS |
| 10 | 部分成功（completion=partial 可见） | PASS |
| 11 | 输入队列（运行中入队、数量/顺序） | PASS |
| 12 | 失败任务阻塞队列（保留、阻塞、删除后可继续） | PASS |
| 13 | 页面切换后恢复状态（队列 + 审批） | PASS |
| 14 | 390px 移动端无横向溢出 | PASS |
| 15 | 深色/浅色主题切换 | PASS |

补充：队列跨页面刷新保留、审批轮询不重建 DOM、时间线展示 Worker 失败、差异化审批字段（未知字段标注）。

**回归测试**：`check_frontend.py` 93 个 ES 模块语法通过；`test_v2_navigation_ui.py` 2 passed；`test_v2_ui.py::test_workbench_chat_memory_and_theme` 8 passed；Phase 1 的 22 项验证不依赖本次改动（未重跑，按纪律不重复引用为全量）。

## 6. 修改前后截图

| 文件 | 内容 |
| --- | --- |
| `output/playwright/phase2-verify/p2-01-approval.png` | 审批卡：字段表 + 允许/拒绝 + 禁用的延后 + 详细参数 |
| `p2-02-interrupted.png` | 中断运行：不提供 Resume，只给事实说明 |
| `p2-03-retry.png` | 重新执行（新 Turn）后的状态 |
| `p2-04-partial.png` | 部分成功 + 执行时间线（Worker 失败） |
| `p2-05-queue.png` | 本地输入队列（展开、顺序、状态、阻塞） |
| `p2-06-mobile-390.png` | 390px 移动端 |
| `p2-07-dark.png` / `p2-08-light.png` | 深/浅主题下的 Phase 2 控件 |
| Phase 1 基线 | `output/playwright/phase1-verify/*.png`；Phase 2 改动集中在运行区与右栏，壳层未变 |

## 7. 未验证项目

1. **真实 OWA Worker 与真实审批**：隔离实例无历史 Ticket/审批，以上为 mock 驱动；建议在真实会话验一次"运行中→审批→允许→工具完成"与"运行中→停止→服务端 cancelled"。
2. **真实工具失败/退出码**：事件投影不含结构化退出码，界面显示未知；未验证真实 shell 失败场景。
3. **服务端自动恢复（Resume）**：未制造进程崩溃/重启验证 `recover()` 行为；界面按"无手动 API"处理。
4. **200% 缩放、Windows 高对比度**：Phase 4 范围。
5. **本地队列跨浏览器/多标签**：同一 localStorage 单标签语义；未验证多标签并发写。

## 8. 回滚方案

- 删除 `web/ui/v2/{approvals,lifecycle,queue,timeline}.js`；
- `git checkout -- web/ui/v2/{index.html,main.js,workbench.css}`（回滚到 Phase 1 版本）；
- 还原 `docs/v/index/*`（可选，索引可再生）；
- 无数据库/协议变更，回滚不影响既有会话、审批与运行数据；localStorage 队列键 `omni.v2.input-queue.v1` 可留存或手动清理。
