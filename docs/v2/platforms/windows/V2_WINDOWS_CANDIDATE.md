# Windows V2 离线候选交付

> 文档范围：本页维护该能力合同与所属批次记录。当前默认入口为 V2；[当前交接](../../../HANDOFF.md)区分已实现、专项通过和原生验收，历史测试数字不覆盖当前源码。

当前目标由所有者限定为Windows开发与离线候选；真实账号、设备、原生桌面操作、72小时和其他系统另作发布门槛。Windows离线候选默认入口为V2，保留显式V1回退；真实环境和整个V2仍未整体验收。当前状态以[候选任务](../../../tasks/v2-windows-candidate.json)为准。

## 安装与维护

准备Python 3.12+、应用wheel及其SHA256、平台匹配的依赖目录及manifest.json的SHA256。依赖目录可由`build_v2_dependencies.py`从已获取的锁定wheel生成；下载依赖与默认离线测试分开，安装器不会下载。home和manager必须是不存在且互不嵌套的新目录。

```powershell
./scripts/install-v2.ps1 -Python 'C:\Python312\python.exe' `
    -Wheel 'D:\release\omnioperator-0.1.0-py3-none-any.whl' -Sha256 <应用SHA256> `
    -DependencyBundle 'D:\release\dependencies' -DependencySha256 <清单SHA256> `
    -Manager 'D:\omni-v2-manager' -InstanceHome 'D:\omni-v2-instance'
& 'D:\omni-v2-instance\omni.cmd' serve --open-browser
```

离开仓库分发时，使用`agent/scripts/install_v2.py`，与wheel一起提供；它不需要仓库或已安装的Omni。执行`python -I install_v2.py --help`查看相同参数。安装器生成独立维护venv和实例候选venv，验证依赖闭包、安装态导入、备份与恢复演练后选择启动指针，不注册或启停系统服务。

停止本候选后，通过外部维护解释器执行同schema候选更新：先`installation stage <wheel> --sha256 <SHA>`，再`installation environment --sha256 <SHA> --dependency-bundle <目录> --dependency-sha256 <清单SHA>`，最后`installation install <wheel> --sha256 <SHA>`。每条命令前加维护解释器、`-I -m omni.composition.cli --home <实例目录>`。跨schema或服务切换继续使用[release/exchange合同](../../operations/V2_SERVICE_EXCHANGE.md)，当前开发wheel验证不等于公开版本升级。

保留数据卸载也必须从实例之外的维护解释器执行：

```powershell
& 'D:\omni-v2-manager\venv\Scripts\python.exe' -I -m omni.composition.cli `
    --home 'D:\omni-v2-instance' installation uninstall --sha256 <当前应用SHA256>
```

文件被占用时有界重试，仍失败则返回`uninstall_cleanup_pending`，保留精确卸载收据；稍后原命令可续跑。`installation status`显示`cleanup_pending`和`next_step=uninstall`。另一个候选成为当前版本时拒绝继续删除旧候选。数据、安装收据、外部备份和维护解释器保留；这里不使用`--purge-data`。

## V2 专用离线门禁

`scripts/v2_candidate_gate.py`只读核对材料，不导入V1，不启动测试或用户服务，不授权正式发布。

```powershell
python -I scripts/v2_candidate_gate.py snapshot --root <固定候选源码> --output <新建source.json>
python -I scripts/v2_candidate_gate.py assess --root <同一候选源码> --manifest <manifest.json> --output <新建assessment.json>
```

source快照覆盖运行代码、测试、脚本、界面、部署配方、依赖/类型配置及V2合同文档，记录逐文件hash和整体指纹；不把进度/证据文件纳入代码身份。manifest格式为`omni-v2-candidate-gate-1`，source及每份检查使用`{"path":"相对证据路径","sha256":"64位hash"}`绑定；路径不可越界或经过链接。

必须提供`full_pytest`、`strict_types`、`ruff`、`python_format`、`frontend`、`package`、`windows_lifecycle`、`business_ui`八份`omni-v2-candidate-check-1`报告。每份包含kind、source_sha256、真实command、returncode和至少一个固定artifact；必须同一源码、退出码为0。全量另含无选择器的collection_command、collection_returncode、collected节点JSON和junit；拒绝专项、`-k`、`--lf`、遗漏/重复节点、失败或跳过。strict要求pyright JSON零错误/警告且文件数等于候选配置，其他检查保留执行日志/证明。

通过仅得到`ready_for_windows_offline_review=true`；`production_release_ready`和`whole_v2_complete`仍为false，真实账号/设备、桌面、72小时和其他平台始终列为未验证。只有同一冻结候选的八项检查材料通过才可合入；工具回归的合成材料不能代替产品验收。
