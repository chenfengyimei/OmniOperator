# API、对话调用与工具声明

> 由 `uv run --no-sync python scripts/project_index.py` 生成。不要手改；分类改 system/catalog/schema.py，分层改 ARCHITECTURE.md。

工作台 API 统一受配对/Host/Origin 规则保护；终端 WS 单独握手校验。社区服务器是独立服务。请求字段为源码声明，完整 schema 可查询 FastAPI OpenAPI。

## 对话与模型

Web/QQ/CLI 的请求经 Runtime 进入同一内核，模型请求经 Gateway 记账。

`create_app.chat`（[src/omni/channels/web.py](../../src/omni/channels/web.py#L432)） → `Agent.run_turn`（[src/omni/core/agent.py](../../src/omni/core/agent.py#L231)） → `Agent._chat`（[src/omni/core/agent.py](../../src/omni/core/agent.py#L407)） → `Gateway.complete`（[src/omni/llm/gateway.py](../../src/omni/llm/gateway.py#L339)）

## 模型调用工具

模型工具调用经统一注册表和审批；结果按原调用顺序回填。

`Agent._execute_tool_calls`（[src/omni/core/agent.py](../../src/omni/core/agent.py#L502)） → `ToolRegistry.execute`（[src/omni/tools/registry.py](../../src/omni/tools/registry.py#L142)） → `WebApprover`（[src/omni/safety/gates.py](../../src/omni/safety/gates.py#L123)） → `ToolResult`（[src/omni/tools/result.py](../../src/omni/tools/result.py#L35)）

## 流式与取消

流式响应和持久运行事件是两条接口；停止通过协作取消传播。

`create_app.chat_stream`（[src/omni/channels/web.py](../../src/omni/channels/web.py#L463)） → `Agent.run_turn`（[src/omni/core/agent.py](../../src/omni/core/agent.py#L231)） → `Gateway.stream`（[src/omni/llm/gateway.py](../../src/omni/llm/gateway.py#L425)） → `RunStore`（[src/omni/memory/runs.py](../../src/omni/memory/runs.py#L87)）

## 交互式终端

WebSocket 先校验身份，再由会话池创建独立 ConPTY；断开时核查退出。

`create_terminal_router.terminal_ws`（[src/omni/channels/terminal/routes.py](../../src/omni/channels/terminal/routes.py#L14)） → `WebAuth.check_websocket`（[src/omni/channels/web_auth.py](../../src/omni/channels/web_auth.py#L218)） → `TerminalManager.open`（[src/omni/channels/terminal/pool.py](../../src/omni/channels/terminal/pool.py#L27)） → `TerminalSession`（[src/omni/channels/terminal/session.py](../../src/omni/channels/terminal/session.py#L8)） → `open_process`（[src/omni/channels/terminal/backend.py](../../src/omni/channels/terminal/backend.py#L18)）

## 插件扩展接入

下载/收包、隔离校验、人工激活、命名空间注册，最终仍由工具审批链执行。

`CommunityClient.install`（[src/omni/plugins/community/client.py](../../src/omni/plugins/community/client.py#L159)） → `PluginHost.install_package`（[src/omni/plugins/host.py](../../src/omni/plugins/host.py#L213)） → `PluginHost.activate`（[src/omni/plugins/host.py](../../src/omni/plugins/host.py#L506)） → `PluginContext.add_tool`（[src/omni/plugins/host.py](../../src/omni/plugins/host.py#L129)） → `ToolRegistry.execute`（[src/omni/tools/registry.py](../../src/omni/tools/registry.py#L142)）

## 技能数据接入

技能是提示与参考数据；导入不执行脚本，激活需人工确认。

`register_skill_inbox_tools`（[src/omni/tools/skills/tools.py](../../src/omni/tools/skills/tools.py#L287)） → `SkillInbox`（[src/omni/memory/skill_inbox.py](../../src/omni/memory/skill_inbox.py#L88)） → `SkillStore`（[src/omni/memory/skills.py](../../src/omni/memory/skills.py#L81)）

## 外部 MCP 能力

渐进发现与授权校验后调用外部进程；结果未知不重放写操作。

`register_mcp_tools`（[src/omni/tools/mcp/tools.py](../../src/omni/tools/mcp/tools.py#L18)） → `MCPManager`（[src/omni/tools/mcp/client.py](../../src/omni/tools/mcp/client.py#L290)） → `MCPSchemaStore`（[src/omni/memory/state.py](../../src/omni/memory/state.py#L1509)）

## 声明路由

| 服务 | 方法 | 路径 | 处理函数 | 定位 |
|---|---|---|---|---|
| community | GET | `/api/download/{plugin_id}` | `create_app.download` | [src/omni/plugins/community/server.py](../../src/omni/plugins/community/server.py#L146) |
| community | GET | `/api/health` | `create_app.health` | [src/omni/plugins/community/server.py](../../src/omni/plugins/community/server.py#L112) |
| community | GET | `/api/plugins` | `create_app.search_plugins` | [src/omni/plugins/community/server.py](../../src/omni/plugins/community/server.py#L116) |
| community | GET | `/api/plugins/{plugin_id}` | `create_app.plugin_detail` | [src/omni/plugins/community/server.py](../../src/omni/plugins/community/server.py#L135) |
| community | POST | `/api/publish` | `create_app.publish` | [src/omni/plugins/community/server.py](../../src/omni/plugins/community/server.py#L174) |
| workbench | POST | `/api/android/element` | `create_app.android_element` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1657) |
| workbench | POST | `/api/android/key` | `create_app.android_key` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1667) |
| workbench | GET | `/api/android/snapshot` | `create_app.android_snapshot` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1626) |
| workbench | GET | `/api/android/state` | `create_app.android_state` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1598) |
| workbench | GET | `/api/android/stream` | `create_app.android_stream` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1676) |
| workbench | POST | `/api/android/tap` | `create_app.android_tap` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1643) |
| workbench | GET | `/api/android/tree` | `create_app.android_tree` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1607) |
| workbench | GET | `/api/approvals` | `create_app.get_approvals` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1041) |
| workbench | POST | `/api/approvals/{approval_id}/resolve` | `create_app.resolve_approval` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1049) |
| workbench | GET | `/api/artifacts` | `create_app.list_artifacts` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L985) |
| workbench | DELETE | `/api/artifacts/{artifact_id}` | `create_app.delete_artifact` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1027) |
| workbench | GET | `/api/artifacts/{artifact_id}/image` | `create_app.artifact_image` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1014) |
| workbench | POST | `/api/auth/logout` | `create_app.auth_logout` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L307) |
| workbench | POST | `/api/auth/pair` | `create_app.auth_pair` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L292) |
| workbench | GET | `/api/auth/status` | `create_app.auth_status` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L284) |
| workbench | POST | `/api/chat` | `create_app.chat` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L432) |
| workbench | POST | `/api/chat/stream` | `create_app.chat_stream` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L463) |
| workbench | GET | `/api/desktop/observe/{window}` | `create_app.desktop_observe` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1587) |
| workbench | GET | `/api/desktop/windows` | `create_app.desktop_windows` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1578) |
| workbench | GET | `/api/facts` | `create_app.get_facts` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L660) |
| workbench | GET | `/api/facts/detail` | `create_app.get_facts_detail` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L664) |
| workbench | DELETE | `/api/facts/{fact_id}` | `create_app.delete_fact` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L669) |
| workbench | GET | `/api/llm/usage` | `create_app.llm_usage` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L626) |
| workbench | GET | `/api/mcp/overview` | `create_app.mcp_overview` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1060) |
| workbench | POST | `/api/mcp/{server}/confirm-schema` | `create_app.mcp_confirm_schema` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1116) |
| workbench | POST | `/api/mcp/{server}/reset` | `create_app.mcp_reset` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1132) |
| workbench | GET | `/api/mcp/{server}/tools` | `create_app.mcp_tools` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1085) |
| workbench | GET | `/api/memory/retrieval` | `create_app.retrieval_status` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1572) |
| workbench | GET | `/api/memory/status` | `create_app.memory_status` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L676) |
| workbench | GET | `/api/profile` | `create_app.get_profile` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L699) |
| workbench | PUT | `/api/profile/line` | `create_app.profile_line_update` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L710) |
| workbench | DELETE | `/api/profile/line/{line_id}` | `create_app.profile_line_delete` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L719) |
| workbench | GET | `/api/publications` | `create_app.publications` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1530) |
| workbench | POST | `/api/publications/{identity}/edit` | `create_app.publication_edit` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1538) |
| workbench | POST | `/api/publications/{identity}/resolve` | `create_app.publication_resolve` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1552) |
| workbench | GET | `/api/resident` | `create_app.get_resident` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L725) |
| workbench | POST | `/api/resident/update` | `create_app.resident_update` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L735) |
| workbench | GET | `/api/reviews` | `create_app.get_reviews` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L756) |
| workbench | GET | `/api/runs` | `create_app.run_list` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L575) |
| workbench | POST | `/api/runs` | `create_app.run_create` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L552) |
| workbench | GET | `/api/runs/{run_id}` | `create_app.run_get` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L583) |
| workbench | POST | `/api/runs/{run_id}/cancel` | `create_app.run_cancel` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L602) |
| workbench | GET | `/api/runs/{run_id}/events` | `create_app.run_events` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L590) |
| workbench | GET | `/api/sessions` | `create_app.get_sessions` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L359) |
| workbench | POST | `/api/sessions` | `create_app.create_session` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L373) |
| workbench | GET | `/api/sessions/{session_id}/messages` | `create_app.get_messages` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L383) |
| workbench | POST | `/api/sessions/{session_id}/select` | `create_app.select_session` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L377) |
| workbench | GET | `/api/sessions/{session_id}/todos` | `create_app.get_todos` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L395) |
| workbench | GET | `/api/skills` | `create_app.get_skills` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L766) |
| workbench | POST | `/api/skills/ack-all` | `create_app.skills_ack_all` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L821) |
| workbench | GET | `/api/skills/pending` | `create_app.skills_pending` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L782) |
| workbench | GET | `/api/skills/pending/{name}/diff` | `create_app.skill_pending_diff` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L845) |
| workbench | GET | `/api/skills/{name}` | `create_app.get_skill` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L869) |
| workbench | POST | `/api/skills/{name}/ack` | `create_app.skill_ack` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L836) |
| workbench | GET | `/api/skills/{name}/diff` | `create_app.skill_diff` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L943) |
| workbench | GET | `/api/skills/{name}/history` | `create_app.skill_history` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L913) |
| workbench | GET | `/api/skills/{name}/revision/{rev}` | `create_app.skill_revision` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L933) |
| workbench | POST | `/api/skills/{name}/rollback` | `create_app.skill_rollback` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L963) |
| workbench | POST | `/api/skills/{name}/save` | `create_app.skill_save` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L891) |
| workbench | GET | `/api/state` | `create_app.get_state` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L336) |
| workbench | POST | `/api/system/backup` | `create_app.system_backup` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1152) |
| workbench | GET | `/api/system/bili-cookies` | `create_app.bili_cookies_status` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1271) |
| workbench | POST | `/api/system/bili-cookies` | `create_app.save_bili_cookies` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1283) |
| workbench | GET | `/api/system/config` | `create_app.system_config` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1184) |
| workbench | POST | `/api/system/config` | `create_app.system_config_save` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1230) |
| workbench | GET | `/api/system/cron` | `create_app.cron_list` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1374) |
| workbench | POST | `/api/system/cron/create` | `create_app.cron_create` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1337) |
| workbench | POST | `/api/system/cron/delete` | `create_app.cron_delete` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1474) |
| workbench | GET | `/api/system/cron/history` | `create_app.cron_history` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1481) |
| workbench | GET | `/api/system/cron/outbox` | `create_app.cron_outbox` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1491) |
| workbench | POST | `/api/system/cron/outbox/{entry_id}/discard` | `create_app.cron_outbox_discard` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1518) |
| workbench | POST | `/api/system/cron/outbox/{entry_id}/retry` | `create_app.cron_outbox_retry` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1506) |
| workbench | POST | `/api/system/cron/toggle` | `create_app.cron_toggle` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1458) |
| workbench | POST | `/api/system/cron/update` | `create_app.cron_update` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1408) |
| workbench | GET | `/api/system/doctor` | `create_app.system_doctor` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1148) |
| workbench | GET | `/api/system/logs/{name}` | `create_app.system_logs` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1180) |
| workbench | POST | `/api/system/model-test` | `create_app.model_test` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1305) |
| workbench | GET | `/api/system/napcat/qr` | `create_app.system_napcat_qr` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1176) |
| workbench | GET | `/api/system/overview` | `create_app.system_overview` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1144) |
| workbench | GET | `/api/system/qq-policy` | `create_app.qq_policy_status` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1250) |
| workbench | POST | `/api/system/qq-policy/stop` | `create_app.qq_policy_stop` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1261) |
| workbench | POST | `/api/system/services/{name}/start` | `create_app.system_service_start` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1160) |
| workbench | POST | `/api/system/services/{name}/stop` | `create_app.system_service_stop` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1169) |
| workbench | GET | `/api/system/state` | `create_app.system_state` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L1567) |
| workbench | WEBSOCKET | `/api/terminal/ws` | `create_terminal_router.terminal_ws` | [src/omni/channels/terminal/routes.py](../../src/omni/channels/terminal/routes.py#L14) |
| workbench | POST | `/api/todos/toggle` | `create_app.toggle_todo` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L407) |
| workbench | GET | `/api/workbench/docs/{name}` | `create_workbench_router.document` | [src/omni/channels/workbench/routes.py](../../src/omni/channels/workbench/routes.py#L39) |
| workbench | GET | `/api/workbench/extensions` | `create_workbench_router.extensions` | [src/omni/channels/workbench/routes.py](../../src/omni/channels/workbench/routes.py#L57) |
| workbench | GET | `/api/workbench/project` | `create_workbench_router.project` | [src/omni/channels/workbench/routes.py](../../src/omni/channels/workbench/routes.py#L35) |
| workbench | GET | `/api/workbench/tools` | `create_workbench_router.tools` | [src/omni/channels/workbench/routes.py](../../src/omni/channels/workbench/routes.py#L50) |
| workbench | GET | `/pair/{code}` | `create_app.pair_link` | [src/omni/channels/web.py](../../src/omni/channels/web.py#L313) |

## 请求模型

### ChatIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L59)。

| 字段 | 类型 | 必填 |
|---|---|---|
| message | `str` | 是 |
| session_id | `int \| None` | 否 |

### ResolveIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L64)。

| 字段 | 类型 | 必填 |
|---|---|---|
| allowed | `bool` | 是 |
| reason | `str` | 否 |

### NapCatStartIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L69)。

| 字段 | 类型 | 必填 |
|---|---|---|
| qq | `str` | 否 |

### ConfigUpdateIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L73)。

| 字段 | 类型 | 必填 |
|---|---|---|
| updates | `dict[str, dict[str, Any]]` | 是 |

### ModelTestIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L77)。

| 字段 | 类型 | 必填 |
|---|---|---|
| model | `str` | 是 |

### MCPSchemaConfirmIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L81)。

| 字段 | 类型 | 必填 |
|---|---|---|
| fingerprint | `str` | 是 |

### BiliCookiesIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L85)。

| 字段 | 类型 | 必填 |
|---|---|---|
| sessdata | `str` | 否 |
| bili_jct | `str` | 否 |
| buvid3 | `str` | 否 |

### CronNameIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L91)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |

### CronToggleIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L95)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 是 |
| enabled | `bool` | 是 |

### CronCreateIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L100)。

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

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L112)。

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

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L128)。

| 字段 | 类型 | 必填 |
|---|---|---|
| session_id | `int` | 是 |
| todo_id | `int` | 是 |
| done | `bool` | 是 |

### AndroidTapIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L134)。

| 字段 | 类型 | 必填 |
|---|---|---|
| x | `int` | 是 |
| y | `int` | 是 |
| stream_w | `int` | 是 |
| stream_h | `int` | 是 |
| observation | `str` | 否 |

### AndroidElementIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L142)。

| 字段 | 类型 | 必填 |
|---|---|---|
| idx | `int` | 是 |
| observation | `str` | 否 |

### AndroidKeyIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L147)。

| 字段 | 类型 | 必填 |
|---|---|---|
| keycode | `str` | 是 |
| observation | `str` | 否 |

### ProfileLineIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L152)。

| 字段 | 类型 | 必填 |
|---|---|---|
| line_id | `int` | 是 |
| content | `str` | 是 |

### ResidentUpdateIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L157)。

| 字段 | 类型 | 必填 |
|---|---|---|
| operation | `str` | 是 |
| id | `int \| None` | 否 |
| content | `str` | 否 |

### PairIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L163)。

| 字段 | 类型 | 必填 |
|---|---|---|
| code | `str` | 是 |

### SkillSaveIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L167)。

| 字段 | 类型 | 必填 |
|---|---|---|
| content | `str` | 是 |
| expected_hash | `str \| None` | 否 |

### SkillRollbackIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L174)。

| 字段 | 类型 | 必填 |
|---|---|---|
| rev | `int` | 是 |
| expected_hash | `str \| None` | 否 |

### SkillAckIn

定位：[src/omni/channels/web.py](../../src/omni/channels/web.py#L179)。

| 字段 | 类型 | 必填 |
|---|---|---|
| rev | `int` | 是 |

### Persona

定位：[src/omni/config.py](../../src/omni/config.py#L106)。

| 字段 | 类型 | 必填 |
|---|---|---|
| name | `str` | 否 |
| system_prompt | `str` | 否 |

### Models

定位：[src/omni/config.py](../../src/omni/config.py#L113)。

| 字段 | 类型 | 必填 |
|---|---|---|
| chat | `str` | 否 |
| vision | `str` | 否 |
| local | `str` | 否 |
| review | `str` | 否 |

### ModelCapability

定位：[src/omni/config.py](../../src/omni/config.py#L120)。

| 字段 | 类型 | 必填 |
|---|---|---|
| tools | `bool` | 否 |
| vision | `bool` | 否 |
| stream | `bool` | 否 |
| context_tokens | `int` | 否 |
| input_usd_per_m | `float \| None` | 否 |
| output_usd_per_m | `float \| None` | 否 |

### Retry

定位：[src/omni/config.py](../../src/omni/config.py#L138)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_attempts | `int` | 否 |
| total_deadline_s | `float` | 否 |
| backoff_s | `float` | 否 |

### LLMBudget

定位：[src/omni/config.py](../../src/omni/config.py#L146)。

| 字段 | 类型 | 必填 |
|---|---|---|
| daily_usd | `float` | 否 |
| per_run_usd | `float` | 否 |

### Vision

定位：[src/omni/config.py](../../src/omni/config.py#L158)。

| 字段 | 类型 | 必填 |
|---|---|---|
| send_to_cloud | `bool` | 否 |
| max_image_bytes | `int` | 否 |
| max_images_per_turn | `int` | 否 |
| max_edge_px | `int` | 否 |

### Artifacts

定位：[src/omni/config.py](../../src/omni/config.py#L172)。

| 字段 | 类型 | 必填 |
|---|---|---|
| retention_days | `int` | 否 |
| max_total_mb | `int` | 否 |

### LLM

定位：[src/omni/config.py](../../src/omni/config.py#L180)。

| 字段 | 类型 | 必填 |
|---|---|---|
| request_timeout_s | `float` | 否 |
| max_output_tokens | `int` | 否 |
| retry | `Retry` | 否 |
| budget | `LLMBudget` | 否 |
| capabilities | `dict[str, ModelCapability]` | 否 |

### Limits

定位：[src/omni/config.py](../../src/omni/config.py#L193)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_history_messages | `int` | 否 |
| max_agent_steps | `int` | 否 |
| resident_memory_chars | `int` | 否 |
| session_queue_depth | `int` | 否 |
| session_lease_ttl_s | `int` | 否 |

### Review

定位：[src/omni/config.py](../../src/omni/config.py#L201)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| max_messages | `int` | 否 |
| steps | `int` | 否 |
| queue_depth | `int` | 否 |
| min_messages | `int` | 否 |

### Memory

定位：[src/omni/config.py](../../src/omni/config.py#L211)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_writes_per_day | `int` | 否 |
| max_fact_chars | `int` | 否 |
| recall_prefix | `bool` | 否 |

### Web

定位：[src/omni/config.py](../../src/omni/config.py#L223)。

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

定位：[src/omni/config.py](../../src/omni/config.py#L240)。

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

定位：[src/omni/config.py](../../src/omni/config.py#L258)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| trigger_chars | `int` | 否 |
| keep_recent | `int` | 否 |
| min_messages | `int` | 否 |

### Asr

定位：[src/omni/config.py](../../src/omni/config.py#L267)。

| 字段 | 类型 | 必填 |
|---|---|---|
| model | `str` | 否 |
| device | `str` | 否 |
| compute_type | `str` | 否 |

### QQ

定位：[src/omni/config.py](../../src/omni/config.py#L275)。

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

定位：[src/omni/config.py](../../src/omni/config.py#L312)。

| 字段 | 类型 | 必填 |
|---|---|---|
| headless | `bool` | 否 |
| slow_mo_ms | `int` | 否 |
| max_writes_per_day | `int` | 否 |
| approval_timeout_s | `float` | 否 |

### UIFormSelectors

定位：[src/omni/config.py](../../src/omni/config.py#L321)。

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

定位：[src/omni/config.py](../../src/omni/config.py#L339)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| forms | `dict[str, UIFormSelectors]` | 否 |
| draft_ttl_s | `int` | 否 |
| max_content_chars | `int` | 否 |

### System

定位：[src/omni/config.py](../../src/omni/config.py#L355)。

| 字段 | 类型 | 必填 |
|---|---|---|
| napcat_dir | `str` | 否 |

### Approval

定位：[src/omni/config.py](../../src/omni/config.py#L361)。

| 字段 | 类型 | 必填 |
|---|---|---|
| mode | `str` | 否 |

### MCPServerConfig

定位：[src/omni/config.py](../../src/omni/config.py#L371)。

| 字段 | 类型 | 必填 |
|---|---|---|
| command | `str` | 否 |
| args | `list[str]` | 否 |
| env | `dict[str, str]` | 否 |
| cwd | `str \| None` | 否 |
| enabled | `bool` | 否 |
| timeout_s | `float` | 否 |
| allowed_tools | `list[str] \| None` | 否 |
| read_only_tools | `list[str]` | 否 |

### MCPConfig

定位：[src/omni/config.py](../../src/omni/config.py#L398)。

| 字段 | 类型 | 必填 |
|---|---|---|
| servers | `dict[str, MCPServerConfig]` | 否 |

### Desktop

定位：[src/omni/config.py](../../src/omni/config.py#L412)。

| 字段 | 类型 | 必填 |
|---|---|---|
| enabled | `bool` | 否 |
| allowed_apps | `list[str]` | 否 |
| max_nodes | `int` | 否 |
| timeout_s | `float` | 否 |
| observation_ttl_s | `float` | 否 |
| max_writes_per_day | `int` | 否 |

### Embedding

定位：[src/omni/config.py](../../src/omni/config.py#L429)。

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

定位：[src/omni/config.py](../../src/omni/config.py#L467)。

| 字段 | 类型 | 必填 |
|---|---|---|
| max_running | `int` | 否 |

### AppConfig

定位：[src/omni/config.py](../../src/omni/config.py#L472)。

| 字段 | 类型 | 必填 |
|---|---|---|
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

## 静态工具声明

不是启用清单；条件注册和插件工具以 `/api/workbench/tools` 的本进程快照为准。SAFE 不等于 read_only。

| 工具 | 审批等级 | 强制人工 | 并发只读 | 模块 | 定位 |
|---|---|---|---|---|---|
| `android_key` | confirm | True | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L181) |
| `android_screen_tree` | safe | False | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L96) |
| `android_screenshot` | safe | False | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L111) |
| `android_status` | safe | False | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L86) |
| `android_swipe` | confirm | True | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L164) |
| `android_tap_coords` | confirm | True | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L146) |
| `android_tap_element` | confirm | True | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L121) |
| `android_type` | confirm | True | False | android | [src/omni/tools/android/tools.py](../../src/omni/tools/android/tools.py#L198) |
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
| `mcp_call_tool` | confirm | True | False | mcp | [src/omni/tools/mcp/tools.py](../../src/omni/tools/mcp/tools.py#L72) |
| `mcp_list_tools` | safe | False | True | mcp | [src/omni/tools/mcp/tools.py](../../src/omni/tools/mcp/tools.py#L41) |
| `mcp_read_tool` | safe | False | True | mcp | [src/omni/tools/mcp/tools.py](../../src/omni/tools/mcp/tools.py#L68) |
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
