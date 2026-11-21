# V2 有界视觉子代理设计合同（K14 可离线缺口，实现前）

**状态：`design_contract_recorded`，未实现。** 本文件只记录实现前的授权/预算/边界合同，供所有者决定是否允许真实视觉模型调用；本批无源码、测试、迁移、路由改动。

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

## 3. 待所有者决策（D0，未决不做真实调用）

| ID | 决策 | 选项 | 默认/不决策时 |
|---|---|---|---|
| D0 | 是否允许真实视觉模型调用 | 允许（需预算/模型 pin/审计）／只做离线桩 | **只做离线桩**，不调用任何真实视觉模型 |
| D1 | 视觉模型与 provider | 复用主模型（若支持图像）／单独固定视觉模型 | 未定则离线桩 |
| D2 | 每次/每日预算 | 复用 K04 子 run 预算／独立小额日预算 | 未定则离线桩，不产生费用 |
| D3 | 帧来源 | Android 已授权 observation/capture 帧／所有者提供的注册图像对象 | 仅现成已授权帧，不做新采集 |
| D4 | 摘要留存 | 仅子 run 事件／所有者可读（不自动进长期记忆/人格） | 仅本次 run，不写长期记忆 |
| D5 | 失败语义 | 缺 key 优雅文本（参考）／显式错误 | V2 默认显式 `visual_unavailable`，不伪造摘要 |

**未决策前只能实现离线桩路径**（注入 fake visual model，测试结构/边界/预算/取消），不得生产调用真实视觉 API。

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
