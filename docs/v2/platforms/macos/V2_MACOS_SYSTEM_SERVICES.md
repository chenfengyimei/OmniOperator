# macOS 系统服务：管理员与整体交换

当前已接入系统域只读、独立管理员包的注册/卸载和显式单次启动源码。同 schema macOS 系统交换已接源码；跨管理员版本协调、跨 schema 和实机仍待完成。默认入口仍为 V1；本机没有提权、编译 Swift/C 或执行 Mac 服务操作，注入通过不代表 macOS 已支持。

## 已接入的只读链路

`ServiceQuery` 对 `macos-user` 和 `macos-system` 均调用固定 Swift 辅助应用，不再解析 `launchctl print`。独立查询协议升级为 `omni-macos-service-query-2`：请求选择固定入口，响应绑定 domain、label、普通所有者 UID、随机 nonce、版本及精确字段。

- 用户入口为 `--service-query LABEL UID NONCE`，继续核验当前 GUI/Security/audit 会话，读取 `kSMDomainUserLaunchd`。
- 系统入口为 `--system-service-query LABEL UID NONCE`，读取 `kSMDomainSystemLaunchd`，使用当前普通所有者的进程 UID/创建身份核验，不要求 Aqua，不调用桌面或权限申请流程。它拒绝 root、提权及其他 UID，不能将用户可写辅助应用用于管理员执行。
- 所有入口由既有受管进程、5 秒总期限和关闭义务拥有。不会从 PATH 寻找替代程序；辅助应用必须匹配当前 Swift/C/H 来源和最终可执行文件指纹。
- 原生层重复读取指定定义，严格限制树、字段和文本；完整枚举成功、未找到目标且再次读取仍为空时，才能报告加载缺失。查询失败或权限不足不等于未安装。原生字典只在比较内存中使用，不进入审计或诊断日志。
- 用户域和系统域响应不能互换，旧协议、额外字段和其他身份均拒绝。两个原生快照使用带类型的 JSON 表达比较，避免把 `true` 与 `1` 当作相同值。

这些 ServiceManagement 读取 API 已弃用，移除、拒绝或描述字段不足时保持不可用；没有文本或私有 API 回退。[Apple SMJobCopyDictionary](https://developer.apple.com/documentation/servicemanagement/smjobcopydictionary(_:_:))、[Apple 全量只读枚举](https://developer.apple.com/documentation/servicemanagement/smcopyalljobdictionaries(_:))。

系统 plist 另按当前计划比较 `UserName`、执行参数、目录、日志、环境及所有声明字段。磁盘文件和已加载定义分别核验；缺失文件不证明已加载内容相同。普通查询不证明启用覆盖，也不授权 bootstrap、bootout、kickstart 或删除。仍未加载的系统定义标记 `explicit_start_or_next_boot`，不会显示成“下次登录”。

## 共享 ACL 证明

原有 `acl_get_file` 的 NULL/ENOENT 同时可能来自读取失败，不能据此推断没有 ACL。当前实现先持有 no-follow 文件描述符，确认本地卷明确支持扩展安全元数据，再以 `fstatx_np` 取得同一对象的 `filesec`，明确查询 `FILESEC_ACL` 是否存在；存在时从同一对象提取并核验 ACL。卷能力、读取、属性查询及最终身份检查任一步失败都拒绝；网络或能力未知卷不作为无 ACL 的证明。[Apple ACL 实现](https://github.com/apple-oss-distributions/Libc/blob/main/posix1e/acl_file.c)

文件前后核对设备、inode、属主/组、mode、链接数、大小及 ctime。用户路径要求当前 UID；系统 plist 路径要求 root，Swift 在 `/`、`/Library`、`/Library/LaunchDaemons` 和目标文件逐级核验。链接、多硬链接文件、其他主体可写、正向授权或未知 ACL 仍拒绝；明确无 ACL 和标准 deny-delete 可接受。这是当前保守策略，正向只读 ACL 尚未放开。

构建脚本签名前增加 `NativeIdentityTests.RegistrationPathTests`：8 项在临时目录执行真实 Mac 文件 API，覆盖无 ACL、deny-delete、正向授权、符号链接、硬链接、组可写、系统属主和缺失新叶子。不修改用户目录 ACL 或系统目录；失败、跳过或缺项均阻止签名。Windows 上只验证了构建门禁接线；8 项 XCTest 本身待 Mac mini 执行。

## Mac mini 交接

使用非 root 测试账号同步完整当前来源及锁文件，按[Mac 交接](V2_MACOS_HANDOFF.md)重新构建。IPC6 桌面协议未改，但服务查询协议和原生来源已改变，不能复用旧安装。构建证据同时保留 `foreground-core.xml`、`registration-paths.xml` 和签名后 `.build.json`；新增测试不申请 TCC 或操作服务。

在已验证的 macOS 系统部署包上，可用同一只读工具收集证据：

```sh
uv run --no-sync python agent/scripts/accept_macos_services.py --bundle "$SYSTEM_BUNDLE" --expect inspection --output "$NEW_PRIVATE_OUTPUT"
```

系统目标接受 `inspection`、`absent`、`loaded`、`ready`，拒绝 `registered`；普通查询无法替管理员证明派发回执。可附加 `--exchange-plan OWNER_EXCHANGE_JSON`，ready 还要求日志前后一致且 finished、预留 boot/HMAC/PID 一致。系统只读查询不证明启用覆盖，`enabled_override_verified=false`，不替代动作后 root 启用核验或管理员审计。证据明确 `registration_source=system_read_only_query`、`administrator_operation_exercised=false`、`whole_platform_acceptance=false`。`ready` 还需实际进程与本次 HMAC 运行证明。工具只读取已有安装，不会为了满足预期安装或启动服务。

## 管理员后端合同

`service admin-bundle NEW_DIRECTORY` 生成标准库独立包，当前含 29 项文件记录，协议/包/审计现为4。固定 `service-admin.py` 和 `service-admin-probe.py` 与纯领域、共享状态及平台策略一起记录指纹。root 不导入应用、数据库、插件、用户 Python 环境或图形辅助应用；框架读取只在独立受管 probe 中执行。

管理员须独立审阅包、完整操作 SHA 与普通所有者 UID，将整个包和 Python 3.12+ 放在自己验证过的 root 所有、不可被其他主体写入的位置。启动使用清空环境、`-I -S`。程序不能替不受信的解释器或已经被修改的启动文件建立信任，不自动提权、复制包或修复权限。新不可变安装、runtime 捕获、部署和操作须由当前来源重新生成。

```text
# 以下是待 Mac 管理员审阅的命令形状，本批未执行
/usr/bin/env -i PATH=/usr/bin:/bin LANG=C ROOT_OWNED_PYTHON_3_12 -I -S ROOT_BUNDLE/service-admin.py --operation OWNER_OPERATION_JSON --approve OPERATION_SHA256 --owner-uid OWNER_UID

OWNER_PYTHON -I -m omni.composition.cli service apply-operation OWNER_OPERATION_JSON --approve OPERATION_SHA256 --admin-socket /private/var/run/omnioperator-service-admin/OPERATION_UUID.sock --admin-bundle-sha256 BUNDLE_SHA256

# 独立批准的 ControlPlan start；就绪仍须所有者的精确 boot/HMAC/PID 证明
OWNER_PYTHON -I -m omni.composition.cli service apply-control OWNER_START_JSON --approve CONTROL_SHA256 --admin-socket /private/var/run/omnioperator-service-admin/CONTROL_UUID.sock --admin-bundle-sha256 BUNDLE_SHA256
```

核查使用对应 `reconcile-operation/reconcile-control` 和同一端点/包指纹；管理员进程须绑定该原计划。已派发不重发；用户/系统或 Linux/macOS 的计划、端点和根策略不能混用。普通端口继续拒绝系统变更，只有显式的 pinned root 客户端走此路线。root 不接受 stop、restart、任意 argv 或强制参数。

root 使用固定本地 `/Local/Default` 的 dscl 单条记录核验 UID、主组和 home，前后账号不一致拒绝；不查询目录搜索节点或 LDAP。socket 使用内核 getpeereid 与非空 LOCAL_PEERPID，要求 AF_UNIX 流、root 父目录、仅绑定 UID 可访问、稳定 inode 及 ACL；TCP 和空原生返回不能提供 root 身份。root 锁与派发审计在 `/private/var/run/omnioperator-service-admin`、`/private/var/db/omnioperator-service-admin`，不读取业务库或凭据。

注册无覆盖发布精确 plist，复核 root 路径/ACL 后 enable，不在实例锁内 bootstrap。卸载要求静止、精确已加载定义，先 disable，再观察、必要时 bootout、确认卸载后才删除同一文件；计划声明潜在 stop 影响。显式启动只在已启用的精确定义上 bootstrap，或对已加载静止目标使用不带 `-k` 的 kickstart。原生提交成功仅为 `acceptance_only`；所有者复用既有启动预留、实例锁释放和本次 boot/HMAC/PID 校验。

每次外部动作前无覆盖登记 root dispatch，后置结果另存 outcome；两平台共用审计字段和所有者用例。root 输出只含固定元数据，不输出 dscl 原文、加载字典、模型或聊天内容。动作后无法核验或写审计失败保持 unknown。

## Darwin 实际进程归属

root broker 保持单线程、SIGCHLD 默认且只拥有一个实际命令。fork child 只运行固定启动代码，先建立自己的组、关闭继承的 root socket/锁等 fd，发 ready 后等待 permit；父进程登记 kqueue EXIT/FORK 观察成功才允许 exec。命令5秒、总输出1MiB，stdout 保留有界内容、stderr 只排空；取消只释放等待者，实际任务继续有所有者。

连续拥有且未回收的直接子 PID/组作为信号锚；确认退出并排空后才 waitpid，回收后不再按数字 PID/PGID 发信号。初始化失败也保留实际归属。fd 的 close 结果不确定时不按原数字重试，保留 cleanup_pending。

**当前采用不允许固定命令发生 fork 的保守合同。** Darwin 的 NOTE_TRACK 自10.5已不支持，不能用 FreeBSD 语义假装追踪后代；观察到 fork、事件失败或句柄状态不可证明时拒绝成功，保留锚、root 锁和关闭义务，需管理员核查。它不是任意进程树回收器，也不提供操作系统沙箱。若 Mac 上的 dscl/launchctl 实际发生 fork，需要另行实现可证明的原生策略，不能简单去掉检查。[Apple kqueue flags](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/event.h)

机器/进程崩溃不能生成清理证明；派发账本与所有者活动标记保持 unknown，不自动新建操作重发。残留 socket 或日志不自动删除。平台验收必须覆盖 broker 终止、命令阻塞、组变化、fork、关闭失败和迟到效果；源码不代替这些证据。

## 系统整体交换与恢复

复用交换计划4、激活预约1、管理员协议/包/审计4和[系统交换合同](../../operations/V2_SYSTEM_SERVICE_EXCHANGE.md)的三个精确子操作。Mac socket 为 `/private/var/run/omnioperator-service-admin/EXCHANGE_UUID.sock`。root 批准完整交换 SHA，所有者另行批准并固定包 SHA；管理员源码须同时匹配新旧不可变安装。

```text
OWNER_PYTHON -I -m omni.composition.cli exchange apply OWNER_EXCHANGE_JSON --approve EXCHANGE_SHA256 --admin-socket /private/var/run/omnioperator-service-admin/EXCHANGE_UUID.sock --admin-bundle-sha256 BUNDLE_SHA256
OWNER_PYTHON -I -m omni.composition.cli exchange resume OWNER_EXCHANGE_JSON --approve EXCHANGE_SHA256 --admin-socket /private/var/run/omnioperator-service-admin/EXCHANGE_UUID.sock --admin-bundle-sha256 BUNDLE_SHA256
```

root 分别登记三个子操作审计，持有单个实例锁和父连接。先移除已证明静止的源，子 settle 后所有者选择目录，再注册目标；发布精确 launch-ticket、释放实例锁后才 bootstrap。源 home 和未预留目标的自动启动被预约拒绝，重复领取不能生成新 boot。取消、fork 或关闭不确定不释放实际归属。

恢复只读核验批准的源定义；同一 label 已切换时，结清源查询后再核验已批准目标，不接受任意 job，不重复派发。abandon 会解除自动激活限制，计划声明该影响且不自动回滚。隔离清理需另行批准 nested uninstall，socket 使用操作 UUID，不能复用父交换授权。

## 下一范围与验收

同 schema 系统交换及启动激活绑定已接源码，后续继续跨管理员协议/跨 schema。只读验收不声称执行过管理员操作，真实派发证据仍须独立采集。Mac mini 须核验 ctypes/系统框架 ABI、statx/ACL/卷能力、dscl 字段、内核 peer、kqueue 和实际 launchd 行为；Swift 图形组件与8项原生文件门禁也仍待编译执行。

Linux/Mac 系统交换及单操作后端分别记录，不能将 Windows 注入或 Linux 运行当作 Mac 证据。业务迁移、正式入口切换及三平台72小时仍待推进。

不同管理员源码或跨schema时，使用[分阶段交接](../../operations/V2_ADMIN_HANDOVER.md)，每角色只执行自己的固定包；实际Mac ABI、root锁与关闭行为仍须实测。单父会话保留同schema/双端源码限制，未提供协议3兼容。
