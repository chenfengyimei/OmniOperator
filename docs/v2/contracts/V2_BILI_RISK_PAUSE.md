# V2 B站风险码每账号暂停与所有者恢复（K18.risk_pause）

状态：已实现并专项通过（2026-11-20）。学习参考 dsh-bili-whale `traffic.mjs` 的风险码集合 `[12015,-105,-101,401]` 与"风控命中即手动暂停、所有者恢复"语义，独立实现于 V2 四层 B 站链，不复用 V1 tools/bilibili。实现 K18 审计缺口"登录凭据风险暂停"的**模型侧读/准备阻断子集**。

## 范围

1. **领域风险码**（`domain/bilibili.py`）：`RISK_CODES = frozenset({12015,-105,-101,401})`（参考 traffic.mjs 原集合）；`is_risk_code` 仅接受精确 int（bool/float/str 一律拒）。
2. **类型化传输**（`domain/contracts.py`）：`BusinessError` 加性 `detail: int | None = None` 关键字参，仅承载数值风险码；既有 code/message/status/retryable 语义不变。
3. **网关识别**（`adapters/bilibili.py`）：`_get` 与 `_get_list` 在 JSON 解析后、通用 `bilibili_read_rejected` 之前识别风险码 → `BusinessError("bilibili_risk_code", status=403, detail=码)`；非风险拒绝码（如 -400）保持既有 `bilibili_read_rejected` 502 不变。
4. **服务读门**（`application/bilibili.py`）：read 在凭据授权后、网关调用前查 `bilibili_risk_get`，活动暂停 → `bilibili_risk_paused` 403（不触达网关）；网关抛 `bilibili_risk_code` 且 detail 非空 → 同事务写 `bilibili_risk_pause` 持久化后转 `bilibili_risk_paused` 403；凭据权威前后 CAS、admission≤8、shield 生命周期全复用。
5. **准备门**（`adapters/persistence/bilibili_repository.py`）：`bilibili_prepare` 顶部活动暂停 → `bilibili_risk_paused` 403，先于幂等键/目标/表单校验。
6. **每账号一行 upsert**：新表 `bilibili_risk_pauses`（agent+config 唯一），暂停写 code/paused_at/resumed_at=NULL；恢复 CAS `WHERE resumed_at IS NULL` 置 resumed_at（无活动暂停 → `bilibili_risk_not_paused` 409）；恢复后再暂停复用同一行（id/created_at/config_id 不变，code/paused_at 更新）。非法码 → `bilibili_risk_invalid` 422。审计 `bilibili.risk_paused`/`bilibili.risk_resumed`。
7. **不可变证据**：`bilibili_risk_identity` 触发器冻结 id/agent_id/created_at/config_id；`bilibili_risk_keep` 禁止删除（恢复是状态更新不是删行，历史保留）。
8. **所有者恢复面**：`BilibiliService.risk_state`/`resume`（closing 门复用）；REST `GET /agents/{agent_id}/bilibili/risk/{config_id}` 与 `POST .../resume`。**无模型侧暂停/恢复工具**——暂停由风控响应自动触发，恢复仅所有者。

## 实现位置

- `domain/bilibili.py`：RISK_CODES/is_risk_code；`domain/contracts.py`：BusinessError.detail 加性。
- `adapters/persistence/schema.py`：bilibili_risk_pauses（scoped + config 复合 FK + code CHECK + payload CHECK）；`migrations/versions/v0046.py`：冻结 SQL（v2_0046，down v2_0045，含两触发器与 agent 索引，downgrade 拒绝）；表数 141→142。
- `adapters/persistence/bilibili_repository.py`：bilibili_risk_get/pause/resume + prepare 顶部门；`application/ports.py` 三签名。
- `adapters/bilibili.py`：_get/_get_list 风险识别；`application/bilibili.py`：读前门/自动暂停/risk_state/resume；`adapters/http.py`：两路由。

## 验证

- 新增 29 项回归（`agent/tests/v2/integrations/test_v2_bilibili_risk.py`）：is_risk_code 精确 int×12、网关 dict/list 读取四码类型化 detail、非风险码保持 read_rejected、风控读→暂停持久化→读/准备双阻断（网关不再被触达）→所有者恢复→读/准备复原、无暂停恢复 409/双重恢复 409、再暂停复用行身份不变、暂停按 config 隔离、删行/改身份触发器 data_constraint、非法码 422×4、v0046 升级幂等+列/触发器反射+downgrade 拒绝、REST 401/404/暂停态/恢复全链。
- 调用方：bilibili 全专项（core/gateway/worker/http + 15 读套件 + risk 29）与 14 个迁移守卫文件合跑，除 6 项既有 v2_0039 钉住失败外全绿。
- 守卫同步：14 处 `len(s.metadata.tables)==141`→142（11 测试文件 + 3 共享助手）。
- **v0045 遗留守卫修复**：`test_v2_action_review_migration.py` 崩溃重试用例跨 v2_0019→head 比较 actions 全列，被 v0045 加性 `started_at` 列破坏（既有失败，非本批引入）；按 preserved_rows 既有 runs 排除列先例增 `excluded_action_columns`，重试后仅投影基线列比较并**额外断言 started_at 全 NULL**（升级不得为旧 unknown 动作臆造开始时间），守卫不放宽。
- strict（pyrightconfig.strict.json，仓库根）：本批文件零错误；余 5 错误均既有他会话已提交 repository.py（6bcb0e98），不追逐。ruff check/format 通过。

## 明确不做

- **已批准 outbox 投递不追溯阻断**：暂停门只覆盖模型侧 read/prepare；已经所有者批准进入 outbox 的投递仍按既有授权 CAS/限额/冷却/送达核查执行（参考 traffic.mjs 亦为请求级门）。是否给 dispatch 加风险门属独立安全决策，列后续。
- 参考读 600/h 写 12/h 速率预算与 120s 冷却（V2 共享 delivery 限额冷却已覆盖投递侧，读侧速率预算列后续）；主动观看/双日记/沉默衰减/候选打分/直播/AI-share 守卫仍列 K18 后续；真实账号风控码触发与恢复验收另验。