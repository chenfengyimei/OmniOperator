你接手 OmniOperator V2 的后续开发与验收。请实际核对代码和证据，不要重复已经完成的类型化。

## 当前断点（2026-11-15）

- 工作区 D:/xm/OmniOperator，PowerShell，分支 codex/architecture-v2，上游 origin（Gitee）。
- 原 P4/P5/P6 的 24 个 adapters 已在第185～214批完成。strict 合同范围332文件、0 errors/0 warnings：domain53、application44、adapters222/222、composition13。
- 最后源码提交 73faa51a；源码指纹 1466148486cd6de41b467a190da0c3cdb062e91c07b7277fde39817d15cc5b39（1208个HEAD跟踪源码文件）。V1/tests与两个仅含说明的包入口不在该strict范围；不得称为全仓严格类型。
- 结构136表，迁移头v2_0037。整个V2仍未完成，默认正式入口仍V1，不迁V1数据，不双写，未经评审不切换入口。
- 最近收割全量：第60节点、批199，固定提交5aec7588：4660 passed / 3 failed / 0 errors / 0 skipped。复跑不替换原始并行口径。
- 待收割固定源码队列：第65节点/批204、第70节点/批209、第75节点/批214。先核对证据的full_run、full_run_history及full_checkpoints_pending；已完成的报告不要重新跑。

## 必读与当前任务

1. AGENTS.md、docs/HANDOFF.md、docs/ARCHITECTURE.md、CONTRIBUTING.md。
2. docs/V2_HANDOFF_2026-11-14.md、docs/tasks/v2.json、docs/DEVELOPMENT_STATUS.md、docs/evidence/v2-typecheck.json。
3. docs/V2_AI_HANDOFF.md用于架构、目录与扩展/MCP合同；TYPE_CHECKING.md用于当前332文件门禁；TESTING.md用于测试纪律。修改UI前再读V2_UI_PROMPT.md。
4. 旧24模块错数表与旧308/314等断点只作历史，禁止从local_vectors或其他已完成模块重新摸底施工。
5. 若收到继续开发的目标，优先从已登记UI同步问题或其他离线交付缺口选择一个清楚范围，实际实现并验证；真实操作仍按既有具体授权。不要因尚需真实验收而虚报完成或停下可完成的离线工作。

## 验收边界

Portal等待、插件详情、记忆候选、模型预览、桌面view_id与Windows输入屏障观察都保留在open_findings，归属不同。逐项看原始报告、串行/隔离与旧提交对照；不能把单次复跑当成持久修复，也不能把他会在途代码导致的转绿叫作抖动。

全量固定提交副本按顺序执行，禁止两个全量并跑。不要直接把快照源码写入PYTHONPATH：它会进入wheel安装子解释器并遮蔽已安装包。当前本机辅助工具temp/build/_full_checkpoint.py与_checkpoint_interpreter.py使用一次性解释器的.pth复用已有依赖及快照源码；不重建用户环境。原报告、快照哈希与HEAD blob指纹分别保存，Git archive换行转换不混作同一个指纹。

真实模型端到端、实际MCP/市场/第三方插件、Windows真实前台键鼠、干净机器安装更新回滚卸载、macOS/Linux构建与容器、24h/72h长跑、入口切换评审均待真实验收。Windows前台验收已明确延后，不自行弹窗或操作真实输入。

## 开发纪律

- 保留其他会话修改与PPT/、docs/competition/；提交前看git status和暂存清单，只用显式路径提交并推送，禁止git add -A。
- 始终uv run --no-sync；不要uv sync、停止用户服务或重建在用环境。演示使用全新绝对home和独立端口。V2开发入口为python -m omni.composition.cli --home <新目录> serve，默认8766。
- 小范围pytest -q -n 0覆盖目标与调用方；故障先按nodeid复跑。收尾全量按已定-n 2，源码/测试/依赖/配置相同时复用已完成结果；文案变更不反复跑全量。默认套件离线，不跳过、不缩短超时、不放宽断言。
- 修改一个明确模块；必要跨模块先给签名合同，避免混入其他函数体。维持332文件strict门禁与文档范围一致，不添加ignore。
- 新增/删除仓库文件更新project_index，task_status生成摘要并--check；源码与文档/证据分开提交。
- 四层单向、单实例单所有者多Agent、单omni.db；审批fail-closed，unknown不等于失败也不自动重放，数据库只用含WAL的一致快照；支付/发布/群发/加好友/无人值守继续审批。
