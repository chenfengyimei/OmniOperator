# OmniOperator 当前交接入口

> 本机worker诊断固定副本（2026-11-15）：源码`e731e079`，默认不采样；新目录按需保存阶段、提交额度、退出码与持久Python故障栈，11项新增回归和固定副本123专项通过。11-16收割第82次全量：22 failed, 4685 passed, 10 warnings, 1 error in 2764.82s (0:46:04)，4707/4707唯一节点；23个失败节点串行全过，原始失败保留。实际捕获0xc00000fd，停在PyAV编码调用，退出前提交余量476KiB；3个视频对照和检索100轮重复通过。本副本排除冻结时其他会话未提交UI/迁移，不能用来宣称那些改动已验；12项观察仍开放，V2未整体验收、默认V1。见[worker诊断证据](evidence/v2-worker-diagnostics.json)。

> 运行与报告接续（2026-11-15）：源码`be756a0d`：运行身份测试等待实际发布，原用例两轮受控复现/修复两轮通过；保留替换检查，生产逻辑未改。失败报告MemoryError有界兜底并即时记录失败节点，9报告回归另在父worker并行通过，96项不同当前专项通过。第81次全量：4 failed, 4666 passed, 12 warnings, 28 errors in 3028.18s (0:50:28)；JUnit 3 failure / 29 error，4696/4696个唯一节点。31个失败节点串行复跑全过，原始失败保留；实测系统提交额度触顶，报告兜底在真实MemoryError下保留原始异常并完成套件。12项观察开放，内存分配来源未定；strict332保持，整个V2未验收、默认V1。见[运行与报告证据](evidence/v2-runtime-reporting.json)。

> Portal授权历史记录（第79/80次全量）：源码`5c5402c8`：系统共享就绪与授权审计分离，新增派生确认状态；界面v130明确等待确认，仍须显式查询/保存。原始用例同症状两轮复现，修正同步后两轮通过；54项不同专项及打包通过，strict332零错误。第80次原始全量因报告MemoryError中断：4512 passed / 2 failed，4514/4686具名节点；JUnit另有2条框架error事件及非标准记录。3项精确复跑和171项遗漏补跑均过，分阶段覆盖4686项，原始中断与失败保留。12项观察开放，整个V2未验收、默认V1。见[Portal授权证据](evidence/v2-portal-authorization.json)。

> 静态质量历史记录（第78次全量）：源码`92fbf78a`：既有15项I001、39个Python格式文件、11个前端格式文件已清零；53份Python业务AST/导入绑定及26份前端格式/版本变更已核对。Ruff/Prettier全范围、strict332零错误、85模块语法、61专项及wheel/sdist隔离导入通过，资源v129。全量第78次：4680 passed, 6 warnings in 2768.05s (0:46:08)；JUnit 0 failure / 0 error，4680个唯一节点。10项观察仍开放，整个V2未验收、默认V1。见[静态质量证据](evidence/v2-static-quality.json)。

> 稳定性历史记录（第77次全量）：源码`e5910a95`：模型请求头类型回归2→0（strict332文件），崩溃后文件调度已永久修复；模型47项、并行29项专项通过。全量第77次：4680 passed, 10 warnings in 2790.27s (0:46:30)；JUnit 0 failure / 0 error，4680个唯一节点；标准调度完成，无手动恢复。10项观察开放；当时有15项I001、39个Python与11个前端格式欠账，已在后续静态质量批次清理。整个V2未验收、默认V1。见[稳定性证据](evidence/v2-test-stability.json)。

> UI v128冻结记录（第76次全量）：手动连接接管、旧流程屏障与后台恢复已修复，89项专项通过。固定源码`4b6c0795`，pytest原始汇总4665 passed / 9 failed，4278.50s；JUnit分为6 failure和3 worker error，4671项收集/4674条记录含3次崩溃重放；调度恢复后完成。11项观察开放（原8项保留）；332文件类型收口不重做，V2未整体验收、默认V1。详见[UI证据](evidence/v2-ui.json)。

> 类型收口全量记录（第 75 节点，第 214 批）：原始 **4660 passed / 3 failed / 0 errors / 0 skipped**；固定提交 cbd71981，后续源码不在本次范围。3项UI原始失败，串行1过2败，再独立隔离2过；旧提交同参数两轮3过及2过1败，模型预览同症状在旧提交复现，UI观察继续保留。该冻结源码与当前332文件类型化交付包一致。详见证据台账。复跑不替换原始并行失败口径。

> strict 当前接续（2026-11-15，第 214 批）：**332 文件 / 0 errors / 0 warnings**；adapters **222/222**，剩 **0** 个模块。最新 `agent/src/omni/adapters/mcp/oauth.py`：MCP OAuth类型收官，V2四层合同范围332文件，真实验收仍待。相关专项 **94 项通过 / 0 项失败**，源码 `73faa51a`；最近全量记录为第 75 节点（4660 passed / 3 failed），仅代表其固定源码；本交付包全量已收割，原始失败与开放观察继续留档。默认正式入口仍是 V1，真实验收未完成。

> V2 严格类型合同范围已收口，当前继续开发验收与产品交付；先读 [V2实施记录](V2_REFACTOR.md) 和 [V2任务台账](tasks/v2.json)。默认入口仍为V1，不能将新基础批次称为完整迁移。

跨会话接续先读 [当前完整交接](V2_HANDOFF_2026-11-14.md) 和 [更新后的开发提示词](V2_HANDOFF_PROMPT_2026-11-14.md)；[架构与目录合同](V2_AI_HANDOFF.md) 保留设计依据。旧提示词与旧错数表不作为当前待办。

> 2026-11-12 历史推进记录： D7：能力报告、令牌管理、业务清单、导航收敛、strict 类型一百八十四批（308 文件：53/53 domain + 44 application 全层 + 198 adapters + 13 composition 全层，0 errors）、Windows 打包与安装器链（verify/seal/artifact/stage/prepare/switch-plan/backup/rollback-check/status/smoke/switch/install/migrate/current/uninstall/environment/rollback/purge）已完成离线验证。第一百三十八批后全量（-n 2）4654 项：4629 passed / 25 failed / 0 errors；24 项 UI 失败根因已修复并复跑 154 项全过（`576913f`），debug_sink 已隔离修复、Hermes 主题色已收敛 tokens.css；最新全量（第 40 节点，-n 2）4658 项：4656 passed / 2 failed / 0 errors，第 35 节点的 14 项 #agents 与 3 项 CSS 守卫已全部转绿（UI 会话修复），剩 2 项 UI 抖动（投递归档串行/干净 HEAD 均过；portal 授权属既有 flaky，多次复跑全过）；并行口径仍记 2 failed；不是全量全绿、全仓严格类型或三平台验收。剩余：application/adapters/composition 严格化、完整安装/更新执行（停服/启动、数据库备份恢复）、容器镜像、macOS/Linux 构建、干净机器端到端与正式入口切换。见[类型证据](evidence/v2-typecheck.json)与[完整交接](V2_AI_HANDOFF.md)。

> 较早的 D6 记录：文本技能已接对象修订、导入导出、参考文件、编辑/节段修补、回滚与归档、明确审阅启用、回合封存/实际披露和撤权恢复。模型工具文本已补编辑修订/内容版本、文件清单、ID与状态回执，实际审批编辑和参考读取闭环通过；不能只在后台data保存模型所需信息。Windows ZIP原始名称与共享关闭短预算接续已修复。78项不同专项最新通过，含16例四视口双主题；16份默认类型、Ruff/排版通过。125表/v2_0035，工作台v47；未全量/重打包、真实模型/设备、Mac/Linux或72小时。旧精确整理UI时序仍待复核。见[当前证据](evidence/v2-skills.json)与[技能合同](V2_SKILLS.md)。下一D6媒体/视频理解，MCP/OAuth/完整插件市场及D7/D8仍待，整个V2未完成，默认入口仍为V1。

> 较早的完整 Windows 离线封存 R3.retention_references：其固定源码全仓2606项通过，0失败/错误/跳过，565.82秒，697份冻结文件一致，见[历史引用释放证据](evidence/v2-references.json)。其后游标批次唯一全量2659通过/1旧终端竞态失败，修复后相关24通过；该历史记录不代表当前源码全绿。整个V2尚未完成，默认入口仍为V1；业务、原生及三平台/72小时待验收。

项目统一整改以 [当前状态](DEVELOPMENT_STATUS.md) 和 [统一规则](UNIFICATION.md) 为入口。
架构唯一权威是 [ARCHITECTURE.md](ARCHITECTURE.md)，界面规范是 [UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md)。

1. 阅读当前状态，确定任务范围与依赖；每批只修改该任务所需内容，保留既有未提交成果。
2. Agent 后端在 agent/，前端源码在仓库根 web/；根 src/tests 是本机兼容联接。
3. config/data/.env/skills 是既有运行位置，不因重排源码而迁移。
4. 使用 `uv run --no-sync`，不要为测试停止用户服务或重建正在使用的虚拟环境。
5. 专项覆盖修改及调用方；任务收尾运行一次全量，默认离线，不将专项写成全量。
6. 新增/删除文件后运行 `uv run --no-sync python scripts/project_index.py`，提交前检查 `--check`。
7. 支付、发布、群发、加好友与无人值守行为继续遵循既有审批和 fail-closed 规则。
8. unknown 不等于失败，不自动重发；数据库使用一致快照，不能只复制 db 而漏掉 WAL。

历史能力、平台坑与各批次证据完整保留在 [历史交接](../temp/archive/docs/2026-11-01/HANDOFF.md)。
[jilu](jilu/) 是历史来源，不能替代当前任务状态；历史测试数只代表对应源码版本。
