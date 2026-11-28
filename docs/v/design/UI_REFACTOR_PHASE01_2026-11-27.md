# UI 重构 Phase 0 + Phase 1 实施记录

日期：2026-11-27 ｜ 依据：`docs/v/design/UI_AUDIT_2026-11-27.md` ｜ 范围：仅 `web/` 前端 + 本文档
状态：**Phase 0 完成，Phase 1 完成（前端可复用既有 API 的部分）；需要改后端的增强项已暂停并列出缺口。**

## 1. 约束与原则

- 保留 Agent 内核、数据库、API 与 Worker 调度架构：本次**未修改任何后端协议、数据库或调度代码**。
- 只改必要前端文件：`web/ui/tokens.css`、`web/ui/v2/{index.html,main.js,workbench.css}`、新增 `web/ui/v2/omniweave.js`，
  以及 9 个页面的 `tokens.css?v=` 缓存版本串；没有做无关重构。
- 信息密度优先：Worker 明细放右栏页签，主会话只留一条紧凑状态条；不堆日志、不建独立聊天窗、不画进度条。
- 取消必须真实：调用既有 `POST /runs/{run_id}/cancel`，UI 只显示服务端状态，不用本地"已停止"。

## 2. Phase 0：基线冻结与低风险清理

### 2.1 基线

| 项 | 位置 |
| --- | --- |
| 审计基线截图（改动前，1440×900 深/浅） | `output/playwright/baseline-phase0/before-*.png`（取自审计集） |
| Phase 0 后截图（同状态） | `output/playwright/phase1-verify/after-phase0-*.png` |
| 复现脚本 | `output/playwright/phase1-verify/capture-default.mjs` |

**视觉一致性（改动前后像素对比）**：

| 页面 | 差异像素 | 平均色差 | 结论 |
| --- | --- | --- | --- |
| 设置页 1440 浅色 | 0 / 324000（0.00%） | 0.00 | 完全一致 |
| 对话页 1440 浅色 | 506 / 324000（0.16%） | 0.13 | 差异集中在右上状态区（实例状态文案差异） |
| 对话页 1440 深色 | 646 / 324000（0.20%） | 0.16 | 差异在顶栏与左栏 OWA 按钮（隔离实例与用户实例状态不同） |

结论：Phase 0 的清理**没有可见视觉回归**；差异均可归因于两个实例的运行时状态不同。

### 2.2 清理内容

| 文件 | 改动 |
| --- | --- |
| `web/ui/tokens.css` | 删除 25 个零引用死令牌：`--terminal-bg`、`--v2-*`（19 个，且误写在浅色块内）、`--hermes-*`（7 个） |
| `web/ui/v2/workbench.css` | 删除 V2 中已无产出的 `.chat-loading` 选择器（保留 `.msg/.empty-state` 规则） |
| `web/ui/v2/index.html` | 增加 `rel="icon"` 指向 `brand-mark.svg`，消除唯一的 favicon 404 控制台错误 |
| `web/index.html` + 8 个子站 `index.html` + `web/ui/v2/index.html` | `tokens.css` 版本串统一 `20261127p0`（共享 token 改动后的缓存纪律） |

### 2.3 Phase 0 验证

- `scripts/check_frontend.py`：89 个 ES 模块语法全通过。
- `pytest agent/tests/test_ui_design_tokens.py test_ui_glass_tokens.py test_ui_theme.py`：24 项通过。
- `scripts/check_ui_contract.py`（V1）：PASS（无缺失 id / 未注册 data-action / CSS 括号问题）。
- 控制台错误：isolated 实例上 **0 条**（favicon 已修复）。

## 3. Phase 1：Worker / Ticket 可见性

### 3.1 复用到的既有 API（未新增、未修改协议）

| 端点 | 用途 | 证据来源 |
| --- | --- | --- |
| `GET /agents/{a}/omniweave/main-session` | 判断是否启用 OWA、当前会话是否主会话 | 既有 `omniweave.py:19-21`；UI 原本已用 |
| `GET /agents/{a}/omniweave/sessions/{s}/tickets` | Ticket 投影：`ticket_id/goal_version/plan_submitted/cancel_requested/delivery/artifacts/tasks[]` | `omniweave.py:31-37` → `application/omniweave.py:238` → `omniweave_repository.py:520-635` |
| `GET /agents/{a}/runs/{root}` | 根 run 真实状态（区分"终态 run"与"仍在跑的 Worker"） | 既有 run 读取端点 |
| `GET /agents/{a}/runs/{root}/delegations` | Worker 子运行：`child_run_id/state/error_code/summary/已知费用/unknown_*` | `http.py:648` → `engine.py:907` → `delegation_repository.py:delegation_result` |
| `GET /agents/{a}/runs/{child}/events` | 子运行工具/模型步骤（按需展开、有界 40 条、取前 24 步） | 既有事件端点 |
| `POST /agents/{a}/runs/{root}/cancel` | 真实取消：`application.cancel` → `repository.cancel` 先 `omniweave_cancel` 再取消 run/子运行 | `http.py:703-706`、`repository.py:1072`、`omniweave_repository.py:699` |

### 3.2 状态映射（服务端事实 → UI）

| 服务端字段/状态 | UI 文案 | 视觉 |
| --- | --- | --- |
| `ticket.plan_submitted=false` | 计划已提交但尚未派发 | 正文说明，不显示假进度 |
| task/run `queued` | 排队中 | 灰色徽章 |
| `running` | 执行中 | 高对比徽章 |
| `waiting_approval` | 等待审批 | 警示色徽章 |
| `cancelling` | 取消中 | 警示色徽章 |
| `succeeded` | 已完成 | 成功色徽章 |
| `failed` | 失败 | 危险色徽章 |
| `cancelled` / `interrupted` | 已取消 / 已中断 | 弱化徽章 |
| `waiting_peer` / `blocked` / `unknown` | 等待同伴 / 被阻塞 / 状态未知 | 虚线警示徽章（不只靠颜色） |
| `unknown_effect=true` | 副作用未知，需核查 | 文本标记 |
| `unknown_model_calls>0` | N 次模型调用费用待核查 | 文本标记 |
| `result_truncated` / `summary_redacted` | 结果已截断 / 因来源变更脱敏 | 文本标记 |
| `ticket.cancel_requested=true` | 已请求取消，等待 Worker 收尾（以服务端状态为准） | 警示状态条 |
| 根 run 终态 + 仍有非终态任务 | 主回合已结束，Worker 仍在执行 | 状态条保留 + 取消入口可用（后端 cancel 会先取消 OWA 执行） |
| 全部任务终态 | 全部任务已结束 | 状态条隐藏，右栏页签保留明细 |

### 3.3 UI 结构

- **主会话状态条** `#owa-strip`（仅 OmniWeave 主会话、存在活跃 Ticket 时显示）：
  `account_tree` 图标 + 汇总（N 个任务 / X 执行中 / Y 等待 / Z 待核查）+ 阶段文案 + 「查看 Worker」「取消 Worker」。
  不展开日志、不占滚动区；390 宽自动换行且无横向溢出（实测 `scrollWidth=390`）。
- **右栏「任务」页签** `#inspector-worker`：
  - Ticket 头：短 ID + 目标版本 + 阶段 + 刷新/取消按钮；
  - 事实行：产物数、投递结果、根 run 状态、先前版本阻塞项；
  - 计划任务列表：`task_key` + 状态徽章 + profile/版本 + 错误码/未知标记/结果摘要（若有）；
  - Worker 子运行列表：短 run ID + 状态 + 已知费用 + 结果摘要（脱敏/截断标注），点击展开按需拉取工具/模型步骤与"事件跨度（按服务端事件时间估算）"；
  - 末尾固定说明投影缺口，明确"不推测一一对应、不显示估算进度"。
- **取消语义**：两个取消按钮都调用真实 `POST /runs/{root}/cancel`；提交后显示"已提交取消请求；服务端状态确认前不视为已停止"，
  并在取消中隐藏/禁用取消按钮；只有轮询到 `cancel_requested`/`cancelling`/终态才更新文案。
- **终态 run + 仍在跑的 Worker**：状态条读取根 run 状态与任务状态组合判断，保留取消入口；这是本次修复的核心 P0 场景。

### 3.4 修改文件清单

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `web/ui/v2/omniweave.js` | 新增（20.5 KB） | Worker/Ticket 状态模块：拉取既有 API、状态映射、取消、按需事件、3s 节流、generation 防脏 |
| `web/ui/v2/index.html` | 修改（+27 行） | 状态条、右栏「任务」页签与容器、favicon、`?v=` |
| `web/ui/v2/main.js` | 修改（+18 行） | 挂载模块；会话/Agent/连接/切页/页签联动；poll 内 3s 节流刷新 |
| `web/ui/v2/workbench.css` | 修改（+285 行） | 状态条、任务/子运行/步骤样式；页签由 5 列改 6 列；390 适配 |
| `web/ui/tokens.css` | 修改（-31 行） | Phase 0 死令牌清理 |
| `web/ui/v2/index.html`、`web/index.html`、8 个子站 `index.html` | 修改 | 缓存版本串 |
| `docs/v/index/*` | 再生 | `scripts/project_index.py`（新增 JS 资产后索引更新） |

### 3.5 验证方式与结果

**真实运行验证**：隔离实例（`--home %TEMP%\omni-phase1-verify`）+ 真实 V2 前端 + Playwright 路由 mock 构造 Ticket/子运行/事件响应。
不写用户会话/项目数据，不调用模型；真实实例数据未被触碰。

`output/playwright/phase1-verify/verify.mjs`：**22/22 断言通过**，包括：

| 断言 | 结果 |
| --- | --- |
| 主会话出现 Worker 状态条（只有主会话） | PASS |
| 汇总/阶段文案正确（3 个任务 · 2 执行中 · 主回合运行中） | PASS |
| 右栏「任务」页签存在且被选中；任务 3 项、子运行 2 项 | PASS |
| 展示等待审批/已完成/未知语义与投影缺口说明 | PASS |
| 子运行展开显示工具步骤与"事件跨度"（4 步，2.9s 估算） | PASS |
| 取消调用真实 cancel 端点（calls=1） | PASS |
| 取消后显示"已请求取消，等待 Worker 收尾"且不再提供取消按钮 | PASS |
| 终态 run + 运行中 Worker：显示"主回合已结束"且仍可取消 | PASS |
| 全部结束：状态条隐藏、页签保留"全部任务已结束" | PASS |
| 390 无横向溢出；状态条在视口内（356px） | PASS |
| 页面无脚本错误、控制台 0 错误 | PASS |

截图（`output/playwright/phase1-verify/`）：
`after-00-chat-connected.png`、`after-01-strip-running.png`、`after-02-worker-tab.png`、`after-03-worker-events.png`、
`after-04-cancelling.png`、`after-05-terminal-run-worker-running.png`、`after-06-all-done.png`、`after-07-mobile-390.png`。

**回归测试**：

| 命令 | 结果 |
| --- | --- |
| `uv run --no-sync python scripts/check_frontend.py` | 89 个 ES 模块语法通过 |
| `uv run --no-sync python scripts/check_ui_contract.py` | PASS（V1 契约） |
| `uv run --no-sync pytest -q -n 0 agent/tests/v2/ui/test_v2_navigation_ui.py` | 2 passed（390/1024，26 页可达） |
| `uv run --no-sync pytest -q -n 0 agent/tests/v2/ui/test_v2_ui.py::test_workbench_chat_memory_and_theme` | 8 passed（宽/窄 × 深/浅） |
| `uv run --no-sync pytest -q -n 0 agent/tests/v2/ui/test_v2_history_ui.py` | 3 passed |
| `pytest test_ui_design_tokens / test_ui_glass_tokens / test_ui_theme` | 24 passed |
| `scripts/project_index.py --check` | stale=空（已再生并复查） |

## 4. 后端缺口（已暂停，未修改协议）

| # | 缺口 | 影响 | 现状处理 |
| --- | --- | --- | --- |
| G1 | Ticket 投影未返回 `owa_tasks.run_id`（task_key → 子运行映射），且 HTTP 未传 `include_results` | 无法把"计划任务"与"Worker 子运行"一一对应，也无法显示任务级结果 | 分两组展示，UI 明示"不推测对应"；等后端加字段后再合并 |
| G2 | 任务/子运行无 `started_at/ended_at`，delegations 无耗时字段 | 无法显示真实耗时 | 展开子运行时按服务端事件时间计算"事件跨度"，明确标注为估算，非计时器 |
| G3 | Ticket/子运行投影无工具调用计数 | 无法在主视图显示每个 Worker 的工具数 | 按需拉取子运行 `/events`（有界、折叠），不做聚合假数 |
| G4 | 无 Ticket 级取消端点（只有 run 级） | 多 Ticket/多目标场景无法只取消某一个 Ticket 的 Worker | 复用 run 级取消（后端实现会先标记该根 run 下 OWA 执行为取消）；单 Ticket 场景语义正确 |
| G5 | Worker 并发/在线数的直接字段缺失 | 状态条的执行中数量来自任务状态派生（可能滞后一拍） | 标注为任务状态派生；不做"实时并发"承诺 |
| G6 | Worker profile 只有标识与版本，无展示名/角色 | 只能显示 `researcher · 1` 这类标识 | 原样显示标识，不编造名字 |

以上 G1–G3 是"完整展示 Worker 执行耗时/工具调用/任务级结果"所必需；按要求**暂停该部分**，等协议评审后再做。
G4–G6 有可接受的前端降级方案，已实现并如实标注。

## 5. 风险与回滚

- 真实 OWA Ticket 数据未在本次验证（隔离实例无历史 Ticket）；mock 覆盖了状态/取消/终态组合，但未跑真实模型委派。
  建议在真实 OWA 会话上做一次人工验收：运行中 → 取消 → 终态 run + Worker 仍在跑。
- 回滚：删除 `web/ui/v2/omniweave.js`，还原 `web/ui/v2/{index.html,main.js,workbench.css}` 与 `web/ui/tokens.css`，
  并还原各页 `tokens.css?v=` 即可；无数据库/协议变更，回滚无副作用。
- 其他会话未提交的后端修改（`omniweave_*.py`、`test_v2_omniweave_*.py`、`TYPE_CHECKING.md`）本次未触碰。

## 6. 下一步（Phase 2 预告，待确认）

- 审批卡人话化（动作/目标/风险/费用/撤销范围）、单对象锚定、延期；
- Stop → Resume → Retry（对象级、写明重跑范围）与输入队列（失败项阻塞、可编辑/删除）；
- 与 G1/G2/G3 后端字段就绪后，把 Worker 任务与子运行合并为一条时间线。
