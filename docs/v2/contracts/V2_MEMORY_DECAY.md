# V2 记忆时间衰减与巩固核心（K15.decay_core）

状态：已实现专项通过（2026-11-20，合同先于接线；接管会话完成收尾回归修复）。学习参考 surfcore `src/memory-card.js`（固定提交 59ec3066，纯逻辑衰减/巩固引擎）的 retention 评分、清晰度层级、每日一步、漏日补追、召回强化与硬遗忘谓词，独立实现于 V2 四层 memory 链。**保守子集**：V2 擦除纪律（tombstone/会话失效/证据删除仅所有者 forget）优先于参考自动删除——`forgotten` 是**隐藏态不是擦除**。

## 范围（本批）

1. **纯领域规则**（新文件 `domain/memory_decay.py`，零 I/O）：
   - `CLARITY_ORDER = ("vivid","clear","fuzzy","trace","forgotten")`（参考原序，末级 forgotten 非 deleted）。
   - `retention_score(importance, recall_count, age_days, since_seen_days, protected)`：`importance + min(12, recall_count*2) + (18 if age_days<=7) + (8 if since_seen_days<=2) - min(80, max(0, age_days-7) * rate)`，rate=0.12（protected）/0.35；地板 protected→≥55、age≤7→≥50（参考原值）。
   - `target_clarity(retention, pinned)`：pinned→vivid；≥68 vivid、≥46 clear、≥24 fuzzy、≥12 trace、否则 forgotten（参考原切分）。
   - `hard_forget(age_days, importance, protected)`：`age_days>60 and importance<50 and not protected`（参考谓词；protected 代理见下）。
   - `decay_step(current, target)`：每遍**最多一步**（上行/下行均 1 级，参考 applyDailyDecay）；`catchup_steps(last_decay_date, today)`：floor 日差 clamp [1,30]，无记录→1。
   - **protected 代理**：V2 无 tags/isInitial，`protected = pinned or importance>=65`（参考 ownerImportant=minOwnerImportance 65 的代理；isInitial 初始窗口不实现）。
2. **持久层加性列**（`memory_entries`，v0047 幂等 ALTER，v0045 先例）：`importance INTEGER NOT NULL DEFAULT 50 CHECK 1..100`、`clarity VARCHAR(12) NOT NULL DEFAULT 'vivid' CHECK in CLARITY_ORDER`、`recall_count INTEGER NOT NULL DEFAULT 0 CHECK 0..999`、`decay_steps INTEGER NOT NULL DEFAULT 0 CHECK 0..999`、`pinned BOOLEAN NOT NULL DEFAULT 0`、`last_seen_at VARCHAR(40)`。新表 `memory_decay_state`（scoped，每 agent 唯一行：last_decay_date VARCHAR(10)、last_decay_at、decayed/forgotten 累计计数；identity+keep 触发器）。表数 142→143。
3. **remember 加性参数**：`importance`（1..100，缺省 50）/`pinned`（缺省 False）；初始 clarity：pinned 或 importance≥65→vivid 否则 clear（参考 initialClarity）；last_seen_at=创建时刻。模型工具 `memory_remember` 增**可选** importance 参（加性协议，timestamps 先例；pinned 仅所有者侧）。既有 remember 调用方缺省零回归。
4. **召回强化**：`memory_touch(agent_id, ids)`（write）：≤5 条/次（参考 touch 上限），recall_count=min(999,+1)、last_seen_at=now；仅 live 非文档条目。接线三处召回路径：memory_tools 无语法义分支（同事务 write）、engine.search_memory 两分支（semantics 结果与 fallback，read 后 write）。层级回升经每日衰减遍上行一步实现（参考同）。
5. **所有者衰减遍**：`memory_decay_pass(agent_id, force=False)`（write）：
   - 门：last_decay_date==今日 且非 force → `memory_decay_already_today` 409（参考 needsDailyDecay；**无 dailyHour 门**——本批仅所有者手动触发，自动 tick 列后续）。
   - steps=catchup_steps；每遍对每条 live 非文档条目（DOCUMENT_KINDS profile/resident **不参与衰减**）算 hard_forget→target forgotten，否则 retention→target_clarity；向 target 移动**一步**；重复遍至 steps 或无变化（漏日补追）。
   - `forgotten` 仅置 clarity 隐藏：**不擦除**、不写 tombstone、不失效会话（所有者 forget 仍是唯一擦除路径，V2 纪律更严于参考自动删除）；下行计入 decayed、到达 forgotten 计入 forgotten 累计；审计 `memory.decayed`。
   - `search_memory` 两分支（FTS/LIKE）排除 clarity='forgotten'；文档/组织谓词不变。
6. **所有者面**：Application `memory_decay_state`/`memory_decay`；REST `GET /agents/{agent_id}/memory/decay`（态）与 `POST .../memory/decay`（执行，body 可选 `{"force": true}` strict）。**无模型侧衰减工具**。

## 明确不做（列后续批次）

- 重要性启发式 scoreMemory（owner+42/emotion+0–24/first-meet+30 等依赖渠道说话人上下文，V2 remember 无该上下文；本批 importance 由写入方显式给出缺省 50）；isInitial 初始 30 天窗口概率分层。
- 自动每日 tick（maintenance/scheduling 集成，dailyHour=4）；双卡分离（衰减卡注入预算裁剪 + 全量 JSONL append-only 归档）；注入渲染按 clarity 截断（200/130/42/22）；容量告警/auto-compact（参考默认 unlimited 下 auto-compact 实际惰性，已记录漂移）；touch 分词命中判定（参考中文 bigram，本批强化作用于**实际返回的召回结果**而非重新分词匹配，更精确）。
- 真实模型 importance 自评质量、长期衰减曲线验收另验。

## 验证计划

领域规则全覆盖（公式/地板/切分/谓词/一步/补追 clamp）；repository（初始 clarity、touch 上限/cap、衰减遍一步/补追/今日门/force、forgotten 隐藏不擦除、文档豁免、pinned 恒 vivid、搜索排除）；v0047 迁移幂等+反射+守卫 143；REST 全链；调用方 memory 全专项 + 迁移守卫。strict 本批零错误（余 5 既有 repository.py 不追逐）；ruff。

## 接管收尾回归修复（2026-11-20）

接管会话发现前会话在途批的 `remember()` 加性列与 `search_memory` 召回强化破坏既有调用方，按既有加性列先例修复（**生产源码不变**，仅测试夹具/断言口径）：

1. **旧 schema 夹具播种**（`omni_test_support/v2/v2_legacy_seed.py`）：`PreSkillSeedRepositories._record` 对 `memory_entries` 剥离 v0047 六加性列（importance/clarity/recall_count/decay_steps/pinned/last_seen_at），使 pin 旧版本（v17/v18/v19/v20/v21/v32/v33/v34/v36）的 `remember()` 播种只插原始列（升级后 v0047 以 server_default 补列）；`preserved_rows` 增 `excluded_memory_columns` 默认剥离同六列，使升级前后行投影比较忽略 v0047 新列（`execution_kind`/`excluded_action_columns` 同先例）。覆盖全部 LegacySeed/PreSkill/PreRouting 派生夹具。
2. **action_review v19 夹具**（`test_v2_action_review_migration.py`）：baseline 原直接用真 `Repositories` 于 v2_0019 播种 `remember()`，改用 designated `LegacySeedRepositories`（"pre-v19 fixture seeding only"），移除随之未用的 Repositories 导入。
3. **事实真相口径**（`test_v2_memory_organization.py::test_proposals_preserve_fact_truth...`）：`original_truth` 投影 memory_entries 时排除 recall_count/last_seen_at——`search_memory` 召回强化（touch）合法变更这两统计列；事实真相=content/kind/revision/importance/clarity/pinned/decay_steps/content_hash/person_id 仍全列比较，组织 submit/decide/undo 不变性**未被放宽**。

**既有失败不追逐（非本批引入，已核实 decay 无关）**：6 个 pin `v2_0039` 旧迁移测试（memory_semantics×2/routing×1/skills×2/media×1，schema_version 滞后）；`execution_pruning[cost]`（retention fingerprint 哈希 runs/actions/run_events，`settle_call` 自 `6bcb0e98` 实时调用链批发 `model.completed` 事件改变 fingerprint；decay 不触及这三表/成本/事件路径，故与本批无关）；media_upload DDL 回滚（zoom_foundation 批已记录既有）。strict 余 5 错误均既有他会话已提交 `repository.py`（6bcb0e98 L633/659/710）。

## 验证结果（2026-11-20，base 8297836b）

- decay 专项 **29 过/0 失败**（18.49 秒，`tests/v2/core/test_v2_memory_decay.py`）。
- 广调用方回归 **38 文件 341 过/17 失败**（922.57 秒，覆盖全部 shared-helper 调用方+memory+迁移守卫+架构/执行/内核/路由）；17 失败均既有非本批：7 pin `v2_0039`（含 images_migration v31）、7 event_pruning（`6bcb0e98` 新增 action.executing/model.started 事件种未入 `event_retention_targets` kind CHECK 白名单 schema.py:1979/v0016:40）、1 execution_pruning[cost]（settle_call 事件改 fingerprint）、2 media_upload DDL。接管首轮 decay 引入的失败（旧 schema remember INSERT importance 错+search 强化破坏事实真相比较+extensions 等 LegacySeed 播种）经上述 3 处夹具/口径修复**全转绿，decay 引入失败 0**。
- strict **412 文件 5 错误 0 警告**，5 错误均既有 repository.py（6bcb0e98 L633/659/710）；新源文件 memory_decay.py/v0047.py 已入 include 且零错误（410→412）。
- ruff check/format 通过（9 decay 源/测试 + 3 接管修复文件，均 unchanged）。
- 表计数守卫 **142→143**（14 处：11 测试+3 共享助手）；v0047 幂等+反射+identity/keep 触发器+downgrade 拒绝专项过。
- 未全量、未真实模型/账号/设备/72h；运行中 Windows 服务与数据库未停止或升级。聚合 `docs/index` 暂缓（并行 UI 会话活跃）。