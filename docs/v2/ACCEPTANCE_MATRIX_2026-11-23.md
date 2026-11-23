# V2 真实验收矩阵（2026-11-23，主线 b29409d0）

> 结论先行：主线已到 `b29409d0`（含 R3 修复 `90de0bbc`）。H 用 b29409d0 重建官方 wheel `8d7d1956…` 实测通过 **注册 → 启动 → 只读 status（pid 142624、ready=true）→ 正常排空 → 卸载**，官方安装链/检查也通过（检查带卸载重试）。但 **最终全量 8655 项中 22 失败、8623 通过、10 跳过**；20/22 单跑复现，主因是默认上下文 32768 触发 `token_budget_exceeded`（R4）。候选不能冻结，72h 不能启动。`whole_v2_complete=false`，`production_release_ready=false`。
>
> 纪律：失败不改写为通过；首次失败与续跑成功分别保留；证据不含凭据、消息正文或像素；不复用旧候选的全量结果冒充当前候选。

> **2026-11-24 更新（A，夜间总集成）**：以下矩阵记录的是 `b29409d0` 的历史状态。当前被测候选为 **`dae24cf7`**（产品代码与 `05b44447` 相同；仅安装检查器重试 + 回归），wheel `7afc5b7d…`。最终全量 **8648 通过 / 0 失败 / 10 跳过（2872.39s）**，Windows 服务链与官方安装链全 0，安装检查 `verified=true`，**72h 长跑已启动**。详见第 5 节与 `docs/evidence/v2-release-candidate-05b44447-2026-11-24.json`。

## 1. 候选身份

| 项 | 值 |
| --- | --- |
| 当前主线 SHA | `b29409d02f5a1a7dd84aa23956938c7652d1aad3`（`origin/v2`） |
| 上一候选 | `5c8550ca992dd10d2792d90e9f19404feec47105`（R1/R2，R3 未修） |
| 当前 wheel | `8d7d195672ed8e0c92de58737b76024ce21f7d765b570f89b6f122ede17db958`，2,825,051 字节 |
| 上一 wheel | `b6b0a12c4917867d73a7366e70c0c703536f1c1825aec58c8b6fb41daa0fc40e`（5c8550ca） |
| 已并入 | C `8512a8bc`、D `c5170795`、E `94d483eb`、G `32d35898`、服务 `6540c724`、D11e `46083137`、R1/R2 `300a3229`、R3 `90de0bbc` |
| 依赖包 | `ddc242906492a815e1893505f0453f922f2c218dda441ad0088effb9f5e1225f`，110 wheels |
| schema | `v2_0052`（163 表） |
| 构建来源 | 干净 worktree `C:/v2-acceptance/c3-08dd1573/source-d7971bc4` |

## 2. 验收矩阵

| 项 | 环境/前置 | 候选 SHA | 结果 | 证据 | 说明 |
| --- | --- | --- | --- | --- | --- |
| C2.7 Windows 原生服务 | 不可变部署 + 真实注册 | `b29409d0` | **通过**：注册 `definition_matches=true`；start succeeded（boot `8dbad3c2…`、pid 142624、ready=true）；只读 status manager running/pid 142624/ready=true；stop closed=true、锁释放；uninstall 任务删除 | `docs/evidence/v2-windows-service-b29409d0-2026-11-23.json`；`C:/v2-acceptance/c3-08dd1573/svc12/accept/transcript.json` | R1/R2/R3 修复在真实 Task Scheduler 生效 |
| C3.1-W1 官方 wheel 安装检查 | 同机全新目录 | `b29409d0` | **部分通过**（wheel `8d7d1956`）：六阶段 verified；首次卸载确定性失败（150B stderr SHA `98b1d782…`），延迟 40s 续跑 32.2s 成功、数据/备份一致；checker `verified=false` | `docs/evidence/v2-windows-install-check-b29409d0-2026-11-23.json` | 非干净 OS；同一 stderr SHA 历轮一致 |
| C3.1-W2 安装→升级→回滚→卸载链 | 同机隔离 | `b29409d0` | **通过（带重试）**（wheel `8d7d1956`）：上一版 `b6b0a12c` 新装 190.8s → 当前 stage/prepare/environment 95.3s/install 3.2s → rollback → doctor → 双候选卸载（attempt1 `cleanup_pending`，attempt2 32.5s/39.4s 成功）；最终 `candidates=[]`、数据/备份保留 | `docs/evidence/v2-windows-install-chain-b29409d0-2026-11-23.json` | 非干净 OS；rollback 只换指针 |
| C3.1-W3 真正干净机器 | 干净 OS/VM | — | **阻塞** | `docs/evidence/v2-clean-machine-kit-2026-11-23.json` | 验收包按 6d81a826 构建，修复后需重建 |
| C3.2 QQ/NapCat | 真实账号 + 窗口 | — | **阻塞**：未登录、WS 3001 未监听 | `docs/evidence/v2-qq-napcat-probe-2026-11-23.json` | 无对端账号 |
| C3.5 安卓真机 | 已授权设备 | — | **阻塞**：adb 无设备、无 emulator | `docs/evidence/v2-android-device-probe-2026-11-23.json` | — |
| C3.3 B 站账号 | 真实账号 | — | **阻塞** | E 验收工具已入主线 | 无窗口 |
| C3.4 模型/视觉 | 模型 + 预算 | — | **阻塞**：LM Studio models 为空/lms 超时 | `docs/evidence/v2-clean-machine-kit-2026-11-23.json` | — |
| C3.6 72h | 冻结 + limits | `5c8550ca` | **未开始（工具试跑已验证）**：19 次/0 失败，仅 `duration_below_72h`；RSS 136,544,256B、线程 14、句柄 346、后代 0、cleanup 0 | `docs/evidence/v2-soak-dry-run-5c8550ca-2026-11-23.json` | 正式 72h 需最终候选与批准 limits |
| C3.7 三平台原生 | 实机 | — | **未在本候选重跑** | 旧 Windows 证据/批次记录 | — |
| C2.8 容器 | Docker | — | **阻塞**：引擎未启动 | — | — |
| C4.2 签名/公证 | 证书/Apple | — | **阻塞（待决策）** | — | 可源码交付 |
| C4.3 发布回滚 | 冻结候选 | — | **部分**：installation rollback 已验 | 安装链证据 | release/exchange 未演练 |
| FULL-1 最终全量 | 冻结候选 | `b29409d0` | **失败（R4）**：8655 项、8623 通过、22 失败、10 跳过（2580.96s）；20/22 单跑复现；多数为默认 32768 `token_budget_exceeded`；media zoom 同因；shell 进程树回收与 UIA act 单跑通过（全量高负载/时序敏感） | `docs/evidence/v2-final-full-suite-b29409d0-2026-11-23.json`；`temp/build/final-b29409d0.xml` | 全量绿前不得冻结/72h |

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

> b29409d0 已跑最终全量：**8655 项 / 8623 通过 / 22 失败 / 10 跳过**；20/22 单跑复现，主因 `token_budget_exceeded`（R4）。全量绿前不得冻结或启动 72h。

## 4. 执行摘要

### 4.1 D11e 实机回归（H 本轮新发现）

- d7971bc4 wheel：install `succeeded`/`definition_matches=true`；任务 Running；HTTP `/api/v1/version` 200/ready=true（boot `e8fd469c…`、pid 134544）。
- `start` 返回 `state=unknown, error_code=service_control_outcome_unverified`；`status` manager `state=unknown`/`pid=null`/`ready=false`。
- helper 原始 inspect 返回 `service_native_unverified`：`GetInstances(0)` 返回集合 Count=1，但 `.Item(0)` 抛 `System.ArgumentException: Value does not fall within the expected range`，外层 catch 吞掉全部状态。
- COM `foreach` 可取到 `EnginePID=91764`、state=4；把 helper 改为 `foreach ($instance in $instances) { $result.engine_pid=[int]$instance.EnginePID; break }` 后，inspect 返回 `state=running / engine_pid=91764 / definition_matches=true`；psutil 在 91764 下找到动作进程 134544，命令行匹配。
- 判定：**D11e-R1 实机回归**，A 必须改 helper 的集合取首元素方式；修后重建 wheel 重跑 start/status。
- 第二轮（svc9）：把该行改为 foreach 后，start/stop/uninstall succeeded、status manager state=running/definition_matches=true；但 `pid=null/service_action_unverified`、overall `ready=true`，与契约矛盾（**D11e-R2**）。根因：计划 Exec/Command 是 venv 的 `Scripts\python.exe`，实际动作进程是基础 Python（`C:\Users\...\Python312\python.exe` + `-I -u -m omni.composition.service_runner`），`_action_pid` 严格比较 `cmdline[0]` 失败。A 需同时修正动作 PID 解析，并在 pid=null/service_action_unverified 时强制 ready=false。
- 第三轮（5c8550ca，R1/R2 已修）：status 已返回 pid=111620/ready=true；但 `start` 仍 unknown，`reconcile` 无法收口，stop/uninstall 被阻塞。**R3**：`application/service_control.py` 的 `goal()` 用 ServiceRegistration.inspect（只有 engine_pid，无动作 pid）调用 `start_postcondition`，永远无法满足 R2 的 `pid == proof.pid`。控制面必须改用与 status 相同的动作 PID 证据。\n- 第三轮（5c8550ca，R1/R2 已修）：status 已返回 pid=111620/ready=true；但 `start` 仍 unknown，`reconcile` 无法收口，stop/uninstall 被阻塞。**R3**：`application/service_control.py` 的 `goal()` 用 ServiceRegistration.inspect（只有 engine_pid，无动作 pid）调用 `start_postcondition`，永远无法满足 R2 的 `pid == proof.pid`。控制面必须改用与 status 相同的动作 PID 证据。

- 最终（b29409d0，R3 已修）：wheel `8d7d1956`。安装 succeeded/definition_matches=true；start succeeded（boot `8dbad3c2…`、pid 142624、HTTP ready=true）；status manager running/pid 142624/ready=true；stop closed=true、锁释放；uninstall 任务删除。**C2.7 生命周期与只读状态均通过。**

### 4.2 官方安装链与检查（b29409d0）

- b29409d0 wheel `8d7d1956`：上一版 `b6b0a12c` 新装 190.8s → 当前 stage 1.5s/prepare 10.2s/environment 95.3s/install 3.2s → rollback 1.5s → doctor ok/163 表 → 双候选卸载（attempt1 `cleanup_pending`，attempt2 32.5s/39.4s 成功）；最终 `candidates=[]`、数据/备份保留（DB `1891045d…`）。\n- 官方安装检查（b294）：六阶段 verified，首次卸载失败 150B stderr SHA `98b1d782…`，延迟 40s 续跑 32.2s 成功；DB `696b4e14…`/备份 `b983394e…` 前后一致；checker `verified=false`。\n- 5c8550ca wheel `b6b0a12c`：上一版 `3fbba07b` 新装 244s → 当前 stage 1.5s/prepare 5s/environment 106.3s/install 2.7s → rollback 1.5s → doctor ok/163 表 → 双候选卸载（attempt1 `cleanup_pending`，attempt2 36s/36.2s 成功）；最终 `candidates=[]`、数据/备份保留。
- 官方安装检查：六阶段 verified，首次卸载确定性失败（150B stderr SHA `98b1d782…`），延迟 40s 续跑 35.9s 成功，DB `4d47d616…`/备份 `50402c86…` 前后一致；checker `verified=false`。
- 6d81a826 历史（不代表当前主线）：wheel `3d3b28a0` 的安装链/检查曾通过（带卸载重试）。

### 4.3 72h 试跑

- 5c8550ca wheel `b6b0a12c` 上 3 分钟 19 次/0 失败；RSS 136,544,256B、线程 14、句柄 346、后代 0、cleanup 0；仅 `duration_below_72h`。正式 72h 需最终候选与批准 limits。

### 4.4 H 准备项

- 验收包已更新为 `C:/v2-acceptance/release-b29409d0`（最终 wheel `8d7d1956`）。
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

## 5. 2026-11-24 最终候选复核（A）

| 项 | 候选 | 结果 | 证据 |
| --- | --- | --- | --- |
| FULL-1 最终全量 | `dae24cf7` | **通过**：8648 通过 / 0 失败 / 10 跳过，2872.39s；前一代 `05b44447` 也为 8647/0/10 | `C:/v2-acceptance/final-dae24cf7/full.xml`（SHA256 `b21e1cb8…`）、`full.log` |
| R4 处置 | — | 22 → 0：20 项 token 预算由 `utf8-envelope-v2` 修复；shell 进程树 / UIA `act` 全量与隔离均过（并行抖动）；`run_compaction` 2 项按截断契约修正夹具 | `docs/evidence/v2-r4-token-envelope.json`、`docs/v2/contracts/V2_TOKEN_ENVELOPE.md` |
| C2.7 Windows 服务链 | `dae24cf7`（wheel `7afc5b7d…`） | **通过**：14 步 exit 0；start 29.6s（`observed.ready=true`）；status `state=running`、pid `141552` == runtime pid、`definition_matches=true`、`ready=true`；stop closed；uninstall 任务移除 | `C:/v2-acceptance/release-05b44447/service-chain-transcript.json` |
| C3.1-W2 官方安装链 | 同上 | **通过**：13 步 exit 0（上一版安装 205.9s → stage/prepare/environment 94.7s → install → rollback → doctor → 双卸载，attempt1 `cleanup_pending`、attempt2 成功；最终 `candidates=[]`、数据/备份保留） | `C:/v2-acceptance/release-05b44447/accept-chain-transcript.json` |
| C3.1-W1 安装检查 | 同上 | **通过（检查器已修）**：`verified=true`（308.1s）；attempt1 `uninstall_cleanup_pending` → attempt2 成功；产品行为不变，检查器补外部重试与尝试记录 | `C:/v2-acceptance/check-05b44447-retry2/report.json`；提交 `dae24cf7` |
| C3.6 72h | 同上（隔离实例） | **运行中**：开始 `2026-11-24T02:26:08+08:00`，结束 `2026-11-27T02:26:08+08:00`；60s 间隔，71+ 采样全 `ok=true`（RSS ≈140MB/线程 11/句柄 349/children 0/cleanup 0/failed 0/unknown 0）；会话独立守护自动 `--resume` | `C:/v2-acceptance/soak-05b44447/evidence`（manifest/samples/writer.lock）、`guardian.log` |
| macOS | `a85ac83c`（已并入） | 用户域闭环、桌面、Keychain、升级回滚真机通过；缺 Developer ID/公证、helper TCC 重授权、第二台 Mac、系统域（失败关闭） | `docs/evidence/v2-macos-release-candidate-2026-11-23.json` |
| Linux | G `32d35898`（已并入） | 真实 X11 身份、frame 截屏通过；Wayland portal 缺授权/撤回 | `docs/evidence/v2-linux-x11-probe-2026-11-23.json` |
| C3.1-W3 干净机 | — | **仍阻塞**：需一台干净 Windows；本机链通过不等于干净机通过 | 待所有者提供 |
| C3.2/C3.3/C3.5/C3.4 | — | **仍阻塞**：QQ/NapCat 账号、B 站账号、安卓设备、兼容视觉模型均缺 | 待所有者提供 |

> 结论：**产品缺陷已清零、候选已冻结（`dae24cf7`）、全量绿、官方产物链与检查通过、72h 运行中**；`whole_v2_complete=false`，`production_release_ready=false` 仅因 72h 窗口、干净机、签名/公证与真实账号/设备未完成。
