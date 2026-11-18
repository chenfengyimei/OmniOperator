# Mac mini 新会话接续提示词

所有者自行复制项目或通过 Git 的 `v2` 分支迁移后，将下方完整文本粘贴给 Mac mini 上的新会话。项目目录和系统版本由接手会话在本机读取。迁移步骤见 [开发环境交接](V2_MACMINI_MIGRATION.md)，原生实现断点见 [macOS 交接](v2/platforms/macos/V2_MACOS_HANDOFF.md)。

```text
你接手 OmniOperator V2 的持续开发，请实际阅读代码并接着实现，完成一个可交付的开发批次，不要只回复计划，也不要重做已有成果。

目标与当前阶段：
- 所有者已完成 Windows 会话的本地收尾，源码、证据、任务记录和本提示词通过项目一起交接。项目迁移由所有者自行完成；macOS 版本、项目路径、工具链和环境状态由你在 Mac 本机读取。
- 当前主线是 v2，远程为 https://gitee.com/chenfengloveyuri/OmniOperator.git。先核对 pwd、git status、当前分支和 HEAD，使用实际导入的源码，不假定 Windows 的绝对路径在 Mac 存在。
- 当前代码布局已整理：平台 Python 适配按 desktop/services/releases/runtime 归组，V2 测试位于 agent/tests/v2，复用助手在 omni_test_support/v2；先读目录迁移清单，V1 和原生资源位置保留。
- Mac mini 已确认是 Apple Silicon（M 系列），所有者告知系统版本为 macOS 27；接手后用 sw_vers 核对实际版本及 SDK。使用原生 arm64 终端、Python 和 helper。
- 优先完成整个 V2 的开发，再统一全量测试、修复与最终验收。Windows 主体开发和旧离线候选已完成；整个 V2 仍在开发，不能将 Windows 阶段完成当作全项目完成。

先按顺序读取：
1. AGENTS.md、docs/HANDOFF.md、docs/ARCHITECTURE.md、CONTRIBUTING.md。
2. docs/tasks/v2-development.json 和 docs/tasks/v2-windows-candidate.json。
   所有者新增完整对标 xuexi 要求：读 docs/v2/contracts/V2_AGENT_CORE_PARITY.md 与 docs/tasks/v2-agent-core-parity.json。K03任务清单已接生产、持久化与压缩/重启/删除恢复，不要重做；先读 docs/v2/contracts/V2_TODOS.md 和 docs/evidence/v2-todos.json。V2内核仍未完整对标，下一项是K04父子run委派，随后批次并行、run内压缩和回合后学习；这些通用内核开发可直接在Mac继续。xuexi为忽略目录，不假定Git迁移已带上参考仓库，固定参考版本记录在对标文档。
3. docs/V2_MACMINI_MIGRATION.md、docs/v2/platforms/macos/V2_MACOS_HANDOFF.md、docs/v2/platforms/macos/V2_MACOS_SYSTEM_SERVICES.md。
4. 本批实际改动涉及的合同、实现、调用方和现有测试。

已完成的断点与证据：
- 本次目录整理已经完成：108 个平台实现归组、原 7354 个测试节点保留，新增 7 个后收集 7361 个；386 文件 strict 与打包通过，当前源码未全量。按 docs/evidence/v2-layout-reorganization.json 和 docs/tasks/v2-layout-migration.json 读取具体结果。
- Linux Enter 控件状态批已提交 f539f21e：194 项专项通过、3 个运行文件 strict 通过；仍有通用 Linux/Wayland 开发，不能据此声称 Linux 原生已验收。
- Windows 点击复核和样例修正已提交 fbfc16b8，人工前台复验与 arm64 交接记录已提交 73970d20；后续本地收尾提交以当前 Git 为准。
- 原 STATIC 顶层样例造成 UIA 跨进程命中，生产拒绝正确；已改为标准窗口类 DefWindowProcW。原生产点击点及真实遮挡拒绝通过，矩形中心候选已撤回，生产 windows_foreground.cs 保持原字节。不要重新实现已撤回的回退方案。
- 2 项新增与 34 项调用方共 36 项专项已通过。人工配合焦点后的完整自有窗口回归通过，最终文本 PREFIX-ONLY-OWNED-TEXT，按钮仅点击 1 次；4 次生产动作回执全为 unknown，保留原回执且未重发。样例实际状态不等于生产后验已确认。
- docs/evidence/v2-windows-click-fixture.json 记录最终结果、指纹及首次焦点超时失败；旧报告中的“普通 Win32 按钮永久拒绝缺陷”结论以后续复核为准，旧失败保留。
- 最近人工复验的冻结运行源码与提交核对了 679 个文件。当前源码没有统一全量覆盖；历史 5998 通过只覆盖 7e64d348 固定候选，不能用于新源码。
- Windows 原始大产物保留于 C:/v2-acceptance，原服务和实例保留；Git 中有汇总证据，但这些 Windows 本机绝对路径不代表原件已复制到 Mac。

在 Mac 上先完成的开发批次：
1. 核对 macOS 14+、uname -m 为 arm64、Xcode 命令行工具和 Swift。按迁移文档创建全新本机依赖环境；已有环境保持原状。确认环境内 Python 的 platform.machine() 为 arm64。
2. 优先用现有 agent/scripts/build_macos_helper.py 实际编译 Swift/C，运行构建器已有的 ForegroundCore 与 NativeIdentityTests，并保存原始日志。先构建到新的输出目录，例如：
   uv run --no-sync python agent/scripts/build_macos_helper.py --arch arm64 --output "temp/build/mac-native-arm64-$(date +%Y%m%dT%H%M%S)"
3. 修复真实编译、ABI、SDK、签名或文件身份问题，做必要的相关 Python/Swift 专项。Swift/C 在 Windows 尚未编译，注入测试通过不证明 Mac 支持完成。
4. 按 docs/v2/platforms/macos/V2_MACOS_HANDOFF.md 接续原生观察、截图、语义动作、前台输入、PTY、Keychain、服务注册/查询/交换等隔离验证和开发。helper 安装、TCC 权限与需要所有者配合的前台样例按现有步骤分别处理；已有应用不覆盖，unknown 不重放。
5. 持续推进任务中的 G5 macOS/交付与 G6 业务、扩展、运维核对。G3 Linux 和 G4 Wayland 仍有明确缺口；适合在当前环境实现的源码可继续，真实平台结果分别登记。G7 等整个 V2 开发冻结后执行。

开发与验证纪律：
- 保留所有其他会话未提交修改。Windows 收尾时 PPT/、docs/competition/ 为其他会话未跟踪资料，未纳入 Git；若所有者另行复制到 Mac，继续保留。
- Mac 主要开发，当前 Windows 保留服务与原生验收，通过同一 v2 分支同步。开发中只跑受影响模块及调用方，统一 uv run --no-sync pytest -q -n 0 agent/tests/v2/<分组>/test_<模块>.py。失败优先重跑具体 nodeid，保留首次失败；不跳过、不降低断言、不缩短被测超时。
- 每个小模块或文档修改后不跑全量。整个 V2 开发冻结后再统一 uv run --no-sync pytest -q，不同时启动多个全量；不能把专项通过写成全量通过。
- 已有运行环境不执行 uv sync，不为测试停止用户服务。新的 Mac 环境按锁文件单独创建，不复制 Windows .venv、原生二进制、DPAPI 凭据或运行中的 SQLite。
- 复用当前四层架构和现有数据库、审批、来源、预算与资源归属链，已派发不明保持 unknown。真实账号、模型质量、设备、干净机器和 72 小时验收分别待验。
- 新增或删除仓库文件后运行 uv run --no-sync python scripts/project_index.py。每批更新 HANDOFF、对应任务与证据，明确实现、离线专项、原生实测和未验证范围。
- 所有者已授权持续普通推送到上述 origin 的 v2 分支，不强推。只提交本任务文件，提交前核对并行改动。

请先给简短的实际检查结果，然后直接实现当前最有价值的 Mac 开发缺口；完成后报告提交、修改目的、专项范围、原生结果及下一断点。仅在整个 V2 的要求真正完成后标记总目标完成。
```
