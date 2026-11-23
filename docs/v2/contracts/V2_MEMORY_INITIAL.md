# V2 记忆「最初」判定设计合同（K15.isInitial 缺口）

**状态：`design_contract_recorded`，未实现，无源码/测试/迁移/路由改动。** 本文件把 K15 审计与 [记忆评分合同](V2_MEMORY_SCORING.md) 里列后续的 `isInitial`（初次相遇 / 30 天首遇窗口）收敛为可裁定的决策门。D0–D5 未决前不改评分权威、不加列、不新增表、不接所有者入口。

## 1. 范围

- 输入：既有记忆及其来源证据（`memory_entries`、`source_anchors`、`messages`、`runs`、`qq_runs`、`people`）；不做新的采集。
- 输出：一次 `remember` 时是否把该条标记为 `is_first`，以及是否记录 `initial_tier`/`initial_probability` 审计元数据。
- 不做：新建人格/记忆卡、把「最初」注入系统前缀、自动删除或恢复记忆、跨 Agent 共享、真实账号/模型验收。
- 不改既有 `score_memory` 因子语义；`is_first` 已完整支持 +30、`kind=first`、`first&&owner→100 pinned`、`pinned` 计算。

## 2. 参考语义（固定提交）

参考 `xuexi/surfcore` 固定提交 `59ec3066` 的 `src/memory-card.js`：

- 只算**和主人的互动**；从第一次主人互动起 30 天窗口；
- 窗口分档 `first10`/`mid10`/`late10`/`after`，概率 `0.9/0.55/0.3/0.03`；
- 概率按记录 id 稳定散列，不随刷新随机变化；
- 「初次相遇」可由用户指定（单条或合并多条，落 `firstMet`/`kind=firstMeet`/`importance=100`/`pinned`）；无指定时自动取最早一条主人互动；
- `isInitial` 记忆在衰减中受保护（`protectedInitial`，最差保持清楚，不被硬遗忘）。

V2 只继承「只算主人互动 / 30 天窗口 / 确定性 / 可指定」语义；不复制角色的记忆卡格式、`firstMeet` kind、文件存储或概率阈值，除非所有者明确选择。

## 3. V2 现状核对（只读）

- `memory_entries`：`kind` 仅 `fact/profile/resident/journal/note`，无 `source/platform/tags/scope`、无 `is_initial/first_met/initial_tier` 列；`created_at` 来自既有 scoped 基类。
- `source_anchors`：可给出一条记忆的 `message_id`/`action_id`/`object_id`/`document_revision_id`/`manual_audit_id` 等来源；模型 `memory_remember` 默认用本 run 的 `input_message_id` 或显式 `message_id`，因此**主要捕获路径有消息锚点**。
- 消息锚点可经 `messages.run_id` → `runs`（含 `session_id`、`created_at`）→ `qq_runs`（`person_id`、`sender_id`、`binding_id`）→ `qq_bindings.scene` → `people.owner_id` 还原「谁在哪个渠道说的、是否主人」。
- 没有消息/动作/文档锚点的记忆（例如 owner 直接写入、隔离注入）无法证明「主人互动」，不应被自动判为 isInitial。
- 当前衰减保护条件是 `pinned` 或 `importance >= 65`；`score_memory` 的 `first&&owner→100 pinned` 可复用，但**概率型 isInitial 不一定达到 65**，若要求参考式保护，需要新列/新语义或明确只保护 first-meet。
- 当前 `remember` 在渠道上下文可解析时自动评分（QQ 私聊/群 @、direct=true）；非渠道保持默认 50。`is_first` 当前固定 `False`。

## 4. 待所有者决策

| ID | 决策 | 选项 | 未决时 |
|---|---|---|---|
| D0 | 判定方式 | 仅自动「最早一条主人互动」／仅所有者手动指定／两者都做 | 不实现，`is_first` 保持 False |
| D1 | 什么算主人互动 | 仅 QQ 主人私聊/群内 @ 的消息锚点／再计入 pet/owner 直接写入／所有 `people.owner_id` 记忆 | 仅消息锚点可证明的 QQ 主人互动 |
| D2 | 30 天窗口用途 | 只用于审计 tier/probability／把窗口内的记忆都按概率标 is_first／只把最早一条标 is_first | 不把概率写进评分 |
| D3 | 持久化 | 完全不存，remember 时重算／只写 `memory.scored` 审计／新列（`initial_tier`/`initial_probability`/`first_met`）或新表 | 不迁移；只重算/审计 |
| D4 | 遗忘保护 | 复用 `pinned`/importance 阈值／新增 `protected_initial` 列／不额外保护 | 复用现有 pinned/importance |
| D5 | 手动指定入口 | 所有者 HTTP/CLI 选择记忆 id／设置 JSON 单条标记／新表 | 不做手动入口（避免共享接口/迁移） |

**D0/D2/D3 未决不实现；D3 未决不新增列/表；D5 未决不加所有者路由。** 迁移与公共 HTTP 接口由 A 协调。

## 5. 两个可落地选项（供选择，不是已批准实现）

### 5.1 保守零迁移（推荐先做）

- 只对**有消息锚点且可证明是主人互动**的记忆，计算「最早主人互动时间」；
- 仅当该条是当前最早一条主人互动时，`is_first=True`（不给窗口内其他记忆做概率提升）；
- `initial_tier`/`initial_probability` 仅写入 `memory.scored` 审计（若 D3 允许审计），不落列；
- 复用 `first&&owner→100 pinned` 作为保护，不新增 `protected_initial`；
- 无手动指定；无新迁移、无新路由、无新表。
- 风险：概率型「最初的日子」不完整；只保护 first-meet，不保护 30 天内其他记忆；锚点缺失时保守不判。

### 5.2 参考完整版（需要迁移与所有者入口）

- 新增 `initial_tier`/`initial_probability`/`first_met` 列或 `memory_initial` 表（迁移由 A 分配编号并做旧库升级）；
- 按稳定散列和 30 天窗口计算 `is_initial`；衰减保护增加 `protected_initial`；
- 所有者手动指定/取消入口（HTTP 或 CLI），CAS/审计/来源删除屏障；
- 需要同步 strict include、表数 pin、迁移测试、UI（若有）与 72h 验收。

## 6. 测试计划（实现时）

1. 领域：day offset 0/9/10/19/20/29/30/31 的 tier/probability、稳定散列确定性、非法 id/时间拒绝。
2. 仓储：最早主人互动查询只读、只认消息锚点、跨 Agent 隔离、来源删除后不再参与。
3. 接线：最早一条 owner 记忆 `is_first=True` 且审计；非主人/无锚点/非渠道保持 False；显式 importance 仍优先；非渠道默认 50 零回归。
4. 保护：first-meet pinned/100 不被硬遗忘；概率型（若 D2 选择）按 D4 保护。
5. 手动指定（若 D5）：只能选 owner 记忆、CAS、取消后恢复自动最早、来源删除阻断。
6. 调用方：memory/decay/decay_tick/scoring/organization/semantics/background_review/QQ；strict/ruff；无迁移选项还需确认表数不变。

## 7. 不做的声明

- 本批未改评分权威、未改 `is_first` 默认值、未新增列/表/迁移/路由、未接真实模型/账号/设备。
- 不宣称「最初」/记忆卡/人格一致性对等；`whole_core_parity_complete`、`whole_v2_complete` 保持 false。
- 真实模型 importance 质量、长期衰减曲线、跨渠道人格一致性另验。
