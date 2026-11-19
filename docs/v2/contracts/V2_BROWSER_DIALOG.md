# V2 浏览器原生对话框（K13.browser_dialog）

状态：已实现并专项通过（2026-11-20）。学习参考 hermes-agent `tools/browser_dialog_tool.py` 的响应语义（accept/dismiss、prompt_text 仅对 prompt、多对话框 dialog_id 消歧），独立实现于 V2 既有 Playwright browser worker，不复用 V1 ToolRegistry，也不依赖参考的 CDP supervisor（V2 无 browser_cdp，另列缺口）。

## 行为变更（重要）

此前 V2 worker 未注册 dialog 监听器，Playwright **自动驳回**所有原生对话框。本批起对话框被**持有等待显式响应**（参考 supervisor 语义）：

- 页面创建时注册 `page.on("dialog")`，对话框入 per-agent 有界队列（`DIALOG_QUEUE_LIMIT=8`，超出者自动 dismiss 防无限阻塞），记录 id/type/message/default_value。
- **持有期间**：`browser_navigate`/`browser_click`/`browser_fill` 在**派发前**拒绝（ERROR/NONE，`invalid_capability_request`），绝不向阻塞页面派发新动作；`browser_read` **不做页面求值**（对话框阻塞渲染器，求值会挂起），直接返回队列信息 `{url, pending_dialogs:[{dialog_id,type,message≤200,default_value≤200}]}`。
- 动作**触发**对话框时：Playwright 的 click/fill 等待渲染器确认会被对话框阻塞至 10 秒超时；此时**队列中的对话框本身证明输入已送达页面**（onclick 已执行），结算为 OK/APPLIED 并附 `pending_dialogs` 计数，而非 unknown。无对话框的超时仍按原 unknown 合同。局限：与动作无关的自发对话框恰与无关超时同时发生时可能误判送达（对话框证明页面 JS 在运行，风险有界）。
- 导航期间页面加载即触发对话框会阻塞 `goto` 至 30 秒超时 → unknown 结算（对话框仍持有可响应），**不重放**；测试夹具用按钮触发避免加载期对话框。

## browser_dialog 工具

- `writes=True`、resource `browser`、agent 档（同 click/fill；不入 RETRIEVAL/CHAT），**复用 expected_url CAS**（页面自审批后变化即拒）。
- 参数：`action` enum accept/dismiss（必填）、`expected_url`（必填）、`prompt_text` ≤2000 字符（仅 prompt 类型生效，其余忽略；**缺省为空串**——Playwright `accept()` 语义，与参考默认一致，非页面 defaultValue）、`dialog_id`（多对话框必填）。
- 无持有 → 拒；多持有无 id → 显式回执 `browser_dialog_ambiguous` **列出候选 id**（专用错误码，不经通用异常掩盖）；未知 id → 拒；非法 action/超长/非串 prompt_text → 派发前拒。
- 响应经 `dialog.accept(text)`/`dismiss()`，`effects.dispatched=True` 先行（响应中途异常 → unknown 不重放），成功 OK/APPLIED，verification=page.url，data={dialog_id,type,action}，出队。
- confirm accept/dismiss 驱动页面状态（true/false）、prompt dismiss 返回 null、beforeunload 类型可响应（accept 允许离开）。

## 实现位置

- `adapters/workers/main.py`：`PendingDialog` dataclass、`_dialog_rows` 有界渲染、`_dialogs`/`_dialog_seq` per-agent 状态、页面创建注册监听、navigate/read/click/fill 持有守卫与超时证据结算、`browser_dialog` 分支。
- `composition/capabilities.py`：新增 spec；`domain/routing.py` 不变（browser_read 已在 RETRIEVAL_TOOLS，写工具默认 agent 档）。

无新表/迁移/HTTP 路由/worker 协议帧/源文件/跨平台改动。declared_tools 94→95。

## 验证

- 新增 9 项回归（真实 Chromium + 127.0.0.1 本地 HTTP 夹具，离线）：alert 持有/列出/派发前拒/accept、confirm accept/dismiss 驱动页面、prompt text/dismiss null/缺省空串、prompt_text 对 alert 忽略、五类非法参数拒且对话框保持、多对话框 id 消歧（白盒 FakeDialog）、队列上限溢出自发对话框自动 dismiss（白盒+定时器）、无持有 read 形状不变、spec 注册写/档位。
- 调用方：browser_dialog 9 + worker_safety 31 = **40 过**（91.68 秒，format 后终源复跑）；另 routing 16 + capabilities + business_inventory + architecture + candidate_gate 合 **80 过 0 失败**（47.99 秒，工具计数守卫未 pin 94）。
- strict（pyrightconfig.strict.json，仓库根）：本批文件零错误；余 5 错误均既有他会话已提交 repository.py（6bcb0e98），不追逐。ruff check/format 通过。
- 首轮修正：click 被对话框阻塞 10 秒超时误判 unknown → 超时+持有队列证据结算 APPLIED；多对话框候选 id 被通用异常文本掩盖 → 专用 `browser_dialog_ambiguous` 显式回执；prompt 缺省误期望页面 defaultValue → Playwright accept() 空串语义；溢出用例误用 click（持有守卫派发前拒）→ 改页面定时器自发对话框。

## 明确不做

参考 CDP supervisor/browser_cdp/browser_exec 门控（V2 无 CDP 面，另列缺口）；对话框截图/视觉；跨 agent 共享浏览器；真实网站对话框质量另验。