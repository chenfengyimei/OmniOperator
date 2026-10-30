# 文档导航（docs/README.md）

本目录的文档分三层：**用户手册**（怎么用）、**开发者文档**（怎么改）、**内部开发台账**
（如实记录开发过程与证据，不代表发布状态）。根 [README](../README.md) 是项目门面；
英文访客另见 [README.en](../README.en.md)。文档文件按此分层导航，物理位置不变。

## 用户手册（怎么用）

| 文档 | 内容 |
|---|---|
| [INSTALLER.md](INSTALLER.md) | 一键图形安装器：步骤、网络策略、失败降级与手动方案 |
| [QQ_SETUP.md](QQ_SETUP.md) | QQ 通道部署：NapCat 安装、正向 WebSocket、扫码登录、风控提醒 |
| [QQ_SCENARIOS.md](QQ_SCENARIOS.md) | QQ 场景隔离、主动提醒、outbox 补投的配置与验收口径 |
| [BILIBILI_WRITING.md](BILIBILI_WRITING.md) | B 站写入：草稿/人工审批/一次 UI 提交（默认关闭，实站选择器待核验） |
| [ANDROID.md](ANDROID.md) | 安卓控制：观察绑定动作、锁帧、输入法恢复、MJPEG（真机待验） |
| [DESKTOP.md](DESKTOP.md) | Windows 桌面：UIAutomation、观察令牌、窗口租约、应用白名单 |
| [MCP.md](MCP.md) | 本地 MCP：stdio 配置、工具目录、只读白名单、漂移复查 |
| [TERMINAL.md](TERMINAL.md) | 内置终端与后台命令任务：ConPTY、会话池、审批后任务 |
| [CONTEXT_SETTINGS.md](CONTEXT_SETTINGS.md) | 设置中心上下文页：窗口/缓存统计、压缩与记忆配额、配置生效范围 |
| [SKILL_SYSTEM.md](SKILL_SYSTEM.md) | 技能系统与设置中心「技能」：生命周期、统计、归档恢复、外来包隔离与配置 |
| [MODE_ROUTING.md](MODE_ROUTING.md) | 能力档位路由：chat/retrieval/agent 判定、升档协议、模型与配置、入口用法 |
| [RETRIEVAL.md](RETRIEVAL.md) | 混合检索：Ollama/API embedding、sqlite-vec + FTS、评测（默认关闭） |
| [STORAGE.md](STORAGE.md) | 全局业务根、工作目录与 Agent 空间的目录布局与边界 |
| [AGENT_STORAGE.md](AGENT_STORAGE.md) | Agent 独立空间：九类业务目录、路径策略与 API 说明 |
| [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md) | 插件系统与社区：manifest、隔离激活、信任模型、部署指南 |

## 开发者文档（怎么改）

| 文档 | 内容 |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | **模块职责与依赖规则唯一权威**（tests/test_arch.py 强制执行） |
| [REPOSITORY_STRUCTURE.md](REPOSITORY_STRUCTURE.md) | 仓库/Agent 产品边界、代码位置、开发命令与运行数据路径 |
| [PROJECT_INDEX.md](PROJECT_INDEX.md) | 项目导航：目录职责、扩展分类、API 与调用链 |
| [PROMPT_CONTEXT.md](PROMPT_CONTEXT.md) | 提示词与上下文缓存布局：稳定前缀、回合快照、压缩边界 |
| [UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md) | 前端唯一规范：token、组件、`?v=` 纪律、验收清单 |
| [REFACTORING.md](REFACTORING.md) | 已落地重构、HTTP 兼容合同与后续批次边界 |
| [OPERATIONS.md](OPERATIONS.md) | 运维手册：服务管理、备份恢复、升级演练、日志 |
| [VALIDATION.md](VALIDATION.md) | 离线验证：隔离环境、出网闸门、评测与 CI 入口 |
| [ACCEPTANCE.md](ACCEPTANCE.md) | 验收与发布准备：证据、预热/72 小时采样、发布闸门 |

贡献流程见根目录 [CONTRIBUTING](../CONTRIBUTING.md)；安全报告见
[SECURITY](../SECURITY.md)。

## 内部开发台账（如实记录，不代表发布状态）

| 文档 | 内容 |
|---|---|
| [HANDOFF.md](HANDOFF.md) | 跨会话交接入口：能力清单、27 条平台坑、关键文件地图 |
| [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md) | 开发台账：每批做了什么、证据是什么、还缺什么 |
| [LOCAL_AGENT_PLAN.md](LOCAL_AGENT_PLAN.md) | 完整评估与路线计划（含历史评估，以台账最新状态为准） |
| [DEEPSEEK_DEVELOPMENT_OUTLINE.md](DEEPSEEK_DEVELOPMENT_OUTLINE.md) | 开发大纲：D00–D27 任务卡、依赖与验收 |
| [CAPABILITIES_PLAN.md](CAPABILITIES_PLAN.md) | C01–C06 能力完善路线与验证记录 |
| [UI_QA.md](UI_QA.md) | UI 操作验证与浏览器回归记录 |
| [UI_REDESIGN_MIGRATION_PLAN.md](UI_REDESIGN_MIGRATION_PLAN.md) | UI 统一改造历史档案（现行规范已由 UI_DESIGN_SYSTEM.md 接管） |
| [index/](index/) | 自动生成的源码/API/机器索引（`scripts/project_index.py` 产出，勿手改） |
| [evidence/](evidence/) | 可提交的脱敏验证证据（JSON） |

另有两份示例文件被验收手册引用：[acceptance-manifest.example.json](acceptance-manifest.example.json)、
[soak-limits.example.json](soak-limits.example.json)；素材与回滚源在
[ui-migration/](ui-migration/)（工作台旧版快照，仅供追溯）。
