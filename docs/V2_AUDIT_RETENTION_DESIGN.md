# V2 180 天审计与运行历史保留实施设计

状态：R3.retention_audit.events 已开发 180 天事件明细预览、计划、原子清理、取消与恢复隔离，结构为 78 表/v2_0016，专项通过、全仓待复验。审计与事件共用执行核心，当前源码全仓待复验。本文件区分当前公共合同与后续设计；批次结果以 [当前交接](HANDOFF.md) 和 [任务台账](tasks/v2.json) 为准。整个 V2 未完成，默认入口仍为 V1。

## 已核实的数据边界

当前没有独立的“运行摘要正文”模型。`runs` 保存业务身份、输入消息、终态、配置/权限版本、运行内事件序号和裁剪证据；`summaries` 保存会话压缩后的语义正文，仍会被后续模型上下文选择。180 天运行历史保留不能等同于删除这两张表。

当前已建立三类历史游标、Agent 审计/事件有界预览及独占离线计划/执行链；在线预览仍不能执行删除。运行归档若接入，只改变默认历史可见性，不删除运行身份，不承诺物理空间已经回收。运行事件明细使用独立政策、计划和证据放行。

| 数据 | 本批规则与原因 |
| --- | --- |
| `runs`、消息、入站去重 | 保留。聊天上下文通过运行连接原消息；删除运行会让仍保留的聊天静默退出上下文，并破坏防重放身份 |
| `summaries`、父摘要、摘要来源及上下文引用 | 保留。最新可选摘要、父链、building/unknown 和已使用快照均是业务状态 |
| 上下文快照及其来源关系 | 保留。遗忘传播依赖这些关系；投递执行前也要核验最新快照的来源代次 |
| 动作、审批、费用、预算、调度、投递及资源账本 | 保留身份与状态。TTL 不结算未知结果，不释放费用，不授权重发 |
| 正文及引用维护批次和目标 | 保留。不可变历史与恢复依据继续引用运行和动作，不能作为普通日志删除 |
| `audit` | 仅考虑明确种类/主体注册表内、至少 180 天且无保护的记录；非白名单一律保留 |
| `instance_audit` | 首批排除。凭据结算读取 requested 行，并以 resolved 行判定幂等；没有入向 FK 不代表无人使用 |
| `run_events` | 独立候选：至少 180 天、事件/运行正文均已裁剪、原批次完成且无业务保护；精确种类与动作关系核验，保留 `runs.last_seq`，不按剩余事件重新计算 |

以下源码是当前依赖依据：`adapters/persistence/context_repository.py` 的历史组合，`retention_repository.py` 的遗忘依赖，`delivery_repository.py` 的投递来源核验，以及 `credential_repository.py` 的凭据审计结算。它们不是可按年龄丢弃的缓存。

## 已实现的持久游标基础

V2 的历史查询使用全局 `run_events.sequence`；运行内部另外维护 `runs.last_seq` 与事件 `seq`，二者不能互换。普通审计和事件初始 DDL 没有 SQLite `AUTOINCREMENT`，仅有 ORM 的自增声明；v2_0014 已以独立持久分配器解决删除尾部后的序号重用边界。V2 当前没有持久 WS/SSE 事件接口，不能把 V1 的实时通道记作已有 V2 恢复能力。

当前使用版本化不透明游标，绑定历史 epoch、Agent 或实例、流、运行筛选、清理修订与最后位置。初次不传 cursor，后续原样传 next_cursor；游标不固定跨页查询的上界，新提交记录可在下一页出现。不同 Agent、流或运行筛选混用返回 422 `history_scope_mismatch`；非法或未来位置返回 422。恢复生成新的历史 epoch，并与业务隔离同事务提交；普通重启保持 epoch。

`history_scopes(kind, scope_id)` 保存清理修订、累计删除数及最后 UTC 清理时间。三类流的 DELETE 触发器在删除原事务中递增相应作用域，不依赖调用方手动更新；删除与事务回滚共同生效或撤销。事件和普通审计按整个 Agent 建立作用域，实例审计按实例建立；删除其他运行的事件也会使该 Agent 原有事件游标保守失效。触发器只追踪变化，不授权清理。

历史 epoch 或清理修订改变时返回 HTTP 410 `history_cursor_expired`；工作台两类审计清空旧游标和旧记录，明确提示并等待用户重新加载。受保护的老记录可能留下中间缺口，因此不能只检查当前最小序号，或只保留一个最低可用位置。重新查询仍允许读取稀疏保留行，`history_trimmed` 明确说明发生过历史清理。

`history_identity` 保存单例 epoch；独立的 `history_sequences(kind, last_issued)` 为 events/audit 分配全局序号。普通审计与事件在原业务事务中先原子递增并取回序号，再显式写入；索引重建及恢复隔离的直接 SQL 写入已共用这一分配器。升级初次从现存最大序号初始化，之后不从剩余行重算；事务回滚不留下业务成功。实例审计继续使用已有的 SQLite AUTOINCREMENT。分配高水位与作用域进度禁止倒退和删除，新 Agent/实例自动获得作用域行。

专用历史查询门面统一返回 `items/next_cursor/has_more/stream/history_trimmed`；stream 提供 `high_water/trim_revision/deleted_count/last_trim_at`。每页默认 100 条、范围 1–200 条，同一读事务核对作用域水位与数据，拒绝未来位置。实例审计虽已接入分页及删除跟踪，仍不属于首批可删除集合。

`high_water` 是该表的全局分配位置，不是当前 Agent 的记录数量。当前失效合同覆盖删行和恢复；30 天正文裁剪对既有事件载荷的原地更新不增加删除数、不触发此类过期。若后续需要让已读取载荷自动同步裁剪状态，应单独设计内容修订，不能把更新伪记为删除。

已接入的 HTTP 路径为 `/api/v1/agents/{agent_id}/audit`、`/api/v1/agents/{agent_id}/runs/{run_id}/events` 和 `/api/v1/system/audit`，均使用 cursor/limit。这三个接口出现旧 after 参数即返回 422 `invalid_history_query`，不提供整数分页转发。工作台 Agent/实例审计均支持加载更多、最多保留最近读取的 200 条、过期清空和迟到响应隔离；未知审计种类使用通用标签，不全当作“保存凭据”。工作台当前不消费持久事件流；未来 WS 应复用同一分页用例，实时模型片段不推进持久游标。

## 已实现：审计候选与晚到保护

采用固定的 `kind → subject` 注册表，明确每个允许种类的主体是运行还是动作，以及如何获取所属运行。`operation_id` 目前还可能指会话、人物、文档、配置、计划和核查，不能一概当作运行 ID，不能按字符串前缀猜测。

首版候选白名单限于 `run.completed`（主体为 succeeded 运行）与 `action.ok/action.error/action.denied/action.cancelled`（主体为动作，再查询所属运行；当前动作状态须与种类一致）；每项仍要满足下述保护条件。缺失、跨 Agent 或状态不符的主体均保护，不读取 data 中的身份进行补救。`action.unknown`、审批、取消请求、恢复核查、运行固定及其他未知种类均保留。预览资格须经过独立计划、备份及提交前重查才能执行。

已核实的易错映射包括：`approval.approved/denied` 指动作而非审批；`settings.changed` 指设置修订；`context.prepared` 指运行，但 `context.invalidated` 指会话；`schedule.saved/archived` 指计划，而 `schedule.due/enqueued/continuation_reviewed/occurrence_cancel_requested` 指到期实例。对象回收和账号解绑记录的主体可能已经删除，不能据主体缺失自行放行。直接 SQL 的索引重建、恢复隔离，以及全部独立实例凭据审计继续保留。

候选查询按页内运行去重复用共享业务保护，不计算正文指纹，不查询 audit.data、动作参数/结果或运行事件正文。30 天正文与引用维护继续使用原有指纹合同；审计用 180 天判断运行年龄，并将正文已裁剪作为前置。运行必须有与 body_sha256、pruned_at、Agent、运行和原计划一致的 pruned 目标，且原批次 complete；动作的裁剪时间须与运行一致。prepared 或 cancelled 的部分裁剪批次仍保护。

记忆人工来源和文档作者保护按本页 `audit.id + agent_id` 批量查询实际外键，不按业务删除标志猜测关系是否还在。主动遗忘当前会删除来源锚点；锚点存在时保护，实际释放后不虚构永久引用。文档擦除后仍在的作者外键继续保护。prepared 引用释放批次内的 pending 运行目标也保护，取消或完成后重新评估。

HTTP 为 `GET /api/v1/agents/{agent_id}/audit/retention-preview`，要求所有者认证。首次可指定带时区且不晚于当前时间的 as_of，默认捕获一次当前时间；默认 20 条、上限 50。按序扫描该 Agent 至少 180 天的审计元数据，非白名单行也返回明确保护原因；全保护页仍能继续分页。响应为 `items/counts/next_cursor/has_more/as_of/cutoff/stream/history_trimmed/can_apply`，counts 只计本页，can_apply 始终 false，没有可执行计划或审计正文。

独立 `omni-audit-retention-preview-1` 游标固定政策、as_of、初始全局分配上界、Agent、epoch、清理修订及位置。新增记录包括晚到的回填记录不能进入原分页截面；运行保护仍按每页当前读事务重新评估，这不是完整业务状态快照。普通历史游标不能混用；显式 as_of 与游标不符或跨 Agent 为 422，清理/恢复为 410；时钟回拨至 as_of 之前拒绝查询，须刷新。工作台“诊断日志”提供独立预览区，显式读取、更多与刷新，最多显示 200 条；410 清空，迟到响应不能覆盖另一个 Agent。

离线入口 `python -m omni.composition.cli --home <独立V2目录> retention-audit-preview --agent <Agent UUID> [--limit 20] [--cursor <原样游标>] [--as-of <时间>]` 取得实例独占锁，只读当前结构，不启动业务或自动迁移；在线实例拒绝。预览不能传给现有正文/引用 apply。

计划前及每个目标提交前都核验：

1. 审计记录与所属运行均达到固定 UTC 时点的 180 天边界；时间只捕获一次，未来或不带时区的计划无效。
2. 原正文已提交裁剪且批次完成；运行未固定，未被其他待处理维护批次占用。
3. 复用运行保留的来源、文档、审批、动作、费用、工作、调度/投递、租约和产物保护；unknown 即使人工允许后续调度，仍不能按普通期限清理。
4. `source_anchors(manual_audit_id, agent_id)` 和 `document_revisions(authored_audit_id, agent_id)` 的真实外键不存在。新增 FK 或逻辑引用也须纳入保护，不能靠捕获约束异常代替候选判断。
5. 未识别种类、主体不明、缺历史依据或存在尚未核清的配置/凭据操作时保留。新来源和费用状态即使不改变原审计指纹，也能使计划失效。

上下文摘要的未来清理另设合同：必须同时排除最新可选 ready 摘要、父链和任何上下文引用，以及 building/unknown。不得为了满足“180 天”数字把仍生效的语义状态归零。

## 已开发：180 天维护执行与恢复

复用 `offline_plans` 的实例独占锁、只接受安装包当前结构、私密且不可覆盖的有界计划、SQLite/对象一致备份、完整备份核验、逐目标短事务、续跑和显式取消。

每个目标固定完整审计 UUID、序号、Agent、种类、主体、指纹及清理修订。删除、不可变目标进度、流清理修订和本次维护审计共同提交；历史目标不建立回到被删除审计/事件的 FK。清理产生的维护审计不得成为本次候选。逐目标重新核验所有保护，不只比较原载荷。

取消仅撤销尚未提交目标，不重建已删除行。恢复使用绑定备份进入新目录，保留业务防重放与费用隔离，并使旧实例游标失效。不得用当前维护构建的 build_id/boot_id 回填缺少历史身份的老运行，伪造其执行版本。

v2_0015 新建 `audit_retention_batches/targets`。计划固定实例、Agent、结构、政策、创建时间、as_of/180天截止、历史 epoch、清理修订、初始分配上界；1–50 个目标按原全局序号升序，保存审计 UUID/种类/主体/运行、完整审计指纹、运行保留 revision 和原裁剪计划/指纹，不包含审计正文。文件上限 64 KiB，重复 JSON 字段拒绝，不能覆盖输出或放在实例目录内。每个 Agent 同时最多一个 prepared 审计批次，每条审计最多一个 pending 目标；其他 Agent 独立。

目标历史不建立到 audit/actions 的活外键，运行及原裁剪批次继续通过真实外键保留。批次与目标身份不可改写或删除；pending 只能转 deleted/cancelled，prepared 只能转 complete/cancelled。执行前检查所有目标，生成并验证 SQLite/对象一致备份，再在备份中重新检查原目标均存在、原水位与全部保护符合；未绑定的孤儿备份不复用。

每条目标在单独短事务重查当前保护、作者外键、完整审计指纹及裁剪依据后精确 DELETE，更新不可变进度并写 audit_retention.deleted 审计；v14 触发器自动推进水位，调用方不重复加数。活动批次要求当前修订恰等于原修订加本批已提交 deleted 数，其他清理导致冲突。仅有匹配持久 deleted 凭证且原 ID 没有重现时允许幂等跳过；记录意外缺失不算成功。complete 回执可在后续合法清理后重复读取，仍绑定原计划/实例/结构/epoch与备份，不重复删行或写审计。

取消核验原计划与持久批次，不依赖当前目标资格、备份可用性或当前时钟晚于计划；已有 deleted 记录保持，响应 audit_rows_restored=false。新目录恢复在更换 epoch 的同一事务取消 prepared 审计批次及 pending 目标，保留已删除证据；旧计划不能在恢复后的实例再次执行。恢复报告包含隔离标志、epoch和取消计数，重复恢复核验标志、目标文件指纹和当前 epoch。普通进程重启保留可续跑进度。

独占离线入口：

```text
retention-audit-plan <实例外新计划.json> --agent <Agent UUID> [--cursor <预览游标>] [--limit 20] [--as-of <UTC时点>]
retention-audit-apply <原计划.json> --backup <实例外新备份目录>
retention-audit-cancel <原计划.json> --reason <取消依据>
```

均使用 `uv run --no-sync python -m omni.composition.cli --home <独立V2目录>` 前缀；apply 续跑必须使用同一计划和原备份路径。doctor 对 prepared 批次报告需要关注，包括全部目标已删但 complete 前中断的批次。运行指标、工作台和默认诊断只展示状态计数，不泄露备份路径、计划或取消正文。

## 已开发：事件明细清理与共同执行核心

`history_pruning_cycle.py` 统一审计与事件的计划身份、原备份绑定、逐目标进度、水位、删除/维护审计事务、完整回执与取消；领域政策分别提供严格计划校验与当前目标检查。`history_protection.py` 统一原正文批次、来源与维护保护，引用释放也会保护被 pending 事件计划占用的运行。两个流保留独立 ledger、政策、游标和错误码，不把整数事件序号伪装为审计 UUID。未扩展旧审计格式，也不改写已封存迁移。

v2_0016 新增 `event_retention_batches/targets`。批次字段与审计同形但政策为 agent-event-retention-v1；目标以 `(batch_id, sequence)` 为主键，并固定 Agent、运行、本地 seq、种类、运行保留版本、原正文计划/指纹及完整事件行指纹。没有到 run_events 的活外键，目标不可改写/删除，同一全局 sequence 只有一个 pending 目标。同 Agent 的审计与事件 prepared 批次互斥；流不同但计划 UUID 相同也不能错误放行。

首版只允许九种事件：run.queued/running/waiting_approval/cancelling/succeeded/failed/cancelled 和 action.prepared/completed。恢复用 run.interrupted 及未知种类保守保留，不按前缀自动扩展。每条事件必须与运行具有相同、非空的正文裁剪时间和有效原载荷指纹；裁剪后追加的未裁剪事件保护。动作事件从裁剪保留的 action_id 核验同 Agent/运行的实际动作；completed 的状态及副作用须与当前动作一致，unknown 仍保护。旧 queued/running 等历史进度不要求等于当前终态；终态事件须与运行状态一致。

元数据预览只投影身份、时间及裁剪后保留的少量关联字段，不向界面返回 data、动作参数或结果。独立事件游标固定 events 的 as_of/分配上界/清理修订和 Agent；默认20、上限50，工作台最多200条。`GET /api/v1/agents/{agent_id}/events/retention-preview` 要求所有者认证，拒绝旧 after；错作用域422，删除/恢复过期410。界面显式读取、更多和刷新，分别显示全局 sequence 与运行内 seq，并隔离迟到响应。

离线命令前缀同上，子命令为 retention-events-preview、retention-events-plan、retention-events-apply、retention-events-cancel，参数与审计相同。plan 为 omni-event-retention-plan-1，1–50 个目标、64 KiB 上限。删除精确匹配 sequence/Agent/run_id/seq/kind；只推进 events 清理修订，维护审计写 audit 流，不推进 audit 删除修订。删空后追加事件继续使用原 runs.last_seq+1 和新的全局分配位置，绝不重编号。

原正文指纹与引用释放计划包含当时事件集合；删事件会使尚未执行的旧引用计划失效，需重新审查新计划。prepared 引用/事件维护互相保护，不为迎合旧计划修改原 body_sha256 或裁剪证据。恢复同事务更换 epoch、取消事件 pending/prepared，保留 deleted 证明，恢复报告及快速重复恢复核验 event_retention_quarantined。普通重启保留可续跑进度。doctor 的 event_retention_attention 与工作台仅显示批次状态计数。

## 实施顺序与放行条件

| 小批次 | 工作 | 放行条件 |
| --- | --- | --- |
| R3.retention_audit.cursors | 已实现持久分配高水位、历史 epoch、作用域清理跟踪、三类分页及审计界面提示；固定源码验证另记 | 保护删除最高行/全部行/中间缺口后的分配与查询、错作用域、恢复 epoch 及真实迁移中断；不是 180 天删除验收 |
| R3.retention_audit.preview | 已实现种类/主体注册表及有界只读候选，公开保护原因；阶段全仓待复验 | 人工记忆、文档作者、未知结果、费用和晚到引用逐项保护，未知种类保留；不执行删行 |
| R3.retention_audit.apply | 已开发验证备份、逐审计事务、持久进度、取消和恢复，专项通过、全仓待复验 | 审计故障回滚，提交前后真实进程退出可续跑，无重复维护审计；备份与错实例/schema拒绝规则通过 |
| R3.retention_audit.events | 已开发独立事件政策/台账、共同执行核心及预览/离线链，专项通过、全仓待复验 | 保留运行内序号和身份，游标明确失效；聊天/遗忘/投递来源/去重/结算不变 |

专项必须覆盖 180 天精确边界、时钟回拨、分页上限、跨 Agent、晚到人工来源与文档作者引用、partial cancel，以及新目录恢复。还要验证重复入站仍返回原运行、到期不重复、费用只结算一次、未知结果继续阻塞、遗忘传播有效、恢复不重放外部动作。

每个小批次分别记录开发、离线验证和真实平台验收。固定源码全量、静态检查、打包及恢复证据按仓库纪律保存；设计文档和模拟通过均不代替真实平台验收。
