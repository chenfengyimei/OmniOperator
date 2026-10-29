"""模块分类与关键调用链；分层规则仍以 docs/ARCHITECTURE.md 为准。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    category: str
    prefixes: tuple[str, ...]
    purpose: str
    docs: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()


MODULES = (
    Module('agent-directories', 'Agent 空间与目录', '平台与数据', ('system/agent_spaces/',), '独立空间注册、目录校验、修订并发控制与有效路径解析；不构造执行对象或搬移数据。', ('AGENT_STORAGE.md',), ('test_agent_storage.py',)),
    Module('http-storage', 'Agent 设置与文件管理', 'Web 接口', ('channels/web_api/storage.py','channels/web_static/storage/'), '目录设置、待重启差异与当前空间公开文件区的有界浏览和人工上传。', ('AGENT_STORAGE.md',), ('test_agent_storage.py',)),
    Module('assembly', '运行时组装', '内核与模型', ('bootstrap.py',), '唯一组装根，构造依赖并暴露 Runtime 门面。', ('ARCHITECTURE.md',), ('test_paths.py',)),
    Module('config', '配置与路径契约', '平台与数据', ('config.py',), '配置模型、加载与 RuntimePaths；不包含业务编排。', ('ARCHITECTURE.md',), ('test_config.py',)),
    Module('engine', 'Agent 内核', '内核与模型', ('core/',), '回合、上下文、工具循环、取消、委派约束、review 与调度。', ('ARCHITECTURE.md',), ('test_agent.py', 'test_runs.py', 'test_parallel_tools.py')),
    Module('models', '模型网关与 embedding', '内核与模型', ('llm/',), '模型路由、能力、重试、流式、预算与 embedding 传输。', ('RETRIEVAL.md',), ('test_gateway.py', 'test_usage.py')),
    Module('safety', '审批与权限', '内核与模型', ('safety/',), '风险分级、人工审批与无人值守拒绝。', ('ARCHITECTURE.md',), ('test_safety.py',)),
    Module('storage', '记忆与运行账本', '平台与数据', ('memory/',), '会话/事实/技能/产物及 state.db 事务、迁移、额度、投递。', ('OPERATIONS.md', 'RETRIEVAL.md'), ('test_state.py', 'test_memory.py')),
    Module('terminal', '内置终端', '交互与终端', ('channels/terminal/', 'channels/web_static/terminal/'), 'ConPTY 后端、会话、会话池、WebSocket 四层；与 agent shell 工具分工。', ('ARCHITECTURE.md', 'TERMINAL.md'), ('test_terminal.py', 'test_terminal_managed.py', 'test_doctor_lifecycle.py')),
    Module('workbench', '项目与能力工作台', '交互与终端', ('channels/workbench/', 'channels/web_static/project/'), '项目索引、API、已注册工具和扩展状态的只读 HTTP 适配。', ('PROJECT_INDEX.md',), ('test_project_catalog.py',)),
    Module('ui-shared', '界面公共交互', '交互与终端', ('channels/web_static/ui/',), '统一导航、移动区域切换、有界请求、反馈和确认框；不执行工具或自动启动依赖。', ('UI_QA.md',), ('test_ui_flows.py',)),
    Module('web-ui', 'Web 界面与身份', '交互与终端', ('channels/web.py', 'channels/web_auth.py', 'channels/web_static/'), '工作台业务路由、统一鉴权与静态界面；新专题使用独立 APIRouter。', ('ARCHITECTURE.md', 'UI_QA.md'), ('test_web.py', 'test_web_auth.py', 'test_ui_flows.py')),
    Module('http-sessions', '会话与待办 API', 'Web 接口', ('channels/web_api/sessions.py',), '会话选择、历史、状态和待办；消费注入 Runtime。', ('PROJECT_INDEX.md',), ('test_web_api.py', 'test_sessions.py')),
    Module('http-conversation', '对话与流式 API', 'Web 接口', ('channels/web_api/conversation.py',), '普通/流式对话、显式运行入口；共用会话闸门、租约和取消。', ('PROJECT_INDEX.md',), ('test_web_api.py', 'test_web.py')),
    Module('command-contracts', '命令账本声明', '平台与数据', ('memory/command_schema.py',), 'state.db 命令账本的纯迁移声明，不导入仓储或执行器。', ('TERMINAL.md',), ('test_command_jobs.py',)),
    Module('command-jobs', '后台命令任务', '工具能力', ('tools/shell/jobs.py', 'tools/shell/job_logs.py', 'tools/shell/job_tools.py', 'channels/web_api/command_jobs.py', 'channels/web_static/jobs/'), '审批后的独立命令任务、状态仓储、有限日志、会话隔离与取消；不复用人工 PTY。', ('TERMINAL.md',), ('test_command_jobs.py',)),
    Module('http-runs', '运行与用量 API', 'Web 接口', ('channels/web_api/runs.py',), '运行查询、事件续读、协作取消和费用视图。', ('PROJECT_INDEX.md',), ('test_web.py',)),
    Module('http-memory', '记忆与档案 API', 'Web 接口', ('channels/web_api/memory.py',), '事实、档案、常驻、review 和检索状态。', ('PROJECT_INDEX.md',), ('test_web.py',)),
    Module('http-skills', '技能与审阅 API', 'Web 接口', ('channels/web_api/skills.py',), '技能编辑/修订/差异/回滚和待审阅状态，保留静态路由优先级。', ('PROJECT_INDEX.md',), ('test_web_api.py', 'test_skills_history.py')),
    Module('http-artifacts', '产物 API', 'Web 接口', ('channels/web_api/artifacts.py',), '产物列表、指纹验证读取和清理，统一领域错误映射。', ('PROJECT_INDEX.md',), ('test_artifacts.py',)),
    Module('http-approvals', '审批 API', 'Web 接口', ('channels/web_api/approvals.py',), '待批准事项和人工决定；不构造或绕过审批器。', ('ARCHITECTURE.md',), ('test_web_auth.py',)),
    Module('http-mcp', 'MCP 管理 API', 'Web 接口', ('channels/web_api/mcp.py', 'channels/web_static/mcp/'), '只读状态、显式发现、目录复核和重连。', ('MCP.md',), ('test_mcp.py',)),
    Module('http-operations', '服务与诊断 API', 'Web 接口', ('channels/web_api/operations.py',), '系统概览、doctor、备份、服务启停和日志。', ('OPERATIONS.md',), ('test_web.py',)),
    Module('http-automation', '调度与投递 API', 'Web 接口', ('channels/web_api/automation.py',), '任务创建/编辑/启停、历史和 outbox 人工处理。', ('PROJECT_INDEX.md',), ('test_cron.py',)),
    Module('http-publications', '发布草稿 API', 'Web 接口', ('channels/web_api/publications.py',), '草稿查询、乐观编辑和未知结果核查，不直接发布。', ('BILIBILI_WRITING.md',), ('test_bilibili_publishing.py',)),
    Module('http-capabilities', '能力与诊断面板', 'Web 接口', ('channels/web_api/capabilities.py', 'channels/web_static/capabilities/'), '被动诊断和能力汇总；显式探测分开执行，不自动启动依赖。', ('OPERATIONS.md',), ('test_android_streams.py',)),
    Module('http-android', '安卓设备 API 与面板', 'Web 接口', ('channels/web_api/android.py', 'channels/web_api/android_stream.py', 'channels/web_static/android/'), '按设备固定目标、配对归属的观察与受控应用操作。', ('ANDROID.md',), ('test_android_devices.py',)),
    Module('http-devices', '桌面与安卓 API', 'Web 接口', ('channels/web_api/devices.py',), '窗口观察、设备树/锁帧、受观察约束的手动动作和 MJPEG。', ('ANDROID.md', 'DESKTOP.md'), ('test_web.py', 'test_web_api.py')),
    Module('http-contracts', 'HTTP 请求契约', 'Web 接口', ('channels/web_api/schemas.py',), '公共请求模型；只承载数据声明，旧 web 模型名继续显式转发。', ('PROJECT_INDEX.md',), ('test_web_api.py',)),
    Module('http-routing', 'HTTP 路由装配', 'Web 接口', ('channels/web_api/',), '聚合注入 Runtime 的领域路由，由应用入口统一挂载；不与请求契约合并为同一职责。', ('PROJECT_INDEX.md', 'REFACTORING.md'), ('test_web_api.py',)),
    Module('cli', 'CLI 入口', '交互与终端', ('channels/cli.py', '__main__.py'), '终端对话命令与 Runtime 适配。', ('HANDOFF.md',)),
    Module('qq-channel', 'QQ 入口', '交互与终端', ('channels/qq.py',), 'QQ 场景队列、事件去重和消息收发适配。', ('QQ_SCENARIOS.md',), ('test_qq.py', 'test_qq_scope.py')),
    Module('heartbeat', '心跳入口', '交互与终端', ('channels/heartbeat.py',), '定时领取、执行、投递与周期清理。', ('OPERATIONS.md',), ('test_cron.py',)),
    Module('channels', '入口公共适配', '交互与终端', ('channels/',), '入口生命周期和只读诊断/检索评测命令。', ('OPERATIONS.md',)),
    Module('personal', '个人记忆工具', '工具能力', ('tools/personal/',), '时钟、事实、档案、常驻、todo、摘要与手动整理各自独立。', ('ARCHITECTURE.md',), ('test_tools.py', 'test_profile_tools.py', 'test_resident.py')),
    Module('skills', '技能工具', '插件与扩展', ('tools/skills/',), '技能查询/创建/编辑/回滚与外来技能隔离安装入口。', ('ARCHITECTURE.md',), ('test_skills.py', 'test_skill_inbox.py')),
    Module('scheduling', '调度工具', '工具能力', ('tools/scheduling/',), '周期/一次性任务、编辑、依赖和执行查询。', ('HANDOFF.md',), ('test_cron.py',)),
    Module('delegation', '委派工具', '工具能力', ('tools/delegation/',), '受父预算/权限/取消约束的子任务注册入口。', ('ARCHITECTURE.md',), ('test_delegate.py',)),
    Module('system-tools', '系统服务工具', '工具能力', ('tools/system/',), '状态查询和受审批的服务操作适配。', ('OPERATIONS.md',), ('test_system.py',)),
    Module('shell', 'Agent shell 工具', '工具能力', ('tools/shell/',), '只读命令语法、执行器、硬黑名单与人工确认命令；不复用交互式 PTY。', ('ARCHITECTURE.md',), ('test_shell_tools.py',)),
    Module('files', '文件工具', '工具能力', ('tools/files/',), '允许根内的列表/搜索/读取/差异与受控写入回滚。', ('ARCHITECTURE.md',), ('test_files.py',)),
    Module('web-fetch', '网页读取工具', '工具能力', ('tools/web/',), '公开网页的目标网络、重定向、体积限制与正文提取。', ('ARCHITECTURE.md',), ('test_web_fetch.py',)),
    Module('browser', '浏览器工具', '工具能力', ('tools/browser/',), 'Playwright 所有权、表单协议、观察与写预算。', ('BILIBILI_WRITING.md',), ('test_browser.py', 'test_browser_owner.py')),
    Module('android', '安卓工具', '工具能力', ('tools/android/',), 'ADB、防护、UI 树、观察令牌、锁帧和输入法。', ('ANDROID.md',), ('test_android.py', 'test_android_observations.py')),
    Module('desktop', '桌面工具', '工具能力', ('tools/desktop/',), 'Windows UIAutomation、窗口观察、租约与受控语义动作。', ('DESKTOP.md',), ('test_desktop.py',)),
    Module('bilibili', 'B站工具', '工具能力', ('tools/bilibili/',), '读 API/转写及注入浏览器协议的草稿与受控写。', ('BILIBILI_WRITING.md',), ('test_bilibili_tools.py', 'test_bilibili_publishing.py')),
    Module('qq-tools', 'QQ 工具', '工具能力', ('tools/qq/',), 'OneBot 客户端与白名单内主动消息。', ('QQ_SCENARIOS.md',), ('test_outbox.py',)),
    Module('mcp', 'MCP 扩展适配', '插件与扩展', ('tools/mcp/',), '外部 stdio 工具发现、授权、漂移撤权与子进程生命周期。', ('MCP.md',), ('test_mcp.py',)),
    Module('toolkit', '工具公共协议与兼容层', '工具能力', ('tools/',), 'Registry、ToolResult、SessionRef/RunRef、路径策略、截图协议及旧 import 薄转发。', ('ARCHITECTURE.md',), ('test_tools.py',)),
    Module('community', '插件社区', '插件与扩展', ('plugins/community/', 'plugins/server.py'), '社区客户端、工具适配与独立分发服务器。', ('PLUGIN_SYSTEM.md',), ('test_plugin_community.py',)),
    Module('plugins', '插件宿主', '插件与扩展', ('plugins/',), 'manifest、隔离校验、人工激活、插件注册与生命周期；进程内受信代码。', ('PLUGIN_SYSTEM.md',), ('test_plugins.py',)),
    Module('catalog', '项目索引基础设施', '开发与验证', ('system/catalog/',), '只读 AST 扫描、分类和确定性文档生成；不会执行待索引代码。', ('PROJECT_INDEX.md',), ('test_project_catalog.py',)),
    Module('platform', '平台基础设施', '平台与数据', ('system/',), '服务、进程身份、配置写回、锁/原子文件、日志、doctor、备份与验收。', ('OPERATIONS.md', 'ACCEPTANCE.md'), ('test_backup.py', 'test_process_identity.py')),
    Module('package', 'Python 包入口', '开发与验证', ('__init__.py',), '安装包标识。'),
)


def classify(relative: str) -> Module | None:
    matches = [(len(prefix), item) for item in MODULES for prefix in item.prefixes
               if (prefix.endswith('/') and relative.startswith(prefix)) or relative == prefix]
    return max(matches, key=lambda pair: pair[0])[1] if matches else None


FLOWS = (
    ('chat', '对话与模型', 'Web/QQ/CLI 的请求经 Runtime 进入同一内核，模型请求经 Gateway 记账。', (
        ('channels/web_api/conversation.py', 'create_conversation_router.chat'),
        ('core/agent.py', 'Agent.run_turn'), ('core/agent.py', 'Agent._chat'), ('llm/gateway.py', 'Gateway.complete'))),
    ('tools', '模型调用工具', '模型工具调用经统一注册表和审批；结果按原调用顺序回填。', (
        ('core/agent.py', 'Agent._execute_tool_calls'), ('tools/registry.py', 'ToolRegistry.execute'),
        ('safety/gates.py', 'WebApprover'), ('tools/result.py', 'ToolResult'))),
    ('stream', '流式与取消', '流式响应和持久运行事件是两条接口；停止通过协作取消传播。', (
        ('channels/web_api/conversation.py', 'create_conversation_router.chat_stream'), ('core/agent.py', 'Agent.run_turn'),
        ('llm/gateway.py', 'Gateway.stream'), ('memory/runs.py', 'RunStore'))),
    ('terminal', '交互式终端', 'WebSocket 先校验身份，再由会话池创建独立 ConPTY；断开时核查退出。', (
        ('channels/terminal/routes.py', 'create_terminal_router.terminal_ws'),
        ('channels/web_auth.py', 'WebAuth.check_websocket'), ('channels/terminal/pool.py', 'TerminalManager.open'),
        ('channels/terminal/session.py', 'TerminalSession'), ('channels/terminal/backend.py', 'open_process'))),
    ('plugin', '插件扩展接入', '下载/收包、隔离校验、人工激活、命名空间注册，最终仍由工具审批链执行。', (
        ('plugins/community/client.py', 'CommunityClient.install'), ('plugins/host.py', 'PluginHost.install_package'),
        ('plugins/host.py', 'PluginHost.activate'), ('plugins/host.py', 'PluginContext.add_tool'), ('tools/registry.py', 'ToolRegistry.execute'))),
    ('skill', '技能数据接入', '技能是提示与参考数据；导入不执行脚本，激活需人工确认。', (
        ('tools/skills/tools.py', 'register_skill_inbox_tools'), ('memory/skill_inbox.py', 'SkillInbox'),
        ('memory/skills.py', 'SkillStore'))),
    ('mcp', '外部 MCP 能力', '渐进发现与授权校验后调用外部进程；结果未知不重放写操作。', (
        ('tools/mcp/tools.py', 'register_mcp_tools'), ('tools/mcp/client.py', 'MCPManager'),
        ('memory/state.py', 'MCPSchemaStore'))),
)
