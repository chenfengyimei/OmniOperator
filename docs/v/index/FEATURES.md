# 功能开发地图

在仓库根运行 `uv run --no-sync python scripts/project_index.py --feature 插件`。可输入功能 ID、页面名、别名或开发任务。编辑 `docs/v/features.json` 后重新生成本页。

| 功能 | 归属 | 常用检索词 |
|---|---|---|
| [可执行插件](#feature-extensions) | V2 | 插件、插件授权、extension、本地市场 |
| [文本技能](#feature-skills) | V2 | 技能、技能回滚、skill、SKILL.md |
| [安卓设备与截图](#feature-android) | V2 | 安卓、安卓截图、安卓设备、adb、android截图 |
| [MCP 外部工具服务](#feature-mcp) | V2 | 外部服务、外部工具、工具发现、OAuth |
| [Agent 运行内核](#feature-kernel) | V2 | 运行、对话、chat、agent、内核、安全接续、完成核验、能力注册、Everything is a Plugin、Hermes 对标 |
| [OmniWeave 主会话编排](#feature-omniweave) | V2 | OWA、主会话、观察模式、Router Worker |
| [提示词与上下文](#feature-context) | V2 | token、上下文、压缩、工具发现、上下文压缩 |
| [模型与配置](#feature-models) | V2 | 模型、供应商、路由、配置、settings、备用模型、模型回退、模型提供者、模型传输 |
| [凭据与授权核查](#feature-credentials) | V2 | 凭据、credential、授权代次、凭据撤权 |
| [记忆与整理](#feature-memory) | V2 | 记忆、语义、检索 |
| [回合后学习与反思](#feature-learning) | V2 | 学习、反思、候选、reflections |
| [来源文档](#feature-documents) | V2 | 知识库、知识、文档 |
| [人物与人格](#feature-people) | V2 | 人物、人格、档案、persona |
| [工作区文件工具](#feature-file-tools) | V2 | 文件工具、files_write、文件写入、补丁、搜索、工作区 |
| [浏览器工具](#feature-browser) | V2 | 浏览器、浏览、自动化、browser_act |
| [文件与对象](#feature-objects) | V2 | 对象、上传、下载、产物、文件 |
| [媒体与分镜](#feature-media) | V2 | 媒体、视频、分镜、抽帧、字幕、ASR |
| [视觉与图像](#feature-vision) | V2 | 视觉、图像、视觉子代理、SoM |
| [跨平台桌面](#feature-desktop) | V2 | 桌面、桌面截图、窗口、Windows、macOS、Linux、平台提供者 |
| [V2 QQ 通道](#feature-qq) | V2 | QQ服务、NapCat、OneBot |
| [B 站读取与发表](#feature-bilibili) | V2 | B站、哔哩哔哩、视频缓存 |
| [持久调度](#feature-schedules) | V2 | 调度、定时、时区、cron |
| [持久投递](#feature-deliveries) | V2 | 投递、发送、通道、渠道提供者、渠道插件 |
| [动作与审批核查](#feature-action-reviews) | V2 | 审批、动作核查、核查 |
| [所有者终端](#feature-terminals) | V2 | 终端、shell、terminal |
| [运维与审计](#feature-operations) | V2 | 运维、日志、审计、能力、诊断、令牌 |
| [V2 数据库与迁移](#feature-storage) | V2 | 存储、数据库、数据迁移、SQLite |
| [安装、服务与发布](#feature-release) | V2 | 安装、发布、回滚、wheel、服务、安装包 |
| [V1 兼容运行时](#feature-v1-runtime) | V1 | V1、旧版、omni-v1、omni-web-v1 |
| [V1 社区插件](#feature-v1-plugins) | V1 | 旧版插件、V1插件、社区插件 |
| [独立 omni-qq](#feature-v1-qq) | V1 | omni-qq、V1QQ、独立QQ |
| [共享开发基础设施](#feature-project-index) | 共享 | 索引、项目地图、目录、文件地图 |

<a id="feature-extensions"></a>

## 可执行插件 (extensions · V2)

为当前 Agent 登记可信 Python 包，按包指纹和工具授权，在受管宿主调用。

工作台页面 extensions

**从这里读**

- [agent/src/omni/adapters/http_routes/extensions.py#extensions_router.extension_authorization](../../../agent/src/omni/adapters/http_routes/extensions.py#L69)

**领域规则**

- [agent/src/omni/domain/extensions.py#parse_authorization](../../../agent/src/omni/domain/extensions.py#L396)

**应用服务**

- [agent/src/omni/application/extensions.py#ExtensionService.authorize](../../../agent/src/omni/application/extensions.py#L279)
- [agent/src/omni/application/extensions.py#ExtensionService.invoke](../../../agent/src/omni/application/extensions.py#L337)

**适配器与仓储**

- [agent/src/omni/adapters/extensions/packages.py#capture_directory](../../../agent/src/omni/adapters/extensions/packages.py#L238)
- [agent/src/omni/adapters/extensions/worker.py](../../../agent/src/omni/adapters/extensions/worker.py)
- [agent/src/omni/adapters/persistence/extension_repository.py](../../../agent/src/omni/adapters/persistence/extension_repository.py)

**组装点**

- [agent/src/omni/composition/features/extensions.py#install_extensions](../../../agent/src/omni/composition/features/extensions.py#L14)

**界面**

- [web/ui/v2/extensions.js](../../../web/ui/v2/extensions.js)

**合同与手册**

- [docs/v/reference/EXTENDING.md](../../../docs/v/reference/EXTENDING.md)

**样例**

- [agent/examples/extensions/text-summary/extension.json](../../../agent/examples/extensions/text-summary/extension.json)
- [agent/examples/extensions/text-summary/plugin.py#invoke](../../../agent/examples/extensions/text-summary/plugin.py#L6)

**专项测试**

- [agent/tests/v2/integrations/test_v2_extension_example.py](../../../agent/tests/v2/integrations/test_v2_extension_example.py)
- [agent/tests/v2/http/test_v2_extensions_http.py](../../../agent/tests/v2/http/test_v2_extensions_http.py)
- [agent/tests/v2/integrations/test_v2_extensions.py](../../../agent/tests/v2/integrations/test_v2_extensions.py)
- [agent/tests/v2/ui/test_v2_extension_market_ui.py](../../../agent/tests/v2/ui/test_v2_extension_market_ui.py)

**改动影响**　授权变化会影响新回合的目录、历史回合的绑定、逐次批准和宿主回收。

**相关功能**

- V1 社区插件 (v1-plugins, V1)　V1 社区包的隔离登记、加载与工具注册。
- 文本技能 (skills, V2)　编辑、导入、启用与回滚工作方法，按回合固定使用的修订。
- MCP 外部工具服务 (mcp, V2)　连接独立服务并发现目录，按工具审阅、授权和调用。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/integrations/test_v2_extension_example.py agent/tests/v2/http/test_v2_extensions_http.py agent/tests/v2/integrations/test_v2_extensions.py agent/tests/v2/ui/test_v2_extension_market_ui.py
```

<a id="feature-skills"></a>

## 文本技能 (skills · V2)

编辑、导入、启用与回滚工作方法，按回合固定使用的修订。

工作台页面 skills

**从这里读**

- [agent/src/omni/adapters/http_routes/skills.py#skills_router.skill_rollback](../../../agent/src/omni/adapters/http_routes/skills.py#L113)

**领域规则**

- [agent/src/omni/domain/skills.py](../../../agent/src/omni/domain/skills.py)

**应用服务**

- [agent/src/omni/application/skills.py#SkillService.rollback](../../../agent/src/omni/application/skills.py#L240)

**适配器与仓储**

- [agent/src/omni/adapters/skill_packages.py](../../../agent/src/omni/adapters/skill_packages.py)
- [agent/src/omni/adapters/persistence/skill_repository.py](../../../agent/src/omni/adapters/persistence/skill_repository.py)

**组装点**

- [agent/src/omni/composition/features/skills.py#install_skills](../../../agent/src/omni/composition/features/skills.py#L12)

**界面**

- [web/ui/v2/skills.js](../../../web/ui/v2/skills.js)

**合同与手册**

- [docs/v/reference/EXTENDING.md](../../../docs/v/reference/EXTENDING.md)

**专项测试**

- [agent/tests/v2/http/test_v2_skills_http.py](../../../agent/tests/v2/http/test_v2_skills_http.py)
- [agent/tests/v2/integrations/test_v2_skills.py](../../../agent/tests/v2/integrations/test_v2_skills.py)
- [agent/tests/v2/ui/test_v2_skills_ui.py](../../../agent/tests/v2/ui/test_v2_skills_ui.py)

**改动影响**　编辑和回滚会改变当前修订、显式启用所需的指纹以及后续回合的技能目录。

**相关功能**

- 回合后学习与反思 (learning, V2)　基于已发生的回合提出待审建议，保留审阅与证据。
- 可执行插件 (extensions, V2)　为当前 Agent 登记可信 Python 包，按包指纹和工具授权，在受管宿主调用。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_skills_http.py agent/tests/v2/integrations/test_v2_skills.py agent/tests/v2/ui/test_v2_skills_ui.py
```

<a id="feature-android"></a>

## 安卓设备与截图 (android · V2)

从授权设备观察界面、截取图像、审阅输入与操作。

工作台页面 android

**从这里读**

- [agent/src/omni/adapters/http_routes/android.py#android_router.android_capture](../../../agent/src/omni/adapters/http_routes/android.py#L111)

**领域规则**

- [agent/src/omni/domain/android.py](../../../agent/src/omni/domain/android.py)

**应用服务**

- [agent/src/omni/application/android.py#AndroidService.capture](../../../agent/src/omni/application/android.py#L348)

**适配器与仓储**

- [agent/src/omni/adapters/android.py#AndroidBackend.execute](../../../agent/src/omni/adapters/android.py#L74)
- [agent/src/omni/adapters/workers/android.py#AndroidWorker.invoke](../../../agent/src/omni/adapters/workers/android.py#L125)

**组装点**

- [agent/src/omni/composition/features/android.py#install_android](../../../agent/src/omni/composition/features/android.py#L12)
- [agent/src/omni/composition/features/catalog.py](../../../agent/src/omni/composition/features/catalog.py)

**界面**

- [web/ui/v2/android.js](../../../web/ui/v2/android.js)
- [web/ui/v2/android-unicode.js](../../../web/ui/v2/android-unicode.js)

**合同与手册**

- [docs/v/reference/ANDROID.md](../../../docs/v/reference/ANDROID.md)

**专项测试**

- [agent/tests/v2/http/test_v2_android_http.py](../../../agent/tests/v2/http/test_v2_android_http.py)
- [agent/tests/v2/integrations/test_v2_android.py](../../../agent/tests/v2/integrations/test_v2_android.py)
- [agent/tests/v2/ui/test_v2_android_ui.py](../../../agent/tests/v2/ui/test_v2_android_ui.py)

**改动影响**　截图涉及设备授权、观测、对象归属、图像回执与工作台预览。

**相关功能**

- 视觉与图像 (vision, V2)　把同一 Agent 的图像或媒体帧交给视觉子代理。
- 跨平台桌面 (desktop, V2)　观察原生窗口、截屏，通过门户与显式许可执行动作。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_android_http.py agent/tests/v2/integrations/test_v2_android.py agent/tests/v2/ui/test_v2_android_ui.py
```

<a id="feature-mcp"></a>

## MCP 外部工具服务 (mcp · V2)

连接独立服务并发现目录，按工具审阅、授权和调用。

工作台页面 mcp

**从这里读**

- [agent/src/omni/adapters/http_routes/mcp.py#mcp_router.mcp_connect](../../../agent/src/omni/adapters/http_routes/mcp.py#L63)
- [agent/src/omni/adapters/http_routes/mcp.py#mcp_router.mcp_authorize_tool](../../../agent/src/omni/adapters/http_routes/mcp.py#L99)

**领域规则**

- [agent/src/omni/domain/mcp.py](../../../agent/src/omni/domain/mcp.py)

**应用服务**

- [agent/src/omni/application/mcp.py#MCPService](../../../agent/src/omni/application/mcp.py#L87)

**适配器与仓储**

- [agent/src/omni/adapters/mcp/hosts.py](../../../agent/src/omni/adapters/mcp/hosts.py)
- [agent/src/omni/adapters/mcp/stdio.py](../../../agent/src/omni/adapters/mcp/stdio.py)
- [agent/src/omni/adapters/mcp/http.py](../../../agent/src/omni/adapters/mcp/http.py)

**组装点**

- [agent/src/omni/composition/features/mcp.py#install_mcp](../../../agent/src/omni/composition/features/mcp.py#L17)

**界面**

- [web/ui/v2/mcp.js](../../../web/ui/v2/mcp.js)

**合同与手册**

- [docs/v/reference/EXTENDING.md](../../../docs/v/reference/EXTENDING.md)
- [docs/v/reference/MCP.md](../../../docs/v/reference/MCP.md)

**专项测试**

- [agent/tests/v2/integrations/test_v2_mcp.py](../../../agent/tests/v2/integrations/test_v2_mcp.py)
- [agent/tests/v2/integrations/test_v2_mcp_flow.py](../../../agent/tests/v2/integrations/test_v2_mcp_flow.py)
- [agent/tests/v2/ui/test_v2_mcp_ui.py](../../../agent/tests/v2/ui/test_v2_mcp_ui.py)

**改动影响**　目录或传输变更影响工具快照、OAuth、调用记录和断开回收。

**相关功能**

- 可执行插件 (extensions, V2)　为当前 Agent 登记可信 Python 包，按包指纹和工具授权，在受管宿主调用。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/integrations/test_v2_mcp.py agent/tests/v2/integrations/test_v2_mcp_flow.py agent/tests/v2/ui/test_v2_mcp_ui.py
```

<a id="feature-kernel"></a>

## Agent 运行内核 (kernel · V2)

接收回合，组装工具、模型调用与动作审批，记录结果。

工作台页面 chat

**从这里读**

- [agent/src/omni/adapters/http.py#create_app.turn](../../../agent/src/omni/adapters/http.py#L601)

**领域规则**

- [agent/src/omni/domain/contracts.py](../../../agent/src/omni/domain/contracts.py)
- [agent/src/omni/domain/turn_prompt.py](../../../agent/src/omni/domain/turn_prompt.py)

**应用服务**

- [agent/src/omni/application/engine.py#Application.submit_turn](../../../agent/src/omni/application/engine.py#L186)
- [agent/src/omni/application/capabilities.py#CapabilityRegistry.capture](../../../agent/src/omni/application/capabilities.py#L106)

**适配器与仓储**

- [agent/src/omni/adapters/persistence/database.py](../../../agent/src/omni/adapters/persistence/database.py)
- [agent/src/omni/adapters/persistence/run_checkpoint_repository.py#RunCheckpointRepositories.requeue_checkpoint](../../../agent/src/omni/adapters/persistence/run_checkpoint_repository.py#L133)

**组装点**

- [agent/src/omni/composition/instance.py#open_instance.application](../../../agent/src/omni/composition/instance.py#L540)
- [agent/src/omni/composition/capabilities.py#builtin_plugins](../../../agent/src/omni/composition/capabilities.py#L21)

**界面**

- [web/ui/v2/main.js](../../../web/ui/v2/main.js)
- [web/ui/v2/model-output.js](../../../web/ui/v2/model-output.js)

**合同与手册**

- [docs/v/reference/KERNEL.md](../../../docs/v/reference/KERNEL.md)
- [docs/v/reference/CAPABILITY_PLUGINS.md](../../../docs/v/reference/CAPABILITY_PLUGINS.md)
- [docs/v/reference/ARCHITECTURE.md](../../../docs/v/reference/ARCHITECTURE.md)

**样例**

- [agent/scripts/eval_kernel.py#score](../../../agent/scripts/eval_kernel.py#L249)
- [agent/scripts/eval_kernel_local.py#main](../../../agent/scripts/eval_kernel_local.py#L658)
- [agent/tests/fixtures/kernel_scenarios_v1.json](../../../agent/tests/fixtures/kernel_scenarios_v1.json)

**专项测试**

- [agent/tests/v2/core/test_v2_kernel.py](../../../agent/tests/v2/core/test_v2_kernel.py)
- [agent/tests/v2/core/test_v2_capability_plugins.py](../../../agent/tests/v2/core/test_v2_capability_plugins.py)
- [agent/tests/v2/http/test_v2_http.py](../../../agent/tests/v2/http/test_v2_http.py)
- [agent/tests/v2/core/test_v2_kernel_evolution.py](../../../agent/tests/v2/core/test_v2_kernel_evolution.py)
- [agent/tests/v2/core/test_v2_kernel_restart.py](../../../agent/tests/v2/core/test_v2_kernel_restart.py)
- [agent/tests/test_kernel_eval.py](../../../agent/tests/test_kernel_eval.py)

**改动影响**　影响所有回合提交方，以及运行状态、预算与取消处理。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/core/test_v2_kernel.py agent/tests/v2/core/test_v2_capability_plugins.py agent/tests/v2/http/test_v2_http.py agent/tests/v2/core/test_v2_kernel_evolution.py agent/tests/v2/core/test_v2_kernel_restart.py agent/tests/test_kernel_eval.py
```

<a id="feature-omniweave"></a>

## OmniWeave 主会话编排 (omniweave · V2)

将所有者 Web 回合关联到 Ticket，在启用模式中从固定的私有主会话规划和查询 Worker。

**从这里读**

- [agent/src/omni/adapters/http.py#create_app.turn](../../../agent/src/omni/adapters/http.py#L601)
- [agent/src/omni/adapters/http.py#create_app.event_stream](../../../agent/src/omni/adapters/http.py#L665)
- [agent/src/omni/adapters/http_routes/omniweave.py#omniweave_router.ticket](../../../agent/src/omni/adapters/http_routes/omniweave.py#L32)
- [agent/src/omni/adapters/http_routes/omniweave.py#omniweave_router.select_main_session](../../../agent/src/omni/adapters/http_routes/omniweave.py#L24)

**领域规则**

- [agent/src/omni/domain/omniweave.py](../../../agent/src/omni/domain/omniweave.py)

**应用服务**

- [agent/src/omni/application/omniweave.py#OmniWeaveService.web_admission](../../../agent/src/omni/application/omniweave.py#L69)
- [agent/src/omni/application/omniweave.py#OmniWeaveService.invoke](../../../agent/src/omni/application/omniweave.py#L109)
- [agent/src/omni/application/engine.py#Application.submit_turn](../../../agent/src/omni/application/engine.py#L186)
- [agent/src/omni/application/engine.py#Application._dispatch](../../../agent/src/omni/application/engine.py#L267)

**适配器与仓储**

- [agent/src/omni/adapters/persistence/omniweave_repository.py#OmniWeaveRepositories.omniweave_observe](../../../agent/src/omni/adapters/persistence/omniweave_repository.py#L133)
- [agent/src/omni/adapters/persistence/migrations/versions/v0054.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0054.py)
- [agent/src/omni/adapters/persistence/migrations/versions/v0055.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0055.py)
- [agent/src/omni/adapters/persistence/migrations/versions/v0056.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0056.py)
- [agent/src/omni/adapters/persistence/migrations/versions/v0057.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0057.py)
- [agent/src/omni/adapters/persistence/migrations/versions/v0058.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0058.py)
- [agent/src/omni/adapters/persistence/migrations/versions/v0059.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0059.py)
- [agent/src/omni/adapters/persistence/migrations/versions/v0060.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0060.py)
- [agent/src/omni/adapters/persistence/migrations/versions/v0061.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0061.py)
- [agent/src/omni/adapters/persistence/migrations/versions/v0062.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0062.py)
- [agent/src/omni/adapters/persistence/migrations/versions/v0063.py](../../../agent/src/omni/adapters/persistence/migrations/versions/v0063.py)
- [agent/src/omni/adapters/persistence/omniweave_delivery.py#OmniWeaveDeliveryRepositories.omniweave_finish_delivery](../../../agent/src/omni/adapters/persistence/omniweave_delivery.py#L465)
- [agent/src/omni/adapters/persistence/omniweave_mailbox.py#OmniWeaveMailboxRepositories._owa_peer_citations](../../../agent/src/omni/adapters/persistence/omniweave_mailbox.py#L154)
- [agent/src/omni/adapters/persistence/omniweave_goals.py#prior_goal_blocker](../../../agent/src/omni/adapters/persistence/omniweave_goals.py#L42)
- [agent/src/omni/adapters/persistence/omniweave_artifacts.py#OmniWeaveArtifactRepositories.omniweave_artifact_read](../../../agent/src/omni/adapters/persistence/omniweave_artifacts.py#L245)
- [agent/src/omni/adapters/persistence/omniweave_authority.py#authorize_mandate](../../../agent/src/omni/adapters/persistence/omniweave_authority.py#L41)
- [agent/src/omni/adapters/persistence/omniweave_attempts.py#task_for_run](../../../agent/src/omni/adapters/persistence/omniweave_attempts.py#L14)
- [agent/src/omni/adapters/persistence/omniweave_mailbox.py#OmniWeaveMailboxRepositories.omniweave_mailbox_tick](../../../agent/src/omni/adapters/persistence/omniweave_mailbox.py#L548)
- [agent/src/omni/adapters/persistence/omniweave_repository.py#OmniWeaveRepositories.omniweave_plan](../../../agent/src/omni/adapters/persistence/omniweave_repository.py#L309)
- [agent/src/omni/adapters/persistence/delegation_repository.py#DelegationRepositories.claim_delegated_runs](../../../agent/src/omni/adapters/persistence/delegation_repository.py#L273)
- [agent/src/omni/adapters/persistence/delegation_repository.py#DelegationRepositories.delegation_recover](../../../agent/src/omni/adapters/persistence/delegation_repository.py#L433)
- [agent/src/omni/adapters/persistence/lease_repository.py#LeaseRepositories.claim_runs](../../../agent/src/omni/adapters/persistence/lease_repository.py#L288)
- [agent/src/omni/adapters/persistence/omniweave_delivery.py#OmniWeaveDeliveryRepositories._owa_delivery_evidence](../../../agent/src/omni/adapters/persistence/omniweave_delivery.py#L183)

**组装点**

- [agent/src/omni/composition/features/omniweave.py#install_omniweave](../../../agent/src/omni/composition/features/omniweave.py#L10)

**合同与手册**

- [docs/v/reference/OMNIWEAVE.md](../../../docs/v/reference/OMNIWEAVE.md)

**专项测试**

- [agent/tests/v2/http/test_v2_omniweave_http.py](../../../agent/tests/v2/http/test_v2_omniweave_http.py)
- [agent/tests/v2/http/test_v2_omniweave_campus.py](../../../agent/tests/v2/http/test_v2_omniweave_campus.py)
- [agent/tests/v2/http/test_v2_omniweave_receipt_resume.py](../../../agent/tests/v2/http/test_v2_omniweave_receipt_resume.py)
- [agent/tests/v2/core/test_v2_omniweave_migration.py](../../../agent/tests/v2/core/test_v2_omniweave_migration.py)
- [agent/tests/v2/ui/test_v2_agents_ui.py](../../../agent/tests/v2/ui/test_v2_agents_ui.py)

**改动影响**　变更影响 Web 请求幂等、授权来源、主会话隔离、Worker 依赖、写入批准与取消。

**相关功能**

- Agent 运行内核 (kernel, V2)　接收回合，组装工具、模型调用与动作审批，记录结果。
- 持久调度 (schedules, V2)　维护调度修订并把到期事件送入队列。
- 动作与审批核查 (action-reviews, V2)　处理待核查动作并保存证据与决定。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_omniweave_http.py agent/tests/v2/http/test_v2_omniweave_campus.py agent/tests/v2/http/test_v2_omniweave_receipt_resume.py agent/tests/v2/core/test_v2_omniweave_migration.py agent/tests/v2/ui/test_v2_agents_ui.py
```

<a id="feature-context"></a>

## 提示词与上下文 (context · V2)

管理回合上下文的预算、压缩和用量展示。

工作台页面 context

**从这里读**

- [agent/src/omni/adapters/http.py#create_app.session_context_overview](../../../agent/src/omni/adapters/http.py#L718)

**领域规则**

- [agent/src/omni/domain/context.py](../../../agent/src/omni/domain/context.py)
- [agent/src/omni/domain/turn_prompt.py](../../../agent/src/omni/domain/turn_prompt.py)

**应用服务**

- [agent/src/omni/application/context.py](../../../agent/src/omni/application/context.py)

**适配器与仓储**

- [agent/src/omni/adapters/token_counter.py](../../../agent/src/omni/adapters/token_counter.py)

**组装点**

- [agent/src/omni/composition/instance.py#open_instance](../../../agent/src/omni/composition/instance.py#L317)

**界面**

- [web/ui/v2/main.js](../../../web/ui/v2/main.js)

**合同与手册**

- [docs/v/reference/KERNEL.md](../../../docs/v/reference/KERNEL.md)

**专项测试**

- [agent/tests/v2/core/test_v2_context.py](../../../agent/tests/v2/core/test_v2_context.py)
- [agent/tests/v2/core/test_v2_context_faults.py](../../../agent/tests/v2/core/test_v2_context_faults.py)
- [agent/tests/v2/core/test_v2_run_compaction.py](../../../agent/tests/v2/core/test_v2_run_compaction.py)

**改动影响**　影响向模型提交的消息、回合快照与上下文页面。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/core/test_v2_context.py agent/tests/v2/core/test_v2_context_faults.py agent/tests/v2/core/test_v2_run_compaction.py
```

<a id="feature-models"></a>

## 模型与配置 (models · V2)

配置模型档案、连接与路由，向运行内核提供模型调用。

工作台页面 models、settings

**从这里读**

- [agent/src/omni/adapters/http_routes/configurations.py#configurations_router.configuration_probe](../../../agent/src/omni/adapters/http_routes/configurations.py#L143)

**领域规则**

- [agent/src/omni/domain/settings.py](../../../agent/src/omni/domain/settings.py)
- [agent/src/omni/domain/routing.py](../../../agent/src/omni/domain/routing.py)

**应用服务**

- [agent/src/omni/application/model_calls.py](../../../agent/src/omni/application/model_calls.py)
- [agent/src/omni/application/configuration.py](../../../agent/src/omni/application/configuration.py)
- [agent/src/omni/application/routing.py](../../../agent/src/omni/application/routing.py)

**适配器与仓储**

- [agent/src/omni/adapters/models.py](../../../agent/src/omni/adapters/models.py)
- [agent/src/omni/adapters/model_stream.py](../../../agent/src/omni/adapters/model_stream.py)

**组装点**

- [agent/src/omni/composition/model_providers.py#register_model_transport](../../../agent/src/omni/composition/model_providers.py#L22)
- [agent/src/omni/composition/features/configurations.py#install_configurations](../../../agent/src/omni/composition/features/configurations.py#L8)

**界面**

- [web/ui/v2/models.js](../../../web/ui/v2/models.js)
- [web/ui/v2/configuration.js](../../../web/ui/v2/configuration.js)
- [web/ui/v2/routing.js](../../../web/ui/v2/routing.js)

**合同与手册**

- [docs/v/reference/KERNEL.md](../../../docs/v/reference/KERNEL.md)

**专项测试**

- [agent/tests/v2/http/test_v2_configuration_http.py](../../../agent/tests/v2/http/test_v2_configuration_http.py)
- [agent/tests/v2/core/test_v2_provider_plugins.py](../../../agent/tests/v2/core/test_v2_provider_plugins.py)
- [agent/tests/v2/ui/test_v2_models_ui.py](../../../agent/tests/v2/ui/test_v2_models_ui.py)

**改动影响**　变更需同时验证配置修订、连接探测和模型选择。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_configuration_http.py agent/tests/v2/core/test_v2_provider_plugins.py agent/tests/v2/ui/test_v2_models_ui.py
```

<a id="feature-credentials"></a>

## 凭据与授权核查 (credentials · V2)

登记和更新实例凭据，按授权代次核查活动。

**从这里读**

- [agent/src/omni/adapters/http_routes/credentials.py#credentials_router](../../../agent/src/omni/adapters/http_routes/credentials.py#L31)

**应用服务**

- [agent/src/omni/application/credentials.py](../../../agent/src/omni/application/credentials.py)

**适配器与仓储**

- [agent/src/omni/adapters/credentials.py](../../../agent/src/omni/adapters/credentials.py)
- [agent/src/omni/adapters/native_credentials.py](../../../agent/src/omni/adapters/native_credentials.py)

**组装点**

- [agent/src/omni/composition/features/credentials.py#install_credentials](../../../agent/src/omni/composition/features/credentials.py#L8)

**专项测试**

- [agent/tests/v2/core/test_v2_credential_authority.py](../../../agent/tests/v2/core/test_v2_credential_authority.py)
- [agent/tests/v2/core/test_v2_credentials.py](../../../agent/tests/v2/core/test_v2_credentials.py)

**改动影响**　凭据修改影响模型、渠道和投递。撤权时须核对在途动作及未知结果。

**相关功能**

- 模型与配置 (models, V2)　配置模型档案、连接与路由，向运行内核提供模型调用。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/core/test_v2_credential_authority.py agent/tests/v2/core/test_v2_credentials.py
```

<a id="feature-memory"></a>

## 记忆与整理 (memory · V2)

审阅记忆、整理候选、语义关联和擦除。

工作台页面 memory

**从这里读**

- [agent/src/omni/adapters/http_routes/memory.py#memory_router.memories](../../../agent/src/omni/adapters/http_routes/memory.py#L28)

**领域规则**

- [agent/src/omni/domain/memory_organization.py](../../../agent/src/omni/domain/memory_organization.py)
- [agent/src/omni/domain/memory_semantics.py](../../../agent/src/omni/domain/memory_semantics.py)

**应用服务**

- [agent/src/omni/application/memory_organization.py](../../../agent/src/omni/application/memory_organization.py)
- [agent/src/omni/application/memory_semantics.py](../../../agent/src/omni/application/memory_semantics.py)

**适配器与仓储**

- [agent/src/omni/adapters/local_vectors.py](../../../agent/src/omni/adapters/local_vectors.py)

**组装点**

- [agent/src/omni/composition/features/memory.py#install_memory](../../../agent/src/omni/composition/features/memory.py#L11)

**界面**

- [web/ui/v2/memory-organization.js](../../../web/ui/v2/memory-organization.js)
- [web/ui/v2/memory-semantics.js](../../../web/ui/v2/memory-semantics.js)

**专项测试**

- [agent/tests/v2/http/test_v2_memory_organization_http.py](../../../agent/tests/v2/http/test_v2_memory_organization_http.py)
- [agent/tests/v2/core/test_v2_memory_organization.py](../../../agent/tests/v2/core/test_v2_memory_organization.py)

**改动影响**　影响回合记忆读取、检索与所有者的批准决定。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_memory_organization_http.py agent/tests/v2/core/test_v2_memory_organization.py
```

<a id="feature-learning"></a>

## 回合后学习与反思 (learning · V2)

基于已发生的回合提出待审建议，保留审阅与证据。

工作台页面 reflections

**从这里读**

- [agent/src/omni/adapters/http_routes/learning.py#learning_router.background_review_decision](../../../agent/src/omni/adapters/http_routes/learning.py#L72)
- [agent/src/omni/adapters/http_routes/learning.py#learning_router.reflection_decision](../../../agent/src/omni/adapters/http_routes/learning.py#L38)

**领域规则**

- [agent/src/omni/domain/background_review.py](../../../agent/src/omni/domain/background_review.py)
- [agent/src/omni/domain/reflection.py](../../../agent/src/omni/domain/reflection.py)

**应用服务**

- [agent/src/omni/application/background_review.py#BackgroundReviewService.decide](../../../agent/src/omni/application/background_review.py#L322)
- [agent/src/omni/application/reflections.py](../../../agent/src/omni/application/reflections.py)

**组装点**

- [agent/src/omni/composition/features/learning.py#install_learning](../../../agent/src/omni/composition/features/learning.py#L12)
- [agent/src/omni/composition/features/maintenance.py#install_maintenance](../../../agent/src/omni/composition/features/maintenance.py#L13)

**界面**

- [web/ui/v2/reflections.js](../../../web/ui/v2/reflections.js)

**合同与手册**

- [docs/v/reference/KERNEL.md](../../../docs/v/reference/KERNEL.md)
- [docs/v/reference/EXTENDING.md](../../../docs/v/reference/EXTENDING.md)

**专项测试**

- [agent/tests/v2/core/test_v2_background_review.py](../../../agent/tests/v2/core/test_v2_background_review.py)
- [agent/tests/v2/core/test_v2_reflection_review.py](../../../agent/tests/v2/core/test_v2_reflection_review.py)

**改动影响**　建议进入所有者决定链；同步核对技能修订、来源擦除与反思页面。

**相关功能**

- 文本技能 (skills, V2)　编辑、导入、启用与回滚工作方法，按回合固定使用的修订。
- 记忆与整理 (memory, V2)　审阅记忆、整理候选、语义关联和擦除。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/core/test_v2_background_review.py agent/tests/v2/core/test_v2_reflection_review.py
```

<a id="feature-documents"></a>

## 来源文档 (documents · V2)

导入来源、维护索引并执行有归属的检索。

工作台页面 documents

**从这里读**

- [agent/src/omni/adapters/http_routes/documents.py#documents_router.source_document_search](../../../agent/src/omni/adapters/http_routes/documents.py#L68)

**领域规则**

- [agent/src/omni/domain/local_vectors.py](../../../agent/src/omni/domain/local_vectors.py)

**应用服务**

- [agent/src/omni/application/documents.py](../../../agent/src/omni/application/documents.py)

**适配器与仓储**

- [agent/src/omni/adapters/vector_files.py](../../../agent/src/omni/adapters/vector_files.py)

**组装点**

- [agent/src/omni/composition/features/documents.py#install_documents](../../../agent/src/omni/composition/features/documents.py#L10)

**界面**

- [web/ui/v2/documents.js](../../../web/ui/v2/documents.js)

**专项测试**

- [agent/tests/v2/http/test_v2_documents_http.py](../../../agent/tests/v2/http/test_v2_documents_http.py)
- [agent/tests/v2/core/test_v2_documents.py](../../../agent/tests/v2/core/test_v2_documents.py)

**改动影响**　变更需覆盖索引重建、版本与来源擦除。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_documents_http.py agent/tests/v2/core/test_v2_documents.py
```

<a id="feature-people"></a>

## 人物与人格 (people · V2)

管理人物关联及人格成长的派生视图。

工作台页面 people、persona-growth

**从这里读**

- [agent/src/omni/adapters/http_routes/people.py#people_router.list_people](../../../agent/src/omni/adapters/http_routes/people.py#L48)
- [agent/src/omni/adapters/http_routes/people.py#people_router.persona_growth](../../../agent/src/omni/adapters/http_routes/people.py#L145)

**领域规则**

- [agent/src/omni/domain/people.py](../../../agent/src/omni/domain/people.py)
- [agent/src/omni/domain/persona_growth.py](../../../agent/src/omni/domain/persona_growth.py)

**应用服务**

- [agent/src/omni/application/people.py](../../../agent/src/omni/application/people.py)
- [agent/src/omni/application/persona.py](../../../agent/src/omni/application/persona.py)

**适配器与仓储**

- [agent/src/omni/adapters/persona_files.py](../../../agent/src/omni/adapters/persona_files.py)

**组装点**

- [agent/src/omni/composition/features/people.py#install_people](../../../agent/src/omni/composition/features/people.py#L11)

**界面**

- [web/ui/v2/people.js](../../../web/ui/v2/people.js)
- [web/ui/v2/persona.js](../../../web/ui/v2/persona.js)

**专项测试**

- [agent/tests/v2/core/test_v2_people.py](../../../agent/tests/v2/core/test_v2_people.py)
- [agent/tests/v2/http/test_v2_persona_growth_http.py](../../../agent/tests/v2/http/test_v2_persona_growth_http.py)

**改动影响**　来源编辑和擦除会影响派生视图的重建与预览。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/core/test_v2_people.py agent/tests/v2/http/test_v2_persona_growth_http.py
```

<a id="feature-file-tools"></a>

## 工作区文件工具 (file-tools · V2)

在选定工作区读取、搜索、按指纹写入和打补丁。

**从这里读**

- [agent/src/omni/adapters/workers/main.py#Worker._files](../../../agent/src/omni/adapters/workers/main.py#L220)

**领域规则**

- [agent/src/omni/domain/files_patch.py](../../../agent/src/omni/domain/files_patch.py)
- [agent/src/omni/domain/files_search.py](../../../agent/src/omni/domain/files_search.py)

**适配器与仓储**

- [agent/src/omni/adapters/workers/client.py](../../../agent/src/omni/adapters/workers/client.py)

**组装点**

- [agent/src/omni/composition/instance.py#open_instance](../../../agent/src/omni/composition/instance.py#L317)

**合同与手册**

- [docs/v/reference/EXTENDING.md](../../../docs/v/reference/EXTENDING.md)

**专项测试**

- [agent/tests/v2/core/test_v2_workers.py](../../../agent/tests/v2/core/test_v2_workers.py)
- [agent/tests/v2/core/test_v2_files_patch.py](../../../agent/tests/v2/core/test_v2_files_patch.py)
- [agent/tests/v2/core/test_v2_files_search.py](../../../agent/tests/v2/core/test_v2_files_search.py)

**改动影响**　影响 Agent 写入审批、路径边界、并发覆盖与插件草稿。

**相关功能**

- 文件与对象 (objects, V2)　登记实例对象，按 Agent 归属访问元数据和内容。
- 可执行插件 (extensions, V2)　为当前 Agent 登记可信 Python 包，按包指纹和工具授权，在受管宿主调用。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/core/test_v2_workers.py agent/tests/v2/core/test_v2_files_patch.py agent/tests/v2/core/test_v2_files_search.py
```

<a id="feature-browser"></a>

## 浏览器工具 (browser · V2)

受管浏览器导航、观察与交互。

**从这里读**

- [agent/src/omni/adapters/workers/main.py#Worker._web](../../../agent/src/omni/adapters/workers/main.py#L515)

**适配器与仓储**

- [agent/src/omni/adapters/workers/client.py](../../../agent/src/omni/adapters/workers/client.py)

**组装点**

- [agent/src/omni/composition/instance.py#open_instance](../../../agent/src/omni/composition/instance.py#L317)

**专项测试**

- [agent/tests/v2/core/test_v2_browser_dialog.py](../../../agent/tests/v2/core/test_v2_browser_dialog.py)
- [agent/tests/v2/core/test_v2_browser_url_floor.py](../../../agent/tests/v2/core/test_v2_browser_url_floor.py)

**改动影响**　导航和动作共享 URL 边界与浏览器会话状态。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/core/test_v2_browser_dialog.py agent/tests/v2/core/test_v2_browser_url_floor.py
```

<a id="feature-objects"></a>

## 文件与对象 (objects · V2)

登记实例对象，按 Agent 归属访问元数据和内容。

工作台页面 objects

**从这里读**

- [agent/src/omni/adapters/http.py#create_app.upload_object](../../../agent/src/omni/adapters/http.py#L555)

**领域规则**

- [agent/src/omni/domain/objects.py](../../../agent/src/omni/domain/objects.py)

**应用服务**

- [agent/src/omni/application/objects.py](../../../agent/src/omni/application/objects.py)

**适配器与仓储**

- [agent/src/omni/adapters/object_store.py](../../../agent/src/omni/adapters/object_store.py)

**组装点**

- [agent/src/omni/composition/instance.py#open_instance.objects](../../../agent/src/omni/composition/instance.py#L410)

**界面**

- [web/ui/v2/main.js](../../../web/ui/v2/main.js)

**专项测试**

- [agent/tests/v2/http/test_v2_http.py](../../../agent/tests/v2/http/test_v2_http.py)
- [agent/tests/v2/http/test_v2_images_http.py](../../../agent/tests/v2/http/test_v2_images_http.py)

**改动影响**　影响媒体和插件产物的归属、下载和保留。

**相关功能**

- 工作区文件工具 (file-tools, V2)　在选定工作区读取、搜索、按指纹写入和打补丁。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_http.py agent/tests/v2/http/test_v2_images_http.py
```

<a id="feature-media"></a>

## 媒体与分镜 (media · V2)

接收来源、规划媒体任务、抽帧并保存转写。

工作台页面 media

**从这里读**

- [agent/src/omni/adapters/http_routes/media.py#media_router.media_frames](../../../agent/src/omni/adapters/http_routes/media.py#L124)

**领域规则**

- [agent/src/omni/domain/media.py](../../../agent/src/omni/domain/media.py)

**应用服务**

- [agent/src/omni/application/media.py](../../../agent/src/omni/application/media.py)

**适配器与仓储**

- [agent/src/omni/adapters/media.py](../../../agent/src/omni/adapters/media.py)
- [agent/src/omni/adapters/workers/media.py](../../../agent/src/omni/adapters/workers/media.py)

**组装点**

- [agent/src/omni/composition/features/media.py#install_media](../../../agent/src/omni/composition/features/media.py#L24)
- [agent/src/omni/composition/features/catalog.py](../../../agent/src/omni/composition/features/catalog.py)

**界面**

- [web/ui/v2/media.js](../../../web/ui/v2/media.js)

**合同与手册**

- [docs/v/reference/VIDEO_UNDERSTANDING.md](../../../docs/v/reference/VIDEO_UNDERSTANDING.md)

**专项测试**

- [agent/tests/v2/integrations/test_v2_media.py](../../../agent/tests/v2/integrations/test_v2_media.py)

**改动影响**　媒体作业、时间锚点、取消及视觉输入共用产物合同。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/integrations/test_v2_media.py
```

<a id="feature-vision"></a>

## 视觉与图像 (vision · V2)

把同一 Agent 的图像或媒体帧交给视觉子代理。

**从这里读**

- [agent/src/omni/application/visual_subagent.py#VisualSubagentService.run](../../../agent/src/omni/application/visual_subagent.py#L134)
- [agent/src/omni/adapters/http_routes/vision.py#vision_router.upload_image](../../../agent/src/omni/adapters/http_routes/vision.py#L26)

**领域规则**

- [agent/src/omni/domain/visual_subagent.py](../../../agent/src/omni/domain/visual_subagent.py)
- [agent/src/omni/domain/som.py](../../../agent/src/omni/domain/som.py)

**应用服务**

- [agent/src/omni/application/images.py](../../../agent/src/omni/application/images.py)

**适配器与仓储**

- [agent/src/omni/adapters/visual.py](../../../agent/src/omni/adapters/visual.py)

**组装点**

- [agent/src/omni/composition/features/vision.py#install_vision](../../../agent/src/omni/composition/features/vision.py#L13)

**界面**

- [web/ui/v2/chat-images.js](../../../web/ui/v2/chat-images.js)

**专项测试**

- [agent/tests/v2/core/test_v2_visual_production.py](../../../agent/tests/v2/core/test_v2_visual_production.py)
- [agent/tests/v2/core/test_v2_visual_subagent.py](../../../agent/tests/v2/core/test_v2_visual_subagent.py)

**改动影响**　影响帧归属、图像协议和真实模型验证。

**相关功能**

- 安卓设备与截图 (android, V2)　从授权设备观察界面、截取图像、审阅输入与操作。
- 媒体与分镜 (media, V2)　接收来源、规划媒体任务、抽帧并保存转写。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/core/test_v2_visual_production.py agent/tests/v2/core/test_v2_visual_subagent.py
```

<a id="feature-desktop"></a>

## 跨平台桌面 (desktop · V2)

观察原生窗口、截屏，通过门户与显式许可执行动作。

工作台页面 desktop

**从这里读**

- [agent/src/omni/adapters/http_routes/desktop.py#desktop_router.desktop_capture](../../../agent/src/omni/adapters/http_routes/desktop.py#L63)

**领域规则**

- [agent/src/omni/domain/desktop.py](../../../agent/src/omni/domain/desktop.py)
- [agent/src/omni/domain/desktop_actions.py](../../../agent/src/omni/domain/desktop_actions.py)

**应用服务**

- [agent/src/omni/application/desktop.py](../../../agent/src/omni/application/desktop.py)
- [agent/src/omni/application/desktop_actions.py](../../../agent/src/omni/application/desktop_actions.py)
- [agent/src/omni/application/desktop_portal.py](../../../agent/src/omni/application/desktop_portal.py)
- [agent/src/omni/application/desktop_control.py](../../../agent/src/omni/application/desktop_control.py)

**适配器与仓储**

- [agent/src/omni/adapters/platform/desktop/backend.py#DesktopBackend](../../../agent/src/omni/adapters/platform/desktop/backend.py#L138)
- [agent/src/omni/adapters/platform/desktop/macos_helper.py](../../../agent/src/omni/adapters/platform/desktop/macos_helper.py)
- [agent/src/omni/adapters/platform/desktop/linux/linux_atspi.py](../../../agent/src/omni/adapters/platform/desktop/linux/linux_atspi.py)
- [agent/src/omni/adapters/platform/desktop/desktop_capture.py](../../../agent/src/omni/adapters/platform/desktop/desktop_capture.py)

**组装点**

- [agent/src/omni/composition/platform_providers.py#platform_provider](../../../agent/src/omni/composition/platform_providers.py#L76)
- [agent/src/omni/composition/features/desktop.py#install_desktop](../../../agent/src/omni/composition/features/desktop.py#L15)
- [agent/src/omni/composition/features/catalog.py](../../../agent/src/omni/composition/features/catalog.py)

**界面**

- [web/ui/v2/desktop.js](../../../web/ui/v2/desktop.js)
- [web/ui/v2/desktop-actions.js](../../../web/ui/v2/desktop-actions.js)

**合同与手册**

- [docs/v/reference/DESKTOP.md](../../../docs/v/reference/DESKTOP.md)

**专项测试**

- [agent/tests/v2/http/test_v2_desktop_http.py](../../../agent/tests/v2/http/test_v2_desktop_http.py)
- [agent/tests/v2/core/test_v2_provider_plugins.py](../../../agent/tests/v2/core/test_v2_provider_plugins.py)

**改动影响**　平台实现共用观测与许可合同；改动需覆盖 HTTP、对应平台与真会话。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_desktop_http.py agent/tests/v2/core/test_v2_provider_plugins.py
```

<a id="feature-qq"></a>

## V2 QQ 通道 (qq · V2)

通过实例组装的 QQ 服务处理绑定、收发与批准。

工作台页面 qq

**从这里读**

- [agent/src/omni/adapters/http_routes/qq.py#qq_router.qq_status](../../../agent/src/omni/adapters/http_routes/qq.py#L45)

**领域规则**

- [agent/src/omni/domain/qq.py](../../../agent/src/omni/domain/qq.py)

**应用服务**

- [agent/src/omni/application/qq.py](../../../agent/src/omni/application/qq.py)

**适配器与仓储**

- [agent/src/omni/adapters/onebot.py](../../../agent/src/omni/adapters/onebot.py)

**组装点**

- [agent/src/omni/composition/channel_providers.py#register_channel_providers](../../../agent/src/omni/composition/channel_providers.py#L25)
- [agent/src/omni/composition/features/qq.py#install_qq](../../../agent/src/omni/composition/features/qq.py#L9)
- [agent/src/omni/composition/features/catalog.py](../../../agent/src/omni/composition/features/catalog.py)

**界面**

- [web/ui/v2/qq.js](../../../web/ui/v2/qq.js)

**合同与手册**

- [docs/v/reference/QQ_SCENARIOS.md](../../../docs/v/reference/QQ_SCENARIOS.md)

**专项测试**

- [agent/tests/v2/http/test_v2_qq_http.py](../../../agent/tests/v2/http/test_v2_qq_http.py)

**改动影响**　监听、投递与风控依赖绑定修订及通道配置。

**相关功能**

- 独立 omni-qq (v1-qq, V1)　独立命令使用 V1 QQDispatcher 和 V1 runtime。
- 持久投递 (deliveries, V2)　审阅草稿、绑定通道路由、追踪发送回执。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_qq_http.py
```

<a id="feature-bilibili"></a>

## B 站读取与发表 (bilibili · V2)

管理账号风控、读缓存与发布动作。

工作台页面 bilibili

**从这里读**

- [agent/src/omni/adapters/http_routes/bilibili.py#bilibili_router.bilibili_read](../../../agent/src/omni/adapters/http_routes/bilibili.py#L89)

**领域规则**

- [agent/src/omni/domain/bilibili.py](../../../agent/src/omni/domain/bilibili.py)

**应用服务**

- [agent/src/omni/application/bilibili.py](../../../agent/src/omni/application/bilibili.py)

**适配器与仓储**

- [agent/src/omni/adapters/bilibili.py](../../../agent/src/omni/adapters/bilibili.py)

**组装点**

- [agent/src/omni/composition/channel_providers.py#register_channel_providers](../../../agent/src/omni/composition/channel_providers.py#L25)
- [agent/src/omni/composition/features/bilibili.py#install_bilibili](../../../agent/src/omni/composition/features/bilibili.py#L10)
- [agent/src/omni/composition/features/catalog.py](../../../agent/src/omni/composition/features/catalog.py)

**界面**

- [web/ui/v2/bilibili.js](../../../web/ui/v2/bilibili.js)

**合同与手册**

- [docs/v/reference/BILIBILI_WRITING.md](../../../docs/v/reference/BILIBILI_WRITING.md)

**专项测试**

- [agent/tests/v2/http/test_v2_bilibili_http.py](../../../agent/tests/v2/http/test_v2_bilibili_http.py)

**改动影响**　账号切换、缓存命中与发表批准需分别验证。

**相关功能**

- 媒体与分镜 (media, V2)　接收来源、规划媒体任务、抽帧并保存转写。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_bilibili_http.py
```

<a id="feature-schedules"></a>

## 持久调度 (schedules · V2)

维护调度修订并把到期事件送入队列。

工作台页面 schedules

**从这里读**

- [agent/src/omni/adapters/http_routes/schedules.py#schedules_router.schedules](../../../agent/src/omni/adapters/http_routes/schedules.py#L29)

**领域规则**

- [agent/src/omni/domain/cronjob.py](../../../agent/src/omni/domain/cronjob.py)

**应用服务**

- [agent/src/omni/application/scheduling.py](../../../agent/src/omni/application/scheduling.py)

**适配器与仓储**

- [agent/src/omni/adapters/timezones.py](../../../agent/src/omni/adapters/timezones.py)

**组装点**

- [agent/src/omni/composition/features/schedules.py#install_schedules](../../../agent/src/omni/composition/features/schedules.py#L11)
- [agent/src/omni/composition/features/catalog.py](../../../agent/src/omni/composition/features/catalog.py)

**界面**

- [web/ui/v2/schedules.js](../../../web/ui/v2/schedules.js)

**专项测试**

- [agent/tests/v2/core/test_v2_scheduling.py](../../../agent/tests/v2/core/test_v2_scheduling.py)
- [agent/tests/v2/core/test_v2_scheduling_faults.py](../../../agent/tests/v2/core/test_v2_scheduling_faults.py)
- [agent/tests/v2/core/test_v2_feature_plugins.py#test_scheduling_starts_after_kernel_recovery_and_is_reclaimed_on_failure](../../../agent/tests/v2/core/test_v2_feature_plugins.py#L303)

**改动影响**　变更影响时区计算、租约、重试及恢复。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/core/test_v2_scheduling.py agent/tests/v2/core/test_v2_scheduling_faults.py agent/tests/v2/core/test_v2_feature_plugins.py::test_scheduling_starts_after_kernel_recovery_and_is_reclaimed_on_failure
```

<a id="feature-deliveries"></a>

## 持久投递 (deliveries · V2)

审阅草稿、绑定通道路由、追踪发送回执。

工作台页面 deliveries

**从这里读**

- [agent/src/omni/adapters/http_routes/deliveries.py#deliveries_router.create_delivery](../../../agent/src/omni/adapters/http_routes/deliveries.py#L64)

**领域规则**

- [agent/src/omni/domain/delivery.py](../../../agent/src/omni/domain/delivery.py)

**应用服务**

- [agent/src/omni/application/delivery.py](../../../agent/src/omni/application/delivery.py)

**组装点**

- [agent/src/omni/composition/channel_providers.py#register_channel_providers](../../../agent/src/omni/composition/channel_providers.py#L25)
- [agent/src/omni/composition/features/deliveries.py#install_deliveries](../../../agent/src/omni/composition/features/deliveries.py#L9)

**界面**

- [web/ui/v2/deliveries.js](../../../web/ui/v2/deliveries.js)

**专项测试**

- [agent/tests/v2/http/test_v2_delivery_http.py](../../../agent/tests/v2/http/test_v2_delivery_http.py)
- [agent/tests/v2/core/test_v2_delivery.py](../../../agent/tests/v2/core/test_v2_delivery.py)
- [agent/tests/v2/core/test_v2_provider_plugins.py](../../../agent/tests/v2/core/test_v2_provider_plugins.py)

**改动影响**　通道返回的不确定结果会影响重试与所有者复核。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_delivery_http.py agent/tests/v2/core/test_v2_delivery.py agent/tests/v2/core/test_v2_provider_plugins.py
```

<a id="feature-action-reviews"></a>

## 动作与审批核查 (action-reviews · V2)

处理待核查动作并保存证据与决定。

工作台页面 action-reviews

**从这里读**

- [agent/src/omni/adapters/http_routes/action_reviews.py#action_reviews_router.action_review_commit](../../../agent/src/omni/adapters/http_routes/action_reviews.py#L39)

**领域规则**

- [agent/src/omni/domain/action_review.py](../../../agent/src/omni/domain/action_review.py)

**应用服务**

- [agent/src/omni/application/action_review.py](../../../agent/src/omni/application/action_review.py)

**组装点**

- [agent/src/omni/composition/features/action_reviews.py#install_action_reviews](../../../agent/src/omni/composition/features/action_reviews.py#L10)

**界面**

- [web/ui/v2/action-reviews.js](../../../web/ui/v2/action-reviews.js)

**专项测试**

- [agent/tests/v2/http/test_v2_action_review_http.py](../../../agent/tests/v2/http/test_v2_action_review_http.py)

**改动影响**　决定绑定动作与修订，影响恢复和后续执行。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_action_review_http.py
```

<a id="feature-terminals"></a>

## 所有者终端 (terminals · V2)

创建会话、输入、读取回执及回收原生终端。

工作台页面 terminals

**从这里读**

- [agent/src/omni/adapters/http_routes/terminals.py#terminals_router.terminal_open](../../../agent/src/omni/adapters/http_routes/terminals.py#L64)

**领域规则**

- [agent/src/omni/domain/terminals.py](../../../agent/src/omni/domain/terminals.py)

**应用服务**

- [agent/src/omni/application/terminals.py](../../../agent/src/omni/application/terminals.py)
- [agent/src/omni/application/terminal_review.py](../../../agent/src/omni/application/terminal_review.py)

**组装点**

- [agent/src/omni/composition/platform_providers.py#PlatformProvider.register](../../../agent/src/omni/composition/platform_providers.py#L32)
- [agent/src/omni/composition/features/terminals.py#install_terminals](../../../agent/src/omni/composition/features/terminals.py#L11)

**界面**

- [web/ui/v2/terminals.js](../../../web/ui/v2/terminals.js)
- [web/ui/v2/terminal-reviews.js](../../../web/ui/v2/terminal-reviews.js)

**合同与手册**

- [docs/v/reference/TERMINAL.md](../../../docs/v/reference/TERMINAL.md)

**专项测试**

- [agent/tests/v2/http/test_v2_terminals_http.py](../../../agent/tests/v2/http/test_v2_terminals_http.py)

**改动影响**　影响会话归属、断开恢复与核查。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_terminals_http.py
```

<a id="feature-operations"></a>

## 运维与审计 (operations · V2)

查询实例能力、日志与审计，管理令牌和维护任务。

工作台页面 logs、capabilities、tokens、audit

**从这里读**

- [agent/src/omni/adapters/http_routes/operations.py#operations_router.logs](../../../agent/src/omni/adapters/http_routes/operations.py#L117)
- [agent/src/omni/adapters/http.py#create_app.owner_token_rotate](../../../agent/src/omni/adapters/http.py#L517)
- [agent/src/omni/adapters/http.py#create_app.capabilities](../../../agent/src/omni/adapters/http.py#L478)
- [agent/src/omni/adapters/http_routes/operations.py#operations_router.audit](../../../agent/src/omni/adapters/http_routes/operations.py#L68)

**领域规则**

- [agent/src/omni/domain/capabilities.py](../../../agent/src/omni/domain/capabilities.py)
- [agent/src/omni/domain/audit_retention.py](../../../agent/src/omni/domain/audit_retention.py)

**应用服务**

- [agent/src/omni/application/operations.py](../../../agent/src/omni/application/operations.py)
- [agent/src/omni/application/maintenance.py](../../../agent/src/omni/application/maintenance.py)

**适配器与仓储**

- [agent/src/omni/adapters/diagnostics.py](../../../agent/src/omni/adapters/diagnostics.py)
- [agent/src/omni/adapters/log_query.py](../../../agent/src/omni/adapters/log_query.py)

**组装点**

- [agent/src/omni/composition/features/operations.py#install_operations](../../../agent/src/omni/composition/features/operations.py#L14)
- [agent/src/omni/composition/commands/operations.py#run_diagnostics](../../../agent/src/omni/composition/commands/operations.py#L124)
- [agent/src/omni/composition/commands/retention.py#run_retention_preview](../../../agent/src/omni/composition/commands/retention.py#L78)
- [agent/src/omni/composition/http.py#http_app](../../../agent/src/omni/composition/http.py#L50)

**界面**

- [web/ui/v2/operations.js](../../../web/ui/v2/operations.js)
- [web/ui/v2/maintenance.js](../../../web/ui/v2/maintenance.js)
- [web/ui/v2/tokens.js](../../../web/ui/v2/tokens.js)
- [web/ui/v2/capabilities.js](../../../web/ui/v2/capabilities.js)

**合同与手册**

- [docs/v/reference/OPERATIONS.md](../../../docs/v/reference/OPERATIONS.md)

**专项测试**

- [agent/tests/v2/http/test_v2_operations_http.py](../../../agent/tests/v2/http/test_v2_operations_http.py)
- [agent/tests/v2/core/test_v2_owner_token.py](../../../agent/tests/v2/core/test_v2_owner_token.py)
- [agent/tests/v2/http/test_v2_audit_retention_http.py](../../../agent/tests/v2/http/test_v2_audit_retention_http.py)

**改动影响**　认证、维护和诊断涉及实例级资源与数据保留。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/http/test_v2_operations_http.py agent/tests/v2/core/test_v2_owner_token.py agent/tests/v2/http/test_v2_audit_retention_http.py
```

<a id="feature-storage"></a>

## V2 数据库与迁移 (storage · V2)

维护主数据库、事务、schema 和版本迁移。

**从这里读**

- [agent/src/omni/adapters/persistence/database.py#Database](../../../agent/src/omni/adapters/persistence/database.py#L27)

**领域规则**

- [agent/src/omni/domain/schema_upgrade.py](../../../agent/src/omni/domain/schema_upgrade.py)

**应用服务**

- [agent/src/omni/application/operations.py](../../../agent/src/omni/application/operations.py)

**适配器与仓储**

- [agent/src/omni/adapters/persistence/migration_config.py](../../../agent/src/omni/adapters/persistence/migration_config.py)

**组装点**

- [agent/src/omni/composition/instance.py#open_instance.database](../../../agent/src/omni/composition/instance.py#L388)
- [agent/src/omni/composition/commands/maintenance.py#run_objects](../../../agent/src/omni/composition/commands/maintenance.py#L88)

**合同与手册**

- [docs/v/reference/DATA_MODEL.md](../../../docs/v/reference/DATA_MODEL.md)

**专项测试**

- [agent/tests/v2/operations/test_v2_schema_release.py](../../../agent/tests/v2/operations/test_v2_schema_release.py)

**改动影响**　迁移影响启动、快照恢复以及各功能仓储。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/operations/test_v2_schema_release.py
```

<a id="feature-release"></a>

## 安装、服务与发布 (release · V2)

构建安装产物，管理服务启动、迁移、升级与回滚。

**从这里读**

- [agent/src/omni/composition/commands/installation.py#run_installation](../../../agent/src/omni/composition/commands/installation.py#L127)
- [agent/src/omni/composition/commands/deployment.py#run_service](../../../agent/src/omni/composition/commands/deployment.py#L213)
- [agent/scripts/install_v2.py](../../../agent/scripts/install_v2.py)
- [agent/scripts/build_release.py](../../../agent/scripts/build_release.py)

**领域规则**

- [agent/src/omni/domain/installation.py](../../../agent/src/omni/domain/installation.py)
- [agent/src/omni/domain/releases.py](../../../agent/src/omni/domain/releases.py)

**应用服务**

- [agent/src/omni/application/releases.py](../../../agent/src/omni/application/releases.py)
- [agent/src/omni/application/service_control.py](../../../agent/src/omni/application/service_control.py)

**适配器与仓储**

- [agent/src/omni/adapters/platform/releases/candidate_installer.py](../../../agent/src/omni/adapters/platform/releases/candidate_installer.py)

**组装点**

- [agent/src/omni/composition/cli.py#parser](../../../agent/src/omni/composition/cli.py#L146)
- [agent/src/omni/composition/commands/deployment.py#run_release](../../../agent/src/omni/composition/commands/deployment.py#L192)
- [agent/src/omni/composition/releases.py](../../../agent/src/omni/composition/releases.py)
- [agent/src/omni/composition/service_control.py](../../../agent/src/omni/composition/service_control.py)

**合同与手册**

- [docs/v/reference/V2_PACKAGING.md](../../../docs/v/reference/V2_PACKAGING.md)

**专项测试**

- [agent/tests/v2/operations/test_v2_installation.py](../../../agent/tests/v2/operations/test_v2_installation.py)
- [agent/tests/v2/operations/test_v2_service_control.py](../../../agent/tests/v2/operations/test_v2_service_control.py)
- [agent/tests/v2/operations/test_v2_packaging_contract.py](../../../agent/tests/v2/operations/test_v2_packaging_contract.py)

**改动影响**　改动影响官方构建、平台服务、干净机生命周期与长跑候选。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/operations/test_v2_installation.py agent/tests/v2/operations/test_v2_service_control.py agent/tests/v2/operations/test_v2_packaging_contract.py
```

<a id="feature-v1-runtime"></a>

## V1 兼容运行时 (v1-runtime · V1)

显式 V1 CLI 和 Web 使用原有运行时与组装入口。

**从这里读**

- [agent/src/omni/channels/cli.py#main](../../../agent/src/omni/channels/cli.py#L34)
- [agent/src/omni/channels/web.py#main](../../../agent/src/omni/channels/web.py#L211)

**应用服务**

- [agent/src/omni/core/agent.py](../../../agent/src/omni/core/agent.py)

**组装点**

- [agent/src/omni/bootstrap.py#build_runtime](../../../agent/src/omni/bootstrap.py#L379)

**合同与手册**

- [docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)

**专项测试**

- [agent/tests/test_arch.py](../../../agent/tests/test_arch.py)

**改动影响**　兼容层改动应覆盖 V1 入口、导入边界与既有状态。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/test_arch.py
```

<a id="feature-v1-plugins"></a>

## V1 社区插件 (v1-plugins · V1)

V1 社区包的隔离登记、加载与工具注册。

**从这里读**

- [agent/src/omni/plugins/quarantine.py](../../../agent/src/omni/plugins/quarantine.py)
- [agent/src/omni/plugins/host.py](../../../agent/src/omni/plugins/host.py)

**适配器与仓储**

- [agent/src/omni/plugins/package_reader.py](../../../agent/src/omni/plugins/package_reader.py)
- [agent/src/omni/plugins/tools.py](../../../agent/src/omni/plugins/tools.py)

**组装点**

- [agent/src/omni/bootstrap.py#build_runtime](../../../agent/src/omni/bootstrap.py#L379)

**合同与手册**

- [docs/v/reference/PLUGIN_SYSTEM.md](../../../docs/v/reference/PLUGIN_SYSTEM.md)

**专项测试**

- [agent/tests/test_plugins.py](../../../agent/tests/test_plugins.py)
- [agent/tests/test_plugin_community.py](../../../agent/tests/test_plugin_community.py)

**改动影响**　变更需验证 V1 市场、隔离区与注册表。

**相关功能**

- 可执行插件 (extensions, V2)　为当前 Agent 登记可信 Python 包，按包指纹和工具授权，在受管宿主调用。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/test_plugins.py agent/tests/test_plugin_community.py
```

<a id="feature-v1-qq"></a>

## 独立 omni-qq (v1-qq · V1)

独立命令使用 V1 QQDispatcher 和 V1 runtime。

**从这里读**

- [agent/src/omni/channels/qq.py#main](../../../agent/src/omni/channels/qq.py#L333)

**应用服务**

- [agent/src/omni/channels/qq.py#QQDispatcher](../../../agent/src/omni/channels/qq.py#L210)

**组装点**

- [agent/src/omni/bootstrap.py#build_runtime](../../../agent/src/omni/bootstrap.py#L379)

**合同与手册**

- [docs/v/reference/QQ_SETUP.md](../../../docs/v/reference/QQ_SETUP.md)

**专项测试**

- [agent/tests/test_qq.py](../../../agent/tests/test_qq.py)
- [agent/tests/test_qq_scope.py](../../../agent/tests/test_qq_scope.py)

**改动影响**　需验证独立 QQ 入口、消息范围与 V1 配置。

**相关功能**

- V2 QQ 通道 (qq, V2)　通过实例组装的 QQ 服务处理绑定、收发与批准。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/test_qq.py agent/tests/test_qq_scope.py
```

<a id="feature-project-index"></a>

## 共享开发基础设施 (project-index · 共享)

从源码静态生成模块、接口、工具与功能导航。

**从这里读**

- [agent/scripts/project_index.py#main](../../../agent/scripts/project_index.py#L16)
- [agent/src/omni/system/catalog/features.py#load_features](../../../agent/src/omni/system/catalog/features.py#L87)
- [agent/src/omni/system/catalog/scan.py#build_index](../../../agent/src/omni/system/catalog/scan.py#L280)

**适配器与仓储**

- [agent/src/omni/system/catalog/render.py](../../../agent/src/omni/system/catalog/render.py)

**合同与手册**

- [docs/v/reference/PROJECT_INDEX.md](../../../docs/v/reference/PROJECT_INDEX.md)

**专项测试**

- [agent/tests/test_feature_navigation.py](../../../agent/tests/test_feature_navigation.py)
- [agent/tests/test_project_catalog.py](../../../agent/tests/test_project_catalog.py)

**改动影响**　同时影响开发命令、生成文档与 V1 项目工作台。

**专项命令**

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/test_feature_navigation.py agent/tests/test_project_catalog.py
```
