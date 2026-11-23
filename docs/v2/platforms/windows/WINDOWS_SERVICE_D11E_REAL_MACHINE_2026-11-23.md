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
