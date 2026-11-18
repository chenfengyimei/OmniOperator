# V2 隔离前代注册的核查与清理

> 文档范围：本页维护该能力合同与所属批次记录。当前默认入口为 V2；[当前交接](../../HANDOFF.md)区分已实现、专项通过和原生验收，历史测试数字不覆盖当前源码。

## 命令与审阅

开发入口为 `python -m omni.composition.cli exchange`：

| 命令 | 行为 |
| --- | --- |
| `cleanup-plan EXCHANGE source或target OUTPUT` | 从完整 isolated 历史生成私密 `omni-service-cleanup-1` 计划，不查询或改变管理器 |
| `inspect-cleanup PLAN` | 读取该清理的持久记录；不连接管理器，不把历史成功当作当前缺失 |
| `apply-cleanup PLAN --approve SHA` | 批准完整计划后，只派发一次精确 uninstall；已明确缺失时只记录观察，不派发移除 |
| `reconcile-cleanup PLAN` | 重新查询原生缺失后置条件，可登记本次观察；不重发移除 |
| `cancel-cleanup PLAN` | 仅结清有意图但从未持久派发的清理，已经派发或 unknown 不可借取消重试 |

计划固定完整前代交换、完整 isolated 历史的数量与指纹、隔离见证、所选 source/target、唯一清理 UUID、唯一 uninstall 操作和派生日志位置。`starts_service=false`、`deletes_instance_data=false`、`preserves_original_records=true`、`late_effects_possible=true` 都进入批准指纹。Mac 的 `stops_service=true` 保留原注册端口对 bootout 潜在影响的声明。

source 和 target 可能使用同一个原生名称，但定义分别指向不同版本。必须选择实际需要核查的部署；另一个定义即使属于同一实例，也不能被当作所选定义移除。恢复版本使用独立名称，清理不会推导或扫描其他服务名称。

```text
# 命令形状，仅在隔离验收环境审阅并授权后执行真实 apply
OWNER_PYTHON -I -m omni.composition.cli exchange cleanup-plan ISOLATED_EXCHANGE target NEW_PRIVATE_PLAN
OWNER_PYTHON -I -m omni.composition.cli exchange reconcile-cleanup NEW_PRIVATE_PLAN
OWNER_PYTHON -I -m omni.composition.cli exchange apply-cleanup NEW_PRIVATE_PLAN --approve FULL_CLEANUP_SHA
```

输出不得位于两份 home、工作目录、安装、环境、bundle、备份、原日志或清理受管目录中。日志位置固定派生为原交换日志旁的 `.<journal-name>.cleanup/<native-key-sha>/<cleanup-uuid>`，调用方不能提供任意清理目录。派生目录与上述资产重叠时拒绝生成计划。所有清理记录为私密、有界、连续指纹日志；未知文件和部分发布不自动删除。

Linux/macOS 系统清理需要独立管理员会话：将计划内的 `operation` 完整 JSON 提供给管理员，root 批准该 uninstall SHA；所有者仍批准外层 cleanup SHA。`apply-cleanup/reconcile-cleanup` 同时传入 `--admin-socket /run/omnioperator-service-admin/OPERATION_UUID.sock --admin-bundle-sha256 SHA`，缺少参数时在派发前拒绝。不是完整交换 UUID 或外层 cleanup UUID，不复用交换 root 会话；普通查询/取消仍不需要 root。包、来源核验及命令形状见[系统交换合同](V2_SYSTEM_SERVICE_EXCHANGE.md)。

## 执行边界

执行先验证旧部署的完整 bundle、代码/资源、解释器链及配置指纹；这是仅供移除的静态资格，不导入或运行旧安装，也不授予旧程序启动资格。定义、源码或部署包缺失/变化时拒绝；不能用当前安装冒充旧安装。

在原交换锁、排序后的两份维护锁和两份实例锁下，再次验证完整 isolated 历史、隔离见证、精确原预约和两份启动闸门。嵌套救援仍使用原始隔离授权对应的闸门。任一旧实例或实际管理器任务仍被拥有时，清理不能越过锁。

清理继续调用原有 `ServiceOperations` 与 `ServiceRegistration`：检查实际账号、目标定义、静止状态；先持久登记 dispatch，才调用一次卸载，之后核验实际缺失。Linux 不调用 stop，Windows 不按历史 PID 操作进程，Mac 保留固定定义及加载核验。外部调用、用户数据或模型调用不进入清理日志。

清理不打开 SQLite。原数据库、对象和历史记录保持原位；只在两份 home 的 `run/service-cleanups/<native-key-sha>.json` 增加独立清理预约，并在外部目录保存新审计。新预约不替换原 unknown、service-exchange 或 release-fence。

同一管理器、域、名称与作用域使用同一原生键；即使换 source/target 或生成新 UUID，已有未知清理仍阻止再次派发。两份 home 共享预约也阻止相关隔离链绕过同一旧 home 的未结清清理。

## 中断、取消与回收

- 新预约先于外部 dispatch 落盘。两份 home 只写入一份时保留意图；不能再次 apply，可核查或在确无 dispatch 时取消。
- 已写 dispatch 后发生崩溃、异常或结果未知，只能重新观察。当前缺失可结清该清理，不能据此改写更早的未知注册效果。
- 取消等待者不取消实际操作。辅助进程回收未完成时，保留原交换、维护和实例锁，以及清理预约；CLI 初始关闭预算耗尽后继续拥有事件循环。
- 只有清理终态与实际回收成立，才释放两份新预约。释放中断可以由原计划完成；原数据、闸门、未知记录和交换预约始终保留。
- 日志和新预约的属主、权限、链接、指纹或连续性无法核验时拒绝；不自动修复原记录，也不清除其他计划的预约。

## 成功与当前状态

清理成功只表示对应时间已观察到注册缺失。原未知请求的迟到效果仍可能重建旧注册；它指向的旧 home 继续被闸门封锁，不影响独立名称的救援服务。

`inspect-cleanup` 和重复 apply 返回历史回执，`live_observation=false`；只有 reconcile 查询当前状态，`live_observation=true`。结果保留 `original_effect=unknown`、`late_effects_possible=true` 及缺失观察时间，不能把旧回执当作永久不存在。当前注册重新出现时，reconcile 返回 unknown，不自动重发；需要另行审阅后续清理。

救援交换本身不运行这项核查。其 `predecessor_cleanup_pending=true` / `predecessor_cleanup_status=not_observed` 表示没有观察当前前代状态，应结合显式清理报告判断；新服务就绪不自动清除此项，也不代表旧 unknown 已解决。

## 验证与限制

离线验证使用私密文件、真实锁、隔离的安装文件和注入管理器；覆盖未知防重发、同键不同侧互斥、取消、双预约中断、数据/原记录保持、错误定义、权限/指纹变化、CLI 与静态旧安装验证。真实服务未被卸载或启停。

Windows/Linux/macOS 分别需在独立账号和不可变安装中验收实际注册、拒绝与恢复；Mac 还需 Swift、ACL、加载字典和图形会话前提。Linux 系统交换与本项清理已接线，真实 root/systemd 仍待验收；macOS 系统实测、跨管理员协议、跨 schema、业务迁移和三平台常驻仍待完成。本批通过不等于完整 D4 或 V2 已交付。

Mac系统清理采用同一nested uninstall合同，socket目录改为 `/private/var/run/omnioperator-service-admin`，仍须单独批准操作UUID/SHA；不是外层cleanup或父交换UUID。

## 历史批次状态（非当前结论）

以下原记录中的默认入口与完成度仅属于当时源码；当前默认入口为 V2，实际进度以当前交接和任务为准。

> 本页定义 D4 的显式清理合同。清理只处理已完成隔离的交换中所选的一份原生注册，不删除数据、原始未知回执、启动领取、交换预约或启动闸门。当前源码复用 Windows/Linux/macOS 用户注册端口及显式授权的 Linux/macOS 系统 root 端口；实际服务操作和平台验收尚未执行，默认入口仍为 V1。
