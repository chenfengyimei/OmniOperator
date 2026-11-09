# V2 服务交换

本页描述 D4 当前的同 schema 交换源码。它将已准备的版本从旧注册串联到目标注册和本次启动就绪；不等于整个更新系统或 V2 完成。正式入口仍为 V1，真实系统服务尚未在本批操作。

## 范围与前置条件

- 在目标的不可变安装中执行。`release prepare` 必须已经完成一致快照、对象验证、恢复隔离及目标 bundle；源实例在准备前已正常停止。也可用显式批准的 `release online-plan / prepare-online` 串联正常排空与准备，见[恢复及在线准备合同](V2_EXCHANGE_RECOVERY.md)。
- 源和目标必须满足既有 release 合同：同实例、同所有者、同 schema、独立环境和目录，采用相同启动闸门。旧数据不做兼容导入。
- 当前交换源码接受 Windows/Linux/macOS 用户目标，以及显式 root 会话绑定的 Linux/macOS 系统目标。Mac 用户/系统和 Linux 系统均通过持久预约限制提前激活；系统会话、共同源码指纹限制及命令见[系统交换合同](V2_SYSTEM_SERVICE_EXCHANGE.md)。Swift、默认 ACL、加载字典、launchd 和 systemd 实际行为仍须各平台验收；跨schema用户服务及共享管理员协议4的分阶段系统交接已接源码；不兼容协议的隐式桥接不开放。
- 源移除由目标代码执行。只验证源公开代码、解释器、bundle 和 release 连续性，不导入或运行旧安装；这种资格仅提供给绑定的源卸载用例，不供启动或安装任意版本使用。

## 显式命令

开发入口为 `python -m omni.composition.cli exchange`：

| 命令 | 行为 |
| --- | --- |
| `plan RELEASE JOURNAL OUTPUT` | 只读核验 prepared release，产生完整计划；不注册、不启动，也不创建交换预约 |
| `apply PLAN --approve SHA` | 批准完整计划指纹后，依次执行四个固定步骤 |
| `resume PLAN --approve SHA` | 先只读核查已派发但未完成的步骤；确认后继续尚未派发的步骤，因此仍要求完整批准 |
| `inspect PLAN` | 读取有界持久记录；不连接原生管理器，`ready=false`，历史完成用 `historically_ready` 表达 |
| `abandon PLAN --approve SHA` | 子操作均已结清时释放交换预约；保留数据、目录选择、注册效果和审计，不执行回滚 |

apply/resume 未完成时返回退出码 1。当前 `omni-service-exchange-4` 绑定 release、源卸载、目标安装、目标启动计划及预定 boot_id；各子操作 ID、SHA 和顺序均不可替换。计划明确 `starts_service=true`、`automatic_rollback=false`；`stops_service` 继承源卸载影响，因此 Mac 为 true，Windows/Linux 为 false。Mac 用户/系统和 Linux 系统均明确 `releases_activation_on_abandon=true`，批准放弃也会解除启动限制，允许已注册目标以后自动激活。旧计划和预约不补默认值，须从实际安装重新生成。

## 顺序与持久证明

1. `remove_source`：复用注册用例，在源实例锁下核验和移除匹配的静止定义。已明确缺失时可以登记只读后置证明，不执行移除。
2. `select_target`：复用 release 双实例锁、快照和数据库连续性检查；先封住源，再开放目标。选择结束释放实例锁。
3. `install_target`：在目标实例锁下注册精确目标定义；不在持锁期间启动应用。已存在、匹配、启用且静止时可以只登记后置证明。
4. `start_target`：复用单次启动预留、原生启动及本次 boot 的 HMAC 就绪核验。目标应用取得实例锁时，交换方只持有维护预约和交换日志锁。

每步调用前先持久写入 dispatch，子用例仍拥有独立意图/派发/回执。完成证明只包含步骤、操作身份、结果、回收状态、错误码和验证方式，不复制正文、凭据、原生命令输出或完整配置。子进程回收未完成不得推进下一步。

外部交换日志最多 64 条，单条 8 KiB，包含连续序号和前项指纹，检查完整状态顺序；为离线隔离预留事件容量。日志必须位于数据、bundle、环境、工作目录及快照以外。首次声明预约在两个 home 的维护锁下核验，不覆盖既有计划或其他交换。空日志目录、plan 已落盘但 intent 未落盘、单侧预约发布中断都可以使用同一计划继续声明；发生未知内容或指纹变化则拒绝。

两个 home 的 `run/service-exchange.json` 在步骤间保持预约。普通注册、启停和独立目录选择不能越过它；协调器子用例必须携带同一预约身份。单个子步骤仍使用原有维护互斥与实例锁，避免长期占用实例锁妨碍目标启动。

## 登录/系统激活与预留

同一预约还携带 `omni-service-activation-1`：固定目标 home、实例、部署 SHA、控制操作 UUID/SHA 和 boot_id，预约自身有独立内容指纹。首次 claim 在开放目标前落盘，且在安装、启用、启动及未知核查期间保留。源启动被拒绝；目标没有精确启动预留时，也不能因登录项出现而生成随机 boot。开发入口不携带固定部署，不能借此越过闸门。

实际控制仍在 `start_target` 才登记派发、取得短实例锁、写入 launch-ticket 并释放实例锁；之后 Mac 才 bootstrap 或无强制参数 kickstart，Linux 系统通过 root 提交一次无阻塞 start。自动提前激活可能只让包装进程退出；这不能计为业务启动成功，也不授权重复激活动作。启动领取无覆盖登记并绑定预定 boot，重复领取、其他控制计划、旧预约或损坏内容均拒绝。就绪要求签名的同一次 boot、精确加载定义，以及原生加载 PID 与实际进程 PID 一致。

有 intent 后缺失预约不能静默重建；intent 之前的部分 claim 仍可用原计划补全。完成或显式批准 abandon 后，必须先证明子操作已结清，才删除预约；删除中断可以依终态记录重做。abandon 保留注册与数据，解除闸门后可能允许目标以后登录或系统激活，此影响已列入审阅计划。隔离保留旧预约和未知状态，启动仍由 release 闸门封锁。

新旧安装还必须共同采用相同的激活合同和启动消费者，详见[版本准备要求](V2_RELEASES.md)。普通源码、旧固定安装和旧捕获不能直接混用，须重新构建并 capture 两份安装及计划。Mac mini 的独立证据步骤见[Mac 交接](V2_MACOS_HANDOFF.md)。系统加载用户 LaunchAgents 的行为依据 [Apple launchd 文档](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)；离线模拟不能代替实际登录验收。

## 中断、未知与恢复边界

- 调用方取消只结束等待；实际交换任务继续被拥有。CLI 的正常清理预算耗尽后继续等待已有资源回收，不关闭事件循环丢弃实际操作。
- 恢复已派发步骤只观察后置条件，不重新移除、安装或启动。即使子意图尚未写入，也只能通过只读证明推进，不能据此补发原动作。
- 注册后置条件必须重新观察；成功启动的历史回执在恢复时也须重新证明预定 boot 就绪。进程已退出或 boot 改变时保持阻塞，不再次 Run/start。
- 源/目标代码、bundle、数据库或快照变化时停止推进，保留原文件和已发生效果。完成记录不会冒充当前存活证明。
- unknown 子操作的活动标记仍阻止 abandon。可核清“后置条件已成立”；无法证明结果时保留阻塞，不能凭用户点击取消当作没有发生。现可另行审阅离线隔离和新目录救援：保留原回执、封住旧 home，以最新验证数据和独立原生名称恢复；旧注册清理继续单独待核查。详见[恢复合同](V2_EXCHANGE_RECOVERY.md)。
- 未选择目录时可以在子操作结清后释放预约，继续审阅现有 prepared release；已选择后保留源退役闸门及目标数据，需要依据原快照和匹配安装制定独立恢复计划。没有自动反向迁移或盲目卸载/重装。

当前验证使用隔离 SQLite 和注入管理器；不代表 Task Scheduler、systemd、launchd、不可变安装的实机验收。Windows/macOS/Linux 原生、完整故障注入、负载及 72 小时仍待推进。

跨schema数据准备及匹配快照恢复已独立接入[release计划3](V2_SCHEMA_UPGRADE.md)。用户交换可使用已验证跨schema prepared数据；系统交换须采用[分阶段管理员交接](V2_ADMIN_HANDOVER.md)，不能删除单父会话的同schema/双端源码闸门。
