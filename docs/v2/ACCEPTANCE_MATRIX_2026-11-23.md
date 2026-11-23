# V2 真实验收矩阵（2026-11-23，主线 5c8550ca）

> 结论先行：主线已到 `5c8550ca`（含 R1/R2 修复 `300a3229`）。H 用 5c8550ca 重建官方 wheel 实测：`status` 已能返回动作 PID 与 `ready=true`，但 `start` 控制仍返回 unknown，`reconcile-control` 无法收口，停止/卸载被未决意图阻塞（**R3**：控制 goal 使用 registration 回执，没有动作 PID）。`whole_v2_complete=false`，`production_release_ready=false`。
>
> 纪律：失败不改写为通过；首次失败与续跑成功分别保留；证据不含凭据、消息正文或像素；不复用旧候选的全量结果冒充当前候选。

## 1. 候选身份

| 项 | 值 |
| --- | --- |
| 当前主线 SHA | `5c8550ca992dd10d2792d90e9f19404feec47105`（`origin/v2`） |
| 上一候选 | `d7971bc473e41330212c319db1c9da30d75284e5`（D11e，R1/R2 未修） |
| 当前 wheel | `b6b0a12c4917867d73a7366e70c0c703536f1c1825aec58c8b6fb41daa0fc40e`，2,824,830 字节 |
| 上一 wheel | `3fbba07b9cbc5377d166555fc7475b58083aab308a57fafc65043ddb9dfa99b1`（d7971bc4） |
| 已并入 | C `8512a8bc`、D `c5170795`、E `94d483eb`、G `32d35898`、服务 `6540c724`、D11e `46083137`、R1/R2 `300a3229` |
| 依赖包 | `ddc242906492a815e1893505f0453f922f2c218dda441ad0088effb9f5e1225f`，110 wheels |
| schema | `v2_0052`（163 表） |
| 构建来源 | 干净 worktree `C:/v2-acceptance/c3-08dd1573/source-d7971bc4` |

## 2. 验收矩阵

| 项 | 环境/前置 | 候选 SHA | 结果 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- |
| C2.7 Windows 原生服务 | 不可变部署 + 真实注册 | `5c8550ca` | **失败（R3）**：R1/R2 已修，`status` 返回 manager running、pid 111620 非空、ready=true；但 `start` unknown/service_control_outcome_unverified，`reconcile` unknown/service_control_unresolved，stop/uninstall 被 `service_operation_unresolved` 阻塞 | `docs/evidence/v2-windows-service-5c8550ca-2026-11-23.json`；`C:/v2-acceptance/c3-08dd1573/svc10/probes/r3-control-goal.txt` | 控制 goal 用 registration 回执（engine_pid，无 action pid），无法满足 R2 的严格 ready；需与 status 共用动作 PID 证据 |
| C3.1-W1 官方 wheel 安装检查 | 同机全新目录 | `6d81a826`（历史） | **部分通过**（3d3b28a0）：六阶段 verified；首次卸载确定性失败，延迟 40s 续跑成功 | `docs/evidence/v2-windows-install-check-6d81a826-2026-11-23.json` | d7971bc4 候选**尚未重跑** |
| C3.1-W2 安装→升级→回滚→卸载链 | 同机隔离 | `6d81a826`（历史） | **通过（带重试）**（3d3b28a0）：上一版 `199072c6` → 当前 → rollback → 双候选卸载 | `docs/evidence/v2-windows-install-chain-6d81a826-2026-11-23.json` | d7971bc4 候选**尚未重跑** |
| C3.1-W3 真正干净机器 | 干净 OS/VM | — | **阻塞** | `docs/evidence/v2-clean-machine-kit-2026-11-23.json` | 验收包按 6d81a826 构建，修复后需重建 |
| C3.2 QQ/NapCat | 真实账号 + 窗口 | — | **阻塞**：未登录、WS 3001 未监听 | `docs/evidence/v2-qq-napcat-probe-2026-11-23.json` | 无对端账号 |
| C3.5 安卓真机 | 已授权设备 | — | **阻塞**：adb 无设备、无 emulator | `docs/evidence/v2-android-device-probe-2026-11-23.json` | — |
| C3.3 B 站账号 | 真实账号 | — | **阻塞** | E 验收工具已入主线 | 无窗口 |
| C3.4 模型/视觉 | 模型 + 预算 | — | **阻塞**：LM Studio models 为空/lms 超时 | `docs/evidence/v2-clean-machine-kit-2026-11-23.json` | — |
| C3.6 72h | 冻结 + limits | — | **未开始（工具试跑已验证）**：8412e773 上 19 次/0 失败 | `docs/evidence/v2-soak-dry-run-2026-11-23.json` | 当前候选未重跑 |
| C3.7 三平台原生 | 实机 | — | **未在本候选重跑** | 旧 Windows 证据/批次记录 | — |
| C2.8 容器 | Docker | — | **阻塞**：引擎未启动 | — | — |
| C4.2 签名/公证 | 证书/Apple | — | **阻塞（待决策）** | — | 可源码交付 |
| C4.3 发布回滚 | 冻结候选 | — | **部分**：installation rollback 已验 | 安装链证据 | release/exchange 未演练 |

## 3. 复用与全量缺口

| 批次 | 合并 | 结果 |
| --- | --- | --- |
| C | `8512a8bc` | 真实素材/检测器/长片边界 |
| D | `c5170795` | 视觉生产 7 专项 + 12 生产专项；K15 69 专项 |
| E | `94d483eb` | 缓存专项 38 通过；验收工具 self-test |
| G | `32d35898` | Linux 全组 1412 通过；X11 frame 裁剪 sample_passed |
| 服务 | `6540c724` | 服务专项 53 passed |
| D11e | `46083137` | query 31 passed（假集合），但真实 COM 集合回归见 C2.7 |
| 最近全量 | `b395cd97` | 8618 / 8609 passed / 0 failed / 9 skipped（2559s），**不覆盖** 6d81a826 之后 |

> 当前 d7971bc4 **没有**完整全量；冻结后必须重跑。

## 4. 执行摘要

### 4.1 D11e 实机回归（H 本轮新发现）

- d7971bc4 wheel：install `succeeded`/`definition_matches=true`；任务 Running；HTTP `/api/v1/version` 200/ready=true（boot `e8fd469c…`、pid 134544）。
- `start` 返回 `state=unknown, error_code=service_control_outcome_unverified`；`status` manager `state=unknown`/`pid=null`/`ready=false`。
- helper 原始 inspect 返回 `service_native_unverified`：`GetInstances(0)` 返回集合 Count=1，但 `.Item(0)` 抛 `System.ArgumentException: Value does not fall within the expected range`，外层 catch 吞掉全部状态。
- COM `foreach` 可取到 `EnginePID=91764`、state=4；把 helper 改为 `foreach ($instance in $instances) { $result.engine_pid=[int]$instance.EnginePID; break }` 后，inspect 返回 `state=running / engine_pid=91764 / definition_matches=true`；psutil 在 91764 下找到动作进程 134544，命令行匹配。
- 判定：**D11e-R1 实机回归**，A 必须改 helper 的集合取首元素方式；修后重建 wheel 重跑 start/status。
- 第二轮（svc9）：把该行改为 foreach 后，start/stop/uninstall succeeded、status manager state=running/definition_matches=true；但 `pid=null/service_action_unverified`、overall `ready=true`，与契约矛盾（**D11e-R2**）。根因：计划 Exec/Command 是 venv 的 `Scripts\python.exe`，实际动作进程是基础 Python（`C:\Users\...\Python312\python.exe` + `-I -u -m omni.composition.service_runner`），`_action_pid` 严格比较 `cmdline[0]` 失败。A 需同时修正动作 PID 解析，并在 pid=null/service_action_unverified 时强制 ready=false。
- 第三轮（5c8550ca，R1/R2 已修）：status 已返回 pid=111620/ready=true；但 `start` 仍 unknown，`reconcile` 无法收口，stop/uninstall 被阻塞。**R3**：`application/service_control.py` 的 `goal()` 用 ServiceRegistration.inspect（只有 engine_pid，无动作 pid）调用 `start_postcondition`，永远无法满足 R2 的 `pid == proof.pid`。控制面必须改用与 status 相同的动作 PID 证据。\n- 第三轮（5c8550ca，R1/R2 已修）：status 已返回 pid=111620/ready=true；但 `start` 仍 unknown，`reconcile` 无法收口，stop/uninstall 被阻塞。**R3**：`application/service_control.py` 的 `goal()` 用 ServiceRegistration.inspect（只有 engine_pid，无动作 pid）调用 `start_postcondition`，永远无法满足 R2 的 `pid == proof.pid`。控制面必须改用与 status 相同的动作 PID 证据。

### 4.2 6d81a826 历史证据（不代表当前主线）

- 6d81a826 wheel `3d3b28a0`：install/start/stop/uninstall 成功；status manager unknown/ready=false（原 D11e）。
- 安装链：上一版 `199072c6` → 当前 `3d3b28a0` → rollback → 双卸载；首次 `cleanup_pending` 后重试成功。
- 官方安装检查：六阶段 verified，首次卸载确定性失败（stderr SHA `98b1d782…`），延迟 40s 续跑成功。

### 4.3 72h 试跑

- 8412e773 上 3 分钟 19 次/0 失败；RSS 135,671,808B、线程 17、句柄 351、后代 0、cleanup 0；仅 `duration_below_72h`。正式 72h 需最终候选与批准 limits。

### 4.4 H 准备项

- 验收包 `C:/v2-acceptance/release-6d81a826`（仍基于旧候选；R3 修复后重建）。
- 无干净 Windows VM/Sandbox；无 QQ/B 站账号；无安卓真机；无可用视觉模型。
- 本轮清理：svc7/svc8 临时任务已 End+Delete，8789 端口释放；8790 的既有 demo serve 保持运行。

## 5. 缺少的机器、账号与时间窗口

| 资源 | 阻塞项 | 建议窗口 |
| --- | --- | --- |
| 干净 Windows VM | C3.1-W3/C3.7 | 11-24~11-25，3–4h |
| QQ 登录 + 对端账号 + NapCat 重启授权 | C3.2 | 11-24 20:00–21:00 |
| 安卓真机 + USB 授权 | C3.5 | 11-25 19:00–20:30 |
| 兼容视觉模型 + 预算 | C3.4 | 11-25 前 |
| B 站测试账号 | C3.3 | 11-26 20:00–21:00 |
| 72h limits + 连续窗口 | C3.6 | 冻结后 11-26 12:00 → 11-29 12:00 |
| 签名/公证或源码交付决定 | C4.2 | 11-25 前 |
| Docker 引擎 | C2.8 | 11-27，1h |
| 发布回滚/交接 | C4.3/C4.4 | 11-30，2–4h |

## 6. 顺序

1. **A 修 D11e-R**：helper 用 foreach 取首实例的 `EnginePID`；重建 wheel；H 复跑 install→start→status（ready=true、pid 非空）→drain→uninstall。
2. 冻结最终 SHA，工作树洁净；跑一次完整全量并保存 JUnit。
3. 用冻结 SHA 重建官方 wheel；干净机执行安装链；Windows 服务完整生命周期。
4. 72h（批准 limits）→ C4.2 决策 → C4.3 发布回滚 → C4.4 交接。

## 7. 完成度

- 当前 d7971bc4：安装链/检查/72h 均未在新候选重跑；服务生命周期因 D11e-R 失败。
- `whole_v2_complete=false`，`production_release_ready=false`。


