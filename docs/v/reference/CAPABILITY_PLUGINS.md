# V2 能力插件与组装

V2 按能力组合 Agent。工作区文件、浏览器、文档、记忆、会话检索、技能、B 站、媒体、桌面、Android、视觉和调度在实例启动时挂载。可执行插件与 MCP 提供者从回合保存的授权绑定中提供工具。每个来源共用内核的执行和回执流程。

Agent 内核统一维护执行循环、上下文、委派、审批、预算、事务、任务状态和恢复。外围能力通过代码声明，由实例在启动时接到对应端口。已有模型档案、渠道配置和工作台入口继续作为配置合同。

| 扩展需求 | 入口 | 实例归属 |
|---|---|---|
| 在源码中接入带服务、工具和 HTTP 的完整业务功能 | 在 `composition/features/catalog.py` 登记 `FeaturePlugin` | 安装器为服务登记暂停和回收。HTTP 与工具共用该实例服务 |
| 在源码中添加可信产品能力 | `CapabilityPlugin`，通过 `open_instance(capability_plugins=...)` 或组装阶段的 `attach_plugin` | 默认由应用关闭。已有独立关闭步骤的服务在组装中声明 `own_adapters=False` |
| 按 Agent 与回合提供工具 | `RunCapabilityPort.tools_for_run`，由组装阶段登记。生产已有 Extension 和 MCP | 提供者服务由组装关闭，按运行授权绑定提供工具。派发前复核权限 |
| Agent 学习一套做法，或所有者安装自己的工具 | 技能、Extension 或 MCP | 沿用各自的修订、指纹、授权和回收合同 |

工具描述和参数用 `ToolSpec`。适配器实现 `CapabilityPort.invoke` 和 `close`。纯规则留在领域层，I/O 实现在适配层，应用层编排服务，组装负责实例的资源连接。在模型可见的 `tools` 与 `owner_tools` 中选择相应的操作入口。后者通过所有者审批路径调用。

注册时检查插件 ID 及工具名冲突。启动时封存注册表。每次执行按注册顺序捕获并过滤工具，遵循回合策略、远程会话限制和委派权限。执行循环保有模型预算、工具参数校验、写入审批、取消、持久化和接续职责。增加有副作用的回合提供者时，要把活跃授权检查接到相应事务与派发路径，并覆盖撤权、审批和未知结果场景。

可执行插件和 MCP 使用各自的登记流程和进程边界。`CapabilityPlugin` 是源码维护者的可信组装入口。对话生成或下载的代码由所有者通过 Extension 的具体包指纹授权和逐次审批进入宿主。[扩展手册](EXTENDING.md)

## 开发入口与回归

```powershell
uv run --no-sync python scripts/project_index.py --feature 能力注册
uv run --no-sync pytest -q -n 0 agent/tests/v2/core/test_v2_provider_plugins.py agent/tests/v2/core/test_v2_capability_plugins.py agent/tests/v2/core/test_v2_lifecycle.py
```

- [能力注册表](../../../agent/src/omni/application/capabilities.py)与[现有挂载点](../../../agent/src/omni/composition/instance.py)。
- [插件授权和升级](../../../agent/tests/v2/integrations/test_v2_extension_example.py)及 [MCP 工作流](../../../agent/tests/v2/integrations/test_v2_mcp_flow.py)。

## 模型、渠道和平台提供者

新增提供者时先实现对应端口的适配器，再在该组的组装模块登记默认实现或替代实现。通过 `open_instance` 的现有注入参数替换端口，使用离线夹具验证默认选择、调用及回收。同一阶段的回收共用实例的关闭预算。

| 能力 | 代码注册入口 | 接入及关闭归属 |
|---|---|---|
| 模型传输 | `composition/model_providers.py` 的 `register_model_transport` | 默认 LiteLLM 提供普通调用、预备和流式端口。调用和配置探测共用模型端口。实例负责所登记流式端口的排空 |
| 入站及出站渠道 | `composition/channel_providers.py` 的 `register_channel_providers` | 按 `qq`、`bilibili` 登记。出站覆盖只作用于发送。QQ 监听、B 站读取、媒体和投递先排空，实例按对象关闭传输，随后释放投递租约 |
| 跨平台实现 | `composition/platform_providers.py` 的 `platform_provider` | 按主机登记终端、桌面、门户和控制端口。macOS 保留 LaunchServices 身份。桌面、门户和控制共用并发池。控制服务关闭原生后端后结算同意记录 |

模型调用继续核对档案修订、取消和未知费用。渠道继续核对会话授权、发送前撤权和重复投递。平台适配器继续返回自身的可用状态并保留现场授权和回收流程。

## 业务功能登记与启动顺序

在 `composition/features/<feature>.py` 的安装器中用 `FeatureContext` 创建服务，在 `Lifetime` 中登记归属。返回服务和已有的 `Capability`。组装器在就绪之前安装功能，在 `feature_services` 发布只读映射，内核继续从能力表取得工具。HTTP 路由从同一映射取得服务，挂在需要所有者授权的 `/api/v1/agents/{agent_id}/<feature>` 下。功能 ID、精确重复的 HTTP 路径和工具名在组装时校验。

Android 和媒体在 `BUILTIN_FEATURES` 登记 ID、安装器和路由工厂。安装器负责启动期恢复，并将资源交给唯一的关闭方。媒体服务持有处理器，启动恢复失败时由同一关闭链回收。Android 分开登记业务服务与设备后端。

需要内核回调的功能在同一个目录的 `BUILTIN_KERNEL_FEATURES` 登记。调度通过 `KernelFeatureContext` 接收运行唤醒、取消、平台信息和日志脱敏。内核对象创建后安装调度服务，在封存工具目录前挂载 `cronjob_manage`。内核完成持久状态恢复并启动派发器后，实例再调用功能的 `start`。若启动到一半失败，已登记的回收步骤关闭后台任务并释放实例锁。

新功能可用 `http_app(feature_plugins=...)` 注入离线替身，再验证启动失败、鉴权、写入审批及排空。UI 页面、配置和领域合同由功能自身维护。

```powershell
uv run --no-sync python scripts/project_index.py --feature 排查安卓截图
uv run --no-sync python scripts/project_index.py --feature 媒体
uv run --no-sync python scripts/project_index.py --feature 调度
uv run --no-sync pytest -q -n 0 agent/tests/v2/core/test_v2_feature_plugins.py
```

下一批把 QQ 与 B 站的服务和 HTTP 接入功能登记，验证入站授权、发送前撤权、未知投递的复核以及共享传输的关闭归属。随后迁移桌面，验证原生句柄、观察权限和平台后端的回收顺序。每批核对工具顺序、HTTP 合同、配置兼容、权限与生命周期。已有的所有者安装流程见 [Extension 与 MCP 扩展手册](EXTENDING.md)。
