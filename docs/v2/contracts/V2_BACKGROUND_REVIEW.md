# V2 回合后学习 reviewer/curator（K08）

状态：已实现并通过专项回归（development_working_tree_scoped）。参考 `xuexi/hermes-agent/agent/background_review.py`（commit `9976f4857b72439d79016354d7b6e1a3ee952e2f`），按 V2 四层架构独立改编，不复制第三方源码、不远程 fetch、不用真实账号/模型/设备。

## 目标与验收映射

一个回合（run）成功结束后，后台评审回放该回合转录，在独立有界预算内调用模型判断是否值得保存/更新记忆或技能，产出**待审建议**进入现有审核与撤回链；来源引用本身不构成授权。

| 验收 | 实现 |
| --- | --- |
| 后台工作项生产接线、启动/关闭/取消归属和独立预算 | `BackgroundReviewService` 由 `MaintenanceService.tick` 驱动 `process_one`；`lifetime.own` 关停；`background_review_cancel` 与批次 `running` 守卫取消；独立 `MAX_CALLS=8` 调用上限 + 批次 `budget_micro` 记账 + `reserve_call` 日预算闸门 |
| 只读/记忆/技能白名单及证据锚点 | `classify_tool`：`READ_TOOLS`（memory_search/skills_list/skill_view/skill_history）派发只读；`WRITE_TOOLS`（memory_remember/forget、skill_create/rewrite/patch/rollback/archive/restore）拦截为待审建议；其余拒绝。记忆建议强制 content+quote，apply 时经 `remember` 校验 quote 命中活来源消息并记 `source_run_id` |
| 建议进入现有审核与撤回链，不把来源变授权 | 候选 `pending→applied/rejected/revoked`，所有者 `decide` 绑定 revision/fingerprint/epoch；记忆 apply 走 `remember`、undo 走 `forget`；技能 apply 走 `SkillService.save/patch/rollback/state`（新修订需所有者审核才启用）、undo 走 `state` archived |
| 无收益可不写，幂等触发不重复付费 | 模型回复 "Nothing to save." → `candidate_count=0`、批次 complete；`UNIQUE(agent_id,run_id)` + `request_key=review:<run_id>` 保证每 run 一个批次，重复 trigger/submit 返回既有批次不新建、不重复评审付费 |

## 四层接线

- **domain** `domain/background_review.py`：纯规则。`POLICY=post-turn-review-v1`、`PURPOSE=review`、`MAX_CALLS=8`、`MAX_PROPOSALS=16`、`READ_TOOLS`/`WRITE_TOOLS`、冻结 `REVIEW_PROMPT`；`review_key`/`parse_request`/`request_fingerprint`（幂等身份）、`classify_tool`、`parse_proposal`（有界建议归一化）、`proposal_fingerprint`、`parse_decision`、`page`。无 I/O、无模型调用。
- **application** `application/background_review.py`：`BackgroundReviewService` 有界并发壳（pending≤8、Semaphore(2)、shield）。`trigger`（引擎回合结束非阻塞幂等钩子）、`submit/page/get/cancel/decide`（所有者）、`process_one`（claim→reserve→invoke→settle 多事务评审循环：只读派发、写拦截、其余拒绝；失败/取消落库结算）。`engine.py` 在两处 `finish`（正常收尾与 grace 收尾）后对**非委派、非远程** run 调 `_trigger_review`；`maintenance.py` tick 在 memory_organization 之后驱动 `background_review.process_one`。
- **adapters** `adapters/persistence/background_review_repository.py`：`BackgroundReviewRepositories(MemoryRepositories)` 混入 `Repositories`。幂等入队（共享 maintenance 队列计数 `MAX_PENDING`）、排他 claim（queued→running，非 queued 返回 None）、record_call、stage、fail、cancel、get/page、`background_review_candidate`（技能 decide 前置 CAS）、`decide_memory`（remember/forget）、`decide_skill`（CAS+应用标识）。`schema.py` 新增 `background_review_batches`/`background_review_candidates`；`migrations/versions/v0043.py` 冻结 SQL 建表 + 不可变身份/单向状态机/禁删触发器。`adapters/http.py` 新增 5 个所有者路由（`/memory/reviews` submit/page/get/cancel/decision），router 级所有者鉴权。
- **composition** `composition/instance.py`：构造 `BackgroundReviewService`（注入 calls、只读 ToolSpec 与 `review_read` 派发闭包、skills），注入 maintenance 与引擎（**仅 `capabilities is None` 生产路径启用引擎触发**，注入 capabilities 的隔离测试不触发，避免额外评审调用破坏既有调用计数/费用断言）；`Instance` 字段/health/ready 门/`composition/http.py` 运行时同步。

## 预算与模型通道

评审复用辅助模型通道：`reserve_call(purpose="summary")` + `invoke(reserved_id=...)`（与 `compact_run` 同款 reserve→invoke→settle，失败结算不重放）。**独立预算**由批次 `MAX_CALLS` 上限、`call_count`/`budget_micro` 记账与日预算闸门共同约束，独立于回合 `max_steps`。未新增 `model_calls.purpose` 取值（避免重建核心表）；评审花费经批次 `budget_micro` 与 `call_count` 独立可见。路由触发器要求 `purpose='turn'`，评审不传 `baseline_tools` 故避开。

## 取消、关闭与失败合同

- 所有者 `cancel` 将 queued/running/ready 批次转 cancelled，pending 候选转 cancelled；评审循环每轮检查批次状态，非 running 即退出。
- 排他 claim 防止 maintenance 与手动 `process_one` 重复评审同一批次。
- `process_one` 捕获 `BusinessError`→`background_review_fail`、`CancelledError`→`background_review_interrupted` 并重新抛出；`close` 等待 pending 任务。
- 评审失败不影响已成功的回合；unknown 不重放。

## 限额与非目标

- 深度/嵌套：评审为单层后台批次，不产生子 run。
- 建议上限 16/批；调用上限 8/批；quote≤2000 字符、content≤20000 字符。
- 文本启发式评审提示为保守冻结指令，非参考完整周期锚点/路由变体集。
- 不实现参考的 prompt-cache parity fork、aux 模型路由、多迭代 skill_manage 读前写守卫全集；技能读前写由现有 `SkillService` 链负责。
- 不新增第二内核或状态库；复用同库 `omni.db`、审计、来源锚点与维护队列。
- 数据库迁移：v0043（新增两表，不改 `model_calls`）。

## 验证

- 新增 `test_v2_background_review.py`（8 域规则 + 8 集成：待审建议带锚点计费、所有者 apply 走证据链/undo 撤回、reject 不写、无收益不写、幂等不重复付费、白名单拒绝、取消不付费、生产路径引擎触发入队）与 `test_v2_background_review_migration.py`（2：head schema 列/FK/触发器一致、幂等入队与证据不可变）。
- 受影响调用方专项：K05 工具批次、K06 run 压缩、K07 迭代守卫、维护与记忆整理迁移、四层架构守卫均绿。
- 严格类型 `pyrightconfig.strict.json` 399 文件 0 错 0 警（新增 domain/application/repository 三文件，396→399）。
- 既有 6 个 pin 旧版本（v2_0032/v2_0033/v2_0034/v2_0036/v2_0039）的升级迁移测试在 HEAD 之前即滞后（head 早已超过所 pin 版本），与 K08 无关；head 升级类迁移测试计数断言已同步 136→140。
- 未跑全量；未触真实账号/模型/设备/原生桌面/72h；未重启当前 Windows 服务或升级其数据库。