# V2 真实验收矩阵（2026-11-23）

> 结论先行：本矩阵只记录**实际执行**与**可核对证据**。候选 `8412e773`（origin/v2）上，Windows 安装检查与安装→更新→回滚→卸载链已实机执行；Windows 原生服务因产品缺陷**失败**；QQ/NapCat、安卓真机、B 站、模型质量、干净机器均**未执行**（缺机器/账号/窗口）；72 小时长跑**仅完成 3 分钟采样器试跑**。`whole_v2_complete=false`，`production_release_ready=false`。
>
> 纪律：失败不改写为通过；首次失败与续跑成功分别保留；证据不含凭据、消息正文或像素；不复用旧候选的全量结果冒充当前候选。

## 1. 候选身份与冻结事实

| 项 | 值 |
| --- | --- |
| 会话开始时的 origin/v2 | `08dd15734373cb80e113de71238dd42031f20110` |
| 会话期间分支前进 | `8412e773`（2026-11-23 14:40:14，合并 Linux 身份交付） |
| 本矩阵候选 SHA | `8412e773e75808a4e2b04ff221bb8cc312ed9899`（= 冻结时 origin/v2 = v2） |
| 候选 wheel | `omnioperator-0.1.0-py3-none-any.whl`，SHA256 `199072c6fc455a47a7e6d67ad696437050804a7269d7248af900883118e72a6c`，2,818,183 字节 |
| 上一版本 wheel | `0e33995b39ab72b6304fd34e30891f2c7307a0f51e977ffb59360393d1b73f41`，源码 `1f31e96d`（同 schema v2_0052） |
| 依赖包 manifest | SHA256 `ddc242906492a815e1893505f0453f922f2c218dda441ad0088effb9f5e1225f`，110 wheels，223,538,734 字节（`uv.lock` 自 1e62fe08 起未变） |
| uv.lock SHA256 | `3900d190babf4b0cfcb42d1e6b82d5ba2ced7a34fe69d28a8f09b5e008f58321` |
| 构建来源 | 干净 worktree `C:/v2-acceptance/c3-08dd1573/source-8412e773`（`uv build --offline`，构建可复现） |
| 基础解释器 | `C:/Users/cy/AppData/Local/Programs/Python/Python312/python.exe` 3.12.10，确认不能 import omni/sqlalchemy |
| schema | `v2_0052`（163 表，新装 doctor integrity=ok） |
| 工作树 | 仅 `temp/_inspect.py`、`temp/worktrees/` 未跟踪；他人改动已由对方提交，不纳入本候选声明 |

> 会话中途 origin/v2 从 `08dd1573` 前进到 `8412e773`。本矩阵以 `8412e773` 为候选**重新构建 wheel 并重跑**安装检查与安装链；`08dd1573` 的中间结果（wheel `7132c57f`）只用于服务缺陷取证，并用 git blob 证明缺陷代码在两版一致。

## 2. 验收矩阵

状态口径：`通过` = 合同 DoD 满足且有证据；`部分通过` = 实际执行但存在未关闭缺陷；`失败` = 产品行为不符合合同；`阻塞` = 缺外部资源；`未开始` = 有前置未满足。

| 项 | 环境/前置 | 候选 SHA | 结果 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- |
| C3.1-W1 干净目录离线安装（check_v2_installation） | 同机全新目录 + 固定基础解释器 + 离线依赖包 | `8412e773` | **部分通过**：base/install/manager/candidate/launcher/current 六阶段 verified；首次卸载 failed（rc=1，stderr 150B，SHA `98b1d782…`）；延迟 40s 外部维护器续跑成功 | `docs/evidence/v2-windows-install-check-2026-11-23.json`；`C:/v2-acceptance/c3-08dd1573/fresh-install-final/report.json` | 首次失败与此前两轮（`a6e5b33a`、`7132c57f`）同一 stderr SHA，确定性；checker `verified=false`，不得称通过 |
| C3.1-W2 install→update→rollback→uninstall 链 | 同机隔离 home，独立 manager，离线 | `8412e773` | **通过（带卸载重试）**：上一版安装 → stage/prepare/environment/install 候选 → live rollback → 双候选卸载（数据/备份保留） | `docs/evidence/v2-windows-install-chain-2026-11-23.json`；`C:/v2-acceptance/c3-08dd1573/chain3/transcript.json` | 更新含 backup + rollback-check 还原校验；rollback 只换代码指针、不恢复数据库；卸载首次 `cleanup_pending`，外部维护器延迟重试成功 |
| C3.1-W3 真正干净机器 install→update→rollback→uninstall | 干净 Windows OS/VM、无开发工具、管理员 | — | **阻塞** | `probes/environment.txt` | 本机无管理员、无 Windows Sandbox、无可用 VM；同机隔离不能替代干净 OS |
| C2.7 Windows 原生服务（Task Scheduler 用户任务） | 不可变部署 + 真实注册 | `8412e773`（模板 blob 与 08dd1573 相同） | **失败（产品缺陷 D10+D11）**：capture/plan/materialize/verify 通过；`apply-operation` 返回 unknown，任务未创建（D10：COM `0x8004131A (1,40) 无法切换编码`）。修掉编码后重走演练：任务可创建，但 `definition_matches=false`（D11：真实导出省略默认值） | `docs/evidence/v2-windows-service-defect-2026-11-23.json`；`C:/v2-acceptance/c3-08dd1573/probes/service-com-probe.txt`；`C:/v2-acceptance/c3-08dd1573/soak/probes-service-rehearsal.txt` | D10 可由去声明/UTF-16 声明修复；D11 需让 `Same-Definition` 把省略的默认值视为等价。两处都修前服务不可用 |
| C3.2 QQ/NapCat 扫码、白名单、收发、断线重连 | 真实 QQ 登录 + 对端账号 + 窗口 | `8412e773` | **阻塞**：NapCat 进程在跑、WebUI 6099 可访问，但 `GetQQLoginInfo` 为空、`OB11Config` 返回 `Not Login`，OneBot WS 3001 未监听 | `docs/evidence/v2-qq-napcat-probe-2026-11-23.json`；`probes/napcat.txt` | 未扫码、未重启 NapCat、未发送；无对端账号与窗口 |
| C3.5 安卓真机输入/Unicode/滚动/恢复 | 已授权安卓设备 | — | **阻塞**：`adb devices -l` 与 `adb mdns services` 均为空；无 USB ADB 设备；AVD 存在但无 emulator 可执行文件 | `docs/evidence/v2-android-device-probe-2026-11-23.json`；`probes/android.txt` | 真机与模拟器都不可用；不得用桌面假设备替代 |
| C3.3 B 站账号读取/发表审批链/风控暂停 | 真实 B 站测试账号 | — | **阻塞** | — | 无测试账号与窗口；C1.10/C1.12 也未实施 |
| C3.4 模型质量（人格一致性、信笺/反思/问题措辞） | 真实模型 + 预算 + 评审 | — | **阻塞** | — | 无 API 预算与评审窗口 |
| C3.6 72 小时长跑 | 最终冻结 + 所有者批准 limits + 连续 72h | — | **未开始（工具试跑已验证）**：19 次采样/0 失败，仅 `duration_below_72h`；正式 72h 未开始 | `docs/evidence/v2-soak-dry-run-2026-11-23.json`；`C:/v2-acceptance/c3-08dd1573/soak/soak-dryrun.log` | 需先完成 D10 修复与候选冻结；limits 8 字段须所有者批准（已给实测验值建议） |
| C3.7 三平台原生 | Windows/Linux/macOS 实机 | 旧候选 | **未在本候选重跑**：Windows 原生读取/终端/前台沿用 `1e62fe08` 证据；Linux 见复用；macOS 由另一会话 | `docs/v2/platforms/windows/WINDOWS_V2_ACCEPTANCE_2026-11-18.md`；`docs/evidence/v2-cdeg-integration.json` | 旧 SHA 证据不能写成当前候选通过 |
| C2.8 容器真实 build/run | Docker engine | — | **阻塞** | `probes/environment.txt` | Docker CLI 存在但引擎未启动；未构建/运行镜像 |
| C4.2 Windows 签名 / macOS 公证 | 代码签名证书 / Apple 凭据 + Mac | — | **阻塞（待所有者决策）** | `probes/environment.txt` | 当前无代码签名证书、无 APPLE_ID/APPLE_TEAM_ID、无本机 macOS；可降级为源码交付 |
| C4.3 升级/回滚演练（release/exchange 链） | 冻结候选 + 两套不可变安装 | `8412e773` | **部分**：`installation` live rollback 已验证；`release prepare/select`、`exchange apply` 未演练 | `docs/evidence/v2-windows-install-chain-2026-11-23.json` | 完整发布回滚仍待 C4.3 |

## 3. 复用的 C/E/G 专项实测结果

> 复用边界：以下结果由对应批次在其合并 SHA 上实际执行。候选 `8412e773` 相对全量绿 SHA `b395cd97` 只增加 **195 行/8 行删除，7 个文件**（Linux dbus 枚举 4 行 + Linux 身份 46 行 + 测试 151 行），无迁移、无 Windows/QQ/Android 代码变化。Linux 平台组在合并提交中记录 1408 通过；**候选本身没有重跑全量**，最终冻结时必须补一次。

| 批次 | 分支/合并 | 专项结果 | 覆盖 |
| --- | --- | --- | --- |
| C 视频分镜 | `k16-storyboard` `489d093b` → `f1469679` | 120 passed（媒体 scenes/frames/cells/worker/plans） | 场景渐变、letterbox/pillar、邻域尖峰、暗部增益、采样上限 |
| D Android/人格 | `k14-visual-persona` `fa1bac0b` → `30310e60` | 77 passed（前缀/初始门/视觉/SoM/explore/android 集成） | 离线核心与人格前缀；未接生产 |
| E 读缓存/D8/D9 | `k18-read-cache` `019f0bce` → `3655d269` | 169 passed（读缓存/动态/收藏夹/B 站读与发表/网关/worker/风控/HTTP） | 只缓存 video/whois；凭据换代清桶；风控先于缓存 |
| G Linux/容器 | `linux-container-g3` `8bb1f0a2` → `b3bcdafd` | 55 passed + 1411 passed/1 skipped（Linux 平台+架构，271.46s） | SIGTERM/SIGINT 退出 0；dbus 枚举修复 |
| 全量（集成基线） | `b395cd97` | 8618 tests / 8609 passed / 0 failed / 9 skipped（2559s，4 worker） | 四批合并+唯一红修复；JUnit `C:/v2-acceptance/integration-cdeg/full-b395cd97.xml` |
| 候选新增 Linux 身份 | `8412e773`（219fe88d 合并说明） | 1408 passed（Linux 全组，批次记录；本会话未独立重跑） | a11y guid 元数据化、DM Xorg `-seat` 回退、X11Socket 引用修复 |

证据：`docs/evidence/v2-cdeg-integration.json`。

## 4. 逐项执行摘要

### 4.1 干净目录离线安装检查（C3.1-W1）

- 命令：`scripts/check_v2_installation.py --wheel 199072c6… --sha256 199072c6… --dependency-bundle <dependencies> --dependency-sha256 ddc242… --output fresh-install-final --base-python <Python312>`
- 阶段：base、install、manager、candidate、launcher（真实 `omni.cmd doctor`）、current 全部 verified；uninstall 在 2 秒内失败（150 字节 stderr，SHA `98b1d782…`，rc=1）。
- 续跑：等待 40 秒，用 manager venv 外部解释器执行同 SHA `installation uninstall`，36.5 秒成功：`removed=true, data_kept=true, backup_kept`；卸载后 DB SHA `4fc80c9c…` 与备份 manifest SHA `bb70a449…` 均与卸载前一致。
- 判定：安装与保留数据卸载路径可用；首次卸载失败是确定性环境/产品问题，未定位到文件占用者，保留为缺陷/债务，不写成 `verified`。

### 4.2 安装→更新→回滚→卸载链（C3.1-W2）

- 上一版 `0e33995b`（`1f31e96d`）用基础 Python + 离线依赖包新装：194.2s，doctor ok，schema v2_0052，163 表。
- 候选 `199072c6`：stage（2.0s）→ prepare（5.1s，868 files/9,245,052 bytes）→ environment（`isolated-wheelhouse`，85.4s）→ install（2.6s，install.verified=true；含 smoke/backup/rollback-check 还原、migrate no-op、switch，previous 指向 0e33995b）。
- `installation rollback`：1.4s，from `1990…` to `0e33…`，written=true；启动器 `omni.cmd doctor` 在回滚后 integrity ok、163 表。
- 卸载：先卸回滚后 active 的 `0e33995b`（attempt1 `cleanup_pending` 30.5s，等待后 attempt2 39.6s 成功）；回滚后候选不 active，按产品语义先 `switch` 到 `1990…`（1.5s）再卸载（attempt1 `cleanup_pending` 27.3s，attempt2 44.7s 成功）；最终 `cleanup_pending=false`、`candidates=[]`、`omni.cmd` 移除，数据库/备份保留。
- 判定：功能链通过；卸载的 Windows 文件访问窗口需要重试，属已记录缺陷，不隐藏首次失败。

### 4.3 Windows 原生服务（C2.7）

- 隔离 home `71ab00ac…`，候选 venv 非 editable；capture（605 files/112 distributions）→ plan `44dc477b…` → materialize → verify exit 0 → operation-plan `3117ac19…`（7 步，`starts_service=false`）。
- `apply-operation --approve` 返回 `state=unknown`、`error_code=service_outcome_unresolved`、`registration.installed=false`，任务不存在。
- 根因（D10）：实际注册走 `adapters/workers/windows_services.ps1` → Task Scheduler COM `RegisterTask`；模板由 `service_templates.py::_windows` 生成 `<?xml version='1.0' encoding='utf-8'?>`，COM 抛 `0x8004131A (1,40) 无法切换编码`（`schtasks /Create /XML` 同样报 `task XML is malformed`）。同一 XML 去掉声明或改为 `encoding='UTF-16'` 后，临时任务注册/删除均成功。
- 第二缺陷（D11）：在安装副本上临时去掉声明后重走全链，任务成功创建（Ready、enabled=true），但 Task Scheduler 导出省略默认值元素（`RunLevel=LeastPrivilege`、`LogonTrigger/Enabled=true`、`Settings/Enabled=true`、`StartWhenAvailable=false`、`RunOnlyIfNetworkAvailable=false`、`Hidden=false`、`WakeToRun=false`），helper `Same-Definition` 要求这些元素存在，返回 `definition_matches=false`，start/status/uninstall 前置仍失败。临时任务已删除、模板已恢复。
- 判定：**产品缺陷 D10+D11**，C2.7 失败；修复前不能声称 Windows 原生服务可用。

### 4.4 QQ/NapCat（C3.2）

- NapCat 自 2026-11-18 13:43 运行，WebUI 6099 可登录；`/api/QQLogin/GetQQLoginInfo` 返回空 uin/nick，`/api/OB11Config/GetConfig` 返回 `Not Login`；配置中的 OneBot WS `127.0.0.1:3001` 未监听。
- 判定：真实账号未登录是硬阻塞；即使登录，仍需对端账号、NapCat 重启授权与 45–60 分钟窗口。

### 4.5 安卓真机（C3.5）

- ADB 1.0.41 可用，server 5037 在监听；`adb devices -l`、`adb mdns services` 为空，无 USB ADB PnP 设备；`Medium_Phone.avd` 存在但无 emulator 可执行文件。
- 判定：真机与模拟器均不可用；输入/Unicode/滚动/恢复一项都未执行。

### 4.6 72h 长跑工具试跑（C3.6 前置）

- 隔离实例 `b05b18ca…`（候选 `199072c6`，schema v2_0052）在 8798 启动，HTTP version 200 且 ready=true，boot `034c441c…`。
- `v2_soak.py run --hours 0.05 --interval 10`：19 次采样、0 失败、实测 180.11s；阻塞仅 `duration_below_72h`，`observational_soak_passed=false`。
- 空载最大值：RSS 135,671,808 B（约 129.4 MiB）、线程 17、句柄 351、后代 0、cleanup 0、失败/unknown 0。
- 建议正式 limits（待所有者批准）：RSS 512 MiB、后代 1 GiB、线程 64、句柄 2048、子进程 8、cleanup 0、new_failed_runs 0、new_unknown_model_calls 0；按实测留约 4 倍 RSS、3.7 倍线程余量。
- 判定：采样器/本次 boot HMAC/指标读取/证据链已验证；**不是 72h**，正式长跑仍需批准 limits 与连续窗口。

## 5. 缺少的机器、账号与时间窗口

| 资源 | 阻塞项 | 具体需求 | 建议窗口 |
| --- | --- | --- | --- |
| 干净 Windows 机器/VM | C3.1-W3、C3.7 | 干净 OS、可管理员、无开发工具；或所有者**书面接受**同机隔离为降级并记录 | 2026-11-24 ~ 11-25，3–4 小时 |
| 机器人 QQ 登录 + 对端账号 | C3.2 | 所有者扫码；一个可发消息的好友/测试群；允许重启 NapCat 开 WS | 2026-11-24 20:00–21:00（60 分钟） |
| 安卓真机 | C3.5 | USB 调试 + RSA 授权；允许切换输入法/锁定测试应用 | 2026-11-25 19:00–20:30（90 分钟） |
| B 站测试账号 | C3.3 | 读取、审批后单次发送、风控暂停核验 | 2026-11-26 20:00–21:00 |
| 模型质量评审 | C3.4 | API 预算、固定人格与评审人 | 2026-11-26 14:00–17:00 |
| 72h limits + 连续窗口 | C3.6 | 8 字段 limits 文件（所有者批准；3 分钟试跑实测 RSS≈129MiB/17 线程/351 句柄，已给建议值）；机器 72h 不休眠、不换候选 | 冻结后 2026-11-26 12:00 → 11-29 12:00 |
| Windows 代码签名证书 / 源码交付决定 | C4.2 | 证书与时间戳；或所有者决定仅源码交付 | 决定 2026-11-25 前；签名链 1–3 工作日 |
| Apple 凭据 + Mac 主机 | C4.2 公证 | Apple ID/Team ID/公证凭据与 macOS 机器（A/Mac 会话） | 2026-11-27 ~ 11-30 |
| Docker engine | C2.8 | 启动 Docker Desktop 或等价 Linux 引擎 | 2026-11-27，1 小时 |
| 最终交接窗口 | C4.3/C4.4 | 发布回滚演练 + 文档/索引/台账同步 | 2026-11-30，2–4 小时 |

## 6. 冻结后的顺序（不可跳步）

1. **修缺陷 D10+D11**：D10 改 Windows 服务 XML 声明（去声明或 UTF-16，且 helper 解码一致）；D11 让 `Same-Definition` 对 Task Scheduler 省略的默认值做等价比较；新增真实注册后导出 XML 的回归；重建 wheel 并重跑 service 注册→启动→就绪→排空→卸载。
2. **处理卸载文件访问窗口**：定位候选 venv 释放时序；若无法短期修复，明确记录为已知限制并保留重试路径，不得删除首次失败证据。
3. **冻结算法与候选**：确认 `v2 == origin/v2 == 冻结 SHA`，工作树洁净；此后不再合并。
4. **冻结全量**：`uv run --no-sync pytest -q`（≤4 worker）跑一次并保存 JUnit；当前候选尚无全量，不能沿用 `b395cd97`。
5. **72 小时长跑**：所有者批准 limits；用冻结候选启动独立实例；`scripts/v2_soak.py run --home <实例> --url http://127.0.0.1:<端口> --output <新目录> --limits <批准文件>`；报告必须 `observational_soak_passed`。采样器已于 3 分钟试跑验证（19 次/0 失败），正式运行只需替换为批准 limits 与 72h 窗口。
6. **C4.2 决策（与 A）**：签名/公证或明确降级为源码交付；两者都要有所有者书面决定与交付物校验。
7. **C4.3 发布回滚**：`release prepare/select` 与 `exchange apply` 真实演练；保留旧 home、快照与回执。
8. **C4.4 交接**：HANDOFF/DEVELOPMENT_STATUS/台账/`docs/index` 同步；生成交接提示词；更新本矩阵与发布门禁。

## 7. 完成度

- 本候选已执行：Windows 安装检查（部分通过）、安装链（通过）、Windows 服务（失败）、QQ/Android 探测（阻塞）、72h 采样器试跑（工具通过，时长不足）。
- 复用：C/D/E/G 专项与 `b395cd97` 全量绿；候选新增 Linux 身份按批次 1408 通过，未独立重跑。
- 未执行：干净机器、QQ 真实收发、安卓真机、B 站、模型质量、正式 72h、签名/公证、完整发布回滚。
- `whole_v2_complete=false`，`production_release_ready=false`；不得对外声称 V2 完成或可发布。
