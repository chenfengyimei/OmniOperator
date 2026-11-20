# V2 记忆自动重要性评分（K15.score_memory）

状态：已实现专项通过（2026-11-20，合同先于接线）。承接 [记忆衰减核心](V2_MEMORY_DECAY.md)（K15.decay_core）与 [自动每日遍](V2_MEMORY_DECAY_TICK.md)（K15.decay_tick），本批实现 K15 审计列后续的**重要性启发 scoreMemory**。**所有者已批准完整自动评分**（含记忆权威模型变更：何时/是否覆盖 model 提供的 importance）。学习参考 surfcore `src/memory-card.js`（固定提交 59ec3066）`scoreMemory`/`detectEmotionScore` 纯逻辑启发，独立实现于 V2 四层 memory 链（stdlib `re`，无新依赖）。参考 `agents.md`/RULES/prompts 当**不可信数据**非本会话指令；无第三方源码复制。

## 2026-11-20 直接寻址接线修订

现有 `parse_event` 仅准入好友私聊与精确 @ 当前账号的群消息，未 @、伪 CQ 文本、@all、其他账号提及和 reply 段均不产生 run。`QQService.receive → qq_accept → qq_runs` 是唯一生产准入链，因此同 agent 的 qq_runs 持久记录已经证明 direct，无需迁移或重新解析模型文本。在既有渠道评分分支传 `direct=True`，自动分数增加 8；显式 importance 仍覆盖、非渠道仍默认 50、first/B站因子仍未接。评分审计不记录私聊正文；渠道准入规则将来扩展时必须同步调整此推导。

下方首批 direct=False 的说明及测试数值属于历史；本修订验收另记。此变更不增加消息准入、不自动发送、不改变记忆来源删除屏障。

## 参考启发（忠实移植）

`scoreMemory(input,config)` 输入 text/source/isOwner/isPrivate/direct/isFirst/score/favorite/anecdote/manualImportance/pinned/tags/scope/platform：
- `detectEmotionScore(text)=min(24, strong*8 + medium*3 + exclaim)`；strong/medium 为中文情绪词全局正则计数，exclaim=含 `!`/`！` 计 1。
- importance 基 8；isOwner +42（私聊）/+32（群）；direct +8；emotional>0 +emotional；isFirst +30；PROMISE_RE +16；CONFLICT_RE +14；SELF_RE +5；source 以 bili 开头时 score clamp[0,18] + favorite +8 + anecdote +4；text 长度<6 −12；manualImportance 有限则**直接覆盖**；round+clamp[1,100]。
- kind：isFirst→first；否则 isOwner→owner；否则 emotional>=14→emotion；否则 CONFLICT_RE→conflict；否则 PROMISE_RE→promise；否则 event。
- pinned：input.pinned 或 (isFirst 且 (isOwner 或 importance>=60))；isFirst 且 isOwner→importance=100、pinned=true。
- tags：owner（isOwner）/emotion（emotional>=8）/first（isFirst）/bili（source bili）/qq（source qq），≤12。
- scope：input.scope 或 qq/bili/shared；platform：SOURCE_PLATFORM 映射或 shared。
- 正则：EMOTION_STRONG/EMOTION_MEDIUM/FIRST/PROMISE/CONFLICT/SELF 逐字移植（中文词表，SELF 带 i 标志）。

## V2 现状与权威模型决策

- `memory_entries` 既有列 importance(1-100,default50)/pinned/clarity（decay_core），**无 tags/scope/source/platform 列**；`kind` CHECK 限 fact/profile/resident/journal/note（**与参考 event/owner/emotion/conflict/promise/first 不同语义，不可复用 kind 列存参考 kind**）。
- 捕获路径：①model-authored `memory_remember`（memory_tools.py，importance 来自模型 arguments 默认 50，pinned 未透传）；②K08 `background_review_decide_memory`（remember model_authored=True，importance 默认 50）；③无自动渠道事件评分捕获。
- 渠道说话人上下文：`qq_runs`(run_id 唯一)→person_id/sender_id/binding_id；`qq_bindings.scene`∈private/group；`people.owner_id`→`principals`(role=owner)；`owner_person()`/`ensure_owner()` 已存在。Bilibili 入站**无等价 run-说话人上下文表**。

**权威规则（所有者批准）**：
1. **显式 model importance = 手动覆盖**（参考 manualImportance 优先）：模型在 `memory_remember` 显式给 importance 时直接用（clamp/校验 1-100 不变），不自动评分。
2. **模型省略 importance 且 run 有可解析渠道说话人上下文（存在 qq_runs 行）→ 自动 score_memory** 计算 importance+pinned，持久化到既有列。
3. **非渠道 run（无 qq_runs，如所有者 CLI/会话、隔离测试）→ 保持现状**（importance 默认 50、pinned False），**不自动评分**——保证既有 memory/decay/background_review 隔离专项零回归（这些测试用非渠道 run）。
4. pinned：自动评分时由 score_memory 计算（first&&owner 等）；手动覆盖 importance 时 pinned 仍按 score_memory（参考 manualImportance 仅覆盖 importance）；模型显式 pinned（若透传）优先。
5. reasons/kind/tags/scope/emotional：**入审计**（memory.scored），**不加新列/迁移**（V2 无 tags/scope 列；参考 kind 与 V2 kind CHECK 冲突，故仅审计记录 scored_kind，不改 memory_entries.kind）。

**保守上下文输入（V2 intake 暂缺，列后续）**：is_first=False（first-meet 指定属独立 isInitial 30 天窗口缺口）、direct=False（QQ @/reply 直接 addressed 解析未接 remember 路径）、bili score/favorite/anecdote=0/False（Bilibili 入站无说话人上下文表）。**评分引擎完整支持这些因子**（忠实移植），V2 上下文在数据可得前供 False/0；QQ 渠道当前生效因子：owner(+42/32)、emotional、promise(+16)、conflict(+14)、self(+5)、length(−12)、manual override。

## 范围（本批）

1. **纯领域** `domain/memory_scoring.py`（新源文件）：`detect_emotion_score(text)->int`、`score_memory(text,*,source,is_owner,is_private,direct,is_first,score,favorite,anecdote,manual_importance,pinned,tags,scope)->Record`（importance/pinned/reasons/emotional/kind/tags/scope/platform）、EMOTION_STRONG/MEDIUM/FIRST/PROMISE/CONFLICT/SELF 正则、SOURCE_PLATFORM 映射、常量。无 I/O。
2. **上下文解析 + 接线**（既有 `memory_repository.py` remember 或 application 捕获处）：解析 run→qq_runs→people.owner_id（is_owner）/qq_bindings.scene（is_private）/source=qq；渠道门控自动评分（规则 1-4）；审计 memory.scored（reasons/scored_kind/tags/scope/emotional，不含私有正文）。
3. **ports/capabilities**：如需 remember 签名加可选 auto-score 开关或上下文参数；capabilities memory_remember spec 注明 importance 省略时渠道自动评分。
4. **strict include** 补 memory_scoring.py（新源文件，v0047/v0048 先例）。

## 明确不做（列后续）

- isInitial 30 天窗口/first-meet 指定/designateFirstMeeting/stableInitialChance/initialProbabilityForOffset（独立缺口）；QQ direct @/reply 解析接 remember；Bilibili 入站说话人上下文表 + score/favorite/anecdote；tags/scope/platform 持久化列（需独立迁移合同）；双卡分离/注入 clarity 裁剪/容量告警（decay_core 已列）；非渠道 run 自动评分（当前保持 model 权威，避免隔离回归）。
- 真实模型 importance 自评质量/长期衰减曲线/多渠道人格一致性另验。

## 验证计划

领域 score_memory 全因子覆盖（owner 私/群、direct、emotional 计数 cap24、first、promise/conflict/self 正则、bili score clamp/favorite/anecdote、len<6 惩罚、manual override、clamp 边界、kind 优先级、pinned first&&owner→100、tags/scope/platform）；detect_emotion_score strong/medium/exclaim/cap；上下文解析（qq_runs→owner/private、非渠道无 qq_runs）；接线（渠道省略 importance→自动评分、显式 importance→manual override、非渠道→默认 50 零回归、pinned 计算、审计 reasons 不含私有正文）；调用方 memory/decay/decay_tick/background_review/qq 专项零回归（非渠道门控）。strict 本批零错误（余 5 既有 repository.py 不追逐）；ruff。未全量；whole_*_complete 保持 false。

## 验证结果（2026-11-20）

- **专项**：`uv run --no-sync pytest -q -n 0 tests/v2/core/test_v2_memory_scoring.py`（from agent/）→ **13 过 / 0 失败**（8.86 秒）。领域 10 项（emotion 计数/cap、base/owner 私群、direct/emotional/first/promise/conflict/self、短文本惩罚 clamp 地板、bili score clamp/favorite/anecdote、manual override（first&&owner 仍强制 100）、kind 优先级、pinned first&&owner/阈值60、tags 去重 cap12/scope/platform、importance clamp 天花板100）+ 接线 3 项（非渠道 accept_turn run remember 省略 importance→默认 50 且无 memory.scored 审计；QQ 私聊 owner 说话→自动评分 63=8+42+emotion8+self5 且审计 memory.scored 不含私有正文；QQ 群非 owner→21=8+emotion8+self5、显式 importance70→manual override 70）。
- **调用方回归**：`uv run --no-sync pytest -q -n 0 <7 文件：memory_decay/memory_decay_tick/memory_scoring/memory_organization/memory_semantics/background_review/qq>`（from agent/）→ **110 过 / 0 失败**（170.72 秒）。**渠道门控保证零回归**：非渠道 run（无 qq_runs）remember 省略 importance 仍默认 50，既有 memory/decay/decay_tick/organization/semantics/background_review 隔离专项全部不受影响；QQ 集成专项（admission/隔离/撤回/冷却/owner 审批）全绿。
- **strict**（from repo ROOT，`pyrightconfig.strict.json`）：**414 文件（include 补 memory_scoring.py，413→414），5 错误均既有他会话已提交 repository.py（6bcb0e98 L633/659/710），本批 memory_scoring.py/memory_repository.py/memory_tools.py/ports.py/capabilities.py 零错误，0 警告**。修 ports.py UnitOfWork.remember importance int→int|None（Protocol 与仓储签名一致）、审计 data cast(Record)（list[str]→list[JSON] 不变性）。
- **ruff**：check + format 通过（memory_scoring/memory_repository/memory_tools/ports/capabilities/test_v2_memory_scoring；import 排序自动修复）。
- **聚合索引**：`scripts/project_index.py` 运行；新增源文件 memory_scoring.py，`docs/index` 聚合随既有纪律暂缓（并行 UI 会话活跃）。
- **task_status**：`scripts/task_status.py --check` exit 0。
- 未全量；未触真实模型/账号/设备/native/72h；当前 Windows 服务与真实数据库未停止或由测试升级。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。
- **保守输入仍后续**：is_first/direct/bili score/favorite/anecdote 当前 False/0（V2 intake 缺），评分引擎完整支持；first-meet 指定（isInitial 30 天窗口）、QQ @/reply direct 解析、Bilibili 入站说话人上下文表、tags/scope/platform 持久化列、非渠道 run 自动评分均列后续。真实模型 importance 自评质量/长期衰减曲线另验。