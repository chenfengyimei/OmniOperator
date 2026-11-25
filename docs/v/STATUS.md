# 当前开发状态

> 从 [开发台账](tasks.json) 生成。更新台账后运行 `uv run --no-sync python scripts/task_status.py`。

当前开发优先内核逻辑与外围能力插件化。模型、渠道和平台提供者已接入正式组装。Android、媒体、调度、QQ 与 B 站已按功能登记，QQ 和调度在内核恢复后启动后台任务。下一批迁移桌面。最近 8706 项通过的全量覆盖固定候选 `5da6dce4`，后续插件化批次执行离线专项。真实模型、账号和设备验收另行排期。

| 任务 | 内容 | 当前状态 | 下一步或验收条件 | 依据 |
|---|---|---|---|---|
| RC | 候选与离线回归 | 等待开发完成 | 开发批次执行相关模块与调用方的专项。发布冻结后选定提交，构建 wheel 和依赖包并协调一次离线全量，登记 JUnit、跳过项和产物哈希。 | [v2-release-candidate-05b44447-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-candidate-05b44447-2026-11-24.json) |
| SOAK | 连续 72 小时 | 待外部验收 | 已有采样属于 05b44447。保留这轮原始日志；对最终交付源码重新计时，核对采样连续性、失败计数和资源阈值。 | [v2-release-candidate-05b44447-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-candidate-05b44447-2026-11-24.json) |
| WIN | Windows 干净机 | 待外部验收 | 在独立机器执行安装→升级→回滚→卸载，复核数据保留与服务、端口清理。 | [v2-clean-machine-kit-dae24cf7-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-clean-machine-kit-dae24cf7-2026-11-24.json)、[v2-service-chain-current-30bea914-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-service-chain-current-30bea914-2026-11-24.json) |
| SOCIAL | QQ／NapCat 与 B 站 | 待外部验收 | 安排测试账号与 QQ 对端；核对消息收发、B 站读缓存命中与 24 小时新鲜度。 | — |
| ANDROID | 安卓真机与视觉模型 | 待外部验收 | 接入测试设备及与图像协议兼容的模型，验证截图、输入、中文恢复与 1–4 张图的实际理解。 | [v2-visual-tool-contract-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-visual-tool-contract-2026-11-24.json) |
| MAC | macOS 交付 | 待外部验收 | Mac mini 复核最终 helper 身份、TCC 重新授权与用户域服务。若选择公开分发，另安排 Developer ID、公证和干净机 Gatekeeper 验证。 | [v2-release-scope-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-scope-2026-11-24.json) |
| LINUX | Linux 图形会话 | 待外部验收 | 在有 portal 的真实 Wayland 会话验证观察、授权和撤回；X11 复核受支持窗口的按键及有限文本追加。 | [v2-release-scope-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-scope-2026-11-24.json) |
| MEDIA | 可选媒体依赖 | 已实现／专项通过 | 最终包分别验证默认安装的缺组件提示、[asr] 安装的解帧及转写；记录平台和素材。 | [v2-media-asr-install-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-media-asr-install-2026-11-24.json) |
| SCOPE | 延期范围 | 本版延期 | Android SoM／自动探索、Wayland 前台键鼠、默认包视频分镜及 macOS 公开公证，待所有者重新纳入后单列开发批次。 | [v2-release-scope-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-scope-2026-11-24.json) |
| NAV | 功能开发导航、技能与插件路由、离线插件样例 | 已实现／专项通过 | 导航首批覆盖 30 个功能与 26 个页面，共 502 项去重专项通过。Android、媒体、调度、QQ 与 B 站迁移后的入口已更新至索引。后续继续在同批更新导航、调用方与测试入口。 | [EXTENDING](reference/EXTENDING.md)、[feature-navigation](evidence/feature-navigation.json)、[FEATURES](index/FEATURES.md) |
| KERNEL | 内核学习、完成核验、模型回退与安全接续 | 已实现／专项通过 | 5da6dce4 的 63 项内核专项和离线全量已通过，候选 wheel 也有技能学习、跨会话复用和撤销回执。当前按具体逻辑缺口开发，以离线专项验证。模型质量与可选策略另行评测。 | [KERNEL](reference/KERNEL.md)、[kernel-parity-2026-11-25](evidence/kernel-parity-2026-11-25.json)、[kernel-plugins-2026-11-25](evidence/kernel-plugins-2026-11-25.json) |
| CAPABILITY-PLUGINS | 内置工具、Extension 和 MCP 的能力插件组装 | 验收完成 | 能力注册、所有者入口、回合捕获和资源归属已接入正式实例。增改工具按同一合同登记并验证调用方。模型、渠道和平台的提供者验证见 PROVIDER-PLUGINS。 | [CAPABILITY_PLUGINS](reference/CAPABILITY_PLUGINS.md)、[kernel-plugins-2026-11-25](evidence/kernel-plugins-2026-11-25.json) |
| PROVIDER-PLUGINS | 模型、渠道与平台提供者代码注册 | 已实现／专项通过 | 模型 84 项、渠道 88 项、平台与架构 118 项专项通过。OpenAPI、40 个 Agent 工具、3 个所有者工具及默认驱动与上一轮候选一致。新增同类实现沿各提供者模块登记并补调用方专项。 | [CAPABILITY_PLUGINS](reference/CAPABILITY_PLUGINS.md)、[provider-plugins-2026-11-25](evidence/provider-plugins-2026-11-25.json) |
| BUSINESS-FEATURE-PLUGINS | Android、媒体、调度、QQ 与 B 站业务功能登记 | 已实现／专项通过 | Android 与媒体批次 317 项、调度与调用方批次 244 项、QQ 与 B 站批次 708 项专项通过。渠道批次覆盖授权撤销、未知投递、后台启动失败和共享传输回收。下一批迁移桌面的观察、共享、控制同意与所有者操作。 | [CAPABILITY_PLUGINS](reference/CAPABILITY_PLUGINS.md)、[feature-plugins-2026-11-25](evidence/feature-plugins-2026-11-25.json)、[scheduling-plugin-2026-11-25](evidence/scheduling-plugin-2026-11-25.json)、[channels-feature-plugins-2026-11-25](evidence/channels-feature-plugins-2026-11-25.json) |
| KERNEL-EVAL | Hermes 与两版 V2 同模型对跑 | 本版延期 | 保留三方主试验的 189/288 份计量回执。模型评测单独恢复时，补委派 4 题、学习 4 题、其余故障恢复 3 题，并按评测候选采集受影响组。4B 与 32K 的结果归属已登记的模型试验。 | [KERNEL](reference/KERNEL.md)、[kernel-plugins-2026-11-25](evidence/kernel-plugins-2026-11-25.json) |

## Windows 阶段与后续验收

Windows 用户域服务在 `30bea914` 构建的 wheel 上完成注册、启动、只读状态、停止、卸载共 14 步。干净机仍须对最终产物验证安装、升级、回滚和卸载，记录 wheel SHA、PID 与就绪回执。

开发分工和接续步骤见 [交接](HANDOFF.md)。
