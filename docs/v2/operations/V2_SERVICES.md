# V2 常驻服务与部署合同

> 文档范围：本页维护该能力合同与所属批次记录。当前默认入口为 V2；[当前交接](../../HANDOFF.md)区分已实现、专项通过和原生验收，历史测试数字不覆盖当前源码。

## 部署流程

开发入口是 `python -m omni.composition.cli service`：

| 操作 | 输入及输出 | 系统影响 |
| --- | --- | --- |
| `capture OUTPUT` | 捕获当前解释器、程序、前端资源、依赖版本和执行账号；写出私密 JSON | 不打开业务库 |
| `admin-bundle DESTINATION` | 从当前程序发布新的标准库管理员源码包、清单和操作说明 | 不安装、不提权、不启动 broker；源码检出也可生成审阅件 |
| `plan REQUEST RUNTIME OUTPUT` | 严格校验请求，绑定运行指纹，产生带 UUID 和摘要的计划 | 不注册服务 |
| `materialize PLAN DESTINATION` | 发布全新部署目录，包含计划、启动文件、原生模板、说明和校验清单 | 不复制到系统服务目录 |
| `verify BUNDLE` | 检查文件、内容、位置、程序及执行资格 | 只读，未具执行资格时退出码 1 |
| `status BUNDLE` | 分别返回部署校验、管理器状态、运行身份和最终 ready | 只读，未证明就绪时退出码 1 |

### `status` 就绪判定（D11e，2026-11-23）

`status` 的 `ready` 是**管理器状态 + 运行身份**的证明，不是「文件存在」或「HTTP 能连」的同义词：

| 平台 | running 证据 | ready 条件 |
| --- | --- | --- |
| `windows-user` | Task Scheduler COM：`IRegisteredTask.State == 4` 且存在运行实例（**枚举** `GetInstances` 取首个非零 `EnginePID`；`Item(0)` 在 PowerShell 5.1 上会抛异常）；再由该引擎 PID 向下找到**动作进程自身**——映像路径属于计划已固定的集合（`Exec/Command`、`python`、`python_realpath`、`process_python`，允许 venv 启动器 re-exec 到真实解释器），且参数按序匹配计划 `Arguments`，报告其 PID | `installed` ∧ `definition_matches` ∧ `state == running` ∧ 已核验且非空的动作 PID；引擎在但动作进程找不到 → `state=running`、`pid=null`、`error_code=service_action_unverified`，**不判 ready**（`pid=null` 永不通过） |
| `linux-user` | systemd `ActiveState=active`/`SubState=running` + `MainPID`/exec 身份 | 同上，`MainPID` 必须属于目标 uid 且可执行文件匹配 |
| `macos-*` | 原生 helper 的 loaded 定义 + 运行 PID（`SMJobCopyDictionary` 完整枚举） | 同上，加载定义匹配且 PID 可证明 |

- `schtasks /XML` 只证明**定义**（动作、主体、触发器、设置）；单独的定义匹配、任务已注册或「引擎进程存在」都不构成 running 证明。
- HTTP `/ready` 与服务启动回执是**另一份证据**：可用于确认就绪窗口，但不能替代 `status` 的管理器状态与 PID 证明；两者一致时才在验收矩阵登记 ready。
- 无法取得管理器状态（辅助程序不可用、被拒或输出不可信）时 `state=unknown`、`pid=null`、`error_code=service_manager_state_unavailable`，不猜测。
| `operation-plan BUNDLE install\|uninstall OUTPUT` | 绑定完整部署、目标、步骤和 SHA256，写入新操作计划 | 不修改系统注册 |
| `apply-operation OPERATION --approve SHA256` | 重新核验指纹，独占离线实例，先持久登记意图再执行 | 真实注册或移除；macOS 必须审阅计划中的激活/卸载影响，不删除实例数据 |
| `inspect-operation OPERATION` | 读取该操作的私密回执 | 不调用系统管理器，不打开业务库 |
| `reconcile-operation OPERATION` | 独占实例后重新查询注册结果，追加核查结论 | 不重复派发原命令；未证明结果时退出码 1 |
| `cancel-operation OPERATION` | 只取消已登记且整个完整历史中没有 dispatch 的意图 | 不调用管理器；已派发或结果未知不能以取消释放阻塞 |

`apply-operation` 是会真实改变操作系统的入口，必须先审阅计划并显式给出同一 SHA256。本轮仅通过注入管理器和临时目录验证这些入口；尚未给本机或其他平台安装常驻服务。

## 离线注册、日志与未知结果

当前操作计划为 `omni-service-operation-2`，包含完整不可变部署、UUID、UTC 时间、目标管理器、步骤和影响标志。Windows/Linux 注册的 `starts_service`、`stops_service` 均为 false；macOS 安装保守声明 `starts_service=true`，因为系统可能独立处理登录项，卸载声明 `stops_service=true`，因为 bootout 可能结束核验后才启动的空闲进程。所有目标的 `deletes_instance_data` 均为 false。标志必须为布尔值，数字 0/1 不能冒充同一审阅内容。CLI 返回这些标志及指纹，必须审阅后批准；旧格式计划需重新生成，不提供兼容转发。Linux 系统目标要求显式匹配的管理员包和端点；未配置时不自动提权。macOS 系统目标同样要求显式匹配的 root 包及端点，不在普通所有者进程执行变更。

应用服务只编排部署验证、注册端口与维护记录，不查找 Runtime 或数据库连接。原生端口复用已有受管进程的每命令 5 秒期限、输出上限和真实回收所有权；文件读取与辅助应用解析共用有界执行器。不运行调用者提供的命令，也不自动提权。工作台未新增安装按钮；当前由所有者显式执行离线 CLI。

执行要求 V2 home 已初始化、数据库版本与实例身份完全一致，并取得主进程共用的实例锁。运行中的实例会阻止注册操作；不能通过 PID 文件判定离线。维护不会迁移数据库。实例初始化明确保护 data、logs、objects、run 目录；锁文件拒绝符号链接、目录联接、硬链接及身份变化。

维护记录位于 `HOME/run/service-operations/`，属于服务变更的独立离线审计，不是另一套聊天或任务存储。`active.json` 在可变系统动作之前落盘并绑定 UUID/指纹；操作目录保存精确计划与连续、哈希相连的元数据事件。每个操作最多 64 条事件、每条最多 16 KiB；文件、目录和发布过程执行私密权限、单链接、完整写入与落盘检查。注册回执不保存原生 stderr、异常正文或凭据值，不将“已登记”当作“就绪”。

同一已结束操作重复执行只返回原回执。已持久登记但未结束的操作，包括在原生派发前后崩溃的情况，一律进入核查流程；同实例的新操作被阻止。取消调用者不会释放实际任务、实例锁或回收义务；辅助进程未确认回收时仍保留锁。进程死亡后依靠持久意图阻止重发。

若进程在登记后、派发前退出，`cancel-operation` 可以在独占锁和完整历史核验下释放意图，记录 `no_persisted_dispatch`。即使只读核查已追加 unknown，只要历史中确实从未派发，仍可取消；任何 dispatch、历史缺失、指纹变化或不完整临时文件都会拒绝该取消路径。

`reconcile-operation` 只核对当前注册后置条件。核查成功记录 `verification=observed_registration_postcondition`，不声称能证明历史命令确实执行；原始未知事件完整保留。正常命令成功且注册复核通过时记录 `command_and_registration_postcondition`。部分安装、文件漂移、查询故障及不明确的原生返回仍为 unknown，不自动重复、删除残留或恢复旧定义。运行中的服务需先按既有授权和正常关闭流程处理，离线核查不会替用户停止它。

Windows 使用独立打包的固定 PowerShell MTA/Task Scheduler COM 辅助程序。当前用户 SID、精确任务名称、模板摘要、动作、主体、登录触发和设置重新核验。仅 `ERROR_FILE_NOT_FOUND` 证明不存在；注册使用 `TASK_CREATE | TASK_IGNORE_REGISTRATION_TRIGGERS`，禁止更新现有任务。卸载要求匹配的定义、无实际任务实例且非排队/运行状态。未知或改变的设置拒绝移除，不根据本地化文本识别成功。[Microsoft RegisterTask](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskfolder-registertask)、[DeleteTask](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskfolder-deletetask)

Linux 用户注册使用固定本地 systemctl。安装目录和文件必须属于目标 uid，禁止其他主体写入；unit 经同目录临时写入和无覆盖发布后才 reload、enable，不传 `--now` 或 `--force`。卸载先 disable，不执行 stop，重新核验完整文件后才移除并 reload。完整、身份匹配的 `LoadState=not-found` 及文件缺失才证明不存在；非零退出码本身不证明缺失。enable/disable 可能部分生效，命令失败仍按未知结果保存。[systemctl 合同](https://www.freedesktop.org/software/systemd/man/255/systemctl.html)

## Linux 系统服务管理员边界

管理员包只包含固定的纯合同、模板、systemd 结果解析、root 文件策略、进程所有权及 Unix IPC 模块，不包含组装根、数据库、插件、凭据、模型或第三方依赖。普通用户进程仍负责完整部署校验、实例独占锁及业务维护意图。root 不运行用户的 Python 环境；操作系统服务使用绑定的非零数字 UID，不以 root 启动应用。

以下是待 Linux 管理员审阅的流程，当前未实际执行：

1. 在拟部署的不可变安装中生成 Linux 系统部署包和操作计划，核对账号、home、解释器、工作目录、unit 名称及操作 SHA。普通用户源码检出的预览件不能因此取得执行资格。
2. 用同一安装执行 `python -m omni.composition.cli service admin-bundle NEW_DIRECTORY`。当前输出包含包 SHA、29 项文件记录及 `installed=false`、`elevated=false`。管理员另行审阅整个包及清单，安装为 root 所有、非用户可写且无链接的独立目录；不把用户可写文件、venv 或导入钩子放入 root 搜索路径。
3. 管理员在清空环境后，用受信、root 所有的 Python 3.12+ 启动下方固定入口。`-I -S`、固定解释器和 root 包是显式运维前提；程序无法替不受信的解释器建立信任。代码自身还会核对包内文件、额外文件和源文件指纹，管理员包必须对应操作计划捕获的实际程序。
4. broker 发布等待所有者的端点和指纹后，普通用户在第二终端执行同一个操作计划，显式指定端点与管理员包 SHA。`apply-operation` 仍须批准原操作 SHA；`reconcile-operation` 使用相同管理员参数但只查询，不重新派发。

```text
# root 管理员命令形状；占位符必须按已审阅部署替换，本文件未执行此命令
/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 ROOT_OWNED_PYTHON_3_12 -I -S ROOT_BUNDLE/service-admin.py --operation PRIVATE_OWNER_OPERATION_JSON --approve OPERATION_SHA256 --owner-uid OWNER_UID

# 普通所有者命令形状；所有者与 root 各自批准、核验不同的边界
OWNER_PYTHON -I -m omni.composition.cli service apply-operation PRIVATE_OWNER_OPERATION_JSON --approve OPERATION_SHA256 --admin-socket /run/omnioperator-service-admin/OPERATION_UUID.sock --admin-bundle-sha256 ADMIN_BUNDLE_SHA256
```

只接受固定 `/run/omnioperator-service-admin/{approval_id}.sock`，单操作使用操作 UUID，完整交换使用交换 UUID。父目录由 root 控制，socket 属于绑定 UID、mode 0600；连接双方通过内核 `SO_PEERCRED` 核验对端 UID，客户端复核路径 inode。当前 `omni-service-admin-4` 请求绑定批准范围 UUID/SHA、精确子操作键与 UUID/SHA（覆盖完整部署）、管理员包 SHA、严格序号和请求 UUID；每连接最多 192 帧，最后一帧保留给 close，每帧 64 KiB。重复键、未知字段、额外命令、流水派发、身份变化及未知报告字段均拒绝，不接受任意 argv。管理员包同步升级 `omni-service-admin-bundle-4`，须从相同源码重新捕获并生成。[Linux Unix socket 身份合同](https://man7.org/linux/man-pages/man7/unix.7.html)

root 在 `/var/lib/omnioperator-service-admin/{operation_id}/` 先无覆盖持久发布 `dispatch.json`，最后单独发布结果。记录仅含身份、指纹、动作及注册元数据，不保存业务正文。root 每实例锁跨连接持有；已登记派发的操作再次连接也不重放。该目录是提权操作的独立见证，业务维护真值仍在所有者原有记录；不能删 root 派发记录来“修复” unknown。断线、部分注册或结果不明按既有只读核查处理。

注册操作仅调用固定 `/usr/bin/systemctl` 的查询、reload、enable、disable；独立的系统启动 ControlPlan 另只允许固定无阻塞 start，见[系统启动合同](V2_SYSTEM_SERVICE_START.md)。broker 不调用 shell、stop、restart、`--now` 或 `--force`。发布前拒绝目标、类型和名称前缀层级的 drop-in；解析与普通查询复用同一严格 systemd 合同。文件无覆盖发布后先 reload，核验实际加载的精确定义且保持停止状态，才 enable。卸载先 disable，再核验静止、精确文件和禁用状态，才移除及 reload。没有权限或输出不明时不降级为“未安装”。

原生命令在独立、单线程 broker 中运行，固定每命令 5 秒、有界输出。pidfd、子进程组、subreaper 和父死亡信号共同拥有实际进程树；组长未回收前保留 PGID，退出后重新检查被收养的子进程，句柄初始化失败也不能遗失归属。回收未完成时保留 root 锁与通道，不接受新工作；普通客户端超时/取消保留实际 RPC，只有明确关闭回执才能报告清理完成。坏帧和断线不能冒充回收完成，未知动作不能自动重发。[Python 3.12 pidfd 信号](https://docs.python.org/3.12/library/signal.html#signal.pidfd_send_signal)、[Linux 父死亡信号](https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html)

首批仅支持 `/etc/passwd` 中的本地普通账号，不在 root 事务内运行 NSS/LDAP 查询。要求受保护的本地 systemd 路径、可验证的 UnitPath、无 drop-in 和可用的 pidfd/prctl；这些前提未在 Linux 实机验证，不据包存在或注入通过标记为平台可用。管理员安装/移除包、处理崩溃留下的 socket 和审计保留需另行核对，不提供自动删除、提权或残留清理脚本。macOS 系统单操作的独立管理员源码已接入，见[Mac管理员合同](../platforms/macos/V2_MACOS_SYSTEM_SERVICES.md)；同 schema 系统交换已接源码，实机仍待推进。

## macOS 用户服务源码与限制

macOS 用户 plist 增加 `LimitLoadToSessionType=Aqua`。安装只无覆盖发布 plist 并执行固定 `launchctl enable`，不在持有实例锁时 bootstrap。磁盘定义、启用状态、加载状态和运行就绪分别报告：未加载时为 `activation=next_login`、ready=false；手动激活需等维护结束并按部署包中的独立命令另行审阅。操作计划没有把“写了 plist”当作当前进程已运行。

已加载定义由固定 Swift 应用的独立 `--service-query` 入口读取，协议为 `omni-macos-service-query-2`；桌面 IPC6 不变。它不请求 AX、屏幕、输入权限，不提交或移除任务，不打开数据库。入口核验 Bundle、当前 UID、控制台、图形 Security Session、非 root/非远程属性及 audit/Security 标识对，并在前后复核；两个标识属于不同合同，不假定数值相等。[Apple SessionGetInfo](https://developer.apple.com/documentation/security/sessiongetinfo(_:_:_:))

只读 helper 使用公开但已弃用的 `SMJobCopyDictionary`，对同一目标重复读取以发现变化；单目标读取为 nil 时，还须完整枚举成功且没有该标签才能证明缺失。枚举失败不能解释为未安装。没有采用 `launchctl print` 的文本片段替代完整加载定义。将来 API 移除或返回内容无法证明预期定义时，对应状态保持 unknown；当前固定 Bundle 并非采用 SMAppService 管理的每实例应用，不能直接以其 status 冒充这些任意 plist 的状态。[Apple Job Dictionary](https://developer.apple.com/documentation/servicemanagement/smjobcopydictionary(_:_:))、[Apple 全量只读枚举](https://developer.apple.com/documentation/servicemanagement/smcopyalljobdictionaries(_:))

目标描述最多 1024 个值、8 层、单字符串 4096 UTF-8 字节、总文本 32 KiB、帧 64 KiB；其他任务描述不返回宿主。宿主精确比较执行参数、目录、日志位置、环境、session 类型及全部声明字段，未知字段或缺失字段不证明匹配，布尔值不等同数字。完整 job 只用于内存比较，不进入维护回执、日志、工作台或验收报告。固定 `print-disabled` 输出另按完整、有界的已知语法读取启用覆盖；截断、重复、未知语法均返回 unknown。

注册路径在原生端检查属主、mode、链接、稳定身份及扩展 ACL；当前仅接受能够明确读取且没有正向授权的 ACL，允许标准 deny-delete 项。**现存路径不从 ACL API 的 NULL/ENOENT 推断缺失；当前源码改用成功的 no-follow statx/filesec 属性查询证明默认无 ACL。APFS 行为和新增8项签名前原生测试仍待 Mac 执行，正向只读 ACL 仍拒绝。** 不自动修改既有目录 ACL 或以 POSIX mode 替代扩展 ACL 证明。Darwin 的 ACL 迭代成功值是 0，结束为 -1/EINVAL；实现按其平台合同处理，不套用 Linux 返回值。[Apple ACL 迭代](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/acl_get_entry.3.html)

卸载先要求匹配文件、加载定义和静止实例，再 disable；复核后才可 bootout 同一标签。确认目标已经卸载后才删除精确匹配的 plist。定义漂移、进程出现、启用/卸载失败、迟到或未知返回都保留意图和文件，不自动重发。实例锁保证活动 V2 及未清理组件不会进入卸载，不按历史 PID 发信号。

Mac 编译、API 的实际返回字段、GUI bootstrap 域对应、默认 ACL、登录项审批及真实安装/卸载均未验证。新 helper 源码要求重新构建并签名，旧来源清单不能复用。只读验收工具及步骤见[Mac 交接](../platforms/macos/V2_MACOS_HANDOFF.md)；注入协议测试不能代替这些门槛。

不可变版本准备、相同 schema 的新目录选择及恢复计划已经接入独立 release CLI：固定新旧安装，持有两份实例锁，调用一致快照和对象验证，恢复并隔离到新目录；旧服务须先经单独审阅的操作移除，选择再退役旧 home、开放新 home。实际注册和本次启动身份仍须独立验证。详情与限制见[版本切换合同](V2_RELEASES.md)。

显式启停计划、启动预留、HMAC 正常排空与就绪/关闭证明已接入，详见[服务控制合同](V2_SERVICE_CONTROL.md)。用户级启动源码覆盖 Windows/Linux/macOS，macOS 原生前提仍未验证；Linux 系统显式启动后端已接线、待实机验收，macOS 系统显式启动及整体交换已接源码，仍待实机验收。运行捕获格式已升级为 `omni-service-runtime-2`，启动器和实际进程映像分别固定，须重新生成描述与计划。

Windows/Linux/macOS 用户目标现已接入[交换协调器](V2_SERVICE_EXCHANGE.md)，将 prepared release、旧注册移除、目录选择、目标注册和预定 boot 就绪串联。当前交换计划为 `omni-service-exchange-4`，Mac 持久预约限制提前登录激活并绑定本次预留。[隔离救援与在线准备](V2_EXCHANGE_RECOVERY.md)进一步保留未知结果、最新数据和费用，以新目录/独立名称恢复，并接入一次正常排空到备份。服务计划为 `omni-service-plan-2`，新旧安装须共享当前启动消费者，重新构建、捕获并生成计划/管理员包。Linux 系统交换编排及隔离旧定义清理已接线，见[系统交换合同](V2_SYSTEM_SERVICE_EXCHANGE.md)；完整 root 批准绑定三项子操作，子 settle 与父 close 分离，管理员源码须同时匹配新旧安装。macOS 系统实测、跨管理员协议、跨 schema 和实机仍待推进；不能据此标记完整 D4 或三平台已支持。

全部路径必须显式给出，目标父目录应提前建立。输出不覆盖已有文件或目录。部署包不能写入实例、程序或系统自动启动目录。发布中断保留带 `.deployment.partial` 后缀的临时目录；它没有注册行为，也不能作为有效部署启动。对照清单核查后可以单独归档，不自动清除同目录其他文件。

在独立的 V2 home 执行 `--home HOME init`，从输出取得 `instance_id`。先离线准备不可变版本的安装目录、独立工作目录和部署包目录，再捕获**实际运行环境**。源码及可编辑安装允许生成预览，执行资格固定为 false；不得通过修改 JSON 的 editable 字段绕过校验。普通 POSIX venv 的解释器符号链接会显式记录整个链接链与最终二进制；目录别名、程序链接和模板链接不被接受。

请求格式 `omni-service-request-1`，字段必须完整，无额外字段。示例仅展示 Windows 用户登录任务；Linux/macOS 使用对应绝对 POSIX 路径和实际 uid。

```json
{
  "format": "omni-service-request-1",
  "target": "windows-user",
  "instance_id": "11111111-1111-4111-8111-111111111111",
  "home": "D:\\OmniInstances\\personal",
  "working_directory": "D:\\OmniWorkspace",
  "bundle_dir": "D:\\OmniDeployments\\personal-v2",
  "principal": {
    "name": "MACHINE\\owner",
    "id": "S-1-5-21-111-222-333-1001",
    "home": "C:\\Users\\owner"
  },
  "host": "127.0.0.1",
  "port": 8766,
  "external_url": null,
  "master_key_file": null,
  "stdout_logs": true
}
```

`principal` 必须来自捕获结果。system 目标也必须绑定普通用户，不接受 root。普通配置只有密钥文件路径，不包含主密钥或所有者令牌。监听外部地址需要显式可信 HTTPS 来源及另外配置的 HTTPS 入口；程序本身的 HTTP 不会因填写 external_url 自动获得 TLS。

## 原生模板与固定启动

| 目标 | 生成物 | 首批行为与限制 |
| --- | --- | --- |
| `windows-user` | `service.xml` | 当前 SID、交互登录、最低权限、忽略重复实例、不限制运行时长；无自动重启 |
| `macos-user` | `agent.plist` | LaunchAgent、固定 argv、RunAtLoad、KeepAlive=false |
| `macos-system` | `daemon.plist` | LaunchDaemon、显式普通 UserName；不开放登录用户桌面能力 |
| `linux-user` | `omni.service` | systemd 用户服务、Type=exec、KillMode=mixed、Restart=no |
| `linux-system` | `omni.service` | systemd 系统服务、显式非零数字 User；注册须独立 root broker，不获得交互桌面权限 |

所有模板以数组或平台规定的转义方式表达参数，不把用户字段拼成 Shell 脚本。模板使用绝对解释器，以 `-I -u -m omni.composition.service_runner --deployment BUNDLE` 启动，隔离 Python 环境注入。程序指纹、解释器、venv 配置、依赖版本、执行账号或资源变化时，需要重新生成并审核部署计划，不能继续静默启动。

runner 先核验完整部署包及资格，再检查实际工作目录。取得实例锁后，在迁移或业务恢复前检查实例 ID 及当前数据库版本；不为未初始化或旧结构的 home 自动执行服务升级。更新和恢复继续采用独立维护流程，不能覆盖正在运行的源码。服务启动不修改默认入口选择。

部署包 README 列出供审核的安装/移除命令及目标路径；这些命令没有自动执行。launchd bootstrap 可能立即启动程序，systemd enable --now 与任务运行也属于实际启动操作，必须按具体方案另外确认。退出预算为主程序 30 秒，系统模板额外留出回收时间；没有清理完的进程或工作不会因服务模板存在而被标记成功。

## 状态与一次启动身份

`/api/v1/version` 增加 instance_id、boot_id、pid、process_created、构建/API/数据库版本和 service_plan_sha256。`run/runtime.json` 是同一次启动的私密元数据，不能用其中的历史 PID 执行 kill。

运行状态先发布 running；开始排空时发布 draining，HTTP ready 立即变为 false；实际业务、原生组件和存储关闭后才能发布 stopped，随后释放实例锁。发布超时或失败保留真实任务及执行器，不能提前释放锁。新启动更换 boot_id，历史 stopped 文件不代表当前服务存在。

每次启动产生独立 32 字节 `run/readiness.key`，不进入数据库、日志或诊断包。仅本机回环客户端能调用 `/health/identity?challenge=<64位小写十六进制>`，响应只包含固定身份字段、ready、挑战和 HMAC-SHA256。客户端核验私密文件权限、进程创建时间、解释器、argv 与监听归属，再读取本次 key 验签。探测不读取也不发送 owner.token；接管端口的其他程序只会收到随机挑战。普通存活与就绪接口仍不能单独证明服务身份。

管理器查询与运行证明互相独立。Windows XML 能证明注册定义，但不从本地化状态文字推断 PID；macOS 查询若不能证明 launchd 实际加载的定义，definition_matches 为 null；systemd 存在未核验的 drop-in、来源或重载差异时也不报匹配。最终 ready 需要部署资格、已注册且匹配的定义与本次运行证明，未知字段不能转换为成功。

查询限制固定 argv、输出体积、执行时间及并发；被取消的客户端不能丢弃实际子进程、文件读取或 HTTP 探测。查询失败只返回错误码，不输出令牌、密钥或任意 stderr 正文。使用代理、跨机 DNS 或无法核验本机监听时，明确返回不可核验。

## 尚待验收

三平台真实安装/移除、登录/重启恢复、权限撤销、挂起唤醒、异常残留、升级切换及每平台 72 小时验收均未完成。macOS/Linux 本机执行不能由 Windows 上的渲染、类型检查或假进程测试替代。真实原生桌面、业务迁移和完整工作台切换仍属于后续批次。

模板依据：[Task Scheduler 登录类型](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-logontype-principaltype-element)、[Apple launchd](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)、[systemd service](https://www.freedesktop.org/software/systemd/man/systemd.service.html)。本文件的生成合同由专项测试守卫；外部文档不能替代目标机器的真实验收。

macOS 系统状态已另接固定辅助应用的 `--system-service-query`，普通所有者可在无GUI会话尝试读取 system 域；权限不足保持不可用，不提权。响应协议2隔离用户/系统域，删除系统目标的 print 文本解析。该只读链与独立管理员后端分开；Mac单操作管理员源码已接入、同 schema 系统交换已接源码、实测待推进，见[Mac管理员合同](../platforms/macos/V2_MACOS_SYSTEM_SERVICES.md)。

## 历史批次状态（非当前结论）

以下原记录中的默认入口与完成度仅属于当时源码；当前默认入口为 V2，实际进度以当前交接和任务为准。

> R5.services 已有可审阅的部署包、固定启动入口、只读查询与运行身份核验。D4 已接入 Windows/Linux 用户服务注册、macOS 用户服务宿主及只读查询源码；新增 Linux 系统服务的独立管理员包、普通用户客户端和 root broker 源码。后者仅在 Windows 注入验证，尚未在 Linux 安装或验收。Swift/C 尚未编译，默认 ACL 缺失证明及原生返回字段仍待 Mac 核验；macOS 系统单操作和同 schema 整体交换已接独立标准库管理员源码，真实验收仍待完成。整个 V2 尚未完成，默认入口仍为 V1；本轮没有执行提权、真实系统注册、启动、停止或重启。
