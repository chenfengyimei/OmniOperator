# Mac mini 开发环境迁移交接（2026-11-18）

本次迁移接续整个 V2 开发。Windows 开发与离线候选已完成，实机专项记录见 [Windows 报告](WINDOWS_V2_ACCEPTANCE_2026-11-18.md)。前台点击样例的后续核查见 [核查证据](evidence/v2-windows-click-fixture.json)：标准窗口类下原生产点击点与遮挡拒绝通过；完整前台回归仍需人工给予样例焦点。当前源码没有统一全量覆盖，真实账号、模型质量、设备、干净机器与 72 小时证据仍分别待验，整个 V2 未完成。

Mac 作为下一开发主机；Windows 工作区、服务和现有实例保留。迁移准备不表示已在 Mac 安装、编译或验收。

## 1. 带走的内容

- 使用 `v2` 的完整 Git 历史、源码、`uv.lock`、测试和文档。离线包的 `migration.json` 记录精确提交及 bundle SHA256，先核对再导入。
- `docs/HANDOFF.md` 是断点入口，`docs/tasks/v2-development.json` 保留整个 V2 的 G3/G4/G5/G6/G7 开发范围，不能因 Windows 通过将总目标标为完成。
- 原始 Windows 大产物在 `C:/v2-acceptance/win-accept-20261118`，本次后续产物也在 `C:/v2-acceptance`。Git 中汇总记录和本机原件是不同层次；离线包清单会说明实际附带哪些文件。
- 未提交修改、未跟踪的 `PPT/`、`docs/competition/` 不会自动进入 Git bundle。它们保留在 Windows，按迁移清单逐项另行处理，不能以 Git 干净副本冒充整个工作区已搬完。

在 Mac 重新创建 `.venv`、浏览器运行时和原生 helper。Windows 的 `.venv`、DLL、控制台 EXE、本机依赖 wheel 包、运行锁/进程身份和 DPAPI 凭据不能作为 macOS 运行环境使用。模型权重是否另行复制由所有者决定，默认测试不下载或调用真实模型。

## 2. Mac 前置条件

使用正常登录的非 root 账号、macOS 14+、Python 3.12 和 uv，以及 Xcode 命令行工具。在 Mac 本机图形会话操作原生权限；SSH 可用于源码与普通测试，不能作为 AX/屏幕/输入权限证明。

```sh
sw_vers
uname -m
xcode-select -p
xcrun swift --version
python3.12 --version
uv --version
```

芯片未预设：`arm64` 对应 M 系列，`x86_64` 对应 Intel。使用与实际芯片一致的原生 Python，避免把 Rosetta 环境误记为 arm64 验收。

## 3. 导入源码并创建新环境

在线取得当前开发分支：

```sh
mkdir -p "$HOME/Developer"
git clone --branch v2 https://gitee.com/chenfengloveyuri/OmniOperator.git "$HOME/Developer/OmniOperator"
cd "$HOME/Developer/OmniOperator"
git status --short
git rev-parse HEAD
```

离线导入时，在已有 Git 仓库中运行 `git bundle verify /绝对路径/OmniOperator-v2.bundle`，或直接从 bundle 克隆到一个尚不存在的新目录：

```sh
git clone --branch v2 /绝对路径/OmniOperator-v2.bundle "$HOME/Developer/OmniOperator"
cd "$HOME/Developer/OmniOperator"
git remote set-url origin https://gitee.com/chenfengloveyuri/OmniOperator.git
git rev-parse HEAD
```

比较输出与 `migration.json` 的 `source_commit`。离线包不包含 Python 依赖；下列新环境安装需要取得锁定依赖，联网安装与默认离线测试分开：

```sh
if [ -e .venv ]; then
  printf '已有环境保持不变；后续使用 --no-sync。\n'
else
  uv sync --frozen --package omnioperator --extra terminal --extra asr --extra vectors
fi
uv run --no-sync python -c 'import platform,sys; print(sys.executable, platform.machine(), sys.platform); assert sys.platform == "darwin"'
uv run --no-sync python -m playwright install chromium
```

只有新环境执行 sync；已有工作环境继续 `--no-sync`。遇到依赖平台问题，保留原始错误并修复对应平台约束，不在迁移时重新生成锁文件来掩盖差异。

## 4. 启动独立开发实例

```sh
OMNI_MAC_DEV_HOME="$HOME/Library/Application Support/OmniOperator Mac Dev"
test ! -e "$OMNI_MAC_DEV_HOME"
uv run --no-sync python -m omni.composition.cli --home "$OMNI_MAC_DEV_HOME" init
uv run --no-sync python -m omni.composition.cli --home "$OMNI_MAC_DEV_HOME" doctor
uv run --no-sync python -m omni.composition.cli --home "$OMNI_MAC_DEV_HOME" serve --port 8876 --open-browser
```

首次 serve 后沿现有本机令牌引导进入工作台。令牌、主密钥、真实账号及 Windows 路径配置分别在 Mac 显式重新配置，不把 Windows 凭据文件当作可用的 Keychain 条目。默认入口已经是 V2，显式 V1 回退保留，不导入 V1 数据。

默认使用新的开发实例。如果需要已有业务数据，先从 Windows 生产 CLI 导出含 WAL 一致快照与完整引用对象的验证备份，再在 Mac 恢复到另一新目录；按 [备份与升级合同](V2_SCHEMA_UPGRADE.md) 核对版本、对象、外键及 unknown 账目。不要裸复制运行中的 SQLite，也不要删除 Windows 原实例。

## 5. 接续 Mac 原生开发

先读 [架构](ARCHITECTURE.md)、[Mac 原生交接](V2_MACOS_HANDOFF.md)、[系统服务合同](V2_MACOS_SYSTEM_SERVICES.md)。先编译 Swift/C，修复真实 ABI/SDK 问题；不要先用 Windows 注入测试结果宣称 Mac 支持完成。

在本机图形账号上明确构建并安装新的开发签名 helper：

```sh
OMNI_MAC_ARCH="$(uname -m)"
case "$OMNI_MAC_ARCH" in arm64|x86_64) ;; *) exit 1 ;; esac
uv run --no-sync python agent/scripts/build_macos_helper.py --arch "$OMNI_MAC_ARCH" --output "$PWD/temp/build/mac-native-$(date +%Y%m%dT%H%M%S)" --install
```

构建器先运行现有纯核心与原生路径测试，再编译、签名及核对来源。固定安装已有应用时会拒绝覆盖；保留原签名与恢复副本，按原交接处理。AX、屏幕、输入监听和投递权限分别由所有者运行显式请求命令，不能把 Python 或终端权限当作 helper 权限。

开发中按改动做专项，例如：

```sh
uv run --no-sync pytest -q -n 0 agent/tests/test_v2_macos_helper.py agent/tests/test_v2_macos_acceptance.py
```

继续当前架构的业务与平台实现，不重做 Windows/MCP/Linux 已完成部分。Mac 原生观察、语义动作、前台、PTY、Keychain、服务与睡眠/锁屏等分别执行既有隔离验收入口并保存原件。整个 V2 开发冻结后才统一全量，开发中不逐模块全量。

## 6. 切换开发主机的完成条件

Mac 成功导入指定提交，依赖和独立实例启动通过，Swift/C 构建结果已保存，专项原始日志和下一代码缺口已记录；能在 Mac 继续提交并同步授权的 origin/v2。未提交工作已核对处理范围，Windows 原实例与原始验收材料仍可回查。

实际复制/远程启动尚未执行。需要 Mac 的架构、macOS 版本和目标目录；若使用远程连接，另提供已有 SSH 配置名或地址，不提供密码、令牌或私钥正文。
