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
| `agent/src/omni/application/media.py` | 媒体源/任务/转写/帧表编排（有界任务、显式 Record） |
| `agent/src/omni/application/images.py` | 图片准入/上下文选择/线材物化（有界任务、显式 Record） |
| `agent/src/omni/application/service_deployment.py` | 服务部署准备/验证/状态组合（公共桥 + 显式 Record） |
| `agent/src/omni/application/service_operations.py` | 服务安装/启动/移除操作编排（显式 Record） |
| `agent/src/omni/application/skills.py` | 技能导入/编辑/启用/回滚/披露编排（有界任务、显式 Record） |
| `agent/src/omni/application/service_exchange.py` | 交换计划/管理员阶段/应用编排（公共桥） |
| `agent/src/omni/application/model_output.py` | 有界实时预览（TypedDict 行、authority Mapping） |
| `agent/src/omni/application/model_calls.py` | 模型调用预检/授权/结算（显式 Record、流对象） |
| `agent/src/omni/application/people.py` | 人物档案用例（显式 Record/类型化调用） |
| `agent/src/omni/application/ports.py` | 应用层端口协议（显式 Record/任务/回调边界） |
| `agent/src/omni/application/qq.py` | QQ 监听/会话授权/健康状态（有界任务、显式 Record） |
| `agent/src/omni/application/memory_tools.py` | 记忆工具用例（UnitOfWork 回调显式类型） |
| `agent/src/omni/application/action_review.py` | 动作复核用例（有界任务集/Record 返回显式化） |
| `agent/src/omni/application/bilibili.py` | B 站读取/发布准备（有界任务、显式 Record） |
| `agent/src/omni/application/configuration.py` | 配置读取/创建/保存/预览/诊断（显式 Record） |
| `agent/src/omni/application/context.py` | 上下文压缩/摘要编排（显式 Record、边界窄化） |
| `agent/src/omni/application/operations.py` | 运维查询/诊断导出用例（有界任务集与关闭任务显式化） |
| `agent/src/omni/application/debugging.py` | 显式调试会话语义（维护任务显式 Task[None]） |
| `agent/src/omni/application/delivery.py` | 出站投递准入/发送/结算编排（有界任务、显式 Record） |
| `agent/src/omni/application/desktop.py` | 桌面观察视图/目标/验证/捕获编排（短租约、显式 Record） |
| `agent/src/omni/application/desktop_portal.py` | 窗口共享授权/会话/捕获编排（显式 Record、有界任务） |
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
| `agent/src/omni/application/engine.py` | 运行准入/调度/工具执行/关闭编排（有界任务、显式 Record） |
| `agent/src/omni/application/exchange_recovery.py` | 交换隔离/救援计划（进度与授权类型化） |
| `agent/src/omni/application/service_cleanup.py` | 隔离定义审查/清理计划复用 |
| `agent/src/omni/application/service_control.py` | 服务启动/停止/复核编排（显式 Record） |
| `agent/src/omni/adapters/metrics.py` | 运行指标观测适配器 |
| `agent/src/omni/adapters/build_identity.py` | 构建身份常量与代码后缀 |
| `agent/src/omni/adapters/primitives.py` | 时钟/ID 原语 Protocol 与实现 |
| `agent/src/omni/adapters/timezones.py` | 时区数据库标识读取边界 |
| `agent/src/omni/adapters/platform/exchange_deployments.py` | 交换部署布局/路径校验（平台适配） |
| `agent/src/omni/adapters/platform/workspaces.py` | 工作区路径解析/校验（平台适配） |
| `agent/src/omni/adapters/persistence/credential_predicates.py` | 凭据谓词 SQL 片段（持久化适配） |
| `agent/src/omni/adapters/persistence/history_sequence.py` | 历史游标序列持久化辅助 |
| `agent/src/omni/adapters/persistence/migration_config.py` | 迁移配置读取边界 |
| `agent/src/omni/adapters/persistence/search_schema.py` | 检索 schema 定义 |
| `agent/src/omni/adapters/persistence/deployed_instance.py` | 已部署实例校验（只读，不迁移） |
| `agent/src/omni/adapters/persistence/delivery_predicates.py` | 出站待处理/未决谓词（持久化适配） |
| `agent/src/omni/adapters/persistence/sqlite_access.py` | 离线 SQLite 连接（只读/备份，含 WAL） |
| `agent/src/omni/adapters/token_counter.py` | 本地保守 token 估算（不取模型元数据） |
| `agent/src/omni/adapters/images.py` | 图像编解码/校验（有界任务、Pillow 校验） |
| `agent/src/omni/adapters/platform/secrets.py` | OS 凭据库/外键加密 vault 适配 |
| `agent/src/omni/adapters/platform/macos_service_state.py` | launchd 定义/覆盖纯比较（stdlib-only） |
| `agent/src/omni/adapters/platform/service_handover.py` | 双阶段管理员交接（按序打开、原生锁释放） |
| `agent/src/omni/adapters/credentials.py` | 凭据读写线程池/失效与关闭（有界 Future 集） |
| `agent/src/omni/adapters/skill_packages.py` | 内存内技能 Markdown/ZIP 解析（不解压/不执行） |
| `agent/src/omni/adapters/platform/service_control_files.py` | 服务启停日志/一次性启动预留 |
| `agent/src/omni/adapters/platform/service_launch.py` | 一次性启动预留（实例锁下，不启动进程） |
| `agent/src/omni/adapters/persistence/migrations/versions/v0003.py` | 冻结迁移版本 v0003（人物/证据/记忆上下文） |
| `agent/src/omni/adapters/platform/processes.py` | 受管进程组与 Windows Job Object（不终止未校验 PID） |
| `agent/src/omni/adapters/platform/desktop.py` | 桌面辅助进程（有界单帧、两阶段授权、不探测即构造） |
| `agent/src/omni/adapters/platform/service_query.py` | 只读系统服务查询（有界输出与保留进程所有权） |
| `agent/src/omni/adapters/persistence/audit_pruning.py` | 离线审计裁剪绑定（计划/应用/取消，备份校验归共享设施） |
| `agent/src/omni/adapters/persistence/event_pruning.py` | 离线事件裁剪绑定（计划/应用/取消，备份校验归共享设施） |
| `agent/src/omni/adapters/workers/client.py` | 有界 JSON IPC 工作进程客户端（能力清单与宿主回调预算） |
| `agent/src/omni/adapters/platform/exchange_files.py` | 服务交换见证与维护预留（公共桥，不触库/不原生调用） |
| `agent/src/omni/adapters/platform/service_cleanup_files.py` | 服务清理日志与双家预留（保留旧未知记录与栅栏） |
| `agent/src/omni/adapters/platform/darwin_admin_policy.py` | macOS 管理员策略（本地账户校验与受保护根工件） |
| `agent/src/omni/adapters/platform/wheel_smoke.py` | 候选树子进程冒烟（不安装/不切换/不迁移） |
| `agent/src/omni/adapters/vector_files.py` | 本地向量包文件校验与启动选择（不导入模型/不下载） |
| `agent/src/omni/adapters/persistence/schema_upgrade.py` | 受审迁移目录与锁定栅栏内的库升级（不重放） |
| `agent/src/omni/adapters/platform/service_admin.py` | Linux 系统服务注册/启动代理（不执行属主代码、不触业务库） |
| `agent/src/omni/adapters/platform/darwin_admin_probe.py` | macOS 管理员原生只读探测（CoreFoundation/SM 有界读取） |
| `agent/src/omni/adapters/platform/linux_x11_wire.py` | X11 只读线上协议（已校验套接字，无发现/输入/重连） |
| `agent/src/omni/adapters/platform/service_readiness.py` | 每次启动的 HMAC 就绪证明（不读不传属主凭据） |
| `agent/src/omni/adapters/platform/desktop_native.py` | Windows 桌面只读诊断与进程/窗口身份（不读界面内容） |
| `agent/src/omni/adapters/diagnostics.py` | 有界单写 JSON 诊断（脱敏与载荷预算） |
| `agent/src/omni/adapters/persistence/object_repository.py` | 对象元数据/固定与真实外键引用（不触文件） |
| `agent/src/omni/adapters/persistence/releases.py` | 发布一致快照与新目录隔离恢复（调用方持实例锁） |
| `agent/src/omni/adapters/media.py` | 可选媒体处理子进程（有界帧与预算，不触业务库） |
| `agent/src/omni/adapters/workers/credentials.py` | 固定单次 OS 凭据辅助进程（无明文回退） |
| `agent/src/omni/adapters/platform/service_admin_policy.py` | Linux 服务管理员根策略（本地账户与受保护根工件） |
| `agent/src/omni/adapters/process_diagnostics.py` | 进程级日志与异常钩子（不保留异常对象/不重复链式） |
| `agent/src/omni/adapters/log_query.py` | 有界诊断分页与跨重命名游标（共享写锁） |
| `agent/src/omni/adapters/workers/android_adb.py` | 有界 ADB smart-socket 客户端（不启动服务/不配对） |
| `agent/src/omni/adapters/platform/darwin_service_admin.py` | macOS 注册与单次启动根后端（固定 launchctl，不强制重启） |
| `agent/src/omni/adapters/extensions/hosts.py` | 扩展宿主池（有界进程归属与吊销，不触库/凭据） |
| `agent/src/omni/adapters/platform/release_files.py` | 发布日志/安装字节与启动栅栏（不触库、不原生注册） |
| `agent/src/omni/adapters/platform/wheel_environment.py` | 离线候选 venv 环境（无网络安装暂存 wheel） |
| `agent/src/omni/adapters/platform/wheel_install.py` | 运行时切换/迁移/回滚指针（不停服、不迁移库） |
| `agent/src/omni/adapters/object_store.py` | 受控对象文件存储（无链接/越界） |
| `agent/src/omni/adapters/persistence/migrations/env.py` | Alembic 环境入口（迁移配置） |
| `agent/src/omni/adapters/persistence/migrations/versions/v0001.py` | 冻结迁移版本 v0001 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0002.py` | 冻结迁移版本 v0002 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0004.py` | 冻结迁移版本 v0004 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0007.py` | 冻结迁移版本 v0007 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0008.py` | 冻结迁移版本 v0008 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0009.py` | 冻结迁移版本 v0009 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0011.py` | 冻结迁移版本 v0011 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0012.py` | 冻结迁移版本 v0012 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0013.py` | 冻结迁移版本 v0013 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0014.py` | 冻结迁移版本 v0014 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0015.py` | 冻结迁移版本 v0015 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0016.py` | 冻结迁移版本 v0016 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0017.py` | 冻结迁移版本 v0017 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0018.py` | 冻结迁移版本 v0018 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0019.py` | 冻结迁移版本 v0019 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0020.py` | 冻结迁移版本 v0020 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0021.py` | 冻结迁移版本 v0021 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0022.py` | 冻结迁移版本 v0022 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0023.py` | 冻结迁移版本 v0023 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0024.py` | 冻结迁移版本 v0024 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0025.py` | 冻结迁移版本 v0025 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0026.py` | 冻结迁移版本 v0026 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0028.py` | 冻结迁移版本 v0028 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0029.py` | 冻结迁移版本 v0029 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0030.py` | 冻结迁移版本 v0030 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0031.py` | 冻结迁移版本 v0031 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0032.py` | 冻结迁移版本 v0032 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0033.py` | 冻结迁移版本 v0033 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0034.py` | 冻结迁移版本 v0034 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0035.py` | 冻结迁移版本 v0035 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0036.py` | 冻结迁移版本 v0036 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0037.py` | 冻结迁移版本 v0037 |
| `agent/src/omni/adapters/platform/darwin_admin_peer.py` | macOS 管理员对端帧校验 |
| `agent/src/omni/adapters/platform/permissions.py` | 平台权限检查边界 |
| `agent/src/omni/adapters/platform/publication.py` | 发布文件写入边界 |
| `agent/src/omni/adapters/platform/service_admin_commands.py` | 服务管理员命令构造 |
| `agent/src/omni/adapters/platform/wheel_artifact.py` | wheel 产物校验 |
| `agent/src/omni/adapters/workers/darwin_admin_probe.py` | macOS 管理员探测 worker |
| `agent/src/omni/adapters/persistence/action_predicates.py` | 动作复核当前解析谓词（持久化适配） |
| `agent/src/omni/adapters/persistence/history_repository.py` | 历史游标分页仓储 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0006.py` | 冻结迁移版本 v0006（文档来源与索引） |
| `agent/src/omni/adapters/persistence/migrations/versions/v0027.py` | 冻结迁移版本 v0027（QQ 入站归属） |
| `agent/src/omni/adapters/persistence/operations_repository.py` | 运维聚合与凭据审计仓储 |
| `agent/src/omni/adapters/platform/darwin_admin_commands.py` | macOS 管理员命令构造 |
| `agent/src/omni/adapters/platform/darwin_admin_files.py` | macOS 管理员文件身份校验 |
| `agent/src/omni/adapters/platform/systemd_state.py` | systemd 状态解码边界 |
| `agent/src/omni/adapters/platform/wheel_stage.py` | wheel 暂存收据 |
| `agent/src/omni/adapters/platform/wheel_switch.py` | wheel 切换计划 |
| `agent/src/omni/adapters/platform/wheel_status.py` | 安装状态汇总 |
| `agent/src/omni/adapters/persistence/repository_base.py` | 仓储基类（审计/记录/游标辅助） |
| `agent/src/omni/adapters/platform/wheel_backup.py` | wheel 更新备份收据 |
| `agent/src/omni/adapters/platform/wheel_prepare.py` | wheel 候选解包与准备收据 |
| `agent/src/omni/adapters/platform/wheel_rollback.py` | wheel 回滚演练收据 |
| `agent/src/omni/adapters/platform/wheel_uninstall.py` | 候选卸载与显式清理 |
| `agent/src/omni/adapters/persistence/offline_plans.py` | 离线维护计划读取/校验 |
| `agent/src/omni/adapters/persistence/retention_repository.py` | 保留/擦除会话失效仓储 |
| `agent/src/omni/adapters/platform/desktop_capture.py` | 桌面截图文件身份校验 |
| `agent/src/omni/adapters/platform/macos_helper.py` | macOS 辅助源指纹与文件校验 |
| `agent/src/omni/adapters/platform/macos_service.py` | macOS launchd 查询边界（原生身份校验） |
| `agent/src/omni/adapters/platform/paths.py` | 实例路径与启动栅栏互斥 |
| `agent/src/omni/adapters/platform/service_admin_bundle.py` | 管理员工件物化（不安装/不提权） |
| `agent/src/omni/adapters/persistence/history_protection.py` | 历史保留保护原因（运行/引用/审计） |
| `agent/src/omni/adapters/workers/terminal.py` | 终端 worker 帧解码/打开校验 |
| `agent/src/omni/adapters/persistence/sqlalchemy_access.py` | SQLAlchemy 引擎事务/PRAGMA 设置 |
| `agent/src/omni/adapters/platform/linux_x11_socket.py` | X11 授权 cookie 解析 |
| `agent/src/omni/adapters/workers/desktop.py` | 桌面 worker 帧解码 |
| `agent/src/omni/adapters/persistence/execution_pruning.py` | 执行正文保留计划（读/建/应用/取消） |
| `agent/src/omni/adapters/persistence/object_inventory.py` | 离线对象登记/物理清单校验 |
| `agent/src/omni/adapters/persistence/reference_release.py` | 引用释放计划（读/建/应用/取消） |
| `agent/src/omni/adapters/workers/terminal_gate.py` | Windows ConPTY 启动门（Job 归属后放行） |

`pyrightconfig.strict.json` 的 `include` 必须与本表完全一致；`agent/tests/test_v2_typecheck_scope.py` 会双向校验。

## 3. 基线例外（未纳入，不得写成已严格化）

在 `1a18fae` 源码上测得（仅记录，不构成门禁）：

| 范围 | strict 错误数 |
| --- | --- |
| `agent/src/omni/domain`（全部 53 文件） | 2321 |

`service_control.py`（94）已在纳入 strict 前清零；`model_stream.py`（75）、`context.py`（38）、`recovery_authority.py`（27）、`memory_semantics.py`（19）、`foreground.py`（24）、`metering.py`（19）与 `release_fence.py`（16）同样已清零，基线表中不再列逐文件例外。`application/` 已覆盖 43 个模块、`adapters/` 126 个模块；`composition/`、`agent/tests/` 尚未开始 strict。

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










