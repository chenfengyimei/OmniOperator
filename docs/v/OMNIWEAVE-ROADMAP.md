# OmniWeave（OWA）开发策划案

| 项目 | 当前口径 |
| --- | --- |
| 编制日期 | 2026-11-26 |
| 主线 | OmniOperator `v2` |
| 策划基线 | Gitee 公开提交 `702ff47ec6415ac136df54edebcb3c24df5f0a40` |
| 实施起点 | 主设备完成现有开发批次后，由所有者发送交接提示词。接手时以主设备工作树、最新 `origin/v2` 和现行合同为准。 |
| 配套材料 | [技术规格书 V1.0](architecture/OMNIWEAVE-ARCHITECTURE-SPEC-V1.0.md) · [主设备交接提示词](OMNIWEAVE-HANDOFF-PROMPT.md) |

## 1. 当前情况与实施窗口

| 已核对事项 | 对 OWA 的影响 |
| --- | --- |
| 规格书依据 `7372a00` 编制。之后 `v2` 已完成 QQ、B 站、桌面以及投递、终端、动作核查等功能登记。现有实例装配 21 项业务功能。 | OWA 接入已经成型的 `FeaturePlugin` 装配。调度、QQ 与动作核查提供内核回调及启动顺序的参考。原规格说明中的 QQ、B 站、桌面迁移顺序属于当时背景。 |
| 插件化验收归档于 `53d2159`，离线受测源码为 `1a2810ea`。8739 项通过，0 项失败，10 项跳过。其后公开主线继续提交跨平台 CI 与 Windows 桌面 worker 传输调整。 | Phase 0 把代码基线和测试证据分别记到对应 SHA。当前批次按实际影响范围补专项。 |
| `702ff47` 将 Windows 桌面请求交由固定 worker 接收，采用匿名管道。已检查的 Alembic 链头为 `v2_0053`。 | Body 阶段从现行平台端口和 worker 回执接入。实施迁移时重新读取迁移头并核对已登记的迁移，规格中的编号是当时示例。 |
| 正式候选、干净机、长期运行、账号与设备验收在公开台账中分别跟踪。主设备的未推送工作由接手者现场核对。 | 等所有者移交再占用开发分支。正式发布沿用已有候选和设备验收。Phase 0–3 使用隔离的离线工作区。 |

来源见 [公开 V2 快照](https://gitee.com/chenfengloveyuri/OmniOperator/tree/702ff47ec6415ac136df54edebcb3c24df5f0a40)、[插件化迁移验收](https://gitee.com/chenfengloveyuri/OmniOperator/blob/702ff47ec6415ac136df54edebcb3c24df5f0a40/docs/v/PLUGIN_MIGRATION.md)、[最新快照的状态台账](https://gitee.com/chenfengloveyuri/OmniOperator/blob/702ff47ec6415ac136df54edebcb3c24df5f0a40/docs/v/STATUS.md)和[Windows 传输变更](https://gitee.com/chenfengloveyuri/OmniOperator/commit/702ff47ec6415ac136df54edebcb3c24df5f0a40)。

## 2. 目标与优先级

**Phase 3 是首个可用里程碑。** 用户在一个私有 Web 主会话提出“开发校园助手、研究竞品、检查代码并整理文档”。Router 受理后让出执行位。Research、Coding、Verification 的运行时间有重叠。Coding 向 Research 请求接口资料，向 Verification 发布待核验版本。用户从原会话追加要求或取消任务。测试实例重启后，未消费的协作输入和同一个 Ticket 可以续接。必需的证据齐备后，Router 在原会话统一交付。

| 优先级 | 开发方向 | 可观察结果 |
| --- | --- | --- |
| 首要 | 单入口与会话解耦 | 用户只操作同一个 `session_id`。后台 Worker 运行期间，用户新指令能得到 Router 的实质处理。 |
| 首要 | 同组 Worker 双向通信 | 持久邮箱支持定向请求、关联回复和授权产物。每个 Worker 保持独立历史、角色与工作目录。 |
| 首要 | 可恢复的任务与原生审批 | Ticket 跨 Run 保存目标、依赖、等待和结果。V2 账本提供审批、动作、费用、投递与未知结果的事实。 |
| 后续 | Watchdog 与受管长作业 | 按外部操作回执、进度、租约与执行器是否已结束来控制恢复。 |
| 后续 | 数字身体和外部事件 | 先接现有本机能力，再以对应平台证据启用远端身体。外部事件从绑定及观察起步，自动准入限于明示规则。 |

单 Agent 空间内建立一个面向用户的主会话和多个内部 Worker Session。Router 管理计划、范围和交付。确定性的应用服务掌握队列、状态和授权检查。Worker 可在同一个根 Ticket 范围内点对点通信，范围变更交回 Router。模型输出、订阅和共享资料只作为输入数据。

## 3. V2 接入路线

| 接入范围 | 接手时优先确认的生产入口 | 预期改动 |
| --- | --- | --- |
| 用户入口 | `adapters/http.py` 中的 `POST /agents/{agent_id}/turns` 和事件流，`Application.submit_turn`，Workbench 主会话 | 复用输入去重及身份。主会话呈现任务、审批、状态和最终回复。Worker 详情来自脱敏投影。 |
| 持久运行 | `adapters/persistence/repository.py` 的 `accept_turn`、`finish`、`recover`、`reserve_call`，`lease_repository.py` 的领取链 | 在现有 `omni.db` 写事务内加入最小任务关系。Run 和 `work_items` 继续承载执行，Ticket 承载跨轮业务目标。 |
| 生命周期与工具 | `composition/features/catalog.py`、`composition/instance.py`、`application/capabilities.py`，调度和动作核查两项安装器 | 按 `FeaturePlugin` 组合。需要内核回调时参照 `KernelFeatureContext`。就绪前完成恢复，按角色捕获工具，利用同一生命周期关闭。 |
| 证据与权限 | 审批、动作核查、投递、现有模型账本与上下文快照 | Worker 共享授权引用。动作、投递或计费未知时依据原账本处理，最终任务状态引用核验结果。 |
| 设备与渠道 | 最新 desktop、terminal、Android、QQ、schedules 等业务登记及平台提供者 | 通过类型端口桥接，按物理资源维护租约。同一事件和调度出现次数有唯一的执行归属。 |

路径以 `agent/src/omni/` 为根。定位入口时用当前检出的 `scripts/project_index.py --feature`。V2 保持 domain、application、adapters、composition 四层，HTTP 调用应用用例，SQL 留在持久化层。 [最新功能架构合同](https://gitee.com/chenfengloveyuri/OmniOperator/blob/702ff47ec6415ac136df54edebcb3c24df5f0a40/docs/v/reference/CAPABILITY_PLUGINS.md)

## 4. 主设备首批交付

### Phase 0 基线核对

1. 读取 `AGENTS.md`、`docs/v/HANDOFF.md`、`docs/v/reference/ARCHITECTURE.md`、`CONTRIBUTING.md` 及任务台账。对照 `git status --short --branch`、本机 `HEAD` 和最新 `origin/v2`。记录其他会话涉及的文件和本轮基线，从最新可用 V2 建短名任务分支，如 `owa-tickets`。
2. 对照本案第 3 节，用现有功能索引定位运行内核、调度、审批核查与上下文。确认入站去重、发布回复、队列领取、取消、恢复、预算和功能登记的实际接缝。确认候选证据的被测 SHA 与测试边界。
3. 读取实际迁移头。保存空库和旧 V2 测试副本的验证方法。旧快照若含未决审批或未知结果，记录原审批和账本的预期状态。登记本批的入口快照、验证命令、冲突及当前阶段。

Phase 0 产出一条可复核的基线记录和首批影响范围。无共享接缝冲突后进入 Phase 1。若在途修改正占用同一组装入口或迁移头，先做独立核对与测试，待该接缝可合入再写入。

### Phase 1 入站、任务与观察模式

| 工序 | 当批交付 | 验收 |
| --- | --- | --- |
| 1. 定义合同 | 最小 `Trigger`、`Thread`、`Ticket` 与授权来源。在内部用例里验证创建任务，客户端沿用原请求字段。明确事件版本、来源键与请求指纹。 | 相同键同载荷返回原回执，相同键异载荷给出冲突。跨 Agent 或主会话的引用被拒绝。 |
| 2. 一条生产链路 | 以私有 Web `/turns` 为首条链路。`observe` 同事务登记所需的规范事件和观察结果，业务继续走原 V2 回合。`off` 是默认模式。 | 实际 HTTP 的 `202` 与 `run_id`、`session_id`、`duplicate` 合同通过。`off` 的 OWA 事件数、`observe` 的 Worker 启动数和重复派发数均为 0。 |
| 3. 持久化与恢复验证 | 按现行 Alembic 头添加最小关系，测试原消息、去重回执与观察记录的提交边界。仅供未来执行的关系随实际调用面逐批加入。 | 空库与旧 V2 测试库升级成功。`integrity_check` 为 `ok`，`foreign_key_check` 为 0 项。提交中断与并发重投无孤儿或额外 Run。 |
| 4. 合入证据 | 记录基线 SHA、迁移前后头、影响入口、专项通过项、控制模式和开关回退。按当前分支台账登记 `OMNIWEAVE`。新增文件后更新索引。 | 能从记录重跑验证。合入前可用 `off` 验证现有入口。Phase 2 以本批的工作队列和身份绑定为起点。 |

首批选取入站、事务与兼容性回归。Phase 1 支持按内部用例验证 Ticket 状态，用户面观察和现有执行回执使用原入口。阶段通过后再投入 Router 提示词、Worker Profile 与界面任务卡。

## 5. Phase 2–7 排期

阶段依赖为 `0 → 1 → 2 → 3 → 4`。Phase 5 和 Phase 6 按平台与账号资源协调，可在 Phase 4 后分别推进。Phase 7 冻结拟发布范围，对实际启用的能力逐项取证。T01–T13 定义见 [规格书验收矩阵](architecture/OMNIWEAVE-ARCHITECTURE-SPEC-V1.0.md#19-测试评估与性能基准)。

| 阶段 | 实施方法 | 出阶段证据 |
| --- | --- | --- |
| **2 Router 与 Worker** | 先以固定计划和离线模型证明同一 dispatcher 可运行内部 Worker，再接 Router 的结构化计划。运行核对角色、授权和租约。为控制回合预留调度能力。用根 Ticket 对应最终会话。 | T01、T02、T04、T09。三个受闸门阻塞的 Worker 同时活动。第四条主会话消息得到实际处理，验收同时观察 HTTP 受理和 Router 应答。旧 `delegate_task` 专项正常。 |
| **3 邮箱与任务续接** | 定向请求和回复、对象版本、订阅与回放先接底层，再串联到主会话和任务详情。目标修订使被影响的旧产物退出当前验收。 | T03、T05、T06、T10、T11。Coding 能拿到两类 Worker 的证据。重复、早到、晚到或跨作用域请求均有确定处理。重启续接和主会话统一交付通过。 |
| **4 后台与恢复** | 现有 V2 审批、撤权、未知结果约束随首次可执行 Worker 生效。本阶段补有界长作业、Watchdog、清理与等待视图，验证派发前后故障和停机顺序。 | T06、T08、T09、T12。无重复副作用、超额派发或提前释放。失去回执的运行按原账本进入可核查状态。 |
| **5 数字身体** | 用 Windows 桌面、终端、Browser、Android 的本机能力证明端口和互斥。Mac mini 在自己的环境验证身份、TCC 和端点。远端配对按已验证资源推进。 | T07、T08、T12。互斥、断线、重连与清理有回执。断连后仅在旧资源已确认安全、授权与观察有效时恢复。 |
| **6 外部事件** | 在已登记的 QQ 和调度服务上增加授权绑定与观察。选定来源和账号完成核对后，试跑允许的只读自动规则。B 站或其他渠道有明确需求时用同一来源合同接入。 | T05、T09、T10。来源重投、跨渠道补充和撤权的结果正确。同一次计划调度只派发一次。 |
| **7 集成与发布** | 固定候选，按实际启用的来源和数字身体整理支持清单。依次完成离线全量、配对性能、包生命周期、备份与回滚、适用的真实平台及 72 小时常驻。 | T01–T13 的被测提交、平台、产物与证明可复核。未有真实环境证据的平台出现在待验清单中。 |

## 6. 重点实测与风险控制

| 风险 | 优先落实的检查 |
| --- | --- |
| 主会话卡在后台执行 | 用可控的 Worker 阻塞闸门保证三个活动窗确实重叠，同时断言 Router 实际应答及队列公平性。阻塞或审批中的执行器继续占用其归属，恢复按可信回执决定。 |
| Worker 相互等待或重复唤醒 | 阻塞请求和依赖图在同一事务检查成环。邮箱使用事件和收件者去重，消费有持久确认。请求有期限和关联 ID。 |
| 消息已投递但进程崩溃 | 以 `action_id`、投递记录及现有模型账本裁决结果。派发后回执未知时挂起相应任务和资源。仅对有证据的可重试操作续接。 |
| 目标版本变化引入陈旧结果或泄露 | 事件与证据绑定目标版本、发送者、接收者和引用范围。目标修订后重验审批和读权限，旧结果保留原证据归属。 |
| 涉及同一迁移或上游组装 | 重新比对 `origin/v2`、本机分支和待合入差异。同批迁移、协议和依赖锁由一个集成会话维护。功能停用保留已受理任务的核查入口。 |

阶段专项按生产入口与调用方选取，默认使用离线替身和临时数据目录。涉及首批接入时，可从 `test_v2_architecture.py`、`test_v2_feature_plugins.py`、`test_v2_workbench.py`、`test_v2_event_stream.py` 及 `test_v2_schema_release.py` 选择实际受影响的用例。调度、委派、动作核查在对应行为变化时扩展回归。每批记录命令、退出结果、被测 SHA 和覆盖范围。 [最新测试纪律](https://gitee.com/chenfengloveyuri/OmniOperator/blob/702ff47ec6415ac136df54edebcb3c24df5f0a40/docs/v/reference/TESTING.md)

## 7. 移交和完成判定

- **副设备交付**是本策划案、技术规格和自包含交接提示词。所有者在主设备当前批次结束后发送提示词，三份资料可随同传递。规格书记录原设计证据，接手时的源码和合同决定实际接缝。
- **主设备首批交付**是 Phase 0 的基线记录和 Phase 1 的小闭环。开发台账使用 `OMNIWEAVE` 行登记进度，依仓库命令再生状态页。每个后续批次记录实际状态、专项、阻塞及下一步。
- **MVP 交付**以 Phase 3 的可复现单会话演示和协作恢复回执为准。**V1.0 发布**以 Phase 7 的冻结候选证据及实际支持矩阵为准。
