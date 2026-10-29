# API、对话调用与工具声明

> 由 `uv run --no-sync python scripts/project_index.py` 生成。不要手改；分类改 system/catalog/schema.py，分层改 ARCHITECTURE.md。

工作台 API 统一受配对/Host/Origin 规则保护；终端 WS 单独握手校验。社区服务器是独立服务。请求字段为源码声明，完整 schema 可查询 FastAPI OpenAPI。

## 对话与模型

Web/QQ/CLI 的请求经 Runtime 进入同一内核，模型请求经 Gateway 记账。

`create_conversation_router.chat`（[src/omni/channels/web_api/conversation.py](../../src/omni/channels/web_api/conversation.py#L43)） → `Agent.run_turn`（[src/omni/core/agent.py](../../src/omni/core/agent.py#L232)） → `Agent._chat`（[src/omni/core/agent.py](../../src/omni/core/agent.py#L408)） → `Gateway.complete`（[src/omni/llm/gateway.py](../../src/omni/llm/gateway.py#L339)）

## 模型调用工具

模型工具调用经统一注册表和审批；结果按原调用顺序回填。

`Agent._execute_tool_calls`（[src/omni/core/agent.py](../../src/omni/core/agent.py#L503)） → `ToolRegistry.execute`（[src/omni/tools/registry.py](../../src/omni/tools/registry.py#L142)） → `WebApprover`（[src/omni/safety/gates.py](../../src/omni/safety/gates.py#L123)） → `ToolResult`（[src/omni/tools/result.py](../../src/omni/tools/result.py#L35)）

## 流式与取消

流式响应和持久运行事件是两条接口；停止通过协作取消传播。

`create_conversation_router.chat_stream`（[src/omni/channels/web_api/conversation.py](../../src/omni/channels/web_api/conversation.py#L74)） → `Agent.run_turn`（[src/omni/core/agent.py](../../src/omni/core/agent.py#L232)） → `Gateway.stream`（[src/omni/llm/gateway.py](../../src/omni/llm/gateway.py#L425)） → `RunStore`（[src/omni/memory/runs.py](../../src/omni/memory/runs.py#L87)）

## 交互式终端

WebSocket 先校验身份，再由会话池创建独立 ConPTY；断开时核查退出。

`create_terminal_router.terminal_ws`（[src/omni/channels/terminal/routes.py](../../src/omni/channels/terminal/routes.py#L15)） → `WebAuth.check_websocket`（[src/omni/channels/web_auth.py](../../src/omni/channels/web_auth.py#L327)） → `TerminalManager.open`（[src/omni/channels/terminal/pool.py](../../src/omni/channels/terminal/pool.py#L35)） → `TerminalSession`（[src/omni/channels/terminal/session.py](../../src/omni/channels/terminal/session.py#L8)） → `open_process`（[src/omni/channels/terminal/backend.py](../../src/omni/channels/terminal/backend.py#L26)）

## 插件扩展接入

下载/收包、隔离校验、人工激活、命名空间注册，最终仍由工具审批链执行。

`CommunityClient.install`（[src/omni/plugins/community/client.py](../../src/omni/plugins/community/client.py#L159)） → `PluginHost.install_package`（[src/omni/plugins/host.py](../../src/omni/plugins/host.py#L213)） → `PluginHost.activate`（[src/omni/plugins/host.py](../../src/omni/plugins/host.py#L506)） → `PluginContext.add_tool`（[src/omni/plugins/host.py](../../src/omni/plugins/host.py#L129)） → `ToolRegistry.execute`（[src/omni/tools/registry.py](../../src/omni/tools/registry.py#L142)）

## 技能数据接入

技能是提示与参考数据；导入不执行脚本，激活需人工确认。

`register_skill_inbox_tools`（[src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L287)） → `SkillInbox`（[src/omni/memory/skill_inbox.py](../../src/omni/memory/skill_inbox.py#L88)） → `SkillStore`（[src/omni/memory/skills.py](../../src/omni/memory/skills.py#L81)）

## 外部 MCP 能力

渐进发现与授权校验后调用外部进程；结果未知不重放写操作。

`register_mcp_tools`（[src/omni/tools/mcp/tools.py](../../src/omni/tools/mcp/tools.py#L18)） → `MCPManager`（[src/omni/tools/mcp/client.py](../../src/omni/tools/mcp/client.py#L252)） → `MCPSchemaStore`（[src/omni/memory/state.py](../../src/omni/memory/state.py#L1511)）

## 声明路由

| 服务 | 方法 | 路径 | 处理函数 | 定位 |
|---|---|---|---|---|
| community | GET | `/api/download/{plugin_id}` | `create_app.download` | [src/omni/plugins/community/server.py](../../src/omni/plugins/community/server.py#L146) |
| community | GET | `/api/health` | `create_app.health` | [src/omni/plugins/community/server.py](../../src/omni/plugins/community/server.py#L112) |
| community | GET | `/api/plugins` | `create_app.search_plugins` | [src/omni/plugins/community/server.py](../../src/omni/plugins/community/server.py#L116) |
| community | GET | `/api/plugins/{plugin_id}` | `create_app.plugin_detail` | [src/omni/plugins/community/server.py](../../src/omni/plugins/community/server.py#L135) |
| community | POST | `/api/publish` | `create_app.publish` | [src/omni/plugins/community/server.py](../../src/omni/plugins/community/server.py#L174) |
| workbench | GET | `/api/android/devices` | `create_android_router.devices` | [src/omni/channels/web_api/android.py](../../src/omni/channels/web_api/android.py#L40) |
| workbench | POST | `/api/android/devices/{serial}/actions` | `create_android_router.action` | [src/omni/channels/web_api/android.py](../../src/omni/channels/web_api/android.py#L63) |
| workbench | GET | `/api/android/devices/{serial}/apps` | `create_android_router.apps` | [src/omni/channels/web_api/android.py](../../src/omni/channels/web_api/android.py#L58) |
| workbench | GET | `/api/android/devices/{serial}/observe` | `create_android_router.observe` | [src/omni/channels/web_api/android.py](../../src/omni/channels/web_api/android.py#L45) |
| workbench | GET | `/api/android/devices/{serial}/snapshot` | `create_android_router.snapshot` | [src/omni/channels/web_api/android.py](../../src/omni/channels/web_api/android.py#L50) |
| workbench | GET | `/api/android/devices/{serial}/stream` | `create_android_router.stream` | [src/omni/channels/web_api/android.py](../../src/omni/channels/web_api/android.py#L69) |
| workbench | GET | `/api/android/devices/{serial}/stream-state` | `create_android_router.stream_state` | [src/omni/channels/web_api/android.py](../../src/omni/channels/web_api/android.py#L74) |
| workbench | POST | `/api/android/element` | `create_devices_router.android_element` | [src/omni/channels/web_api/devices.py](../../src/omni/channels/web_api/devices.py#L96) |
| workbench | POST | `/api/android/key` | `create_devices_router.android_key` | [src/omni/channels/web_api/devices.py](../../src/omni/channels/web_api/devices.py#L106) |
| workbench | GET | `/api/android/snapshot` | `create_devices_router.android_snapshot` | [src/omni/channels/web_api/devices.py](../../src/omni/channels/web_api/devices.py#L65) |
| workbench | GET | `/api/android/state` | `create_devices_router.android_state` | [src/omni/channels/web_api/devices.py](../../src/omni/channels/web_api/devices.py#L37) |
| workbench | GET | `/api/android/stream` | `create_devices_router.android_stream` | [src/omni/channels/web_api/devices.py](../../src/omni/channels/web_api/devices.py#L115) |
| workbench | POST | `/api/android/tap` | `create_devices_router.android_tap` | [src/omni/channels/web_api/devices.py](../../src/omni/channels/web_api/devices.py#L82) |
| workbench | GET | `/api/android/tree` | `create_devices_router.android_tree` | [src/omni/channels/web_api/devices.py](../../src/omni/channels/web_api/devices.py#L46) |
| workbench | GET | `/api/approvals` | `create_approvals_router.get_approvals` | [src/omni/channels/web_api/approvals.py](../../src/omni/channels/web_api/approvals.py#L18) |
| workbench | POST | `/api/approvals/{approval_id}/resolve` | `create_approvals_router.resolve_approval` | [src/omni/channels/web_api/approvals.py](../../src/omni/channels/web_api/approvals.py#L26) |
| workbench | GET | `/api/artifacts` | `create_artifacts_router.list_artifacts` | [src/omni/channels/web_api/artifacts.py](../../src/omni/channels/web_api/artifacts.py#L31) |
| workbench | DELETE | `/api/artifacts/{artifact_id}` | `create_artifacts_router.delete_artifact` | [src/omni/channels/web_api/artifacts.py](../../src/omni/channels/web_api/artifacts.py#L73) |
| workbench | GET | `/api/artifacts/{artifact_id}/image` | `create_artifacts_router.artifact_image` | [src/omni/channels/web_api/artifacts.py](../../src/omni/channels/web_api/artifacts.py#L60) |
| workbench | POST | `/api/auth/logout` | `create_app.auth_logout` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L196) |
| workbench | POST | `/api/auth/pair` | `create_app.auth_pair` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L181) |
| workbench | GET | `/api/auth/status` | `create_app.auth_status` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L173) |
| workbench | GET | `/api/capabilities` | `create_capabilities_router.capabilities` | [src/omni/channels/web_api/capabilities.py](../../src/omni/channels/web_api/capabilities.py#L12) |
| workbench | POST | `/api/chat` | `create_conversation_router.chat` | [src/omni/channels/web_api/conversation.py](../../src/omni/channels/web_api/conversation.py#L43) |
| workbench | POST | `/api/chat/stream` | `create_conversation_router.chat_stream` | [src/omni/channels/web_api/conversation.py](../../src/omni/channels/web_api/conversation.py#L74) |
| workbench | GET | `/api/command-jobs` | `create_command_jobs_router.list_jobs` | [src/omni/channels/web_api/command_jobs.py](../../src/omni/channels/web_api/command_jobs.py#L9) |
| workbench | GET | `/api/command-jobs/{identifier}` | `create_command_jobs_router.read_job` | [src/omni/channels/web_api/command_jobs.py](../../src/omni/channels/web_api/command_jobs.py#L13) |
| workbench | POST | `/api/command-jobs/{identifier}/cancel` | `create_command_jobs_router.cancel_job` | [src/omni/channels/web_api/command_jobs.py](../../src/omni/channels/web_api/command_jobs.py#L19) |
| workbench | GET | `/api/desktop/observe/{window}` | `create_devices_router.desktop_observe` | [src/omni/channels/web_api/devices.py](../../src/omni/channels/web_api/devices.py#L28) |
| workbench | GET | `/api/desktop/windows` | `create_devices_router.desktop_windows` | [src/omni/channels/web_api/devices.py](../../src/omni/channels/web_api/devices.py#L19) |
| workbench | GET | `/api/facts` | `create_memory_router.get_facts` | [src/omni/channels/web_api/memory.py](../../src/omni/channels/web_api/memory.py#L19) |
| workbench | GET | `/api/facts/detail` | `create_memory_router.get_facts_detail` | [src/omni/channels/web_api/memory.py](../../src/omni/channels/web_api/memory.py#L23) |
| workbench | DELETE | `/api/facts/{fact_id}` | `create_memory_router.delete_fact` | [src/omni/channels/web_api/memory.py](../../src/omni/channels/web_api/memory.py#L28) |
| workbench | GET | `/api/llm/usage` | `create_runs_router.llm_usage` | [src/omni/channels/web_api/runs.py](../../src/omni/channels/web_api/runs.py#L69) |
| workbench | GET | `/api/mcp/overview` | `create_mcp_router.mcp_overview` | [src/omni/channels/web_api/mcp.py](../../src/omni/channels/web_api/mcp.py#L52) |
| workbench | GET | `/api/mcp/{server}/catalog` | `create_mcp_router.mcp_catalog` | [src/omni/channels/web_api/mcp.py](../../src/omni/channels/web_api/mcp.py#L42) |
| workbench | POST | `/api/mcp/{server}/confirm-schema` | `create_mcp_router.mcp_confirm_schema` | [src/omni/channels/web_api/mcp.py](../../src/omni/channels/web_api/mcp.py#L108) |
| workbench | POST | `/api/mcp/{server}/connect` | `create_mcp_router.mcp_connect` | [src/omni/channels/web_api/mcp.py](../../src/omni/channels/web_api/mcp.py#L30) |
| workbench | POST | `/api/mcp/{server}/disconnect` | `create_mcp_router.mcp_disconnect` | [src/omni/channels/web_api/mcp.py](../../src/omni/channels/web_api/mcp.py#L37) |
| workbench | GET | `/api/mcp/{server}/health` | `create_mcp_router.mcp_health` | [src/omni/channels/web_api/mcp.py](../../src/omni/channels/web_api/mcp.py#L19) |
| workbench | GET | `/api/mcp/{server}/logs` | `create_mcp_router.mcp_logs` | [src/omni/channels/web_api/mcp.py](../../src/omni/channels/web_api/mcp.py#L25) |
| workbench | POST | `/api/mcp/{server}/reset` | `create_mcp_router.mcp_reset` | [src/omni/channels/web_api/mcp.py](../../src/omni/channels/web_api/mcp.py#L124) |
| workbench | GET | `/api/mcp/{server}/tools` | `create_mcp_router.mcp_tools` | [src/omni/channels/web_api/mcp.py](../../src/omni/channels/web_api/mcp.py#L77) |
| workbench | GET | `/api/memory/retrieval` | `create_memory_router.retrieval_status` | [src/omni/channels/web_api/memory.py](../../src/omni/channels/web_api/memory.py#L125) |
| workbench | GET | `/api/memory/status` | `create_memory_router.memory_status` | [src/omni/channels/web_api/memory.py](../../src/omni/channels/web_api/memory.py#L35) |
| workbench | GET | `/api/profile` | `create_memory_router.get_profile` | [src/omni/channels/web_api/memory.py](../../src/omni/channels/web_api/memory.py#L58) |
| workbench | PUT | `/api/profile/line` | `create_memory_router.profile_line_update` | [src/omni/channels/web_api/memory.py](../../src/omni/channels/web_api/memory.py#L69) |
| workbench | DELETE | `/api/profile/line/{line_id}` | `create_memory_router.profile_line_delete` | [src/omni/channels/web_api/memory.py](../../src/omni/channels/web_api/memory.py#L78) |
| workbench | GET | `/api/publications` | `create_publications_router.publications` | [src/omni/channels/web_api/publications.py](../../src/omni/channels/web_api/publications.py#L16) |
| workbench | POST | `/api/publications/{identity}/edit` | `create_publications_router.publication_edit` | [src/omni/channels/web_api/publications.py](../../src/omni/channels/web_api/publications.py#L24) |
| workbench | POST | `/api/publications/{identity}/resolve` | `create_publications_router.publication_resolve` | [src/omni/channels/web_api/publications.py](../../src/omni/channels/web_api/publications.py#L38) |
| workbench | GET | `/api/resident` | `create_memory_router.get_resident` | [src/omni/channels/web_api/memory.py](../../src/omni/channels/web_api/memory.py#L84) |
| workbench | POST | `/api/resident/update` | `create_memory_router.resident_update` | [src/omni/channels/web_api/memory.py](../../src/omni/channels/web_api/memory.py#L94) |
| workbench | GET | `/api/reviews` | `create_memory_router.get_reviews` | [src/omni/channels/web_api/memory.py](../../src/omni/channels/web_api/memory.py#L115) |
| workbench | GET | `/api/runs` | `create_runs_router.run_list` | [src/omni/channels/web_api/runs.py](../../src/omni/channels/web_api/runs.py#L20) |
| workbench | POST | `/api/runs` | `create_conversation_router.run_create` | [src/omni/channels/web_api/conversation.py](../../src/omni/channels/web_api/conversation.py#L161) |
| workbench | GET | `/api/runs/{run_id}` | `create_runs_router.run_get` | [src/omni/channels/web_api/runs.py](../../src/omni/channels/web_api/runs.py#L28) |
| workbench | POST | `/api/runs/{run_id}/cancel` | `create_runs_router.run_cancel` | [src/omni/channels/web_api/runs.py](../../src/omni/channels/web_api/runs.py#L47) |
| workbench | GET | `/api/runs/{run_id}/events` | `create_runs_router.run_events` | [src/omni/channels/web_api/runs.py](../../src/omni/channels/web_api/runs.py#L35) |
| workbench | GET | `/api/sessions` | `create_sessions_router.get_sessions` | [src/omni/channels/web_api/sessions.py](../../src/omni/channels/web_api/sessions.py#L84) |
| workbench | POST | `/api/sessions` | `create_sessions_router.create_session` | [src/omni/channels/web_api/sessions.py](../../src/omni/channels/web_api/sessions.py#L98) |
| workbench | GET | `/api/sessions/{session_id}/messages` | `create_sessions_router.get_messages` | [src/omni/channels/web_api/sessions.py](../../src/omni/channels/web_api/sessions.py#L108) |
| workbench | POST | `/api/sessions/{session_id}/select` | `create_sessions_router.select_session` | [src/omni/channels/web_api/sessions.py](../../src/omni/channels/web_api/sessions.py#L102) |
| workbench | GET | `/api/sessions/{session_id}/todos` | `create_sessions_router.get_todos` | [src/omni/channels/web_api/sessions.py](../../src/omni/channels/web_api/sessions.py#L120) |
| workbench | GET | `/api/skills` | `create_skills_router.get_skills` | [src/omni/channels/web_api/skills.py](../../src/omni/channels/web_api/skills.py#L19) |
| workbench | POST | `/api/skills/ack-all` | `create_skills_router.skills_ack_all` | [src/omni/channels/web_api/skills.py](../../src/omni/channels/web_api/skills.py#L71) |
| workbench | GET | `/api/skills/pending` | `create_skills_router.skills_pending` | [src/omni/channels/web_api/skills.py](../../src/omni/channels/web_api/skills.py#L32) |
| workbench | GET | `/api/skills/pending/{name}/diff` | `create_skills_router.skill_pending_diff` | [src/omni/channels/web_api/skills.py](../../src/omni/channels/web_api/skills.py#L95) |
| workbench | GET | `/api/skills/{name}` | `create_skills_router.get_skill` | [src/omni/channels/web_api/skills.py](../../src/omni/channels/web_api/skills.py#L119) |
| workbench | POST | `/api/skills/{name}/ack` | `create_skills_router.skill_ack` | [src/omni/channels/web_api/skills.py](../../src/omni/channels/web_api/skills.py#L86) |
| workbench | GET | `/api/skills/{name}/diff` | `create_skills_router.skill_diff` | [src/omni/channels/web_api/skills.py](../../src/omni/channels/web_api/skills.py#L191) |
| workbench | GET | `/api/skills/{name}/history` | `create_skills_router.skill_history` | [src/omni/channels/web_api/skills.py](../../src/omni/channels/web_api/skills.py#L161) |
| workbench | GET | `/api/skills/{name}/revision/{rev}` | `create_skills_router.skill_revision` | [src/omni/channels/web_api/skills.py](../../src/omni/channels/web_api/skills.py#L181) |
| workbench | POST | `/api/skills/{name}/rollback` | `create_skills_router.skill_rollback` | [src/omni/channels/web_api/skills.py](../../src/omni/channels/web_api/skills.py#L211) |
| workbench | POST | `/api/skills/{name}/save` | `create_skills_router.skill_save` | [src/omni/channels/web_api/skills.py](../../src/omni/channels/web_api/skills.py#L139) |
| workbench | GET | `/api/state` | `create_sessions_router.get_state` | [src/omni/channels/web_api/sessions.py](../../src/omni/channels/web_api/sessions.py#L22) |
| workbench | GET | `/api/storage` | `create_storage_router.overview` | [src/omni/channels/web_api/storage.py](../../src/omni/channels/web_api/storage.py#L53) |
| workbench | PUT | `/api/storage` | `create_storage_router.update` | [src/omni/channels/web_api/storage.py](../../src/omni/channels/web_api/storage.py#L63) |
| workbench | GET | `/api/storage/files` | `create_storage_router.files` | [src/omni/channels/web_api/storage.py](../../src/omni/channels/web_api/storage.py#L85) |
| workbench | POST | `/api/storage/folders` | `create_storage_router.create_folder` | [src/omni/channels/web_api/storage.py](../../src/omni/channels/web_api/storage.py#L126) |
| workbench | POST | `/api/storage/preview` | `create_storage_router.preview` | [src/omni/channels/web_api/storage.py](../../src/omni/channels/web_api/storage.py#L58) |
| workbench | GET | `/api/storage/preview-file` | `create_storage_router.preview_file` | [src/omni/channels/web_api/storage.py](../../src/omni/channels/web_api/storage.py#L110) |
| workbench | POST | `/api/storage/upload` | `create_storage_router.upload` | [src/omni/channels/web_api/storage.py](../../src/omni/channels/web_api/storage.py#L136) |
| workbench | POST | `/api/system/backup` | `create_operations_router.system_backup` | [src/omni/channels/web_api/operations.py](../../src/omni/channels/web_api/operations.py#L27) |
| workbench | GET | `/api/system/bili-cookies` | `create_app.bili_cookies_status` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L319) |
| workbench | POST | `/api/system/bili-cookies` | `create_app.save_bili_cookies` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L331) |
| workbench | GET | `/api/system/config` | `create_app.system_config` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L234) |
| workbench | POST | `/api/system/config` | `create_app.system_config_save` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L280) |
| workbench | GET | `/api/system/cron` | `create_automation_router.cron_list` | [src/omni/channels/web_api/automation.py](../../src/omni/channels/web_api/automation.py#L59) |
| workbench | POST | `/api/system/cron/create` | `create_automation_router.cron_create` | [src/omni/channels/web_api/automation.py](../../src/omni/channels/web_api/automation.py#L22) |
| workbench | POST | `/api/system/cron/delete` | `create_automation_router.cron_delete` | [src/omni/channels/web_api/automation.py](../../src/omni/channels/web_api/automation.py#L159) |
| workbench | GET | `/api/system/cron/history` | `create_automation_router.cron_history` | [src/omni/channels/web_api/automation.py](../../src/omni/channels/web_api/automation.py#L166) |
| workbench | GET | `/api/system/cron/outbox` | `create_automation_router.cron_outbox` | [src/omni/channels/web_api/automation.py](../../src/omni/channels/web_api/automation.py#L174) |
| workbench | POST | `/api/system/cron/outbox/{entry_id}/discard` | `create_automation_router.cron_outbox_discard` | [src/omni/channels/web_api/automation.py](../../src/omni/channels/web_api/automation.py#L201) |
| workbench | POST | `/api/system/cron/outbox/{entry_id}/retry` | `create_automation_router.cron_outbox_retry` | [src/omni/channels/web_api/automation.py](../../src/omni/channels/web_api/automation.py#L189) |
| workbench | POST | `/api/system/cron/toggle` | `create_automation_router.cron_toggle` | [src/omni/channels/web_api/automation.py](../../src/omni/channels/web_api/automation.py#L143) |
| workbench | POST | `/api/system/cron/update` | `create_automation_router.cron_update` | [src/omni/channels/web_api/automation.py](../../src/omni/channels/web_api/automation.py#L93) |
| workbench | GET | `/api/system/doctor` | `create_operations_router.system_doctor` | [src/omni/channels/web_api/operations.py](../../src/omni/channels/web_api/operations.py#L23) |
| workbench | GET | `/api/system/logs/{name}` | `create_operations_router.system_logs` | [src/omni/channels/web_api/operations.py](../../src/omni/channels/web_api/operations.py#L55) |
| workbench | POST | `/api/system/model-key` | `create_app.model_key_set` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L356) |
| workbench | GET | `/api/system/model-keys` | `create_app.model_keys` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L351) |
| workbench | POST | `/api/system/model-test` | `create_app.model_test` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L373) |
| workbench | GET | `/api/system/napcat/qr` | `create_operations_router.system_napcat_qr` | [src/omni/channels/web_api/operations.py](../../src/omni/channels/web_api/operations.py#L51) |
| workbench | GET | `/api/system/overview` | `create_operations_router.system_overview` | [src/omni/channels/web_api/operations.py](../../src/omni/channels/web_api/operations.py#L19) |
| workbench | GET | `/api/system/qq-policy` | `create_app.qq_policy_status` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L300) |
| workbench | POST | `/api/system/qq-policy/stop` | `create_app.qq_policy_stop` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L311) |
| workbench | POST | `/api/system/services/{name}/start` | `create_operations_router.system_service_start` | [src/omni/channels/web_api/operations.py](../../src/omni/channels/web_api/operations.py#L35) |
| workbench | POST | `/api/system/services/{name}/stop` | `create_operations_router.system_service_stop` | [src/omni/channels/web_api/operations.py](../../src/omni/channels/web_api/operations.py#L44) |
| workbench | GET | `/api/system/state` | `create_operations_router.system_state` | [src/omni/channels/web_api/operations.py](../../src/omni/channels/web_api/operations.py#L59) |
| workbench | GET | `/api/terminal/options` | `create_sessions_router.options` | [src/omni/channels/terminal/sessions.py](../../src/omni/channels/terminal/sessions.py#L41) |
| workbench | GET | `/api/terminal/sessions` | `create_sessions_router.sessions` | [src/omni/channels/terminal/sessions.py](../../src/omni/channels/terminal/sessions.py#L45) |
| workbench | POST | `/api/terminal/sessions` | `create_sessions_router.create` | [src/omni/channels/terminal/sessions.py](../../src/omni/channels/terminal/sessions.py#L49) |
| workbench | DELETE | `/api/terminal/sessions/{identifier}` | `create_sessions_router.end` | [src/omni/channels/terminal/sessions.py](../../src/omni/channels/terminal/sessions.py#L63) |
| workbench | PATCH | `/api/terminal/sessions/{identifier}` | `create_sessions_router.rename` | [src/omni/channels/terminal/sessions.py](../../src/omni/channels/terminal/sessions.py#L57) |
| workbench | WEBSOCKET | `/api/terminal/sessions/{identifier}/ws` | `create_sessions_router.connect` | [src/omni/channels/terminal/sessions.py](../../src/omni/channels/terminal/sessions.py#L71) |
| workbench | WEBSOCKET | `/api/terminal/ws` | `create_terminal_router.terminal_ws` | [src/omni/channels/terminal/routes.py](../../src/omni/channels/terminal/routes.py#L15) |
| workbench | POST | `/api/todos/toggle` | `create_sessions_router.toggle_todo` | [src/omni/channels/web_api/sessions.py](../../src/omni/channels/web_api/sessions.py#L132) |
| workbench | GET | `/api/workbench/docs/{name}` | `create_workbench_router.document` | [src/omni/channels/workbench/routes.py](../../src/omni/channels/workbench/routes.py#L39) |
| workbench | GET | `/api/workbench/extensions` | `create_workbench_router.extensions` | [src/omni/channels/workbench/routes.py](../../src/omni/channels/workbench/routes.py#L57) |
| workbench | GET | `/api/workbench/project` | `create_workbench_router.project` | [src/omni/channels/workbench/routes.py](../../src/omni/channels/workbench/routes.py#L35) |
| workbench | GET | `/api/workbench/tools` | `create_workbench_router.tools` | [src/omni/channels/workbench/routes.py](../../src/omni/channels/workbench/routes.py#L50) |
| workbench | GET | `/pair/{code}` | `create_app.pair_link` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L202) |

## 请求模型

### TerminalCreateIn

定位：[src/omni/channels/terminal/sessions.py](../../src/omni/channels/terminal/sessions.py#L15)。

| 字段 | 类型 | 必填 |
|---|---|---|
| shell | `Literal['powershell', 'pwsh', 'cmd']` | 否 |
| cwd | `str \| None` | 否 |
| cols | `int` | 否 |
| rows | `int` | 否 |

### TerminalRenameIn

定位：[src/omni/channels/terminal/sessions.py](../../src/omni/channels/terminal/sessions.py#L22)。

| 字段 | 类型 | 必填 |
|---|---|---|
| label | `str` | 否 |

### ModelKeyIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L77)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| value | `str \| None` | 否 |

### AndroidActionIn

定位：[src/omni/channels/web_api/android.py](../../src/omni/channels/web_api/android.py#L14)。

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

### ChatIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L9)。

| 字段 | 类型 | 必填 |
|---|---|---|
| message | `str` | 是 |
| session_id | `int \| None` | 否 |

### ResolveIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L14)。

| 字段 | 类型 | 必填 |
|---|---|---|
| allowed | `bool` | 是 |
| reason | `str` | 否 |

### NapCatStartIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L19)。

| 字段 | 类型 | 必填 |
|---|---|---|
| qq | `str` | 否 |

### ConfigUpdateIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L23)。

| 字段 | 类型 | 必填 |
|---|---|---|
| updates | `dict[str, dict[str, Any]]` | 是 |

### ModelTestIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L27)。

| 字段 | 类型 | 必填 |
|---|---|---|
| model | `str` | 是 |

### MCPSchemaConfirmIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L31)。

| 字段 | 类型 | 必填 |
|---|---|---|
| fingerprint | `str` | 是 |

### BiliCookiesIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L35)。

| 字段 | 类型 | 必填 |
|---|---|---|
| sessdata | `str` | 否 |
| bili_jct | `str` | 否 |
| buvid3 | `str` | 否 |

### CronNameIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L41)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |

### CronToggleIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L45)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| enabled | `bool` | 是 |

### CronCreateIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L50)。

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

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L62)。

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

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L78)。

| 字段 | 类型 | 必填 |
|---|---|---|
| session_id | `int` | 是 |
| todo_id | `int` | 是 |
| done | `bool` | 是 |

### AndroidTapIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L84)。

| 字段 | 类型 | 必填 |
|---|---|---|
| x | `int` | 是 |
| y | `int` | 是 |
| stream_w | `int` | 是 |
| stream_h | `int` | 是 |
| observation | `str` | 否 |

### AndroidElementIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L92)。

| 字段 | 类型 | 必填 |
|---|---|---|
| idx | `int` | 是 |
| observation | `str` | 否 |

### AndroidKeyIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L97)。

| 字段 | 类型 | 必填 |
|---|---|---|
| keycode | `str` | 是 |
| observation | `str` | 否 |

### ProfileLineIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L102)。

| 字段 | 类型 | 必填 |
|---|---|---|
| line_id | `int` | 是 |
| content | `str` | 是 |

### ResidentUpdateIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L107)。

| 字段 | 类型 | 必填 |
|---|---|---|
| operation | `str` | 是 |
| id | `int \| None` | 否 |
| content | `str` | 否 |

### PairIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L113)。

| 字段 | 类型 | 必填 |
|---|---|---|
| code | `str` | 是 |

### SkillSaveIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L117)。

| 字段 | 类型 | 必填 |
|---|---|---|
| content | `str` | 是 |
| expected_hash | `str \| None` | 否 |

### SkillRollbackIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L124)。

| 字段 | 类型 | 必填 |
|---|---|---|
| rev | `int` | 是 |
| expected_hash | `str \| None` | 否 |

### SkillAckIn

定位：[src/omni/channels/web_api/schemas.py](../../src/omni/channels/web_api/schemas.py#L129)。

| 字段 | 类型 | 必填 |
|---|---|---|
| rev | `int` | 是 |

### DirectoryUpdate

定位：[src/omni/channels/web_api/storage.py](../../src/omni/channels/web_api/storage.py#L26)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `str` | 否 |
| catalog | `dict` | 是 |
| acknowledge | `bool` | 否 |

### FolderCreate

定位：[src/omni/channels/web_api/storage.py](../../src/omni/channels/web_api/storage.py#L33)。

| 字段 | 类型 | 必填 |
|---|---|---|
| scope | `str` | 是 |
| path | `str` | 否 |

### Persona

定位：[src/omni/config.py](../../src/omni/config.py#L126)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 否 |
| system_prompt | `str` | 否 |

### Models

定位：[src/omni/config.py](../../src/omni/config.py#L133)。

| 字段 | 类型 | 必填 |
|---|---|---|
| chat | `str` | 否 |
| vision | `str` | 否 |
| local | `str` | 否 |
| review | `str` | 否 |

### ModelCapability

定位：[src/omni/config.py](../../src/omni/config.py#L140)。

| 字段 | 类型 | 必填 |
|---|---|---|
| tools | `bool` | 否 |
| vision | `bool` | 否 |
| stream | `bool` | 否 |
| context_tokens | `int` | 否 |
| input_usd_per_m | `float \| None` | 否 |
| output_usd_per_m | `float \| None` | 否 |

### Retry

定位：[src/omni/config.py](../../src/omni/config.py#L158)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_attempts | `int` | 否 |
| total_deadline_s | `float` | 否 |
| backoff_s | `float` | 否 |

### LLMBudget

定位：[src/omni/config.py](../../src/omni/config.py#L166)。

| 字段 | 类型 | 必填 |
|---|---|---|
| daily_usd | `float` | 否 |
| per_run_usd | `float` | 否 |

### Vision

定位：[src/omni/config.py](../../src/omni/config.py#L178)。

| 字段 | 类型 | 必填 |
|---|---|---|
| send_to_cloud | `bool` | 否 |
| max_image_bytes | `int` | 否 |
| max_images_per_turn | `int` | 否 |
| max_edge_px | `int` | 否 |

### Artifacts

定位：[src/omni/config.py](../../src/omni/config.py#L192)。

| 字段 | 类型 | 必填 |
|---|---|---|
| retention_days | `int` | 否 |
| max_total_mb | `int` | 否 |

### LLM

定位：[src/omni/config.py](../../src/omni/config.py#L200)。

| 字段 | 类型 | 必填 |
|---|---|---|
| request_timeout_s | `float` | 否 |
| max_output_tokens | `int` | 否 |
| retry | `Retry` | 否 |
| budget | `LLMBudget` | 否 |
| capabilities | `dict[str, ModelCapability]` | 否 |

### Limits

定位：[src/omni/config.py](../../src/omni/config.py#L213)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_history_messages | `int` | 否 |
| max_agent_steps | `int` | 否 |
| resident_memory_chars | `int` | 否 |
| session_queue_depth | `int` | 否 |
| session_lease_ttl_s | `int` | 否 |

### Review

定位：[src/omni/config.py](../../src/omni/config.py#L221)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| max_messages | `int` | 否 |
| steps | `int` | 否 |
| queue_depth | `int` | 否 |
| min_messages | `int` | 否 |

### Memory

定位：[src/omni/config.py](../../src/omni/config.py#L231)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_writes_per_day | `int` | 否 |
| max_fact_chars | `int` | 否 |
| recall_prefix | `bool` | 否 |

### Web

定位：[src/omni/config.py](../../src/omni/config.py#L243)。

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

定位：[src/omni/config.py](../../src/omni/config.py#L260)。

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

定位：[src/omni/config.py](../../src/omni/config.py#L278)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| trigger_chars | `int` | 否 |
| keep_recent | `int` | 否 |
| min_messages | `int` | 否 |

### Asr

定位：[src/omni/config.py](../../src/omni/config.py#L287)。

| 字段 | 类型 | 必填 |
|---|---|---|
| model | `str` | 否 |
| device | `str` | 否 |
| compute_type | `str` | 否 |

### QQ

定位：[src/omni/config.py](../../src/omni/config.py#L295)。

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

定位：[src/omni/config.py](../../src/omni/config.py#L332)。

| 字段 | 类型 | 必填 |
|---|---|---|
| headless | `bool` | 否 |
| slow_mo_ms | `int` | 否 |
| max_writes_per_day | `int` | 否 |
| approval_timeout_s | `float` | 否 |

### UIFormSelectors

定位：[src/omni/config.py](../../src/omni/config.py#L341)。

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

定位：[src/omni/config.py](../../src/omni/config.py#L359)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| forms | `dict[str, UIFormSelectors]` | 否 |
| draft_ttl_s | `int` | 否 |
| max_content_chars | `int` | 否 |

### System

定位：[src/omni/config.py](../../src/omni/config.py#L375)。

| 字段 | 类型 | 必填 |
|---|---|---|
| napcat_dir | `str` | 否 |

### Approval

定位：[src/omni/config.py](../../src/omni/config.py#L381)。

| 字段 | 类型 | 必填 |
|---|---|---|
| mode | `str` | 否 |

### MCPServerConfig

定位：[src/omni/config.py](../../src/omni/config.py#L391)。

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

定位：[src/omni/config.py](../../src/omni/config.py#L421)。

| 字段 | 类型 | 必填 |
|---|---|---|
| servers | `dict[str, MCPServerConfig]` | 否 |

### Desktop

定位：[src/omni/config.py](../../src/omni/config.py#L435)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| allowed_apps | `list[str]` | 否 |
| max_nodes | `int` | 否 |
| timeout_s | `float` | 否 |
| observation_ttl_s | `float` | 否 |
| max_writes_per_day | `int` | 否 |

### Embedding

定位：[src/omni/config.py](../../src/omni/config.py#L452)。

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

定位：[src/omni/config.py](../../src/omni/config.py#L490)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_running | `int` | 否 |

### AndroidConfig

定位：[src/omni/config.py](../../src/omni/config.py#L495)。

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

定位：[src/omni/config.py](../../src/omni/config.py#L514)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_running | `int` | 否 |
| max_waiting | `int` | 否 |
| default_timeout_s | `float` | 否 |
| log_bytes | `int` | 否 |
| retention_days | `int` | 否 |
| total_log_bytes | `int` | 否 |

### TerminalConfig

定位：[src/omni/config.py](../../src/omni/config.py#L524)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_sessions | `int` | 否 |
| roots | `list[str]` | 否 |
| buffer_bytes | `int` | 否 |
| detach_ttl_s | `float` | 否 |

### AppConfig

定位：[src/omni/config.py](../../src/omni/config.py#L532)。

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
| compression | `Compression` | 否 |
| asr | `Asr` | 否 |
| qq | `QQ` | 否 |
| browser | `Browser` | 否 |
| bilibili_write | `BilibiliWrite` | 否 |
| system | `System` | 否 |
| approval | `Approval` | 否 |
| mcp | `MCPConfig` | 否 |
| cron | `Cron` | 否 |
| embedding | `Embedding` | 否 |
| desktop | `Desktop` | 否 |

### Catalog

定位：[src/omni/system/agent_spaces/catalog.py](../../src/omni/system/agent_spaces/catalog.py#L19)。

| 字段 | 类型 | 必填 |
|---|---|---|
| version | `int` | 否 |
| active | `str` | 否 |
| agents | `list[AgentProfile]` | 否 |

### DirectoryOverrides

定位：[src/omni/system/agent_spaces/layout.py](../../src/omni/system/agent_spaces/layout.py#L17)。

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

定位：[src/omni/system/agent_spaces/layout.py](../../src/omni/system/agent_spaces/layout.py#L30)。

| 字段 | 类型 | 必填 |
|---|---|---|
| id | `str` | 否 |
| name | `str` | 否 |
| home | `str` | 否 |
| directories | `DirectoryOverrides` | 否 |

## 静态工具声明

不是启用清单；条件注册和插件工具以 `/api/workbench/tools` 的本进程快照为准。SAFE 不等于 read_only。

| 工具 | 审批等级 | 强制人工 | 并发只读 | 模块 | 定位 |
|---|---|---|---|---|---|
| `android_key` | confirm | True | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L182) |
| `android_launch_app` | confirm | True | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L232) |
| `android_list_apps` | safe | False | True | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L230) |
| `android_long_press` | confirm | True | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L235) |
| `android_screen_tree` | safe | False | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L97) |
| `android_screenshot` | safe | False | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L112) |
| `android_status` | safe | False | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L87) |
| `android_swipe` | confirm | True | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L165) |
| `android_tap_coords` | confirm | True | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L147) |
| `android_tap_element` | confirm | True | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L122) |
| `android_type` | confirm | True | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L199) |
| `bilibili_video_comments` | safe | False | True | bilibili | [src/omni/tools/bilibili/tools.py](../../src/omni/tools/bilibili/tools.py#L113) |
| `bilibili_video_info` | safe | False | True | bilibili | [src/omni/tools/bilibili/tools.py](../../src/omni/tools/bilibili/tools.py#L93) |
| `bilibili_video_subtitle` | safe | False | True | bilibili | [src/omni/tools/bilibili/tools.py](../../src/omni/tools/bilibili/tools.py#L131) |
| `bilibili_video_transcript` | safe | False | True | bilibili | [src/omni/tools/bilibili/tools.py](../../src/omni/tools/bilibili/tools.py#L148) |
| `browser_click` | confirm | True | False | browser | [src/omni/tools/browser/tools.py](../../src/omni/tools/browser/tools.py#L165) |
| `browser_navigate` | safe | False | False | browser | [src/omni/tools/browser/tools.py](../../src/omni/tools/browser/tools.py#L131) |
| `browser_read` | safe | False | False | browser | [src/omni/tools/browser/tools.py](../../src/omni/tools/browser/tools.py#L145) |
| `browser_screenshot` | safe | False | False | browser | [src/omni/tools/browser/tools.py](../../src/omni/tools/browser/tools.py#L155) |
| `browser_type` | confirm | True | False | browser | [src/omni/tools/browser/tools.py](../../src/omni/tools/browser/tools.py#L183) |
| `community_config` | safe | False | False | community | [src/omni/plugins/community/tools.py](../../src/omni/plugins/community/tools.py#L55) |
| `community_info` | safe | False | True | community | [src/omni/plugins/community/tools.py](../../src/omni/plugins/community/tools.py#L89) |
| `community_install` | safe | False | False | community | [src/omni/plugins/community/tools.py](../../src/omni/plugins/community/tools.py#L104) |
| `community_publish` | confirm | True | False | community | [src/omni/plugins/community/tools.py](../../src/omni/plugins/community/tools.py#L124) |
| `community_search` | safe | False | True | community | [src/omni/plugins/community/tools.py](../../src/omni/plugins/community/tools.py#L71) |
| `cron_add` | safe | False | False | scheduling | [src/omni/tools/scheduling/tools.py](../../src/omni/tools/scheduling/tools.py#L199) |
| `cron_edit` | safe | False | False | scheduling | [src/omni/tools/scheduling/tools.py](../../src/omni/tools/scheduling/tools.py#L278) |
| `cron_list` | safe | False | True | scheduling | [src/omni/tools/scheduling/tools.py](../../src/omni/tools/scheduling/tools.py#L188) |
| `cron_once` | safe | False | False | scheduling | [src/omni/tools/scheduling/tools.py](../../src/omni/tools/scheduling/tools.py#L237) |
| `cron_remove` | safe | False | False | scheduling | [src/omni/tools/scheduling/tools.py](../../src/omni/tools/scheduling/tools.py#L264) |
| `daily_digest` | safe | False | True | personal | [src/omni/tools/personal/digest.py](../../src/omni/tools/personal/digest.py#L46) |
| `delegate_task` | safe | False | False | delegation | [src/omni/tools/delegation/tools.py](../../src/omni/tools/delegation/tools.py#L103) |
| `files_diff` | safe | False | True | files | [src/omni/tools/files/tools.py](../../src/omni/tools/files/tools.py#L487) |
| `files_history` | safe | False | True | files | [src/omni/tools/files/tools.py](../../src/omni/tools/files/tools.py#L559) |
| `files_list` | safe | False | True | files | [src/omni/tools/files/tools.py](../../src/omni/tools/files/tools.py#L429) |
| `files_read` | safe | False | True | files | [src/omni/tools/files/tools.py](../../src/omni/tools/files/tools.py#L468) |
| `files_restore` | confirm | True | False | files | [src/omni/tools/files/tools.py](../../src/omni/tools/files/tools.py#L574) |
| `files_search` | safe | False | True | files | [src/omni/tools/files/tools.py](../../src/omni/tools/files/tools.py#L446) |
| `files_write` | confirm | True | False | files | [src/omni/tools/files/tools.py](../../src/omni/tools/files/tools.py#L514) |
| `forget` | confirm | False | False | personal | [src/omni/tools/personal/facts.py](../../src/omni/tools/personal/facts.py#L136) |
| `get_time` | safe | False | True | personal | [src/omni/tools/personal/clock.py](../../src/omni/tools/personal/clock.py#L14) |
| `mcp_call_tool` | confirm | True | False | mcp | [src/omni/tools/mcp/tools.py](../../src/omni/tools/mcp/tools.py#L73) |
| `mcp_list_tools` | safe | False | True | mcp | [src/omni/tools/mcp/tools.py](../../src/omni/tools/mcp/tools.py#L41) |
| `mcp_read_tool` | safe | False | True | mcp | [src/omni/tools/mcp/tools.py](../../src/omni/tools/mcp/tools.py#L69) |
| `peek_command` | safe | False | True | shell | [src/omni/tools/shell/tools.py](../../src/omni/tools/shell/tools.py#L214) |
| `plugin_activate` | confirm | True | False | plugins | [src/omni/plugins/tools.py](../../src/omni/plugins/tools.py#L116) |
| `plugin_disable` | safe | False | False | plugins | [src/omni/plugins/tools.py](../../src/omni/plugins/tools.py#L151) |
| `plugin_enable` | safe | False | False | plugins | [src/omni/plugins/tools.py](../../src/omni/plugins/tools.py#L137) |
| `plugin_install` | safe | False | False | plugins | [src/omni/plugins/tools.py](../../src/omni/plugins/tools.py#L95) |
| `plugin_list` | safe | False | True | plugins | [src/omni/plugins/tools.py](../../src/omni/plugins/tools.py#L80) |
| `plugin_remove` | confirm | True | False | plugins | [src/omni/plugins/tools.py](../../src/omni/plugins/tools.py#L168) |
| `profile_manage` | safe | False | False | personal | [src/omni/tools/personal/profile.py](../../src/omni/tools/personal/profile.py#L60) |
| `qq_send_message` | safe | False | False | qq-tools | [src/omni/tools/qq/tools.py](../../src/omni/tools/qq/tools.py#L75) |
| `recall` | safe | False | True | personal | [src/omni/tools/personal/facts.py](../../src/omni/tools/personal/facts.py#L116) |
| `remember` | safe | False | False | personal | [src/omni/tools/personal/facts.py](../../src/omni/tools/personal/facts.py#L97) |
| `resident_memory` | safe | False | False | personal | [src/omni/tools/personal/resident.py](../../src/omni/tools/personal/resident.py#L38) |
| `run_command` | confirm | True | False | shell | [src/omni/tools/shell/tools.py](../../src/omni/tools/shell/tools.py#L243) |
| `search_history` | safe | False | True | personal | [src/omni/tools/personal/facts.py](../../src/omni/tools/personal/facts.py#L169) |
| `session_refine` | safe | False | False | personal | [src/omni/tools/personal/refine.py](../../src/omni/tools/personal/refine.py#L45) |
| `shell_job_cancel` | confirm | True | False | command-jobs | [src/omni/tools/shell/job_tools.py](../../src/omni/tools/shell/job_tools.py#L50) |
| `shell_job_list` | safe | False | True | command-jobs | [src/omni/tools/shell/job_tools.py](../../src/omni/tools/shell/job_tools.py#L46) |
| `shell_job_read` | safe | False | True | command-jobs | [src/omni/tools/shell/job_tools.py](../../src/omni/tools/shell/job_tools.py#L48) |
| `shell_job_start` | confirm | True | False | command-jobs | [src/omni/tools/shell/job_tools.py](../../src/omni/tools/shell/job_tools.py#L44) |
| `skill_activate` | confirm | True | False | skills | [src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L365) |
| `skill_create` | safe | False | False | skills | [src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L171) |
| `skill_discard` | safe | False | False | skills | [src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L385) |
| `skill_history` | safe | False | True | skills | [src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L244) |
| `skill_import` | safe | False | False | skills | [src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L330) |
| `skill_patch` | safe | False | False | skills | [src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L194) |
| `skill_pending` | safe | False | True | skills | [src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L354) |
| `skill_rewrite` | safe | False | False | skills | [src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L221) |
| `skill_rollback` | safe | False | False | skills | [src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L265) |
| `skill_view` | safe | False | True | skills | [src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L153) |
| `skills_list` | safe | False | True | skills | [src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L142) |
| `system_service` | confirm | False | False | system-tools | [src/omni/tools/system/tools.py](../../src/omni/tools/system/tools.py#L94) |
| `system_status` | safe | False | True | system-tools | [src/omni/tools/system/tools.py](../../src/omni/tools/system/tools.py#L83) |
| `todo` | safe | False | False | personal | [src/omni/tools/personal/todos.py](../../src/omni/tools/personal/todos.py#L60) |
| `web_read` | safe | False | True | web-fetch | [src/omni/tools/web/tools.py](../../src/omni/tools/web/tools.py#L113) |
