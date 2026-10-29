# 提示词、上下文与缓存前缀

本轮把会随时间和资料变化的内容移出 system，保留现有唯一 system、工具调用完整配对、群聊隔离与预算协议。架构裁决仍归 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 请求顺序与模块职责

```text
固定工具定义：名称排序，schema 键排序，回合内冻结
system：人设 + 固定运行上下文协议 + 场景权限说明
历史：持久摘要 + 保留窗口中的原消息及原轮快照
最新 user：用户原文 → 必要的记忆/档案更新 → 本轮时间
工具循环：只在尾部追加 assistant(tool_calls) 与 tool 结果
```

`core/context.py` 是提示词布局与每轮数据渲染的单点，`core/agent.py` 负责在入口取快照和编排调用，`core/compress.py` 管理摘要边界。`memory/store.py` 只存储用户原文与不透明的回合快照；不导入 core，也不拼模型指令。`ToolRegistry.specs` 提供独立、确定顺序的规格副本，执行时继续按实时权限检查。

固定 system 中没有时间、个人笔记、档案、运行 ID、预算余量或随机标识。人设配置改变、私聊/群聊场景切换属于有意的前缀变化。旧人设里的 `{now}` 自动转换为固定的“见本轮运行上下文的 observed_at”引用；原配置文件不改写。

## 每轮快照与历史稳定性

在一轮开始时只读取一次时间、常驻笔记和档案，与用户消息同一事务存入 memory.db 的 `message_contexts`。该表按 `message_id` 关联原消息；删除消息会同步清理快照。旧库只增表，历史消息不回填虚构时间、不改正文，不改 state.db 版本。

模型视图在该轮用户正文末尾追加 `omni_runtime_context` JSON。时间使用带时区的 ISO 时间，放在资料之后；它表示这一轮开始时的时间。长工具执行后需要精确时间，应调用 `get_time`。

后续轮次和重启都从数据库恢复旧轮快照，不用“现在的时间/资料”重写旧消息。记忆发生变化时，仅在新轮末尾发送新快照，空字符串明确表示清空；当前回合写记忆，下个回合才进入新的快照。相同常驻与档案在一个保留窗口内只发送一次，减少重复 token。

原用户文本仍单独保存：聊天页面、会话标题、FTS、review 和 search_history 不会混入时间和重复档案。快照是额外的私人记忆数据，随同 memory.db 的 SQLite 备份保存；不新增磁盘目录，也不把它复制到系统日志。

## 截断、压缩与权限边界

- 历史窗口从中间开始或持久摘要折叠旧消息后，在第一个保留的快照处重新附上当时完整记忆，防止原承载位置被删除后丢失资料。
- 当前用户请求和当前快照始终保留，`keep_recent=0` 也不能把它们压成摘要。回合内临时压缩只折叠较早的前缀，保留当前轮之后的完整工具原子块。
- 临时压缩移除旧的资料承载位置时，重新附上本轮冻结的完整资料；不能依赖可能被摘要省略的旧增量。恢复只发生在压缩边界，不会每次请求重写旧轮内容。
- 单轮工具输出过大、已经没有旧前缀可压时，保留上下文完整性；上下文上限仍由 Gateway 明确拒绝，不为提高命中率丢弃当前约束。
- 群聊不读取当前个人档案/笔记，历史快照中的私人字段也不会渲染。快照、记忆、外部文本都是数据，不能提高指令权限或替代人工审批。
- 工具规格在同一回合内冻结。插件启停改变下一轮的工具目录；当前实际工具是否存在和是否获批仍实时检查，不因规格快照而放宽。

## 缓存能力与实际边界

前缀重复是供应商缓存复用的基础。DeepSeek 默认构建缓存，并根据重复前缀匹配；供应商仍可能因落盘时机或淘汰策略而未命中。[DeepSeek 官方说明](https://api-docs.deepseek.com/zh-cn/guides/kv_cache/)

本轮实现的是跨供应商的输入布局，不自行加入供应商专属 `cache_control` 或付费 TTL。Claude 的 Messages API 需通过自动缓存或显式断点启用，其缓存行为与 DeepSeek 不同；应按所用接口单独配置，不能宣称本轮已经为所有接口开启缓存。[Claude 官方说明](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)

已有的工具中间消息、临时压缩和图像视图仍不跨回合持久化，因此下一轮不能完整复用上一轮所有工具追踪前缀。常规文本历史可以逐轮延长；发生摘要、窗口滑动、工具目录变更、场景或人设切换时，相关边界之后需要重新构建缓存。这里只保证可控部分稳定，不承诺固定命中率。

## 用量观察与验证

`Usage` 归一化以下供应商上报值：

| 输出字段 | 来源 |
|---|---|
| `cached_prompt_tokens` | DeepSeek prompt_cache_hit_tokens；LiteLLM prompt_tokens_details.cached_tokens；cache_read_input_tokens |
| `cache_creation_tokens` | cache_creation_input_tokens 或 LiteLLM details.cache_creation_tokens |
| `cache_miss_tokens` | DeepSeek prompt_cache_miss_tokens |

这些值进入 Reply/流式 usage 和模型结束事件；未上报保持 null，不用本地字符串相似度冒充真实命中率。当前费用流水的旧表结构不额外记录这些字段，费用预留、结算与价格算法保持原协议。

离线用例直接比较实际模型入参：跨时间/记忆变化的历史前缀相等、进程重开后旧轮不变、相同记忆不重复、工具循环规格不变、群聊整段请求不含私人快照、当前上下文不被压缩、原文和搜索不受影响、快照事务回滚与旧库升级。供应商 token 字段用假响应验证；本轮不调用真实付费模型，因此真实命中率仍需在实际使用中观察。

验证入口：`uv run --no-sync pytest -q tests/test_prompt_context.py`；全量结果与证据见 [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md)。
