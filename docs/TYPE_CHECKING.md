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
| `agent/src/omni/domain/action_review.py` | 未知写动作的观察复核/证据指纹纯规则 |
| `agent/src/omni/domain/android.py` | Android 身份/观察/输入/权限请求纯校验 |
| `agent/src/omni/domain/android_unicode.py` | 输入助手配置/认证报文纯校验 |
| `agent/src/omni/domain/audit_retention.py` | 审计保留主体/冻结预览/计划纯校验 |
| `agent/src/omni/domain/bilibili.py` | WBI 签名/字幕与流地址/表单/提及区间纯校验 |
| `agent/src/omni/domain/capabilities.py` | 能力报告纯投影，需保持无 `Any` 泄漏 |
| `agent/src/omni/domain/credential_ipc.py` | 原生凭据帧解码/请求/响应纯校验 |
| `agent/src/omni/domain/credentials.py` | 凭据引用与权限指纹纯函数 |
| `agent/src/omni/domain/delivery.py` | 出站意图身份/受限文本/可信结果纯规则 |
| `agent/src/omni/domain/desktop.py` | 桌面目标/请求/能力/观察/响应帧纯校验 |
| `agent/src/omni/domain/desktop_actions.py` | 语义桌面动作/节点证据/结果纯校验（经公共桥） |
| `agent/src/omni/domain/desktop_portal.py` | 窗口共享授权/命令/事件/帧收据纯校验 |
| `agent/src/omni/domain/documents.py` | 源文本/分块/向量归一化与合并纯规则 |
| `agent/src/omni/domain/event_retention.py` | 事件保留冻结预览/计划纯校验 |
| `agent/src/omni/domain/extensions.py` | 扩展清单/schema/产物元数据/授权纯校验 |
| `agent/src/omni/domain/history.py` | 历史游标编码/解码与代际校验 |
| `agent/src/omni/domain/images.py` | 图像引用/策略/低细节计量纯规则 |
| `agent/src/omni/domain/installation.py` | 安装收据校验与 schema 一致性纯规则 |
| `agent/src/omni/domain/objects.py` | 对象类型/上限纯规则 |
| `agent/src/omni/domain/people.py` | 人物档案纯规则 |
| `agent/src/omni/domain/metering.py` | 请求形状/记忆提示/令牌估算纯函数 |
| `agent/src/omni/domain/qq.py` | OneBot 文本事件/连接/会话授权纯校验 |
| `agent/src/omni/domain/release_fence.py` | 启动互斥栅栏纯校验 |
| `agent/src/omni/domain/releases.py` | 发布/恢复计划、栅栏文件与路径分离纯校验（经公共桥） |
| `agent/src/omni/domain/routing.py` | 能力档位/路由策略/工具筛选纯规则 |
| `agent/src/omni/domain/settings.py` | Agent 设置与运行配置纯校验 |
| `agent/src/omni/domain/scheduling.py` | 数值 cron/下次到期/到期选择纯规则 |
| `agent/src/omni/domain/local_vectors.py` | 本地向量包清单/协议纯校验 |
| `agent/src/omni/domain/memory_semantics.py` | 派生记忆索引/相关事实合并纯规则 |
| `agent/src/omni/domain/foreground.py` | 前台输入授权与有界输入纯校验 |
| `agent/src/omni/domain/maintenance.py` | 维护请求/认领/租约时间纯校验 |
| `agent/src/omni/domain/media.py` | 媒体源/字幕分段/帧表/转写载荷纯校验 |
| `agent/src/omni/domain/mcp.py` | MCP 传输身份/目录指纹/授权/结果文本纯校验 |
| `agent/src/omni/domain/memory_organization.py` | 精确事实整理请求/决定纯校验 |
| `agent/src/omni/domain/retention.py` | 执行正文保留策略/计划纯校验 |
| `agent/src/omni/domain/contracts.py` | 执行合同/状态机/动作结果纯类型 |
| `agent/src/omni/domain/configuration.py` | 配置载荷/名称/指纹纯校验 |
| `agent/src/omni/domain/context.py` | 对话压缩/批次/尾部预算纯规则 |
| `agent/src/omni/domain/recovery_authority.py` | 恢复授权与源见证校验（经 service_deployment 公共桥） |
| `agent/src/omni/domain/reference_release.py` | 工件引用释放计划结构与指纹纯校验 |
| `agent/src/omni/domain/schema_upgrade.py` | 前向 schema 路由与双运行时清单校验（经公共桥） |
| `agent/src/omni/domain/model_stream.py` | 有界流式装配与工具调用解析纯规则 |
| `agent/src/omni/domain/service_control.py` | 服务启动/排空身份与 HMAC 消息纯规则（经 service_deployment 公共桥） |
| `agent/src/omni/domain/service_deployment.py` | 服务请求/运行时/计划/启动纯校验与公共桥 |
| `agent/src/omni/domain/service_cleanup.py` | 隔离注册清理计划纯校验（经公共桥） |
| `agent/src/omni/domain/service_exchange.py` | 同 schema 服务交换/管理员阶段/证据纯校验 |
| `agent/src/omni/domain/service_isolation.py` | 中断交换隔离计划与启动隔离校验（经公共桥） |
| `agent/src/omni/domain/service_operations.py` | 离线服务安装/卸载操作指纹纯规则 |
| `agent/src/omni/domain/service_activation.py` | 无人值守启动绑定/预留纯校验（经公共桥） |
| `agent/src/omni/domain/service_admin.py` | 管理员操作/阶段/请求/响应帧纯校验（经公共桥） |
| `agent/src/omni/domain/skills.py` | 技能元数据/文件路径/文档/补丁/清单纯规则 |
| `agent/src/omni/domain/terminal_review.py` | 终端未知结果核查与证据指纹纯规则 |
| `agent/src/omni/domain/terminals.py` | 终端打开/输入/缩放/关闭请求纯校验 |
| `agent/src/omni/application/routing.py` | 档位配置读取/保存用例（显式 Record） |
| `agent/src/omni/application/scheduling.py` | 单调度器保存/到期入队（UnitOfWork 回调与嵌套字段显式化） |
| `agent/src/omni/application/release_drain.py` | 发布排空绑定既有 once-only 启动控制 |
| `agent/src/omni/application/releases.py` | 发布计划/准备/恢复编排（显式 Record） |
| `agent/src/omni/application/exchange_recovery_steps.py` | 隔离/救援计划复用发布快照与栅栏 |
| `agent/src/omni/application/local_vectors.py` | 本地 ONNX 向量器选择/状态无 I/O 规则 |
| `agent/src/omni/application/maintenance.py` | 维护作业编排/心跳/结算（有界任务、显式 Record） |
| `agent/src/omni/application/mcp.py` | MCP 连接/目录授权/运行期调用编排（显式 Record、有界传输端口） |
| `agent/src/omni/application/images.py` | 图片准入/上下文选择/线材物化（有界任务、显式 Record） |
| `agent/src/omni/application/service_deployment.py` | 服务部署准备/验证/状态组合（公共桥 + 显式 Record） |
| `agent/src/omni/application/service_operations.py` | 服务安装/启动/移除操作编排（显式 Record） |
| `agent/src/omni/application/service_exchange.py` | 交换计划/管理员阶段/应用编排（公共桥） |
| `agent/src/omni/application/model_output.py` | 有界实时预览（TypedDict 行、authority Mapping） |
| `agent/src/omni/application/model_calls.py` | 模型调用预检/授权/结算（显式 Record、流对象） |
| `agent/src/omni/application/people.py` | 人物档案用例（显式 Record/类型化调用） |
| `agent/src/omni/application/qq.py` | QQ 监听/会话授权/健康状态（有界任务、显式 Record） |
| `agent/src/omni/application/memory_tools.py` | 记忆工具用例（UnitOfWork 回调显式类型） |
| `agent/src/omni/application/action_review.py` | 动作复核用例（有界任务集/Record 返回显式化） |
| `agent/src/omni/application/bilibili.py` | B 站读取/发布准备（有界任务、显式 Record） |
| `agent/src/omni/application/configuration.py` | 配置读取/创建/保存/预览/诊断（显式 Record） |
| `agent/src/omni/application/context.py` | 上下文压缩/摘要编排（显式 Record、边界窄化） |
| `agent/src/omni/application/operations.py` | 运维查询/诊断导出用例（有界任务集与关闭任务显式化） |
| `agent/src/omni/application/debugging.py` | 显式调试会话语义（维护任务显式 Task[None]） |
| `agent/src/omni/application/delivery.py` | 出站投递准入/发送/结算编排（有界任务、显式 Record） |
| `agent/src/omni/application/terminal_review.py` | 终端核查用例（有界任务/Record 显式化） |
| `agent/src/omni/application/terminals.py` | 终端会话/输入/缩放/关闭编排（有界任务、单写者租约） |
| `agent/src/omni/application/objects.py` | 对象发布/访问用例（有界任务与分页返回显式化） |
| `agent/src/omni/application/memory_organization.py` | 记忆整理用例（有界任务/分页返回显式化） |
| `agent/src/omni/application/memory_semantics.py` | 记忆索引/混合召回/关联评审（有界任务与显式 Record） |
| `agent/src/omni/application/exchange_steps.py` | 服务交换步骤编排（公开部署校验桥/显式 Mapping） |
| `agent/src/omni/application/extensions.py` | 扩展安装/授权/运行期调用编排（显式 Record、有界任务集） |
| `agent/src/omni/application/credentials.py` | 凭据变更/审阅用例（UnitOfWork 回调与有界任务显式化） |
| `agent/src/omni/application/desktop_actions.py` | 语义桌面动作用例（绑定请求显式化/有界任务与守卫回调） |
| `agent/src/omni/application/documents.py` | 源文档/索引/检索编排（有界任务、显式 Record） |
| `agent/src/omni/application/exchange_recovery.py` | 交换隔离/救援计划（进度与授权类型化） |
| `agent/src/omni/application/service_cleanup.py` | 隔离定义审查/清理计划复用 |
| `agent/src/omni/application/service_control.py` | 服务启动/停止/复核编排（显式 Record） |
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

`service_control.py`（94）已在纳入 strict 前清零；`model_stream.py`（75）、`context.py`（38）、`recovery_authority.py`（27）、`memory_semantics.py`（19）、`foreground.py`（24）、`metering.py`（19）与 `release_fence.py`（16）同样已清零，基线表中不再列逐文件例外。`application/` 已覆盖 36 个模块、`adapters/` 4 个模块；`composition/`、`agent/tests/` 尚未开始 strict。

## 4. 推进规则

1. 每次只把一个模块或一组强相关纯函数纳入 strict；纳入前先清零该模块错误，不把“忽略注释”当清零。
2. 新增的 strict 模块必须同时更新 `pyrightconfig.strict.json`、本文件表格与证据；守卫测试会拦截漂移。
3. 基线例外数字只代表对应源码版本；源码变化后需重测，不沿用旧数字。
4. 不允许用 `# type: ignore`/`# pyright: ignore` 掩盖真实错误；确需忽略时必须在类型检查文档写明原因与范围。
5. 全仓 strict 完成前，任何报告不得写“全仓严格类型已通过”。

## 5. 验证

- strict 门禁 0 errors / 0 warnings。
- `agent/tests/test_v2_typecheck_scope.py`：配置与文档表一致、文件存在、只允许 `domain`/`application`/`adapters` 前缀、无目录/通配批量纳入、文档保留“全仓严格类型尚未完成”。
- 相关调用方回归：各批守卫 + 对应模块调用方测试。










