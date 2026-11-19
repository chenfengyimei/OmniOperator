# V2 模型批次工具并行（K05）

## 结论与范围

本合同对应 [Agent 内核对标](V2_AGENT_CORE_PARITY.md) 的 K05。V2 此前对同一模型回包内的多个工具调用一律逐个 `await` 顺序执行；本批在共享内核内加入**批次分段并行**：相互独立的只读能力调用可并发执行，交互控制、写动作与冲突资源保持串行。参考 `xuexi/hermes-agent` 固定提交 `9976f4857b72439d79016354d7b6e1a3ee952e2f` 的 `agent/tool_executor.py` 与 `agent/tool_dispatch_helpers.py`，学习其分段规划、按序汇合与取消持有行为，在 V2 四层内独立实现，未复制第三方源码。

K04 的子任务批内并行是父子 run 委派，不能代替本项的工具冲突分类、资源归属与按序汇合。本批不新增第二内核、状态库或数据库迁移。

## 分类与分段合同

1. 分段规划是纯领域规则：`domain/tool_batch.py` 的 `plan_tool_batch` 把一个批次切成有序的 `("parallel" | "serial", 索引)` 段，**严格保持原调用顺序**——后面的调用绝不跨越前面的屏障，结果顺序与副作用边界与完全顺序执行一致。
2. 一个调用可并行，当且仅当它是**本回合档位已提供（batch_registry）的只读能力**（`ToolSpec.writes == False`）。以下一律串行屏障：
   - 内部控制 `request_mode`、`todo_list`、`delegate_task`（改变 run 状态/档位/计划，逐项内联处理）；
   - 所有写动作——写动作独占 run 的审批状态机（`prepare_action` 转 `waiting_approval`、`resolve_approval` 要求该状态、`authorize_action` 要求 `running`），同一 run 不能同时存在两个待审写动作；
   - 档位外（`tool_outside_mode`）与未知能力（内联返回 DENIED/ERROR，不派发）。
3. 冲突键为能力的有效资源 `ToolSpec.resource or name`。一个并行段只接纳**两两不同**的冲突键；出现冲突即关闭当前段，该调用在其后开启新段。同资源调用即便都是只读也串行——引擎的资源锁与 worker 的单飞 IPC 本就独占该资源域，声明并行只会增加任务开销而无收益。
4. 长度不足 2 的并行段降级为串行；相邻串行段合并。空批次不产生段。

## 执行与生命周期合同

1. 并行段经 `Application._execute_parallel` 用 `asyncio.gather(..., return_exceptions=True)` 并发派发，每个调用仍走原 `_execute`：schema 校验、`prepare_action`、资源锁、`authorize_action`、观察 span、`InvocationContext`、逐项 `timeout_s`、`settle_action`。**逐项观察/审批/预算/生命周期与顺序执行完全一致**，不新增调用上限（批次仍受 K02 的 32 调用/262144 字节约束）。
2. 结果**按原调用顺序**写回 tool 消息：即便后一个调用先完成，下一轮模型输入仍按调用顺序排列（回归以模型第二轮实际输入断言）。
3. 段内某个调用失败（如 `approval_expired`/`policy_changed`）或返回 `Effect.UNKNOWN` 时，**已派发的兄弟调用照常结算**，不中途释放；按序写入已结算结果后，再按调用顺序抛出第一个异常或 `action_outcome_unknown`。未知效果不重放、不自动重试。
4. 取消持有：run 被取消时，`gather` 取消整组并**等待每个子任务经自身 `_execute` 结算后**才向上抛 `CancelledError`；写动作结算为 unknown、只读结算为 `action_interrupted`，随后走原 `_cancel_join_children`/`_end_error`/`close_run_actions`。实际清理完成前不释放归属，不丢弃仍执行的任务。
5. 串行段保持原有逐调用 `_checkpoint` 与内联分派（`_dispatch_call`），行为与本批前一致。

## 验收

- 独立只读调用真实并发（互相等待对方启动的屏障在顺序执行下会超时），结果按调用顺序汇合。
- 同资源只读调用串行；写动作之间、写与读之间以审批/顺序屏障串行；两个写动作绝不共享审批窗口。
- 取消并行批次时每个已启动动作都结算、无残留 executing、调用计数各 1、实例可正常关闭。
- 并行段内 unknown 使 run 失败，但兄弟动作以真实结果结算、不重放。
- 每个并行动作保留独立 action 记录与审计/事件，无审批泄漏。

## 限制

- 仅只读能力可并行；写动作并行需要改造 run 审批状态机，不在本项范围。
- 冲突分类基于显式资源键，不做 Hermes 的文件路径重叠/破坏性命令启发式；V2 的资源锁与 worker 单飞 IPC 是运行时真值。
- 无批次级总超时（沿用逐项 `timeout_s` 与 run 取消）；无新增并行管理界面。
- 未跑当前源码全量；未做真实模型/设备/原生与 72 小时验收。

## 证据

见 [批次并行证据](../../evidence/v2-tool-batch.json)。