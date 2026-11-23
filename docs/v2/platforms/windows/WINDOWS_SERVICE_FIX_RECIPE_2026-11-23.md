# Windows 原生服务修复配方（D10–D11e，2026-11-23）

> 性质：**审阅用补丁 + 回归/验收清单**。验收会话未修改仓库业务源码；`windows-service-fixes-2026-11-23.patch` 未应用到 `v2`，只作为 A/开发会话的输入。
>
> 基线：候选 `8412e773e75808a4e2b04ff221bb8cc312ed9899`（origin/v2）。补丁已用 `git apply --check` 验证可干净应用到该 SHA，Python 文件 `py_compile` 通过，PowerShell helper 解析通过。

## 1. 缺陷与补丁

| ID | 文件/函数 | 问题 | 补丁做法 |
| --- | --- | --- | --- |
| D10 | `adapters/platform/services/service_templates.py::_windows` | 生成 `<?xml ... encoding='utf-8'?>`，Task Scheduler COM `RegisterTask` 抛 `0x8004131A (1,40) 无法切换编码` | `ET.tostring(..., xml_declaration=False)`（helper 与 query 都按 UTF-8 字节处理） |
| D11 | `adapters/workers/windows_services.ps1::Same-Definition` | 真实导出省略默认值（`RunLevel`、`Enabled`、`StartWhenAvailable`…），`LogonTrigger/UserId` 由 SID 归一化为 `CY\cy`，`UseUnifiedSchedulingEngine=true`；严格比较失败 | 新增 `Add-Defaults`：补默认值、账号名→SID、移除 `UseUnifiedSchedulingEngine`，再比较 |
| D11c | `adapters/platform/services/service_query.py::_task_identity` | 要求 Principal 恰含 `RunLevel`，真实导出省略 | 允许子集；缺失 `RunLevel` 规范化为 `LeastPrivilege`；拒绝重复元素 |
| D11d | `adapters/platform/services/service_query.py::_xml` | `schtasks /Query /XML` 输出 UTF-8 字节却声明 `encoding="UTF-16"`，`ET.fromstring` 抛 ParseError | 解析前去掉 XML 声明 |
| D11e | `adapters/platform/services/service_query.py::_query` | 只读查询不补 running/PID，manager 永远 `state=unknown`，status 永远 `ready=false` | **未包含在补丁**：需二选一——COM/受管进程与监听证明 running，或合同明确 Windows status ready 不可得、以 start 回执为准 |

## 2. 补丁内容

文件：`docs/v2/platforms/windows/windows-service-fixes-2026-11-23.patch`（5,655 字节，3 文件，+43/−5）。

```powershell
git switch -c windows-service-fix v2
git apply --check docs/v2/platforms/windows/windows-service-fixes-2026-11-23.patch
git apply docs/v2/platforms/windows/windows-service-fixes-2026-11-23.patch
```

> 应用后必须重建 wheel；`service query` 与 helper 都进入部署完整性哈希，**不能**在 capture/materialize 之后再改这些文件（实测会变成 `deployment_program_changed`）。

## 3. 必须补的回归

1. `agent/tests/v2/operations/test_v2_service_query.py`
   - 输入 UTF-8 字节但声明 `encoding="UTF-16"` 的真实 `schtasks` 样例 → `_xml` 成功解析。
   - Principal 省略 `RunLevel` → `_task_identity` 返回 `LeastPrivilege`，且与完整定义的 identity 相等。
   - 重复 `RunLevel`/未知 Principal 子元素 → 仍拒绝。
2. `agent/tests/v2/operations/test_v2_service_registration.py`（或 helper 专用用例）
   - 真实注册后导出的 XML（省略默认值、`UserId` 为账号名、`UseUnifiedSchedulingEngine=true`）→ `Same-Definition` 为真。
   - 改动动作/路径/账号 → 仍为假。
3. `agent/tests/v2/operations/test_v2_service_deployment.py`
   - `_windows` 渲染结果不含 `<?xml` 声明；`service.xml` 可被 `schtasks /Create /XML` 与 COM `RegisterTask` 接受（真实 OS 用例单独放平台验收脚本，离线测试至少断言无声明）。

## 4. 官方验收顺序（修完重建 wheel 后）

```powershell
# 1) 安装候选到隔离 home（基础 Python + 固定依赖包）
install_v2.py --wheel <新whl> --sha256 <新sha> --dependency-bundle <deps> --dependency-sha256 <deps-sha> --manager <mgr> --home <home>

# 2) capture → plan → materialize → verify
service capture <capture.json>
service plan <request.json> <capture.json> <plan.json>
service materialize <plan.json> <bundle>
service verify <bundle>            # 期望 exit 0

# 3) 注册（不启动）
service operation-plan <bundle> install <op-install.json>
service apply-operation <op-install.json> --approve <op-sha>
# 期望 state=succeeded, installed=true, definition_matches=true, state=stopped

# 4) 启动 → 就绪
service control-plan <bundle> start <cp-start.json>
service apply-control <cp-start.json> --approve <cp-sha>
# 期望 state=succeeded, observed.ready=true, boot_id 与计划一致
# 期望 HTTP /api/v1/version 200, ready=true

# 5) 只读状态（D11e 修法决定后）
service status <bundle>
# 若 D11e 选“补 running 证明”：期望 ready=true
# 若 D11e 选“明确不可得”：期望 manager.state=unknown 但文档/输出明确 ready 不可用

# 6) 正常排空 → 卸载
service control-plan <bundle> stop <cp-stop.json>
service apply-control <cp-stop.json> --approve <cp-stop-sha>
# 期望 closed=true, instance_lock_released=true
service operation-plan <bundle> uninstall <op-uninstall.json>
service apply-operation <op-uninstall.json> --approve <op-uninstall-sha>
# 期望 task removed, 端口关闭, 进程退出
```

## 5. 已完成的等价演练

补丁等价改动已在隔离安装副本上跑通（未入仓库）：

| 演练 | 结果 |
| --- | --- |
| svc2：D10+D11 | 注册 succeeded/definition_matches=true；start boot `a71ef415…` pid 93500 ready=true HTTP 200；stop closed=true；uninstall 删除任务 |
| svc3：D10+D11+D11c | 生命周期再次通过；status 被 D11d 挡住 |
| svc4：D10+D11+D11c+D11d | definition_matches=true、生命周期通过；status manager `state=unknown`、overall `ready=false`（D11e） |

证据：`docs/evidence/v2-windows-service-defect-2026-11-23.json`、`C:/v2-acceptance/c3-08dd1573/svc2/rehearsal4/transcript.json`。

## 6. 当前结论

- 官方候选 `8412e773` 未修复，C2.7 仍为 **failed**；本补丁只是给 A 的修复输入。
- 修复并重建后，需要重跑上面的注册→启动→就绪→排空→卸载；D11e 的决定影响 `service status` 是否能为 `ready=true`。
- 临时任务、端口、进程已在演练后清理；安装副本文件已恢复为仓库哈希。
