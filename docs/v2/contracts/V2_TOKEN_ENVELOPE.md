# V2 模型容量与 UTF-8 token 信封（R4）

状态：**已实现（2026-11-23，R4）**。本文件说明 `context_tokens` 的单位、两版保守计数器的语义、默认值与回归边界；不新增迁移、不改模型调用协议。

## 1. 单位

`context_tokens`、`output_tokens`、`summary_tokens`、`margin_tokens` 全部是**模型 token**。守卫恒为：

```
estimate.tokens + output(reserve) + margin_tokens <= context_tokens
```

估算器是本地保守计数器（`utf8_upper_bound`），不联网、不读凭据、不调用 provider 元数据。

## 2. 两版信封

| `counter_ref` | 语义 | 用途 |
| --- | --- | --- |
| `utf8-envelope-v1` | **1 字节 = 1 token**（把 UTF-8 字节数直接当 token 上界），另加 256 token 信封 | 历史配置，保持逐字节行为；最保守 |
| `utf8-envelope-v2` | 把同一份序列化请求换算成 token 上界：ASCII **3 字节/token**、非 ASCII **2 token/字符**，另加 256 token 信封与图像 token 成本 | **新配置默认**；token 单位与 `context_tokens` 一致 |

- v2 的常量是**上界**而非平均值：英文/JSON 通常 3–4 字节/token，CJK 通常 1.5–2 token/字符。
- 图像成本（`model_tokens`）在 v2 中是 token，直接相加；v1 时代按字节混算，仅为兼容保留。
- 两版都要求 `counter_mode == "utf8_upper_bound"`；`required`（外部精确 tokenizer）仍需独立实现与安装。

## 3. 为什么改（R4）

C/D/E/G 合入后系统提示、persona、工具 schema 与对话增长到约 30 KB。v1 把 30 KB 当 30K token，32K 声明容量只剩下 3K，正常工具回合在第三轮就以 `token_budget_exceeded` 失败（TODO 用例 29 808+2048+1024=32 880，仅超 112）。真实 32K-token 模型可容纳该请求；因此把**新配置**切到 v2，v1 语义保留给既有配置。

## 4. 默认与兼容

- `ModelOptions.counter_ref` 默认 `utf8-envelope-v2`；`context_tokens` 默认仍 **32768**（即约 98 KB ASCII 的输入余量）。
- 既有 v1 配置不变：守卫仍按字节，行为可复现；所有者可显式改配置到 v2。
- 图像路径（`application/images.py`、`application/configuration.py`）接受 v1/v2；`required` 模式仍报 `image_counter_unavailable`。
- 迁移 v0005 的 v1 模板保持不变（历史数据），新实例走 v2 默认。

## 5. 回归与验收

| 用例 | 断言 |
| --- | --- |
| `test_utf8_envelope_v2_lets_a_declared_32k_config_finish_normal_tool_rounds` | 29.8 KB ASCII：v1 超 32768（保持旧行为），v2 远小于且 `+2048+1024 <= 32768` |
| `test_utf8_envelope_v2_keeps_cjk_conservative_and_still_fails_closed` | 1 000 个 CJK 字符 ≥ 2000 token；200 KB ASCII 仍被 `fits` 拒绝 |
| `test_token_capacity_compresses_whole_turns_with_output_reservation` | 小容量下仍按整轮压缩并记录 `omitted_count`/`truncated` |
| 图像套件 | 17 passed；v2 下图像 token 成本正确相加、预算与擦除不变 |
| 上下文/执行/委派/cron 套件 | 155 passed（含 v1 行为不变） |

未验：真实 provider 的 tokenizer 对照（v2 是上界，不是精确值）；真实 32K/128K 模型长对话压力测试。
