# 当前开发状态

> 从 [开发台账](tasks.json) 生成。更新台账后运行 `uv run --no-sync python scripts/task_status.py`。

V2 Everything is a Plugin 架构迁移已验收。冻结源码 `1a2810ea` 离线全量 8739 项通过、0 失败、10 项跳过。OmniWeave 首批已接入正式 Web 受理和同库事务，观察模式默认关闭。正式产物与真实环境验收继续按各自任务记录。

| 任务 | 内容 | 当前状态 | 下一步或验收条件 | 依据 |
|---|---|---|---|---|
| RC | 候选与离线回归 | 等待开发完成 | 架构冻结源码 `1a2810ea` 的完整离线结果已归档。正式发布时核对源码与测试边界，构建 wheel 和依赖包并登记产物哈希。源码、测试、依赖和配置一致时引用本次结果。 | [v2-release-candidate-05b44447-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-candidate-05b44447-2026-11-24.json)、[plugin-architecture-final-2026-11-25](evidence/plugin-architecture-final-2026-11-25.json) |
| SOAK | 连续 72 小时 | 待外部验收 | 已有采样属于 05b44447。保留这轮原始日志；对最终交付源码重新计时，核对采样连续性、失败计数和资源阈值。 | [v2-release-candidate-05b44447-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-candidate-05b44447-2026-11-24.json) |
| WIN | Windows 干净机 | 待外部验收 | 在独立机器执行安装→升级→回滚→卸载，复核数据保留与服务、端口清理。 | [v2-clean-machine-kit-dae24cf7-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-clean-machine-kit-dae24cf7-2026-11-24.json)、[v2-service-chain-current-30bea914-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-service-chain-current-30bea914-2026-11-24.json) |
| SOCIAL | QQ／NapCat 与 B 站 | 待外部验收 | 安排测试账号与 QQ 对端；核对消息收发、B 站读缓存命中与 24 小时新鲜度。 | — |
| ANDROID | 安卓真机与视觉模型 | 待外部验收 | 接入测试设备及与图像协议兼容的模型，验证截图、输入、中文恢复与 1–4 张图的实际理解。 | [v2-visual-tool-contract-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-visual-tool-contract-2026-11-24.json) |
| MAC | macOS 交付 | 待外部验收 | Mac mini 复核最终 helper 身份、TCC 重新授权与用户域服务。若选择公开分发，另安排 Developer ID、公证和干净机 Gatekeeper 验证。 | [v2-release-scope-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-scope-2026-11-24.json) |
| LINUX | Linux 图形会话 | 待外部验收 | 在有 portal 的真实 Wayland 会话验证观察、授权和撤回；X11 复核受支持窗口的按键及有限文本追加。 | [v2-release-scope-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-scope-2026-11-24.json) |
| MEDIA | 可选媒体依赖 | 已实现／专项通过 | 最终包分别验证默认安装的缺组件提示、[asr] 安装的解帧及转写；记录平台和素材。 | [v2-media-asr-install-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-media-asr-install-2026-11-24.json) |
| SCOPE | 延期范围 | 本版延期 | Android SoM／自动探索、Wayland 前台键鼠、默认包视频分镜及 macOS 公开公证，待所有者重新纳入后单列开发批次。 | [v2-release-scope-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-scope-2026-11-24.json) |
| NAV | 功能开发导航、技能与插件路由、离线插件样例 | 已实现／专项通过 | 导航首批覆盖 30 个功能与 26 个页面，共 502 项去重专项通过。后续迁移在同批更新功能导航、调用方和测试入口。 | [EXTENDING](reference/EXTENDING.md)、[feature-navigation](evidence/feature-navigation.json)、[FEATURES](index/FEATURES.md) |
| KERNEL | 内核学习、完成核验、模型回退与安全接续 | 已实现／专项通过 | 5da6dce4 的 63 项内核专项和离线全量已通过，候选 wheel 也有技能学习、跨会话复用和撤销回执。当前按具体逻辑缺口开发，以离线专项验证。模型质量与可选策略另行评测。 | [KERNEL](reference/KERNEL.md)、[kernel-parity-2026-11-25](evidence/kernel-parity-2026-11-25.json)、[kernel-plugins-2026-11-25](evidence/kernel-plugins-2026-11-25.json) |
| OMNIWEAVE | OWA Phase 0–1 基线与 Web 观察受理 | 开发中 | 核对 `d897f286` 基线、`v2_0053` → `v2_0054` 迁移、原回执与幂等规则。Phase 2 接入 Router、任务依赖和 Worker，验收三个 Worker 并行期间主会话仍可接收输入。 | [OMNIWEAVE](reference/OMNIWEAVE.md) |
| CAPABILITY-PLUGINS | 内置工具、Extension 和 MCP 的能力插件组装 | 验收完成 | 能力注册、所有者入口、回合捕获和资源归属已接入正式实例。增改工具按同一合同登记并验证调用方。模型、渠道和平台的提供者验证见 PROVIDER-PLUGINS。 | [CAPABILITY_PLUGINS](reference/CAPABILITY_PLUGINS.md)、[kernel-plugins-2026-11-25](evidence/kernel-plugins-2026-11-25.json) |
| PROVIDER-PLUGINS | 模型、渠道与平台提供者代码注册 | 已实现／专项通过 | 模型 84 项、渠道 88 项、平台与架构 118 项专项通过。OpenAPI、40 个 Agent 工具、3 个所有者工具及默认驱动与上一轮候选一致。新增同类实现沿各提供者模块登记并补调用方专项。 | [CAPABILITY_PLUGINS](reference/CAPABILITY_PLUGINS.md)、[provider-plugins-2026-11-25](evidence/provider-plugins-2026-11-25.json) |
| BUSINESS-FEATURE-PLUGINS | 二十一项业务功能的注册与生产装配 | 验收完成 | 冻结源码通过离线全量。业务服务、旧运行时读取和 HTTP 使用同一登记实例，212 个 OpenAPI 路径、40 个 Agent 工具和 3 个所有者工具合同一致。后续按功能目录登记新业务。 | [CAPABILITY_PLUGINS](reference/CAPABILITY_PLUGINS.md)、[feature-plugins-2026-11-25](evidence/feature-plugins-2026-11-25.json)、[scheduling-plugin-2026-11-25](evidence/scheduling-plugin-2026-11-25.json)、[channels-feature-plugins-2026-11-25](evidence/channels-feature-plugins-2026-11-25.json)、[desktop-feature-plugins-2026-11-25](evidence/desktop-feature-plugins-2026-11-25.json)、[extensions-feature-plugins-2026-11-25](evidence/extensions-feature-plugins-2026-11-25.json)、[operations-feature-plugins-2026-11-25](evidence/operations-feature-plugins-2026-11-25.json)、[content-feature-plugins-2026-11-25](evidence/content-feature-plugins-2026-11-25.json)、[workflows-feature-plugins-2026-11-25](evidence/workflows-feature-plugins-2026-11-25.json)、[config-ops-feature-plugins-2026-11-25](evidence/config-ops-feature-plugins-2026-11-25.json)、[plugin-architecture-final-2026-11-25](evidence/plugin-architecture-final-2026-11-25.json) |
| CLI-PLUGINS | CLI 业务命令的参数与派发登记 | 验收完成 | 安装、发布、服务、交换、诊断及离线维护专项 526 项通过，31 个根命令与 44 个子命令的帮助和参数一致。完整结果由架构候选收尾证据归档。 | [CAPABILITY_PLUGINS](reference/CAPABILITY_PLUGINS.md)、[cli-feature-plugins-2026-11-25](evidence/cli-feature-plugins-2026-11-25.json) |
| PLUGIN-ARCHITECTURE | V2 全部业务模块的插件化迁移 | 验收完成 | 修复后候选 `1a2810ea` 共 8749 项离线测试，8739 通过、0 失败、10 跳过，控制台耗时 37 分 15 秒。严格类型检查 497 个文件零错误、零警告。真实设备、账号、正式交付与长跑按各自验收项推进。 | [PLUGIN_MIGRATION](PLUGIN_MIGRATION.md)、[plugin-architecture-final-2026-11-25](evidence/plugin-architecture-final-2026-11-25.json)、[compatibility-final-feature-plugins-2026-11-25](evidence/compatibility-final-feature-plugins-2026-11-25.json)、[cli-feature-plugins-2026-11-25](evidence/cli-feature-plugins-2026-11-25.json) |
| KERNEL-EVAL | Hermes 与两版 V2 同模型对跑 | 本版延期 | 保留三方主试验的 189/288 份计量回执。模型评测单独恢复时，补委派 4 题、学习 4 题、其余故障恢复 3 题，并按评测候选采集受影响组。4B 与 32K 的结果归属已登记的模型试验。 | [KERNEL](reference/KERNEL.md)、[kernel-plugins-2026-11-25](evidence/kernel-plugins-2026-11-25.json) |

## Windows 阶段与后续验收

Windows 用户域服务在 `30bea914` 构建的 wheel 上完成注册、启动、只读状态、停止、卸载共 14 步。干净机仍须对最终产物验证安装、升级、回滚和卸载，记录 wheel SHA、PID 与就绪回执。

开发分工和接续步骤见 [交接](HANDOFF.md)。
