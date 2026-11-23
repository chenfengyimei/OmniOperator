# K14/K15 决策台账（2026-11-22）

本文件只汇总 [视觉子代理](V2_VISUAL_SUBAGENT.md)、[主动动机](V2_MOTIVATION.md)、[分层人格](V2_PERSONA_LAYERS.md) 三份合同里**已经确定**与**仍需所有者选择**的条款，避免后续批次把未决项当成已授权。合同正文仍是边界权威；本台账随决策更新。

## 1. K14 有界视觉子代理

### 已决定（合同默认，可离线实现）

| 条款 | 已定内容 |
|---|---|
| 真实视觉调用（D0） | **默认只做离线桩**；D0 未批准前不调用任何真实视觉模型、不产生费用。 |
| 输入 | 一次任务文本 1–4000 字符；帧必须是**已由所有者授权路径获得并已注册**的图像对象；focus 固定 `ui/text/diagram/general`。 |
| 帧上限 | 模型可见 ≤60 帧；超过 60 按原序等距取 60，含首尾、不重排、不去重时间戳；单帧 ≤16MiB。 |
| 隔离 | 子代理无工具、无 master 历史/记忆、不可再委派、无多轮 loop；一次调用只允许一次 reserved call（D2 未决时走离线桩）。 |
| 输出 | 只接受纯文本 ≤4000 字符；tool_calls/图像/空文本/超长 → `visual_response_invalid`，不执行、不重试。 |
| master 边界 | master 只收到文本摘要 + 有界元数据；**不携带帧对象、object_id、sha 或帧字节**；不自动写 memory/persona/反思/投递。 |
| 失败/取消 | 缺模型/凭据/预算显式 `visual_unavailable`/`budget_exceeded`；unknown 不重放；来源擦除在调用前零调用；取消等待实际清理。 |
| 并发/超时 | 每父 run 同时 1 个、全局 ≤4、单次 ≤120s；帧临时文件结束后清理。 |
| 可见面 | 不新增模型工具、HTTP 路由或迁移；生产接线阻塞于 D0/D1/D3。 |

### 已定案（2026-11-23，按现有授权写回合同）

| ID | 决策 | 已定内容 |
|---|---|---|
| D0 | 真实视觉调用 | **允许，但只复用父 run 已在运行的模型配置**（同 provider/凭据/模型）；不新建 provider/凭据/fallback，未配置图像能力即 `visual_unavailable`。 |
| D1 | 模型 | **父 run 固定模型**；首版不设独立视觉模型 pin（独立模型需另立授权批次）。 |
| D2 | 计费 | **一次 reserved call，purpose 复用 `turn`**，入父 run 账本与日预算，图像按既有 `model_tokens` 计费；每父 run ≤1 次；unknown 不重放。 |
| D3 | 帧来源 | **同 agent 已授权且已注册的帧**：Android `session_captures`/ImageService 与 `media_frames`；输入 1–240、模型可见 ≤60；禁新采集/URL/data URL/跨 agent/已擦除。 |
| D4 | 留存 | **只进本次 run**（≤4000 字符摘要 + 有界元数据）；不写 memory/persona/反思/投递、不建表、不复制帧。 |
| D5 | 失败 | **显式 `visual_unavailable`/`visual_response_invalid`/`budget_exceeded`**，不伪造、不 fallback、不重放。 |

生产接线已按上述 D0–D5 完成首批路径（2026-11-23，`k14-visual-production`）：`visual_subagent` 能力 + `ImageObjectFrames` + `ModelCallsVisualModel`，12 项生产专项通过；真实模型/设备因 key/设备缺失未执行，`agent/scripts/accept_visual_production.py` 留给指定窗口。证据见 [v2-visual-production-path.json](../../evidence/v2-visual-production-path.json)。验收用例见 [视觉子代理合同 §3](V2_VISUAL_SUBAGENT.md)。

**合同措辞待确认**：§4.1 写帧 1–60，§4.2 又规定 >60 等距子采样。离线核心按「原始输入 1–240、模型可见 ≤60」实现，并在测试中锁定；若所有者希望硬拒绝 >60，只需改一个上限并补回归。

**本批状态**：离线核心 + 首批生产路径已接线（`visual_subagent` 能力、`ImageObjectFrames`、`ModelCallsVisualModel`）；生产专项 12 项通过，真实模型/设备因 key/设备缺失未执行。证据见 [v2-visual-production-path.json](../../evidence/v2-visual-production-path.json)。

## 2. K15 主动动机引擎

### 已决定（合同默认）

- D0 未决前不实现任何生产状态；D1 未决前不新增表/迁移；D2 未决前不接 tick；D3 未决前不调用模型；D4 未决前**只写内部提议，绝不自动外发**。
- 9 需求固定枚举（play/install/learn/connect/care/share/identity/create/consume），9 维驱动 0–1，半衰期 8h；10 情绪只影响排序，不直接触发写/外联。
- 每 tick 至多 1 条提议；`need+goal+day` 幂等去重；证据只存既有 id/来源，不复制私密正文。
- 若未来批准外联，硬门同时满足：quiet 23:00–08:00（所有者时区）、每日 ≤4、白名单、无 reserved/审批/高优任务、预算余量、幂等未发送、unknown 不重放、同一 tick 载体互斥（失败不改渠道重发）。
- 只由 `MaintenanceService`/所有者启动 tick；远程 QQ run 不暴露；只读观察，不扩权。

### 仍需所有者选择

| ID | 待选 | 不决策时 |
|---|---|---|
| D0 | 全量（9 需求/9 驱动/10 情绪）／仅核心（需求→目标→驱动）／不做 | 不实现，保持 `audited_gaps_recorded` |
| D1 | 新 `motivation_state` 表（迁移/冻结触发器）／仅内存+事件重放 | 不落库，仅离线桩 |
| D2 | 复用 `MaintenanceService.tick` 60s／独立调度 | 不接线 |
| D3 | 纯模板（零模型）／模型措辞（预算/授权/降级） | 纯模板，禁模型 |
| D4 | 仅内部提议／所有者审批后经既有 outbox／指定白名单渠道 | 仅内部提议，绝不自动外发 |

### 给 E 的主动外联边界与候选稳定接口

**边界（不可由实现批次放宽）**

1. 动机引擎**永远不直接发送** QQ/B 站/任何渠道消息；最多生成内部提议。
2. 只有 D4 明确批准且所有者审批后，才允许把提议交给既有 `DeliveryService` outbox；不得新建第二套投递/状态库。
3. 外联前逐项通过 quiet/日限/白名单/reserved/预算/幂等/unknown/载体互斥硬门；任一失败即零发送，不换渠道重试。
4. 不自动加好友、评论、私聊、下单、支付或执行设备动作；不读未授权屏幕/设备。
5. 观察只读既有会话/事件/记忆/媒体记录；提议审计只记 need/goal/action/channel/状态/错误码，不记正文/凭据/像素。

**候选稳定接口（设计；D0–D4 未决，尚未实现）**

```text
MotivationService.tick(agent_id, now) -> MotivationProposal | None
```

- `MotivationProposal`: `proposal_id`（UUID，幂等）、`agent_id`、`created_at`、`need`（9 枚举）、
  `goal`（`{attribution: self|owner|member|media, confidence, value, urgency, risk, evidence_refs}`）、
  `emotion`（10 标签 + `intensity`）、`action`（share/ask/invite/confide/curiosity/follow）、
  `draft_template`、`risk`、`evidence_refs`、`dedupe_key`（`need+goal+day`）。
- `tick` 只返回/写入内部提议，不发送、不写 memory/persona；无提议返回 `None`。
- 若将来接 outbox，提交边界是既有 `DeliveryService` 的显式所有者审批接口，而不是动机引擎直接调用渠道适配器。
- E 若先做调度/展示，可只消费 `MotivationProposal` 的上述字段；不要依赖未定的模型措辞或自动发送。

## 3. K15 分层人格与跨渠道统一卡

### 已决定（合同边界）

- 层序固定 `identity-base → core → tone → scene → daily`；空层跳过，层间固定分隔符。
- `core` 永不因预算被裁；裁剪顺序 `daily → scene → tone`，最后才裁 identity-base 尾部；组合结果 ≤24,000 字符（建议值）。
- 每层文本是 owner 提供的**不可信数据**：不执行、不解析命令/模板、不拼任意 URL/凭据；控制字节/孤立代理项拒绝。
- scene 选择：QQ 复用既有 `qq_run.scene`（private/group），未知回退 core；B 站仅 D4 批准后注入 sanitized 文本；pet/desktop 显式请求时使用，否则 core。
- run 内冻结 `settings["persona"]`；跨 run scene 变化必须体现在 K01 缓存键，**不得让不同 scene 的前缀字节共享缓存桶**。
- B 站 sanitize 固定规则：删 QQ 号/昵称/群号/`@` 账号/外部链接，保留人格/价值观/兴趣文本，追加固定 banner「QQ 和 B 站是同一个人」；疑似凭据模式 → 拒绝注入、回退 core、审计 `persona_sanitize_denied`，不发送原文。
- D5 角色包 epoch：单事务新 epoch + 当前指针 CAS；run 起点捕获，run 内不切换；旧 epoch 只读归档；擦除只存引用。

### 仍需所有者选择

| ID | 待选 | 不决策时 |
|---|---|---|
| D0 | 层存储：扩展 `settings` JSON／角色卡文件包／新表 | 不实现，保持单 persona 串 |
| D1 | scene 来源：仅 QQ／QQ+B站+pet／按会话显式标签 | 仅 QQ，其他渠道用 core |
| D2 | 预算裁剪：固定字符上限（如 identity 8k/core 8k/tone 2k/scene 4k/daily 4k）／比例 | 未定不裁剪，只做组合 |
| D3 | 注入点：`run_settings` 组合成 `settings["persona"]`／`memory_system` 组合（触及冻结前缀） | 不实现 |
| D4 | B 站 sanitize：剥离 QQ 号/昵称/群号+固定 banner／仅剥离凭据类 | 不注入 B 站，仅 QQ |
| D5 | 角色包 epoch：新表/列+原子切换（需迁移）／仅配置 revision CAS | 不做角色包切换 |

### 现有前缀与撤回核对（只读，不实现 D0/D3）

- 当前 V2 的冻结前缀由 `domain/metering.memory_system(settings, memories, notice)` 生成：
  `settings["persona"]` + 可见 owner memory JSON + runtime notice；`history_request` 把它作为第一条 system 消息。
- K01 缓存键当前是 `(agent, session, content_epoch)`（`pc_<sha256(...)>`）；**同一 session 跨 scene 会复用同一前缀缓存**，因此 D3 落地前必须先选「session 内固定 scene」或「缓存键加 scene_hash」。
- 记忆撤回/遗忘：`memory_system` 只纳入 `state == "captured"` 且未擦除的记忆；`forget`/衰减为 `forgotten`/来源擦除后，下一次 run 的前缀立即变化（本轮实测见 `test_v2_context.py` 与 K15 记忆专项）。
- 反思/人格成长撤回：`reflection_decide(retract)` 只改变人格成长派生视图与来源日阻断，**不进入系统前缀**（合同明确人格成长文件不注入）；因此不会自动改变当前前缀。
- 分层人格、scene 裁剪、B 站 sanitize、角色包 epoch 目前**均未实现**；D0/D3 未决，本批不写生产代码。

## 4. 本轮已核对的既有能力（回归证据）

| 范围 | 结果 |
|---|---|
| Android 截图/输入/撤权链 | `test_v2_android.py` + `test_v2_android_model.py` **36 passed / 217.97s**（真实 SQLite、受管 Python worker、隔离假 ADB；无真机） |
| 记忆评分 | `test_v2_memory_scoring.py` 16 passed（含真实 SQLite 渠道门控） |
| 记忆衰减/巩固/自动 tick | `test_v2_memory_decay.py` + `test_v2_memory_decay_tick.py` 31 passed；两文件合计 **47 passed / 85.15s** |
| 前缀/撤回/人物 | `test_v2_context.py` + `test_v2_persona_growth.py` + `test_v2_people.py` **39 passed / 1 skipped / 55.71s**（含遗忘级联到摘要与后续上下文、擦除后前缀变化、人物擦除使上下文失效） |

**未验/未做**：真机 Android I/O 与截图质量、真实 QQ/B 站账号送达、真实视觉模型质量与付费调用、72 小时；动机/分层人格/角色包 epoch 未实现；D0–D5 决策未到。
