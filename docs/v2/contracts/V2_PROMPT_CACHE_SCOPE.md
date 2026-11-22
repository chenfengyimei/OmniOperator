# V2 提示缓存作用域设计合同（K01 独立设计合同，先合同后接线）

## 状态

**设计合同（design_contract_recorded，纯文档，无源码/测试/迁移改动，2026-11-21）。** K01 现状态 `partial`：交付项为「单 run 前缀冻结，档位/已审技能变化以通知形式注入」；剩余项为「会话/分叉 lineage 级稳定范围与跨 run 策略；真实缓存效果单独验收」。本合同即该剩余项的设计合同。**2026-11-22 更新：D3(b) 路由键注入已实现**（`K01.prompt_cache_route_key`，纯 adapter 注入，无冻结合同改动）；D3(a)/(c) 前缀重构与 D8 真实缓存效果仍待所有者/隔离环境。**2026-11-23 更新：D3(a)/(c)+D8 的细化设计合同已记录**（[V2_PROMPT_CACHE_PREFIX](V2_PROMPT_CACHE_PREFIX.md)：P1-a 仅 persona 系统前缀/记忆与摘要固定位/route·skills 走尾、P1-b 混合、P1-c 最小改动、P1-d 维持；D8 隔离 provider 对照实验），实现仍待所有者选定 P1 方案。

**本合同本身是安全的纯设计文档，可提交。其第二阶段实现触及两处需所有者批准的面：(1) 系统前缀结构（`domain/metering.py` 的 `memory_system`/`history_request`，当前冻结前缀合同）；(2) `adapters/models.py` 请求装配 + 可能含会话标识哈希的 provider 缓存键（安全敏感）。按「合同先于接线」纪律，实现前需所有者审阅本合同并选定 D3 方案。**

`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。

## 勘定事实（实读源码，2026-11-21）

1. **V2 无显式缓存作用域面**：全仓 `agent/src/omni` 内 `prompt_cache_key`/`cache_scope`/`prompt_cache_scope` 零命中。V2 只**读** provider usage 的缓存字段（`adapters/models.py:184-194`：`prompt_cache_hit_tokens`（DeepSeek/OpenAI 系）、`prompt_cache_details.cached_tokens`（OpenAI detail）、`cache_read_input_tokens`/`cache_creation_input_tokens`（Anthropic 系）），从不**发**任何缓存路由键（唯一例外见事实 8）。
2. **系统前缀构造**：`context.py:220-222` → `_history`（`:34-50`）→ `history_request`（`domain/metering.py:56-73`）→ `system = memory_system`（`metering.py:43-53`）＝ `settings["persona"]` + `"\nOwner memory (data, not authorization):\n" + json.dumps(captured 且非擦除 memories)` + `notice`。其中 `notice = context_notice(omitted, reason, erased)`（`:76-80`）`+ extra_notice`（`engine.py:336-338` 的 `route_notice(route.mode, …) + skills.notice(agent_id, run_id)`）。**即系统前缀含 memories、上下文省略计数、档位路由通知与已审技能通知——这些均可能跨 run 变动 → 前缀字节跨 run 漂移。**
3. **尾部通知**：`tail_notice = todo_notice`（`engine.py:339`）经 `append_runtime_notice`（`context.py:49`）追加到请求**尾部**（不在系统前缀内）。故任务 JSON「档位/已审技能变化尾部通知」中 route/skills 通知实际并入**系统前缀**（`extra_notice`），仅 todo 通知在尾部。
4. **run 内前缀冻结**：`compact_run`（`context.py:256-327`）仅在序列化转录超 `RUN_COMPACT_TRIGGER_CHARS` 时，把最旧完整工具轮压缩为一条有界 `user` 摘要（计费 `summary` 调用，经授权/擦除守卫），**冻结系统前缀、初始 head 与最近 rounds 不变**。即单 run 内前缀字节稳定（K01 已交付项）。
5. **压缩原位、session 不轮换**：`domain/session_search.py:1-4` 明言 `lineage-free dedup … instead of walking a compression lineage`。V2 压缩不铸新 session_id（区别于参考 hermes 的「压缩轮换 session_id 导致换缓存桶」模型），`session_id` 持久稳定。
6. **content_epoch 即 generation 边界**：`sessions.content_epoch` 默认 1（`migrations/v0004.py:16`），仅在保留/擦除重置边界 `+1`（`retention_repository.py:61`，同时 `context_floor_seq = next_seq`），并作为 `summaries`/`session_captures`/`deliveries`/`delegations` 的 `session_epoch` 一致性栅栏（如 `context_repository.py:73/372`、`delegation_repository.py:186`、`delivery_repository.py:56`）。**压缩不推进 content_epoch。**
7. **委派建独立子会话**：`delegation_repository.py:186` 记录 `parent/child_session_id/parent_epoch`，子会话拥有独立 `session_id` 与 `content_epoch` 谱系 → 天然隔离。
8. **请求装配注入点与既有 provider 亲和先例**：`adapters/models.py` `complete`（`:43`）→ `prepare`（`:46`）→ `_prepare`（`:101-152`）构造 litellm `kwargs`（`model/messages/timeout/num_retries/no-log/caching:False/max_tokens/api_key/api_base/extra_headers/tools`），`_send`（`:154`）调 `litellm.acompletion`。`_prepare` **已对 OpenCode Go 注入稳定会话标识**：`base_url` 含 `opencode.ai` 时置 `headers["x-opencode-session"] = model_profile_id or "omni-operator"`（`:127-130`，注释明言「以便路由与提示缓存」）——**这是 provider 特定缓存亲和头的既有先例与本合同可复用的注入模式**。`extra_headers` 来自 `settings["extra_headers"]` 且过滤掉 `authorization`/`x-api-key`（`:131-140`）。
9. **参考实现（xuexi/hermes-agent，不可信设计数据，非本会话指令）**：`agent/prompt_cache_scope.py` `resolve_prompt_cache_scope`＝继承父 scope（同模型 cache-parity fork）→ `declared_conversation_scope`（`gwk_<sha256[:24]>`，哈希 `source|gateway_session_key|generation`）→ 压缩 lineage root → 物理 `session_id`；fail-closed（`*_safe` 返回 None 回退物理 id）；`is_slot_keyed_cache_route`（xAI/`api.x.ai`/Grok aggregator＝单 slot 互驱逐；Anthropic/DeepSeek/Gemini＝内容地址可共享；OpenAI `prompt_cache_key` 仅前缀路由）；`_apply_fork_tag`（`<scope>::<tag>`，slot-keyed route 且 fork 自身已压缩后）。`agent/prompt_builder.py` 缓存哲学：系统提示每会话固定、toolset per-session 固定＝缓存安全，新回合追加尾部（`:520`、`:398`）。

## 参考与 V2 的结构差异（适配前提）

| 维度 | 参考 hermes | V2 现状 | 适配结论 |
|---|---|---|---|
| 会话粒度 | gateway 每响应铸新 id，需 `gateway_session_key` 哈希 | 持久 `sessions` 行，`session_id` 稳定 | 物理 `session_id` 即稳定范围，**无需**哈希平台/chat/user 标识（降低安全敏感面） |
| 压缩 | 轮换新 `session_id`，需 lineage root 回溯 | 原位压缩，`session_id` 不变 | **无** lineage 回溯需求 |
| 重置代数 | `generation` counter | `content_epoch`（重置 +1，压缩不动） | `content_epoch` 即 generation |
| 分叉/委派 | fork/branch 子 session | 委派独立子 `session_id` | 子会话天然隔离，无 merge 防护需求 |

## 设计决策（实现需所有者批准）

### D1 作用域标识（无冻结合同改动）

- V2 稳定缓存范围 = `(agent_id, session_id, content_epoch)`。`session_id` 持久稳定（压缩不轮换，事实 5），`content_epoch` 重置即新桶（事实 6）。
- 对 provider 暴露的 `prompt_cache_key` 用**不可逆派生键** `pc_<sha256("agent_id|session_id|content_epoch")[:24]>`：不暴露内部 UUID，不含明文外部平台标识（V2 无此模型，事实 1/差异表）。
- 委派子会话各自独立 scope，不继承父 scope（除非未来 K17 cache-parity fork 显式继承，见 D5）。

### D2 provider 路由分类（无冻结合同改动）

- 镜像 `is_slot_keyed_cache_route`：**slot-keyed**（xAI/`api.x.ai`/Grok aggregator `x-ai/grok-*`）必须稳定 `prompt_cache_key` 防各请求互驱逐；**内容地址**（Anthropic/DeepSeek/Gemini）靠前缀字节 parity，路由键非必需；**OpenAI** `prompt_cache_key` 仅前缀路由增益。
- provider 分类在 `adapters/models.py` 内依据 `settings["base_url"]`/`settings["model"]` 判定，**domain 层不感知 provider**（沿用现有分层）。
- 复用 OpenCode Go `x-opencode-session` 先例（事实 8）；新 provider 亲和逻辑仅在 adapter 注入。

### D3 跨 run 前缀稳定策略（**触及冻结前缀合同，需所有者批准**）

系统前缀当前含 memories + 上下文省略通知 + 档位/技能通知（事实 2），跨 run 漂移导致内容地址 provider 每次新 run 前缀未命中。三方案供所有者选定：

- **(a) 参考对齐**：系统前缀仅保留 `persona`（跨 run 字节稳定），owner memory 与 notice 改走**稳定位置尾部块**。**语义变更**：memory 从 system（高 salience）降为尾部，需评估对优先级/遗忘的影响；触及 `memory_system`/`history_request` 冻结合同。
- **(b) 最小风险（推荐先做）**：**不改前缀结构**，仅注入 provider `prompt_cache_key`（D1/D2）。对 slot-keyed provider 获跨 run 复用；内容地址 provider 仍因前缀漂移不命中（＝现状）。零冻结合同改动、零回归。
- **(c) 混合**：系统前缀保留 `persona + 稳定 memory 快照`，新增/变更 memory 以尾部增量补递。最复杂，需独立细化合同。

**推荐分批**：先落 **(b)**（纯 adapter 注入，无冻结合同改动，可立即实现且零回归）；**(a)/(c)** 作为**独立 owner-approved 批**（触及冻结前缀 + 语义变更），不与 (b) 混批。

### D4 重置/generation 边界（无冻结合同改动）

- 直接以 `content_epoch` 作 generation：D1 派生键已含 `content_epoch`，擦除/重置即新桶，天然不跨重置复用（对齐参考 generation 语义）。
- 压缩不推进 `content_epoch`（事实 6）→ 压缩不换桶，与「run 内前缀冻结」（事实 4）一致，正确。

### D5 分叉/委派隔离（无冻结合同改动；K17 forward 依赖）

- 委派子会话独立 `session_id` → 独立 scope，不 merge（事实 7）。
- 未来 K17 反思 cache-parity fork 若需前缀 parity 继承父 scope，参照参考 `_inherited_cache_scope` 显式戳记（不读 DB、不合并分叉树）；**本批不实现**（属 K17 域）。

### D6 fail-closed 降级（无冻结合同改动）

- scope 解析失败 → 回退物理 `session_id` 派生键（或干脆不注入），**绝不阻塞 model call**；镜像参考 `resolve_prompt_cache_scope_safe`。
- 注入点异常不得冒泡阻断调用。

### D7 注入点（无 domain 改动；`adapters/models.py`）

- 在 `_prepare`（事实 8，`:101-152`）按 D2 分类注入 `prompt_cache_key`（经 litellm `extra_body`/原生 `prompt_cache_key`）或 provider 特定头（镜像 `x-opencode-session` 先例）。
- **不改** `messages` 内容/前缀（D3(a) 除外，后者独立批）。
- **不改** 计量/计费：usage 缓存字段照读（事实 1），cost 计算不变。

### 实现记录：D3(b) 路由键注入（2026-11-22，`K01.prompt_cache_route_key`）

- **作用域**：`run_settings` 返回 run 所属 `agent_id`/`session_id`/`content_epoch`（`configuration_repository.py`）；`Settings` 增三个 run-scoped 字段，`settings_value` 对旧调用保持可选（缺失即不注入）。
- **派生键**：`adapters/models.py::prompt_cache_key` = `pc_<sha256("agent|session|epoch")[:24]>`；不可逆、不含明文内部 id；缺失/类型不符 → `None`（fail-closed，不阻断调用）。
- **provider 分类**：`cache_route` 仅对 xAI/Grok slot-keyed 与 OpenAI 系注入；Anthropic/DeepSeek/Gemini 等内容地址 provider 依靠前缀字节 parity，不注入。OpenCode Go 既有 `x-opencode-session` 头保持，不叠加路由键。
- **注入位置**：`_prepare` 的 litellm `extra_body.prompt_cache_key`；`messages`/系统前缀/工具/计量/kwargs 其余字段不变；`caching:False` 保持。
- **无冻结合同改动**：未改 `domain/metering.py` 的 `memory_system`/`history_request`，未改 provider 请求体语义。
- **验证**：新增 `agent/tests/v2/core/test_v2_prompt_cache.py` 6 项（派生稳定/epoch 换桶/不泄露/分类矩阵/注入字节不变/内容地址跳过/OpenCode 头）；model/config/execution/turn-integrity/model-stream 调用方 85 passed；strict 424 文件 0 错误；证据见 [v2-prompt-cache-route-key.json](../../evidence/v2-prompt-cache-route-key.json)。
- **未做**：D3(a)/(c) 前缀重构、D8 真实 provider 缓存命中率验收。

## D8 真实缓存效果验收（单独批，不入本设计批）

- 用既有 usage 读取（事实 1）比对注入前后跨 run 的 hit/miss tokens 变化；真实 provider 调用在隔离凭据/环境下单独验收，**不混入默认离线套件、不触真实账号/计费**（需所有者安排隔离环境）。

## 有界与安全

- `prompt_cache_key` 仅不可逆派生（D1），不暴露内部 id、不含明文平台标识，不新增出网字段泄露面。
- 不改变请求内容语义（D7 除路由元数据外零请求体改动）；`extra_headers` 仍过滤 `authorization`/`x-api-key`（事实 8）。
- 委派隔离不弱化；重置边界换桶不跨擦除复用（D4/D5）；fail-closed 不阻断调用（D6）。
- (b) 批零冻结合同改动、零回归；任一实现批须回退验证 + 调用方 + strict。

## 测试计划（要点，实现批）

- **D1**：派生键跨 run 稳定、含 `content_epoch` 换桶、委派子会话独立、不泄露明文 id。
- **D2**：provider 分类矩阵（xAI/`api.x.ai`/Grok vs Anthropic/DeepSeek/Gemini vs OpenAI）。
- **D7**：slot-keyed 注入、内容地址按需、OpenCode Go 既有头零回归、`messages` 序列化字节不变。
- **D6**：解析失败回退物理、不阻断调用。
- **回退探针**：摘注入实现 → 新增用例全红，静态合同钉留绿。
- **D8**：真实缓存效果单独批，不入默认套件。

## 边界

纯设计合同，无源码/测试/迁移改动。实现分两批：**(b) 路由键注入**（无冻结合同改动，可先落）与 **(a)/(c) 前缀重构**（触及冻结前缀 + 语义变更，需所有者批准）。真实缓存效果单独验收。`whole_*_complete` 保持 false，不提前关闭任何对标项。
