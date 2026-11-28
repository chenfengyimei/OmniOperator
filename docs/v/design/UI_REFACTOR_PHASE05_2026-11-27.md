# UI 重构 Phase 5：完整产品验收与发布质量收尾

日期：2026-11-27 ｜ 依据：Phase 0–4 实施记录 ｜ 结论：**CONDITIONALLY READY（有条件可发布）**
本阶段以验收为主：**未修改任何业务/后端/前端代码**，仅新增验收脚本与截图（`output/playwright/phase5-accept/`）。

## 1. Phase 5A 真实 Agent 端到端验收

环境：隔离实例（`--home %TEMP%\omni-phase5-accept`，真实后端代码、真实 DB、真实 API、真实 V2 前端），未使用路由 Mock；所有写入仅发生在隔离实例，实例已销毁。

| 步骤 | 证据 | 结果 |
| --- | --- | --- |
| 真实 Agent 列表 | `GET /agents` → agent id | ✅ |
| 真实 Turn 提交 | `POST /agents/{a}/turns`（mode=agent）→ **HTTP 202**，`run_id=b6678f79…`、`session_id=4a48c8c7…` | ✅ 真实受理 |
| 真实运行链 | `GET /runs/{id}` 轮询 → `queued → running → failed`；事件 `run.queued / routing.selected / run.running / run.failed` | ✅ 真实调度链执行 |
| 终态 | `state=failed`，`error_code=model_not_configured`，completion=not_required | ⚠️ 环境缺模型凭据 |
| Worker / Ticket / 审批 | `GET /delegations` → 0；`GET /approvals` → 0 | ⛔ **BLOCKED** |
| Stop / Resume / Retry 真实链 | 无可运行的模型回合，无法触达真实审批/Worker 取消 | ⛔ **BLOCKED** |

**结论**：真实 API → 真实后端调度 → 真实 run 生命周期已跑通；**主 Agent 与 Worker 协作、真实审批、真实停止、多 Worker 依赖顺序、异常恢复等场景 BLOCKED**：隔离实例未配置模型凭据/OWA dispatch，后端立即以 `model_not_configured` 终止，未产生 Worker 与审批。按任务要求**未用 Mock 冒充真实验收**。解锁条件：在隔离实例配置可用的测试模型凭据并启用 OWA dispatch，再执行同一脚本补测。

## 2. Phase 5B 性能基线（小数据真实实例）

| 指标 | 实测 |
| --- | --- |
| 页面启动（到可交互） | **740 ms** |
| DOMContentLoaded / load | 365 ms / 378 ms |
| 初始 DOM 节点 | 1,961 |
| 初始资源请求 | 80 |
| 轮询频率 | 6 秒 6 次（1 Hz，符合现状设计） |
| JS 堆内存 | 4.6 MB |
| 26 页平均切换（含 220ms 等待） | **254 ms** |
| 控制台/页面错误 | 0 |

**未验证（BLOCKED，未伪造）**：10,000 日志 / 5,000 审计 / 1,000 Worker 事件 / 100 任务记录 / 长会话 / 并行 Worker 的压力场景未构造。当前无后端/DB 修改权限，未向真实用户库写入数据；建议后续在隔离实例用测试数据生成脚本补齐，并建立首屏/列表/搜索/分页/DOM 增长/内存/CPU 基线门槛。

## 3. Phase 5C 26 页视觉终审

- 26/26 页：**唯一 H1、1440 无横向溢出、390/430/768/1024 无横向溢出、控制台 0 错误**（`p5-acceptance.json`）。
- 深/浅主题：均无溢出；主题切换正常。
- 代表页截图：chat/settings/logs/audit/capabilities @1440 深色、chat @390/768、导航抽屉 @390。
- 遗留问题：V1 冻结副本 5 条检测残留（V2 未使用/已被覆盖，非当前页面实际缺陷）；extensions/mcp/action-reviews 仍偏卡片表单结构；大数据的表格/时间线真实形态未验证；长表单局部按钮层级可继续打磨。不视为阻断。

## 4. Phase 5D 真机与无障碍

- 真机（Android/iPhone/平板）、NVDA、VoiceOver：**本环境不可用，UNVERIFIED**（未编造结果）。
- 已用自动化覆盖（Phase 4，38/38）：四视口布局、移动抽屉、Inspector 全屏、触控 44px、skip link、弹窗焦点恢复、Esc 层级、无正数 tabindex、图标按钮/输入命名、消息区无整体 aria-live、摘要播报、对比度 ≥4.5:1、200% 缩放、reduced-motion。
- 发布前仍需真机 + 读屏人工验收（重点：软键盘、安全区、抽屉、审批播报、Worker 状态播报）。

## 5. 功能与安全回归

主 Agent 单会话、真实取消 API、Retry 新 Turn 语义、不支持 Resume 的明示、本地队列非服务端队列、Worker 结果归属、审批权限边界：代码层与 Phase 1–4 已验证项保持；本轮真实链路因模型凭据缺失无法完整验，**权限未扩大/未改协议**。

## 6. 缺口管理（G1–G6 / A1–A7）

| 缺口 | 状态 |
| --- | --- |
| G1 任务↔子运行映射、G2 任务级耗时、G3 工具计数、G5 并发数、G6 Worker 展示名 | **需要后端支持**（未修改协议，等待批准） |
| G4 Ticket 级取消 | 复用 run 级取消，单 Ticket 语义可接受；多 Ticket 需后端支持 |
| A1 审批风险/原因字段、A2 延后 API、A6 结构化退出码 | **需要后端支持** |
| A3 手动 Resume | **需要后端支持**（当前仅自动恢复，UI 已明示不支持） |
| A4 run Retry | 已用“新 Turn 重新执行”前端降级，可接受 |
| A5 服务端队列管理 | 本地队列已明确标注，可接受；服务端队列需后端支持 |
| A7 位置/草稿服务端语义 | 需要未来版本处理 |

无“阻断发布”级别的新缺口；无数据库变更需求。

## 7. 修改文件清单

**无代码修改**（Phase 5 为验收阶段）。新增仅验收资产：
`output/playwright/phase5-accept/{accept.mjs, p5-acceptance.json, run.log, *.png}`、本报告与发布就绪总结。

## 8. 截图与测试

- 截图：`output/playwright/phase5-accept/`（26 页结构检查 + 8 张代表页/移动截图）。
- 测试：Phase 4 `38/38`；Phase 3D `69/69`；`test_v2_navigation_ui.py` 2 passed；`check_frontend.py` 93 模块；本轮 26 页结构 + 四视口 + 主题 + 控制台全部通过（`p5-acceptance.json`）。

## 9. 最终发布判定

**CONDITIONALLY READY**：
- 核心 UI、页面结构、主题、四视口、Phase 1/2 交互能力、自动化无障碍检查满足发布质量；
- 阻断项为**真实 Agent/Worker/OWA/审批/停止 E2E 因测试环境缺模型凭据而 BLOCKED**，以及真机/NVDA/VoiceOver 未验收、大数据性能未测；
- 未发现核心功能错误、权限扩大、状态不一致、数据丢失或严重交互缺陷。

## 10. 回滚方案

本阶段无代码变更，回滚无需操作。若需整体回退 UI 重构：按 Phase 01–04 各自记录逆向还原 `web/ui/v2` 相关文件与共享 `tokens.css` 追加内容；后端/数据库未变，无数据迁移。
