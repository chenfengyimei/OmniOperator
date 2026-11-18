# OmniOperator 当前交接入口

## 当前状态与工作方式

- 开发分支 `v2`，跟踪 `origin/v2`。默认入口是 V2，显式 V1 回退保留。
- 当前工作：目录、代码位置与文档整理。平台适配按职责归组，V2 测试独立分组；业务逻辑、大型文件和原生协议保持。
- Windows 主体开发及旧离线候选已完成；当前源码未统一全量，整个 V2 仍在开发。真实账号/模型质量/设备、干净机器及 72 小时验收分别待办。
- Mac mini 为 Apple Silicon，所有者提供 macOS 27，实际版本在 Mac 用 `sw_vers` 核对。Mac 做主开发机，现有 Windows 保留运行和原生验收环境，两端同步同一分支。
- 当前 Windows 服务、实例、凭据和其他会话的 `PPT/`、`docs/competition/` 保留。根 `src`/`tests` 是本机兼容联接，正式代码与测试分别在 `agent/src`、`agent/tests`。

## 接手顺序

1. 读 [目录规则](REPOSITORY_STRUCTURE.md)、[架构](ARCHITECTURE.md) 和 [测试纪律](TESTING.md)。
2. 当前任务以 [持续开发任务](tasks/v2-development.json) 为准，Windows 阶段见 [候选任务](tasks/v2-windows-candidate.json)；[状态摘要](DEVELOPMENT_STATUS.md)由这两份当前记录生成。
3. 按 [V2 导航](v2/README.md) 找合同与平台断点；迁移到 Mac 后直接粘贴 [接续提示词](V2_MACMINI_RESUME_PROMPT.md)，环境步骤见 [迁移说明](V2_MACMINI_MIGRATION.md)。

## 已完成断点与证据

- Linux Enter 控件状态批 `f539f21e`：194 专项、3 个运行文件 strict 通过，真实 Linux 未验。[证据](evidence/v2-linux-key-state.json)。
- Windows 样例修正 `fbfc16b8`、人工复验 `73970d20`：标准顶层窗口下点击点与遮挡拒绝通过，36 专项。自有窗口完整回归最终文本 `PREFIX-ONLY-OWNED-TEXT`、按钮 1 次，四次生产回执仍为 unknown、不重发。原 STATIC 样例导致跨进程命中，生产拒绝正确，矩形中心候选未保留。[复核证据](evidence/v2-windows-click-fixture.json)。
- 历史 5998 全量仅覆盖 `7e64d348` 固定候选。后续修改、其他会话提交及本次目录迁移均不能复用为当前源码全量通过。

## 下一开发断点

先完成本次目录迁移的相关验证与打包。Mac 接手后优先实际编译 Swift/C，再推进 G5 平台/交付和 G6 业务核对；G3 Linux、G4 Wayland 缺口继续按任务推进。G7 统一全量与最终实机验收留整个 V2 开发冻结后。

## 追溯入口

- [旧交接记录](history/pre-layout-2026-11-18/HANDOFF.md)、[旧重构记录](history/pre-layout-2026-11-18/V2_REFACTOR.md)只证明所属版本，不作为当前待办。
- [目录迁移清单](tasks/v2-layout-migration.json)记录旧新路径与基线指纹；原始 Windows 大产物仍在 `C:/v2-acceptance`，Git 只包含注明的汇总证据。
