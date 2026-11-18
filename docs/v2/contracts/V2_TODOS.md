# V2 会话任务清单

## 用途与入口

K03 的生产入口是模型内部控制 `todo_list`，以及所有者只读接口 `GET /api/v1/agents/{agent_id}/sessions/{session_id}/todos`。生产默认内核在 agent 档位提供控制；chat/retrieval、QQ、被禁用的工具以及自定义注入工具集不自动获得它。

计划存在同一个 `omni.db` 的 `session_todos`，每个 Agent/会话一个当前快照。它记录模型对任务的安排和进度，完成标记不证明实际工作成功。与 `request_mode` 一样，它是内部控制；不派发外部动作，不生成外部写入审批，也不能批准随后文件、Shell、设备或投递动作。实际动作继续经过原观察、审批、预算和后验链。

## 读写协议

读取：`todo_list({})`，返回 `{session_id, revision, todos}`；首次为空且 revision 为 0。

替换：

```json
{"revision": 0, "todos": [{"id": "inspect", "content": "核对相关实现", "status": "in_progress"}, {"id": "implement", "content": "实现已核实的缺口", "status": "pending"}]}
```

合并更新：

```json
{"revision": 1, "merge": true, "todos": [{"id": "inspect", "status": "completed"}, {"id": "implement", "status": "in_progress"}]}
```

- 写入必须携带实际观察到的 revision；过期返回 `revision_conflict`，先重新读取，不覆盖新状态。没有变化不递增修订号。
- `merge: true` 按 id 更新指定字段，保留未提及条目和其顺序；新条目需要 content，status 默认为 pending。默认替换完整清单；空数组清空。
- 状态为 pending / in_progress / completed / cancelled。parent 指向同清单的 id，空字符串解除父级。重复 id、失效父级、环和超过 16 层的层级整体拒绝。
- ID 为 1–100 个字母、数字或 `_.:-`；每项正文 1–4000 字符，不接受空白、NUL 或无法编码的文本。清单最多 256 项，紧凑 ASCII JSON 最多 24000 字节，超限明确拒绝，不静默截断落库内容。
- 平铺列表将当前 in_progress 步骤移到更早的 pending 占位之前；层级列表保持作者顺序。

所有检查、修订比较、快照更新与审计在同一写事务内完成，并发旧修订只有一个能提交。Agent 启用状态、模型授权、会话/来源代次、运行状态、租约、当前策略与工具档位在事务内重新校验。Agent 被禁用后，旧运行不能再修改计划。跨会话 InvocationContext 在写入前拒绝；所有者只读 API 保留身份验证与 Agent 隔离。

## 压缩、重启和删除

ContextService 的容量准备包含计划尾部通知，正文不会写进冻结的系统前缀，也不作为历史摘要的指令。每次模型调用前重新读当前计划，只把未完成条目及其必要祖先写入通知。通知最多 8000 字节，单项只预览 160 字符，并明确 `content_truncated` 与 `omitted_items`；通过控制工具可以读取完整快照。已完成/取消的孤立条目不重新注入，全部完成或清空时仍给出当前修订与空活动列表。

计划来源于持久会话，历史压缩和进程重启不会丢失，也不从任意工具文本或用户粘贴的 JSON 恢复。来源删除触发原会话失效屏障时，同事务清空计划、递增修订并更新来源 epoch。旧运行后续回包不能恢复删除的计划或触发下一次付费调用；已发送的费用继续按原规则结算。

内部控制只记录 `todo.updated` 的修订、条目数和 run 身份，不向动作参数、审计内容或事件内容重复保存计划正文。当前阶段提供模型工具和只读 API；没有新增清单编辑界面。

## 架构、升级与验证

- domain/todos：纯校验、合并、层级、排序和有界通知。
- application/todos：模型控制用例；engine 编排调用并保留系统前缀；context 将通知纳入容量估算。
- adapters/persistence/todo_repository：同库、同事务、授权与修订；retention 统一处理删除。冻结迁移 `v2_0041` 从 `v2_0040` 增加表，不改已有消息或用户数据。
- composition/instance：生产默认组装，注入测试工具集维持原隔离约定。HTTP 仅转发只读用例。

已有 Windows 服务未停止，真实运行数据库未由本批测试升级；重启到新源码时沿原升级/快照机制执行。专项结果和原始失败保留在 [证据](../../evidence/v2-todos.json)。本批不声明全量、真实模型或子代理委派验收；K04 仍需独立实现父子 run 和资源汇合。
