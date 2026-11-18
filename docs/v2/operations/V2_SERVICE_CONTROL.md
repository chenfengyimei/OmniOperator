# V2 服务启动与正常停止

> 文档范围：本页维护该能力合同与所属批次记录。当前默认入口为 V2；[当前交接](../../HANDOFF.md)区分已实现、专项通过和原生验收，历史测试数字不覆盖当前源码。

本页是 D4 服务启停的当前源码合同。它复用部署核验、原生注册检查、每次启动的 HMAC 证明和实际关闭生命周期；不修改正式 V1 入口，不提供 restart、强制终止或失败自动重发。平台支持和实机验收单独记录。

## 命令与授权

开发入口为 `python -m omni.composition.cli service`：

| 命令 | 行为 |
| --- | --- |
| `control-plan BUNDLE start OUTPUT` | 验证当前不可变安装，预先分配本次启动 UUID，生成 `omni-service-control-1` 计划；不启动服务 |
| `control-plan BUNDLE stop OUTPUT` | 只读核验本机进程、监听与签名，将当前 boot_id 固定到计划；不读取或发送 owner.token |
| `apply-control PLAN --approve SHA` | 审阅并批准精确计划后，执行一次启动或正常排空；结果未知时退出码为 1 |
| `inspect-control PLAN` | 读取维护记录；不连接服务或操作管理器 |
| `reconcile-control PLAN` | 只核验已登记操作的当前后置条件，不重发启动或排空 |
| `cancel-control PLAN` | 只释放从未派发、也没有启动领取或排空证据的意图；已派发/未知不能借取消重放 |

计划绑定完整部署、动作、UUID、UTC 时间、目标 boot_id、`graceful_only=true` 和 30 秒观察预算，指纹覆盖全部字段。普通源码/可编辑安装不能通过正式控制入口取得执行资格。操作文件、审计、启动预留和回执均为所有者私密文件。

## 启动

启动要求精确匹配的注册定义、启用状态和静止状态。注册与启停共享 `run/service-maintenance.lock`；两类未解决操作互相阻止，不在其他动作仍被拥有时更换定义。

先持久登记派发，再在实例独占锁下核对数据库身份并写入 `run/launch-ticket.json`。写完预留后释放实例锁，才请求管理器启动，避免新进程因维护方仍持锁而启动失败。没有覆盖现存预留的路径。

新进程取得实例锁后验证预留的部署，先无覆盖发布 `run/service-launches/{operation_id}.json`，再采用预定 boot_id。领取之后启动失败，也不能再次使用同一预留；后续启动拒绝已领取票据，等待核查。没有预留的普通已注册启动继续生成新的随机 boot_id。

平台命令仅表示请求被接受。成功还要求：本次 boot_id 的签名就绪证明、正确进程和监听、可用且 running 的管理器、匹配的注册定义及启用状态。Linux 必须提供与证明相同的正整数 MainPID，macOS 必须提供一致的原生加载 PID；Windows 在管理器提供 PID 时也必须一致。确认后才释放预留。命令超时、启动了其他 boot、定义变化或证明不足均保留 unknown，不重复调用管理器。

| 目标 | 当前启动源码 | 实测口径 |
| --- | --- | --- |
| Windows 用户任务 | 固定 MTA PowerShell/Task Scheduler COM；检查定义、SID、静止状态、AllowDemandStart 后显式 Run | 语法、注入与资源打包验证；未真实注册/Run 用户任务 |
| Linux 用户服务 | 固定 `systemctl --user start`，使用 `--no-block --no-ask-password` | 注入验证；未 Linux 实机验证 |
| macOS 用户服务 | 复核当前图形域、文件及加载定义；未加载用 bootstrap，已加载且匹配用无强制参数的 kickstart；交换预约要求精确 boot 预留 | Swift/ACL/实际返回仍待 Mac 编译与实测，不能据此标记可用 |
| Linux 系统服务 | 显式双端批准、绑定 ControlPlan 的标准库 root broker，固定无阻塞 start，主进程独立核验本次 boot 与 MainPID | 注入与包完整性验证；未真实 root/systemd 验收，详见[系统启动合同](V2_SYSTEM_SERVICE_START.md) |
| macOS 系统服务 | 显式固定 root 包与端点，单次 bootstrap/无强制 kickstart；所有者核验预留 boot 与原生 PID | 管理员单操作及同 schema 交换已接源码；Mac ABI/kqueue/launchd 实测待完成 |

Windows Run 可能仅返回成功而没有实际任务运行，故代码同时检查按需启动设置，并以后置证明决定结果。[Microsoft Run 合同](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/nf-taskschd-iregisteredtask-run)

## 正常停止

停止不调用 Task.Stop、systemctl stop、进程组信号或历史 PID。所有者先取得并核对本次启动的只读 HMAC 证明，之后才向仅限回环访问的 `POST /api/v1/service/drain` 发送签名请求。请求及回执分别使用独立的 HMAC 用途前缀，绑定操作 UUID/SHA、boot_id 和部署 SHA；请求有效期 60 秒，允许最多 5 秒的时钟前偏。

该接口采用本机私密启动密钥授权，不使用业务 API 的 Bearer 令牌。客户端不传 Cookie、owner.token 或密钥原文，禁用代理、重定向和压缩回执；响应有界，实际 HTTP 与文件任务受既有容量和期限管理。请求正文最多 4096 字节，重复字段、错误签名、其他启动身份和非回环请求均拒绝。

服务先持久写入 `run/service-drains/{boot_id}.json`，再关闭所有组件的任务入口并请求 Uvicorn 正常退出。完整的登记和排空请求由服务拥有的任务执行，客户端断开不会在“已登记”和“停止接收”之间丢失操作。同一操作重复请求取得同一回执，其他操作不能接管该次排空。

HMAC 回执只证明排空已接受。最终成功须同时看到同一 boot 的 stopped 元数据和实际实例锁释放；残留工作不能报成已停止。正常 HTTP 排空和组件关闭共用剩余预算；预算耗尽后记录 `runtime.shutdown_pending`，保持事件循环、进程与锁，继续回收已有任务，不在预算到期时强杀。控制端观察超时记录 unknown，稍后只能核查原操作。

停止支持已经运行、可证明身份且提供排空回调的本机固定部署；它不等于该平台的服务安装已验收，也不禁用以后登录时的独立启动。单独嵌入的 `http_app` 未提供 shutdown 回调时不开放此接口。[Uvicorn 正常关闭](https://uvicorn.dev/server-behavior/)

## 审计与关闭归属

注册与启停共用连续、有界、哈希相连的维护日志实现，分别位于 `run/service-operations` 和 `run/service-controls`。实际外部动作前先登记 dispatch，unknown 不等于普通失败。可见结果区分已观察到的业务后置条件与 `cleanup_complete`；端口回收未完成时不释放活动标记及维护锁。正常控制端关闭或后续只读核查完成后才能结清。

新版本的部署运行描述为 **`omni-service-runtime-2`**。`python`/`python_realpath`/`python_sha256`继续表示启动器和链接链；`process_python`/`process_python_sha256`单独绑定实际进程映像。Windows venv 启动器可能启动另一 PID 的基础 Python，不能把启动器路径当作服务映像，也不能仅因路径不同就放宽身份校验。旧描述不补默认值，需要从实际安装重新 capture、plan 和生成管理员包；数据库版本不因本项改变。

本批隔离源码 HTTP 进程已在 Windows 完成 HMAC 排空、正常退出及锁释放，并检查真实进程映像/监听；该样例使用专用测试 argv，不代替规范服务 argv、不可变安装、Task Scheduler、launchd、systemd 或三平台实机验收。用户交换协调源码现已接入，见[交换合同](V2_SERVICE_EXCHANGE.md)；系统启动授权、跨 schema 升级、原生验收及 72 小时运行仍待推进，当前未形成完整 V2。
