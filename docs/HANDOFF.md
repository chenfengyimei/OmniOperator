# OmniOperator 当前交接入口

> 当前正在实施V2底层重构；先读 [V2实施记录](V2_REFACTOR.md) 和 [V2任务台账](tasks/v2.json)。默认入口仍为V1，不能将新基础批次称为完整迁移。

跨会话接续请使用 [完整 AI 交接文档](V2_AI_HANDOFF.md) 和 [可复制的开发提示词](V2_AI_CONTINUATION_PROMPT.md)（2026-11-10）。它们补充源码恢复、当前欠账、D6.media开发合同及验证边界；后续状态仍以任务台账、当前源码和对应证据核验。

> 2026-11-12 当前推进 D7：能力报告、令牌管理、业务清单、导航收敛、strict 类型一百六十一批（285 文件：53/53 domain + 44 application 全层 + 175 adapters + 13 composition 全层，0 errors）、Windows 打包与安装器链（verify/seal/artifact/stage/prepare/switch-plan/backup/rollback-check/status/smoke/switch/install/migrate/current/uninstall/environment/rollback/purge）已完成离线验证。第一百三十八批后全量（-n 2）4654 项：4629 passed / 25 failed / 0 errors；24 项 UI 失败根因已修复并复跑 154 项全过（`576913f`），debug_sink 已隔离修复、Hermes 主题色已收敛 tokens.css；最新全量（第 20 节点，-n 2）4656 项：4655 passed / 1 failed / 0 errors，唯一失败为并行 UI 会话引入的 embed 副本守卫违规，其工作区已删副本但未提交，守卫读工作区故串行复跑通过（HEAD 仍含副本，版本库层面未关闭；并行口径仍记 1 failed）；不是全量全绿、全仓严格类型或三平台验收。剩余：application/adapters/composition 严格化、完整安装/更新执行（停服/启动、数据库备份恢复）、容器镜像、macOS/Linux 构建、干净机器端到端与正式入口切换。见[类型证据](evidence/v2-typecheck.json)与[完整交接](V2_AI_HANDOFF.md)。

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
