你接手 OmniOperator V2 的后续开发与验收。请实际核对代码和证据，不要重复已经完成的类型化。

> Portal授权接续（2026-11-15）：源码`a8c0048f`：系统共享就绪与授权审计分离，新增派生确认状态；界面v130明确等待确认，仍须显式查询/保存。原始用例同症状两轮复现，修正同步后两轮通过；54项不同专项及打包通过，strict332零错误。第79次固定源码全量（-n 2，artifact219）运行中，尚未收割。10项观察开放，整个V2未验收、默认V1。见[Portal授权证据](evidence/v2-portal-authorization.json)。

> 静态质量历史记录（第78次全量）：源码`92fbf78a`：既有15项I001、39个Python格式文件、11个前端格式文件已清零；53份Python业务AST/导入绑定及26份前端格式/版本变更已核对。Ruff/Prettier全范围、strict332零错误、85模块语法、61专项及wheel/sdist隔离导入通过，资源v129。全量第78次：4680 passed, 6 warnings in 2768.05s (0:46:08)；JUnit 0 failure / 0 error，4680个唯一节点。10项观察仍开放，整个V2未验收、默认V1。见[静态质量证据](evidence/v2-static-quality.json)。

> 稳定性历史记录（第77次全量）：源码`e5910a95`：模型请求头类型回归2→0（strict332文件），崩溃后文件调度已永久修复；模型47项、并行29项专项通过。全量第77次：4680 passed, 10 warnings in 2790.27s (0:46:30)；JUnit 0 failure / 0 error，4680个唯一节点；标准调度完成，无手动恢复。10项观察开放；当时有15项I001、39个Python与11个前端格式欠账，已在后续静态质量批次清理。整个V2未验收、默认V1。见[稳定性证据](evidence/v2-test-stability.json)。

> UI v128冻结记录（第76次全量）：手动连接接管、旧流程屏障与后台恢复已修复，89项专项通过。固定源码`4b6c0795`，pytest原始汇总4665 passed / 9 failed，4278.50s；JUnit分为6 failure和3 worker error，4671项收集/4674条记录含3次崩溃重放；调度恢复后完成。11项观察开放（原8项保留）；332文件类型收口不重做，V2未整体验收、默认V1。详见[UI证据](evidence/v2-ui.json)。

## 当前断点（2026-11-15）

- 工作区 D:/xm/OmniOperator，PowerShell（用分号，不用 &&），分支 codex/architecture-v2，上游 origin（Gitee）。
- 原 P4/P5/P6 的 24 个 adapters 已在第185～214批完成。strict 合同范围332文件、0 errors/0 warnings：domain53、application44、adapters222/222、composition13。
- 类型收口源码提交 73faa51a；源码指纹 1466148486cd6de41b467a190da0c3cdb062e91c07b7277fde39817d15cc5b39（1208个HEAD跟踪源码文件）。V1/tests与两个仅含说明的包入口不在该strict范围；不得称为全仓严格类型。
- 结构136表，迁移头v2_0037。整个V2仍未完成，默认正式入口仍V1，不迁V1数据，不双写，未经评审不切换入口。
- 类型收口历史全量：第75节点、批214，固定提交cbd71981：4660 passed / 3 failed / 0 errors / 0 skipped。复跑不替换原始并行口径。
- 待收割固定源码队列：第79次/artifact219运行中，固定源码a8c0048f；不要启动第二个全量。

## 必读与当前任务

1. AGENTS.md、docs/HANDOFF.md、docs/ARCHITECTURE.md、CONTRIBUTING.md。
2. docs/V2_HANDOFF_2026-11-14.md、docs/tasks/v2.json、docs/DEVELOPMENT_STATUS.md、docs/evidence/v2-typecheck.json。
3. docs/V2_AI_HANDOFF.md用于架构、目录与扩展/MCP合同；TYPE_CHECKING.md用于当前332文件门禁；TESTING.md用于测试纪律。修改UI前再读V2_UI_PROMPT.md。
4. 旧24模块错数表与旧308/314等断点只作历史，禁止从local_vectors或其他已完成模块重新摸底施工。
5. 若收到继续开发的目标，优先从已登记UI同步问题或其他离线交付缺口选择一个清楚范围，实际实现并验证；真实操作仍按既有具体授权。不要因尚需真实验收而虚报完成或停下可完成的离线工作。

## 验收边界

Portal等待、插件详情、记忆候选、模型预览、桌面view_id与Windows输入屏障等观察都保留在open_findings，归属不同。逐项看原始报告、串行/隔离与旧提交对照；不能把单次复跑当成持久修复，也不能把他会在途代码导致的转绿叫作抖动。

全量固定提交副本按顺序执行，禁止两个全量并跑。不要直接把快照源码写入PYTHONPATH：它会进入wheel安装子解释器并遮蔽已安装包。当前本机辅助工具temp/build/_full_checkpoint.py与_checkpoint_interpreter.py使用一次性解释器的.pth复用已有依赖及快照源码；不重建用户环境。原报告、快照哈希与HEAD blob指纹分别保存，Git archive换行转换不混作同一个指纹。

真实模型端到端、实际MCP/市场/第三方插件、Windows真实前台键鼠、干净机器安装更新回滚卸载、macOS/Linux构建与容器、24h/72h长跑、入口切换评审均待真实验收。Windows前台验收已明确延后，不自行弹窗或操作真实输入。

## 开发纪律

- 保留其他会话修改与PPT/、docs/competition/；提交前看git status和暂存清单，只用显式路径提交并推送，禁止git add -A。
- 始终uv run --no-sync；不要uv sync、停止用户服务或重建在用环境。演示使用全新绝对home和独立端口。V2开发入口为python -m omni.composition.cli --home <新目录> serve，默认8766。
- 小范围pytest -q -n 0覆盖目标与调用方；故障先按nodeid复跑。收尾全量按已定-n 2，源码/测试/依赖/配置相同时复用已完成结果；文案变更不反复跑全量。默认套件离线，不跳过、不缩短超时、不放宽断言。
- 修改一个明确模块；必要跨模块先给签名合同，避免混入其他函数体。维持332文件strict门禁与文档范围一致，不添加ignore。
- 新增/删除仓库文件更新project_index，task_status生成摘要并--check；源码与文档/证据分开提交。
- 四层单向、单实例单所有者多Agent、单omni.db；审批fail-closed，unknown不等于失败也不自动重放，数据库只用含WAL的一致快照；支付/发布/群发/加好友/无人值守继续审批。
