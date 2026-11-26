# 开发交接

先看 [当前状态与未完成项](STATUS.md)，再核对本机分支、提交和实际源码。任务编号、下一步、完成条件和已有证据统一维护在 [tasks.json](tasks.json)。同一事项沿用同一行，完成时附被测提交、平台与证据，再生成状态页。

## 当前接续顺序

拿到功能需求后，在仓库根用一句任务或一个功能名定位生产入口、组装点、改动影响和专项命令。例如：

```powershell
uv run --no-sync python scripts/project_index.py --feature 修改插件授权
uv run --no-sync python scripts/project_index.py --feature 修改技能回滚
uv run --no-sync python scripts/project_index.py --feature 排查安卓截图
```

也可浏览[功能开发地图](index/FEATURES.md)或[技能、插件和 MCP 开发手册](reference/EXTENDING.md)。接手 V1 独立 QQ 命令查 `omni-qq`，接手 V2 工作台 QQ 通道查 `qq`。在对应功能的专项运行通过后，把本次验证范围登记到任务行。

接手内核从「运行内核」「学习」「上下文」三个功能的入口查起。当前开发优先代码逻辑和提供者组装，使用已有环境和离线替身。KERNEL-EVAL 保留已采集的模型结果，另行安排真实评测。恢复、核验和评测规则见 [V2 内核合同](reference/KERNEL.md)。

接手 OmniWeave 时查询 `--feature OWA`。从主会话观察链路、任务作用域和 Phase 2 的 Router／Worker 验收条件读起。[OWA 开发与交接](reference/OMNIWEAVE.md)

添加产品工具时查询 `--feature 能力注册`。接入传输或后端时分别查询「模型提供者」「渠道提供者」「平台提供者」。挂载、授权和关闭规则见 [V2 能力插件与组装](reference/CAPABILITY_PLUGINS.md)。

迁移完整业务功能时，从 Android 或媒体的安装器和路由工厂读起。后台功能参考调度与 QQ 的启动和回滚，共享传输参考投递的排空与租约释放，跨平台资源参考桌面与终端。跨功能依赖参考动作核查对调度服务的读取，按回合工具参考可执行插件和 MCP 的授权与宿主静默回调。[功能迁移说明](reference/CAPABILITY_PLUGINS.md#业务功能登记与启动顺序)

需要依赖已装配服务或读取工具快照时，参考视觉、学习和维护的工作流安装。模块范围、推进顺序和收尾条件维护在 [V2 业务插件化迁移](PLUGIN_MIGRATION.md)。

新增离线运维或部署命令时，在 `composition/commands/` 内维护参数和执行入口，再接入 CLI 命令目录。按原有审批、锁和退出码核对调用方。安装、服务与发布的代码路径可查询 `--feature 发布`。[CLI 命令登记](reference/CAPABILITY_PLUGINS.md#cli-命令登记)

1. 每批代码调整记录改动、专项与调用方覆盖。发布冻结时由 Windows 集成会话核对 `origin/v2`、工作树和候选差异，打包并登记新的候选。
2. 可以并行安排干净 Windows、测试账号、安卓真机、兼容视觉模型、Mac mini 和 Linux 原生会话。每项结果都记具体设备、提交、产物和未覆盖范围。
3. 最终候选确定后统一执行离线全量、官方产物生命周期和 72 小时验证。旧长跑可以继续留存；把它登记在实际运行的候选名下。

## Windows 主机

负责 Windows 原生能力、Web、通用内核与跨平台集成。接手时先检查工作区并保留其他会话的修改，从最新 `origin/v2` 起短名分支。合入 Mac 或 Linux 批次前，核对远端分支 tip、实际差异与调用方专项。已有服务与测试实例分开运行。

## Mac mini

负责 macOS 原生 helper、TCC、用户域服务和交付实测。Linux 桌面与容器在独立 Linux 环境验证，记录架构和会话类型。开始前核对 arm64 环境、基线和工作树，交接时提供任务分支、SHA、原生结果及下一项所需资源。

两端均以 `v2` 集成，任务分支直接用短名，例如 `macos-helper-tcc`。同一批共享数据库、协议、依赖锁或 CI 变更由一个会话负责。每批跑受影响模块与调用方专项，按 [开发规则](../../AGENTS.md) 和 [测试纪律](reference/TESTING.md) 协调全量。

## 文档怎么放

| 内容 | 维护位置 |
|---|---|
| 项目介绍 | `docs/README.md` |
| 开发计划、进度、未完成项 | 编辑 `docs/v/tasks.json`，运行 `uv run --no-sync python scripts/task_status.py` 更新 `docs/v/STATUS.md` |
| 跨设备分工、下一会话的接手步骤 | 更新本页；需要独立计划或交接时直接建在 `docs/v/` 下 |
| 工作台读取的手册、受测试约束的合同 | `docs/v/reference/` |
| 源码、路由与工具索引 | `docs/v/index/`，由 `uv run --no-sync python scripts/project_index.py` 生成 |
| 功能名、别名、关键路径与影响 | 编辑 [features.json](features.json)，用同一条命令再生；`--check` 检查路径、符号、页面覆盖和生成差异 |
| 新验收证据 | 需跨设备复核的脱敏摘要可建在 `docs/v/evidence/`；任务行关联对应提交和产物 |

旧版 V1、V2、历史计划和证据的本机原件在 `docs/temp/`，该目录由 Git 忽略。`docs/temp/归档索引.json` 记录原路径、归档位置和 SHA-256。其他机器可从整理前的 [固定版本文档树](https://gitee.com/chenfengloveyuri/OmniOperator/tree/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs) 查阅已提交的材料。本机整理前的未提交修订随本机归档保存。
