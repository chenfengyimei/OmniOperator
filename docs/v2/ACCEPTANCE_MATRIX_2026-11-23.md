# V2 真实验收矩阵（2026-11-23，主线 6d81a826）

> 结论先行：主线 `6d81a826` 已包含 C/D/E/G 四批与 Windows 服务修复 `6540c724`。官方 wheel 的安装检查、安装→升级→回滚→卸载链、Windows 任务真实注册/启动/正常排空/卸载均已在隔离目录执行；**只读 `service status` 仍缺运行状态证明（D11e）**。干净机器、QQ/NapCat、安卓真机、兼容视觉模型、B 站账号、正式 72 小时、签名/公证与完整发布回滚仍未完成。`whole_v2_complete=false`，`production_release_ready=false`。
>
> 纪律：失败不改写为通过；首次失败与续跑成功分别保留；证据不含凭据、消息正文或像素；不复用旧候选的全量结果冒充当前候选。

## 1. 候选身份

| 项 | 值 |
| --- | --- |
| 主线 SHA | `6d81a826e0f5a6dae149604ebc0b49d95b93b040`（`v2 == origin/v2`；Gitee 主线同一 SHA） |
| 已并入批次 | C `8512a8bc`、D `c5170795`、E `94d483eb`、G `32d35898`、Windows 服务修复 `6540c724` |
| 候选 wheel | `3d3b28a0b91cb49904a476c8e0e5f9a9d130386f43d66abab0777605ffb6f9d2`，2,823,338 字节 |
| 上一版 wheel | `199072c6fc455a47a7e6d67ad696437050804a7269d7248af900883118e72a6c`，源码 `8412e773`（同 schema v2_0052） |
| 依赖包 manifest | `ddc242906492a815e1893505f0453f922f2c218dda441ad0088effb9f5e1225f`，110 wheels |
| uv.lock | `3900d190babf4b0cfcb42d1e6b82d5ba2ced7a34fe69d28a8f09b5e008f58321`（自 1e62fe08 起未变） |
| 构建来源 | 干净 worktree `C:/v2-acceptance/c3-08dd1573/source-6d81a826`（`uv build --offline`） |
| schema | `v2_0052`（新装 163 表，doctor integrity=ok） |
| 工作树 | 仅 `temp/_inspect.py`、`temp/worktrees/` 未跟踪 |

## 2. 验收矩阵

状态口径：`通过` = 合同 DoD 满足且有证据；`部分通过` = 实际执行但存在未关闭缺陷；`失败` = 产品行为不符合合同；`阻塞` = 缺外部资源；`未开始` = 有前置未满足。

| 项 | 环境/前置 | 候选 SHA | 结果 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- |
| C3.1-W1 干净目录离线安装（官方 wheel） | 同机全新目录 + 基础解释器 + 离线依赖 | `6d81a826` | **部分通过**：base/install/manager/candidate/launcher/current verified；首次卸载 failed（rc=1，150B stderr，SHA `98b1d782…`）；延迟 40s 外部维护器续跑成功 | `docs/evidence/v2-windows-install-check-6d81a826-2026-11-23.json` | checker `verified=false`；同一 stderr SHA 已在四轮出现，确定性 |
| C3.1-W2 install→update→rollback→uninstall 链 | 同机隔离 home，独立 manager，离线 | `6d81a826` | **通过（带卸载重试）**：上一版 `199072c6` 新装 → 当前 `3d3b28a0` stage/prepare/environment/install → live rollback → 双候选卸载；最终 `candidates=[]`、数据/备份保留 | `docs/evidence/v2-windows-install-chain-6d81a826-2026-11-23.json` | 两处卸载首次 `cleanup_pending`，30s 重试成功；rollback 只换指针不恢复数据库 |
| C3.1-W3 真正干净机器 | 干净 Windows OS/VM、管理员、无开发工具 | — | **阻塞** | `docs/evidence/v2-clean-machine-kit-2026-11-23.json` | 本机无管理员/VM/Sandbox；验收包已备好，待所有者提供干净 VM |
| C2.7 Windows 原生服务（Task Scheduler） | 不可变部署 + 真实注册 | `6d81a826` | **部分通过（D11e 未收口）**：注册 `definition_matches=true`；启动 HTTP ready=true（boot `f40f1572…`、pid 132948）；正常排空 `closed=true`；卸载任务删除；只读 `status` 仍 `ready=false`（manager `state=unknown`、pid null） | `docs/evidence/v2-windows-service-6d81a826-2026-11-23.json` | `6540c724` 已修 D10/D11/D11c/D11d；D11e 需 A 补 running 证明或明确验收口径 |
| C3.2 QQ/NapCat 扫码/白名单/收发/重连 | 真实 QQ + 对端账号 + 窗口 | — | **阻塞**：NapCat 在跑、WebUI 6099 可访问，但 QQ 未登录（`GetQQLoginInfo` 空、`OB11Config` Not Login），OneBot WS 3001 未监听 | `docs/evidence/v2-qq-napcat-probe-2026-11-23.json` | 无对端账号、无重启授权 |
| C3.5 安卓真机输入/Unicode/滚动/恢复 | 已授权安卓设备 | — | **阻塞**：`adb devices -l`、`adb mdns services` 为空；无 USB ADB 设备；AVD 存在但无 emulator | `docs/evidence/v2-android-device-probe-2026-11-23.json` | 真机与模拟器均不可用 |
| C3.3 B 站账号读取/发表/风控 | 真实账号 | — | **阻塞** | E 批次验收工具 `94d483eb` 已入主线 | 无账号与窗口 |
| C3.4 模型质量/兼容视觉模型 | 真实模型 + 预算 + 评审 | — | **阻塞** | `docs/evidence/v2-clean-machine-kit-2026-11-23.json` | LM Studio models 为空、lms 连接超时；无可用视觉模型 |
| C3.6 72 小时长跑 | 最终冻结 + 批准 limits + 连续 72h | — | **未开始（工具试跑已验证）**：3 分钟 19 次采样/0 失败，仅 `duration_below_72h` | `docs/evidence/v2-soak-dry-run-2026-11-23.json` | 试跑在 `8412e773`；正式 72h 需最终候选与批准 limits |
| C3.7 三平台原生 | Windows/Linux/macOS 实机 | 旧候选 | **未在本候选重跑**：Windows 桌面/终端沿用 `1e62fe08`；Linux G 见复用；macOS 由另一会话 | `docs/v2/platforms/windows/WINDOWS_V2_ACCEPTANCE_2026-11-18.md` | 旧 SHA 证据不能写成当前候选通过 |
| C2.8 容器真实 build/run | Docker engine | — | **阻塞** | `docs/evidence/v2-clean-machine-kit-2026-11-23.json` | Docker 引擎未启动 |
| C4.2 签名/公证 | 证书 / Apple 凭据 + Mac | — | **阻塞（待决策）** | — | 无证书、无 Apple 凭据、无本机 macOS；可降级源码交付 |
| C4.3 发布回滚演练（release/exchange） | 冻结候选 + 两套不可变安装 | `6d81a826` | **部分**：installation live rollback 已验；release/exchange 未演练 | `docs/evidence/v2-windows-install-chain-6d81a826-2026-11-23.json` | 依赖 D11e 与冻结候选 |

## 3. 复用的批次结果与全量缺口

| 批次 | 合并 SHA | 专项结果 | 覆盖 |
| --- | --- | --- | --- |
| C 视频分镜 | `8512a8bc` | 真实素材验收、检测器评测、长片边界 | 场景/letterbox/采样 |
| D Android/视觉 | `c5170795` | 视觉生产路径 7 专项 + 12 项生产专项；K15 69 专项 | 截图/输入/撤回链、1–4 图单次调用 |
| E B 站读缓存 | `94d483eb` | 缓存专项 38 通过；验收工具 self-test 通过 | video/whois TTL/换代/暂停/永不缓存 |
| G Linux/容器 | `32d35898` | Linux 全组 1412 通过；X11 frame 裁剪截屏 sample_passed | frame/parent/translate 与 4 项裁剪 |
| Windows 服务修复 | `6540c724` | 服务专项 53 passed（query/registration/deployment） | D10/D11/D11c/D11d 回归 |
| 最近完整全量 | `b395cd97` | 8618 tests / 8609 passed / 0 failed / 9 skipped（2559s） | **不覆盖** 6d81a826 的后续生产代码 |

> 6d81a826 相对 b395cd97 已包含 C/D/E/G 与 Windows 服务修复；**尚未对 6d81a826 跑完整全量**。冻结候选后必须跑一次并保存 JUnit，不能沿用旧结果。

## 4. 执行摘要

### 4.1 官方 wheel 安装检查（C3.1-W1）

- wheel `3d3b28a0…`；base/install/manager/candidate/launcher/current 六阶段 verified。
- 首次卸载 **确定性失败**：150B stderr，SHA `98b1d782…`，与前四轮相同；延迟 40s 由 manager venv 续跑 36.7s 成功，`data_kept=true`。
- 卸载前后 DB SHA `82a10d2e…`、备份 manifest `e3106aae…` 完全一致；checker 仍为 `verified=false`。

### 4.2 官方 wheel 安装链（C3.1-W2）

- 上一版 `199072c6`（`8412e773`）新装 230s，doctor ok，163 表。
- 当前 `3d3b28a0`：stage 1.7s → prepare 5.5s（869 files/9,266,022 bytes）→ environment 95.7s → install 3.3s（backup、rollback-check、migrate no-op、switch）；rollback 1.5s 回 `199072c6`；回滚后 `omni.cmd doctor` ok。
- 卸载：上一版 attempt1 `cleanup_pending` 28.5s → attempt2 39.9s 成功；switch 后当前版 attempt1 29.0s → attempt2 41.7s 成功；最终 `cleanup_pending=false`、`candidates=[]`、`omni.cmd` 移除。

### 4.3 Windows 原生服务（C2.7）

- `6540c724` 已修：D10 去 XML 声明、D11 helper 默认值/账号归一、D11c Principal RunLevel、D11d schtasks UTF-16 声明解析；服务专项 53 passed。
- 官方 6d81a826 wheel：capture/plan/materialize/verify 通过；install `succeeded`/`definition_matches=true`；start `succeeded`（boot `f40f1572…`、pid 132948、HTTP 200/ready=true）；stop `closed=true`/锁释放；uninstall 任务删除。
- **D11e 仍在**：`service_query._query` 对 Windows 只读 XML，返回 manager `state=unknown`、`pid=null`，overall `ready=false`。当前实现见 `agent/src/omni/adapters/platform/services/service_query.py:398`。
- 判定：生命周期通过；只读 status 未收口，C2.7 记为**部分通过**，不能写成完整通过。

### 4.4 QQ/NapCat（C3.2）

- NapCat 运行、WebUI 6099 可访问；QQ 未登录，OneBot WS 3001 未监听；无对端账号与窗口。

### 4.5 安卓真机（C3.5）

- ADB 1.0.41、server 5037 在监听；`adb devices -l`/`adb mdns services` 为空；无 emulator 可执行文件。

### 4.6 72h 试跑（C3.6 前置）

- `8412e773` 上 3 分钟、19 次采样、0 失败；RSS 135,671,808B、线程 17、句柄 351、后代 0、cleanup 0；仅 `duration_below_72h` 阻塞。
- 建议 limits：RSS 512MiB、后代 1GiB、线程 64、句柄 2048、子进程 8、cleanup 0、failed 0、unknown 0（待批准）。

### 4.7 H 准备的验收包（干净机前置）

- `C:/v2-acceptance/release-6d81a826`：官方 wheel、上一版 wheel、110 wheels 依赖包、`install_v2.py`、`accept_chain.ps1`、`SHA256SUMS.txt`、README。
- 脚本解析通过；未在干净 OS 执行。
- 本机无干净 Windows VM/Sandbox；无 QQ/B 站账号、无安卓真机、无可用视觉模型（LM Studio models 为空且 lms 超时）。

## 5. 缺少的机器、账号与时间窗口

| 资源 | 阻塞项 | 具体需求 | 建议窗口 |
| --- | --- | --- | --- |
| 干净 Windows 机器/VM | C3.1-W3、C3.7 | 干净 OS、管理员、无开发工具；验收包已就绪 | 2026-11-24 ~ 11-25，3–4 小时 |
| 机器人 QQ + 对端账号 | C3.2 | 扫码登录、好友/测试群、允许重启 NapCat 开 WS | 2026-11-24 20:00–21:00 |
| 安卓真机 | C3.5 | USB 调试 + RSA 授权 | 2026-11-25 19:00–20:30 |
| 兼容视觉模型 | C3.4 | 本地或已授权 API 视觉模型 + 预算 | 2026-11-25 前配置 |
| B 站测试账号 | C3.3 | 读取、审批发表、风控核对 | 2026-11-26 20:00–21:00 |
| 72h limits + 连续窗口 | C3.6 | 8 字段 limits 所有者批准；机器 72h 不休眠 | 冻结后 2026-11-26 12:00 → 11-29 12:00 |
| 签名/公证或源码交付决定 | C4.2 | 证书/Apple 凭据，或书面降级源码交付 | 2026-11-25 前决定 |
| Docker engine | C2.8 | 启动 Docker Desktop 或等价引擎 | 2026-11-27，1 小时 |
| 发布回滚/交接窗口 | C4.3/C4.4 | release/exchange 演练 + 文档/索引/台账同步 | 2026-11-30，2–4 小时 |

## 6. 冻结后的顺序

1. **A 处理 D11e**：补可信 running 状态（COM/受管进程与监听证明，不依赖本地化文字），或明确 Windows 以 `apply-control` 的 `observed_reviewed_boot` 作为就绪回执并同步验收标准。
2. **冻结候选**：确认 `v2 == origin/v2 == 冻结 SHA`，工作树洁净；此后不再合并。
3. **冻结全量**：`uv run --no-sync pytest -q`（≤4 worker）一次并保存 JUnit；当前 6d81a826 尚无全量。
4. **官方产物验收**：用冻结 SHA 重建 wheel，在干净机执行验收包（安装→升级→回滚→卸载），并重跑服务注册→状态→启动→就绪→排空→卸载。
5. **72h**：所有者批准 limits 后启动；报告必须 `observational_soak_passed`。
6. **C4.2 决策**：签名/公证或源码交付；与 A 确认交付物校验。
7. **C4.3 发布回滚**：release prepare/select 与 exchange apply 真实演练。
8. **C4.4 交接**：HANDOFF/DEVELOPMENT_STATUS/台账/docs/index 同步。

## 7. 完成度

- 当前候选已执行：官方 wheel 安装检查（部分通过）、官方 wheel 安装链（通过）、Windows 服务生命周期（通过，status D11e 未收口）、72h 工具试跑（旧候选）。
- 复用：C/D/E/G 与 6540c724 专项；最近全量仍只覆盖 `b395cd97`。
- 未执行：干净机器、QQ 真实收发、安卓真机、视觉模型、B 站、正式 72h、签名/公证、完整发布回滚。
- `whole_v2_complete=false`，`production_release_ready=false`。
