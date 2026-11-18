# 当前开发状态

> 由 docs/tasks/v2-development.json 与 docs/tasks/v2-windows-candidate.json 生成；历史证据只属于其固定源码。

整个 V2 仍在开发，当前源码未统一全量。默认入口为 V2，显式 V1 回退保留。

| 任务 | 内容 | 当前状态 | 合同与证据 |
|---|---|---|---|
| G1 | 持久事件通知及工作台断线续接 | 已实现／专项通过 | [V2_EVENT_STREAM](v2/contracts/V2_EVENT_STREAM.md)、[v2-event-stream](evidence/v2-event-stream.json) |
| G2 | 核对并补齐多Agent空间的实际用户入口 | 已实现／专项通过 | [V2_AGENTS](v2/contracts/V2_AGENTS.md)、[v2-agents](evidence/v2-agents.json) |
| G3 | Linux持续窗口身份与显式前台控制 | 开发中 | [V2_LINUX_HANDOFF](v2/platforms/linux/V2_LINUX_HANDOFF.md) |
| G4 | Wayland独立控制授权、撤回与用户输入暂停 | 开发中 | [V2_WAYLAND_CONTROL](v2/platforms/linux/V2_WAYLAND_CONTROL.md) |
| G5 | macOS、Linux、无桌面及容器交付实现收口 | 开发中 | 见持续开发任务 |
| G6 | 按原V2需求逐项核对剩余业务、扩展与运维生产链 | 代码核对中 | 见持续开发任务 |
| G7 | 整个V2开发冻结后统一全量、缺陷修复和最终交接 | 等待开发完成 | 见持续开发任务 |

## Windows 阶段与后续验收

Windows 主体开发与旧离线候选完成。历史 5998 全量只覆盖 7e64d348；不代表目录迁移后的当前源码。

自有窗口前台复验已通过，生产 unknown 回执保留。真实账号、模型质量、设备、干净机器及 72 小时仍待验。

Mac mini 主开发，现有 Windows 保留运行和原生验收环境。开发中执行受影响专项，整个 V2 开发冻结后统一全量。

当前真值：[持续开发任务](tasks/v2-development.json)、[Windows 阶段](tasks/v2-windows-candidate.json)。

历史交付范围：[旧 R/D 台账](tasks/v2.json)。目录与接手：[HANDOFF](HANDOFF.md)、[V2 导航](v2/README.md)。
