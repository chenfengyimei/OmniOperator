# API、对话调用与工具声明

> 由 `uv run --no-sync python scripts/project_index.py` 生成。不要手改；分类改 system/catalog/schema.py，分层改 ARCHITECTURE.md。

工作台 API 统一受配对/Host/Origin 规则保护；终端 WS 单独握手校验。社区服务器是独立服务。请求字段为源码声明，完整 schema 可查询 FastAPI OpenAPI。

## 对话与模型

Web/QQ/CLI 的请求经 Runtime 进入同一内核，模型请求经 Gateway 记账。

`create_conversation_router.chat`（[agent/src/omni/channels/web_api/conversation.py](../../../agent/src/omni/channels/web_api/conversation.py#L45)） → `Agent.run_turn`（[agent/src/omni/core/agent.py](../../../agent/src/omni/core/agent.py#L609)） → `Agent._chat`（[agent/src/omni/core/agent.py](../../../agent/src/omni/core/agent.py#L795)） → `Gateway.complete`（[agent/src/omni/llm/gateway.py](../../../agent/src/omni/llm/gateway.py#L467)）

## 模型调用工具

模型工具调用经统一注册表和审批；结果按原调用顺序回填。

`Agent._execute_tool_calls`（[agent/src/omni/core/agent.py](../../../agent/src/omni/core/agent.py#L991)） → `ToolRegistry.execute`（[agent/src/omni/tools/registry.py](../../../agent/src/omni/tools/registry.py#L147)） → `WebApprover`（[agent/src/omni/safety/gates.py](../../../agent/src/omni/safety/gates.py#L127)） → `ToolResult`（[agent/src/omni/tools/result.py](../../../agent/src/omni/tools/result.py#L36)）

## 流式与取消

流式响应和持久运行事件是两条接口；停止通过协作取消传播。

`create_conversation_router.chat_stream`（[agent/src/omni/channels/web_api/conversation.py](../../../agent/src/omni/channels/web_api/conversation.py#L80)） → `Agent.run_turn`（[agent/src/omni/core/agent.py](../../../agent/src/omni/core/agent.py#L609)） → `Gateway.stream`（[agent/src/omni/llm/gateway.py](../../../agent/src/omni/llm/gateway.py#L556)） → `RunStore`（[agent/src/omni/memory/runs.py](../../../agent/src/omni/memory/runs.py#L86)）

## 交互式终端

WebSocket 先校验身份，再由会话池创建独立 ConPTY；断开时核查退出。

`create_terminal_router.terminal_ws`（[agent/src/omni/channels/terminal/routes.py](../../../agent/src/omni/channels/terminal/routes.py#L19)） → `WebAuth.check_websocket`（[agent/src/omni/channels/web_auth.py](../../../agent/src/omni/channels/web_auth.py#L328)） → `TerminalManager.open`（[agent/src/omni/channels/terminal/pool.py](../../../agent/src/omni/channels/terminal/pool.py#L38)） → `TerminalSession`（[agent/src/omni/channels/terminal/session.py](../../../agent/src/omni/channels/terminal/session.py#L12)） → `open_process`（[agent/src/omni/channels/terminal/backend.py](../../../agent/src/omni/channels/terminal/backend.py#L30)）

## 插件扩展接入

下载/收包、隔离校验、人工激活、命名空间注册，最终仍由工具审批链执行。

`CommunityClient.install`（[agent/src/omni/plugins/community/client.py](../../../agent/src/omni/plugins/community/client.py#L198)） → `PluginHost.install_package`（[agent/src/omni/plugins/host.py](../../../agent/src/omni/plugins/host.py#L159)） → `PluginHost.activate`（[agent/src/omni/plugins/host.py](../../../agent/src/omni/plugins/host.py#L229)） → `PluginContext.add_tool`（[agent/src/omni/plugins/host.py](../../../agent/src/omni/plugins/host.py#L76)） → `ToolRegistry.execute`（[agent/src/omni/tools/registry.py](../../../agent/src/omni/tools/registry.py#L147)）

## 技能数据接入

技能是提示与参考数据；导入不执行脚本，激活需人工确认。

`register_skill_inbox_tools`（[agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L345)） → `SkillInbox`（[agent/src/omni/memory/skill_inbox.py](../../../agent/src/omni/memory/skill_inbox.py#L82)） → `SkillStore`（[agent/src/omni/memory/skills.py](../../../agent/src/omni/memory/skills.py#L86)）

## 外部 MCP 能力

渐进发现与授权校验后调用外部进程；结果未知不重放写操作。

`register_mcp_tools`（[agent/src/omni/tools/mcp/tools.py](../../../agent/src/omni/tools/mcp/tools.py#L19)） → `MCPManager`（[agent/src/omni/tools/mcp/client.py](../../../agent/src/omni/tools/mcp/client.py#L375)） → `MCPSchemaStore`（[agent/src/omni/memory/mcp_schemas.py](../../../agent/src/omni/memory/mcp_schemas.py#L23)）

## 声明路由

| 服务 | 方法 | 路径 | 处理函数 | 定位 |
|---|---|---|---|---|
| community | GET | `/api/download/{plugin_id}` | `create_app.download` | [agent/src/omni/plugins/community/server.py](../../../agent/src/omni/plugins/community/server.py#L141) |
| community | GET | `/api/health` | `create_app.health` | [agent/src/omni/plugins/community/server.py](../../../agent/src/omni/plugins/community/server.py#L109) |
| community | GET | `/api/plugins` | `create_app.search_plugins` | [agent/src/omni/plugins/community/server.py](../../../agent/src/omni/plugins/community/server.py#L113) |
| community | GET | `/api/plugins/{plugin_id}` | `create_app.plugin_detail` | [agent/src/omni/plugins/community/server.py](../../../agent/src/omni/plugins/community/server.py#L130) |
| community | POST | `/api/publish` | `create_app.publish` | [agent/src/omni/plugins/community/server.py](../../../agent/src/omni/plugins/community/server.py#L169) |
| v2 | GET | `/` | `create_app.index` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L767) |
| v2 | GET | `/api/v1/agents` | `create_app.agents` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L524) |
| v2 | POST | `/api/v1/agents` | `create_app.create_agent` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L528) |
| v2 | GET | `/api/v1/agents/{agent_id}` | `create_app.get_agent` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L533) |
| v2 | PUT | `/api/v1/agents/{agent_id}` | `create_app.rename_agent` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L537) |
| v2 | GET | `/api/v1/agents/{agent_id}/action-reviews` | `action_reviews_router.action_review_list` | [agent/src/omni/adapters/http_routes/action_reviews.py](../../../agent/src/omni/adapters/http_routes/action_reviews.py#L16) |
| v2 | GET | `/api/v1/agents/{agent_id}/actions/{action_id}/review` | `action_reviews_router.action_review_detail` | [agent/src/omni/adapters/http_routes/action_reviews.py](../../../agent/src/omni/adapters/http_routes/action_reviews.py#L25) |
| v2 | POST | `/api/v1/agents/{agent_id}/actions/{action_id}/review` | `action_reviews_router.action_review_commit` | [agent/src/omni/adapters/http_routes/action_reviews.py](../../../agent/src/omni/adapters/http_routes/action_reviews.py#L39) |
| v2 | GET | `/api/v1/agents/{agent_id}/actions/{action_id}/reviews` | `action_reviews_router.action_review_history` | [agent/src/omni/adapters/http_routes/action_reviews.py](../../../agent/src/omni/adapters/http_routes/action_reviews.py#L29) |
| v2 | GET | `/api/v1/agents/{agent_id}/android/access` | `android_router.android_access_list` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L69) |
| v2 | POST | `/api/v1/agents/{agent_id}/android/access/{access_id}/revoke` | `android_router.android_access_revoke` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L75) |
| v2 | POST | `/api/v1/agents/{agent_id}/android/actions` | `android_router.android_action` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L122) |
| v2 | GET | `/api/v1/agents/{agent_id}/android/environments` | `android_router.android_ime_environments` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L56) |
| v2 | POST | `/api/v1/agents/{agent_id}/android/environments/{environment_id}/restore` | `android_router.android_ime_restore` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L62) |
| v2 | GET | `/api/v1/agents/{agent_id}/android/grants` | `android_router.android_grants` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L32) |
| v2 | POST | `/api/v1/agents/{agent_id}/android/grants` | `android_router.android_grant` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L92) |
| v2 | POST | `/api/v1/agents/{agent_id}/android/grants/{grant_id}/apps` | `android_router.android_apps` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L80) |
| v2 | POST | `/api/v1/agents/{agent_id}/android/grants/{grant_id}/capture` | `android_router.android_capture` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L111) |
| v2 | POST | `/api/v1/agents/{agent_id}/android/grants/{grant_id}/observe` | `android_router.android_observe` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L104) |
| v2 | POST | `/api/v1/agents/{agent_id}/android/grants/{grant_id}/revoke` | `android_router.android_revoke` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L99) |
| v2 | GET | `/api/v1/agents/{agent_id}/android/grants/{grant_id}/unicode` | `android_router.android_unicode_profile` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L45) |
| v2 | PUT | `/api/v1/agents/{agent_id}/android/grants/{grant_id}/unicode` | `android_router.android_unicode_profile_save` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L49) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/android/observations/{observation_id}` | `android_router.android_dismiss` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L118) |
| v2 | POST | `/api/v1/agents/{agent_id}/android/probe` | `android_router.android_probe` | [agent/src/omni/adapters/http_routes/android.py](../../../agent/src/omni/adapters/http_routes/android.py#L38) |
| v2 | GET | `/api/v1/agents/{agent_id}/approvals` | `create_app.approvals` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L717) |
| v2 | POST | `/api/v1/agents/{agent_id}/approvals/{approval_id}` | `create_app.approve` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L721) |
| v2 | GET | `/api/v1/agents/{agent_id}/audit` | `operations_router.audit` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L68) |
| v2 | GET | `/api/v1/agents/{agent_id}/audit/retention-preview` | `operations_router.audit_retention_preview` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L45) |
| v2 | GET | `/api/v1/agents/{agent_id}/bilibili/profiles/{config_id}` | `bilibili_router.bilibili_profile` | [agent/src/omni/adapters/http_routes/bilibili.py](../../../agent/src/omni/adapters/http_routes/bilibili.py#L70) |
| v2 | PUT | `/api/v1/agents/{agent_id}/bilibili/profiles/{config_id}` | `bilibili_router.bilibili_profile_save` | [agent/src/omni/adapters/http_routes/bilibili.py](../../../agent/src/omni/adapters/http_routes/bilibili.py#L74) |
| v2 | GET | `/api/v1/agents/{agent_id}/bilibili/publications` | `bilibili_router.bilibili_publications` | [agent/src/omni/adapters/http_routes/bilibili.py](../../../agent/src/omni/adapters/http_routes/bilibili.py#L109) |
| v2 | POST | `/api/v1/agents/{agent_id}/bilibili/publications` | `bilibili_router.bilibili_prepare` | [agent/src/omni/adapters/http_routes/bilibili.py](../../../agent/src/omni/adapters/http_routes/bilibili.py#L96) |
| v2 | GET | `/api/v1/agents/{agent_id}/bilibili/publications/{identity}` | `bilibili_router.bilibili_publication` | [agent/src/omni/adapters/http_routes/bilibili.py](../../../agent/src/omni/adapters/http_routes/bilibili.py#L118) |
| v2 | POST | `/api/v1/agents/{agent_id}/bilibili/read` | `bilibili_router.bilibili_read` | [agent/src/omni/adapters/http_routes/bilibili.py](../../../agent/src/omni/adapters/http_routes/bilibili.py#L89) |
| v2 | GET | `/api/v1/agents/{agent_id}/bilibili/risk/{config_id}` | `bilibili_router.bilibili_risk_state` | [agent/src/omni/adapters/http_routes/bilibili.py](../../../agent/src/omni/adapters/http_routes/bilibili.py#L81) |
| v2 | POST | `/api/v1/agents/{agent_id}/bilibili/risk/{config_id}/resume` | `bilibili_router.bilibili_risk_resume` | [agent/src/omni/adapters/http_routes/bilibili.py](../../../agent/src/omni/adapters/http_routes/bilibili.py#L85) |
| v2 | GET | `/api/v1/agents/{agent_id}/clarifications` | `create_app.clarifications` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L726) |
| v2 | POST | `/api/v1/agents/{agent_id}/clarifications/{clarification_id}/cancel` | `create_app.cancel_clarify` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L739) |
| v2 | POST | `/api/v1/agents/{agent_id}/clarifications/{clarification_id}/respond` | `create_app.respond_clarify` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L730) |
| v2 | GET | `/api/v1/agents/{agent_id}/configurations` | `configurations_router.configurations` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L49) |
| v2 | POST | `/api/v1/agents/{agent_id}/configurations` | `configurations_router.create_configuration` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L77) |
| v2 | POST | `/api/v1/agents/{agent_id}/configurations/preview` | `configurations_router.preview_configuration` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L96) |
| v2 | GET | `/api/v1/agents/{agent_id}/configurations/template` | `configurations_router.configuration_template` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L89) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/configurations/{config_id}` | `configurations_router.archive_configuration` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L118) |
| v2 | GET | `/api/v1/agents/{agent_id}/configurations/{config_id}` | `configurations_router.get_configuration` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L101) |
| v2 | PUT | `/api/v1/agents/{agent_id}/configurations/{config_id}` | `configurations_router.save_configuration` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L105) |
| v2 | GET | `/api/v1/agents/{agent_id}/configurations/{config_id}/diagnosis` | `configurations_router.configuration_diagnosis` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L135) |
| v2 | GET | `/api/v1/agents/{agent_id}/configurations/{config_id}/preset` | `configurations_router.apply_preset` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L139) |
| v2 | POST | `/api/v1/agents/{agent_id}/configurations/{config_id}/probe` | `configurations_router.configuration_probe` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L143) |
| v2 | GET | `/api/v1/agents/{agent_id}/configurations/{config_id}/revisions` | `configurations_router.configuration_history` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L125) |
| v2 | GET | `/api/v1/agents/{agent_id}/context/constants` | `create_app.context_constants` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L703) |
| v2 | GET | `/api/v1/agents/{agent_id}/debug` | `operations_router.debug_status` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L83) |
| v2 | POST | `/api/v1/agents/{agent_id}/debug` | `operations_router.debug_start` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L87) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/debug/{operation_id}` | `operations_router.debug_stop` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L92) |
| v2 | GET | `/api/v1/agents/{agent_id}/debug/{operation_id}/logs` | `operations_router.debug_logs` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L96) |
| v2 | GET | `/api/v1/agents/{agent_id}/deliveries` | `deliveries_router.deliveries` | [agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L53) |
| v2 | POST | `/api/v1/agents/{agent_id}/deliveries` | `deliveries_router.create_delivery` | [agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L64) |
| v2 | GET | `/api/v1/agents/{agent_id}/deliveries/capabilities` | `deliveries_router.delivery_capabilities` | [agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L49) |
| v2 | GET | `/api/v1/agents/{agent_id}/deliveries/channels` | `deliveries_router.delivery_channels` | [agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L40) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/deliveries/{delivery_id}` | `deliveries_router.archive_delivery` | [agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L110) |
| v2 | GET | `/api/v1/agents/{agent_id}/deliveries/{delivery_id}` | `deliveries_router.delivery` | [agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L77) |
| v2 | GET | `/api/v1/agents/{agent_id}/deliveries/{delivery_id}/attempts` | `deliveries_router.delivery_attempts` | [agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L81) |
| v2 | POST | `/api/v1/agents/{agent_id}/deliveries/{delivery_id}/cancel` | `deliveries_router.cancel_delivery` | [agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L99) |
| v2 | POST | `/api/v1/agents/{agent_id}/deliveries/{delivery_id}/decision` | `deliveries_router.decide_delivery` | [agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L92) |
| v2 | POST | `/api/v1/agents/{agent_id}/deliveries/{delivery_id}/review` | `deliveries_router.review_delivery` | [agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L103) |
| v2 | GET | `/api/v1/agents/{agent_id}/desktop` | `desktop_router.desktop_status` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L29) |
| v2 | POST | `/api/v1/agents/{agent_id}/desktop/actions` | `desktop_router.desktop_action` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L97) |
| v2 | POST | `/api/v1/agents/{agent_id}/desktop/capture` | `desktop_router.desktop_capture` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L63) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/desktop/control` | `desktop_router.desktop_control_close` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L93) |
| v2 | GET | `/api/v1/agents/{agent_id}/desktop/control` | `desktop_router.desktop_control_status` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L89) |
| v2 | POST | `/api/v1/agents/{agent_id}/desktop/control` | `desktop_router.desktop_control_open` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L85) |
| v2 | POST | `/api/v1/agents/{agent_id}/desktop/observe` | `desktop_router.desktop_observe` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L57) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/desktop/portal` | `desktop_router.desktop_portal_close` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L81) |
| v2 | GET | `/api/v1/agents/{agent_id}/desktop/portal` | `desktop_router.desktop_portal_status` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L73) |
| v2 | POST | `/api/v1/agents/{agent_id}/desktop/portal` | `desktop_router.desktop_portal_open` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L69) |
| v2 | POST | `/api/v1/agents/{agent_id}/desktop/portal/capture` | `desktop_router.desktop_portal_capture` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L77) |
| v2 | POST | `/api/v1/agents/{agent_id}/desktop/probe` | `desktop_router.desktop_probe` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L39) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/desktop/snapshot` | `desktop_router.desktop_forget` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L105) |
| v2 | POST | `/api/v1/agents/{agent_id}/desktop/targets` | `desktop_router.desktop_targets` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L45) |
| v2 | POST | `/api/v1/agents/{agent_id}/desktop/verify` | `desktop_router.desktop_verify` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L51) |
| v2 | POST | `/api/v1/agents/{agent_id}/desktop/view` | `desktop_router.desktop_begin` | [agent/src/omni/adapters/http_routes/desktop.py](../../../agent/src/omni/adapters/http_routes/desktop.py#L33) |
| v2 | GET | `/api/v1/agents/{agent_id}/documents` | `documents_router.source_documents` | [agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L37) |
| v2 | POST | `/api/v1/agents/{agent_id}/documents` | `documents_router.source_document_create` | [agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L46) |
| v2 | POST | `/api/v1/agents/{agent_id}/documents/import` | `documents_router.source_document_import` | [agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L57) |
| v2 | GET | `/api/v1/agents/{agent_id}/documents/search` | `documents_router.source_document_search` | [agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L68) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/documents/{document_id}` | `documents_router.source_document_erase` | [agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L94) |
| v2 | GET | `/api/v1/agents/{agent_id}/documents/{document_id}` | `documents_router.source_document_get` | [agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L77) |
| v2 | PUT | `/api/v1/agents/{agent_id}/documents/{document_id}` | `documents_router.source_document_save` | [agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L85) |
| v2 | GET | `/api/v1/agents/{agent_id}/documents/{document_id}/indexes` | `documents_router.source_document_indexes` | [agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L111) |
| v2 | POST | `/api/v1/agents/{agent_id}/documents/{document_id}/indexes` | `documents_router.source_document_rebuild` | [agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L123) |
| v2 | GET | `/api/v1/agents/{agent_id}/documents/{document_id}/revisions` | `documents_router.source_document_history` | [agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L101) |
| v2 | GET | `/api/v1/agents/{agent_id}/events/retention-preview` | `operations_router.event_retention_preview` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L29) |
| v2 | GET | `/api/v1/agents/{agent_id}/events/stream` | `create_app.event_stream` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L659) |
| v2 | GET | `/api/v1/agents/{agent_id}/extensions` | `extensions_router.extension_list` | [agent/src/omni/adapters/http_routes/extensions.py](../../../agent/src/omni/adapters/http_routes/extensions.py#L46) |
| v2 | POST | `/api/v1/agents/{agent_id}/extensions` | `extensions_router.extension_install` | [agent/src/omni/adapters/http_routes/extensions.py](../../../agent/src/omni/adapters/http_routes/extensions.py#L30) |
| v2 | POST | `/api/v1/agents/{agent_id}/extensions/market` | `extensions_router.extension_market` | [agent/src/omni/adapters/http_routes/extensions.py](../../../agent/src/omni/adapters/http_routes/extensions.py#L34) |
| v2 | GET | `/api/v1/agents/{agent_id}/extensions/{extension_id}` | `extensions_router.extension_detail` | [agent/src/omni/adapters/http_routes/extensions.py](../../../agent/src/omni/adapters/http_routes/extensions.py#L55) |
| v2 | GET | `/api/v1/agents/{agent_id}/extensions/{extension_id}/actions` | `extensions_router.extension_actions` | [agent/src/omni/adapters/http_routes/extensions.py](../../../agent/src/omni/adapters/http_routes/extensions.py#L59) |
| v2 | PUT | `/api/v1/agents/{agent_id}/extensions/{extension_id}/authorization` | `extensions_router.extension_authorization` | [agent/src/omni/adapters/http_routes/extensions.py](../../../agent/src/omni/adapters/http_routes/extensions.py#L69) |
| v2 | POST | `/api/v1/agents/{agent_id}/extensions/{extension_id}/disable` | `extensions_router.extension_disable` | [agent/src/omni/adapters/http_routes/extensions.py](../../../agent/src/omni/adapters/http_routes/extensions.py#L75) |
| v2 | POST | `/api/v1/agents/{agent_id}/images` | `vision_router.upload_image` | [agent/src/omni/adapters/http_routes/vision.py](../../../agent/src/omni/adapters/http_routes/vision.py#L26) |
| v2 | POST | `/api/v1/agents/{agent_id}/images/{object_id}/prepare` | `vision_router.prepare_image` | [agent/src/omni/adapters/http_routes/vision.py](../../../agent/src/omni/adapters/http_routes/vision.py#L46) |
| v2 | GET | `/api/v1/agents/{agent_id}/maintenance` | `maintenance_router.maintenance_list` | [agent/src/omni/adapters/http_routes/maintenance.py](../../../agent/src/omni/adapters/http_routes/maintenance.py#L22) |
| v2 | POST | `/api/v1/agents/{agent_id}/maintenance` | `maintenance_router.maintenance_submit` | [agent/src/omni/adapters/http_routes/maintenance.py](../../../agent/src/omni/adapters/http_routes/maintenance.py#L18) |
| v2 | GET | `/api/v1/agents/{agent_id}/maintenance/{job_id}` | `maintenance_router.maintenance_get` | [agent/src/omni/adapters/http_routes/maintenance.py](../../../agent/src/omni/adapters/http_routes/maintenance.py#L31) |
| v2 | POST | `/api/v1/agents/{agent_id}/maintenance/{job_id}/cancel` | `maintenance_router.maintenance_cancel` | [agent/src/omni/adapters/http_routes/maintenance.py](../../../agent/src/omni/adapters/http_routes/maintenance.py#L35) |
| v2 | GET | `/api/v1/agents/{agent_id}/mcp/health` | `mcp_router.mcp_health` | [agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L128) |
| v2 | GET | `/api/v1/agents/{agent_id}/mcp/servers` | `mcp_router.mcp_servers` | [agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L55) |
| v2 | GET | `/api/v1/agents/{agent_id}/mcp/servers/{config_id}` | `mcp_router.mcp_server` | [agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L59) |
| v2 | PUT | `/api/v1/agents/{agent_id}/mcp/servers/{config_id}/authorization` | `mcp_router.mcp_authorize` | [agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L86) |
| v2 | GET | `/api/v1/agents/{agent_id}/mcp/servers/{config_id}/calls` | `mcp_router.mcp_calls` | [agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L112) |
| v2 | GET | `/api/v1/agents/{agent_id}/mcp/servers/{config_id}/catalog` | `mcp_router.mcp_catalog` | [agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L76) |
| v2 | POST | `/api/v1/agents/{agent_id}/mcp/servers/{config_id}/connect` | `mcp_router.mcp_connect` | [agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L63) |
| v2 | POST | `/api/v1/agents/{agent_id}/mcp/servers/{config_id}/disconnect` | `mcp_router.mcp_disconnect` | [agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L68) |
| v2 | POST | `/api/v1/agents/{agent_id}/mcp/servers/{config_id}/oauth/start` | `mcp_router.mcp_oauth_start` | [agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L122) |
| v2 | POST | `/api/v1/agents/{agent_id}/mcp/servers/{config_id}/reset` | `mcp_router.mcp_reset` | [agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L72) |
| v2 | PUT | `/api/v1/agents/{agent_id}/mcp/servers/{config_id}/tools/{tool_id}/authorization` | `mcp_router.mcp_authorize_tool` | [agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L99) |
| v2 | GET | `/api/v1/agents/{agent_id}/media/frames/{frames_id}` | `media_router.media_frames` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L124) |
| v2 | GET | `/api/v1/agents/{agent_id}/media/jobs/{job_id}` | `media_router.media_job` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L102) |
| v2 | POST | `/api/v1/agents/{agent_id}/media/jobs/{job_id}/cancel` | `media_router.media_job_cancel` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L106) |
| v2 | GET | `/api/v1/agents/{agent_id}/media/processor` | `media_router.media_processor` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L128) |
| v2 | POST | `/api/v1/agents/{agent_id}/media/processor/probe` | `media_router.media_processor_probe` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L132) |
| v2 | GET | `/api/v1/agents/{agent_id}/media/sources` | `media_router.media_sources` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L53) |
| v2 | POST | `/api/v1/agents/{agent_id}/media/sources` | `media_router.media_source_register` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L48) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/media/sources/{source_id}` | `media_router.media_source_delete` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L71) |
| v2 | GET | `/api/v1/agents/{agent_id}/media/sources/{source_id}` | `media_router.media_source` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L67) |
| v2 | GET | `/api/v1/agents/{agent_id}/media/sources/{source_id}/jobs` | `media_router.media_jobs` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L92) |
| v2 | POST | `/api/v1/agents/{agent_id}/media/sources/{source_id}/jobs` | `media_router.media_job_submit` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L76) |
| v2 | GET | `/api/v1/agents/{agent_id}/media/transcripts/{transcript_id}` | `media_router.media_transcript` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L110) |
| v2 | GET | `/api/v1/agents/{agent_id}/media/transcripts/{transcript_id}/window` | `media_router.media_transcript_window` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L114) |
| v2 | POST | `/api/v1/agents/{agent_id}/media/uploads` | `media_router.media_upload_source` | [agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L62) |
| v2 | GET | `/api/v1/agents/{agent_id}/memory` | `memory_router.memories` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L28) |
| v2 | POST | `/api/v1/agents/{agent_id}/memory` | `memory_router.remember` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L119) |
| v2 | GET | `/api/v1/agents/{agent_id}/memory/decay` | `memory_router.memory_decay_state` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L40) |
| v2 | POST | `/api/v1/agents/{agent_id}/memory/decay` | `memory_router.memory_decay` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L44) |
| v2 | POST | `/api/v1/agents/{agent_id}/memory/index` | `memory_router.memory_index` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L63) |
| v2 | GET | `/api/v1/agents/{agent_id}/memory/organizations` | `memory_router.memory_organizations` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L89) |
| v2 | POST | `/api/v1/agents/{agent_id}/memory/organizations` | `memory_router.memory_organization_create` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L83) |
| v2 | POST | `/api/v1/agents/{agent_id}/memory/organizations/candidates/{candidate_id}/decision` | `memory_router.memory_organization_decision` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L113) |
| v2 | GET | `/api/v1/agents/{agent_id}/memory/organizations/{batch_id}` | `memory_router.memory_organization_detail` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L98) |
| v2 | POST | `/api/v1/agents/{agent_id}/memory/organizations/{batch_id}/cancel` | `memory_router.memory_organization_cancel` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L102) |
| v2 | GET | `/api/v1/agents/{agent_id}/memory/reviews` | `learning_router.background_reviews` | [agent/src/omni/adapters/http_routes/learning.py](../../../agent/src/omni/adapters/http_routes/learning.py#L50) |
| v2 | POST | `/api/v1/agents/{agent_id}/memory/reviews` | `learning_router.background_review_create` | [agent/src/omni/adapters/http_routes/learning.py](../../../agent/src/omni/adapters/http_routes/learning.py#L44) |
| v2 | POST | `/api/v1/agents/{agent_id}/memory/reviews/candidates/{candidate_id}/decision` | `learning_router.background_review_decision` | [agent/src/omni/adapters/http_routes/learning.py](../../../agent/src/omni/adapters/http_routes/learning.py#L72) |
| v2 | GET | `/api/v1/agents/{agent_id}/memory/reviews/{batch_id}` | `learning_router.background_review_detail` | [agent/src/omni/adapters/http_routes/learning.py](../../../agent/src/omni/adapters/http_routes/learning.py#L59) |
| v2 | POST | `/api/v1/agents/{agent_id}/memory/reviews/{batch_id}/cancel` | `learning_router.background_review_cancel` | [agent/src/omni/adapters/http_routes/learning.py](../../../agent/src/omni/adapters/http_routes/learning.py#L63) |
| v2 | POST | `/api/v1/agents/{agent_id}/memory/semantic-links/{link_id}/decision` | `memory_router.semantic_decide` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L77) |
| v2 | GET | `/api/v1/agents/{agent_id}/memory/semantics` | `memory_router.semantic_batches` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L57) |
| v2 | POST | `/api/v1/agents/{agent_id}/memory/semantics` | `memory_router.semantic_propose` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L69) |
| v2 | GET | `/api/v1/agents/{agent_id}/memory/semantics/{batch_id}` | `memory_router.semantic_detail` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L73) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/memory/{memory_id}` | `memory_router.forget` | [agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L130) |
| v2 | GET | `/api/v1/agents/{agent_id}/memory/{memory_id}/revisions` | `people_router.memory_revisions` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L135) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/messages/{message_id}` | `create_app.erase_message` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L546) |
| v2 | GET | `/api/v1/agents/{agent_id}/objects` | `create_app.list_objects` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L575) |
| v2 | POST | `/api/v1/agents/{agent_id}/objects` | `create_app.upload_object` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L555) |
| v2 | GET | `/api/v1/agents/{agent_id}/objects/{object_id}` | `create_app.describe_object` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L584) |
| v2 | GET | `/api/v1/agents/{agent_id}/objects/{object_id}/content` | `create_app.download_object` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L588) |
| v2 | PUT | `/api/v1/agents/{agent_id}/objects/{object_id}/pin` | `create_app.pin_object` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L597) |
| v2 | GET | `/api/v1/agents/{agent_id}/omniweave/main-session` | `omniweave_router.main_session` | [agent/src/omni/adapters/http_routes/omniweave.py](../../../agent/src/omni/adapters/http_routes/omniweave.py#L20) |
| v2 | PUT | `/api/v1/agents/{agent_id}/omniweave/main-session` | `omniweave_router.select_main_session` | [agent/src/omni/adapters/http_routes/omniweave.py](../../../agent/src/omni/adapters/http_routes/omniweave.py#L24) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/omniweave/sessions/{session_id}/artifacts/{artifact_id}/shares/{reader_key}` | `omniweave_router.revoke_artifact` | [agent/src/omni/adapters/http_routes/omniweave.py](../../../agent/src/omni/adapters/http_routes/omniweave.py#L44) |
| v2 | GET | `/api/v1/agents/{agent_id}/omniweave/sessions/{session_id}/ticket-options` | `omniweave_router.ticket_options` | [agent/src/omni/adapters/http_routes/omniweave.py](../../../agent/src/omni/adapters/http_routes/omniweave.py#L40) |
| v2 | GET | `/api/v1/agents/{agent_id}/omniweave/sessions/{session_id}/tickets` | `omniweave_router.ticket` | [agent/src/omni/adapters/http_routes/omniweave.py](../../../agent/src/omni/adapters/http_routes/omniweave.py#L32) |
| v2 | GET | `/api/v1/agents/{agent_id}/people` | `people_router.list_people` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L48) |
| v2 | POST | `/api/v1/agents/{agent_id}/people` | `people_router.create_person` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L57) |
| v2 | GET | `/api/v1/agents/{agent_id}/people/owner` | `people_router.owner_person` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L44) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/people/{person_id}` | `people_router.erase_person` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L69) |
| v2 | GET | `/api/v1/agents/{agent_id}/people/{person_id}` | `people_router.get_person` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L61) |
| v2 | PUT | `/api/v1/agents/{agent_id}/people/{person_id}` | `people_router.rename_person` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L65) |
| v2 | GET | `/api/v1/agents/{agent_id}/people/{person_id}/accounts` | `people_router.person_accounts` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L74) |
| v2 | POST | `/api/v1/agents/{agent_id}/people/{person_id}/accounts` | `people_router.bind_account` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L84) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/people/{person_id}/accounts/{account_id}` | `people_router.unbind_account` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L88) |
| v2 | GET | `/api/v1/agents/{agent_id}/people/{person_id}/documents/{kind}` | `people_router.personal_document` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L95) |
| v2 | PUT | `/api/v1/agents/{agent_id}/people/{person_id}/documents/{kind}` | `people_router.save_personal_document` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L101) |
| v2 | POST | `/api/v1/agents/{agent_id}/people/{person_id}/documents/{kind}/import` | `people_router.import_personal_document` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L118) |
| v2 | GET | `/api/v1/agents/{agent_id}/persona-growth` | `people_router.persona_growth` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L145) |
| v2 | POST | `/api/v1/agents/{agent_id}/persona-growth/rebuild` | `people_router.persona_growth_rebuild` | [agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L149) |
| v2 | GET | `/api/v1/agents/{agent_id}/qq/bindings` | `qq_router.qq_bindings` | [agent/src/omni/adapters/http_routes/qq.py](../../../agent/src/omni/adapters/http_routes/qq.py#L36) |
| v2 | POST | `/api/v1/agents/{agent_id}/qq/bindings` | `qq_router.qq_binding_create` | [agent/src/omni/adapters/http_routes/qq.py](../../../agent/src/omni/adapters/http_routes/qq.py#L49) |
| v2 | PUT | `/api/v1/agents/{agent_id}/qq/bindings/{binding_id}` | `qq_router.qq_binding_edit` | [agent/src/omni/adapters/http_routes/qq.py](../../../agent/src/omni/adapters/http_routes/qq.py#L57) |
| v2 | GET | `/api/v1/agents/{agent_id}/qq/status` | `qq_router.qq_status` | [agent/src/omni/adapters/http_routes/qq.py](../../../agent/src/omni/adapters/http_routes/qq.py#L45) |
| v2 | GET | `/api/v1/agents/{agent_id}/reflections` | `learning_router.reflections` | [agent/src/omni/adapters/http_routes/learning.py](../../../agent/src/omni/adapters/http_routes/learning.py#L18) |
| v2 | POST | `/api/v1/agents/{agent_id}/reflections/candidates/{candidate_id}/decision` | `learning_router.reflection_decision` | [agent/src/omni/adapters/http_routes/learning.py](../../../agent/src/omni/adapters/http_routes/learning.py#L38) |
| v2 | GET | `/api/v1/agents/{agent_id}/reflections/{journal_id}` | `learning_router.reflection_detail` | [agent/src/omni/adapters/http_routes/learning.py](../../../agent/src/omni/adapters/http_routes/learning.py#L28) |
| v2 | PUT | `/api/v1/agents/{agent_id}/reflections/{journal_id}` | `learning_router.reflection_edit` | [agent/src/omni/adapters/http_routes/learning.py](../../../agent/src/omni/adapters/http_routes/learning.py#L32) |
| v2 | GET | `/api/v1/agents/{agent_id}/routing` | `configurations_router.route_policy` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L65) |
| v2 | PUT | `/api/v1/agents/{agent_id}/routing` | `configurations_router.save_route_policy` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L69) |
| v2 | GET | `/api/v1/agents/{agent_id}/runs` | `create_app.runs` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L647) |
| v2 | GET | `/api/v1/agents/{agent_id}/runs/{run_id}` | `create_app.run` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L651) |
| v2 | POST | `/api/v1/agents/{agent_id}/runs/{run_id}/cancel` | `create_app.cancel` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L698) |
| v2 | GET | `/api/v1/agents/{agent_id}/runs/{run_id}/configuration` | `configurations_router.run_configuration` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L204) |
| v2 | GET | `/api/v1/agents/{agent_id}/runs/{run_id}/context` | `create_app.run_context` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L542) |
| v2 | GET | `/api/v1/agents/{agent_id}/runs/{run_id}/delegations` | `create_app.run_delegations` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L643) |
| v2 | GET | `/api/v1/agents/{agent_id}/runs/{run_id}/events` | `create_app.events` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L685) |
| v2 | GET | `/api/v1/agents/{agent_id}/runs/{run_id}/images` | `vision_router.run_images` | [agent/src/omni/adapters/http_routes/vision.py](../../../agent/src/omni/adapters/http_routes/vision.py#L50) |
| v2 | GET | `/api/v1/agents/{agent_id}/runs/{run_id}/live-output` | `create_app.model_live_output` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L655) |
| v2 | GET | `/api/v1/agents/{agent_id}/runs/{run_id}/retention` | `operations_router.run_retention` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L59) |
| v2 | PUT | `/api/v1/agents/{agent_id}/runs/{run_id}/retention-pin` | `operations_router.run_retention_pin` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L63) |
| v2 | GET | `/api/v1/agents/{agent_id}/runs/{run_id}/routing` | `configurations_router.route_report` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L73) |
| v2 | GET | `/api/v1/agents/{agent_id}/runs/{run_id}/skills` | `skills_router.skill_run_report` | [agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L117) |
| v2 | GET | `/api/v1/agents/{agent_id}/schedules` | `schedules_router.schedules` | [agent/src/omni/adapters/http_routes/schedules.py](../../../agent/src/omni/adapters/http_routes/schedules.py#L29) |
| v2 | POST | `/api/v1/agents/{agent_id}/schedules` | `schedules_router.create_schedule` | [agent/src/omni/adapters/http_routes/schedules.py](../../../agent/src/omni/adapters/http_routes/schedules.py#L42) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/schedules/{schedule_id}` | `schedules_router.archive_schedule` | [agent/src/omni/adapters/http_routes/schedules.py](../../../agent/src/omni/adapters/http_routes/schedules.py#L60) |
| v2 | GET | `/api/v1/agents/{agent_id}/schedules/{schedule_id}` | `schedules_router.schedule` | [agent/src/omni/adapters/http_routes/schedules.py](../../../agent/src/omni/adapters/http_routes/schedules.py#L49) |
| v2 | PUT | `/api/v1/agents/{agent_id}/schedules/{schedule_id}` | `schedules_router.save_schedule` | [agent/src/omni/adapters/http_routes/schedules.py](../../../agent/src/omni/adapters/http_routes/schedules.py#L53) |
| v2 | GET | `/api/v1/agents/{agent_id}/schedules/{schedule_id}/history` | `schedules_router.schedule_history` | [agent/src/omni/adapters/http_routes/schedules.py](../../../agent/src/omni/adapters/http_routes/schedules.py#L67) |
| v2 | GET | `/api/v1/agents/{agent_id}/schedules/{schedule_id}/occurrences` | `schedules_router.schedule_occurrences` | [agent/src/omni/adapters/http_routes/schedules.py](../../../agent/src/omni/adapters/http_routes/schedules.py#L78) |
| v2 | POST | `/api/v1/agents/{agent_id}/schedules/{schedule_id}/occurrences/{occurrence_id}/cancel` | `schedules_router.schedule_cancel` | [agent/src/omni/adapters/http_routes/schedules.py](../../../agent/src/omni/adapters/http_routes/schedules.py#L97) |
| v2 | POST | `/api/v1/agents/{agent_id}/schedules/{schedule_id}/review` | `schedules_router.schedule_review` | [agent/src/omni/adapters/http_routes/schedules.py](../../../agent/src/omni/adapters/http_routes/schedules.py#L89) |
| v2 | GET | `/api/v1/agents/{agent_id}/sessions` | `create_app.sessions` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L631) |
| v2 | GET | `/api/v1/agents/{agent_id}/sessions/{session_id}/captures` | `create_app.session_captures` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L748) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/sessions/{session_id}/captures/{capture_id}` | `create_app.release_capture` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L760) |
| v2 | GET | `/api/v1/agents/{agent_id}/sessions/{session_id}/context-overview` | `create_app.session_context_overview` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L712) |
| v2 | GET | `/api/v1/agents/{agent_id}/sessions/{session_id}/messages` | `create_app.messages` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L635) |
| v2 | GET | `/api/v1/agents/{agent_id}/sessions/{session_id}/todos` | `create_app.session_todos` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L639) |
| v2 | GET | `/api/v1/agents/{agent_id}/settings` | `configurations_router.settings` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L208) |
| v2 | PUT | `/api/v1/agents/{agent_id}/settings` | `configurations_router.save_settings` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L212) |
| v2 | GET | `/api/v1/agents/{agent_id}/settings/revisions` | `configurations_router.settings_history` | [agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L195) |
| v2 | GET | `/api/v1/agents/{agent_id}/skills` | `skills_router.skill_list` | [agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L51) |
| v2 | POST | `/api/v1/agents/{agent_id}/skills` | `skills_router.skill_save` | [agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L57) |
| v2 | GET | `/api/v1/agents/{agent_id}/skills/available` | `skills_router.skill_available` | [agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L73) |
| v2 | POST | `/api/v1/agents/{agent_id}/skills/import` | `skills_router.skill_import` | [agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L63) |
| v2 | GET | `/api/v1/agents/{agent_id}/skills/{skill_id}` | `skills_router.skill_detail` | [agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L85) |
| v2 | POST | `/api/v1/agents/{agent_id}/skills/{skill_id}/export` | `skills_router.skill_export` | [agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L77) |
| v2 | GET | `/api/v1/agents/{agent_id}/skills/{skill_id}/history` | `skills_router.skill_versions` | [agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L97) |
| v2 | POST | `/api/v1/agents/{agent_id}/skills/{skill_id}/rollback` | `skills_router.skill_rollback` | [agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L113) |
| v2 | PUT | `/api/v1/agents/{agent_id}/skills/{skill_id}/state` | `skills_router.skill_change_state` | [agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L107) |
| v2 | GET | `/api/v1/agents/{agent_id}/summaries/{summary_id}` | `create_app.summary` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L551) |
| v2 | GET | `/api/v1/agents/{agent_id}/terminal-operations/{operation_id}` | `terminals_router.terminal_lookup` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L68) |
| v2 | GET | `/api/v1/agents/{agent_id}/terminal-reviews` | `terminals_router.terminal_review_list` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L17) |
| v2 | GET | `/api/v1/agents/{agent_id}/terminal-reviews/{operation_id}` | `terminals_router.terminal_review_detail` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L23) |
| v2 | POST | `/api/v1/agents/{agent_id}/terminal-reviews/{operation_id}` | `terminals_router.terminal_review_record` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L37) |
| v2 | GET | `/api/v1/agents/{agent_id}/terminal-reviews/{operation_id}/history` | `terminals_router.terminal_review_history` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L27) |
| v2 | DELETE | `/api/v1/agents/{agent_id}/terminal-reviews/{operation_id}/history/{review_id}/evidence` | `terminals_router.terminal_review_erase_evidence` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L43) |
| v2 | GET | `/api/v1/agents/{agent_id}/terminals` | `terminals_router.terminal_list` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L55) |
| v2 | POST | `/api/v1/agents/{agent_id}/terminals` | `terminals_router.terminal_open` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L64) |
| v2 | GET | `/api/v1/agents/{agent_id}/terminals/options` | `terminals_router.terminal_options` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L51) |
| v2 | GET | `/api/v1/agents/{agent_id}/terminals/{terminal_id}` | `terminals_router.terminal_detail` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L72) |
| v2 | POST | `/api/v1/agents/{agent_id}/terminals/{terminal_id}/attach` | `terminals_router.terminal_attach` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L80) |
| v2 | POST | `/api/v1/agents/{agent_id}/terminals/{terminal_id}/close` | `terminals_router.terminal_close` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L118) |
| v2 | POST | `/api/v1/agents/{agent_id}/terminals/{terminal_id}/detach` | `terminals_router.terminal_detach` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L86) |
| v2 | POST | `/api/v1/agents/{agent_id}/terminals/{terminal_id}/input` | `terminals_router.terminal_input` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L106) |
| v2 | GET | `/api/v1/agents/{agent_id}/terminals/{terminal_id}/operations/{operation_id}` | `terminals_router.terminal_receipt` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L76) |
| v2 | GET | `/api/v1/agents/{agent_id}/terminals/{terminal_id}/output` | `terminals_router.terminal_output` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L93) |
| v2 | POST | `/api/v1/agents/{agent_id}/terminals/{terminal_id}/resize` | `terminals_router.terminal_resize` | [agent/src/omni/adapters/http_routes/terminals.py](../../../agent/src/omni/adapters/http_routes/terminals.py#L112) |
| v2 | POST | `/api/v1/agents/{agent_id}/turns` | `create_app.turn` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L601) |
| v2 | GET | `/api/v1/capabilities` | `create_app.capabilities` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L478) |
| v2 | DELETE | `/api/v1/credentials` | `credentials_router.delete_credential` | [agent/src/omni/adapters/http_routes/credentials.py](../../../agent/src/omni/adapters/http_routes/credentials.py#L89) |
| v2 | GET | `/api/v1/credentials` | `credentials_router.credential_list` | [agent/src/omni/adapters/http_routes/credentials.py](../../../agent/src/omni/adapters/http_routes/credentials.py#L35) |
| v2 | PUT | `/api/v1/credentials` | `credentials_router.put_credential` | [agent/src/omni/adapters/http_routes/credentials.py](../../../agent/src/omni/adapters/http_routes/credentials.py#L81) |
| v2 | POST | `/api/v1/credentials/register` | `credentials_router.credential_register` | [agent/src/omni/adapters/http_routes/credentials.py](../../../agent/src/omni/adapters/http_routes/credentials.py#L41) |
| v2 | GET | `/api/v1/credentials/{identity}` | `credentials_router.credential_get` | [agent/src/omni/adapters/http_routes/credentials.py](../../../agent/src/omni/adapters/http_routes/credentials.py#L46) |
| v2 | GET | `/api/v1/credentials/{identity}/operations` | `credentials_router.credential_operations` | [agent/src/omni/adapters/http_routes/credentials.py](../../../agent/src/omni/adapters/http_routes/credentials.py#L50) |
| v2 | POST | `/api/v1/credentials/{identity}/review` | `credentials_router.credential_review` | [agent/src/omni/adapters/http_routes/credentials.py](../../../agent/src/omni/adapters/http_routes/credentials.py#L68) |
| v2 | GET | `/api/v1/credentials/{identity}/reviews` | `credentials_router.credential_reviews` | [agent/src/omni/adapters/http_routes/credentials.py](../../../agent/src/omni/adapters/http_routes/credentials.py#L59) |
| v2 | GET | `/api/v1/owner-token/local` | `create_app.owner_token_local` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L484) |
| v2 | POST | `/api/v1/owner-token/rotate` | `create_app.owner_token_rotate` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L517) |
| v2 | GET | `/api/v1/owner-token/sessions` | `create_app.owner_token_sessions` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L510) |
| v2 | POST | `/api/v1/service/drain` | `create_app.drain` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L430) |
| v2 | GET | `/api/v1/system/audit` | `operations_router.instance_audit` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L131) |
| v2 | GET | `/api/v1/system/diagnostics` | `operations_router.diagnostic_bundle` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L108) |
| v2 | GET | `/api/v1/system/logs` | `operations_router.logs` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L117) |
| v2 | GET | `/api/v1/system/metrics` | `operations_router.metrics` | [agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L79) |
| v2 | GET | `/api/v1/version` | `create_app.version` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L474) |
| v2 | GET | `/assets/{path:path}` | `create_app.resource` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L772) |
| v2 | GET | `/health/identity` | `create_app.identity_proof` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L419) |
| v2 | GET | `/health/live` | `create_app.live` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L410) |
| v2 | GET | `/health/ready` | `create_app.ready` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L414) |
| v2 | GET | `/oauth/callback` | `create_app.oauth_callback` | [agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L443) |
| workbench | GET | `/api/android/devices` | `create_android_router.devices` | [agent/src/omni/channels/web_api/android.py](../../../agent/src/omni/channels/web_api/android.py#L42) |
| workbench | POST | `/api/android/devices/{serial}/actions` | `create_android_router.action` | [agent/src/omni/channels/web_api/android.py](../../../agent/src/omni/channels/web_api/android.py#L79) |
| workbench | GET | `/api/android/devices/{serial}/apps` | `create_android_router.apps` | [agent/src/omni/channels/web_api/android.py](../../../agent/src/omni/channels/web_api/android.py#L72) |
| workbench | GET | `/api/android/devices/{serial}/observe` | `create_android_router.observe` | [agent/src/omni/channels/web_api/android.py](../../../agent/src/omni/channels/web_api/android.py#L49) |
| workbench | GET | `/api/android/devices/{serial}/snapshot` | `create_android_router.snapshot` | [agent/src/omni/channels/web_api/android.py](../../../agent/src/omni/channels/web_api/android.py#L56) |
| workbench | GET | `/api/android/devices/{serial}/stream` | `create_android_router.stream` | [agent/src/omni/channels/web_api/android.py](../../../agent/src/omni/channels/web_api/android.py#L94) |
| workbench | GET | `/api/android/devices/{serial}/stream-state` | `create_android_router.stream_state` | [agent/src/omni/channels/web_api/android.py](../../../agent/src/omni/channels/web_api/android.py#L100) |
| workbench | POST | `/api/android/element` | `create_devices_router.android_element` | [agent/src/omni/channels/web_api/devices.py](../../../agent/src/omni/channels/web_api/devices.py#L113) |
| workbench | POST | `/api/android/key` | `create_devices_router.android_key` | [agent/src/omni/channels/web_api/devices.py](../../../agent/src/omni/channels/web_api/devices.py#L123) |
| workbench | GET | `/api/android/snapshot` | `create_devices_router.android_snapshot` | [agent/src/omni/channels/web_api/devices.py](../../../agent/src/omni/channels/web_api/devices.py#L66) |
| workbench | GET | `/api/android/state` | `create_devices_router.android_state` | [agent/src/omni/channels/web_api/devices.py](../../../agent/src/omni/channels/web_api/devices.py#L38) |
| workbench | GET | `/api/android/stream` | `create_devices_router.android_stream` | [agent/src/omni/channels/web_api/devices.py](../../../agent/src/omni/channels/web_api/devices.py#L132) |
| workbench | POST | `/api/android/tap` | `create_devices_router.android_tap` | [agent/src/omni/channels/web_api/devices.py](../../../agent/src/omni/channels/web_api/devices.py#L93) |
| workbench | GET | `/api/android/tree` | `create_devices_router.android_tree` | [agent/src/omni/channels/web_api/devices.py](../../../agent/src/omni/channels/web_api/devices.py#L47) |
| workbench | GET | `/api/approvals` | `create_approvals_router.get_approvals` | [agent/src/omni/channels/web_api/approvals.py](../../../agent/src/omni/channels/web_api/approvals.py#L19) |
| workbench | POST | `/api/approvals/{approval_id}/resolve` | `create_approvals_router.resolve_approval` | [agent/src/omni/channels/web_api/approvals.py](../../../agent/src/omni/channels/web_api/approvals.py#L27) |
| workbench | GET | `/api/artifacts` | `create_artifacts_router.list_artifacts` | [agent/src/omni/channels/web_api/artifacts.py](../../../agent/src/omni/channels/web_api/artifacts.py#L32) |
| workbench | DELETE | `/api/artifacts/{artifact_id}` | `create_artifacts_router.delete_artifact` | [agent/src/omni/channels/web_api/artifacts.py](../../../agent/src/omni/channels/web_api/artifacts.py#L72) |
| workbench | GET | `/api/artifacts/{artifact_id}/image` | `create_artifacts_router.artifact_image` | [agent/src/omni/channels/web_api/artifacts.py](../../../agent/src/omni/channels/web_api/artifacts.py#L59) |
| workbench | POST | `/api/auth/logout` | `create_settings_router.auth_logout` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L92) |
| workbench | POST | `/api/auth/pair` | `create_settings_router.auth_pair` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L81) |
| workbench | GET | `/api/auth/status` | `create_settings_router.auth_status` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L70) |
| workbench | GET | `/api/capabilities` | `create_capabilities_router.capabilities` | [agent/src/omni/channels/web_api/capabilities.py](../../../agent/src/omni/channels/web_api/capabilities.py#L13) |
| workbench | POST | `/api/chat` | `create_conversation_router.chat` | [agent/src/omni/channels/web_api/conversation.py](../../../agent/src/omni/channels/web_api/conversation.py#L45) |
| workbench | POST | `/api/chat/stream` | `create_conversation_router.chat_stream` | [agent/src/omni/channels/web_api/conversation.py](../../../agent/src/omni/channels/web_api/conversation.py#L80) |
| workbench | GET | `/api/command-jobs` | `create_command_jobs_router.list_jobs` | [agent/src/omni/channels/web_api/command_jobs.py](../../../agent/src/omni/channels/web_api/command_jobs.py#L10) |
| workbench | GET | `/api/command-jobs/{identifier}` | `create_command_jobs_router.read_job` | [agent/src/omni/channels/web_api/command_jobs.py](../../../agent/src/omni/channels/web_api/command_jobs.py#L14) |
| workbench | POST | `/api/command-jobs/{identifier}/cancel` | `create_command_jobs_router.cancel_job` | [agent/src/omni/channels/web_api/command_jobs.py](../../../agent/src/omni/channels/web_api/command_jobs.py#L23) |
| workbench | GET | `/api/context` | `create_context_router.overview` | [agent/src/omni/channels/web_api/context.py](../../../agent/src/omni/channels/web_api/context.py#L65) |
| workbench | GET | `/api/context/settings` | `create_context_router.get_settings` | [agent/src/omni/channels/web_api/context.py](../../../agent/src/omni/channels/web_api/context.py#L81) |
| workbench | PUT | `/api/context/settings` | `create_context_router.update` | [agent/src/omni/channels/web_api/context.py](../../../agent/src/omni/channels/web_api/context.py#L88) |
| workbench | GET | `/api/desktop/observe/{window}` | `create_devices_router.desktop_observe` | [agent/src/omni/channels/web_api/devices.py](../../../agent/src/omni/channels/web_api/devices.py#L29) |
| workbench | GET | `/api/desktop/windows` | `create_devices_router.desktop_windows` | [agent/src/omni/channels/web_api/devices.py](../../../agent/src/omni/channels/web_api/devices.py#L20) |
| workbench | GET | `/api/facts` | `create_memory_router.get_facts` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L42) |
| workbench | POST | `/api/facts` | `create_memory_router.create_fact` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L55) |
| workbench | GET | `/api/facts/detail` | `create_memory_router.get_facts_detail` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L66) |
| workbench | GET | `/api/facts/rows` | `create_memory_router.get_fact_rows` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L46) |
| workbench | DELETE | `/api/facts/{fact_id}` | `create_memory_router.delete_fact` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L71) |
| workbench | GET | `/api/llm/usage` | `create_runs_router.llm_usage` | [agent/src/omni/channels/web_api/runs.py](../../../agent/src/omni/channels/web_api/runs.py#L68) |
| workbench | GET | `/api/market/installed` | `create_market_router.market_installed` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L230) |
| workbench | GET | `/api/market/personas` | `create_market_router.market_personas` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L174) |
| workbench | GET | `/api/market/personas/{persona_id}` | `create_market_router.market_persona_detail` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L185) |
| workbench | GET | `/api/market/plugins` | `create_market_router.market_plugins` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L197) |
| workbench | DELETE | `/api/market/plugins/{plugin_id}` | `create_market_router.market_plugin_remove` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L292) |
| workbench | GET | `/api/market/plugins/{plugin_id}` | `create_market_router.market_plugin_detail` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L218) |
| workbench | POST | `/api/market/plugins/{plugin_id}/disable` | `create_market_router.market_plugin_disable` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L285) |
| workbench | POST | `/api/market/plugins/{plugin_id}/enable` | `create_market_router.market_plugin_enable` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L278) |
| workbench | POST | `/api/market/plugins/{plugin_id}/install` | `create_market_router.market_plugin_install` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L234) |
| workbench | POST | `/api/market/quarantine/{plugin_id}/activate` | `create_market_router.market_plugin_activate` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L258) |
| workbench | POST | `/api/market/quarantine/{plugin_id}/discard` | `create_market_router.market_plugin_discard` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L271) |
| workbench | PUT | `/api/market/source` | `create_market_router.market_source_save` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L164) |
| workbench | GET | `/api/market/status` | `create_market_router.market_status` | [agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L133) |
| workbench | POST | `/api/mcp/oauth-callback` | `create_mcp_router.oauth_callback` | [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py#L156) |
| workbench | GET | `/api/mcp/overview` | `create_mcp_router.mcp_overview` | [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py#L66) |
| workbench | POST | `/api/mcp/{server}/authorize` | `create_mcp_router.mcp_authorize` | [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py#L140) |
| workbench | GET | `/api/mcp/{server}/catalog` | `create_mcp_router.mcp_catalog` | [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py#L49) |
| workbench | POST | `/api/mcp/{server}/confirm-schema` | `create_mcp_router.mcp_confirm_schema` | [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py#L112) |
| workbench | POST | `/api/mcp/{server}/connect` | `create_mcp_router.mcp_connect` | [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py#L34) |
| workbench | POST | `/api/mcp/{server}/disconnect` | `create_mcp_router.mcp_disconnect` | [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py#L42) |
| workbench | GET | `/api/mcp/{server}/health` | `create_mcp_router.mcp_health` | [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py#L20) |
| workbench | GET | `/api/mcp/{server}/logs` | `create_mcp_router.mcp_logs` | [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py#L27) |
| workbench | POST | `/api/mcp/{server}/oauth-callback` | `create_mcp_router.mcp_oauth_callback` | [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py#L147) |
| workbench | POST | `/api/mcp/{server}/reset` | `create_mcp_router.mcp_reset` | [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py#L128) |
| workbench | GET | `/api/mcp/{server}/tools` | `create_mcp_router.mcp_tools` | [agent/src/omni/channels/web_api/mcp.py](../../../agent/src/omni/channels/web_api/mcp.py#L85) |
| workbench | GET | `/api/memory-system/dialogue/days` | `create_memory_system_router.dialogue_days` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L109) |
| workbench | GET | `/api/memory-system/dialogue/entries` | `create_memory_system_router.dialogue_entries` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L119) |
| workbench | GET | `/api/memory-system/dialogue/search` | `create_memory_system_router.dialogue_search` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L129) |
| workbench | GET | `/api/memory-system/dialogue/sessions` | `create_memory_system_router.dialogue_sessions` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L113) |
| workbench | GET | `/api/memory-system/notes` | `create_memory_system_router.notes` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L168) |
| workbench | GET | `/api/memory-system/notes/{kind}/{period}` | `create_memory_system_router.note_detail` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L179) |
| workbench | POST | `/api/memory-system/notes/{kind}/{period}/retry` | `create_memory_system_router.note_retry` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L188) |
| workbench | GET | `/api/memory-system/overview` | `create_memory_system_router.overview` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L41) |
| workbench | GET | `/api/memory-system/people` | `create_memory_system_router.people_list` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L206) |
| workbench | POST | `/api/memory-system/people` | `create_memory_system_router.people_create` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L210) |
| workbench | DELETE | `/api/memory-system/people/{person_id}` | `create_memory_system_router.person_delete` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L239) |
| workbench | GET | `/api/memory-system/people/{person_id}` | `create_memory_system_router.person_detail` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L220) |
| workbench | PUT | `/api/memory-system/people/{person_id}` | `create_memory_system_router.person_update` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L228) |
| workbench | POST | `/api/memory-system/people/{person_id}/accounts` | `create_memory_system_router.account_link` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L245) |
| workbench | DELETE | `/api/memory-system/people/{person_id}/accounts/{account_id}` | `create_memory_system_router.account_unlink` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L253) |
| workbench | POST | `/api/memory-system/people/{person_id}/entries` | `create_memory_system_router.entry_add` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L259) |
| workbench | DELETE | `/api/memory-system/people/{person_id}/entries/{entry_id}` | `create_memory_system_router.entry_delete` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L282) |
| workbench | PUT | `/api/memory-system/people/{person_id}/entries/{entry_id}` | `create_memory_system_router.entry_edit` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L273) |
| workbench | GET | `/api/memory-system/runs` | `create_memory_system_router.journal_runs` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L200) |
| workbench | GET | `/api/memory-system/session-log/days` | `create_memory_system_router.session_log_days` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L137) |
| workbench | GET | `/api/memory-system/session-log/entries` | `create_memory_system_router.session_log_entries` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L147) |
| workbench | GET | `/api/memory-system/session-log/search` | `create_memory_system_router.session_log_search` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L160) |
| workbench | GET | `/api/memory-system/session-log/sessions` | `create_memory_system_router.session_log_sessions` | [agent/src/omni/channels/web_api/memory_system.py](../../../agent/src/omni/channels/web_api/memory_system.py#L141) |
| workbench | GET | `/api/memory/retrieval` | `create_memory_router.retrieval_status` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L168) |
| workbench | GET | `/api/memory/status` | `create_memory_router.memory_status` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L78) |
| workbench | GET | `/api/persona/presets` | `create_memory_router.persona_presets` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L22) |
| workbench | PUT | `/api/persona/presets` | `create_memory_router.persona_presets_save` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L29) |
| workbench | GET | `/api/profile` | `create_memory_router.get_profile` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L98) |
| workbench | POST | `/api/profile/line` | `create_memory_router.profile_line_create` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L108) |
| workbench | PUT | `/api/profile/line` | `create_memory_router.profile_line_update` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L117) |
| workbench | DELETE | `/api/profile/line/{line_id}` | `create_memory_router.profile_line_delete` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L127) |
| workbench | GET | `/api/publications` | `create_publications_router.publications` | [agent/src/omni/channels/web_api/publications.py](../../../agent/src/omni/channels/web_api/publications.py#L17) |
| workbench | POST | `/api/publications/{identity}/edit` | `create_publications_router.publication_edit` | [agent/src/omni/channels/web_api/publications.py](../../../agent/src/omni/channels/web_api/publications.py#L28) |
| workbench | POST | `/api/publications/{identity}/resolve` | `create_publications_router.publication_resolve` | [agent/src/omni/channels/web_api/publications.py](../../../agent/src/omni/channels/web_api/publications.py#L48) |
| workbench | GET | `/api/resident` | `create_memory_router.get_resident` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L134) |
| workbench | POST | `/api/resident/update` | `create_memory_router.resident_update` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L142) |
| workbench | GET | `/api/reviews` | `create_memory_router.get_reviews` | [agent/src/omni/channels/web_api/memory.py](../../../agent/src/omni/channels/web_api/memory.py#L159) |
| workbench | GET | `/api/runs` | `create_runs_router.run_list` | [agent/src/omni/channels/web_api/runs.py](../../../agent/src/omni/channels/web_api/runs.py#L21) |
| workbench | POST | `/api/runs` | `create_conversation_router.run_create` | [agent/src/omni/channels/web_api/conversation.py](../../../agent/src/omni/channels/web_api/conversation.py#L168) |
| workbench | GET | `/api/runs/{run_id}` | `create_runs_router.run_get` | [agent/src/omni/channels/web_api/runs.py](../../../agent/src/omni/channels/web_api/runs.py#L29) |
| workbench | POST | `/api/runs/{run_id}/cancel` | `create_runs_router.run_cancel` | [agent/src/omni/channels/web_api/runs.py](../../../agent/src/omni/channels/web_api/runs.py#L48) |
| workbench | GET | `/api/runs/{run_id}/events` | `create_runs_router.run_events` | [agent/src/omni/channels/web_api/runs.py](../../../agent/src/omni/channels/web_api/runs.py#L36) |
| workbench | GET | `/api/sessions` | `create_sessions_router.get_sessions` | [agent/src/omni/channels/web_api/sessions.py](../../../agent/src/omni/channels/web_api/sessions.py#L49) |
| workbench | POST | `/api/sessions` | `create_sessions_router.create_session` | [agent/src/omni/channels/web_api/sessions.py](../../../agent/src/omni/channels/web_api/sessions.py#L63) |
| workbench | GET | `/api/sessions/{session_id}/messages` | `create_sessions_router.get_messages` | [agent/src/omni/channels/web_api/sessions.py](../../../agent/src/omni/channels/web_api/sessions.py#L73) |
| workbench | POST | `/api/sessions/{session_id}/select` | `create_sessions_router.select_session` | [agent/src/omni/channels/web_api/sessions.py](../../../agent/src/omni/channels/web_api/sessions.py#L67) |
| workbench | GET | `/api/sessions/{session_id}/todos` | `create_sessions_router.get_todos` | [agent/src/omni/channels/web_api/sessions.py](../../../agent/src/omni/channels/web_api/sessions.py#L82) |
| workbench | GET | `/api/skills` | `create_skills_router.get_skills` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L129) |
| workbench | POST | `/api/skills` | `create_skills_router.skill_create` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L198) |
| workbench | POST | `/api/skills/ack-all` | `create_skills_router.skills_ack_all` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L290) |
| workbench | POST | `/api/skills/archive/{name}/restore` | `create_skills_router.skill_restore` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L358) |
| workbench | GET | `/api/skills/inbox` | `create_skills_router.skills_inbox` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L214) |
| workbench | POST | `/api/skills/inbox/upload` | `create_skills_router.skill_package_upload` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L223) |
| workbench | DELETE | `/api/skills/inbox/{package}` | `create_skills_router.skill_package_remove` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L254) |
| workbench | POST | `/api/skills/inbox/{package}/import` | `create_skills_router.skill_package_import` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L245) |
| workbench | GET | `/api/skills/overview` | `create_skills_router.skills_overview` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L143) |
| workbench | GET | `/api/skills/pending` | `create_skills_router.skills_pending` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L285) |
| workbench | GET | `/api/skills/pending/{name}/diff` | `create_skills_router.skill_pending_diff` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L305) |
| workbench | POST | `/api/skills/quarantine/{name}/activate` | `create_skills_router.skill_package_activate` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L262) |
| workbench | POST | `/api/skills/quarantine/{name}/discard` | `create_skills_router.skill_package_discard` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L275) |
| workbench | GET | `/api/skills/settings` | `create_skills_router.skills_settings` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L171) |
| workbench | PUT | `/api/skills/settings` | `create_skills_router.skills_settings_save` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L178) |
| workbench | GET | `/api/skills/{name}` | `create_skills_router.get_skill` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L368) |
| workbench | POST | `/api/skills/{name}/ack` | `create_skills_router.skill_ack` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L329) |
| workbench | POST | `/api/skills/{name}/archive` | `create_skills_router.skill_archive` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L340) |
| workbench | GET | `/api/skills/{name}/diff` | `create_skills_router.skill_diff` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L445) |
| workbench | GET | `/api/skills/{name}/history` | `create_skills_router.skill_history` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L410) |
| workbench | GET | `/api/skills/{name}/revision/{rev}` | `create_skills_router.skill_revision` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L430) |
| workbench | POST | `/api/skills/{name}/rollback` | `create_skills_router.skill_rollback` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L465) |
| workbench | POST | `/api/skills/{name}/save` | `create_skills_router.skill_save` | [agent/src/omni/channels/web_api/skills.py](../../../agent/src/omni/channels/web_api/skills.py#L388) |
| workbench | GET | `/api/state` | `create_sessions_router.get_state` | [agent/src/omni/channels/web_api/sessions.py](../../../agent/src/omni/channels/web_api/sessions.py#L20) |
| workbench | GET | `/api/storage` | `create_storage_router.overview` | [agent/src/omni/channels/web_api/storage.py](../../../agent/src/omni/channels/web_api/storage.py#L84) |
| workbench | PUT | `/api/storage` | `create_storage_router.update` | [agent/src/omni/channels/web_api/storage.py](../../../agent/src/omni/channels/web_api/storage.py#L101) |
| workbench | GET | `/api/storage/files` | `create_storage_router.files` | [agent/src/omni/channels/web_api/storage.py](../../../agent/src/omni/channels/web_api/storage.py#L130) |
| workbench | POST | `/api/storage/folders` | `create_storage_router.create_folder` | [agent/src/omni/channels/web_api/storage.py](../../../agent/src/omni/channels/web_api/storage.py#L202) |
| workbench | POST | `/api/storage/preview` | `create_storage_router.preview` | [agent/src/omni/channels/web_api/storage.py](../../../agent/src/omni/channels/web_api/storage.py#L91) |
| workbench | GET | `/api/storage/preview-file` | `create_storage_router.preview_file` | [agent/src/omni/channels/web_api/storage.py](../../../agent/src/omni/channels/web_api/storage.py#L175) |
| workbench | POST | `/api/storage/upload` | `create_storage_router.upload` | [agent/src/omni/channels/web_api/storage.py](../../../agent/src/omni/channels/web_api/storage.py#L216) |
| workbench | POST | `/api/system/backup` | `create_operations_router.system_backup` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L98) |
| workbench | GET | `/api/system/bili-account` | `create_embodiments_router.bili_account` | [agent/src/omni/channels/web_api/embodiments.py](../../../agent/src/omni/channels/web_api/embodiments.py#L116) |
| workbench | GET | `/api/system/bili-cookies` | `create_settings_router.bili_cookies_status` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L341) |
| workbench | POST | `/api/system/bili-cookies` | `create_settings_router.save_bili_cookies` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L353) |
| workbench | GET | `/api/system/config` | `create_settings_router.system_config` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L172) |
| workbench | POST | `/api/system/config` | `create_settings_router.system_config_save` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L288) |
| workbench | GET | `/api/system/cron` | `create_automation_router.cron_list` | [agent/src/omni/channels/web_api/automation.py](../../../agent/src/omni/channels/web_api/automation.py#L54) |
| workbench | POST | `/api/system/cron/create` | `create_automation_router.cron_create` | [agent/src/omni/channels/web_api/automation.py](../../../agent/src/omni/channels/web_api/automation.py#L23) |
| workbench | POST | `/api/system/cron/delete` | `create_automation_router.cron_delete` | [agent/src/omni/channels/web_api/automation.py](../../../agent/src/omni/channels/web_api/automation.py#L138) |
| workbench | GET | `/api/system/cron/history` | `create_automation_router.cron_history` | [agent/src/omni/channels/web_api/automation.py](../../../agent/src/omni/channels/web_api/automation.py#L145) |
| workbench | GET | `/api/system/cron/outbox` | `create_automation_router.cron_outbox` | [agent/src/omni/channels/web_api/automation.py](../../../agent/src/omni/channels/web_api/automation.py#L153) |
| workbench | POST | `/api/system/cron/outbox/{entry_id}/discard` | `create_automation_router.cron_outbox_discard` | [agent/src/omni/channels/web_api/automation.py](../../../agent/src/omni/channels/web_api/automation.py#L180) |
| workbench | POST | `/api/system/cron/outbox/{entry_id}/retry` | `create_automation_router.cron_outbox_retry` | [agent/src/omni/channels/web_api/automation.py](../../../agent/src/omni/channels/web_api/automation.py#L168) |
| workbench | POST | `/api/system/cron/toggle` | `create_automation_router.cron_toggle` | [agent/src/omni/channels/web_api/automation.py](../../../agent/src/omni/channels/web_api/automation.py#L124) |
| workbench | POST | `/api/system/cron/update` | `create_automation_router.cron_update` | [agent/src/omni/channels/web_api/automation.py](../../../agent/src/omni/channels/web_api/automation.py#L84) |
| workbench | GET | `/api/system/doctor` | `create_operations_router.system_doctor` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L94) |
| workbench | GET | `/api/system/embodiments` | `create_embodiments_router.embodiments` | [agent/src/omni/channels/web_api/embodiments.py](../../../agent/src/omni/channels/web_api/embodiments.py#L112) |
| workbench | GET | `/api/system/logs/{name}` | `create_operations_router.system_logs` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L125) |
| workbench | POST | `/api/system/model-key` | `create_settings_router.model_key_set` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L376) |
| workbench | GET | `/api/system/model-keys` | `create_settings_router.model_keys` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L371) |
| workbench | POST | `/api/system/model-test` | `create_settings_router.model_test` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L393) |
| workbench | GET | `/api/system/napcat/qr` | `create_operations_router.system_napcat_qr` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L121) |
| workbench | GET | `/api/system/overview` | `create_operations_router.system_overview` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L24) |
| workbench | GET | `/api/system/qq-policy` | `create_settings_router.qq_policy_status` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L312) |
| workbench | POST | `/api/system/qq-policy/stop` | `create_settings_router.qq_policy_stop` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L333) |
| workbench | POST | `/api/system/services/cleanup` | `create_operations_router.system_services_cleanup` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L46) |
| workbench | GET | `/api/system/services/summary` | `create_operations_router.system_services_summary` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L28) |
| workbench | POST | `/api/system/services/{name}/restart` | `create_operations_router.system_service_restart` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L33) |
| workbench | POST | `/api/system/services/{name}/start` | `create_operations_router.system_service_start` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L107) |
| workbench | POST | `/api/system/services/{name}/stop` | `create_operations_router.system_service_stop` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L114) |
| workbench | GET | `/api/system/state` | `create_operations_router.system_state` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L129) |
| workbench | GET | `/api/system/supervisor` | `create_operations_router.system_supervisor` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L59) |
| workbench | POST | `/api/system/supervisor` | `create_operations_router.system_supervisor_save` | [agent/src/omni/channels/web_api/operations.py](../../../agent/src/omni/channels/web_api/operations.py#L72) |
| workbench | GET | `/api/system/updates` | `create_updates_router.status` | [agent/src/omni/channels/web_api/updates.py](../../../agent/src/omni/channels/web_api/updates.py#L32) |
| workbench | POST | `/api/system/updates/check` | `create_updates_router.check` | [agent/src/omni/channels/web_api/updates.py](../../../agent/src/omni/channels/web_api/updates.py#L40) |
| workbench | POST | `/api/system/updates/install` | `create_updates_router.install` | [agent/src/omni/channels/web_api/updates.py](../../../agent/src/omni/channels/web_api/updates.py#L48) |
| workbench | GET | `/api/system/updates/operations/{identifier}` | `create_updates_router.operation` | [agent/src/omni/channels/web_api/updates.py](../../../agent/src/omni/channels/web_api/updates.py#L52) |
| workbench | PUT | `/api/system/updates/policy` | `create_updates_router.policy` | [agent/src/omni/channels/web_api/updates.py](../../../agent/src/omni/channels/web_api/updates.py#L36) |
| workbench | POST | `/api/system/updates/prepare` | `create_updates_router.prepare` | [agent/src/omni/channels/web_api/updates.py](../../../agent/src/omni/channels/web_api/updates.py#L44) |
| workbench | GET | `/api/system/version` | `create_updates_router.version` | [agent/src/omni/channels/web_api/updates.py](../../../agent/src/omni/channels/web_api/updates.py#L28) |
| workbench | GET | `/api/terminal/options` | `create_sessions_router.options` | [agent/src/omni/channels/terminal/sessions.py](../../../agent/src/omni/channels/terminal/sessions.py#L42) |
| workbench | GET | `/api/terminal/sessions` | `create_sessions_router.sessions` | [agent/src/omni/channels/terminal/sessions.py](../../../agent/src/omni/channels/terminal/sessions.py#L46) |
| workbench | POST | `/api/terminal/sessions` | `create_sessions_router.create` | [agent/src/omni/channels/terminal/sessions.py](../../../agent/src/omni/channels/terminal/sessions.py#L50) |
| workbench | DELETE | `/api/terminal/sessions/{identifier}` | `create_sessions_router.end` | [agent/src/omni/channels/terminal/sessions.py](../../../agent/src/omni/channels/terminal/sessions.py#L66) |
| workbench | PATCH | `/api/terminal/sessions/{identifier}` | `create_sessions_router.rename` | [agent/src/omni/channels/terminal/sessions.py](../../../agent/src/omni/channels/terminal/sessions.py#L60) |
| workbench | WEBSOCKET | `/api/terminal/sessions/{identifier}/ws` | `create_sessions_router.connect` | [agent/src/omni/channels/terminal/sessions.py](../../../agent/src/omni/channels/terminal/sessions.py#L74) |
| workbench | WEBSOCKET | `/api/terminal/ws` | `create_terminal_router.terminal_ws` | [agent/src/omni/channels/terminal/routes.py](../../../agent/src/omni/channels/terminal/routes.py#L19) |
| workbench | POST | `/api/todos/toggle` | `create_sessions_router.toggle_todo` | [agent/src/omni/channels/web_api/sessions.py](../../../agent/src/omni/channels/web_api/sessions.py#L94) |
| workbench | GET | `/api/workbench/docs/{name}` | `create_workbench_router.document` | [agent/src/omni/channels/workbench/routes.py](../../../agent/src/omni/channels/workbench/routes.py#L40) |
| workbench | GET | `/api/workbench/extensions` | `create_workbench_router.extensions` | [agent/src/omni/channels/workbench/routes.py](../../../agent/src/omni/channels/workbench/routes.py#L68) |
| workbench | GET | `/api/workbench/project` | `create_workbench_router.project` | [agent/src/omni/channels/workbench/routes.py](../../../agent/src/omni/channels/workbench/routes.py#L36) |
| workbench | GET | `/api/workbench/tools` | `create_workbench_router.tools` | [agent/src/omni/channels/workbench/routes.py](../../../agent/src/omni/channels/workbench/routes.py#L56) |
| workbench | GET | `/pair/{code}` | `create_settings_router.pair_link` | [agent/src/omni/channels/web_api/settings.py](../../../agent/src/omni/channels/web_api/settings.py#L98) |

## 请求模型

### AgentInput

定位：[agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L260)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |

### AgentRenameInput

定位：[agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L264)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| revision | `int` | 是 |

### GoalUpdateInput

定位：[agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L268)。

| 字段 | 类型 | 必填 |
|---|---|---|
| ticket_id | `uuid.UUID` | 是 |
| expected_goal_version | `int` | 是 |

### TurnInput

定位：[agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L273)。

| 字段 | 类型 | 必填 |
|---|---|---|
| session_id | `uuid.UUID \| None` | 否 |
| key | `str` | 是 |
| message | `str` | 否 |
| image_ids | `list[uuid.UUID]` | 否 |
| mode | `Literal['auto', 'chat', 'retrieval', 'agent'] \| None` | 否 |
| skill_ids | `list[uuid.UUID]` | 否 |
| goal_update | `GoalUpdateInput \| None` | 否 |

### ApprovalInput

定位：[agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L283)。

| 字段 | 类型 | 必填 |
|---|---|---|
| allowed | `bool` | 是 |
| policy_revision | `int` | 是 |

### ClarifyRespondInput

定位：[agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L288)。

| 字段 | 类型 | 必填 |
|---|---|---|
| answers | `dict[str, object]` | 是 |
| revision | `int` | 是 |
| policy_revision | `int` | 是 |

### ClarifyCancelInput

定位：[agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L294)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `int` | 是 |
| notice | `str` | 否 |

### ObjectPinInput

定位：[agent/src/omni/adapters/http.py](../../../agent/src/omni/adapters/http.py#L299)。

| 字段 | 类型 | 必填 |
|---|---|---|
| pinned | `bool` | 是 |
| revision | `int` | 是 |

### StrictInput

定位：[agent/src/omni/adapters/http_routes/__init__.py](../../../agent/src/omni/adapters/http_routes/__init__.py#L29)。

| 字段 | 类型 | 必填 |
|---|---|---|

### BilibiliProfileInput

定位：[agent/src/omni/adapters/http_routes/bilibili.py](../../../agent/src/omni/adapters/http_routes/bilibili.py#L19)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 是 |
| headless | `bool` | 否 |
| forms | `Payload` | 是 |
| revision | `int` | 是 |

### BilibiliReadInput

定位：[agent/src/omni/adapters/http_routes/bilibili.py](../../../agent/src/omni/adapters/http_routes/bilibili.py#L26)。

| 字段 | 类型 | 必填 |
|---|---|---|
| config_id | `uuid.UUID` | 是 |
| operation | `Literal['account', 'video', 'comments', 'replies', 'search', 'whois', 'relation', 'dynamic_list', 'danmaku', 'popular', 'ranking', 'following', 'favorites', 'fa` | 是 |
| target | `str` | 否 |
| limit | `int` | 否 |

### BilibiliPublicationInput

定位：[agent/src/omni/adapters/http_routes/bilibili.py](../../../agent/src/omni/adapters/http_routes/bilibili.py#L51)。

| 字段 | 类型 | 必填 |
|---|---|---|
| config_id | `uuid.UUID` | 是 |
| message_id | `uuid.UUID` | 是 |
| kind | `Literal['comment', 'private_message']` | 是 |
| target | `str` | 是 |
| mentions | `list[Payload]` | 否 |
| key | `str` | 是 |

### RoutingInput

定位：[agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L19)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `int` | 是 |
| value | `Payload` | 是 |

### SettingsInput

定位：[agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L24)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `int` | 是 |
| value | `Payload` | 是 |

### ConfigurationInput

定位：[agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L29)。

| 字段 | 类型 | 必填 |
|---|---|---|
| kind | `Literal['model', 'channel', 'mcp', 'preset']` | 是 |
| name | `str` | 是 |
| enabled | `bool` | 否 |
| value | `Payload` | 是 |
| image_policy | `Payload \| None` | 否 |

### ConfigurationEdit

定位：[agent/src/omni/adapters/http_routes/configurations.py](../../../agent/src/omni/adapters/http_routes/configurations.py#L37)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| enabled | `bool` | 是 |
| revision | `int` | 是 |
| value | `Payload` | 是 |
| image_policy | `Payload \| None` | 否 |

### CredentialInput

定位：[agent/src/omni/adapters/http_routes/credentials.py](../../../agent/src/omni/adapters/http_routes/credentials.py#L14)。

| 字段 | 类型 | 必填 |
|---|---|---|
| value | `str` | 是 |

### CredentialRegistration

定位：[agent/src/omni/adapters/http_routes/credentials.py](../../../agent/src/omni/adapters/http_routes/credentials.py#L18)。

| 字段 | 类型 | 必填 |
|---|---|---|
| reference | `str` | 是 |

### CredentialReview

定位：[agent/src/omni/adapters/http_routes/credentials.py](../../../agent/src/omni/adapters/http_routes/credentials.py#L22)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `int` | 是 |
| authority_epoch | `int` | 是 |
| fingerprint | `str` | 是 |
| policy | `str` | 是 |
| decision | `Literal['allow_current', 'revoke']` | 是 |
| evidence | `str` | 是 |

### DeliveryInput

定位：[agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L14)。

| 字段 | 类型 | 必填 |
|---|---|---|
| message_id | `uuid.UUID` | 是 |
| config_id | `uuid.UUID` | 是 |
| target | `str` | 是 |
| key | `str` | 是 |
| parent_id | `uuid.UUID \| None` | 否 |
| acknowledge_duplicate | `bool` | 否 |

### DeliveryDecision

定位：[agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L23)。

| 字段 | 类型 | 必填 |
|---|---|---|
| allowed | `bool` | 是 |
| revision | `int` | 是 |
| fingerprint | `str` | 是 |

### DeliveryReview

定位：[agent/src/omni/adapters/http_routes/deliveries.py](../../../agent/src/omni/adapters/http_routes/deliveries.py#L29)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `int` | 是 |
| fingerprint | `str` | 是 |
| decision | `Literal['confirmed_sent', 'confirmed_not_sent', 'allow_future']` | 是 |
| evidence | `str` | 是 |

### SourceDocumentInput

定位：[agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L15)。

| 字段 | 类型 | 必填 |
|---|---|---|
| document_id | `uuid.UUID \| None` | 否 |
| title | `str` | 是 |
| content | `str` | 是 |
| revision | `int` | 否 |

### SourceDocumentImport

定位：[agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L22)。

| 字段 | 类型 | 必填 |
|---|---|---|
| title | `str` | 是 |
| object_id | `uuid.UUID` | 是 |
| document_id | `uuid.UUID \| None` | 否 |
| revision | `int` | 否 |

### SourceIndexInput

定位：[agent/src/omni/adapters/http_routes/documents.py](../../../agent/src/omni/adapters/http_routes/documents.py#L29)。

| 字段 | 类型 | 必填 |
|---|---|---|
| kind | `Literal['full_text', 'vector']` | 是 |

### MCPConnectInput

定位：[agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L17)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `int` | 是 |

### MCPToolAuthorizationInput

定位：[agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L21)。

| 字段 | 类型 | 必填 |
|---|---|---|
| tool_id | `uuid.UUID` | 是 |
| read_only | `bool` | 是 |

### MCPAuthorizationInput

定位：[agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L26)。

| 字段 | 类型 | 必填 |
|---|---|---|
| catalog_fingerprint | `str` | 是 |
| tools | `list[MCPToolAuthorizationInput]` | 否 |
| evidence | `str` | 是 |

### MCPToolDecisionInput

定位：[agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L34)。

| 字段 | 类型 | 必填 |
|---|---|---|
| catalog_fingerprint | `str` | 是 |
| read_only | `bool` | 否 |
| authorized | `bool` | 否 |
| evidence | `str` | 是 |

### MCPOAuthStartInput

定位：[agent/src/omni/adapters/http_routes/mcp.py](../../../agent/src/omni/adapters/http_routes/mcp.py#L41)。

| 字段 | 类型 | 必填 |
|---|---|---|
| redirect_uri | `str` | 否 |

### MediaSourceInput

定位：[agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L17)。

| 字段 | 类型 | 必填 |
|---|---|---|
| config_id | `uuid.UUID` | 是 |
| target | `str` | 是 |

### MediaJobInput

定位：[agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L22)。

| 字段 | 类型 | 必填 |
|---|---|---|
| operation | `Literal['subtitle', 'transcribe', 'frames']` | 是 |
| model | `str` | 否 |
| device | `Literal['cpu', 'auto']` | 否 |
| compute_type | `Literal['int8', 'float16', 'float32']` | 否 |
| count | `int` | 否 |
| width | `int` | 否 |
| timestamps | `list[float] \| None` | 否 |
| select | `Literal['equidistant', 'scenes']` | 否 |

### MediaUploadSourceInput

定位：[agent/src/omni/adapters/http_routes/media.py](../../../agent/src/omni/adapters/http_routes/media.py#L33)。

| 字段 | 类型 | 必填 |
|---|---|---|
| object_id | `uuid.UUID` | 是 |
| title | `str` | 是 |

### MemoryInput

定位：[agent/src/omni/adapters/http_routes/memory.py](../../../agent/src/omni/adapters/http_routes/memory.py#L16)。

| 字段 | 类型 | 必填 |
|---|---|---|
| content | `str` | 是 |
| message_id | `uuid.UUID \| None` | 否 |
| quote | `str` | 否 |
| action_id | `uuid.UUID \| None` | 否 |
| document_revision_id | `uuid.UUID \| None` | 否 |

### MainSessionInput

定位：[agent/src/omni/adapters/http_routes/omniweave.py](../../../agent/src/omni/adapters/http_routes/omniweave.py#L12)。

| 字段 | 类型 | 必填 |
|---|---|---|
| session_id | `UUID \| None` | 否 |

### DebugInput

定位：[agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L16)。

| 字段 | 类型 | 必填 |
|---|---|---|
| duration_s | `int` | 否 |

### RunPinInput

定位：[agent/src/omni/adapters/http_routes/operations.py](../../../agent/src/omni/adapters/http_routes/operations.py#L20)。

| 字段 | 类型 | 必填 |
|---|---|---|
| pinned | `bool` | 是 |
| revision | `int` | 是 |

### PersonInput

定位：[agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L14)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |

### PersonEdit

定位：[agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L18)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| revision | `int` | 是 |

### AccountInput

定位：[agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L22)。

| 字段 | 类型 | 必填 |
|---|---|---|
| channel | `str` | 是 |
| external_id | `str` | 是 |

### DocumentVersion

定位：[agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L27)。

| 字段 | 类型 | 必填 |
|---|---|---|
| entry_id | `uuid.UUID \| None` | 是 |
| revision | `int` | 是 |

### DocumentInput

定位：[agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L32)。

| 字段 | 类型 | 必填 |
|---|---|---|
| entry_id | `uuid.UUID \| None` | 是 |
| revision | `int` | 是 |
| content | `str` | 是 |

### DocumentImport

定位：[agent/src/omni/adapters/http_routes/people.py](../../../agent/src/omni/adapters/http_routes/people.py#L36)。

| 字段 | 类型 | 必填 |
|---|---|---|
| entry_id | `uuid.UUID \| None` | 是 |
| revision | `int` | 是 |
| object_id | `uuid.UUID` | 是 |

### QQBindingInput

定位：[agent/src/omni/adapters/http_routes/qq.py](../../../agent/src/omni/adapters/http_routes/qq.py#L16)。

| 字段 | 类型 | 必填 |
|---|---|---|
| config_id | `uuid.UUID` | 是 |
| scene | `Literal['private', 'group']` | 是 |
| peer_id | `str` | 是 |
| enabled | `bool` | 是 |
| persona | `str` | 是 |
| cooldown_s | `int` | 是 |
| revision | `int` | 否 |

### ScheduleInput

定位：[agent/src/omni/adapters/http_routes/schedules.py](../../../agent/src/omni/adapters/http_routes/schedules.py#L13)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| enabled | `bool` | 否 |
| value | `dict[str, object]` | 是 |
| revision | `int` | 否 |

### ScheduleReview

定位：[agent/src/omni/adapters/http_routes/schedules.py](../../../agent/src/omni/adapters/http_routes/schedules.py#L20)。

| 字段 | 类型 | 必填 |
|---|---|---|
| occurrence_id | `uuid.UUID` | 是 |
| fingerprint | `str` | 是 |

### SkillSaveInput

定位：[agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L16)。

| 字段 | 类型 | 必填 |
|---|---|---|
| content | `str` | 是 |
| skill_id | `uuid.UUID \| None` | 否 |
| revision | `int` | 否 |

### SkillImportInput

定位：[agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L22)。

| 字段 | 类型 | 必填 |
|---|---|---|
| object_id | `uuid.UUID` | 是 |
| format | `Literal['markdown', 'zip']` | 是 |
| skill_id | `uuid.UUID \| None` | 否 |
| revision | `int` | 否 |

### SkillStateInput

定位：[agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L29)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `int` | 是 |
| state | `Literal['enabled', 'disabled', 'archived']` | 是 |
| revision_id | `uuid.UUID` | 是 |
| manifest_sha256 | `str` | 是 |

### SkillRollbackInput

定位：[agent/src/omni/adapters/http_routes/skills.py](../../../agent/src/omni/adapters/http_routes/skills.py#L36)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `int` | 是 |
| revision_id | `uuid.UUID` | 是 |

### TerminalCreateIn

定位：[agent/src/omni/channels/terminal/sessions.py](../../../agent/src/omni/channels/terminal/sessions.py#L16)。

| 字段 | 类型 | 必填 |
|---|---|---|
| shell | `Literal['powershell', 'pwsh', 'cmd']` | 否 |
| cwd | `str \| None` | 否 |
| cols | `int` | 否 |
| rows | `int` | 否 |

### TerminalRenameIn

定位：[agent/src/omni/channels/terminal/sessions.py](../../../agent/src/omni/channels/terminal/sessions.py#L23)。

| 字段 | 类型 | 必填 |
|---|---|---|
| label | `str` | 是 |

### AndroidActionIn

定位：[agent/src/omni/channels/web_api/android.py](../../../agent/src/omni/channels/web_api/android.py#L15)。

| 字段 | 类型 | 必填 |
|---|---|---|
| action | `Literal['tap_element', 'tap_coords', 'tap_scaled', 'swipe', 'key', 'type_text', 'long_press', 'launch_app']` | 是 |
| observation | `str` | 是 |
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

定位：[agent/src/omni/channels/web_api/context.py](../../../agent/src/omni/channels/web_api/context.py#L30)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `str` | 是 |
| updates | `dict[str, dict[str, Any]]` | 是 |

### MarketSourceIn

定位：[agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L34)。

| 字段 | 类型 | 必填 |
|---|---|---|
| server_url | `str` | 否 |

### MarketInstallIn

定位：[agent/src/omni/channels/web_api/market.py](../../../agent/src/omni/channels/web_api/market.py#L40)。

| 字段 | 类型 | 必填 |
|---|---|---|
| version | `str` | 否 |

### ChatIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L10)。

| 字段 | 类型 | 必填 |
|---|---|---|
| message | `str` | 是 |
| session_id | `int \| None` | 否 |
| mode | `Literal['auto', 'chat', 'retrieval', 'agent'] \| None` | 否 |

### ResolveIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L18)。

| 字段 | 类型 | 必填 |
|---|---|---|
| allowed | `bool` | 是 |
| reason | `str` | 否 |

### NapCatStartIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L23)。

| 字段 | 类型 | 必填 |
|---|---|---|
| qq | `str` | 否 |

### ConfigUpdateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L27)。

| 字段 | 类型 | 必填 |
|---|---|---|
| updates | `dict[str, dict[str, Any]]` | 是 |

### ServiceCleanupIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L31)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 否 |
| pid | `int \| None` | 否 |
| confirm | `bool` | 否 |

### SupervisorPolicyIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L39)。

| 字段 | 类型 | 必填 |
|---|---|---|
| autostart | `bool \| None` | 否 |
| keep_running_after_exit | `bool \| None` | 否 |
| restart_max_attempts | `int \| None` | 否 |
| napcat_qq | `str \| None` | 否 |

### ModelTestIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L48)。

| 字段 | 类型 | 必填 |
|---|---|---|
| model | `str` | 是 |

### MCPSchemaConfirmIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L52)。

| 字段 | 类型 | 必填 |
|---|---|---|
| fingerprint | `str` | 是 |

### BiliCookiesIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L56)。

| 字段 | 类型 | 必填 |
|---|---|---|
| sessdata | `str` | 否 |
| bili_jct | `str` | 否 |
| buvid3 | `str` | 否 |

### CronNameIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L62)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |

### CronToggleIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L66)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| enabled | `bool` | 是 |

### CronCreateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L71)。

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

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L83)。

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

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L99)。

| 字段 | 类型 | 必填 |
|---|---|---|
| session_id | `int` | 是 |
| todo_id | `int` | 是 |
| done | `bool` | 是 |

### AndroidTapIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L105)。

| 字段 | 类型 | 必填 |
|---|---|---|
| x | `int` | 是 |
| y | `int` | 是 |
| stream_w | `int` | 是 |
| stream_h | `int` | 是 |
| observation | `str` | 否 |

### AndroidElementIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L113)。

| 字段 | 类型 | 必填 |
|---|---|---|
| idx | `int` | 是 |
| observation | `str` | 否 |

### AndroidKeyIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L118)。

| 字段 | 类型 | 必填 |
|---|---|---|
| keycode | `str` | 是 |
| observation | `str` | 否 |

### ProfileLineIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L123)。

| 字段 | 类型 | 必填 |
|---|---|---|
| line_id | `int` | 是 |
| content | `str` | 是 |

### ProfileLineCreateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L128)。

| 字段 | 类型 | 必填 |
|---|---|---|
| content | `str` | 是 |

### FactCreateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L134)。

| 字段 | 类型 | 必填 |
|---|---|---|
| content | `str` | 是 |

### ResidentUpdateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L140)。

| 字段 | 类型 | 必填 |
|---|---|---|
| operation | `str` | 是 |
| id | `int \| None` | 否 |
| content | `str` | 否 |

### PairIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L146)。

| 字段 | 类型 | 必填 |
|---|---|---|
| code | `str` | 是 |

### SkillSaveIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L150)。

| 字段 | 类型 | 必填 |
|---|---|---|
| content | `str` | 是 |
| expected_hash | `str \| None` | 否 |

### SkillCreateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L157)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| description | `str` | 是 |
| content | `str` | 是 |

### SkillArchiveIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L165)。

| 字段 | 类型 | 必填 |
|---|---|---|
| expected_hash | `str \| None` | 否 |
| reason | `str` | 否 |

### SkillSettingsIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L172)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `str` | 是 |
| updates | `dict[str, dict[str, Any]]` | 是 |

### SkillRollbackIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L179)。

| 字段 | 类型 | 必填 |
|---|---|---|
| rev | `int` | 是 |
| expected_hash | `str \| None` | 否 |

### SkillAckIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L184)。

| 字段 | 类型 | 必填 |
|---|---|---|
| rev | `int` | 是 |

### PersonCreateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L193)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| importance | `str` | 否 |
| note | `str` | 否 |

### PersonUpdateIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L199)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str \| None` | 否 |
| importance | `str \| None` | 否 |
| note | `str \| None` | 否 |

### AccountLinkIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L205)。

| 字段 | 类型 | 必填 |
|---|---|---|
| channel | `str` | 是 |
| value | `str` | 是 |
| label | `str` | 否 |

### PersonEntryIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L211)。

| 字段 | 类型 | 必填 |
|---|---|---|
| entry_kind | `str` | 是 |
| content | `str` | 是 |
| expires_at | `str \| None` | 否 |

### UpdatePolicyIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L217)。

| 字段 | 类型 | 必填 |
|---|---|---|
| channel | `Literal['stable', 'development']` | 否 |
| auto_check | `bool` | 否 |

### UpdatePrepareIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L222)。

| 字段 | 类型 | 必填 |
|---|---|---|
| candidate_id | `str` | 是 |

### UpdateInstallIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L226)。

| 字段 | 类型 | 必填 |
|---|---|---|
| operation_id | `str` | 是 |
| confirm | `bool` | 否 |

### PersonaPresetsIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L231)。

| 字段 | 类型 | 必填 |
|---|---|---|
| items | `list[dict[str, str]]` | 是 |
| revision | `str` | 是 |

### ModelKeyIn

定位：[agent/src/omni/channels/web_api/schemas.py](../../../agent/src/omni/channels/web_api/schemas.py#L236)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| value | `str \| None` | 否 |

### DirectoryUpdate

定位：[agent/src/omni/channels/web_api/storage.py](../../../agent/src/omni/channels/web_api/storage.py#L28)。

| 字段 | 类型 | 必填 |
|---|---|---|
| revision | `str` | 是 |
| catalog | `dict` | 是 |
| acknowledge | `bool` | 否 |

### FolderCreate

定位：[agent/src/omni/channels/web_api/storage.py](../../../agent/src/omni/channels/web_api/storage.py#L35)。

| 字段 | 类型 | 必填 |
|---|---|---|
| scope | `str` | 是 |
| path | `str` | 是 |

### Persona

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L162)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 否 |
| system_prompt | `str` | 否 |
| group_prompt | `str` | 否 |
| private_prompt | `str` | 否 |

### Models

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L206)。

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

### Reasoning

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L219)。

| 字段 | 类型 | 必填 |
|---|---|---|
| chat | `str` | 否 |
| vision | `str` | 否 |
| review | `str` | 否 |
| router | `str` | 否 |
| chat_mode | `str` | 否 |
| retrieval_mode | `str` | 否 |
| agent_mode | `str` | 否 |

### ModeRouting

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L255)。

| 字段 | 类型 | 必填 |
|---|---|---|
| default_mode | `str` | 否 |
| allow_escalation | `bool` | 否 |

### ModelCapability

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L276)。

| 字段 | 类型 | 必填 |
|---|---|---|
| tools | `bool` | 否 |
| vision | `bool` | 否 |
| stream | `bool` | 否 |
| context_tokens | `int` | 否 |
| input_usd_per_m | `float \| None` | 否 |
| output_usd_per_m | `float \| None` | 否 |

### Retry

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L294)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_attempts | `int` | 否 |
| total_deadline_s | `float` | 否 |
| backoff_s | `float` | 否 |

### LLMBudget

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L302)。

| 字段 | 类型 | 必填 |
|---|---|---|
| daily_usd | `float` | 否 |
| per_run_usd | `float` | 否 |
| scope_daily_usd | `dict[str, float]` | 否 |

### Vision

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L324)。

| 字段 | 类型 | 必填 |
|---|---|---|
| send_to_cloud | `bool` | 否 |
| max_image_bytes | `int` | 否 |
| max_images_per_turn | `int` | 否 |
| max_edge_px | `int` | 否 |

### Artifacts

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L338)。

| 字段 | 类型 | 必填 |
|---|---|---|
| retention_days | `int` | 否 |
| max_total_mb | `int` | 否 |

### LLM

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L346)。

| 字段 | 类型 | 必填 |
|---|---|---|
| request_timeout_s | `float` | 否 |
| max_output_tokens | `int` | 否 |
| retry | `Retry` | 否 |
| budget | `LLMBudget` | 否 |
| capabilities | `dict[str, ModelCapability]` | 否 |

### Limits

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L359)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_history_messages | `int` | 否 |
| max_agent_steps | `int` | 否 |
| resident_memory_chars | `int` | 否 |
| session_queue_depth | `int` | 否 |
| session_lease_ttl_s | `int` | 否 |

### Review

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L367)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| max_messages | `int` | 否 |
| steps | `int` | 否 |
| queue_depth | `int` | 否 |
| min_messages | `int` | 否 |

### Memory

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L377)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_writes_per_day | `int` | 否 |
| max_fact_chars | `int` | 否 |
| recall_prefix | `bool` | 否 |

### Skills

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L389)。

| 字段 | 类型 | 必填 |
|---|---|---|
| history_keep | `int` | 否 |
| max_write_chars | `int` | 否 |

### Web

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L402)。

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

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L419)。

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

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L437)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| trigger_chars | `int` | 否 |
| keep_recent | `int` | 否 |
| min_messages | `int` | 否 |

### Asr

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L446)。

| 字段 | 类型 | 必填 |
|---|---|---|
| model | `str` | 否 |
| device | `str` | 否 |
| compute_type | `str` | 否 |

### QQ

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L454)。

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
| proactive_cooldown_minutes | `int` | 否 |
| quiet_start | `str` | 否 |
| quiet_end | `str` | 否 |
| timezone | `str` | 否 |

### Browser

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L495)。

| 字段 | 类型 | 必填 |
|---|---|---|
| headless | `bool` | 否 |
| slow_mo_ms | `int` | 否 |
| max_writes_per_day | `int` | 否 |
| approval_timeout_s | `float` | 否 |

### UIFormSelectors

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L504)。

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
| mention_input | `str` | 否 |
| mention_options | `str` | 否 |
| mention_nodes | `str` | 否 |
| mention_uid_attr | `str` | 否 |

### BilibiliWrite

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L527)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| forms | `dict[str, UIFormSelectors]` | 否 |
| draft_ttl_s | `int` | 否 |
| max_content_chars | `int` | 否 |

### System

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L549)。

| 字段 | 类型 | 必填 |
|---|---|---|
| napcat_dir | `str` | 否 |
| autostart | `bool` | 否 |
| keep_running_after_exit | `bool` | 否 |
| supervise_interval_s | `float` | 否 |
| restart_max_attempts | `int` | 否 |
| restart_backoff_cap_s | `int` | 否 |
| health_timeout_s | `int` | 否 |
| napcat_qq | `str` | 否 |

### Approval

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L567)。

| 字段 | 类型 | 必填 |
|---|---|---|
| mode | `str` | 否 |

### MCPServerConfig

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L577)。

| 字段 | 类型 | 必填 |
|---|---|---|
| command | `str` | 否 |
| transport | `Literal['stdio', 'streamable_http']` | 否 |
| url | `str` | 否 |
| auth | `Literal['none', 'oauth']` | 否 |
| oauth_redirect_uri | `str` | 否 |
| oauth_allowed_origins | `list[str]` | 否 |
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

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L658)。

| 字段 | 类型 | 必填 |
|---|---|---|
| servers | `dict[str, MCPServerConfig]` | 否 |

### Desktop

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L672)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| foreground_enabled | `bool` | 否 |
| allowed_apps | `list[str]` | 否 |
| max_nodes | `int` | 否 |
| timeout_s | `float` | 否 |
| observation_ttl_s | `float` | 否 |
| max_writes_per_day | `int` | 否 |

### Embedding

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L690)。

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

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L733)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_running | `int` | 否 |

### Journal

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L738)。

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

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L767)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_entry_chars | `int` | 否 |
| max_tool_args_chars | `int` | 否 |
| max_tool_result_chars | `int` | 否 |
| retention_days | `int` | 否 |
| dialogue_retention_days | `int` | 否 |

### People

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L782)。

| 字段 | 类型 | 必填 |
|---|---|---|
| person_card_chars | `int` | 否 |
| archive_max_chars | `int` | 否 |
| resident_max_chars | `int` | 否 |
| event_max_chars | `int` | 否 |

### AndroidConfig

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L792)。

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

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L812)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_running | `int` | 否 |
| max_waiting | `int` | 否 |
| default_timeout_s | `float` | 否 |
| log_bytes | `int` | 否 |
| retention_days | `int` | 否 |
| total_log_bytes | `int` | 否 |

### TerminalConfig

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L822)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_sessions | `int` | 否 |
| roots | `list[str]` | 否 |
| buffer_bytes | `int` | 否 |
| detach_ttl_s | `float` | 否 |

### Updates

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L830)。

| 字段 | 类型 | 必填 |
|---|---|---|
| channel | `Literal['stable', 'development']` | 否 |
| auto_check | `bool` | 否 |

### AppConfig

定位：[agent/src/omni/config.py](../../../agent/src/omni/config.py#L836)。

| 字段 | 类型 | 必填 |
|---|---|---|
| updates | `Updates` | 否 |
| android | `AndroidConfig` | 否 |
| command_jobs | `CommandJobsConfig` | 否 |
| terminal | `TerminalConfig` | 否 |
| persona | `Persona` | 否 |
| models | `Models` | 否 |
| reasoning | `Reasoning` | 否 |
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

### Form

定位：[agent/src/omni/domain/bilibili.py](../../../agent/src/omni/domain/bilibili.py#L883)。

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
| record_author | `str` | 是 |
| record_author_attr | `str` | 否 |
| record_text | `str` | 是 |
| challenge | `str` | 是 |
| logged_out | `str` | 是 |
| mention_input | `str` | 否 |
| mention_options | `str` | 否 |
| mention_nodes | `str` | 否 |
| mention_uid_attr | `str` | 否 |
| record_mentions | `str` | 否 |

### Payload

定位：[agent/src/omni/domain/configuration.py](../../../agent/src/omni/domain/configuration.py#L46)。

| 字段 | 类型 | 必填 |
|---|---|---|

### ModelOptions

定位：[agent/src/omni/domain/configuration.py](../../../agent/src/omni/domain/configuration.py#L50)。

| 字段 | 类型 | 必填 |
|---|---|---|
| model | `str` | 否 |
| base_url | `str` | 否 |
| credential_ref | `str` | 否 |
| context_tokens | `int` | 否 |
| output_tokens | `int` | 否 |
| summary_tokens | `int` | 否 |
| margin_tokens | `int` | 否 |
| counter_mode | `Literal['utf8_upper_bound', 'required']` | 否 |
| counter_ref | `str` | 否 |
| extra_headers | `dict[str, str]` | 否 |

### ChannelOptions

定位：[agent/src/omni/domain/configuration.py](../../../agent/src/omni/domain/configuration.py#L82)。

| 字段 | 类型 | 必填 |
|---|---|---|
| driver | `Literal['qq', 'bilibili', 'android']` | 否 |
| endpoint | `str` | 否 |
| account | `str` | 否 |
| credential_ref | `str` | 否 |

### MCPOptions

定位：[agent/src/omni/domain/configuration.py](../../../agent/src/omni/domain/configuration.py#L95)。

| 字段 | 类型 | 必填 |
|---|---|---|
| transport | `Literal['stdio', 'streamable_http']` | 否 |
| command | `str` | 否 |
| arguments | `list[str]` | 否 |
| endpoint | `str` | 否 |
| auth_mode | `Literal['none', 'bearer', 'oauth']` | 否 |
| credential_ref | `str` | 否 |

### PresetOptions

定位：[agent/src/omni/domain/configuration.py](../../../agent/src/omni/domain/configuration.py#L123)。

| 字段 | 类型 | 必填 |
|---|---|---|
| content | `str` | 否 |

### ScheduleValue

定位：[agent/src/omni/domain/scheduling.py](../../../agent/src/omni/domain/scheduling.py#L164)。

| 字段 | 类型 | 必填 |
|---|---|---|
| kind | `Literal['cron', 'interval', 'once']` | 否 |
| timezone | `str` | 否 |
| expression | `str` | 否 |
| interval_seconds | `int \| None` | 否 |
| start_at | `str \| None` | 否 |
| session_id | `str \| None` | 否 |
| prompt | `str` | 是 |
| misfire | `Literal['latest']` | 否 |
| skills | `list[str]` | 否 |
| context_from | `list[str]` | 否 |
| continuity | `bool` | 否 |

### Catalog

定位：[agent/src/omni/system/agent_spaces/catalog.py](../../../agent/src/omni/system/agent_spaces/catalog.py#L28)。

| 字段 | 类型 | 必填 |
|---|---|---|
| version | `int` | 否 |
| active | `str` | 否 |
| application | `ApplicationDirectories` | 否 |
| agents | `list[AgentProfile]` | 否 |

### DirectoryOverrides

定位：[agent/src/omni/system/agent_spaces/layout.py](../../../agent/src/omni/system/agent_spaces/layout.py#L18)。

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

定位：[agent/src/omni/system/agent_spaces/layout.py](../../../agent/src/omni/system/agent_spaces/layout.py#L31)。

| 字段 | 类型 | 必填 |
|---|---|---|
| id | `str` | 是 |
| name | `str` | 是 |
| home | `str` | 否 |
| directories | `DirectoryOverrides` | 否 |

### ApplicationDirectories

定位：[agent/src/omni/system/agent_spaces/layout.py](../../../agent/src/omni/system/agent_spaces/layout.py#L39)。

| 字段 | 类型 | 必填 |
|---|---|---|
| data_root | `str` | 否 |
| workspace | `str` | 否 |
| agents_root | `str` | 否 |

## 静态工具声明

不是启用清单；条件注册和插件工具以 `/api/workbench/tools` 的本进程快照为准。SAFE 不等于 read_only。

| 工具 | 审批等级 | 强制人工 | 并发只读 | 模块 | 定位 |
|---|---|---|---|---|---|
| `android_key` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../../agent/src/omni/tools/android/tools.py#L209) |
| `android_launch_app` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../../agent/src/omni/tools/android/tools.py#L280) |
| `android_list_apps` | safe | False | True | android | [agent/src/omni/tools/android/tools.py](../../../agent/src/omni/tools/android/tools.py#L269) |
| `android_long_press` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../../agent/src/omni/tools/android/tools.py#L299) |
| `android_screen_tree` | safe | False | False | android | [agent/src/omni/tools/android/tools.py](../../../agent/src/omni/tools/android/tools.py#L109) |
| `android_screenshot` | safe | False | False | android | [agent/src/omni/tools/android/tools.py](../../../agent/src/omni/tools/android/tools.py#L130) |
| `android_status` | safe | False | False | android | [agent/src/omni/tools/android/tools.py](../../../agent/src/omni/tools/android/tools.py#L93) |
| `android_swipe` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../../agent/src/omni/tools/android/tools.py#L191) |
| `android_tap_coords` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../../agent/src/omni/tools/android/tools.py#L172) |
| `android_tap_element` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../../agent/src/omni/tools/android/tools.py#L146) |
| `android_type` | confirm | True | False | android | [agent/src/omni/tools/android/tools.py](../../../agent/src/omni/tools/android/tools.py#L227) |
| `bilibili_account_info` | safe | False | True | bilibili | [agent/src/omni/tools/bilibili/tools.py](../../../agent/src/omni/tools/bilibili/tools.py#L242) |
| `bilibili_comment_replies` | safe | False | True | bilibili | [agent/src/omni/tools/bilibili/tools.py](../../../agent/src/omni/tools/bilibili/tools.py#L310) |
| `bilibili_user_search` | safe | False | True | bilibili | [agent/src/omni/tools/bilibili/tools.py](../../../agent/src/omni/tools/bilibili/tools.py#L329) |
| `bilibili_video_comments` | safe | False | True | bilibili | [agent/src/omni/tools/bilibili/tools.py](../../../agent/src/omni/tools/bilibili/tools.py#L284) |
| `bilibili_video_frames` | safe | False | True | bilibili | [agent/src/omni/tools/bilibili/tools.py](../../../agent/src/omni/tools/bilibili/tools.py#L255) |
| `bilibili_video_info` | safe | False | True | bilibili | [agent/src/omni/tools/bilibili/tools.py](../../../agent/src/omni/tools/bilibili/tools.py#L224) |
| `bilibili_video_subtitle` | safe | False | True | bilibili | [agent/src/omni/tools/bilibili/tools.py](../../../agent/src/omni/tools/bilibili/tools.py#L347) |
| `bilibili_video_transcript` | safe | False | True | bilibili | [agent/src/omni/tools/bilibili/tools.py](../../../agent/src/omni/tools/bilibili/tools.py#L362) |
| `browser_click` | confirm | True | False | browser | [agent/src/omni/tools/browser/tools.py](../../../agent/src/omni/tools/browser/tools.py#L168) |
| `browser_navigate` | safe | False | False | browser | [agent/src/omni/tools/browser/tools.py](../../../agent/src/omni/tools/browser/tools.py#L134) |
| `browser_read` | safe | False | False | browser | [agent/src/omni/tools/browser/tools.py](../../../agent/src/omni/tools/browser/tools.py#L148) |
| `browser_screenshot` | safe | False | False | browser | [agent/src/omni/tools/browser/tools.py](../../../agent/src/omni/tools/browser/tools.py#L158) |
| `browser_type` | confirm | True | False | browser | [agent/src/omni/tools/browser/tools.py](../../../agent/src/omni/tools/browser/tools.py#L183) |
| `community_config` | safe | False | False | community | [agent/src/omni/plugins/community/tools.py](../../../agent/src/omni/plugins/community/tools.py#L54) |
| `community_info` | safe | False | True | community | [agent/src/omni/plugins/community/tools.py](../../../agent/src/omni/plugins/community/tools.py#L88) |
| `community_install` | safe | False | False | community | [agent/src/omni/plugins/community/tools.py](../../../agent/src/omni/plugins/community/tools.py#L103) |
| `community_publish` | confirm | True | False | community | [agent/src/omni/plugins/community/tools.py](../../../agent/src/omni/plugins/community/tools.py#L120) |
| `community_search` | safe | False | True | community | [agent/src/omni/plugins/community/tools.py](../../../agent/src/omni/plugins/community/tools.py#L70) |
| `cron_add` | safe | False | False | scheduling | [agent/src/omni/tools/scheduling/tools.py](../../../agent/src/omni/tools/scheduling/tools.py#L197) |
| `cron_edit` | safe | False | False | scheduling | [agent/src/omni/tools/scheduling/tools.py](../../../agent/src/omni/tools/scheduling/tools.py#L286) |
| `cron_list` | safe | False | True | scheduling | [agent/src/omni/tools/scheduling/tools.py](../../../agent/src/omni/tools/scheduling/tools.py#L186) |
| `cron_once` | safe | False | False | scheduling | [agent/src/omni/tools/scheduling/tools.py](../../../agent/src/omni/tools/scheduling/tools.py#L238) |
| `cron_remove` | safe | False | False | scheduling | [agent/src/omni/tools/scheduling/tools.py](../../../agent/src/omni/tools/scheduling/tools.py#L272) |
| `daily_digest` | safe | False | True | personal | [agent/src/omni/tools/personal/digest.py](../../../agent/src/omni/tools/personal/digest.py#L47) |
| `delegate_task` | safe | False | False | delegation | [agent/src/omni/tools/delegation/tools.py](../../../agent/src/omni/tools/delegation/tools.py#L97) |
| `dialogue_read` | safe | False | True | personal | [agent/src/omni/tools/personal/transcript.py](../../../agent/src/omni/tools/personal/transcript.py#L143) |
| `dialogue_search` | safe | False | True | personal | [agent/src/omni/tools/personal/transcript.py](../../../agent/src/omni/tools/personal/transcript.py#L122) |
| `files_diff` | safe | False | True | files | [agent/src/omni/tools/files/tools.py](../../../agent/src/omni/tools/files/tools.py#L465) |
| `files_history` | safe | False | True | files | [agent/src/omni/tools/files/tools.py](../../../agent/src/omni/tools/files/tools.py#L536) |
| `files_list` | safe | False | True | files | [agent/src/omni/tools/files/tools.py](../../../agent/src/omni/tools/files/tools.py#L407) |
| `files_read` | safe | False | True | files | [agent/src/omni/tools/files/tools.py](../../../agent/src/omni/tools/files/tools.py#L446) |
| `files_restore` | confirm | True | False | files | [agent/src/omni/tools/files/tools.py](../../../agent/src/omni/tools/files/tools.py#L551) |
| `files_search` | safe | False | True | files | [agent/src/omni/tools/files/tools.py](../../../agent/src/omni/tools/files/tools.py#L424) |
| `files_write` | confirm | True | False | files | [agent/src/omni/tools/files/tools.py](../../../agent/src/omni/tools/files/tools.py#L492) |
| `forget` | confirm | False | False | personal | [agent/src/omni/tools/personal/facts.py](../../../agent/src/omni/tools/personal/facts.py#L217) |
| `get_time` | safe | False | True | personal | [agent/src/omni/tools/personal/clock.py](../../../agent/src/omni/tools/personal/clock.py#L16) |
| `journal_view` | safe | False | True | personal | [agent/src/omni/tools/personal/journal.py](../../../agent/src/omni/tools/personal/journal.py#L43) |
| `mcp_call_tool` | confirm | True | False | mcp | [agent/src/omni/tools/mcp/tools.py](../../../agent/src/omni/tools/mcp/tools.py#L98) |
| `mcp_list_tools` | safe | False | True | mcp | [agent/src/omni/tools/mcp/tools.py](../../../agent/src/omni/tools/mcp/tools.py#L41) |
| `mcp_read_tool` | safe | False | True | mcp | [agent/src/omni/tools/mcp/tools.py](../../../agent/src/omni/tools/mcp/tools.py#L87) |
| `peek_command` | safe | False | True | shell | [agent/src/omni/tools/shell/tools.py](../../../agent/src/omni/tools/shell/tools.py#L218) |
| `person_event` | safe | False | False | personal | [agent/src/omni/tools/personal/people.py](../../../agent/src/omni/tools/personal/people.py#L222) |
| `person_lookup` | safe | False | True | personal | [agent/src/omni/tools/personal/people.py](../../../agent/src/omni/tools/personal/people.py#L167) |
| `person_remember` | safe | False | False | personal | [agent/src/omni/tools/personal/people.py](../../../agent/src/omni/tools/personal/people.py#L189) |
| `plugin_activate` | confirm | True | False | plugins | [agent/src/omni/plugins/tools.py](../../../agent/src/omni/plugins/tools.py#L116) |
| `plugin_disable` | safe | False | False | plugins | [agent/src/omni/plugins/tools.py](../../../agent/src/omni/plugins/tools.py#L149) |
| `plugin_enable` | safe | False | False | plugins | [agent/src/omni/plugins/tools.py](../../../agent/src/omni/plugins/tools.py#L135) |
| `plugin_install` | safe | False | False | plugins | [agent/src/omni/plugins/tools.py](../../../agent/src/omni/plugins/tools.py#L95) |
| `plugin_list` | safe | False | True | plugins | [agent/src/omni/plugins/tools.py](../../../agent/src/omni/plugins/tools.py#L80) |
| `plugin_remove` | confirm | True | False | plugins | [agent/src/omni/plugins/tools.py](../../../agent/src/omni/plugins/tools.py#L165) |
| `profile_manage` | safe | False | False | personal | [agent/src/omni/tools/personal/profile.py](../../../agent/src/omni/tools/personal/profile.py#L97) |
| `qq_send_message` | safe | False | False | qq-tools | [agent/src/omni/tools/qq/tools.py](../../../agent/src/omni/tools/qq/tools.py#L108) |
| `recall` | safe | False | False | personal | [agent/src/omni/tools/personal/facts.py](../../../agent/src/omni/tools/personal/facts.py#L201) |
| `remember` | safe | False | False | personal | [agent/src/omni/tools/personal/facts.py](../../../agent/src/omni/tools/personal/facts.py#L181) |
| `resident_memory` | safe | False | False | personal | [agent/src/omni/tools/personal/resident.py](../../../agent/src/omni/tools/personal/resident.py#L44) |
| `run_command` | confirm | True | False | shell | [agent/src/omni/tools/shell/tools.py](../../../agent/src/omni/tools/shell/tools.py#L244) |
| `search_history` | safe | False | True | personal | [agent/src/omni/tools/personal/facts.py](../../../agent/src/omni/tools/personal/facts.py#L246) |
| `session_log_read` | safe | False | True | personal | [agent/src/omni/tools/personal/transcript.py](../../../agent/src/omni/tools/personal/transcript.py#L186) |
| `session_log_search` | safe | False | True | personal | [agent/src/omni/tools/personal/transcript.py](../../../agent/src/omni/tools/personal/transcript.py#L166) |
| `session_refine` | safe | False | False | personal | [agent/src/omni/tools/personal/refine.py](../../../agent/src/omni/tools/personal/refine.py#L44) |
| `shell_job_cancel` | confirm | True | False | command-jobs | [agent/src/omni/tools/shell/job_tools.py](../../../agent/src/omni/tools/shell/job_tools.py#L107) |
| `shell_job_list` | safe | False | True | command-jobs | [agent/src/omni/tools/shell/job_tools.py](../../../agent/src/omni/tools/shell/job_tools.py#L85) |
| `shell_job_read` | safe | False | True | command-jobs | [agent/src/omni/tools/shell/job_tools.py](../../../agent/src/omni/tools/shell/job_tools.py#L96) |
| `shell_job_start` | confirm | True | False | command-jobs | [agent/src/omni/tools/shell/job_tools.py](../../../agent/src/omni/tools/shell/job_tools.py#L74) |
| `skill_activate` | confirm | True | False | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L423) |
| `skill_archive` | confirm | True | False | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L306) |
| `skill_create` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L189) |
| `skill_discard` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L440) |
| `skill_history` | safe | False | True | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L262) |
| `skill_import` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L388) |
| `skill_patch` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L212) |
| `skill_pending` | safe | False | True | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L412) |
| `skill_restore` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L328) |
| `skill_rewrite` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L239) |
| `skill_rollback` | safe | False | False | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L283) |
| `skill_view` | safe | False | True | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L171) |
| `skills_list` | safe | False | True | skills | [agent/src/omni/tools/skills/tools.py](../../../agent/src/omni/tools/skills/tools.py#L160) |
| `system_service` | confirm | False | False | system-tools | [agent/src/omni/tools/system/tools.py](../../../agent/src/omni/tools/system/tools.py#L98) |
| `system_status` | safe | False | True | system-tools | [agent/src/omni/tools/system/tools.py](../../../agent/src/omni/tools/system/tools.py#L87) |
| `todo` | safe | False | False | personal | [agent/src/omni/tools/personal/todos.py](../../../agent/src/omni/tools/personal/todos.py#L60) |
| `video_storyboard` | safe | False | True | storyboard | [agent/src/omni/tools/storyboard/tools.py](../../../agent/src/omni/tools/storyboard/tools.py#L204) |
| `video_zoom` | safe | False | True | storyboard | [agent/src/omni/tools/storyboard/tools.py](../../../agent/src/omni/tools/storyboard/tools.py#L234) |
| `web_read` | safe | False | True | web-fetch | [agent/src/omni/tools/web/tools.py](../../../agent/src/omni/tools/web/tools.py#L114) |
