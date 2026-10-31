# 模型选择与思考开关（D30）

设置中心「模型」页的三件事：**选谁干活、存得住、要不要让它先想再答**。
本文是这三件事的设计权威；改这块前先读这里，否则很容易把"看起来没选"再修一遍。

## 1 为什么"选过又变没选"（已修）

现场症状：模型明明选过，刷新页面或重启服务后再进设置中心，卡片又回到"还没选"，
只能再选一次。

根因有四处，缺一不可地同时修：

1. **接口读的是启动快照**。`GET /api/system/config` 以前返回 `runtime.config`——进程
   启动那一刻的内存副本。保存写的是 `config.yaml`，所以保存成功后重新打开页面看到的
   仍是旧值。用户以为没存上，于是再选一遍，而第二遍的提交会把第一遍的选择覆盖掉。
   现在 GET **直接读磁盘**（`load_config(runtime.paths.config)`），读失败才退回快照；
   同时返回 `pending_restart`，把"已保存但还没生效"和"已经生效"在界面上分开说。
2. **认不出的型号显示成"没选"**。槽位卡副标题以前在"型号不在内置列表里"时退回槽位
   说明文案（如"平时跟它说话用这个"），和真的没选长得一模一样。现在只要槽位里
   有字符串就显示原文（`deepseek/还没发布的名字` 原样展示），并提示"已下线/手输"。
3. **再点一次同一家供应商会重置型号**。`pickProvider` 以前无论如何都把型号设成内置
   列表第一个；用户因为"看起来没选"再点一次 DeepSeek，就把自己选的 V4 Pro 换成了
   列表首项。现在同一家且已有型号时直接早退，不动选择。
4. **一个空槽位能让整次保存失败**。`models.vision` 以前是"必须非空字符串"，只要它是空，
   整次 POST 就 400，界面上只有一句"保存失败"。现在 `vision` 允许为空（等于跟随
   日常对话），保存不再被一个可选槽位连坐。

新增 `agent/tests/test_ui_model_settings.py` 用真实 Chromium 锁住第 1 条：选型号 →
保存 → `page.reload()` → 再进设置中心必须看到刚存的那个型号。

## 2 模型目录

目录是**前端内置的卡片清单**（`ui/app/hub-models.js` 的 `PROVIDERS`），不是从接口拉的：
供应商/型号要带"便宜/视觉/已下线"这类人写的说明，接口给不了。

- 现役型号按各家官方文档核对；核对不了的（千问、Kimi）保留长期可用的别名，
  并在每一家提供 **「自己填型号」** 入口，列表过期也不会把人挡住。
- 已下线的旧名（`deepseek-chat` / `deepseek-reasoner`，2026-07-24 下线）**保留但标
  `retired`**：已经配了旧名的用户不会因为升级而丢配置，界面会提示"请求已转到 Flash"。
- 用户手填的型号串原样存进 `config.yaml`；不认识的供应商前缀归到「其它 / 中转站」。

## 3 思考开关与强度

配置在 `config.yaml` 的 `reasoning` 段，**每个模型槽位一套**（`chat` / `vision` /
`review` / `router` / `chat_mode` / `retrieval_mode` / `agent_mode`）：

```yaml
reasoning:
  chat: "auto"      # auto / off / low / medium / high / max
  agent_mode: "high"
```

为什么按槽位而不是全局一个开关：日常对话想快、聊完复盘想深，两件事的诉求相反。

### 3.1 档位语义

| 档位 | 含义 | 下发的参数（DeepSeek） |
| --- | --- | --- |
| `auto` | 一个参数都不发，沿用供应商默认（V4 系列默认开着思考、effort=high） | 不发 |
| `off` | 明确关闭思考 | `thinking.type=disabled` |
| `low` / `medium` / `high` / `max` | 愿意等它想多久 | `thinking.type=enabled` + `reasoning_effort` |

### 3.2 供应商支持矩阵

参数只对**听得懂它们**的供应商下发（`llm/gateway.py` 的 `REASONING_PROVIDERS`）：

| 供应商 | 支持 | 说明 |
| --- | --- | --- |
| DeepSeek | 是 | `thinking` + `reasoning_effort`，两个都必须走 `extra_body`：LiteLLM 的 OpenAI 兼容层会把 `reasoning_effort` 当未知参数过滤掉，注册成顶层参数等于"设了没用"。官方映射表里没有 `medium`，按 `high` 服务，界面会直说"这家按「高」服务"。 |
| OpenAI | 是 | 只认 `reasoning_effort`；"关闭"用 `none`（OpenAI 没有 disabled 这种说法）。 |
| Anthropic | 否（本轮不做） | extended thinking 要求 `budget_tokens < max_tokens`，而本项目不显式发 `max_tokens`（交给供应商默认），贸然下发在部分型号上会直接 400。界面明说"这家不支持在这里调"。 |
| 千问 / Kimi / Ollama / 中转站 | 否 | 一律不发：被静默忽略至少还能聊天，发错参数 400 会直接变成"聊不了天"。 |

### 3.3 生效时机

- 档位与模型一样是**服务端行为**：写进 `config.yaml` 后要**重启工作台进程**才生效，
  只刷新浏览器不生效。接口用 `pending_restart` 把这件事说清楚，不再每次都甩一句
  "重启后生效"让人自己去猜。

## 4 已知限制（未验收，别当成已完成）

1. **思考内容（`reasoning_content`）不回放**。DeepSeek 文档要求在带 `tools` 的请求里
   回传历史回合的思考内容，否则可能 400。实测（2026-11-01，`deepseek-flash`，
   构造"历史里有 assistant tool_calls 但没有 reasoning_content"的请求）返回 **200**，
   所以本轮没有为此改 `messages` 表结构。**但这是现场探针，不是保证**：一旦供应商
   收紧校验，需要给 `messages` 加一列并在历史里回传——那是一次真实的存储迁移。
2. **费用预留偏低**。`llm.max_output_tokens=1024` 用于调用前的费用预留；开了思考之后
   实际输出（含思考）会明显超过这个数。设了 `llm.budget.daily_usd` 时，硬拦截仍按
   预留值判断，所以可能"超一点点"。要更准就把 `max_output_tokens` 调大。
3. **真实模型质量的档位收益未测**。离线只用假网关验证"参数发对了"，同一问题在
   `off` / `high` / `max` 下的时延与正确率差异没有真实评测。
4. **千问/Kimi 的型号清单未逐个核对**（只保证有"自己填型号"兜底）。
5. 界面观感（浅色/深色、窄屏）只做过一次截图核对，**未做多视口验收**。
