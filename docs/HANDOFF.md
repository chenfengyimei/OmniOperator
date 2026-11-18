# OmniOperator 当前交接入口

## 当前状态与工作方式

- 开发分支 `v2`，跟踪 `origin/v2`。默认入口是 V2，显式 V1 回退保留。
- 当前工作：目录整理已完成；按 [Agent 内核与学习项目对标](v2/contracts/V2_AGENT_CORE_PARITY.md)持续补齐。K03 [修订化任务清单](v2/contracts/V2_TODOS.md)已接生产模型控制、同库持久化和所有者只读 API；下一项 K04 父子 run 委派仍未实现。
- Windows 主体开发及旧离线候选已完成；当前源码未统一全量，整个 V2 仍在开发。真实账号/模型质量/设备、干净机器及 72 小时验收分别待办。
- Mac mini 为 Apple Silicon，所有者提供 macOS 27，实际版本在 Mac 用 `sw_vers` 核对。Mac 做主开发机，现有 Windows 保留运行和原生验收环境，两端同步同一分支。
- 当前 Windows 服务、实例、凭据和其他会话的 `PPT/`、`docs/competition/` 保留。根 `src`/`tests` 是本机兼容联接，正式代码与测试分别在 `agent/src`、`agent/tests`。

## 接手顺序

1. 读 [目录规则](REPOSITORY_STRUCTURE.md)、[架构](ARCHITECTURE.md) 和 [测试纪律](TESTING.md)。
2. 当前任务以 [持续开发任务](tasks/v2-development.json) 为准，内核对标子任务见 [缺口清单](tasks/v2-agent-core-parity.json)，Windows 阶段见 [候选任务](tasks/v2-windows-candidate.json)；[状态摘要](DEVELOPMENT_STATUS.md)由当前任务记录生成。
3. 按 [V2 导航](v2/README.md) 找合同与平台断点；迁移到 Mac 后直接粘贴 [接续提示词](V2_MACMINI_RESUME_PROMPT.md)，环境步骤见 [迁移说明](V2_MACMINI_MIGRATION.md)。

## 已完成断点与证据

- Linux Enter 控件状态批 `f539f21e`：194 专项、3 个运行文件 strict 通过，真实 Linux 未验。[证据](evidence/v2-linux-key-state.json)。
- Windows 样例修正 `fbfc16b8`、人工复验 `73970d20`：标准顶层窗口下点击点与遮挡拒绝通过，36 专项。自有窗口完整回归最终文本 `PREFIX-ONLY-OWNED-TEXT`、按钮 1 次，四次生产回执仍为 unknown、不重发。原 STATIC 样例导致跨进程命中，生产拒绝正确，矩形中心候选未保留。[复核证据](evidence/v2-windows-click-fixture.json)。
- 历史 5998 全量仅覆盖 `7e64d348` 固定候选。后续修改、其他会话提交及本次目录迁移均不能复用为当前源码全量通过。

## 本次目录整理验证

108 个平台实现按职责归组，378 个测试/既有助手文件迁移，108 个共享提供方提取；V1 实现及原生资源位置保留。原 7354 个测试节点全部保留，新增 7 个回归后收集 7361 个，无重复。分阶段 397 个不同专项节点通过，386 文件 strict 零错误/警告，Ruff 与格式检查通过。直接 wheel、sdist→wheel、124 个界面资产及隔离 V2 导入/关闭通过；原服务包数量失败保留并已精确修正验证。

Windows 自有窗口点位与遮挡、ConPTY 原生专项已执行；未重复前台系统输入，未在 Mac 编译或执行，未全量。279 份既有证据/原生资源/迁移文件内容保持；并行 UI 提交 `2bdab425` 更新其自身证据，已保留，补 6 项现有界面/目录节点与新资源包通过。详见 [整理证据](evidence/v2-layout-reorganization.json)和 [旧新路径清单](tasks/v2-layout-migration.json)。

## 下一开发断点

所有者新增完整对标要求；K03 已完成读写/合并、修订冲突、层级/容量限制、压缩/重启恢复与来源删除屏障，见 [清单证据](evidence/v2-todos.json)。完成标记不授予权限、不证明外部动作成功。当前没有清单编辑 UI。

下一批推进 G6.core_parity / K04 父子 run 委派，再做 K05 批次并行。现有 `runs.parent_id` 只有关系基础，尚无实际委派生产链；不能将 child 放回父会话 FIFO，也不能让父任务占满全局名额后等待无法被领取的孩子。独立上下文、父子费用/深度/数量限制、授权继承和取消/关闭实际汇合须先接清楚。多 Agent 空间、任务清单、显式记忆整理和有限抽帧均不能替代相应缺失功能。

K03 新增 34 项回归；首轮冻结源码 114 项集成专项通过（182.82 秒），收尾补强 Agent 禁用检查后最终 34 项清单/HTTP 模块专项通过（39.09 秒），另 7 项目录/状态守卫通过。最终 391 文件 strict 零错误/警告，Ruff 与格式检查通过，未全量。每轮源码指纹与失败均保留，不把首轮结果写为最终源码全量。此前 K01/K02 批 `c4e205d8` 的结果见 [固定证据](evidence/v2-agent-core-parity.json)。测试只用隔离数据库与冻结源码，当前 Windows 服务和真实数据库未停止或由测试升级。

会话级缓存稳定、run 内工具压缩、停止前验核和回合后学习仍待实现；未全量、未真实模型效果验收。

目录迁移、专项和打包已完成；Mac 接手还需实际编译 Swift/C，再推进 G5 平台/交付，G3 Linux、G4 Wayland 缺口继续按任务推进。G7 统一全量与最终实机验收留整个 V2 开发冻结后。

## 追溯入口

- [旧交接记录](history/pre-layout-2026-11-18/HANDOFF.md)、[旧重构记录](history/pre-layout-2026-11-18/V2_REFACTOR.md)只证明所属版本，不作为当前待办。
- [目录迁移清单](tasks/v2-layout-migration.json)记录旧新路径与基线指纹；原始 Windows 大产物仍在 `C:/v2-acceptance`，Git 只包含注明的汇总证据。
