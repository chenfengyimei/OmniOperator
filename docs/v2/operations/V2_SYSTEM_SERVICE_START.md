# V2 Linux 系统服务显式启动

> 文档范围：本页维护该能力合同与所属批次记录。当前默认入口为 V2；[当前交接](../../HANDOFF.md)区分已实现、专项通过和原生验收，历史测试数字不覆盖当前源码。

## 两端分别审阅

1. 在普通所有者环境捕获不可变安装、生成部署包并完成独立的系统注册操作。系统 unit 必须精确匹配、已启用且已停止。
2. 普通所有者执行 `service control-plan BUNDLE start NEW_PLAN`。`omni-service-control-1` 固定完整部署、操作 UUID/SHA、`start`、预分配的 `boot_id`、30 秒观察预算和 `graceful_only=true`；生成计划不启动服务。
3. 从相同固定源码生成新管理员包。包格式为 `omni-service-admin-bundle-3`，协议为 `omni-service-admin-3`，只有标准库依赖。将包转交至受保护、root 所有的位置并核验指纹，依照[管理员包前提](V2_SERVICES.md)选择受信 Python 3.12+。本项目不自动提权、安装或启动管理员包。
4. root 审阅完整控制计划并批准该 SHA，以固定入口绑定普通所有者 UID。所有者另行批准同一计划，并显式指定该操作的 socket 和管理员包 SHA；没有自动查找、隐式本地回退或凭据提升。

```text
# 以下仅为命令形状，占位符须替换；不是本批已执行的服务操作
/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 ROOT_OWNED_PYTHON_3_12 -I -S ROOT_BUNDLE/service-admin.py --operation OWNER_START_PLAN --approve CONTROL_SHA256 --owner-uid OWNER_UID

OWNER_PYTHON -I -m omni.composition.cli service apply-control OWNER_START_PLAN --approve CONTROL_SHA256 --admin-socket /run/omnioperator-service-admin/CONTROL_UUID.sock --admin-bundle-sha256 ADMIN_BUNDLE_SHA256

# 已登记操作的只读核查：重新准备绑定原计划的管理员端，只观察，不重发 change
OWNER_PYTHON -I -m omni.composition.cli service reconcile-control OWNER_START_PLAN --admin-socket /run/omnioperator-service-admin/CONTROL_UUID.sock --admin-bundle-sha256 ADMIN_BUNDLE_SHA256
```

socket 与管理员包 SHA 必须同时提供，且只开放给 `apply-control` / `reconcile-control` 的 Linux 系统 `start`。root 不接受 `stop` 控制计划、任意 argv、其他部署或其他平台。普通正常停止继续走本次 boot 的 HMAC 排空端口；不新增 root stop、restart 或 force。

## 派发与成功判定

所有者先复核不可变部署和管理器前置条件，持久写入控制派发记录；在短实例锁内写入一次性启动预留，释放实例锁后才调用管理员端。维护锁与实际工作归属持续保留。

管理员端重新核验本地普通账号、root 文件与固定 systemctl，要求已加载定义和磁盘 unit 均匹配，状态明确停止、PID 为空、已启用，并拒绝 UnitPath 中适用的 drop-in。派发前再次核验权限及定义。root 先无覆盖持久登记 `dispatch.json`，之后只允许：

```text
/usr/bin/systemctl --system start EXACT_UNIT --no-block --job-mode=fail --no-ask-password
```

`--no-block` 只提交作业；`--job-mode=fail` 拒绝替换冲突作业。返回码 0 只记录 `acceptance_only=true`，不能据此宣称主程序已就绪。[systemctl 作业与等待合同](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html)

所有者必须取得预留 `boot_id` 的签名就绪证明、实际进程与监听证明，以及精确注册定义、已启用且 `running` 的管理器报告。Linux 要求正整数 `MainPID` 与被证明的应用 PID 相同，Mac 同样要求固定原生查询返回的加载 PID 一致；布尔值、缺失 PID、未知状态或旧 boot 均拒绝。Windows 管理器不提供 PID 时保留其现有独立进程证明。

启动前无效可拒绝；所有者派发后的超时、崩溃、断线或证据不足保留 `unknown` 和启动预留。管理员派发见证跨进程保留，同一 ID 再次请求不能重放；核查仅重读后置条件。root 不读取应用数据库、`owner.token`、`readiness.key` 或业务正文。

## 有界传输与实际回收

请求绑定计划 ID/SHA、管理员包 SHA、请求 UUID 和严格序号。每帧最多 64 KiB，每连接最多 192 帧；最后一帧专供 `close`，不允许就绪轮询耗尽关闭容量。没有并发请求、流水操作或失败自动重连。

一条生产 `close` 请求等待实际受管命令与子进程回收，只有完成后才返回关闭证明。所有者等待超时或取消只释放等待方，RPC、root 命令、传输和锁仍有所有者。断线、坏帧及回收失败不构成关闭证明；root 保留实例锁继续清理，普通控制 CLI 保留事件循环及维护锁，直至实际关闭成立。

root 审计格式为 `omni-service-admin-audit-3`，包含完整批准 ID/SHA、子操作键、操作、部署实例、双方 UID、包 SHA、动作、目标 boot、阶段及固定结果元数据；不包含 systemctl 原始输出或应用内容。协议和包升级需要重新捕获运行描述、生成部署/控制计划及管理员包，旧指纹不得混用。

## 验证边界

离线验证使用假管理器、假 root IO 和受控帧，覆盖固定命令、先登记后派发、拒绝漂移/权限/禁用、一次启动、错误 PID/boot、未知核查、容量与断线回收、CLI 接线、标准库隔离包及字节指纹。它不执行 root 提权、systemd 服务操作或真实账号动作。

Linux 实机须在隔离普通测试账号、受保护安装和临时实例中验证 root socket、实际 UnitPath/加载定义、pidfd/subreaper、成功/失败启动、冲突作业、进程退出、取消、恢复和残留；结果记录到独立平台证据。注入通过不能标记 Linux 已支持，也不能替代三平台全量或 72 小时验收。

## 历史批次状态（非当前结论）

以下原记录中的默认入口与完成度仅属于当时源码；当前默认入口为 V2，实际进度以当前交接和任务为准。

> 本页定义 D4 的系统启动授权源码合同。Linux `linux-system` 通过独立、显式审阅的管理员包提交一次启动；它复用现有控制计划、启动预留和本次运行证明，不新增业务表。Linux 系统完整交换已另行接入[父会话与子操作合同](V2_SYSTEM_SERVICE_EXCHANGE.md)；macOS 系统启动及真实 Linux 安装验收仍待实施。默认入口仍为 V1。
