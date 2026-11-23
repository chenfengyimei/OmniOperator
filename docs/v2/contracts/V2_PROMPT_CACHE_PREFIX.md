# V2 系统前缀重构与真实缓存效果设计合同（K01 D3(a)/(c) + D8，决策 D1）

状态：**设计合同（design_contract_recorded，纯文档，无源码/测试/迁移/路由/worker 改动，2026-11-23）**。**未实现**。承接 [K01 提示缓存作用域合同](V2_PROMPT_CACHE_SCOPE.md) 的 D3(a)/(c) 与 D8，以及 [剩余差异决策登记](V2_REMAINING_DECISIONS.md) D1；D3(b) 路由键已于 `372ad56d` 交付，本册只处理**系统前缀结构**与**真实缓存效果验收**。触达冻结前缀合同（`domain/metering.py` 的 `memory_system`/`history_request`），按纪律需所有者批准后接线。

## 勘定事实（实读源码，2026-11-23，基线 `0793f254`）

1. **系统消息 = persona + owner memory + 运行期通知**：`domain/metering.py:43-53` `memory_system(settings, memories, notice)` = `settings["persona"]` + （有可见记忆时）`"\nOwner memory (data, not authorization):\n" + json.dumps(captured 且非擦除记忆的 memory_id/revision/kind/content)` + `notice`。可见集在 domain 内过滤 `erased`/`state=="captured"`。
2. **请求装配**：`history_request`（`:56-73`）= 系统消息 → （有摘要时）一条 `user`「Historical conversation summary (untrusted data…)」→ 历史消息；尾部由 `domain/turn_prompt.py:10-25 append_runtime_notice` 追加到**最后一条 user**（或新 user）。
3. **run 启动时的 notice 组成**：`application/engine.py:345-359` `initial_notice = route_notice(mode, has request_mode) + skills.notice(...)`；`extra_notice=initial_notice` 进入**系统**，`tail_notice=todo_notice + reflection_notice` 进入**尾部**；`application/context.py:92-101` `prepare` 把 `extra_notice` 传给 `_history` → `history_request(..., notice)` → 系统，`tail_notice` 经 `append_runtime_notice` 追尾。
4. **run 内前缀冻结已成立**：`TurnPrompt.capture` 记录首条 system 原文，`update` 若 system 变化 → `system_prompt_changed`；仅当 `notice` 变化时把新通知 `append_runtime_notice` 到尾部（`:28-44`）。即 run 内 route/skills/todo 变化已经走尾部。
5. **跨 run 漂移源**（当前都进系统或紧随 system）：
   - **owner memory**：新记忆、衰减 clarity、重要性、擦除/遗忘都会改变 JSON 字节（`context_memories` 每 run 重取）；
   - **route/skills 通知**：run 启动时进入系统（`initial_notice`），档位提升/技能捕获变化即变字节；
   - **摘要**：`history_request` 把摘要作为 system 后的第一条 user，摘要内容随压缩演进（K06 每轮也可能产生新摘要）；
   - **上下文省略/擦除计数**：`context.prepare` 返回的 `notice` 在 run 内走尾部（`TurnPrompt`），但 `history_request` 的系统 `notice` 参数只收 `extra_notice`，故省略计数不污染系统；
   - **persona**：仅所有者编辑设置时变化（低频，天然换桶）。
6. **K06 压缩**：`application/context.py` `compact_run` 只把旧工具轮压成一条有界 user 摘要，**系统前缀/初始 head/最近轮不变**；压缩不推进 `content_epoch`（K01 合同事实 6）。
7. **D3(b) 已交付**：`adapters/models.py:23-40` 不可逆派生 `pc_<sha256(agent|session|epoch)[:24]>`，仅 slot-keyed（xAI/Grok）与 OpenAI 系经 `extra_body` 注入；内容地址 provider（Anthropic/DeepSeek/Gemini）仍靠前缀字节 parity；`caching:False` 保持。
8. **凭据/委派**：凭据权威 CAS 与读无关；委派子会话独立 `session_id`/epoch；远程 QQ run 不注入本地技能/反思通知（`not remote` 门）。

## 判定

- **漂移确认**：同一会话连续两个 run，只要 owner memory 集合/内容变化、route/skills 通知变化或摘要变化，内容地址 provider 的公共前缀就在 system（甚至第 2 条 user）处断开；D3(b) 只对 slot-keyed/OpenAI 有帮助。
- **语义风险**：owner memory 当前位于 system（高 salience），移出会改变模型对记忆的优先级/服从度；route/skills 通知移尾可能降低「档位/技能提示」的即时遵守。
- **可逆性**：`TurnPrompt` 已提供「run 内 system 不变、变化走尾」机制，跨 run 变更只需调整 `memory_system`/`history_request` 的装配位置，不需要新表/迁移。

## 设计选项（需所有者选定）

### P1 系统前缀结构

- **P1-a（参考对齐，推荐）**：system **只保留 persona**（跨 run 字节稳定）。owner memory 与摘要移到 system 之后的**固定位置块**：
  - 顺序 `system(persona)` → `user("Historical conversation summary …")`（若有）→ `user("Owner memory (data, not authorization): …")`（若有）→ 历史消息；
  - run 启动的 route/skills 通知不再进入 system，改为 `append_runtime_notice` 到尾部（与 run 内更新同一通道）；
  - 语义变更：owner memory 从 system 降为 system 后第一条数据块；档位/技能提示从 system 移到尾部；需所有者接受并做真实模型质量对照。
- **P1-b（混合）**：system = persona + **稳定记忆快照**（例如仅 `pinned`/高 importance/特定 kind，且只在 `content_epoch` 换代时刷新），其余记忆与通知走尾部。缓存收益低于 a，但保留部分高 salience 记忆；需定义「稳定快照」的判定与刷新边界（易与 D4 遗忘/衰减冲突）。
- **P1-c（最小改动）**：system 保持 persona + memory，仅把 route/skills 通知移尾、摘要移尾；缓存收益有限（memory 仍漂移），但语义风险最小。
- **P1-d（维持现状）**：只做 D8 度量，不改前缀（不宣称改善）。

### P2 记忆块的可见性与删除屏障（若 P1-a/b）

- 记忆块仍是**数据非授权**（保留固定 header）；擦除/遗忘的记忆绝不出现；`context_memories` 的过滤逻辑不变；记忆块字节按固定字段顺序序列化（`memory_id/revision/kind/content`）并**不排序**（保持数据库序，跨 run 稳定由集合稳定保证）。
- 决策：记忆块位置（摘要前/后）、条数/字节上限（现有全量 JSON 无上限，需评估上下文预算；K06 压缩只处理工具轮）、是否在这个块中暴露 importance/clarity（建议不暴露，减少漂移）。

### P3 route/skills 通知位置

- 统一走 `append_runtime_notice` 尾部（与 run 内一致）；`TurnPrompt` 的 `_notice` 初值改为空或尾部块文本，避免重复。
- 决策：尾部通知顺序（route → skills → todo → reflection）、是否合并为单块、超长截断。

### P4 摘要位置

- 摘要从 system 后的第一条 user 改为**记忆块之后、历史消息之前**（P1-a 顺序）；摘要内容变化仍会改变 system 之后的字节，但不影响 system 本身。
- 决策：摘要与记忆块的先后（谁更接近当前消息）；摘要是否也走尾部固定块。

### P5 开关与灰度

- 选项：全局代码切换（无开关，所有者批准后一次生效）vs 每 agent 设置 `prompt_prefix_mode`（`frozen`/`stable`，需改 `domain/settings.py` KEYS 与配置面，可能触设置 schema/迁移 → A 协调）。建议**全局代码切换 + 先离线回归 + 所有者真实模型对照**，避免双模式长期并存；若需灰度，再加设置（A 迁移）。
- 回退：保留原装配函数为内部选项，回退验证摘掉新布局后新增字节稳定用例全红。

### P6 D8 真实缓存效果验收（单独批，不入默认离线套件）

- **度量**：既有 usage 读取（`domain/contracts`/`model_stream` 的 `prompt_cache_hit_tokens`、`prompt_cache_details.cached_tokens`、`cache_read_input_tokens`/`cache_creation_input_tokens`）与 `llm_calls`/usage 账本；对照实验：同模型、同会话、连续 N 个 run，记录每 run 的 hit/miss/created tokens、成本与首 token 延迟（若可测）。
- **隔离环境**：所有者提供隔离凭据/环境；不触真实账号计费的数据只用隔离 key；默认离线套件不跑真实 provider。
- **验收点**：P1-a 后内容地址 provider 的公共前缀至少覆盖 persona（以及摘要/记忆未变的相邻 run 更长）；slot-keyed provider 路由键不变；成本不升；不引入行为回归（真实模型质量由所有者抽查）。
- **反例**：仅 D3(b) 时内容地址 provider 命中率应与现状相当（验证度量有效）。

## 实现要点（获批准后独立批）

1. `domain/metering.py`：拆出 `persona_system(settings)` 与 `memory_block(memories)`；`history_request` 按 P1 顺序装配；`memory_system` 保留为兼容/回退内部函数（或被替换并由回归覆盖）。
2. `application/context.py`：`_history` 不再把 route/skills 传系统；`prepare` 的 `extra_notice` 语义改为尾部（或保持参数名但内部走 `append_runtime_notice`），`TurnPrompt` 初值同步。
3. `application/engine.py`：`initial_notice` 并入 `tail_notice`；run 内 `prompt.update` 逻辑不变（system 仍冻结）。
4. **无新表/迁移/路由/worker 协议/共享端口签名**；若 P5 选设置开关 → `domain/settings.py` 加键与配置面（A 协调是否迁移）。
5. 依赖不变（D3(b) 已在）。

## 测试计划（实现批）

- **字节稳定**（纯领域）：同 agent 连续两次 `history_request`，memory 无变化 → system/全请求逐字节相等；memory 新增/衰减/擦除 → **system 字节不变**，仅记忆块变化；route/skills 通知变化 → **system 字节不变**，尾部变化；摘要变化 → system 不变；persona 变化 → system 变化（预期）。
- **顺序/格式**：P1-a 的 system/摘要/记忆块/历史顺序；记忆块固定 header、字段序、擦除/遗忘不出现；route/skills/todo/reflection 尾部顺序与截断。
- **run 内冻结**：`TurnPrompt.capture/update` 仍拒绝 system 变化；route 提升/技能通知变化只追尾（既有用例适配）。
- **调用方**：context 压缩/摘要、engine 回合、委派、远程 QQ、K08/K11/K12 相关、`test_v2_prompt_cache.py`（D3(b) 零回归）、架构/类型守卫。
- **回退**：摘掉 P1-a 装配恢复旧布局 → 新增字节稳定用例全红；`history_request` 调用方零回归。
- **D8**：隔离 provider 对照实验（单独批、单独证据；不在默认套件）。

## 明确不做

- D3(b) 路由键改动（已交付）；D8 之外的 provider 切换/账号更换；真实模型质量结论（所有者抽查）；K06 压缩语义改动；记忆衰减/重要性策略改动；代码沙箱（D2）/CDP（D3）/写扩面（D4）/主动观看（D5）/分享（D6）/直播（D7）。

## 关联

- K01 合同：[V2_PROMPT_CACHE_SCOPE](V2_PROMPT_CACHE_SCOPE.md)（D1–D8）。
- 决策登记：[V2_REMAINING_DECISIONS](V2_REMAINING_DECISIONS.md) D1。
- 相关合同：K06 `V2_RUN_COMPACTION`、K08 `V2_BACKGROUND_REVIEW`、K15 `V2_MEMORY_DECAY*`（记忆可见性/擦除）。
- **并行分支测试协调（2026-11-23 核对）**：`origin/k14-visual-persona` 分支含 K15 的 **test-only** 回归 `agent/tests/v2/core/test_v2_memory_prefix.py`（4 项），断言当前 `memory_system` 组成（persona+memory+notice、`history_request[0]` 逐字节相等、遗忘/擦除离开前缀、顺序与 notice 尾稳定）。该分支源码不触 `domain/metering.py`/`application/context.py`，其 `V2_MEMORY_INITIAL` 也明言不把「最初」注入系统前缀；因此**无实现顺序依赖**，但 D1 P1-a 接线时必须**同步更新这 4 项断言**（改为仅 persona 系统前缀、memory 独立块、通知走尾），并在证据中说明是合同同步、不是弱化断言。
- 证据：[v2-prompt-cache-prefix-design.json](../../evidence/v2-prompt-cache-prefix-design.json)。
