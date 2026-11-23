# D11e 实机回归（d7971bc4）：COM 集合 `.Item(0)` 不可用

> 给 A：D11e 的 COM state + EnginePID + 动作进程证明思路正确，但 `windows_services.ps1` 取首个 running instance 的方式在真实 Windows PowerShell 5.1 上失败。H 用 d7971bc4 官方 wheel 实测复现。

## 现象

- 任务已注册且 `definition_matches=true`；`schtasks /Query` 显示 **Running**。
- 服务实际可用：`GET /api/v1/version` 200，`ready=true`，runner pid 134544。
- 但 `service start` 返回 `state=unknown, error_code=service_control_outcome_unverified`；`service status` 返回 manager `state=unknown`、`pid=null`、overall `ready=false`。
- helper 原始 inspect 返回 `service_native_unverified`，把整个回执吞成 unknown。

## 根因

`agent/src/omni/adapters/workers/windows_services.ps1::Inspect-Task`：

```powershell
$instances = $task.GetInstances(0)
...
if ($instances.Count -gt 0) { $result.engine_pid = [int]$instances.Item(0).EnginePID }
```

真实环境：

- `$instances.Count` = 1；
- `$instances.Item(0)` 抛 `System.ArgumentException: Value does not fall within the expected range`；
- 外层 `catch` 将 `state` 置 unknown、`engine_pid` 置 null，返回 `service_native_unverified`；
- `foreach ($instance in $instances)` 可正常取到 `EnginePID=91764`、`State=4`。

## 建议修复

```powershell
$first = $null
foreach ($instance in $instances) { $first = $instance; break }
if ($null -ne $first) { $result.engine_pid = [int]$first.EnginePID }
```

不要使用 COM `Item(0)`/索引器。

## 验证

H 用一个临时副本把该行改为 foreach 后，同一 helper inspect 返回：

```json
{"state":"running","engine_pid":91764,"definition_matches":true,"error_code":""}
```

psutil 在 91764 下找到动作进程 134544，命令行为 `python -I -u -m omni.composition.service_runner --deployment .../bundle`，与计划 `Exec/Command+Arguments` 匹配。

## 修复后必须重跑

1. 重建 wheel（`3fbba07b…` 之后新 SHA）。
2. 隔离 home：capture→plan→materialize→verify→install→start→**status（期望 ready=true、pid 非空）**→drain→uninstall。
3. 若 status 仍失败，保留 helper JSON、任务 XML、HTTP 回执和动作进程命令行。

证据：`docs/evidence/v2-windows-service-d7971bc4-2026-11-23.json`、`C:/v2-acceptance/c3-08dd1573/svc8/probes/d11e-real-machine.txt`。

## 第二轮：foreach 修复后仍有 R2

H 在另一个隔离 home（svc9）把该行改为 foreach 并重走 capture/materialize：

- `start`：`succeeded`、`ready=true`（boot `cdfe63ca…`、pid 133796）；
- `status`：manager `state=running`、`definition_matches=true`，但 **`pid=null`、`error_code=service_action_unverified`，overall `ready=true`**；
- `stop`/`uninstall`：`succeeded`。

这与 `docs/v2/operations/V2_SERVICES.md` 的“pid null / service_action_unverified 不判 ready”矛盾。

**R2 根因**：计划 `Exec/Command` 是 venv 的 `Scripts\python.exe`，任务引擎下的实际动作进程是基础 Python（`C:\Users\...\Python312\python.exe`，参数 `-I -u -m omni.composition.service_runner ...`）。`_action_pid` 用 `cmdline[0]` 与 venv 路径严格比较，匹配失败。

**A 需要同时修**：

1. helper 用 foreach 取 `EnginePID`；
2. `_action_pid` 将 venv 启动器解析到实际 `process_python`（或按 python 进程映像+参数匹配）；
3. `pid=null/service_action_unverified` 时强制 `ready=false`。

证据：`C:/v2-acceptance/c3-08dd1573/svc9/probes/foreach-fix-and-action-pid.txt`。

## 当前源码位置（d7971bc4）

| 问题 | 文件:行 | 现状 |
| --- | --- | --- |
| R1 | `agent/src/omni/adapters/workers/windows_services.ps1:151` | `$instances.Item(0).EnginePID` 抛 ArgumentException，catch 后 `service_native_unverified` |
| R2 动作身份 | `agent/src/omni/adapters/platform/services/service_query.py:94` / `:116` | `_action_pid` 只接受计划 `Exec/Command`（venv `python.exe`）；真实动作进程是 `process_python`（基础 Python），`cmdline[0]` 严格比较失败 |
| R2 就绪条件 | `agent/src/omni/domain/service_control.py:133` | Windows 分支 `pid is None` 仍允许 ready；导致 `pid=null/service_action_unverified` 时 `ready=true` |

## 建议的最小修改

```powershell
# windows_services.ps1
foreach ($instance in $instances) { $result.engine_pid = [int]$instance.EnginePID; break }
```

```python
# service_query.py：_action_pid 接受计划 runtime 的 process_python
def _action_pid(engine_pid, expected, plan):
    ...
    commands = {os.path.normcase(command)}
    process_python = (plan.get("runtime") or {}).get("process_python")
    if isinstance(process_python, str) and process_python:
        commands.add(os.path.normcase(process_python))
    ...
    if not cmdline or os.path.normcase(cmdline[0]) not in commands:
        continue
```

```python
# service_control.py：所有目标都要求非空且匹配的 action PID
return type(pid) is int and pid > 0 and pid == proof.get("pid")
```

## 修复后的 H 验收标准

1. `status`：manager `state=running`、`definition_matches=true`、`pid` 非空且等于 runtime pid、`ready=true`；
2. 无法证明动作 PID 时：`pid=null`、`service_action_unverified`、**`ready=false`**；
3. `start`/`stop`/`uninstall` 仍 succeeded；任务删除、端口释放、实例锁释放。
