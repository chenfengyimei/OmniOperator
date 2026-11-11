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

`pyrightconfig.strict.json` 的 `include` 必须与本表完全一致；`agent/tests/test_v2_typecheck_scope.py` 会双向校验。

## 3. 基线例外（未纳入，不得写成已严格化）

在 `1a18fae` 源码上测得（仅记录，不构成门禁）：

| 范围 | strict 错误数 |
| --- | --- |
| `agent/src/omni/domain`（全部 53 文件） | 2321 |
| `domain/model_stream.py` | 75 |
| `domain/service_control.py` | 94 |
| `domain/context.py` | 38 |
| `domain/recovery_authority.py` | 27 |

`memory_semantics.py`（19）与 `foreground.py`（24）已在纳入 strict 前清零；`metering.py`（19）与 `release_fence.py`（16）同样已清零，不再列为基线例外。`application/`、`adapters/`、`composition/`、`agent/tests/` 尚未开始 strict。

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
