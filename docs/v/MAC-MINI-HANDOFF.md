# Mac mini 长程开发交接

你负责 OmniOperator 的 macOS 原生能力和交付验收。先在 Mac mini 上编译并复核当前 `v2` 的 helper、TCC、桌面、终端、Keychain 和用户域服务。修复本机能复现的缺口，产出同一 arm64 候选的源码、产物和回执。随后衔接 OmniWeave（OWA）Phase 5 的 macOS 数字身体。每批从现有生产入口做到可复验的结果，并在 `MAC` 任务行记录下一步。

## 接手时的进度

以下快照核对于 2026-11-28。每批以 Mac 实际拉取的 `origin/v2` 和被测提交为准。

| 位置 | 已有进展 | 当前验收边界 |
| --- | --- | --- |
| Gitee `v2` | 基线 `1330ec70` 已收录 Phase 4 公开作业查询、取消、Watchdog 与验收台账。V2 插件化迁移、OWA Phase 3、V2 工作台改造和 WorkWeb 联调版已合入。迁移头仍为 `v2_0064`、167 张声明表。 | Phase 4 源码 `43a28074` 的 Windows 作业与合同 12 项、OWA HTTP 39 项、Watchdog 7 项专项通过。派发前撤权与预算、短时故障与重连、关闭超时仍待专项。最终候选的全量、原生平台和 72 小时需另行取证。 |
| macOS 历史候选 | `b29409d0` 对应的旧 arm64 候选有隔离 wheel、用户域服务、Keychain 和升级回滚回执。旧授权 helper `9360128b` 验过 AX、ScreenCaptureKit、语义与前台样例。 | 当时替换为候选 helper 后权限探针返回 denied，因而实际桌面动作使用了旧 helper。本轮要在**同一新候选 wheel 与同一新签名 helper**上重新取证。 |

[现行状态](STATUS.md) · [OWA 实现](reference/OMNIWEAVE.md) · [Phase 4 验收证据](evidence/owa-phase4-jobs-watchdog-2026-11-28.json) · [2026-11-23 macOS 候选证据](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/v2-macos-release-candidate-2026-11-23.json)

当前基线的功能导航守卫仍断言 226 项 HTTP 操作。源码提取与 OpenAPI 均已匹配 228 项，新增两条为作业查询和取消。固定计数留给 Windows 的共享集成收尾。

## 双设备分工

Mac 负责 Swift/C helper、Darwin 平台适配、TCC 与 GUI 会话、用户域 LaunchAgent 和 Mac 产物生命周期。Windows 会话继续负责 OWA Phase 4 剩余专项、公共执行器、调度和集成。两端直接在各自的 `v2` 检出推进小批提交。涉及共享数据库、协议、依赖锁、CI 或公共组装点时，按 [双设备交接约定](HANDOFF.md) 协调同一批次的修改方。在等待共享变更或所有者的系统操作期间，继续独立的构建、只读检查、隔离用例和文档证据。

接手前核对工作树并保留已有改动。在以 Gitee 仓库为 origin 的干净检出中执行下列命令。先读 [开发规则](../../AGENTS.md)、[架构](reference/ARCHITECTURE.md)、[贡献指南](../../CONTRIBUTING.md)、[桌面合同](reference/DESKTOP.md) 和 [发布合同](reference/V2_PACKAGING.md)。

```sh
git status --short --branch
git fetch origin
git switch v2
git pull --ff-only origin v2
git rev-parse HEAD origin/v2
uname -sm
sw_vers
xcode-select -p
xcrun swift --version
xcrun --sdk macosx --show-sdk-version
uv --version
```

确认当前为 macOS 14+、原生 arm64，并使用普通图形登录账号。工具链须支持仓库的 Swift Testing 门禁。保留已有虚拟环境，用 `uv run --no-sync` 运行专项。全新专用检出需要准备 Python 3.12+ 和锁定依赖时，按贡献指南初始化。记录测试进程的实际架构、系统版本、SDK 和会话类型。

```sh
uv run --no-sync python -c 'import platform, sys; print(platform.machine(), sys.version.split()[0])'
uv run --no-sync python scripts/project_index.py --feature macOS
uv run --no-sync python scripts/project_index.py --feature 发布
```

## 第一批 构建产物和原生专项

以当前源码从零构建 arm64 helper。一个批次使用一个新输出目录，把系统日志、原始回执和构建产物留在私有目录。先独立构建和校验签名，再进入固定位置安装与授权。脚本默认使用开发签名，先保留现有日常 helper。

```sh
mkdir -p temp/build
MAC_RUN_DIR="$(mktemp -d "$PWD/temp/build/omni-mac-accept.XXXXXX")"
uv run --no-sync python agent/scripts/build_macos_helper.py \
  --arch arm64 --output "$MAC_RUN_DIR/helper"
codesign --verify --strict "$MAC_RUN_DIR/helper/OmniOperator Desktop Helper.app"
uv run --no-sync pytest -q -n 0 agent/tests/v2/platforms/macos
```

保存 `foreground-core.xml`、`registration-paths.xml`、helper 构建清单的 SHA-256 和签名后可执行文件的 SHA-256，登记 Swift/C 编译与两组原生测试的真实结果。遇到 SDK、Swift Testing、签名或 ARM 架构故障，按第一处失败修复原生资源或 macOS 适配器，保留身份、权限、超时与回收守卫。变更范围覆盖对应 Python 宿主及 HTTP 调用方。

常用入口如下。

- Swift/C 和前台编排在 `agent/src/omni/adapters/workers/macos/`。
- 固定 helper 身份、清单和启动在 `agent/src/omni/adapters/platform/desktop/macos_helper.py` 和 `macos_launch.py`。
- 用户域服务与 Darwin 查询在 `agent/src/omni/adapters/platform/services/`。按功能索引定位具体组装及 CLI。
- 修改桌面调用时补跑 `agent/tests/v2/http/test_v2_desktop_http.py`，并按改动选跑 `agent/tests/v2/platforms/common/` 的前台、桌面或凭据专项。

本批完成后应有同一提交上的 arm64 编译、签名验证、Swift 测试报告及受影响的 Python 调用方结果。

## 第二批 固定 helper 身份和 TCC 样例

使用已经构建的候选，核对 App Bundle、签名身份、固定安装位置 `~/Applications/OmniOperator Desktop Helper.app` 和相邻的 `.app.build.json` 彼此对应。现有 helper 在用时，先按仓库安装合同准备可恢复副本或隔离 GUI 账号。用脚本产出的清单与探针实际使用的可执行文件互证。若用构建脚本的 `--install`，使用新输出目录，并把这次构建的清单用于后续验收。

1. 在正常登录的隔离图形账号先跑只读 probe，记录各权限状态和当前会话识别。让所有者按现有流程批准该候选需要的辅助功能、录屏、输入监控或发事件权限。重新启动 helper 后复验实际权限。
2. 先跑隔离样例的 AX 观察、密码脱敏和 ScreenCaptureKit 窗口截图。在所有者确认样例窗口后，分别运行 `--mutate-owned-fixture` 和 `--foreground-owned-fixture`，每次使用不同输出目录。用 fixture 的独立状态核对后验，不能证明结果的动作保留 `unknown`，沿原有核查流程处理。
3. 在获准的隔离身份上复核撤权、重新授权、锁屏或睡眠恢复、helper 中断后的拒绝及回收。记录实际覆盖的会话状态。核对 PID、目标窗口及观测代次变化后的写入是否安全失败。
4. PTY 验证 Unicode 读写、退出、重复关闭和子进程回收。Keychain 使用 `agent/scripts/accept_macos_keychain.py --write-owned-item --output` 走新的自有测试命名空间。保留私有清理回执，结果未明时使用 `--cleanup` 核查或清理本次测试项。凭据、清理标识、原始截图和窗口数据留在本机。

以下命令验收与实际就绪的固定 helper 匹配的构建。输出目录应是新路径。

```sh
uv run --no-sync python agent/scripts/accept_macos_desktop.py \
  --build "$MAC_RUN_DIR/helper" --output "$MAC_RUN_DIR/desktop-read"
```

本批的核心证据是**当前候选** helper 的签名和权限状态、样例独立后验，以及断开和撤权后的资源回收。需所有者配合的状态暂缺时，把具体操作和剩余证据记入 `MAC`，继续下一项独立工作。

[历史 helper 与 TCC 手册](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/platforms/macos/V2_MACOS_HANDOFF.md)

## 第三批 隔离 wheel 和用户域服务

从同一 SHA 构建 wheel 与 helper，按 [V2 发布合同](reference/V2_PACKAGING.md) 准备与锁文件一致的本地依赖包。构建缓存齐备后从仓库根运行 `uv build --offline --package omnioperator --wheel --out-dir "$MAC_RUN_DIR/wheels"`。使用独立的非 editable 安装、运行时、实例 home 和本地端口。记录 wheel、依赖清单、helper 和部署计划指纹。验证首次启动、工作台和静态资源、所有者鉴权、重启后实例与数据保留。以正式备份与恢复路径演练升级和回滚，核对 schema、对象和旧回执。

用户域目标使用 `macos-user`。在已核验的不可变安装中走 `service capture/plan/materialize`，先取得只读 `absent` 证据。真实注册和卸载沿用 `service operation-plan` 与精确计划哈希审批。分别记录 `registered`、`loaded`、`ready`，将运行 PID、定义和就绪回执对齐。用正常排空和批准的卸载回执核对最终 `absent`、端口及测试进程无残留、实例数据仍在。

```sh
"$MAC_DEPLOYED_RUNTIME/bin/python" agent/scripts/accept_macos_services.py \
  --bundle "$MAC_SERVICE_BUNDLE" --expect ready \
  --output "$MAC_RUN_DIR/service-ready.json"
```

上面的目录变量由本批已核验部署提供。每次输出使用新文件。若验证交换后的启动，本批再传入匹配的 `--exchange-plan`，复核预定 boot 与当前运行一致。正式候选以用户域服务和自用桌面为本阶段目标。系统域注册和公开分发按 [发布范围](STATUS.md) 决定。Developer ID、公证和干净机 Gatekeeper 取证在范围确定后单列。

[用户域和系统域合同](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/operations/V2_SERVICES.md)

## 第四批 衔接 OWA Phase 5

前几批可以和 Windows 的 Phase 4 专项收尾同时推进。现在就用现有 macOS 适配器准备可控窗体、输入争用和断线夹具，梳理当前身份、授权、资源键、执行器清理及健康观测怎样映射到数字身体。准备中发现公共合同缺口时记清失败场景、现有端口和受影响调用方，交共享集成批次协调。

基于已合入的 Phase 4 实现核对现有端口。Windows 补齐相关 T09 和 T12 缺口并确认资源与恢复合同后，再启用 macOS 数字身体的生产派发。用同一原生桌面资源上的两条独立 Worker 流和可控中断验收以下合同。

- **T07** 同一资源只允许一个有效写持有人。断线时保留可解释的等待，恢复前确认旧执行器已静默，再重新核对授权、观测和新 fence。
- **T08** 取消后保留既有回执和实际清理义务。迟到结果不能覆盖新分配。派发结果未知进入原核查面，重复写入次数为 0。
- **T12** 跳过显式等待和未核查副作用，真实清理完成前保持资源归属，关闭后确认没有额外领取或辅助进程残留。

沿用现有生产端口和唯一业务库。Windows 与 Mac 签核同一可验证资源合同，再扩大启用范围。远端配对试验依赖已确认的设备、端点和授权，作为该端点的单独证据。

[OWA 阶段路线图](OMNIWEAVE-ROADMAP.md) · [技术规格与验收矩阵](architecture/OMNIWEAVE-ARCHITECTURE-SPEC-V1.0.md)

## 候选冻结和交付

开发小步使用 `uv run --no-sync pytest -q -n 0` 验证受影响项及调用方。复现失败优先指定 nodeid。默认套件用临时目录、离线替身和仓库的网络闸门。真实 GUI 动作、凭据写入和服务生命周期在各自的独立验收中取证。共同候选冻结后按仓库纪律协调一次离线全量，并用同一产物重做安装、升级、回滚、卸载和必要平台核查。Mac 上可先跑 `scripts/v2_soak.py` 的短冒烟，正式候选以明确采样阈值和完整 72 小时报告验收。

每批提交前核对 `v2` 的基线与变更归属。提交与推送采用当前双设备协作方式，远端推进时整合后复跑受影响专项。请把进度写入 `docs/v/tasks.json` 的 `MAC` 行，必要时在 `docs/v/evidence/` 登记脱敏证据。每份交付记录被测 SHA、macOS 版本与架构、会话类型、wheel/签名/helper/计划指纹、执行命令、通过和阻塞项、耗时及下一步。生成状态页，增删仓库文件后重生索引。

```sh
uv run --no-sync python scripts/task_status.py
uv run --no-sync python scripts/project_index.py
uv run --no-sync python scripts/task_status.py --check
uv run --no-sync python scripts/project_index.py --check
```

本任务的近期交付是同一 arm64 候选的原生 helper 与用户域服务闭环及可复验证据。共享的权限与恢复合同完成相应专项后，继续到 macOS 数字身体的 T07、T08、T12。正式发布完成条件见 [验收合同](reference/ACCEPTANCE.md)，需要用户在系统上操作的事项应逐项记录并继续推进其余工作。
