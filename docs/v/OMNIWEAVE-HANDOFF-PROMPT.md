# 主设备接手 OWA 的交接提示词

复制下方全文发送到 OmniOperator 主设备会话。提示词包含独立的目标、首批范围和验收条件。配套资料见 [开发策划案](OMNIWEAVE-ROADMAP.md) 与 [技术规格书 V1.0](architecture/OMNIWEAVE-ARCHITECTURE-SPEC-V1.0.md)。

```text
请接手 OmniOperator v2 的 OmniWeave Architecture（OWA）。我已安排你在当前开发批次交付后接续这项工作。副设备已经完成策划和技术规格，本轮请从主设备的实际源码开始核对和开发。

目标是一个用户只用一个私有 Web 主会话。Router 理解需求、规划、调度、协调和最终交付。多个 Worker 各自使用内部 Session、工作目录、能力范围和预算，经同一内核并行执行。Worker 可以在同一个根 Ticket 范围内直接发起请求、接收回复和共享授权产物，未完成的任务和未消费的消息可在重启后续接。用户在主会话追加、审批、取消和读取结果。

先读 AGENTS.md、docs/v/HANDOFF.md、docs/v/reference/ARCHITECTURE.md、CONTRIBUTING.md 和当前台账。若已收到配套文件，再读 docs/v/OMNIWEAVE-ROADMAP.md 与 docs/v/architecture/OMNIWEAVE-ARCHITECTURE-SPEC-V1.0.md。仅收到本提示词时，也可按以下范围完成首批工作。保留其他会话的在途修改，核对主设备 HEAD、最新 origin/v2、实际迁移头和候选证据。策划使用公开快照 702ff47。原规格使用 7372a00。已归档的 8739 项离线通过属于 1a2810ea。以接手当天的真实源码和各自证据为准。

首批从 Phase 0 到 Phase 1。在最新可用 v2 上用短名任务分支，例如 owa-tickets。Phase 0 固定计划改动的入口和基线。用现有功能索引定位 /turns、SSE、Application、work_items、leases、审批/动作核查、现有调度和功能登记。核对空库与旧 V2 测试数据的升级方式，并记录涉及的共用接口。基线成立后直接做 Phase 1 首个生产闭环。

Phase 1 先定最小 Trigger、Thread、Ticket 与授权来源。沿现有 Web /turns 打通一条观察链路。默认 off。observe 在同一数据库事务保存规范事件和观察决定，正常完成原 V2 回合，后台 Worker 启动数为零。验证相同来源键同载荷返回原回执，异载荷冲突，事务中断和并发重投无额外任务或 Run，跨 Agent 与跨主会话引用被拒绝。沿用 HTTP 202 和 run_id、session_id、duplicate 回执。必要的 Alembic 加法迁移从实际头接续，验证空库、旧 V2 测试副本、integrity_check 和 foreign_key_check。

实现走 V2 四层、同一 omni.db 和同一 Application/dispatcher。以现有 FeaturePlugin 登记，同一实例拥有 HTTP、工具和生命周期。需要内核回调时参照 KernelFeatureContext 和调度启动顺序。沿用原有 Run、工作队列、租约、预算、审批、动作、投递和核查账本。旧 delegate_task 继续按原合同提供。Worker 写入须按现有逐次审批。已派发且结果不明的操作进入原 unknown 核查流程，资源在执行器清理后释放。内部 Session、Worker 私有文本与跨范围引用由服务端执行访问控制。

随后分阶段推进。Phase 2 完成 Router / Worker 和任务依赖，验收三个 Worker 并行时 Router 仍能处理主会话新输入。Phase 3 验收同组双向邮箱、版本化产物、追加需求和重启续接，由 Router 在原会话交付。Phase 4 补长作业与 Watchdog。Phase 5 接数字身体。Phase 6 观测并逐步接外部事件。Phase 7 冻结发布候选，核对全量、升级回滚、真实平台与 72 小时常驻。

遵循仓库离线测试纪律。每批按受影响模块和调用方跑专项，保留现有网络和模型闸门，以临时目录与替身作测试。集成收尾安排一次全量并记录实际覆盖的 SHA。使用 docs/v/tasks.json 中的 OMNIWEAVE 条目，必要时新增该条目，按仓库命令再生状态页和索引。首批交付时给我候选分支、基线和测试 SHA、迁移前后头、专项结果、尚存阻塞及 Phase 2 的切入点。
```
