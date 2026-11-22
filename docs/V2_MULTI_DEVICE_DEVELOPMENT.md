# OmniOperator V2 双设备协作开发规范

Windows 与 Mac mini 共同迭代 Gitee 的 `v2`。两端各自检出代码、运行独立开发实例，以 `origin/v2` 为集成基线。短期任务分支用来交付单个批次，交付结果统一合回 `v2`。新会话使用 [Windows 和 Mac mini 接续提示词](V2_MULTI_DEVICE_PROMPTS.md)。

## 分工与代码位置

| 设备 | 主责 | 优先使用的现有路径 |
|---|---|---|
| Windows | Windows 原生适配和验收、工作台 UI、通用功能、共享合同及主线集成 | `web/ui/v2/`、`agent/src/omni/adapters/platform/`、`agent/src/omni/adapters/workers/windows_*`、`agent/tests/v2/ui/`、[Windows 运行手册](v2/platforms/windows/WINDOWS_V2_RUNBOOK.md) |
| Mac mini 的 macOS | Swift/C helper、宿主接线、原生构建和权限、服务、终端验收 | `agent/src/omni/adapters/workers/macos/`、`agent/src/omni/adapters/platform/desktop/macos_helper.py`、`agent/scripts/build_macos_helper.py`、`agent/tests/v2/platforms/macos/`、[macOS 交接](v2/platforms/macos/V2_MACOS_HANDOFF.md) |
| Mac mini 上的 Linux 环境 | Linux、X11、Wayland/Portal、无桌面与容器交付 | `agent/src/omni/adapters/platform/desktop/linux/`、`agent/src/omni/adapters/workers/linux_*`、`agent/tests/v2/platforms/linux/`、`deploy/v2/`、[Linux 交接](v2/platforms/linux/V2_LINUX_HANDOFF.md) |

Windows 侧负责共享领域与应用合同、HTTP、能力注册与组装、数据库迁移、锁文件和公共 CI 的集成。平台任务发现必须改共享接口时，先提供具体变化、受影响调用方和测试范围，由两端约定一个提交方。需要两阶段交付时，先合入共享合同，再由平台和 UI 基于该提交接线。数据库迁移由 Windows 集成方按最新迁移头分配编号，处理唯一迁移链及旧库升级验证。

两端统一遵循 [V2 架构](ARCHITECTURE.md)。平台差异走已有端口和适配层。共享业务使用同一套源码与合同，运行实例和数据库按环境隔离。

## 每批 Git 流程

1. **开工。** 核对实际仓库路径、`git status --short --branch`、`git remote -v` 和当前提交。运行 `git fetch origin`，记录 `git rev-parse origin/v2`。从该提交新建单任务分支，如 `codex/windows-<任务>`、`codex/macos-<任务>` 或 `codex/linux-<任务>`。当前检出的是干净且仅落后的本地 `v2` 时，可用 `git merge --ff-only origin/v2` 快进。已有任务先查实际分支和在途状态。
2. **保留并行工作。** 当前 checkout 有其他会话的修改时，保留其工作树和暂存区。需要换基线、切分支或做集成验证时，使用合适的独立 checkout/worktree。遇到同文件或同一合同的在途改动，和当前负责人确认接入点，先推进独立部分。
3. **开发与验证。** 一个批次集中解决一个平台缺口或一项功能。按 [测试纪律](TESTING.md) 跑受影响模块及调用方离线专项。提交前复查文件归属、差异、证据及 `git diff --check`。逐路径提交本批文件。新增或删除文件后运行项目索引生成命令；在干净候选源码上生成提交用索引。
4. **交付。** 普通推送自己的任务分支，随后用 `git ls-remote --exit-code origin refs/heads/<任务分支>` 核对 Gitee 返回的 SHA。接收方取到提交后进入审查。远端缺分支或推送失败时，在原设备保留本地提交和推送日志，按实际鉴权、分支权限或网络错误恢复。需要离线交付时，可用 Git bundle，并核对包的 SHA256 与交付提交。
5. **集成。** Windows 在干净的集成 checkout 获取最新 `origin/v2` 和任务分支，审查实际差异及依赖，验证合并后的源码。有 Gitee PR 时以 `v2` 为目标，附对应专项与原生证据。若主线前进或合并有冲突，先把最新主线纳入候选、核对行为并重跑受影响专项，再按现有权限合并。任务分支用普通推送更新；远程 `v2` 接受已审查的正常合并和快进更新。
6. **回传。** 把实际合入的 `v2` SHA 返回给开发端。两边下一批从更新后的 `origin/v2` 起步，证据保留原来实测的 SHA。

本地开发、任务分支已发布、已合入 `v2` 是三个可核对的状态。分别用本地提交、Gitee 上的分支提交和集成提交确认。共享汇总或索引有冲突时，在合并后的干净树上重新生成，保留各平台各自的验收证据。

## 开发环境与验证

- Windows 当前工作区通常是 `D:/xm/OmniOperator`。Mac 通常是 `~/Developer/OmniOperator`，缺少 checkout 时由 Codex 从 Gitee 克隆。Mac 工具链和独立实例用 [迁移手册](V2_MACMINI_MIGRATION.md) 中的命令初始化。Linux VM 内单独检出同一提交并创建本机环境。
- Git 传递源码、锁文件和脱敏的证据摘要。每个环境单独维护 `.venv`、浏览器运行时、`.env`、服务、实例目录、SQLite、凭据和签名产物。使用隔离开发 home 及测试临时库，保持现有服务正常运行。业务数据确需跨设备迁移时执行 [备份与恢复合同](v2/operations/V2_SCHEMA_UPGRADE.md)。
- 专项统一使用 `uv run --no-sync pytest -q -n 0` 加相关路径或 nodeid。默认离线并保留闸门。多个模块集成收尾或所有者明确要求时，再协调一次全量测试。每项结果写明实际被测 SHA；有未提交变化时另记源码状态。源码变化后按受影响范围重验。
- Windows 报 Windows 原生结果。Mac 报构建架构、SDK/Swift、签名和实际桌面权限/服务/PTY 验收。Linux 报 VM 或实机、架构、发行版、桌面会话和具体能力。注入专项、编译成功、真实调用、验收完成分别按实际结果登记。容器覆盖无桌面核心；X11、Wayland/Portal 和正式部署架构分别在对应环境验证。

## 文档、证据和原始产物

| 位置 | 用途 |
|---|---|
| [`AGENTS.md`](../AGENTS.md)、[`docs/HANDOFF.md`](HANDOFF.md) | 会话入口。Windows 集成方维护跨设备状态和下一批断点，平台批次贡献对应平台的交接。 |
| [`docs/tasks/v2-development.json`](tasks/v2-development.json)、[`docs/tasks/v2-agent-core-parity.json`](tasks/v2-agent-core-parity.json) | V2 全局进度及共享功能任务；集成时按证据更新实际状态。 |
| [`docs/v2/platforms/`](v2/README.md) | 各平台的现行合同、当前断点和验收步骤。优先更新对应的既有交接文档。 |
| [`docs/evidence/`](evidence/) | 可提交的脱敏证据摘要，沿用对应任务已有文件；新能力可使用 `v2-<平台或能力>-<批次>.json`。 |
| `temp/build/<平台>-<批次>-<日期>/` | 开发机本地原始日志、xUnit、构建清单及原生包。沿用已有验收目录时记录机器和本机绝对路径。 |
| [`docs/index/`](index/MODULES.md)、[`docs/DEVELOPMENT_STATUS.md`](DEVELOPMENT_STATUS.md) | 生成内容。分别通过 `scripts/project_index.py` 和 `scripts/task_status.py` 更新。 |

交接时用对应平台文档或证据摘要记录下面几项。大体积原件和含账号、令牌、个人截图或系统凭据信息的材料留在本机。

```text
任务、负责设备、日期
基线 v2 SHA、交付分支与 SHA、Gitee 回读状态、集成 SHA（合并后）
已实现内容、触及的共享合同及调用方
专项命令、通过/失败/跳过与耗时、当前源码的全量覆盖情况
原生环境和实测结果、未验证事项、证据摘要和本机产物路径
剩余缺口、下一批负责人和可执行断点
```
