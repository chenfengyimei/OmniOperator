# V2 模型侧历史会话检索（K09）

## 结论与范围

K09 在已有所有者历史 API（`GET /agents/{id}/sessions`、`GET .../sessions/{id}/messages`、audit/events 流）之外，补上**模型侧** `session_search` 只读工具，让模型在回合内回忆本 Agent 空间的过往会话。它**不等同 `memory_search`**：memory_search 检索的是固化记忆条目并注册上下文捕获，session_search 检索的是原始会话消息并返回原文锚点，不做记忆写入、不注册 context 捕获。

参考 `xuexi/hermes-agent/tools/session_search_tool.py`（固定提交 `9976f4857b72439d79016354d7b6e1a3ee952e2f`）的单工具四形态设计，在 V2 四层内独立适配，未复制第三方源码、未拉取远程、未用真实账号/模型。V2 与参考的存储差异决定了适配边界（见下）。

## 四形态（由参数推断，无 LLM 调用）

| 形态 | 触发参数 | 返回 |
| --- | --- | --- |
| discover | `query` | 按会话去重的匹配命中，每条含原文锚点 `match_message_id`/`match_seq`/`matched_role` 与有界 `snippet` |
| scroll | `session_id` + `around_seq` | 以锚点 seq 为中心的 ±window 消息窗口，含 `messages_before`/`messages_after` 边界计数 |
| read | `session_id` | 会话 head+tail 消息（超量截断并标记 `truncated`/`message_count`） |
| browse | 无参数 | 最近会话元数据（title/created_at/message_count/preview） |

所有形态返回真实数据库消息原文（有界截断），不经过模型生成。

## 四层接线

- **domain** `domain/session_search.py`：纯规则。`parse_request` 推断形态、clamp 数值界、归一化角色/排除列表、拒绝越界/控制字符；`query_tokens` Unicode 分词（≤20）；`truncate_content`/`shape_message` 逐消息内容上限与锚点标记；`group_discover` 按会话去重并限流；`browse_entry`/`render` 预览截断与文本渲染。无 I/O。
- **application** `application/session_search.py`：`SessionSearchCapabilities.invoke` 经 `DatabasePort.read` 调仓储，用 domain shaping 组装 `ActionResult(OK, text, data)`；`invalid_session_query`/`not_found`/`session_deleted`/`context_invalidated` 映射为 ERROR，其余抛出。只读，无写事务、无捕获注册。
- **adapters** `adapters/persistence/session_search_repository.py`：`SessionSearchRepositories(RepositoryBase)` 四方法。全部 `agent_id` 显式过滤 + `_one` 会话归属校验（跨 Agent 返回 404）；`deleted_at IS NULL` 且 `seq > sessions.context_floor_seq` 遵守删除屏障；discover/browse 隐藏 `run_delegations.child_session_id` 委派子会话（非用户交互历史）。discover 用有界 token LIKE（`contains(autoescape=True)` OR）扫描 ≤300 行后交 domain 分组；scroll 校验锚点存在/未擦除/高于 floor，窗口与前后计数；read 计总数并按 head/tail 取首尾；browse 逐会话计数与首条预览。
- **composition** `composition/capabilities.py`：新增只读 `ToolSpec("session_search", writes=False, resource="")`，适配器为 `SessionSearchCapabilities(database)`，进入既有 `builtin_capabilities` 组装链。`domain/routing.py` 将 `session_search` 纳入 `RETRIEVAL_TOOLS`（agent 档全量、retrieval 档可见；chat 档与远程 QQ 空注册表不提供）。

`engine._execute` 无需改动：工具按 `registry[name].adapter.invoke` 派发，复用原 schema 校验/prepare/资源锁/authorize/observe/逐项超时/settle 生命周期。K05 批次并行中 `session_search` 作为只读能力按 `resource or name`（即 `session_search`）参与独立并发，与 `memory_search` 键不同可并行，重复键串行。

## 限额

- discover 结果 `limit` 默认 3、上限 10；原始扫描 ≤300 行；snippet ≤600 字符。
- scroll `window` 默认 5、上限 20；read `head`/`tail` 默认 20/10、上限 50；逐消息内容 ≤2000 字符（超出截断并标记 `content_truncated`/`original_content_chars`）。
- browse ≤10 会话、preview ≤200 字符；`query` ≤500 字符；`exclude_session_ids` ≤20；token ≤20。

## 删除屏障与隔离

- Agent 隔离是结构性的：复合 `(id, agent_id)` 外键 + 显式 `agent_id` WHERE + `_one` 归属校验，跨 Agent id 返回 `not_found`。
- 擦除消息被置空 `content` 并设 `deleted_at`，读取一律 `deleted_at IS NULL` 过滤。
- 擦除同时把会话 `context_floor_seq` 抬到 `next_seq`（V2 上下文屏障语义），因此 `seq > floor` 过滤会把该会话整段 prior 历史排除出 recall——与「不要重建被遗忘内容」一致。scroll 锚点若已擦除或 ≤floor 返回 `context_invalidated`。
- 委派子会话（subagent）从 discover/browse 隐藏；read/scroll 按显式 id 仍受 Agent 隔离与屏障约束。

## 明确不在本项范围（非参考全集对等）

- V2 `messages` 无 FTS 索引：discover 用有界 token LIKE + 按会话分组，**非**参考 FTS5/bm25 排序、lineage 去重、自适应/top 全量 hydrate、bookend、标题匹配。
- V2 `sessions` 无 `source`/`parent_session_id`/`end_reason` 列：无压缩 lineage 行走、无 cron 降级、无 fresh-reset/compacted 行区分、无跨 profile 只读岛、无 `after`/`before` 时间界、无 `@session:` link 渲染。
- 不做读取后追溯 scrub（并发擦除不回填已返回的工具结果），不注册 context 捕获（区别于 memory/document 的 `capture_tool_memories`/`document_capture`）。
- 无新表、无数据库迁移、无新 HTTP 路由（模型工具，所有者历史 API 已存在）。

## 完成条件

生产工具/组装链/档位/派发已接；原文锚点、分页/大小限额、Agent/会话隔离与删除屏障均有专项回归；错误/取消由原 `_execute` 生命周期承接（只读失败结算为 ERROR，不重放）。仅类/提示词/空工具名不算完成。本项关闭不代表整个内核对标或 V2 完成；真实模型/设备/72 小时验收另列。