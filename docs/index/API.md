# API、对话调用与工具声明

> 由 `uv run --no-sync python scripts/project_index.py` 生成。不要手改；分类改 system/catalog/schema.py，分层改 ARCHITECTURE.md。

工作台 API 统一受配对/Host/Origin 规则保护；终端 WS 单独握手校验。社区服务器是独立服务。请求字段为源码声明，完整 schema 可查询 FastAPI OpenAPI。

## 对话与模型

Web/QQ/CLI 的请求经 Runtime 进入同一内核，模型请求经 Gateway 记账。

`create_conversation_router.chat`（[agent/src/omni/channels/web_api/conversation.py](../../agent/src/omni/channels/web_api/conversation.py#L44)） → `Agent.run_turn`（[agent/src/omni/core/agent.py](../../agent/src/omni/core/agent.py#L523)） → `Agent._chat`（[agent/src/omni/core/agent.py](../../agent/src/omni/core/agent.py#L710)） → `Gateway.complete`（[agent/src/omni/llm/gateway.py](../../agent/src/omni/llm/gateway.py#L339)）

## 模型调用工具

模型工具调用经统一注册表和审批；结果按原调用顺序回填。

`Agent._execute_tool_calls`（[agent/src/omni/core/agent.py](../../agent/src/omni/core/agent.py#L910)） → `ToolRegistry.execute`（[agent/src/omni/tools/registry.py](../../agent/src/omni/tools/registry.py#L142)） → `WebApprover`（[agent/src/omni/safety/gates.py](../../agent/src/omni/safety/gates.py#L123)） → `ToolResult`（[agent/src/omni/tools/result.py](../../agent/src/omni/tools/result.py#L35)）

## 流式与取消

流式响应和持久运行事件是两条接口；停止通过协作取消传播。

`create_conversation_router.chat_stream`（[agent/src/omni/channels/web_api/conversation.py](../../agent/src/omni/channels/web_api/conversation.py#L79)） → `Agent.run_turn`（[agent/src/omni/core/agent.py](../../agent/src/omni/core/agent.py#L523)） → `Gateway.stream`（[agent/src/omni/llm/gateway.py](../../agent/src/omni/llm/gateway.py#L425)） → `RunStore`（[agent/src/omni/memory/runs.py](../../agent/src/omni/memory/runs.py#L87)）

## 交互式终端

WebSocket 先校验身份，再由会话池创建独立 ConPTY；断开时核查退出。

`create_terminal_router.terminal_ws`（[agent/src/omni/channels/terminal/routes.py](../../agent/src/omni/channels/terminal/routes.py#L15)） → `WebAuth.check_websocket`（[agent/src/omni/channels/web_auth.py](../../agent/src/omni/channels/web_auth.py#L327)） → `TerminalManager.open`（[agent/src/omni/channels/terminal/pool.py](../../agent/src/omni/channels/terminal/pool.py#L35)） → `TerminalSession`（[agent/src/omni/channels/terminal/session.py](../../agent/src/omni/channels/terminal/session.py#L8)） → `open_process`（[agent/src/omni/channels/terminal/backend.py](../../agent/src/omni/channels/terminal/backend.py#L26)）

## 插件扩展接入

下载/收包、隔离校验、人工激活、命名空间注册，最终仍由工具审批链执行。

`CommunityClient.install`（[agent/src/omni/plugins/community/client.py](../../agent/src/omni/plugins/community/client.py#L159)） → `PluginHost.install_package`（[agent/src/omni/plugins/host.py](../../agent/src/omni/plugins/host.py#L213)） → `PluginHost.activate`（[agent/src/omni/plugins/host.py](../../agent/src/omni/plugins/host.py#L506)） → `PluginContext.add_tool`（[agent/src/omni/plugins/host.py](../../agent/src/omni/plugins/host.py#L129)） → `ToolRegistry.execute`（[agent/src/omni/tools/registry.py](../../agent/src/omni/tools/registry.py#L142)）

## 技能数据接入

技能是提示与参考数据；导入不执行脚本，激活需人工确认。

`register_skill_inbox_tools`（[agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L354)） → `SkillInbox`（[agent/src/omni/memory/skill_inbox.py](../../agent/src/omni/memory/skill_inbox.py#L108)） → `SkillStore`（[agent/src/omni/memory/skills.py](../../agent/src/omni/memory/skills.py#L84)）

## 外部 MCP 能力

渐进发现与授权校验后调用外部进程；结果未知不重放写操作。

`register_mcp_tools`（[agent/src/omni/tools/mcp/tools.py](../../agent/src/omni/tools/mcp/tools.py#L18)） → `MCPManager`（[agent/src/omni/tools/mcp/client.py](../../agent/src/omni/tools/mcp/client.py#L252)） → `MCPSchemaStore`（[agent/src/omni/memory/state.py](../../agent/src/omni/memory/state.py#L1511)）

## 声明路由

| 服务 | 方法 | 路径 | 处理函数 | 定位 |
|---|---|---|---|---|
| community | GET | `/api/download/{plugin_id}` | `create_app.download` | [agent/src/omni/plugins/community/server.py](../../agent/src/omni/plugins/community/server.py#L146) |
| community | GET | `/api/health` | `create_app.health` | [agent/src/omni/plugins/community/server.py](../../agent/src/omni/plugins/community/server.py#L112) |
| community | GET | `/api/plugins` | `create_app.search_plugins` | [agent/src/omni/plugins/community/server.py](../../agent/src/omni/plugins/community/server.py#L116) |
| community | GET | `/api/plugins/{plugin_id}` | `create_app.plugin_detail` | [agent/src/omni/plugins/community/server.py](../../agent/src/omni/plugins/community/server.py#L135) |
| community | POST | `/api/publish` | `create_app.publish` | [agent/src/omni/plugins/community/server.py](../../agent/src/omni/plugins/community/server.py#L174) |
| workbench | GET | `/api/android/devices` | `create_android_router.devices` | [agent/src/omni/channels/web_api/android.py](../../agent/src/omni/channels/web_api/android.py#L40) |
| workbench | POST | `/api/android/devices/{serial}/actions` | `create_android_router.action` | [agent/src/omni/channels/web_api/android.py](../../agent/src/omni/channels/web_api/android.py#L63) |
| workbench | GET | `/api/android/devices/{serial}/apps` | `create_android_router.apps` | [agent/src/omni/channels/web_api/android.py](../../agent/src/omni/channels/web_api/android.py#L58) |
| workbench | GET | `/api/android/devices/{serial}/observe` | `create_android_router.observe` | [agent/src/omni/channels/web_api/android.py](../../agent/src/omni/channels/web_api/android.py#L45) |
| workbench | GET | `/api/android/devices/{serial}/snapshot` | `create_android_router.snapshot` | [agent/src/omni/channels/web_api/android.py](../../agent/src/omni/channels/web_api/android.py#L50) |
| workbench | GET | `/api/android/devices/{serial}/stream` | `create_android_router.stream` | [agent/src/omni/channels/web_api/android.py](../../agent/src/omni/channels/web_api/android.py#L69) |
| workbench | GET | `/api/android/devices/{serial}/stream-state` | `create_android_router.stream_state` | [agent/src/omni/channels/web_api/android.py](../../agent/src/omni/channels/web_api/android.py#L74) |
| workbench | POST | `/api/android/element` | `create_devices_router.android_element` | [agent/src/omni/channels/web_api/devices.py](../../agent/src/omni/channels/web_api/devices.py#L96) |
| workbench | POST | `/api/android/key` | `create_devices_router.android_key` | [agent/src/omni/channels/web_api/devices.py](../../agent/src/omni/channels/web_api/devices.py#L106) |
| workbench | GET | `/api/android/snapshot` | `create_devices_router.android_snapshot` | [agent/src/omni/channels/web_api/devices.py](../../agent/src/omni/channels/web_api/devices.py#L65) |
| workbench | GET | `/api/android/state` | `create_devices_router.android_state` | [agent/src/omni/channels/web_api/devices.py](../../agent/src/omni/channels/web_api/devices.py#L37) |
| workbench | GET | `/api/android/stream` | `create_devices_router.android_stream` | [agent/src/omni/channels/web_api/devices.py](../../agent/src/omni/channels/web_api/devices.py#L115) |
| workbench | POST | `/api/android/tap` | `create_devices_router.android_tap` | [agent/src/omni/channels/web_api/devices.py](../../agent/src/omni/channels/web_api/devices.py#L82) |
| workbench | GET | `/api/android/tree` | `create_devices_router.android_tree` | [agent/src/omni/channels/web_api/devices.py](../../agent/src/omni/channels/web_api/devices.py#L46) |
| workbench | GET | `/api/approvals` | `create_approvals_router.get_approvals` | [agent/src/omni/channels/web_api/approvals.py](../../agent/src/omni/channels/web_api/approvals.py#L18) |
| workbench | POST | `/api/approvals/{approval_id}/resolve` | `create_approvals_router.resolve_approval` | [agent/src/omni/channels/web_api/approvals.py](../../agent/src/omni/channels/web_api/approvals.py#L26) |
| workbench | GET | `/api/artifacts` | `create_artifacts_router.list_artifacts` | [agent/src/omni/channels/web_api/artifacts.py](../../agent/src/omni/channels/web_api/artifacts.py#L31) |
| workbench | DELETE | `/api/artifacts/{artifact_id}` | `create_artifacts_router.delete_artifact` | [agent/src/omni/channels/web_api/artifacts.py](../../agent/src/omni/channels/web_api/artifacts.py#L73) |
| workbench | GET | `/api/artifacts/{artifact_id}/image` | `create_artifacts_router.artifact_image` | [agent/src/omni/channels/web_api/artifacts.py](../../agent/src/omni/channels/web_api/artifacts.py#L60) |
| workbench | POST | `/api/auth/logout` | `create_app.auth_logout` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L198) |
| workbench | POST | `/api/auth/pair` | `create_app.auth_pair` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L183) |
| workbench | GET | `/api/auth/status` | `create_app.auth_status` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L175) |
| workbench | GET | `/api/capabilities` | `create_capabilities_router.capabilities` | [agent/src/omni/channels/web_api/capabilities.py](../../agent/src/omni/channels/web_api/capabilities.py#L12) |
| workbench | POST | `/api/chat` | `create_conversation_router.chat` | [agent/src/omni/channels/web_api/conversation.py](../../agent/src/omni/channels/web_api/conversation.py#L44) |
| workbench | POST | `/api/chat/stream` | `create_conversation_router.chat_stream` | [agent/src/omni/channels/web_api/conversation.py](../../agent/src/omni/channels/web_api/conversation.py#L79) |
| workbench | GET | `/api/command-jobs` | `create_command_jobs_router.list_jobs` | [agent/src/omni/channels/web_api/command_jobs.py](../../agent/src/omni/channels/web_api/command_jobs.py#L9) |
| workbench | GET | `/api/command-jobs/{identifier}` | `create_command_jobs_router.read_job` | [agent/src/omni/channels/web_api/command_jobs.py](../../agent/src/omni/channels/web_api/command_jobs.py#L13) |
| workbench | POST | `/api/command-jobs/{identifier}/cancel` | `create_command_jobs_router.cancel_job` | [agent/src/omni/channels/web_api/command_jobs.py](../../agent/src/omni/channels/web_api/command_jobs.py#L19) |
| workbench | GET | `/api/context` | `create_context_router.overview` | [agent/src/omni/channels/web_api/context.py](../../agent/src/omni/channels/web_api/context.py#L49) |
| workbench | GET | `/api/context/settings` | `create_context_router.get_settings` | [agent/src/omni/channels/web_api/context.py](../../agent/src/omni/channels/web_api/context.py#L60) |
| workbench | PUT | `/api/context/settings` | `create_context_router.update` | [agent/src/omni/channels/web_api/context.py](../../agent/src/omni/channels/web_api/context.py#L65) |
| workbench | GET | `/api/desktop/observe/{window}` | `create_devices_router.desktop_observe` | [agent/src/omni/channels/web_api/devices.py](../../agent/src/omni/channels/web_api/devices.py#L28) |
| workbench | GET | `/api/desktop/windows` | `create_devices_router.desktop_windows` | [agent/src/omni/channels/web_api/devices.py](../../agent/src/omni/channels/web_api/devices.py#L19) |
| workbench | GET | `/api/facts` | `create_memory_router.get_facts` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L19) |
| workbench | POST | `/api/facts` | `create_memory_router.create_fact` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L32) |
| workbench | GET | `/api/facts/detail` | `create_memory_router.get_facts_detail` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L43) |
| workbench | GET | `/api/facts/rows` | `create_memory_router.get_fact_rows` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L23) |
| workbench | DELETE | `/api/facts/{fact_id}` | `create_memory_router.delete_fact` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L48) |
| workbench | GET | `/api/llm/usage` | `create_runs_router.llm_usage` | [agent/src/omni/channels/web_api/runs.py](../../agent/src/omni/channels/web_api/runs.py#L69) |
| workbench | GET | `/api/mcp/overview` | `create_mcp_router.mcp_overview` | [agent/src/omni/channels/web_api/mcp.py](../../agent/src/omni/channels/web_api/mcp.py#L52) |
| workbench | GET | `/api/mcp/{server}/catalog` | `create_mcp_router.mcp_catalog` | [agent/src/omni/channels/web_api/mcp.py](../../agent/src/omni/channels/web_api/mcp.py#L42) |
| workbench | POST | `/api/mcp/{server}/confirm-schema` | `create_mcp_router.mcp_confirm_schema` | [agent/src/omni/channels/web_api/mcp.py](../../agent/src/omni/channels/web_api/mcp.py#L108) |
| workbench | POST | `/api/mcp/{server}/connect` | `create_mcp_router.mcp_connect` | [agent/src/omni/channels/web_api/mcp.py](../../agent/src/omni/channels/web_api/mcp.py#L30) |
| workbench | POST | `/api/mcp/{server}/disconnect` | `create_mcp_router.mcp_disconnect` | [agent/src/omni/channels/web_api/mcp.py](../../agent/src/omni/channels/web_api/mcp.py#L37) |
| workbench | GET | `/api/mcp/{server}/health` | `create_mcp_router.mcp_health` | [agent/src/omni/channels/web_api/mcp.py](../../agent/src/omni/channels/web_api/mcp.py#L19) |
| workbench | GET | `/api/mcp/{server}/logs` | `create_mcp_router.mcp_logs` | [agent/src/omni/channels/web_api/mcp.py](../../agent/src/omni/channels/web_api/mcp.py#L25) |
| workbench | POST | `/api/mcp/{server}/reset` | `create_mcp_router.mcp_reset` | [agent/src/omni/channels/web_api/mcp.py](../../agent/src/omni/channels/web_api/mcp.py#L124) |
| workbench | GET | `/api/mcp/{server}/tools` | `create_mcp_router.mcp_tools` | [agent/src/omni/channels/web_api/mcp.py](../../agent/src/omni/channels/web_api/mcp.py#L77) |
| workbench | GET | `/api/memory-system/dialogue/days` | `create_memory_system_router.dialogue_days` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L74) |
| workbench | GET | `/api/memory-system/dialogue/entries` | `create_memory_system_router.dialogue_entries` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L84) |
| workbench | GET | `/api/memory-system/dialogue/search` | `create_memory_system_router.dialogue_search` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L94) |
| workbench | GET | `/api/memory-system/dialogue/sessions` | `create_memory_system_router.dialogue_sessions` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L78) |
| workbench | GET | `/api/memory-system/notes` | `create_memory_system_router.notes` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L130) |
| workbench | GET | `/api/memory-system/notes/{kind}/{period}` | `create_memory_system_router.note_detail` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L142) |
| workbench | POST | `/api/memory-system/notes/{kind}/{period}/retry` | `create_memory_system_router.note_retry` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L151) |
| workbench | GET | `/api/memory-system/overview` | `create_memory_system_router.overview` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L40) |
| workbench | GET | `/api/memory-system/people` | `create_memory_system_router.people_list` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L169) |
| workbench | POST | `/api/memory-system/people` | `create_memory_system_router.people_create` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L173) |
| workbench | DELETE | `/api/memory-system/people/{person_id}` | `create_memory_system_router.person_delete` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L202) |
| workbench | GET | `/api/memory-system/people/{person_id}` | `create_memory_system_router.person_detail` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L183) |
| workbench | PUT | `/api/memory-system/people/{person_id}` | `create_memory_system_router.person_update` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L191) |
| workbench | POST | `/api/memory-system/people/{person_id}/accounts` | `create_memory_system_router.account_link` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L208) |
| workbench | DELETE | `/api/memory-system/people/{person_id}/accounts/{account_id}` | `create_memory_system_router.account_unlink` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L218) |
| workbench | POST | `/api/memory-system/people/{person_id}/entries` | `create_memory_system_router.entry_add` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L224) |
| workbench | DELETE | `/api/memory-system/people/{person_id}/entries/{entry_id}` | `create_memory_system_router.entry_delete` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L244) |
| workbench | PUT | `/api/memory-system/people/{person_id}/entries/{entry_id}` | `create_memory_system_router.entry_edit` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L235) |
| workbench | GET | `/api/memory-system/runs` | `create_memory_system_router.journal_runs` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L163) |
| workbench | GET | `/api/memory-system/session-log/days` | `create_memory_system_router.session_log_days` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L102) |
| workbench | GET | `/api/memory-system/session-log/entries` | `create_memory_system_router.session_log_entries` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L112) |
| workbench | GET | `/api/memory-system/session-log/search` | `create_memory_system_router.session_log_search` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L122) |
| workbench | GET | `/api/memory-system/session-log/sessions` | `create_memory_system_router.session_log_sessions` | [agent/src/omni/channels/web_api/memory_system.py](../../agent/src/omni/channels/web_api/memory_system.py#L106) |
| workbench | GET | `/api/memory/retrieval` | `create_memory_router.retrieval_status` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L159) |
| workbench | GET | `/api/memory/status` | `create_memory_router.memory_status` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L55) |
| workbench | GET | `/api/profile` | `create_memory_router.get_profile` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L79) |
| workbench | POST | `/api/profile/line` | `create_memory_router.profile_line_create` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L92) |
| workbench | PUT | `/api/profile/line` | `create_memory_router.profile_line_update` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L101) |
| workbench | DELETE | `/api/profile/line/{line_id}` | `create_memory_router.profile_line_delete` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L111) |
| workbench | GET | `/api/publications` | `create_publications_router.publications` | [agent/src/omni/channels/web_api/publications.py](../../agent/src/omni/channels/web_api/publications.py#L16) |
| workbench | POST | `/api/publications/{identity}/edit` | `create_publications_router.publication_edit` | [agent/src/omni/channels/web_api/publications.py](../../agent/src/omni/channels/web_api/publications.py#L24) |
| workbench | POST | `/api/publications/{identity}/resolve` | `create_publications_router.publication_resolve` | [agent/src/omni/channels/web_api/publications.py](../../agent/src/omni/channels/web_api/publications.py#L38) |
| workbench | GET | `/api/resident` | `create_memory_router.get_resident` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L118) |
| workbench | POST | `/api/resident/update` | `create_memory_router.resident_update` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L128) |
| workbench | GET | `/api/reviews` | `create_memory_router.get_reviews` | [agent/src/omni/channels/web_api/memory.py](../../agent/src/omni/channels/web_api/memory.py#L149) |
| workbench | GET | `/api/runs` | `create_runs_router.run_list` | [agent/src/omni/channels/web_api/runs.py](../../agent/src/omni/channels/web_api/runs.py#L20) |
| workbench | POST | `/api/runs` | `create_conversation_router.run_create` | [agent/src/omni/channels/web_api/conversation.py](../../agent/src/omni/channels/web_api/conversation.py#L171) |
| workbench | GET | `/api/runs/{run_id}` | `create_runs_router.run_get` | [agent/src/omni/channels/web_api/runs.py](../../agent/src/omni/channels/web_api/runs.py#L28) |
| workbench | POST | `/api/runs/{run_id}/cancel` | `create_runs_router.run_cancel` | [agent/src/omni/channels/web_api/runs.py](../../agent/src/omni/channels/web_api/runs.py#L47) |
| workbench | GET | `/api/runs/{run_id}/events` | `create_runs_router.run_events` | [agent/src/omni/channels/web_api/runs.py](../../agent/src/omni/channels/web_api/runs.py#L35) |
| workbench | GET | `/api/sessions` | `create_sessions_router.get_sessions` | [agent/src/omni/channels/web_api/sessions.py](../../agent/src/omni/channels/web_api/sessions.py#L50) |
| workbench | POST | `/api/sessions` | `create_sessions_router.create_session` | [agent/src/omni/channels/web_api/sessions.py](../../agent/src/omni/channels/web_api/sessions.py#L64) |
| workbench | GET | `/api/sessions/{session_id}/messages` | `create_sessions_router.get_messages` | [agent/src/omni/channels/web_api/sessions.py](../../agent/src/omni/channels/web_api/sessions.py#L74) |
| workbench | POST | `/api/sessions/{session_id}/select` | `create_sessions_router.select_session` | [agent/src/omni/channels/web_api/sessions.py](../../agent/src/omni/channels/web_api/sessions.py#L68) |
| workbench | GET | `/api/sessions/{session_id}/todos` | `create_sessions_router.get_todos` | [agent/src/omni/channels/web_api/sessions.py](../../agent/src/omni/channels/web_api/sessions.py#L86) |
| workbench | GET | `/api/skills` | `create_skills_router.get_skills` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L126) |
| workbench | POST | `/api/skills` | `create_skills_router.skill_create` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L201) |
| workbench | POST | `/api/skills/ack-all` | `create_skills_router.skills_ack_all` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L291) |
| workbench | POST | `/api/skills/archive/{name}/restore` | `create_skills_router.skill_restore` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L359) |
| workbench | GET | `/api/skills/inbox` | `create_skills_router.skills_inbox` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L217) |
| workbench | POST | `/api/skills/inbox/upload` | `create_skills_router.skill_package_upload` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L226) |
| workbench | DELETE | `/api/skills/inbox/{package}` | `create_skills_router.skill_package_remove` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L257) |
| workbench | POST | `/api/skills/inbox/{package}/import` | `create_skills_router.skill_package_import` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L248) |
| workbench | GET | `/api/skills/overview` | `create_skills_router.skills_overview` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L140) |
| workbench | GET | `/api/skills/pending` | `create_skills_router.skills_pending` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L286) |
| workbench | GET | `/api/skills/pending/{name}/diff` | `create_skills_router.skill_pending_diff` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L306) |
| workbench | POST | `/api/skills/quarantine/{name}/activate` | `create_skills_router.skill_package_activate` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L265) |
| workbench | POST | `/api/skills/quarantine/{name}/discard` | `create_skills_router.skill_package_discard` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L276) |
| workbench | GET | `/api/skills/settings` | `create_skills_router.skills_settings` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L168) |
| workbench | PUT | `/api/skills/settings` | `create_skills_router.skills_settings_save` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L175) |
| workbench | GET | `/api/skills/{name}` | `create_skills_router.get_skill` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L369) |
| workbench | POST | `/api/skills/{name}/ack` | `create_skills_router.skill_ack` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L330) |
| workbench | POST | `/api/skills/{name}/archive` | `create_skills_router.skill_archive` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L341) |
| workbench | GET | `/api/skills/{name}/diff` | `create_skills_router.skill_diff` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L441) |
| workbench | GET | `/api/skills/{name}/history` | `create_skills_router.skill_history` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L411) |
| workbench | GET | `/api/skills/{name}/revision/{rev}` | `create_skills_router.skill_revision` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L431) |
| workbench | POST | `/api/skills/{name}/rollback` | `create_skills_router.skill_rollback` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L461) |
| workbench | POST | `/api/skills/{name}/save` | `create_skills_router.skill_save` | [agent/src/omni/channels/web_api/skills.py](../../agent/src/omni/channels/web_api/skills.py#L389) |
| workbench | GET | `/api/state` | `create_sessions_router.get_state` | [agent/src/omni/channels/web_api/sessions.py](../../agent/src/omni/channels/web_api/sessions.py#L19) |
| workbench | GET | `/api/storage` | `create_storage_router.overview` | [agent/src/omni/channels/web_api/storage.py](../../agent/src/omni/channels/web_api/storage.py#L57) |
| workbench | PUT | `/api/storage` | `create_storage_router.update` | [agent/src/omni/channels/web_api/storage.py](../../agent/src/omni/channels/web_api/storage.py#L68) |
| workbench | GET | `/api/storage/files` | `create_storage_router.files` | [agent/src/omni/channels/web_api/storage.py](../../agent/src/omni/channels/web_api/storage.py#L90) |
| workbench | POST | `/api/storage/folders` | `create_storage_router.create_folder` | [agent/src/omni/channels/web_api/storage.py](../../agent/src/omni/channels/web_api/storage.py#L131) |
| workbench | POST | `/api/storage/preview` | `create_storage_router.preview` | [agent/src/omni/channels/web_api/storage.py](../../agent/src/omni/channels/web_api/storage.py#L62) |
| workbench | GET | `/api/storage/preview-file` | `create_storage_router.preview_file` | [agent/src/omni/channels/web_api/storage.py](../../agent/src/omni/channels/web_api/storage.py#L115) |
| workbench | POST | `/api/storage/upload` | `create_storage_router.upload` | [agent/src/omni/channels/web_api/storage.py](../../agent/src/omni/channels/web_api/storage.py#L141) |
| workbench | POST | `/api/system/backup` | `create_operations_router.system_backup` | [agent/src/omni/channels/web_api/operations.py](../../agent/src/omni/channels/web_api/operations.py#L27) |
| workbench | GET | `/api/system/bili-cookies` | `create_app.bili_cookies_status` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L354) |
| workbench | POST | `/api/system/bili-cookies` | `create_app.save_bili_cookies` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L366) |
| workbench | GET | `/api/system/config` | `create_app.system_config` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L236) |
| workbench | POST | `/api/system/config` | `create_app.system_config_save` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L319) |
| workbench | GET | `/api/system/cron` | `create_automation_router.cron_list` | [agent/src/omni/channels/web_api/automation.py](../../agent/src/omni/channels/web_api/automation.py#L59) |
| workbench | POST | `/api/system/cron/create` | `create_automation_router.cron_create` | [agent/src/omni/channels/web_api/automation.py](../../agent/src/omni/channels/web_api/automation.py#L22) |
| workbench | POST | `/api/system/cron/delete` | `create_automation_router.cron_delete` | [agent/src/omni/channels/web_api/automation.py](../../agent/src/omni/channels/web_api/automation.py#L159) |
| workbench | GET | `/api/system/cron/history` | `create_automation_router.cron_history` | [agent/src/omni/channels/web_api/automation.py](../../agent/src/omni/channels/web_api/automation.py#L166) |
| workbench | GET | `/api/system/cron/outbox` | `create_automation_router.cron_outbox` | [agent/src/omni/channels/web_api/automation.py](../../agent/src/omni/channels/web_api/automation.py#L174) |
| workbench | POST | `/api/system/cron/outbox/{entry_id}/discard` | `create_automation_router.cron_outbox_discard` | [agent/src/omni/channels/web_api/automation.py](../../agent/src/omni/channels/web_api/automation.py#L201) |
| workbench | POST | `/api/system/cron/outbox/{entry_id}/retry` | `create_automation_router.cron_outbox_retry` | [agent/src/omni/channels/web_api/automation.py](../../agent/src/omni/channels/web_api/automation.py#L189) |
| workbench | POST | `/api/system/cron/toggle` | `create_automation_router.cron_toggle` | [agent/src/omni/channels/web_api/automation.py](../../agent/src/omni/channels/web_api/automation.py#L143) |
| workbench | POST | `/api/system/cron/update` | `create_automation_router.cron_update` | [agent/src/omni/channels/web_api/automation.py](../../agent/src/omni/channels/web_api/automation.py#L93) |
| workbench | GET | `/api/system/doctor` | `create_operations_router.system_doctor` | [agent/src/omni/channels/web_api/operations.py](../../agent/src/omni/channels/web_api/operations.py#L23) |
| workbench | GET | `/api/system/logs/{name}` | `create_operations_router.system_logs` | [agent/src/omni/channels/web_api/operations.py](../../agent/src/omni/channels/web_api/operations.py#L55) |
| workbench | POST | `/api/system/model-key` | `create_app.model_key_set` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L391) |
| workbench | GET | `/api/system/model-keys` | `create_app.model_keys` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L386) |
| workbench | POST | `/api/system/model-test` | `create_app.model_test` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L408) |
| workbench | GET | `/api/system/napcat/qr` | `create_operations_router.system_napcat_qr` | [agent/src/omni/channels/web_api/operations.py](../../agent/src/omni/channels/web_api/operations.py#L51) |
| workbench | GET | `/api/system/overview` | `create_operations_router.system_overview` | [agent/src/omni/channels/web_api/operations.py](../../agent/src/omni/channels/web_api/operations.py#L19) |
| workbench | GET | `/api/system/qq-policy` | `create_app.qq_policy_status` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L335) |
| workbench | POST | `/api/system/qq-policy/stop` | `create_app.qq_policy_stop` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L346) |
| workbench | POST | `/api/system/services/{name}/start` | `create_operations_router.system_service_start` | [agent/src/omni/channels/web_api/operations.py](../../agent/src/omni/channels/web_api/operations.py#L35) |
| workbench | POST | `/api/system/services/{name}/stop` | `create_operations_router.system_service_stop` | [agent/src/omni/channels/web_api/operations.py](../../agent/src/omni/channels/web_api/operations.py#L44) |
| workbench | GET | `/api/system/state` | `create_operations_router.system_state` | [agent/src/omni/channels/web_api/operations.py](../../agent/src/omni/channels/web_api/operations.py#L59) |
| workbench | GET | `/api/terminal/options` | `create_sessions_router.options` | [agent/src/omni/channels/terminal/sessions.py](../../agent/src/omni/channels/terminal/sessions.py#L41) |
| workbench | GET | `/api/terminal/sessions` | `create_sessions_router.sessions` | [agent/src/omni/channels/terminal/sessions.py](../../agent/src/omni/channels/terminal/sessions.py#L45) |
| workbench | POST | `/api/terminal/sessions` | `create_sessions_router.create` | [agent/src/omni/channels/terminal/sessions.py](../../agent/src/omni/channels/terminal/sessions.py#L49) |
| workbench | DELETE | `/api/terminal/sessions/{identifier}` | `create_sessions_router.end` | [agent/src/omni/channels/terminal/sessions.py](../../agent/src/omni/channels/terminal/sessions.py#L63) |
| workbench | PATCH | `/api/terminal/sessions/{identifier}` | `create_sessions_router.rename` | [agent/src/omni/channels/terminal/sessions.py](../../agent/src/omni/channels/terminal/sessions.py#L57) |
| workbench | WEBSOCKET | `/api/terminal/sessions/{identifier}/ws` | `create_sessions_router.connect` | [agent/src/omni/channels/terminal/sessions.py](../../agent/src/omni/channels/terminal/sessions.py#L71) |
| workbench | WEBSOCKET | `/api/terminal/ws` | `create_terminal_router.terminal_ws` | [agent/src/omni/channels/terminal/routes.py](../../agent/src/omni/channels/terminal/routes.py#L15) |
| workbench | POST | `/api/todos/toggle` | `create_sessions_router.toggle_todo` | [agent/src/omni/channels/web_api/sessions.py](../../agent/src/omni/channels/web_api/sessions.py#L98) |
| workbench | GET | `/api/workbench/docs/{name}` | `create_workbench_router.document` | [agent/src/omni/channels/workbench/routes.py](../../agent/src/omni/channels/workbench/routes.py#L39) |
| workbench | GET | `/api/workbench/extensions` | `create_workbench_router.extensions` | [agent/src/omni/channels/workbench/routes.py](../../agent/src/omni/channels/workbench/routes.py#L57) |
| workbench | GET | `/api/workbench/project` | `create_workbench_router.project` | [agent/src/omni/channels/workbench/routes.py](../../agent/src/omni/channels/workbench/routes.py#L35) |
| workbench | GET | `/api/workbench/tools` | `create_workbench_router.tools` | [agent/src/omni/channels/workbench/routes.py](../../agent/src/omni/channels/workbench/routes.py#L50) |
| workbench | GET | `/pair/{code}` | `create_app.pair_link` | [agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L204) |

## 请求模型

### TerminalCreateIn

定位：[agent/src/omni/channels/terminal/sessions.py](../../agent/src/omni/channels/terminal/sessions.py#L15)。

| 字段 | 类型 | 必填 |
|---|---|---|
| shell | `Literal['powershell', 'pwsh', 'cmd']` | 否 |
| cwd | `str \| None` | 否 |
| cols | `int` | 否 |
| rows | `int` | 否 |

### TerminalRenameIn

定位：[agent/src/omni/channels/terminal/sessions.py](../../agent/src/omni/channels/terminal/sessions.py#L22)。

| 字段 | 类型 | 必填 |
|---|---|---|
| label | `str` | 否 |

### ModelKeyIn

定位：[agent/src/omni/channels/web.py](../../agent/src/omni/channels/web.py#L79)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| value | `str \| None` | 否 |

### AndroidActionIn

定位：[agent/src/omni/channels/web_api/android.py](../../agent/src/omni/channels/web_api/android.py#L14)。

| 字段 | 类型 | 必填 |
|---|---|---|
| action | `Literal['tap_element', 'tap_coords', 'tap_scaled', 'swipe', 'key', 'type_text', 'long_press', 'launch_app']` | 是 |
| observation | `str` | 否 |
| idx | `int \| None` | 否 |
| x | `int \| None` | 否 |
| y | `int \| None` | 否 |
| stream_w | `int \| None` | 否 |
| stream_h | `int \| None` | 否 |
| direction | `Literal['up', 'down', 'left', 'right', 'back'] \| None` | 否 |
| keycode | `str \| None` | 否 |
| text | `str \| None` | 否 |
| package | `str \| None` | 否 |
| duration_ms | `int \| None` | 否 |

### ContextUpdate

定位：[agent/src/omni/channels/web_api/context.py](../../agent/src/omni/channels/web_api/context.py#L25)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `str` | 否 |
| updates | `dict[str, dict[str, Any]]` | 是 |

### ChatIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L9)。

| 字段 | 类型 | 必填 |
|---|---|---|
| message | `str` | 是 |
| session_id | `int \| None` | 否 |
| mode | `Literal['auto', 'chat', 'retrieval', 'agent'] \| None` | 否 |

### ResolveIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L17)。

| 字段 | 类型 | 必填 |
|---|---|---|
| allowed | `bool` | 是 |
| reason | `str` | 否 |

### NapCatStartIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L22)。

| 字段 | 类型 | 必填 |
|---|---|---|
| qq | `str` | 否 |

### ConfigUpdateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L26)。

| 字段 | 类型 | 必填 |
|---|---|---|
| updates | `dict[str, dict[str, Any]]` | 是 |

### ModelTestIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L30)。

| 字段 | 类型 | 必填 |
|---|---|---|
| model | `str` | 是 |

### MCPSchemaConfirmIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L34)。

| 字段 | 类型 | 必填 |
|---|---|---|
| fingerprint | `str` | 是 |

### BiliCookiesIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L38)。

| 字段 | 类型 | 必填 |
|---|---|---|
| sessdata | `str` | 否 |
| bili_jct | `str` | 否 |
| buvid3 | `str` | 否 |

### CronNameIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L44)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |

### CronToggleIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L48)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| enabled | `bool` | 是 |

### CronCreateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L53)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| prompt | `str` | 是 |
| every_minutes | `int \| None` | 否 |
| daily_at | `str \| None` | 否 |
| deliver_to_qq | `int \| None` | 否 |
| skills | `list[str]` | 否 |
| context_from | `str \| None` | 否 |
| timezone | `str` | 否 |
| context_max_age_minutes | `int` | 否 |

### CronUpdateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L65)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| prompt | `str \| None` | 否 |
| every_minutes | `int \| None` | 否 |
| daily_at | `str \| None` | 否 |
| deliver_to_qq | `int \| None` | 否 |
| skills | `list[str] \| None` | 否 |
| context_from | `str \| None` | 否 |
| enabled | `bool \| None` | 否 |
| expected_revision | `int \| None` | 否 |
| timezone | `str \| None` | 否 |
| context_max_age_minutes | `int \| None` | 否 |

### TodoToggleIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L81)。

| 字段 | 类型 | 必填 |
|---|---|---|
| session_id | `int` | 是 |
| todo_id | `int` | 是 |
| done | `bool` | 是 |

### AndroidTapIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L87)。

| 字段 | 类型 | 必填 |
|---|---|---|
| x | `int` | 是 |
| y | `int` | 是 |
| stream_w | `int` | 是 |
| stream_h | `int` | 是 |
| observation | `str` | 否 |

### AndroidElementIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L95)。

| 字段 | 类型 | 必填 |
|---|---|---|
| idx | `int` | 是 |
| observation | `str` | 否 |

### AndroidKeyIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L100)。

| 字段 | 类型 | 必填 |
|---|---|---|
| keycode | `str` | 是 |
| observation | `str` | 否 |

### ProfileLineIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L105)。

| 字段 | 类型 | 必填 |
|---|---|---|
| line_id | `int` | 是 |
| content | `str` | 是 |

### ProfileLineCreateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L110)。

| 字段 | 类型 | 必填 |
|---|---|---|
| content | `str` | 否 |

### FactCreateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L116)。

| 字段 | 类型 | 必填 |
|---|---|---|
| content | `str` | 否 |

### ResidentUpdateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L122)。

| 字段 | 类型 | 必填 |
|---|---|---|
| operation | `str` | 是 |
| id | `int \| None` | 否 |
| content | `str` | 否 |

### PairIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L128)。

| 字段 | 类型 | 必填 |
|---|---|---|
| code | `str` | 是 |

### SkillSaveIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L132)。

| 字段 | 类型 | 必填 |
|---|---|---|
| content | `str` | 是 |
| expected_hash | `str \| None` | 否 |

### SkillCreateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L139)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| description | `str` | 是 |
| content | `str` | 是 |

### SkillArchiveIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L147)。

| 字段 | 类型 | 必填 |
|---|---|---|
| expected_hash | `str \| None` | 否 |
| reason | `str` | 否 |

### SkillSettingsIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L154)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `str` | 否 |
| updates | `dict[str, dict[str, Any]]` | 是 |

### SkillRollbackIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L161)。

| 字段 | 类型 | 必填 |
|---|---|---|
| rev | `int` | 是 |
| expected_hash | `str \| None` | 否 |

### SkillAckIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L166)。

| 字段 | 类型 | 必填 |
|---|---|---|
| rev | `int` | 是 |

### PersonCreateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L175)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| importance | `str` | 否 |
| note | `str` | 否 |

### PersonUpdateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L181)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str \| None` | 否 |
| importance | `str \| None` | 否 |
| note | `str \| None` | 否 |

### AccountLinkIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L187)。

| 字段 | 类型 | 必填 |
|---|---|---|
| channel | `str` | 是 |
| value | `str` | 是 |
| label | `str` | 否 |

### PersonEntryIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../agent/src/omni/channels/web_api/schemas.py#L193)。

| 字段 | 类型 | 必填 |
|---|---|---|
| entry_kind | `str` | 是 |
| content | `str` | 是 |
| expires_at | `str \| None` | 否 |

### DirectoryUpdate

定位：[agent/src/omni/channels/web_api/storage.py](../../agent/src/omni/channels/web_api/storage.py#L27)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `str` | 否 |
| catalog | `dict` | 是 |
| acknowledge | `bool` | 否 |

### FolderCreate

定位：[agent/src/omni/channels/web_api/storage.py](../../agent/src/omni/channels/web_api/storage.py#L34)。

| 字段 | 类型 | 必填 |
|---|---|---|
| scope | `str` | 是 |
| path | `str` | 否 |

### Persona

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L141)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 否 |
| system_prompt | `str` | 否 |

### Models

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L148)。

| 字段 | 类型 | 必填 |
|---|---|---|
| chat | `str` | 否 |
| vision | `str` | 否 |
| local | `str` | 否 |
| review | `str` | 否 |
| router | `str` | 否 |
| chat_mode | `str` | 否 |
| retrieval_mode | `str` | 否 |
| agent_mode | `str` | 否 |

### ModeRouting

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L161)。

| 字段 | 类型 | 必填 |
|---|---|---|
| default_mode | `str` | 否 |
| allow_escalation | `bool` | 否 |

### ModelCapability

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L182)。

| 字段 | 类型 | 必填 |
|---|---|---|
| tools | `bool` | 否 |
| vision | `bool` | 否 |
| stream | `bool` | 否 |
| context_tokens | `int` | 否 |
| input_usd_per_m | `float \| None` | 否 |
| output_usd_per_m | `float \| None` | 否 |

### Retry

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L200)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_attempts | `int` | 否 |
| total_deadline_s | `float` | 否 |
| backoff_s | `float` | 否 |

### LLMBudget

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L208)。

| 字段 | 类型 | 必填 |
|---|---|---|
| daily_usd | `float` | 否 |
| per_run_usd | `float` | 否 |

### Vision

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L220)。

| 字段 | 类型 | 必填 |
|---|---|---|
| send_to_cloud | `bool` | 否 |
| max_image_bytes | `int` | 否 |
| max_images_per_turn | `int` | 否 |
| max_edge_px | `int` | 否 |

### Artifacts

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L234)。

| 字段 | 类型 | 必填 |
|---|---|---|
| retention_days | `int` | 否 |
| max_total_mb | `int` | 否 |

### LLM

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L242)。

| 字段 | 类型 | 必填 |
|---|---|---|
| request_timeout_s | `float` | 否 |
| max_output_tokens | `int` | 否 |
| retry | `Retry` | 否 |
| budget | `LLMBudget` | 否 |
| capabilities | `dict[str, ModelCapability]` | 否 |

### Limits

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L255)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_history_messages | `int` | 否 |
| max_agent_steps | `int` | 否 |
| resident_memory_chars | `int` | 否 |
| session_queue_depth | `int` | 否 |
| session_lease_ttl_s | `int` | 否 |

### Review

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L263)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| max_messages | `int` | 否 |
| steps | `int` | 否 |
| queue_depth | `int` | 否 |
| min_messages | `int` | 否 |

### Memory

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L273)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_writes_per_day | `int` | 否 |
| max_fact_chars | `int` | 否 |
| recall_prefix | `bool` | 否 |

### Skills

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L285)。

| 字段 | 类型 | 必填 |
|---|---|---|
| history_keep | `int` | 否 |
| max_write_chars | `int` | 否 |

### Web

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L298)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_bytes | `int` | 否 |
| max_chars | `int` | 否 |
| max_links | `int` | 否 |
| timeout_s | `float` | 否 |
| total_deadline_s | `float` | 否 |
| max_redirects | `int` | 否 |
| allow_private_hosts | `list[str]` | 否 |
| user_agent | `str` | 否 |

### Files

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L315)。

| 字段 | 类型 | 必填 |
|---|---|---|
| root | `str` | 否 |
| extra_roots | `list[str]` | 否 |
| deny_globs | `list[str]` | 否 |
| write_deny_globs | `list[str]` | 否 |
| max_read_lines | `int` | 否 |
| max_list_entries | `int` | 否 |
| max_search_files | `int` | 否 |
| max_search_results | `int` | 否 |
| max_search_seconds | `float` | 否 |

### Compression

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L333)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| trigger_chars | `int` | 否 |
| keep_recent | `int` | 否 |
| min_messages | `int` | 否 |

### Asr

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L342)。

| 字段 | 类型 | 必填 |
|---|---|---|
| model | `str` | 否 |
| device | `str` | 否 |
| compute_type | `str` | 否 |

### QQ

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L350)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| ws_url | `str` | 否 |
| access_token | `str` | 否 |
| allowed_users | `list[int]` | 否 |
| allowed_groups | `list[int]` | 否 |
| reply_delay_s | `list[float]` | 否 |
| group_reply_to_bot | `bool` | 否 |
| merge_window_s | `float` | 否 |
| max_pending | `int` | 否 |
| max_concurrent | `int` | 否 |
| proactive_enabled | `bool` | 否 |
| owner_qq | `int` | 否 |
| proactive_daily_limit | `int` | 否 |
| quiet_start | `str` | 否 |
| quiet_end | `str` | 否 |
| timezone | `str` | 否 |

### Browser

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L387)。

| 字段 | 类型 | 必填 |
|---|---|---|
| headless | `bool` | 否 |
| slow_mo_ms | `int` | 否 |
| max_writes_per_day | `int` | 否 |
| approval_timeout_s | `float` | 否 |

### UIFormSelectors

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L396)。

| 字段 | 类型 | 必填 |
|---|---|---|
| account | `str` | 是 |
| account_attr | `str` | 否 |
| target | `str` | 是 |
| target_attr | `str` | 否 |
| editor | `str` | 是 |
| submit | `str` | 是 |
| records | `str` | 是 |
| record_id_attr | `str` | 否 |
| record_text | `str` | 是 |
| record_author | `str` | 是 |
| record_author_attr | `str` | 否 |
| challenge | `str` | 是 |
| logged_out | `str` | 是 |

### BilibiliWrite

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L414)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| forms | `dict[str, UIFormSelectors]` | 否 |
| draft_ttl_s | `int` | 否 |
| max_content_chars | `int` | 否 |

### System

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L430)。

| 字段 | 类型 | 必填 |
|---|---|---|
| napcat_dir | `str` | 否 |

### Approval

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L436)。

| 字段 | 类型 | 必填 |
|---|---|---|
| mode | `str` | 否 |

### MCPServerConfig

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L446)。

| 字段 | 类型 | 必填 |
|---|---|---|
| command | `str` | 否 |
| args | `list[str]` | 否 |
| env | `dict[str, str]` | 否 |
| cwd | `str \| None` | 否 |
| enabled | `bool` | 否 |
| timeout_s | `float` | 否 |
| allowed_tools | `list[str] \| None` | 否 |
| max_waiting | `int` | 否 |
| log_bytes | `int` | 否 |
| log_segments | `int` | 否 |
| read_only_tools | `list[str]` | 否 |

### MCPConfig

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L476)。

| 字段 | 类型 | 必填 |
|---|---|---|
| servers | `dict[str, MCPServerConfig]` | 否 |

### Desktop

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L490)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| allowed_apps | `list[str]` | 否 |
| max_nodes | `int` | 否 |
| timeout_s | `float` | 否 |
| observation_ttl_s | `float` | 否 |
| max_writes_per_day | `int` | 否 |

### Embedding

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L507)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| provider | `str` | 否 |
| model | `str` | 否 |
| revision | `str` | 否 |
| dimensions | `int` | 否 |
| endpoint | `str` | 否 |
| api_key_env | `str` | 否 |
| send_to_cloud | `bool` | 否 |
| daily_usd | `float` | 否 |
| input_usd_per_m | `float` | 否 |
| timeout_s | `float` | 否 |
| batch_size | `int` | 否 |
| max_text_chars | `int` | 否 |
| scopes | `list[str]` | 否 |

### Cron

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L545)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_running | `int` | 否 |

### Journal

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L550)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| daily_time | `str` | 否 |
| steps | `int` | 否 |
| max_catchup | `int` | 否 |
| max_input_chars | `int` | 否 |
| max_note_chars | `int` | 否 |
| daily_prompt | `str` | 否 |
| weekly_prompt | `str` | 否 |
| monthly_prompt | `str` | 否 |

### Transcript

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L579)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_entry_chars | `int` | 否 |
| max_tool_args_chars | `int` | 否 |
| max_tool_result_chars | `int` | 否 |
| retention_days | `int` | 否 |
| dialogue_retention_days | `int` | 否 |

### People

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L594)。

| 字段 | 类型 | 必填 |
|---|---|---|
| person_card_chars | `int` | 否 |
| archive_max_chars | `int` | 否 |
| resident_max_chars | `int` | 否 |
| event_max_chars | `int` | 否 |

### AndroidConfig

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L604)。

| 字段 | 类型 | 必填 |
|---|---|---|
| allowed_apps | `list[str]` | 否 |
| observation_ttl_s | `float` | 否 |
| operation_timeout_s | `float` | 否 |
| max_devices | `int` | 否 |
| preview_fps | `float` | 否 |
| preview_clients | `int` | 否 |
| preview_idle_s | `float` | 否 |

### CommandJobsConfig

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L623)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_running | `int` | 否 |
| max_waiting | `int` | 否 |
| default_timeout_s | `float` | 否 |
| log_bytes | `int` | 否 |
| retention_days | `int` | 否 |
| total_log_bytes | `int` | 否 |

### TerminalConfig

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L633)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_sessions | `int` | 否 |
| roots | `list[str]` | 否 |
| buffer_bytes | `int` | 否 |
| detach_ttl_s | `float` | 否 |

### AppConfig

定位：[agent/src/omni/config.py](../../agent/src/omni/config.py#L641)。

| 字段 | 类型 | 必填 |
|---|---|---|
| android | `AndroidConfig` | 否 |
| command_jobs | `CommandJobsConfig` | 否 |
| terminal | `TerminalConfig` | 否 |
| persona | `Persona` | 否 |
| models | `Models` | 否 |
| llm | `LLM` | 否 |
| limits | `Limits` | 否 |
| review | `Review` | 否 |
| files | `Files` | 否 |
| web | `Web` | 否 |
| vision | `Vision` | 否 |
| artifacts | `Artifacts` | 否 |
| memory | `Memory` | 否 |
| skills | `Skills` | 否 |
| compression | `Compression` | 否 |
| asr | `Asr` | 否 |
| qq | `QQ` | 否 |
| browser | `Browser` | 否 |
| bilibili_write | `BilibiliWrite` | 否 |
| system | `System` | 否 |
| approval | `Approval` | 否 |
| mcp | `MCPConfig` | 否 |
| cron | `Cron` | 否 |
| journal | `Journal` | 否 |
| transcript | `Transcript` | 否 |
| people | `People` | 否 |
| embedding | `Embedding` | 否 |
| desktop | `Desktop` | 否 |
| mode_routing | `ModeRouting` | 否 |

### Catalog

定位：[agent/src/omni/system/agent_spaces/catalog.py](../../agent/src/omni/system/agent_spaces/catalog.py#L19)。

| 字段 | 类型 | 必填 |
|---|---|---|
| version | `int` | 否 |
| active | `str` | 否 |
| application | `ApplicationDirectories` | 否 |
| agents | `list[AgentProfile]` | 否 |

### DirectoryOverrides

定位：[agent/src/omni/system/agent_spaces/layout.py](../../agent/src/omni/system/agent_spaces/layout.py#L17)。

| 字段 | 类型 | 必填 |
|---|---|---|
| workspace | `str` | 否 |
| files | `str` | 否 |
| documents | `str` | 否 |
| memory | `str` | 否 |
| skills | `str` | 否 |
| artifacts | `str` | 否 |
| cache | `str` | 否 |
| logs | `str` | 否 |
| backups | `str` | 否 |

### AgentProfile

定位：[agent/src/omni/system/agent_spaces/layout.py](../../agent/src/omni/system/agent_spaces/layout.py#L30)。

| 字段 | 类型 | 必填 |
|---|---|---|
| id | `str` | 否 |
| name | `str` | 否 |
| home | `str` | 否 |
| directories | `DirectoryOverrides` | 否 |

### ApplicationDirectories

定位：[agent/src/omni/system/agent_spaces/layout.py](../../agent/src/omni/system/agent_spaces/layout.py#L38)。

| 字段 | 类型 | 必填 |
|---|---|---|
| data_root | `str` | 否 |
| workspace | `str` | 否 |
| agents_root | `str` | 否 |

## 静态工具声明

不是启用清单；条件注册和插件工具以 `/api/workbench/tools` 的本进程快照为准。SAFE 不等于 read_only。

| 工具 | 审批等级 | 强制人工 | 并发只读 | 模块 | 定位 |
|---|---|---|---|---|---|
| `android_key` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py#L182) |
| `android_launch_app` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py#L232) |
| `android_list_apps` | safe | False | True | android | [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py#L230) |
| `android_long_press` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py#L235) |
| `android_screen_tree` | safe | False | False | android | [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py#L97) |
| `android_screenshot` | safe | False | False | android | [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py#L112) |
| `android_status` | safe | False | False | android | [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py#L87) |
| `android_swipe` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py#L165) |
| `android_tap_coords` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py#L147) |
| `android_tap_element` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py#L122) |
| `android_type` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../agent/src/omni/tools/android/tools.py#L199) |
| `bilibili_video_comments` | safe | False | True | bilibili | [agent/src/omni/tools/bilibili/tools.py](../../agent/src/omni/tools/bilibili/tools.py#L118) |
| `bilibili_video_info` | safe | False | True | bilibili | [agent/src/omni/tools/bilibili/tools.py](../../agent/src/omni/tools/bilibili/tools.py#L98) |
| `bilibili_video_subtitle` | safe | False | True | bilibili | [agent/src/omni/tools/bilibili/tools.py](../../agent/src/omni/tools/bilibili/tools.py#L136) |
| `bilibili_video_transcript` | safe | False | True | bilibili | [agent/src/omni/tools/bilibili/tools.py](../../agent/src/omni/tools/bilibili/tools.py#L153) |
| `browser_click` | confirm | True | False | browser | [agent/src/omni/tools/browser/tools.py](../../agent/src/omni/tools/browser/tools.py#L165) |
| `browser_navigate` | safe | False | False | browser | [agent/src/omni/tools/browser/tools.py](../../agent/src/omni/tools/browser/tools.py#L131) |
| `browser_read` | safe | False | False | browser | [agent/src/omni/tools/browser/tools.py](../../agent/src/omni/tools/browser/tools.py#L145) |
| `browser_screenshot` | safe | False | False | browser | [agent/src/omni/tools/browser/tools.py](../../agent/src/omni/tools/browser/tools.py#L155) |
| `browser_type` | confirm | True | False | browser | [agent/src/omni/tools/browser/tools.py](../../agent/src/omni/tools/browser/tools.py#L183) |
| `community_config` | safe | False | False | community | [agent/src/omni/plugins/community/tools.py](../../agent/src/omni/plugins/community/tools.py#L55) |
| `community_info` | safe | False | True | community | [agent/src/omni/plugins/community/tools.py](../../agent/src/omni/plugins/community/tools.py#L89) |
| `community_install` | safe | False | False | community | [agent/src/omni/plugins/community/tools.py](../../agent/src/omni/plugins/community/tools.py#L104) |
| `community_publish` | confirm | True | False | community | [agent/src/omni/plugins/community/tools.py](../../agent/src/omni/plugins/community/tools.py#L124) |
| `community_search` | safe | False | True | community | [agent/src/omni/plugins/community/tools.py](../../agent/src/omni/plugins/community/tools.py#L71) |
| `cron_add` | safe | False | False | scheduling | [agent/src/omni/tools/scheduling/tools.py](../../agent/src/omni/tools/scheduling/tools.py#L199) |
| `cron_edit` | safe | False | False | scheduling | [agent/src/omni/tools/scheduling/tools.py](../../agent/src/omni/tools/scheduling/tools.py#L278) |
| `cron_list` | safe | False | True | scheduling | [agent/src/omni/tools/scheduling/tools.py](../../agent/src/omni/tools/scheduling/tools.py#L188) |
| `cron_once` | safe | False | False | scheduling | [agent/src/omni/tools/scheduling/tools.py](../../agent/src/omni/tools/scheduling/tools.py#L237) |
| `cron_remove` | safe | False | False | scheduling | [agent/src/omni/tools/scheduling/tools.py](../../agent/src/omni/tools/scheduling/tools.py#L264) |
| `daily_digest` | safe | False | True | personal | [agent/src/omni/tools/personal/digest.py](../../agent/src/omni/tools/personal/digest.py#L54) |
| `delegate_task` | safe | False | False | delegation | [agent/src/omni/tools/delegation/tools.py](../../agent/src/omni/tools/delegation/tools.py#L106) |
| `dialogue_read` | safe | False | True | personal | [agent/src/omni/tools/personal/transcript.py](../../agent/src/omni/tools/personal/transcript.py#L158) |
| `dialogue_search` | safe | False | True | personal | [agent/src/omni/tools/personal/transcript.py](../../agent/src/omni/tools/personal/transcript.py#L137) |
| `files_diff` | safe | False | True | files | [agent/src/omni/tools/files/tools.py](../../agent/src/omni/tools/files/tools.py#L487) |
| `files_history` | safe | False | True | files | [agent/src/omni/tools/files/tools.py](../../agent/src/omni/tools/files/tools.py#L559) |
| `files_list` | safe | False | True | files | [agent/src/omni/tools/files/tools.py](../../agent/src/omni/tools/files/tools.py#L429) |
| `files_read` | safe | False | True | files | [agent/src/omni/tools/files/tools.py](../../agent/src/omni/tools/files/tools.py#L468) |
| `files_restore` | confirm | True | False | files | [agent/src/omni/tools/files/tools.py](../../agent/src/omni/tools/files/tools.py#L574) |
| `files_search` | safe | False | True | files | [agent/src/omni/tools/files/tools.py](../../agent/src/omni/tools/files/tools.py#L446) |
| `files_write` | confirm | True | False | files | [agent/src/omni/tools/files/tools.py](../../agent/src/omni/tools/files/tools.py#L514) |
| `forget` | confirm | False | False | personal | [agent/src/omni/tools/personal/facts.py](../../agent/src/omni/tools/personal/facts.py#L136) |
| `get_time` | safe | False | True | personal | [agent/src/omni/tools/personal/clock.py](../../agent/src/omni/tools/personal/clock.py#L14) |
| `journal_view` | safe | False | True | personal | [agent/src/omni/tools/personal/journal.py](../../agent/src/omni/tools/personal/journal.py#L42) |
| `mcp_call_tool` | confirm | True | False | mcp | [agent/src/omni/tools/mcp/tools.py](../../agent/src/omni/tools/mcp/tools.py#L73) |
| `mcp_list_tools` | safe | False | True | mcp | [agent/src/omni/tools/mcp/tools.py](../../agent/src/omni/tools/mcp/tools.py#L41) |
| `mcp_read_tool` | safe | False | True | mcp | [agent/src/omni/tools/mcp/tools.py](../../agent/src/omni/tools/mcp/tools.py#L69) |
| `peek_command` | safe | False | True | shell | [agent/src/omni/tools/shell/tools.py](../../agent/src/omni/tools/shell/tools.py#L214) |
| `person_event` | safe | False | False | personal | [agent/src/omni/tools/personal/people.py](../../agent/src/omni/tools/personal/people.py#L192) |
| `person_lookup` | safe | False | True | personal | [agent/src/omni/tools/personal/people.py](../../agent/src/omni/tools/personal/people.py#L147) |
| `person_remember` | safe | False | False | personal | [agent/src/omni/tools/personal/people.py](../../agent/src/omni/tools/personal/people.py#L169) |
| `plugin_activate` | confirm | True | False | plugins | [agent/src/omni/plugins/tools.py](../../agent/src/omni/plugins/tools.py#L116) |
| `plugin_disable` | safe | False | False | plugins | [agent/src/omni/plugins/tools.py](../../agent/src/omni/plugins/tools.py#L151) |
| `plugin_enable` | safe | False | False | plugins | [agent/src/omni/plugins/tools.py](../../agent/src/omni/plugins/tools.py#L137) |
| `plugin_install` | safe | False | False | plugins | [agent/src/omni/plugins/tools.py](../../agent/src/omni/plugins/tools.py#L95) |
| `plugin_list` | safe | False | True | plugins | [agent/src/omni/plugins/tools.py](../../agent/src/omni/plugins/tools.py#L80) |
| `plugin_remove` | confirm | True | False | plugins | [agent/src/omni/plugins/tools.py](../../agent/src/omni/plugins/tools.py#L168) |
| `profile_manage` | safe | False | False | personal | [agent/src/omni/tools/personal/profile.py](../../agent/src/omni/tools/personal/profile.py#L61) |
| `qq_send_message` | safe | False | False | qq-tools | [agent/src/omni/tools/qq/tools.py](../../agent/src/omni/tools/qq/tools.py#L75) |
| `recall` | safe | False | True | personal | [agent/src/omni/tools/personal/facts.py](../../agent/src/omni/tools/personal/facts.py#L116) |
| `remember` | safe | False | False | personal | [agent/src/omni/tools/personal/facts.py](../../agent/src/omni/tools/personal/facts.py#L97) |
| `resident_memory` | safe | False | False | personal | [agent/src/omni/tools/personal/resident.py](../../agent/src/omni/tools/personal/resident.py#L39) |
| `run_command` | confirm | True | False | shell | [agent/src/omni/tools/shell/tools.py](../../agent/src/omni/tools/shell/tools.py#L243) |
| `search_history` | safe | False | True | personal | [agent/src/omni/tools/personal/facts.py](../../agent/src/omni/tools/personal/facts.py#L169) |
| `session_log_read` | safe | False | True | personal | [agent/src/omni/tools/personal/transcript.py](../../agent/src/omni/tools/personal/transcript.py#L202) |
| `session_log_search` | safe | False | True | personal | [agent/src/omni/tools/personal/transcript.py](../../agent/src/omni/tools/personal/transcript.py#L181) |
| `session_refine` | safe | False | False | personal | [agent/src/omni/tools/personal/refine.py](../../agent/src/omni/tools/personal/refine.py#L45) |
| `shell_job_cancel` | confirm | True | False | command-jobs | [agent/src/omni/tools/shell/job_tools.py](../../agent/src/omni/tools/shell/job_tools.py#L50) |
| `shell_job_list` | safe | False | True | command-jobs | [agent/src/omni/tools/shell/job_tools.py](../../agent/src/omni/tools/shell/job_tools.py#L46) |
| `shell_job_read` | safe | False | True | command-jobs | [agent/src/omni/tools/shell/job_tools.py](../../agent/src/omni/tools/shell/job_tools.py#L48) |
| `shell_job_start` | confirm | True | False | command-jobs | [agent/src/omni/tools/shell/job_tools.py](../../agent/src/omni/tools/shell/job_tools.py#L44) |
| `skill_activate` | confirm | True | False | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L432) |
| `skill_archive` | confirm | True | False | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L312) |
| `skill_create` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L195) |
| `skill_discard` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L452) |
| `skill_history` | safe | False | True | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L268) |
| `skill_import` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L397) |
| `skill_patch` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L218) |
| `skill_pending` | safe | False | True | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L421) |
| `skill_restore` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L334) |
| `skill_rewrite` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L245) |
| `skill_rollback` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L289) |
| `skill_view` | safe | False | True | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L177) |
| `skills_list` | safe | False | True | skills | [agent/src/omni/tools/skills/tools.py](../../agent/src/omni/tools/skills/tools.py#L166) |
| `system_service` | confirm | False | False | system-tools | [agent/src/omni/tools/system/tools.py](../../agent/src/omni/tools/system/tools.py#L94) |
| `system_status` | safe | False | True | system-tools | [agent/src/omni/tools/system/tools.py](../../agent/src/omni/tools/system/tools.py#L83) |
| `todo` | safe | False | False | personal | [agent/src/omni/tools/personal/todos.py](../../agent/src/omni/tools/personal/todos.py#L60) |
| `web_read` | safe | False | True | web-fetch | [agent/src/omni/tools/web/tools.py](../../agent/src/omni/tools/web/tools.py#L113) |
