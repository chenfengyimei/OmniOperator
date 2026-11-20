# V2 记忆衰减自动每日遍（K15.decay_tick）

状态：已实现专项通过（2026-11-20，合同先于接线）。承接 [记忆衰减核心](V2_MEMORY_DECAY.md)（K15.decay_core 已实现所有者手动衰减遍），本批把衰减接入 `MaintenanceService.tick` 自动每日运行，学习参考 surfcore `night-schedule.mjs`/`memory-card.js` 的 dailyHour=4 夜间触发 + `needsDailyDecay`（last_decay_date != today）语义，独立实现于 V2 四层。**保守子集**：复用 decay_core 的 forgotten 隐藏态不擦除/protected 永不衰减/DOCUMENT_KINDS 豁免；自动遍仅步进 clarity，绝不擦除、不写 tombstone、不失效会话。

## 范围（本批）

1. **纯领域门规则**（既有 `domain/memory_decay.py` 加，无新源文件）：
   - `DECAY_DAILY_HOUR = 4`（参考 dailyHour，UTC 小时；clock.now() 为 UTC ISO）。
   - `DECAY_TICK_LIMIT = 8`（每 tick 最多处理 agent 数，有界扫描防长尾阻塞）。
   - `should_auto_decay(last_decay_date, today, hour, daily_hour)`：`hour >= daily_hour and last_decay_date != today`（参考 needsDailyDecay + 夜间门；无记录 last_decay_date=None != today 视为待衰减）。
2. **仓储自动遍**（既有 `memory_repository.py` 加 `memory_decay_auto_tick(daily_hour, limit) -> int`）：
   - now=clock.now()，today=now[:10]，hour=int(now[11:13])。
   - 有界候选：`agents enabled=1 LEFT JOIN memory_decay_state`，SQL 过滤 `last_decay_date IS NULL OR != today`，`LIMIT limit`（只取待衰减 agent，已衰减今日者天然排除）。
   - 逐候选 `should_auto_decay` 确认（hour 门）→ 调既有 `memory_decay_pass(agent, force=False)`（复用今日门 409/catchup/一步/隐藏不擦除/审计/state 累计）；捕获 `memory_decay_already_today`（与所有者手动遍竞态）跳过不计；其余错误传播（事务回滚）。
   - 返回本 tick 实际衰减 agent 数；hour<daily_hour 时 should_auto_decay 全 False→0（夜间门前不衰减）。
3. **maintenance 接线**（既有 `application/maintenance.py`）：`MaintenanceService.__init__` 加 `decay_daily_hour: int | None = None`（None=禁用，隔离测试默认）；`tick` 在 background_review 之后、claim 之前加 `if self._decay_daily_hour is not None: decayed = await self._db.write(lambda u: u.memory_decay_auto_tick(self._decay_daily_hour, DECAY_TICK_LIMIT))`，返回值 `len(claims)+processed+decayed`（decayed 独立计，不占 MAX_ACTIVE 槽——同步 DB 写非 durable job）。**不干扰** memory_organization/background_review 槽位 accounting。
4. **composition 装配**（既有 `composition/instance.py`）：`MaintenanceService(..., decay_daily_hour=DECAY_DAILY_HOUR if capabilities is None else None)`——**仅生产路径（capabilities is None）启用**，隔离测试 None 禁用（K08 background_review 引擎触发同先例，避免破坏既有 maintenance tick 计数/费用断言）。
5. **ports**：UnitOfWork Protocol 加 `memory_decay_auto_tick(daily_hour: int, limit: int) -> int`。

## 默认与可观测

- **生产默认 ON**（maintenance 驱动，参考 parity；K08 后台学习同为 maintenance 自动驱动先例）。每日 UTC 4 点后每 agent 一次（今日门保证幂等），protected（importance>=65 或 pinned）永不衰减、forgotten 仅隐藏不擦除、所有者 forget 仍是唯一擦除路径——**自动遍安全可逆**，无数据丢失。
- 所有者经既有 `GET /agents/{id}/memory/decay`（decay_state：last_decay_date/decayed/forgotten 累计）观测自动遍效果；`POST .../memory/decay {"force":true}` 手动补遍不变。**无新路由/无新模型工具/无新表/无新迁移**（复用 decay_core 的 memory_decay_state + v0047）。
- 本批不加 enable/disable 设置项（列后续：所有者侧自动衰减开关/ dailyHour 配置）；当前生产 ON、隔离测试 OFF。

## 明确不做（列后续）

- 所有者侧自动衰减开关/dailyHour 可配置（settings 集成）；按 agent 时区/local hour（当前 UTC clock hour）；重要性启发 scoreMemory/isInitial（decay_core 已列）；双卡分离/注入 clarity 裁剪/容量告警（decay_core 已列）；衰减通知/所有者告警。
- 真实长期衰减曲线/多 agent 长尾性能验收另验。

## 验证计划

领域 should_auto_decay 门全覆盖（hour 边界/date 相等/None）；仓储自动遍（夜间门前 0、门后衰减待衰减 agent、今日门幂等第二遍 0、有界 limit、disabled agent 跳过、protected 不衰减、与手动遍竞态 409 跳过、文档豁免）；maintenance tick 集成（decay_daily_hour=None 禁用零回归、=4 启用 sweep、返回值含 decayed、不占槽）；composition 生产启用/测试禁用；既有 maintenance/memory 全专项 + 迁移守卫零回归。strict 本批零错误（余 5 既有 repository.py 不追逐）；ruff。

## 验证结果（2026-11-20）

- **专项**：`uv run --no-sync pytest -q -n 0 tests/v2/core/test_v2_memory_decay_tick.py`（from agent/）→ **15 过 / 0 失败**（15.07 秒）。覆盖 should_auto_decay 门参数化 6 例（hour<4 False/hour==4 True/hour>4 True/last==today False/last None True/last 昨日 True）、仓储夜间门前 0、门后衰减、今日门幂等第二遍 0、次日再衰减、有界 limit 跨 agent（limit2→2,1,0）、disabled agent 跳过、手动遍今日后自动遍 0、protected/document 永不褪色、maintenance tick 默认禁用（tick()==0）、启用 sweep（tick()==1 后 0）、composition 生产=4/隔离=None 接线（NoModel）。
- **调用方回归**：`uv run --no-sync pytest -q -n 0 <10 文件：maintenance/maintenance_migration/memory_decay/memory_decay_tick/memory_organization/memory_semantics/lifecycle/kernel/execution/routing>`（from agent/）→ **107 过 / 0 失败**（190.53 秒）。decay sweep 在隔离测试经 `decay_daily_hour=None` 门控 OFF，既有 maintenance tick 计数/费用断言零回归。
- **strict**（from repo ROOT，`pyrightconfig.strict.json`）：**412 文件，5 错误均既有他会话已提交 repository.py（6bcb0e98 L633/659/710），本批 7 文件零错误，0 警告**。本批无新源文件（domain/repository/maintenance/ports/instance 均既有文件加方法），include 无需新增。
- **ruff**：check + format 通过（5 源文件 + 1 测试）。
- **聚合索引**：`scripts/project_index.py` 运行；无新增/删除源文件（仅既有文件加方法 + 新测试/合同/证据），`docs/index` 聚合随既有纪律暂缓（并行 UI 会话活跃）。
- **task_status**：`scripts/task_status.py --check` exit 0。
- 未全量；未触真实模型/账号/设备/native/72h；当前 Windows 服务与真实数据库未停止或由测试升级。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。