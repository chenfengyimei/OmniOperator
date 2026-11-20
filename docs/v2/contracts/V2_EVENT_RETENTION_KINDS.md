# V2 事件保留 kind 白名单对齐（event_retention_targets CHECK 修复）

状态：已实现专项通过（2026-11-20，合同先于接线）。**既有缺陷修复批**（非新对标项）：他会话已提交 `6bcb0e98` 在实时调用链新增 `action.executing`/`model.started`/`model.completed` 三种事件种，并同步扩展了纯领域权威集合 `domain/event_retention.py` `KINDS`（12 种），但**持久层 `event_retention_targets.kind` CHECK 白名单（`schema.py` 与冻结迁移 `v0016`）未同步扩展**（仍 9 种）。后果：事件保留裁剪 `plan_event_retention` 把含新种的 run 事件写入 `event_retention_targets` 时触发 CHECK 违约 `OperationalError`，`event_pruning` 7 项专项失败；**生产侧自 6bcb0e98 起每次真实模型调用都发 model.started/model.completed、每个动作执行发 action.executing，故事件保留裁剪对当前所有 run 实际不可用**。

## 根因（机制核实）

- `domain/event_retention.py:20-35` `KINDS` frozenset 权威 12 种：run.queued/run.running/run.waiting_approval/run.cancelling/run.succeeded/run.failed/run.cancelled/action.prepared/**action.executing**/action.completed/**model.started**/**model.completed**。`validate_event_plan` 用 `kind not in KINDS` 校验计划目标——领域已允许新种。
- `repository.py:851/853` 发 `action.executing` 审计+事件；`repository.py:656/707` 发 `model.started`/`model.completed` 事件（6bcb0e98）。
- `schema.py:1977-1981` `event_retention_targets` `CheckConstraint(name="kind")` 仅 9 种（缺 action.executing/model.started/model.completed）；`v0016.py:39-40` 冻结 CREATE TABLE 同 9 种。
- 测试 DB 经 `Database`→alembic `command.upgrade(head)` 建表（非 create_all），故 v0016 的 9 种 CHECK 生效；`event_pruning` 的 `event_case`→`plan_event_retention` 读真实 run 事件（含新种）建目标 INSERT → CHECK 违约。
- 反射守卫 `omni_test_support/v2/event_plan_migration.py:assert_schema` 以 `normalized_check`（去全部空白+小写+剥外层括号）比较**反射 CHECK sqltext 与 schema.py CheckConstraint sqltext 集合**（不比较约束名）——故只需 schema.py 与重建 DDL 的 CHECK 文本一致即可通过，约束名无需匹配（v0016 无名 vs schema.py 有名当前即通过）。

## 范围（本批）

1. **schema.py**：`event_retention_targets` kind CHECK 扩至 **12 种**，顺序对齐 `domain/event_retention.py KINDS`（action.prepared 后插 action.executing，action.completed 后插 model.started/model.completed）。仅改 CHECK 文本，其余列/约束/索引/触发器不变。
2. **v0048 冻结重建迁移**（新源文件，down_revision v2_0047，downgrade 拒绝）：按 **v0039 媒体表事务化重建先例**——
   - `ALTER TABLE event_retention_targets RENAME TO _v48_event_retention_targets`；
   - `CREATE TABLE event_retention_targets (...)`：复用 v0016 完整 DDL（12 列、复合 PK(batch_id,sequence)、UNIQUE(batch_id,run_id,seq)、复合 FK→event_retention_batches(id,agent_id)/runs(id,agent_id)、单 FK→agents(id)/retention_batches(id)、identity/bounds/state CHECK 不变），**仅 kind CHECK 扩至 12 种**；
   - `INSERT INTO event_retention_targets(12列) SELECT 12列 FROM _v48_event_retention_targets`（保留全部既有证据行）；
   - `DROP TABLE _v48_event_retention_targets`（SQLite DROP TABLE 不触发 DELETE 触发器，keep 触发器随旧表删除；event_retention_targets 为叶子证据表无入向 FK，DROP 安全）；
   - 重建 2 索引（ix_event_retention_targets_agent_id、ix_event_retention_pending_sequence 部分唯一 WHERE state='pending'）+ 2 触发器（event_retention_target_identity 不可变、event_retention_target_keep 禁删）——**逐字复用 v0016 定义不变**。
   - 表数不变（重建非新表，143 保持）；head v2_0047→v2_0048。
3. **test_v2_memory_decay.py:331-332**：head 断言 `v2_0047`→`v2_0048`（该测试升级至 head 验证 decay 列/表存在，v0048 仅重建 event_retention_targets 不影响 decay 列；断言随 head 前移）。
4. **pyrightconfig.strict.json**：include 补 `v0048.py`（新源文件须入 include 才被 strict 覆盖，v0047 先例）；412→413。

## 明确不做

- 不改 `domain/event_retention.py KINDS`（已正确 12 种，为权威）；不改 v0016 冻结迁移（不可变，只追加 v0048）；不改事件发射/裁剪业务逻辑（repository.py/event_pruning.py 不动）。
- 不追逐其余既有失败：execution_pruning[cost]（retention fingerprint 哈希含 settle 事件，独立机制）、7 个 pin v2_0039 schema_version 滞后、2 个 media_upload DDL、5 个 repository.py strict 错误（他会话 6bcb0e98）——均与本 CHECK 白名单无关，列独立批。
- 不加新事件种/新表/新路由/新模型工具；不改保持触发器语义（禁删/不可变不变）。

## 验证结果（2026-11-20）

- **专项转绿**：`uv run --no-sync pytest -q -n 0 tests/v2/core/test_v2_event_pruning.py tests/v2/core/test_v2_event_pruning_crash.py tests/v2/core/test_v2_memory_decay.py`（from agent/）→ **52 过 / 0 失败**（96.61 秒）。event_pruning 此前 7 项失败（6 项 event_retention_targets kind CHECK 违约 + 1 项 forged 预览 limit=50 截断）全部转绿；memory_decay head 断言随 v0048 前移通过。
- **forged 测试截断修复**（既有脆弱性，本批 CHECK 修复解除夹具掩盖后暴露）：`test_unpruned_unknown_recovery_and_forged_action_events_are_protected` 原仅取预览首页 limit=50；6bcb0e98 每回合新增 action.executing/model.started/model.completed 事件使模板在 cutoff 内事件数 >50，伪造 future.kind（全局 sequence 58）落到第 51 行被截断（实测 preview scanned=50/has_more=True，future.kind 已存 run_events 但不在首页）。改为有界分页（≤20 页跟随 next_cursor）收集全部 items 再断言——**3 个伪造种均受保护（event_kind_not_eligible/event_body_not_pruned）的不变性完整验证且更强**（原测试实际漏检 future.kind），私有正文 not exported 检查逐页生效，未放宽任何断言。
- **迁移/模式守卫回归**：`uv run --no-sync pytest -q -n 0 <8 文件：event_plan_migration/maintenance_migration/event_retention_contract/execution_pruning/audit_plan_migration/history_migration/retention_preview/schema_release>`（from agent/）→ **112 过 / 1 失败**（309.15 秒）。event_plan_migration 反射守卫 assert_schema（12 种 CHECK 反射对齐 schema.py、143 表、索引/触发器/FK 不变、源行保留、integrity/foreign_key ok）、v15→head 崩溃重试（marker 命中 v0016 首次 CREATE，重试经 v0048 重建至 head）、identity/keep/kind/unique 约束（run.interrupted 仍被拒，不在 12 种）、schema_release 跨模式交换/head 前移全绿。**唯一失败 execution_pruning[cost] 为既有缺陷**（retention fingerprint 哈希 runs/actions/run_events，settle_call 自 6bcb0e98 发 model.completed 改变指纹 vs 冻结期望哈希；本批仅改 event_retention_targets kind CHECK，不触及指纹哈希/这三表内容，机制核实无关，不追逐）。
- **strict**（from repo ROOT，`pyrightconfig.strict.json`）：**413 文件（include 补 v0048.py，412→413），5 错误均既有他会话已提交 repository.py（6bcb0e98 L633/659/710），本批 schema.py/v0048.py 零错误，0 警告**。
- **ruff**：check + format 通过（schema.py/v0048.py/test_v2_memory_decay.py/test_v2_event_pruning.py）。
- **聚合索引**：`scripts/project_index.py` 运行；新增源文件 v0048.py，`docs/index` 聚合随既有纪律暂缓（并行 UI 会话活跃）。
- **task_status**：`scripts/task_status.py --check` exit 0。
- 未全量；未触真实模型/账号/设备/native/72h；当前 Windows 服务与真实数据库未停止或由测试升级。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。
- **仍待独立批的既有缺陷**（本批不追逐）：execution_pruning[cost] 指纹含 settle 事件、7 个 pin v2_0039 schema_version 滞后、2 个 media_upload DDL、5 个 repository.py strict 错误。

## 验证计划

- **专项转绿**：`test_v2_event_pruning.py`（7 项既有失败→0）、`test_v2_event_pruning_crash.py`、`test_v2_execution_pruning.py`（除既有 [cost] 外）。
- **反射/迁移守卫**：event_plan_migration `assert_schema`（12 种 CHECK 反射对齐 schema.py、143 表、索引/触发器/FK 不变、源行保留）、`test_v2_maintenance_migration.py`、`test_v2_schema_release.py` 跨模式守卫、`test_v2_memory_decay.py`（v0047 列/表幂等 + head v2_0048）。
- **重建数据保全**：既有 event_retention_targets 证据行经 v0048 INSERT SELECT 全列保留；identity/keep 触发器重建后仍禁删/不可变；PRAGMA integrity_check/foreign_key_check ok。
- strict 本批零错误（余 5 既有 repository.py 不追逐）；ruff check/format。
- 未全量；未触真实模型/账号/设备；当前 Windows 服务与真实数据库未停止或由测试升级。`whole_*_complete` 保持 false。