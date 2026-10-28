# 本地与 API embedding（D24）

默认 `embedding.enabled: false`，recall 继续使用现有 FTS5/LIKE 检索。
开启后，recall 将关键词结果与 sqlite-vec 余弦距离结果做 RRF 融合；工作台记忆页显示
供应商、模型、索引数量和降级原因。工作台的即时关键词输入框仍使用 FTS，不随键入调用付费 API。

## 本地模型

连接已经部署、已经下载模型的 Ollama，不替用户安装或拉取模型。先确认该模型在本机执行，
不配置 Ollama 云端模型；只接受回环 HTTP 地址，关闭代理与重定向。

```yaml
embedding:
  enabled: true
  provider: local
  endpoint: http://127.0.0.1:11434
  model: 你已安装的向量模型名称
  dimensions: 768  # 必须与模型实际输出一致，示例数值不是自动探测
  revision: "1"
  scopes: [private]
  batch_size: 8
  timeout_s: 15
```

模型不可用、返回维度变化、扩展缺失、超时、非法向量均回退关键词检索。
不自动把失败请求转交给其它模型或云端。

## API 模型

通过 LiteLLM embedding 出口，使用用户配置的 HTTPS 服务。`send_to_cloud` 明确表示
允许把所选 scope 的事实文本和查询发给该服务；密钥只用环境变量引用，不写入索引或调用日志。
`daily_usd` 与已有 LLM 每日限额取更严格者，共用费用账本；查询还受当前回合预算约束。

```yaml
embedding:
  enabled: true
  provider: api
  endpoint: https://你的供应商地址/v1
  model: openai/供应商提供的向量模型
  api_key_env: EMBEDDING_API_KEY
  send_to_cloud: true
  dimensions: 供应商的实际维度
  revision: "1"
  daily_usd: 0.10       # 用户自己设定，示例不代表已授权花费
  input_usd_per_m: 0.02 # 每百万输入 token 的实际单价；示例不是报价
  scopes: [private]
```

启用 API 必须有显式预算、输入单价和云端开关。调用前按 UTF-8 字节数保守预留，成功按
上报 usage 结算；不确定失败保留预留费用，不视作免费。无自动重试。
修改供应商/模型/维度/endpoint/revision 会使用新索引身份；同名模型升级后需递增 revision。
配置变化在重启该入口后生效。

## 索引、遗忘与边界

- 向量和来源 hash 保存在同一个 `memory.db` 的普通表中，sqlite-vec 只负责距离计算；
  扩展在初始化受控加载后立即禁止再次加载。个人小规模记忆使用精确扫描，不宣称 ANN 性能。
- 后台每批最多 8 条，跨进程租约限制一个索引者；进程中断后按缺失行续建。
  来源、scope 与正文以事实表为准，网络返回后先比较当前原文，再写索引。
- 删除/纠错触发普通 SQLite 触发器，所有模型版本的该条向量同步删除；不依赖后台清理。
  返回结果前再次回源检查，防止查询期间遗忘的文本重新出现。
- 只索引配置的 scopes；默认 private。群聊权限由入口绑定，模型不能在工具参数中改 scope。
- 此版向量化事实记忆，不自动上传全部聊天、档案、技能或截图。旧模型派生索引保留供诊断，
  会增加磁盘占用；遗忘会删除对应事实的所有版本，备份仍需要用户按保留策略处理。

## 冻结评测

数据集 `tests/fixtures/retrieval_v1.json` 包含 20 个合成样本：中文精确词/短词/姓名/数字/
英文/混合词/同义表达/跨群隔离。每个样本重复三次，报告 Recall@5、MRR、延迟、Python
分配峰值和调用费用（分配峰值不是进程 RSS）。

```powershell
uv run --no-sync python -m omni.channels.eval_retrieval --output docs/evidence/retrieval-fts-v1.json
# 以下命令会调用所配置模型；API 配置会花费预算，只对合成样本执行
uv run --no-sync python -m omni.channels.eval_retrieval --mode hybrid --config <配置路径> --output <报告路径>
```

本机 FTS 基线：20 样本 × 3 次，Recall@5/MRR 均 0.7778；完整结果见
[冻结报告](evidence/retrieval-fts-v1.json)。尚未配置真实 embedding 模型，所以没有宣称
混合检索已优于 FTS，更没有把确定性假模型测试当作语义质量证据；默认不启用向量检索。

依据：[Ollama embed](https://docs.ollama.com/api/embed)、
[LiteLLM embedding](https://docs.litellm.ai/docs/embedding/supported_embedding)、
[sqlite-vec API](https://github.com/asg017/sqlite-vec/blob/main/site/api-reference.md)。
