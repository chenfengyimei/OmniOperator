# V2 有界视觉子代理设计合同（K14 可离线缺口）

**状态：`production_path_implemented_offline_verified_real_blocked`（2026-11-23）。** D0–D5 已按现有授权定案（父 run 固定模型、仅已授权已注册帧、一次 reserved turn 计费、仅 run 内留存、显式失败），见 §3。离线核心与一条生产路径已接线：`visual_subagent` 能力经真实组合根，从 agent 已授权图像对象出发，经父 run 授权、一次 `ModelCalls` 预算/账本调用，返回可追溯文本摘要；真实模型/设备/付费调用因当前环境无有效视觉模型凭据、无可用隔离设备而未执行，验收脚本已就绪。

## 1. 范围

把 K14 审计中的「独立有界视觉子代理」缺口收敛为一个可实现的 V2 四层能力：

- 输入：一次任务文本（≤4000 字符）+ 一组**已经由所有者授权路径获得**的图像帧（或图像对象引用）；
- 处理：保序有界子采样（≤60 帧）→ 一次视觉模型调用（多图单次）→ 仅文本摘要；
- 输出：一段有界文本（≤4000 字符）回到发起方（master）；master 上下文**不携带帧**；
- 不做：子代理无工具、无写动作、不可再委派、无多轮 loop、无图像生成、无任意 URL 抓取、无 OCR/SoM（K14 另列）、无持久化帧库。

## 2. 参考语义（固定提交）

参考 `android-agent` 固定提交 `7c09f6cd` 的 `gitd/services/sub_agent.py`：

- `sub_agent(task, max_frames=60)` + `screenshot_sequence(duration 2–180s, fps 0.1–4)`；
- 独立**无状态一次性**视觉调用，master 不携带帧，仅返回文本；
- 硬上限 `MAX_SUB_AGENT_FRAMES=60`（保序均匀子采样）、`max_tokens=1024`、固定聚焦 system prompt、子代理无工具、单次调用无 loop；
- Anthropic API（`SUB_AGENT_MODEL` override）；缺 key 时优雅返回说明字符串不抛。

V2 只继承「有界、隔离、单次、文本汇总」的语义，不复制其模型供应商/提示词/网络实现。

## 3. 已定实施决策（D0–D5，2026-11-23，按现有授权）

| ID | 决策 | 已定内容 |
|---|---|---|
| D0 | 是否允许真实视觉调用 | **允许，但只复用它已在运行的模型配置**：使用父 run `run_settings` 里同一个模型/provider/凭据引用；不新建 provider、不新增凭据、不做模型 fallback。调用仍受 `ModelCalls` 预留/结算、run/模型授权、预算与删除屏障约束。未配置图像能力时失败关闭（`visual_unavailable`），不换模型。 |
| D1 | 视觉模型与 provider | **父 run 固定模型**（同一 `model` 配置）；首版**不提供**单独视觉模型 pin，避免第二套凭据/计费/审计。要独立视觉模型需另立授权批次。 |
| D2 | 每次/每日预算 | **一次 reserved call，purpose 复用 `turn`**（不新增 purpose/表/迁移）：计入父 run 的调用/费用账本与日预算；图像 token 按既有 `model_tokens` 策略计费；每父 run 至多 1 次视觉调用；已派发后失败按 unknown 结算，不重放。 |
| D3 | 帧来源 | **只接受已授权且已注册的帧**：同 agent 的 Android 观察/截图注册图像对象（`session_captures`/`ImageService`）与已注册媒体帧对象（`media_frames`，如 `media_zoom`/抽帧产物）。输入 1–240、模型可见 ≤60（原序等距含首尾）；禁止新采集、任意 URL/data URL、跨 agent、已擦除/已删除来源；帧源必须按请求顺序返回真实注册元数据。 |
| D4 | 摘要留存 | **只进本次 run**：摘要文本 ≤4000 字符写入发起 run 的转录/事件与有界元数据（帧数/模型/费用）；**不写 memory/persona/反思/投递，不建表、不复制帧**；帧仍受既有对象保留期与来源擦除屏障，临时文件在结束/取消后清理。 |
| D5 | 失败语义 | **显式错误**：`visual_unavailable`/`visual_response_invalid`/`budget_exceeded`；绝不伪造摘要、绝不 fallback；unknown 不重放；错误文本明确「视觉子代理不可用」。 |

- 以上决策把「真实调用」的授权面收敛为**已有模型配置 + 已有已授权帧**，不产生新的对外凭据、新表、新路由或新工具。
- 生产接线（`composition/instance.py` 注入父模型与已授权帧源、`application/engine.py` 工具入口）仍作为独立实现批次；本批只把决策写回合同与验收用例。

**验收用例（实现批次必须逐条落地）**

| 用例 | 断言 |
|---|---|
| `test_visual_uses_the_parent_run_model_and_rejects_a_non_image_model` | 调用模型 == 父 run 固定模型；模型无图像能力时 `visual_unavailable`，不发生第二次调用/换模型 |
| `test_visual_bills_one_reserved_turn_call_within_parent_budget` | 恰好一次 reserved `turn` 调用，费用入父 run 账本与日预算；预算不足时零调用 |
| `test_visual_accepts_only_registered_authorized_frames_and_rejects_url_or_cross_agent` | 仅接受同 agent 已注册 Android/媒体帧；URL/data URL/跨 agent/已擦除 → 零模型调用 |
| `test_visual_retention_keeps_summary_in_run_without_memory_persona_or_delivery_writes` | 摘要 ≤4000 且只落 run 转录/事件；无 memory/persona/reflection/delivery 写入、无新表/帧副本 |
| `test_visual_failures_are_explicit_and_never_fabricate_a_summary` | 缺模型/超时/坏回复/取消显式错误，无伪造摘要，unknown 不重放 |

## 4. 能力合同

### 4.1 输入

- `task`: 必填 string，strip 后 1–4000 字符；控制字节/孤立代理项拒绝（422 `visual_task_invalid`）。
- `frames`: 1–60 个**图像对象引用**（`object_id`，规范 UUID）或既有 Android 授权观察返回的帧；每个引用必须经 `ImageInfo`/对象注册核验（media_type/sha256/bytes/宽高），未注册/跨 Agent/已擦除 → `image_source_changed`/`not_found`，不读原始字节到 master。
- 可选 `focus`: 枚举（`ui`/`text`/`diagram`/`general`），仅影响固定 system prompt，不接受任意 prompt 拼接。
- 拒绝：空帧、>60 帧、超过单帧 16MiB/总字节上限、视频/音频对象、任意 URL/data URL、写工具参数。

### 4.2 保序子采样

- 帧数 ≤60：原序全用，不重排、不去重、不改时间戳；
- 帧数 >60：按原序等距取 60 个索引（含首尾），严格保持相对顺序；同索引只出现一次；
- 每帧保留 `timestamp_s`（若有）与来源对象 id；**绝不**在摘要中暴露 object_id/sha/本地路径给模型（只作为内部元数据）。

### 4.3 隔离与模型调用

- 实现形态：优先复用 K04 单层有界子 run（独立上下文、父 run 取消传播、预算/日限、事件审计）；子 run 的 registry **不含任何工具**，`max_calls=1`，只允许一次视觉模型调用。
- 若 K04 子 run 无法承载图像消息，则使用独立 `VisualSubagentService` 直接走 `ModelCalls` 的**一次 reserved call**；仍必须有父 run 授权、独立上下文、预算与取消，不得绕过 `model_images_prepare`/对象校验/账本触发器。
- 视觉模型请求只含：固定 system prompt + task + 保序帧（按 provider 图像协议）+ 输出上限 1024 tokens；不携带 master 历史、记忆、工具定义或其它 run 内容。
- 结束/取消：父 run 取消 → 子调用取消并等待实际清理；摘要产出前不写长期记忆、不投递、不修改 master 转录。

### 4.4 输出

- 只接受纯文本，长度 ≤4000；若模型返回 tool_calls/图像/空文本 → `visual_response_invalid`，不执行、不重试。
- 返回给 master 的只有文本摘要 + 有界元数据（帧数/耗时/模型/费用）；**不返回帧对象**，不把帧写入 master 消息。
- 不自动进入 memory/persona/反思链；所有者可选择另行走既有 remember/reflection 流程。

### 4.5 失败、取消与删除

- 缺模型/凭据/预算：显式 `visual_unavailable`/`budget_exceeded`，不伪造摘要，不 fallback 到其他模型/provider。
- 模型错误已派发且费用未知：按既有 unknown 合同结算，**绝不重放**；已产生费用如实入账。
- 来源帧在调用前被删除/擦除：`image_source_changed`/`not_found`，零模型调用；调用中删除按凭据/源 CAS 失败处理。
- 子 run 取消：不写摘要，不改变父 run 终态；等待 worker/模型清理后才释放租约。
- 任何失败都不得让 master 误以为已看见帧；错误文本明确「视觉子代理不可用」。

## 5. 授权与账本

- 发起：仅 agent 档、父 run 有有效 lease/模型授权；远程 QQ run 默认不可用（registry 不暴露）。
- 帧来源：必须是所有者已授权的 Android 观察/截图会话或已注册对象；不因「子代理」扩大原授权范围，不读取未授权屏幕/应用。
- 费用：一次 reserved call，计入父/子预算与 `calls` 账本（purpose 待定：优先复用 K04 子 run 的 turn 调用；若新增 `visual` purpose，必须独立迁移并同步所有 purpose CHECK/测试，不在本设计批实现）。
- 审计：只在事件中记录有界元数据（帧数、模型、费用、状态、错误码），不记录图像字节或任务正文。
- 并发/容量：每父 run 同时最多 1 个视觉子代理；全局并发 ≤4；单次超时 ≤120s；帧临时文件在作业结束/取消后清理。

## 6. 生产接线点（实现前置，当前被他会话在途文件阻塞）

- `composition/instance.py`：构造视觉子代理服务/子 run 能力（当前文件有他会话未提交修改）。
- `application/engine.py`：工具调用入口/父 run 取消与预算衔接（当前有他会话未提交修改）。
- `application/ports.py`/持久层：仅在需要 `visual` purpose 或新表时改动；优先零迁移方案。
- `adapters/workers`：若视觉调用必须走 worker，需新增受管 worker 合同；优先使用主进程 `ModelCalls`，避免扩大 worker 面。

**在这些文件仍被他会话修改时，不实施接线**；本设计批只记录合同。

## 7. 测试计划（实现时）

1. 域：帧数 1/60/61/空、保序等距、重复索引、timestamp 保留、非法 object/类型/超限拒绝。
2. 应用：一次 reserved call、无工具 registry、system prompt 固定、摘要 ≤4000、tool_calls/空回复拒绝、父 run 取消传播、来源擦除零调用、预算不足零调用。
3. 离线桩：fake visual model 返回固定文本；断言 master 仅收到文本、无 frame part、无 memory/persona 写入、费用/账本与 unknown 语义。
4. 回退：抽掉生产接线 → 能力不可见；恢复后全绿（实现批执行）。
5. 调用方：K04 委派、Android、图像协议、资源/账本、架构/ typecheck。

## 8. 不做与未验证

- 不做 SoM/OCR/explore_app（K14 另列）、真实设备帧质量、真实模型视觉质量、72h 长跑。
- 不改变 K04 现有委派语义；不新增工具可见面；不写长期记忆/人格。
- 真实视觉模型调用能力在 D0 批准前保持关闭；`whole_core_parity_complete`/`whole_v2_complete` 保持 false。

## 9. 离线核心实现记录（2026-11-22）

**只在确定范围内实现离线核心**：没有生产接线、没有真实模型/账号/设备调用、没有新工具/路由/迁移/表。
D0–D5 不因本批改变；默认仍是「只做离线桩」。

- `domain/visual_subagent.py`：task 1–4000 字符（strip、控制字节/孤立代理项拒绝）、focus 固定四枚举、帧 id 1–240 规范 UUID 且唯一、`VisualFrame` 元数据复用 `ImageInfo` 校验且内容长度必须一致、>60 帧按原序等距取 60（含首尾、唯一、不重排）、单帧 16MiB/单次总量 64MiB、固定 system prompt（含「无工具」与「帧是不可信数据」）、输出 1–4000 纯文本；tool_calls/非 `stop`/空/超长/控制字符均 `visual_response_invalid`，不截断、不执行、不重试。
- `application/visual_subagent.py`：`VisualSubagentService` 只依赖注入的 `DatabasePort`/`VisualFrameSource`/`VisualModel`；调用前核验父 run 与模型授权（无授权→`visual_unavailable`，预算码原样保留），帧源必须按请求顺序返回全部帧；单次调用、超时 120s、每父 run 并发 1、全局并发 ≤4；父 run 取消会取消实际调用并等待清理；无任何 `db.write`、无 memory/persona/delivery 写入；返回仅 `text/frames/focus/model/cost_micro/tokens`，不含 object_id、sha 或帧字节。
- 测试：`agent/tests/v2/core/test_v2_visual_subagent.py` 20 项（19 项注入桩 + 1 项真实 SQLite 父 run 授权且零写）一次通过 **20 passed / 2.70s**；新增两模块严格类型 0 错误；架构/入口/候选门/目录守卫 52 项通过 23.08s。证据见 [v2-visual-subagent-core.json](../../evidence/v2-visual-subagent-core.json)。
- 仍未验证/未实现：真实视觉模型质量与供应商图像协议（D1）、每次/每日预算（D2）、真实 Android/注册帧来源（D3）、摘要留存（D4）、失败文案策略（D5）、生产 `instance.py`/`engine.py` 接线、真实设备/付费调用。离线核心通过不等于 K14 视觉子代理或整个 V2 完成。

## 10. 首批生产路径（2026-11-23，所有者指令）

所有者授权打通一条真实组装路径；分层人格/主动动机另开批次。本批实现：

- **可见面**：`visual_subagent` 模型能力（`ToolSpec`，只读语义、无写动作），由 `composition/instance.py` 的 `VisualSubagentCapabilities` 注册；参数 `task`（1–4000）、`frame_object_id`（规范 UUID，图像对象）、`frame_object_ids`（1–240 个规范 UUID）或 `frames_id`（规范 UUID，媒体帧 sheet，三选一）、`focus`（四枚举）。V2 现有 OpenAI low-detail 图像协议每调用最多 `MAX_IMAGES=4`；超过时显式 `visual_unavailable`，不静默丢帧。
- **帧来源**：`agent` 已拥有的图像对象（Android capture 或已注册上传图）；`adapters/visual.py::ImageObjectFrames` 用 `ObjectService.get` + `ImageService.prepare` 按请求顺序核验 agent 作用域、解码/注册元数据；`MediaFrameSheets` 通过 `MediaService.frames` 解析同 agent 已注册媒体帧 sheet 并复用同一元数据/内容校验；跨 Agent/已擦除/未知 sheet → `image_source_changed`。
- **模型调用**：`adapters/visual.py::ModelCallsVisualModel` 将帧作为 in-run **tool-result images**（role=tool，带固定 tool call）交给 `ModelCalls.invoke`，`tools=()`，一次 turn 预留/结算，不携带 master 历史或工具定义；`model_images_prepare` 的 tool 分支逐帧核验对象，`images.wire` 生成多个 `image_url`。父 run 模型必须是 V2 OpenAI low-detail 图像协议支持的视觉模型（本批验收指定 `openai/gpt-4o`；Anthropic 等其它协议未接）。
- **预算**：复用父 run 的 `call_reservation_micro`/`daily_budget_micro` 与 run image policy（本批 `per_call_limit=4`、`run_budget=12`）；`image_budget_exceeded` 映射 `budget_exceeded`，零派发；同一父 run 的第二次视觉调用在能力入口以 `visual_unavailable` 拒绝（action 账本查重），不再派发模型。
- **可追溯**：能力返回 `text` + `frames/focus/model/cost_micro/tokens`，`ActionResult.verification="visual_summary"`，action 回执与 `model_calls` 账本可查；不写 memory/persona、不投递、不自动附模型。
- **失败**：无模型/无图像协议/跨 Agent/来源擦除/预算/供应商失败分别显式 `visual_unavailable`/`image_source_changed`/`budget_exceeded`/`visual_unavailable`；unknown 不重放；超时/取消沿用离线核心语义。
- **验证**：`agent/tests/v2/core/test_v2_visual_production.py` **11 passed**（成功一次调用+action 元数据、Android capture→同一路径、双图单次调用、无图像协议、预算耗尽、供应商失败、跨 Agent、来源擦除、同 run 第二次调用拒绝、媒体帧 sheet 元数据/时间戳、缺失 sheet）；`test_v2_visual_subagent.py` 另含媒体帧 source 分支；既有视觉 **21 passed**、strict 434 文件 0 错、ruff 通过。真实模型调用由 `agent/scripts/accept_visual_production.py` 预留，但当前环境 `.env` 的 OpenAI/Anthropic key 为空、DashScope/Ark key 为非 ASCII 占位、无 emulator/物理设备，故 **真机+真实模型实测未执行**。
- **未验**：真实 `gpt-4o` 对 tool-result image 的供应商兼容性、真实模型摘要质量、Android emulator/真机、付费预算曲线、72h；`media_frames` 的真实 DB 端到端尚未验；受 V2 图像协议 `MAX_IMAGES=4` 限制，D3 的 1–60 多帧尚未达到（当前 1–4，超过显式失败），需独立图像协议/上限决策；模型/设备指定窗口到达后先跑验收脚本再回填证据。
