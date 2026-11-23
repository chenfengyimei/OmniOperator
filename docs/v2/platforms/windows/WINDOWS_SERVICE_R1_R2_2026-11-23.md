# Windows 服务 R1/R2 回归修复记录（2026-11-23）

状态：**已实现，待 H 实机复验**。两条都来自 H 在官方 wheel 上的真实 Task Scheduler 验收。

## R1 · 无法读取运行实例

- **现象**：`$instances.Item(0).EnginePID` 在真实 Task Scheduler 上抛异常，`start`/`status` 无法得到 running。
- **根因**：`IRegisteredTaskCollection` 是 COM 集合，PowerShell 5.1 不提供 `Item()` 方法；H 的 `foreach` 临时改法可取值。
- **修复**：`windows_services.ps1::Inspect-Task` 改为**枚举**实例，取首个非零 `EnginePID`；不再依赖 `.Count`/`.Item()`。状态判定改为「有非零 EnginePID 或 `State ∈ {2,4}` → running」。

## R2 · 误报就绪

- **现象**：R1 修正后动作 PID 仍不可证（`service_action_unverified`），但 `status` 仍返回 `ready=true`。
- **根因 A（进程身份）**：任务 `Exec/Command` 是计划捕获的 venv 启动器路径，而真实进程映像可能是计划同时捕获的 `process_python`（如 `C:\Python312\python.exe`）；原实现要求命令行首项**完全等于** `Command`，因此永远匹配不上。
- **根因 B（就绪条件）**：`service_control.running_postcondition` 对 Windows 分支显式允许 `pid is None`（历史原因：D11e 之前 Windows 拿不到 PID），于是「已就绪」与「无动作 PID」同时成立。
- **修复**：
  1. `service_query._action_pid(engine_pid, expected, plan)`：可接受映像集合 = 计划已固定的 `Exec/Command`、`runtime.python`、`python_realpath`、`process_python`（`normcase(realpath)` 归一），仍要求**参数按序匹配**计划 `Arguments`（`-I -u -m omni.composition.service_runner --deployment <bundle>`），并新增 Windows 引号规则的分词器 `_windows_tokens`。
  2. `service_control.running_postcondition`：所有平台统一要求 `type(pid) is int and pid > 0 and pid == proof.pid`；`pid=null` 一律不判 ready。
- **保持不变**：引擎缺失/不可证 → `state=unknown`、`service_manager_state_unavailable`；定义漂移优先 `service_definition_mismatch`；不猜测、不重试。

## 验收与回归

| 项 | 结果 |
|---|---|
| `test_v2_service_query.py` | **37 passed**（新增 launcher→python 正向、4 类外来进程拒绝、引擎消失、原生不可用） |
| `test_v2_system_service_start.py` | **35 passed**（新增 Windows `pid=None`/错 PID 不通过、匹配 PID 通过） |
| 服务四模块合计 | **95 passed / 31.57s** |
| strict / ruff | **434 文件 0 错 0 警**；`ruff check` 与格式检查通过 |

## H 的复验步骤（同一候选）

```powershell
# 从修复提交重建官方 wheel 后：
service apply-operation <install.json> --approve <sha>   # 期望 installed=true/definition_matches=true/state=stopped
service apply-control <start.json> --approve <sha>        # 期望 ready=true、boot_id 一致、HTTP ready=true
service status <bundle>                                   # 期望 state=running、pid=真实动作进程 PID、ready=true
service apply-control <stop.json> --approve <sha>         # 期望 closed=true
service apply-operation <uninstall.json> --approve <sha>  # 期望任务移除、端口关闭
```

若仍失败，请带回以下之一：`error_code`（`service_action_unverified` / `service_manager_state_unavailable` / `service_definition_mismatch`）、`state`、`pid`，以及任务动作命令行与引擎 PID；`pid` 应等于启动回执的 `boot` PID。
