# V2 类型检查（分模块严格化）

> **全仓严格类型尚未完成。** 本文件记录已纳入 strict 的模块、当前基线例外与推进规则；默认正式入口仍是 V1。

## 1. 门禁

```powershell
uv run --no-sync pyright --project pyrightconfig.strict.json
```

要求输出 `0 errors, 0 warnings`。该命令只检查已纳入 strict 的文件；普通 `uv run --no-sync pyright` 仍按默认模式检查改动文件。

## 2. 已纳入 strict 的模块

| 文件 | 纳入原因 |
| --- | --- |
| `agent/src/omni/domain/capabilities.py` | 能力报告纯投影，需保持无 `Any` 泄漏 |
| `agent/src/omni/domain/credentials.py` | 凭据引用与权限指纹纯函数 |
| `agent/src/omni/domain/objects.py` | 对象类型/上限纯规则 |
| `agent/src/omni/domain/people.py` | 人物档案纯规则 |
| `agent/src/omni/domain/metering.py` | 请求形状/记忆提示/令牌估算纯函数 |
| `agent/src/omni/domain/release_fence.py` | 启动互斥栅栏纯校验 |
| `agent/src/omni/domain/memory_semantics.py` | 派生记忆索引/相关事实合并纯规则 |
| `agent/src/omni/domain/foreground.py` | 前台输入授权与有界输入纯校验 |
| `agent/src/omni/domain/context.py` | 对话压缩/批次/尾部预算纯规则 |
| `agent/src/omni/domain/recovery_authority.py` | 恢复授权与源见证校验（经 service_deployment 公共桥） |
| `agent/src/omni/domain/model_stream.py` | 有界流式装配与工具调用解析纯规则 |
| `agent/src/omni/domain/service_control.py` | 服务启动/排空身份与 HMAC 消息纯规则（经 service_deployment 公共桥） |
| `agent/src/omni/application/routing.py` | 档位配置读取/保存用例（显式 Record） |
| `agent/src/omni/application/release_drain.py` | 发布排空绑定既有 once-only 启动控制 |
| `agent/src/omni/application/exchange_recovery_steps.py` | 隔离/救援计划复用发布快照与栅栏 |
| `agent/src/omni/application/local_vectors.py` | 本地 ONNX 向量器选择/状态无 I/O 规则 |
| `agent/src/omni/application/service_deployment.py` | 服务部署准备/验证/状态组合（公共桥 + 显式 Record） |
| `agent/src/omni/application/model_output.py` | 有界实时预览（TypedDict 行、authority Mapping） |
| `agent/src/omni/application/people.py` | 人物档案用例（显式 Record/类型化调用） |
| `agent/src/omni/application/memory_tools.py` | 记忆工具用例（UnitOfWork 回调显式类型） |
| `agent/src/omni/application/exchange_recovery.py` | 交换隔离/救援计划（进度与授权类型化） |
| `agent/src/omni/application/service_cleanup.py` | 隔离定义审查/清理计划复用 |
| `agent/src/omni/domain/terminal_review.py` | 终端未知结果核查与证据指纹纯规则 |
| `agent/src/omni/adapters/metrics.py` | 运行指标观测适配器 |
| `agent/src/omni/adapters/build_identity.py` | 构建身份常量与代码后缀 |
| `agent/src/omni/adapters/primitives.py` | 时钟/ID 原语 Protocol 与实现 |
| `agent/src/omni/adapters/timezones.py` | 时区数据库标识读取边界 |

`pyrightconfig.strict.json` 的 `include` 必须与本表完全一致；`agent/tests/test_v2_typecheck_scope.py` 会双向校验。

## 3. 基线例外（未纳入，不得写成已严格化）

在 `1a18fae` 源码上测得（仅记录，不构成门禁）：

| 范围 | strict 错误数 |
| --- | --- |
| `agent/src/omni/domain`（全部 53 文件） | 2321 |

`service_control.py`（94）已在纳入 strict 前清零；`model_stream.py`（75）、`context.py`（38）、`recovery_authority.py`（27）、`memory_semantics.py`（19）、`foreground.py`（24）、`metering.py`（19）与 `release_fence.py`（16）同样已清零，基线表中不再列逐文件例外。`application/` 与 `adapters/` 已从 15 个小模块开始；`composition/`、`agent/tests/` 尚未开始 strict。

## 4. 推进规则

1. 每次只把一个模块或一组强相关纯函数纳入 strict；纳入前先清零该模块错误，不把“忽略注释”当清零。
2. 新增的 strict 模块必须同时更新 `pyrightconfig.strict.json`、本文件表格与证据；守卫测试会拦截漂移。
3. 基线例外数字只代表对应源码版本；源码变化后需重测，不沿用旧数字。
4. 不允许用 `# type: ignore`/`# pyright: ignore` 掩盖真实错误；确需忽略时必须在类型检查文档写明原因与范围。
5. 全仓 strict 完成前，任何报告不得写“全仓严格类型已通过”。

## 5. 验证

- strict 门禁 0 errors / 0 warnings。
- `agent/tests/test_v2_typecheck_scope.py`：配置与文档表一致、文件存在、只允许 `agent/src/omni/domain/` 前缀、无目录/通配批量纳入、文档保留“全仓严格类型尚未完成”。
- 相关调用方回归：能力/凭据/对象/人物测试。
