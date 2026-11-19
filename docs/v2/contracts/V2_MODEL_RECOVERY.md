# V2 显式模型故障恢复（K12）

## 结论与范围

K12 补上**经过授权与预算约束的有界模型空回复恢复**：当模型返回空完成（无答案文本且无工具调用）时，回合可在**有界次数**内重试**同一个已授权模型**；每次重试是一次**全新预留调用**，重新计费完整输入并受日预算硬限。两条守卫缩短重试串：确定性空回复（连续两次同签名、用量证明零输出）跳过剩余重试；单次昂贵尝试把预算从 3 降到 1。

**V2 硬约束（参考没有）**：成本未知的调用结算为 `unknown`，**绝不重放**；恢复**永不更换账号或模型**（无参考的 fallback 链）。固定授权模型、超时/取消、unknown 费用归属、适配器关闭自动重试均保持。

参考 `xuexi/hermes-agent/agent/empty_response_guard.py` 与 `agent/conversation_loop.py`（固定提交 `9976f4857b72439d79016354d7b6e1a3ee952e2f`）的「确定性空检测 + 成本感知重试预算」设计（默认 3 次、昂贵降 1、同签名零输出短路、每次重试再计费），在 V2 四层内独立适配。参考空回复重试后走**不同模型 fallback 链**；V2 禁止擅换模型，故只做同模型有界重试，预算用尽即终态失败。未复制第三方源码、未拉取远程、未用真实账号/模型。

## 证据与判定（纯领域）

`domain/model_recovery.py`：

- `DEFAULT_EMPTY_RETRY_BUDGET=3`、`REDUCED_EMPTY_RETRY_BUDGET=1`、`DEFAULT_COST_THRESHOLD_MICRO=250000`（$0.25）。
- `is_empty(reply)`：无工具调用且 `text.strip()` 为空——与 `validate_reply` 的 `model_empty_reply` 条件一致。
- `EmptyAttempt`（model/provider/finish_reason/usage_present/zero_output/cost_micro），`signature=(model, provider, finish_reason)`；`empty_attempt` 从回包提取（usage_present 要求 output_tokens 与 input_tokens>0 均在）。
- `deterministic_empty(attempts)`：≥2 次、同签名、且全部 usage_present+zero_output 才判定（用量缺失保守不判，保留重试）。
- `should_retry_empty(attempts, threshold)`：最新尝试 `cost_micro is None`（结算 unknown）→ **False 绝不重放**；确定性空 → False；否则预算 = 昂贵（cost≥threshold）?1:3，`len(attempts) < budget` 才重试。

## 四层接线

- **domain** `domain/model_recovery.py`：上述纯规则，只判定，不预留/派发/触存储。
- **application** `application/model_calls.py`：把原 `invoke` 主体抽为 `_invoke_once`（保留预留→准备→派发→结算→length/content_filter/output_limit 终态检查，**移出**末尾 `validate_reply`）。`invoke` 薄包装 = `_invoke_once` + 非摘要 `validate_reply`（**既有调用方行为不变**）。新增 `invoke_recovering`：循环 `_invoke_once`（每次新预留、再计费、受日预算约束），非空回包走 `validate_reply` 返回；空回包记录 `EmptyAttempt`，`should_retry_empty` 为 False 则抛 `model_empty_reply`（恢复耗尽，终态），否则继续下一轮。
- **engine** `application/engine.py`：构造器 `model_recovery: bool = False`。`_run` 主回合调用在 `self._model_recovery and not remote` 时走 `invoke_recovering`，否则原 `invoke`。grace（K07 预算 fallback）与摘要调用不套恢复。
- **composition** `composition/instance.py`：`open_instance(model_recovery=False)` 透传 `Application`。`composition/http.py`：`http_app(model_recovery=False)` 透传 `open_instance`，给生产 HTTP 入口显式启用通道。

## 取消、预算与未知

- **取消/租约**：每次重试的 `_invoke_once` 在预留与派发前重断言 `assert_run_authorized`/`assert_context`/`assert_model_authorized`/凭据权威；run 被取消或租约丢失会在下一次预留即失败传播，恢复不会绕过取消。重试间无额外 sleep（与参考一致立即再计费）。
- **预算**：每次重试 `reserve_call` 重新预留 `call_reservation_micro` 并受 `daily_budget_micro` 硬限；预算耗尽抛 `budget_exceeded` 终态传播。已发生费用（含空回复）逐次结算保留，不退还、不杜撰。
- **未知不重放**：空回复若 `cost_micro is None`，`settle_call` 置 `unknown`；`should_retry_empty` 见 unknown 成本立即停止，绝不重放。派发后未知效果故障（非空回复路径）仍按既有 `known`/`unknown` 结算并失败，不进入恢复。
- **终态故障不重试**：`model_response_incomplete`（length/content_filter）、`model_output_limit_exceeded`、`invalid_model_tool_calls`（非空但协议无效）均终态，恢复只针对空回复。

## 限额

默认空回复预算 3（昂贵降 1）、成本阈值 250000 micro（$0.25）、确定性空短路需 ≥2 次同签名零输出。默认 OFF（opt-in）。

## 明确不在本项范围

- **无模型/provider fallback 链**（参考重试后换模型；V2 禁止擅换账号/模型，只同模型有界重试）。
- 恢复仅覆盖**空回复**；参考的超时重试、网络瞬态重试、context_compressor_retry、turn_recovery 自动恢复、auxiliary_fallback 等未对等（V2 超时/取消/unknown 结算保持既有终态语义）。
- 默认 OFF 由 `open_instance`/`http_app` 的 `model_recovery` 开关驱动，未接 Agent 设置严格 schema（避免改 KEYS 全集）；参考由 `config.yaml agent.empty_response_guard` 驱动。
- 成本阈值用 micro 常量（V2 计价单位），非参考 USD Decimal；provider 签名用 `base_url`。
- 无重试间退避延迟、无重试次数的持久化审计行（重试体现为多条 settled model_calls）；参考的 streak 成本累计对外暴露未对等。

## 完成条件

域规则/`_invoke_once` 抽取与 `invoke` 兼容包装/`invoke_recovering` 有界恢复/engine 主回合接线/composition 与 HTTP 启用通道已接；空回复重试后成功、确定性空短路、昂贵降 1、unknown 成本绝不重放、预算 3 次耗尽、默认 OFF 立即失败、非空无效调用终态均有专项回归；既有 invoke 调用方（执行/委派/压缩/迭代守卫/上下文/turn_integrity）专项全过证明重构兼容。仅类/提示词/空工具名不算完成。本项关闭不代表整个内核对标或 V2 完成；真实模型/设备/72 小时验收另列。