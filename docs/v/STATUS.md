# 当前开发状态

> 从 [开发台账](tasks.json) 生成。更新台账后运行 `uv run --no-sync python scripts/task_status.py`。

2026-11-24 整理时，最近有完整离线结果的候选为 `dae24cf7`（8648 通过、0 失败、10 跳过）。之后的视觉、媒体和本轮工作台文档路径均有改动。当前工作树待冻结复验，发布仍待干净机器、真实账号与设备以及最终候选的 72 小时记录。

| 任务 | 内容 | 当前状态 | 下一步或验收条件 | 依据 |
|---|---|---|---|---|
| RC | 候选与离线回归 | 开发中 | Windows 集成方选定提交、重建 wheel 和依赖包；在同一源码上协调一次全量，登记 JUnit、跳过项和产物哈希。 | [v2-release-candidate-05b44447-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-candidate-05b44447-2026-11-24.json) |
| SOAK | 连续 72 小时 | 待外部验收 | 已有采样属于 05b44447。保留这轮原始日志；对最终交付源码重新计时，核对采样连续性、失败计数和资源阈值。 | [v2-release-candidate-05b44447-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-candidate-05b44447-2026-11-24.json) |
| WIN | Windows 干净机 | 待外部验收 | 在独立机器执行安装→升级→回滚→卸载，复核数据保留与服务、端口清理。 | [v2-clean-machine-kit-dae24cf7-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-clean-machine-kit-dae24cf7-2026-11-24.json)、[v2-service-chain-current-30bea914-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-service-chain-current-30bea914-2026-11-24.json) |
| SOCIAL | QQ／NapCat 与 B 站 | 待外部验收 | 安排测试账号与 QQ 对端；核对消息收发、B 站读缓存命中与 24 小时新鲜度。 | — |
| ANDROID | 安卓真机与视觉模型 | 待外部验收 | 接入测试设备及与图像协议兼容的模型，验证截图、输入、中文恢复与 1–4 张图的实际理解。 | [v2-visual-tool-contract-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-visual-tool-contract-2026-11-24.json) |
| MAC | macOS 交付 | 待外部验收 | Mac mini 复核最终 helper 身份、TCC 重新授权与用户域服务。若选择公开分发，另安排 Developer ID、公证和干净机 Gatekeeper 验证。 | [v2-release-scope-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-scope-2026-11-24.json) |
| LINUX | Linux 图形会话 | 待外部验收 | 在有 portal 的真实 Wayland 会话验证观察、授权和撤回；X11 复核受支持窗口的按键及有限文本追加。 | [v2-release-scope-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-scope-2026-11-24.json) |
| MEDIA | 可选媒体依赖 | 已实现／专项通过 | 最终包分别验证默认安装的缺组件提示、[asr] 安装的解帧及转写；记录平台和素材。 | [v2-media-asr-install-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-media-asr-install-2026-11-24.json) |
| SCOPE | 延期范围 | 本版延期 | Android SoM／自动探索、Wayland 前台键鼠、默认包视频分镜及 macOS 公开公证，待所有者重新纳入后单列开发批次。 | [v2-release-scope-2026-11-24](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-release-scope-2026-11-24.json) |
| NAV | 功能开发导航、技能与插件路由、离线插件样例 | 已实现／专项通过 | 30 个功能覆盖 26 个页面；502 项去重专项通过。后续桌面、媒体或渠道发生实质变更时逐批迁移接口并更新地图。此工作树纳入最终候选全量。 | [EXTENDING](reference/EXTENDING.md)、[feature-navigation](evidence/feature-navigation.json)、[FEATURES](index/FEATURES.md) |

## Windows 阶段与后续验收

Windows 用户域服务在 `30bea914` 构建的 wheel 上完成注册、启动、只读状态、停止、卸载共 14 步。干净机仍须对最终产物验证安装、升级、回滚和卸载，记录 wheel SHA、PID 与就绪回执。

开发分工和接续步骤见 [交接](HANDOFF.md)。
