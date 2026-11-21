# V2 主动动机引擎设计合同（K15 可离线缺口，实现前）

**状态：`design_contract_recorded`，未实现。** 本文件只记录实现前的授权/预算/quiet/外联硬门与纯逻辑边界，供所有者裁定 D0–D4；本批无源码、测试、迁移、路由改动。

## 1. 范围

把 K15 审计中的「主动动机/需求/目标/情绪引擎」收敛为一个**可离线纯逻辑**的核心，再接 V2 既有调度/投递/审批框架：

- 观察输入（只读）：所有者沉默时长、QQ/B 站入站事件、日程/任务完成、媒体观看记录、记忆/人物事实（只读引用）；
- 纯逻辑状态：9 需求、9 维驱动 0–1、目标假设、10 情绪、动作提议；
- 输出：**有界动作提议**（share/ask/invite/confide/curiosity/follow），默认只写入内部待办/观察账本；
- 不做：自动外联、自动私聊/评论、自动加好友、自动下单/支付、无审批的工具执行、无上限模型调用、人格/记忆写入。

## 2. 参考语义（固定提交）

参考 `xuexi/surfcore` 固定提交 `59ec3066f7d42a0452fae01f170c9a8484d5dbe5` 的 `purpose-engine.js`：

- 管线：attention → 9 需求（play/install/learn/connect/care/share/identity/create/consume）→ 目标假设（归因 self/owner/member/media + confidence/value/urgency/risk）→ 10 情绪评估 → 动作（share/ask/invite/confide/curiosity/follow）；
- 驱动 9 维 0–1，半衰期 8h 向默认衰减；`connect` 随 owner 沉默上升；`analyzePersona` 人格偏置；发送满足需求（×0.45）；
- `purposeTick` 60s tick，12 硬门（reserved2/whitelist/quiet/dailyMax=4/idle/token/friend）；载体互斥与回退。

V2 只继承「纯逻辑状态 + 有界动作提议 + 硬门」语义，不复制参考实现/供应商/提示词/账号行为。

## 3. 待所有者决策（D0–D4）

| ID | 决策 | 选项 | 不决策时 |
|---|---|---|---|
| D0 | 主动动机范围 | 全量（9 需求/9 驱动/10 情绪）／仅核心（需求→目标→驱动）／不做 | 不实现，保持 `audited_gaps_recorded` |
| D1 | 状态持久化 | 新 `motivation_state` 表（需迁移/冻结触发器）／仅内存+事件重放 | 不落库，仅离线桩 |
| D2 | tick 载体 | 复用 `MaintenanceService.tick` 60s／独立调度 | 不接线 |
| D3 | 话语生成 | 纯模板（零模型）／模型措辞（需预算/授权/降级） | 纯模板，禁模型 |
| D4 | 外联渠道 | 仅内部提议／所有者审批后经既有 outbox／指定白名单渠道 | 仅内部提议，绝不自动外发 |

**D0 未决前不实现任何生产状态/外联**；D1 未决前不新增表/迁移；D3 未决前不调用模型；D4 未决前不投递。

## 4. 纯逻辑合同

### 4.1 状态与边界

- 需求/驱动/情绪均为**有界浮点**（0–1，clamp；拒绝 NaN/Inf）；9 需求固定枚举，未知需求拒绝。
- 驱动每 tick 向默认值半衰期 8h 衰减：`next = default + (prev-default)*2^(-dt/8h)`；`dt` 由注入 Clock 提供，**不读系统墙钟**。
- `connect` 随 owner 沉默上升，但设上限；沉默时长只取既有会话/消息时间戳，不推断外部在线状态。
- 目标假设：纯结构 `{need, attribution, confidence, value, urgency, risk, evidence_refs}`；`attribution` 仅 self/owner/member/media；`evidence_refs` 只存既有 id/来源，不复制私密正文。
- 情绪：10 标签有界枚举 + intensity；只影响提议排序，不直接触发写/外联。
- 人格偏置：只读 `settings["persona"]`/people 事实的**既有纯函数**，不写人格；偏置系数有界。

### 4.2 动作提议

- 动作枚举固定：share/ask/invite/confide/curiosity/follow；每 tick 最多 1 条提议，每提议带 `need`/`goal`/`draft_template`/`risk`/`evidence_refs`。
- 提议只写内部状态/审计（D4 未批时不投递），UUID 幂等键；同一 need+goal+day 不重复提议（有界去重）。
- 提议不得包含凭据、token、密码、文件路径、原始聊天全文；模板变量只允许既有公开/所有者可见字段。
- 模型措辞（若 D3 批准）：单次、有界 prompt、无工具、输出 ≤1000 字符；模型失败/空回复 → 保留模板，不重试、不阻塞 tick。

### 4.3 外联硬门（D4 批准后）

即使所有者批准外联，也必须同时满足：

1. quiet hours（所有者本地时区；默认 23:00–08:00）内不发送；
2. 每日主动外联 ≤4（按上海日/所有者时区，原子计数）；
3. 所有者/渠道白名单匹配；受控联系人只读既有授权；
4. 当前无 reserved/审批等待/运行中高优先级任务；
5. 模型/token/预算余量足够；无预算不发送；
6. 同一提议幂等键未发送过；unknown 不重放；
7. 载体互斥：同一 tick 只选一个渠道；失败按既有 outbox 语义，不自动改渠道重发。

## 5. 授权与账本

- 触发：仅 `MaintenanceService`/所有者启动的 tick；远程 QQ run 不暴露该引擎。
- 观察：只读既有会话/事件/记忆/媒体记录；不扩权、不读未授权屏幕/设备。
- 提议/外联审计：只记录有界元数据（need、goal id、动作、渠道、状态、错误码），不记录聊天全文/凭据/像素。
- 模型调用（若 D3）：一次 reserved call，计入既有预算/账本；usage/成本照常，不伪造缓存命中。
- 所有者控制：全局开关 + 每 Agent 开关 + 暂停/恢复；关闭后 tick 不产生提议。

## 6. 生产接线点（实现前置，当前被他会话在途文件阻塞）

- `composition/instance.py`：构造/注入动机服务与 tick（当前有他会话未提交修改）。
- `application/engine.py`：run 结束/回合后观察钩子（当前有他会话未提交修改）。
- `adapters/persistence`/迁移：仅 D1 批准后新增；需冻结迁移 + head 同步 + 表数 pin。
- `MaintenanceService`：tick 顺序与预算（当前未在途，可后续小批）。

## 7. 测试计划（实现时）

1. 纯逻辑：9 驱动衰减半衰期/默认值/clamp、connect 随沉默、人格偏置有界、非法值拒绝。
2. 目标/情绪：归因枚举、confidence/value/urgency/risk 排序、每 tick 1 提议、去重。
3. 硬门：quiet/日限/白名单/reserved/预算/幂等/unknown 不重放/载体互斥，逐项拒绝零发送。
4. 持久化（D1）：重启恢复、原子 tick、擦除/保留边界、迁移表数 pin。
5. 模型措辞（D3）：无工具、单次、空回复降级模板、预算耗尽零调用。
6. 回退：摘 tick/硬门 → 新增用例红；恢复后绿。

## 8. 不做与未验证

- 不做自动外联、自动加好友/评论/私聊、支付、设备操作、真实账号行为。
- 不做完整自主作息/质量反馈闭环（K15 另列）；不宣称完整自主行为对等。
- 真实模型/账号/72h 质量另验；`whole_core_parity_complete`/`whole_v2_complete` 保持 false。
