# macOS 原生实现与 Mac mini 交接

> 文档范围：本页维护该能力合同与所属批次记录。当前默认入口为 V2；[当前交接](../../../HANDOFF.md)区分已实现、专项通过和原生验收，历史测试数字不覆盖当前源码。

当前交付包为 D2，仍在开发中。Windows 主机验证了 Python 宿主、协议、隔离及打包。Mac mini 已完成首批 arm64 构建、开发签名、固定安装和只读 probe，图形动作仍待隔离样例验收。整个 V2 尚未完成；Windows 离线候选默认入口已经是 V2，显式 V1 回退保留。当前迁移步骤与源码交接见 [Mac mini 开发迁移](../../../V2_MACMINI_MIGRATION.md)。

所有者已确认迁移目标为 Apple Silicon Mac mini；本次使用原生 arm64 Python 与 `--arch arm64`。Windows 自有窗口的前台输入/点击完整回归已在人工聚焦后通过，四次动作的生产回执仍均为 unknown；不据此声明真实应用或 Mac 原生通过。

Windows 本地阶段已收尾，由所有者自行迁移。Mac 新会话可直接使用[完整接续提示词](../../../V2_MACMINI_RESUME_PROMPT.md)，在本机读取路径、系统与工具链，优先实际编译现有 Swift/C 并修复原生缺口。

当前公共桌面协议为`omni-desktop-ipc-6`，Swift协议常量已同步。任何IPC5构建均需使用当前源码重新构建并更新签名后来源清单；Windows打包不能代替Mac编译或原生验收。

D4 服务查询现为 `omni-macos-service-query-2`：用户/系统入口分别读取原生 GUI/system 域，响应显式绑定域；系统查询可在普通所有者的无图形会话运行，不提权。默认 ACL 缺失改用 statx/filesec 正面证明，签名前增加 8 项真实临时文件测试；当前 Windows 尚未编译 Swift/C 或运行这些 XCTest。须重建整个辅助应用，不能标记 Mac 已支持；macOS 管理员单操作源码已接入，同 schema 系统交换已接源码，实际安装/交换验收仍待推进，见[系统服务前置](V2_MACOS_SYSTEM_SERVICES.md)。

## 已加入的实现

- 固定 Bundle ID：`org.omnioperator.desktophelper`；固定位置：`~/Applications/OmniOperator Desktop Helper.app`；固定可执行文件：`Contents/MacOS/OmniDesktopHelper`。
- 宿主复用既有有界进程、总期限、取消、管道限额及回收义务。只接受固定安装，不搜索 PATH，不在运行时编译，不使用 V1 脚本。
- 构建清单在应用旁的 `.app.build.json`，记录签名后的可执行文件指纹与当前包内全部 Swift/C/H 来源。将可执行文件的最终签名指纹放在其自己的签名资源内会形成依赖循环，因此清单单独保存。宿主拒绝来源、协议、指纹、属主或权限不符。
- 只读 probe 不申请系统权限；权限属于 Swift 辅助应用自身，Python 的 TCC 结果不能作为它的授权。
- 身份使用当前控制台用户、audit session、libproc 创建时间、应用标识及当前会话的 CGWindowID；前后重新核验。同 UID、窗口号或窗口几何本身均不构成写授权。
- AX 窗口通过所属进程与几何唯一匹配；多个相同几何窗口拒绝匹配。没有使用私有 AX 窗口函数。128 节点、8 层、单字段 512 字符、总文本 16 KiB、IPC 64 KiB；每次 AX 消息超时 250 ms，宿主总期限 5 秒。
- 文本仅使用有界范围读取，不复制整个 AXValue；密码及其子树不读取。引用是有限路径和祖先描述指纹，动作重新定位并比较，不持久保存 AX 指针。
- ScreenCaptureKit 只采指定窗口；输出经私有独占文件、RGB 解码、尺寸和 SHA256 验证后进入现有对象/会话引用链。没有全桌面回退，不自动作为模型附件。
- 语义 invoke/select/set_text 复用所有者运行、审批、租约和 unknown 核查，需要 AX 和输入监测权限。AX语义可作用于已审批的后台目标，不切换前台；派发后的原焦点必须保持稳定。被动事件监听不记录按键内容，用户介入、目标变化、权限丢失或不能证明后置状态时拒绝或保持 unknown。
- 前台输入源码已接入逐次授权、ready/permit、10秒手动焦点等待及250ms静止期。键盘事件投递至已核验目标PID；鼠标点击经窗口服务器投递到 AX 命中控件且仍在目标窗口内的已核验点（直接向 PID 投递的鼠标事件 windowNumber 为 0，不会路由到窗口），前后保存并恢复光标位置。仍然只有一个事件对、不分块续写、不切换前台。会话监听、目标进程监听、AX焦点通知及睡眠/会话切换共同约束执行；缺少通知支持或权限时拒绝。
- 独立ForegroundCore包只含编排和端口，没有AppKit、时钟或系统I/O。9 个 Swift Testing 用例用注入端口覆盖焦点期限、静止期与代次、撤权、未知结果和回收；构建脚本先执行用例，再编译和签名。当前Windows主机没有执行 Swift 编译或这些用例；加入CI不代表CI已运行通过。
- 发送不是成功回执。目标进程监听须观察到本次两个不同标记，之后再次核验身份、焦点与权限；只有有界文本和选区能证明精确追加时才可返回ok。按键、点击和不能证明的结果保持unknown。
- PTY沿用受管POSIX后端；Keychain另有只操作新测试命名空间的显式验收脚本。未知写入不重试，执行前保存私有清理标识；两项均仍需Mac实测。

## Apple Silicon 首批构建与启动

macOS 27.2.1（26B131）上使用 Command Line Tools 的 Swift 6.4.2、macOS SDK 27.2 与原生 arm64 Python 3.12.16。此工具链没有 XCTest，构建门禁改用 Swift Testing，保留 9 项前台核心和 8 项真实临时文件 ACL/属主检查。测试以宿主架构和 macOS 14 为目标，构建器显式补充所选工具链中的 Swift Testing 宏搜索路径（该目录存在时），XML 写入指定输出目录。

本机新实例通过 `init`、`doctor` 和工作台回环验证，浏览器自动连接并读取隔离实例的 Agent 清单。开发签名包通过 `codesign --verify --strict` 并固定安装。生产宿主通过 IPC6 probe 识别当前图形会话并回收进程，AX、录屏及前台控制报告权限拒绝。原生 PTY 样例已读回 Unicode，退出后的 `close(5)` 回收确认连续返回 false，保留为后续 POSIX 终端断点。

## Mac mini 上执行

先同步本次封存的完整源码及锁文件，核对 [当前交接](../../../HANDOFF.md) 和 [证据](../../../evidence/v2-macos-control.json) 中的指纹。不要仅复制 Swift 目录，宿主和 IPC 必须匹配。使用非 root、已登录的 macOS 14+，安装含 Swift Testing 的 Swift 6+ 工具链（已验证 Command Line Tools 27.2）及 Python 3.12。新环境可按部署文档安装锁定依赖；已有工作环境继续用 `--no-sync`。

构建 arm64 开发签名包（Intel 使用 x86_64；通用包使用 universal）：

```sh
uv run --no-sync python agent/scripts/build_macos_helper.py --arch arm64 --output temp/build/mac-native-arm64 --install
```

输出目录必须不存在。默认签名为 ad hoc；有正式证书时使用 `--signing-identity`。ForegroundCore 和 8 项原生路径测试、构建、签名验证及来源检查成功后才安装。固定位置已有应用时脚本拒绝覆盖；先核清其来源及 TCC 状态，另存恢复副本，再决定替换，不能直接递归删除。更换签名或路径后必须重新核验权限。

## 用户服务的独立验收

先按[服务合同](../../operations/V2_SERVICES.md)准备**安装到不可变目录的实际 wheel**、独立测试 home/workspace、runtime、request 和 deployment bundle。使用当前正常登录的非 root 图形账号；SSH、系统 daemon、锁屏状态或不能核验的图形会话不会被当作当前 GUI 权限。只读服务入口不申请 AX、屏幕或输入权限，也不替验收者修改 ACL。

优先在 Mac 完成以下原生开发核验，再运行真实安装：

1. 用当前 SDK 编译全部 Swift/C，确认 ServiceManagement 弃用 API 仍可用；其移除/失败必须使查询不可用，不切换到私有 API 或解析 print 文本冒充完整描述。
2. 检查同一 GUI bootstrap 域内目标的 API 归属、实际字典字段及重复读取一致性。未知元数据字段先保持 unknown，不通过删除检查、放宽全部字段或伪造匹配来通过验收。不要将其他任务的字典或环境变量写入报告。
3. 对缺省目录、明确空 ACL、deny-delete、正向只读/写入 ACL 分别验证。当前默认缺失源码使用成功的 statx/filesec 属性查询，与读取失败分开；不能将 NULL/ENOENT 直接等同“没有 ACL”。构建新增 8 项 NativeIdentityTests 门禁，不为了测试移除真实用户目录 ACL。仍需 Mac 上的实际编译、文件系统行为和专用隔离账号证据。
4. 分别检查图形、远程、锁屏、快速用户切换及睡眠前后状态；Security Session 与 audit ID 保存为标识对，不要求其数值相等。

只读证据入口如下。`BUNDLE` 必须是已验证的真实安装部署包；每次 OUTPUT 使用一个新的绝对 JSON 路径，位于实例、程序和 bundle 外：

```sh
uv run --no-sync python agent/scripts/accept_macos_services.py --bundle "$BUNDLE" --expect absent --output "$OUTPUT"
```

可选预期为 `inspection`、`absent`、`registered`、`loaded`、`ready`。它组合注册元数据、完整加载定义和独立的运行身份/HMAC 就绪校验，生成私密、有界报告；不会安装、enable、bootstrap、disable、bootout 或停止服务。`registered` 允许未加载，不能替代 `ready`。没有必要条件时返回非零并保存可用诊断，不修改系统以取得通过。

原生限制收口后，真实安装/卸载在隔离账号按既有授权规则单独确认。先生成并审阅 `service operation-plan BUNDLE install OUTPUT`，检查返回的 `starts_service=true` 与精确 SHA256，再显式运行 `apply-operation ... --approve ...`。源码不在实例锁内 bootstrap；系统可能独立处理登录项，状态必须保持注册/加载/就绪分离。手动激活使用 bundle 中的独立审阅命令，须在维护结束后另行确认。安装后先保存 `--expect registered`，登录或经批准激活后再保存 `--expect loaded` 和 `--expect ready`。

正常排空并确认实例锁释放后，重新生成卸载计划；其中 `stops_service=true` 表示 bootout 可能结束核验后启动的空闲进程。定义不符、活动实例、回收残留或无法证明目标状态时应拒绝；未知结果不重新 apply，使用已有回执和只读核查。最后保存 `--expect absent`，核对测试 home/数据库未被删除。三种预期及人工操作记录共同保留，单独一个只读报告不能证明安装/卸载闭环或 72 小时验收。

### D4 交换激活交接

当前源码已加入 `omni-service-exchange-3` 及 `omni-service-activation-1`。两份固定安装须重新构建、捕获并包含相同启动合同；不能用此前安装搭配新计划。当前迁移头为 v2_0039，版本以实际源码及 doctor 为准；跨 schema 使用既有结构升级合同，不导入 V1 数据。Windows 只验证私密文件、实例锁、原有应用用例及注入的管理器/逻辑路径，未验证 Mac 原生。

完成上述 Swift、ACL 与真实注册前提后，按[交换合同](../../operations/V2_SERVICE_EXCHANGE.md)准备隔离的新旧版本与 home。先审阅计划的源卸载影响、目标启动以及 abandon 解除后续登录限制。真实变更另按既有授权执行。验收包含：

1. claim 后、启动预留前模拟登录激活；源与未预留目标均须拒绝，不能创建随机业务 boot 或执行模型/设备动作。
2. 注册结束后，只有精确预留可领取一次；bootstrap/kickstart 后核验同一 boot 与加载 PID。已加载、未加载、权限拒绝、登录切换、睡眠恢复和目标退出分别记录。
3. 在安装/启动前后中断，resume 只观察已派发步骤；未知结果不得再次激活。调用者取消和辅助进程回收未完成时保留预约/实际任务。
4. 完成后释放预约；显式 abandon 后注册与数据保留，下一次登录可能启动目标。隔离不删除旧 unknown 或预约，救援使用新目录及独立名称。缺失/损坏/旧格式预约不报告成功。

完成真实交换后，保存当前预定启动的只读证据：

```sh
uv run --no-sync python agent/scripts/accept_macos_services.py --bundle "$TARGET_BUNDLE" --exchange-plan "$EXCHANGE_PLAN" --expect ready --output "$NEW_PRIVATE_REPORT"
```

报告格式为 `omni-macos-service-acceptance-2`。该入口将完整交换历史读取前后对照，核对终态、预定 boot、当前 HMAC、原生 PID 和加载定义；不操作服务。旧 boot、历史变化、未完成交换或证据不足均不能通过 ready，关闭预算耗尽时继续保留实际查询的回收归属。输出必须位于两个 home、安装、bundle、备份和 journal 之外。它只证明当前观察，不替代真实变更操作记录或整个平台验收。

只有所有者明确执行以下命令才申请权限；普通启动、probe 和 doctor 不运行它们：

```sh
"$HOME/Applications/OmniOperator Desktop Helper.app/Contents/MacOS/OmniDesktopHelper" --request-permission accessibility
"$HOME/Applications/OmniOperator Desktop Helper.app/Contents/MacOS/OmniDesktopHelper" --request-permission screen
"$HOME/Applications/OmniOperator Desktop Helper.app/Contents/MacOS/OmniDesktopHelper" --request-permission input
"$HOME/Applications/OmniOperator Desktop Helper.app/Contents/MacOS/OmniDesktopHelper" --request-permission post
```

授权后重新启动辅助进程并重新 probe，不把命令退出或系统设置界面已打开当作授权成功。

在仅由脚本创建的隔离样例上验收 AX、密码脱敏及截图：

```sh
uv run --no-sync python agent/scripts/accept_macos_desktop.py --build temp/build/mac-native-arm64 --output temp/build/mac-native-read-acceptance
```

所有者准备好前台隔离窗口后，可独立执行受控语义写入验收：

```sh
uv run --no-sync python agent/scripts/accept_macos_desktop.py --build temp/build/mac-native-arm64 --output temp/build/mac-native-action-acceptance --mutate-owned-fixture
```

该命令只设置样例文本并调用样例按钮，保存原始回执及独立状态证明。unknown不触发重放。上述语义验收命令不发送前台键鼠输入，不读取个人应用，不代表整个平台已验收。

逐次前台输入使用独立隔离样例运行：

```sh
uv run --no-sync python agent/scripts/accept_macos_desktop.py --build temp/build/mac-native-arm64 --output temp/build/mac-native-foreground-acceptance --foreground-owned-fixture
```

该显式命令只向本次创建的样例追加文字、发送Left、在中间插入文字并点击按钮。样例可激活自己的窗口；生产辅助程序不抢焦点。若系统阻止聚焦，需所有者手动点击样例文本框。测试保存原始回执并独立核对样例状态，不重写unknown，不重发。语义和前台两种验收不能合在一次样例中。

PTY与Keychain分别执行：

```sh
uv run --no-sync pytest -q -n 0 agent/tests/v2/platforms/common/test_v2_terminal_native.py::test_native_terminal_unicode_resize_exit_and_verified_cleanup
uv run --no-sync python agent/scripts/accept_macos_keychain.py --write-owned-item --output temp/build/mac-keychain-acceptance
```

Keychain命令会创建、读回并删除随机命名空间中的一个测试条目。脚本不记录凭据值；遇到已有条目不会覆盖或删除。主进程30秒等待，工作器另有35秒生存上限，超时或清理不明不能标记通过。仅在cleanup.json的needs_cleanup为true时，由所有者显式清理该测试条目：

```sh
uv run --no-sync python agent/scripts/accept_macos_keychain.py --cleanup temp/build/mac-keychain-acceptance/cleanup.json --output temp/build/mac-keychain-cleanup
```

本地离线调用方验证：

```sh
uv run --no-sync pytest -q -n 0 agent/tests/v2/platforms/macos/test_v2_macos_helper.py agent/tests/v2/platforms/common/test_v2_desktop_contracts.py agent/tests/v2/platforms/common/test_v2_desktop_backend.py agent/tests/v2/platforms/common/test_v2_desktop_capture.py
uv run --no-sync pytest -q -n 0 agent/tests/v2/platforms/common/test_v2_foreground.py agent/tests/v2/platforms/common/test_v2_foreground_transport.py agent/tests/v2/platforms/macos/test_v2_macos_acceptance.py
```

## 待接续开发及证据回传

先编译并修正Swift/C与真实API问题，再验证前台协议、事件监听、权限撤回、锁屏、登录切换、睡眠恢复、多屏缩放和回收。主机已有前台输入、PTY、Keychain源码路径和实测步骤，不能把编写完脚本算成实测完成。Linux开发可在Mac交接后接续；三平台真实结果仍分别登记。

回传构建清单、源码指纹、架构、系统/Xcode/Swift 版本、签名类型、原始编译日志、专项 XML、只读、语义、前台及Keychain的acceptance.json和PTY原始结果 和缺陷记录。只回传隔离样例图片，不包含令牌、聊天、个人截图或 Keychain 内容。编译失败也保留原始输出；开发完成、离线验证、真实验收分别记录。三平台全量及各 72 小时仍是后续候选版门槛。

接口依据：[AX 消息超时](https://developer.apple.com/documentation/applicationservices/1459345-axuielementsetmessagingtimeout)、[AX 有界数组读取](https://developer.apple.com/documentation/applicationservices/1462060-axuielementcopyattributevalues)、[独立窗口过滤器](https://developer.apple.com/documentation/screencapturekit/sccontentfilter/init(desktopindependentwindow:))、[ScreenCaptureKit 截图](https://developer.apple.com/documentation/screencapturekit/scscreenshotmanager/captureimage(contentfilter:configuration:completionhandler:))。


## 三平台统一系统凭据样例（尚未执行）

新`accept_native_credentials.py`通过生产NativeCredentials/受管worker执行，仅操作随机独立测试命名空间。只在已准备的隔离账号中显式运行；此处未调用任何系统存储。

```sh
uv run --no-sync python agent/scripts/accept_native_credentials.py --write-owned-item --output temp/private/native-credentials-unique
```

先将cleanup.json清理身份落盘并同步，再验证测试项不存在、写入随机值、完整回读、删除和不存在核验。不会改已有条目；证据不包含密钥、引用或系统存储内容。sample_passed只证明该次样例，不替代工作台审计、完整平台或真实账号验收。当前实测状态保持待真实验收。

遇到丢回复、锁定、权限或超时后，不在finally自动重做/删除；保留cleanup_required和未确认状态。确认实际进程已回收、OS解锁或提示已处理后，验收者显式清理该测试身份：

```sh
uv run --no-sync python agent/scripts/accept_native_credentials.py --cleanup <原目录>/cleanup.json --output temp/private/native-credentials-cleanup-unique
```

清理只接受本平台的固定格式随机token，不能传入生产命名空间或任意引用。原生解锁提示、拒绝/撤回、注销与睡眠恢复、原生服务阻塞及条目清理分别回传；不要回传密钥、系统凭据清单或数据库。Linux原生后端需要实际Secret Service及可访问的当前UID用户总线；headless使用显式FileVault，不自动生成同目录主密钥。Keychain/WinVault的权限与提示也必须在对应平台核验。


D4追加独立root管理员包：单次注册/卸载/start使用标准库、固定原生框架probe和Darwin kqueue无fork命令合同，不在root执行本图形helper。必须由管理员独立审阅并使用受信root Python；Mac原生ABI/ACL/dscl/peer/kqueue/launchd未在本Windows主机验证，见[管理员合同](V2_MACOS_SYSTEM_SERVICES.md)。

## macOS PTY 退出回收

在 macOS 27.2.1 arm64 上，已退出的隔离 shell 仍能查询到创建时间和 zombie 状态，`getsid` 与 `getpgid` 会返回 `ESRCH`。POSIX worker 对会话身份查询的 `ESRCH` 沿用进程消失时的只读枚举路径。完整扫描确认没有存活成员才允许报告回收。扫描失败或仍有会话成员时返回未确认，无会话身份时不发送信号。

隔离样例通过 Unicode 写入与读回、调整尺寸、退出和回收，原生启动拒绝与启动中取消样例也通过。注入用例覆盖两个查询失败点，以及空会话、扫描不完整和存活成员。应用生命周期、持久回执、HTTP 和架构调用方完成专项验证。此批 Linux 实机复验待图形虚拟机准备。

### 2026-11-22 复验（基线 18b75b75）

两个任务提交已 rebase 到 `origin/v2` 的 `18b75b75`（`56f0aff1`→`e4a61f4d`、`98a12a13`→`55e71150`，`git range-diff` 确认补丁等价），并在此基线上重新构建与复验：

- 本机 macOS 27.2.1 arm64、Swift 6.4.2、SDK 27.2。`build_macos_helper.py --arch arm64` 先执行 9 项 ForegroundCore 与 8 项 NativeIdentityTests，再编译、ad hoc 签名并通过 `codesign --verify --strict`；产物为 `omni-desktop-ipc-6`、arm64，清单 18 个源文件哈希与已安装应用完全一致。两次构建的可执行文件哈希不同（链接期 LC_UUID 不同），因此保留原安装、未覆盖。
- 真实 `DesktopBackend.probe()` 经已安装 helper 与 IPC 6：interactive 会话，四个能力均为 `desktop_permission_denied` 且 `available=false`（拒绝即失败关闭），关闭后 resident 与 cleanup_pending 均为 0。
- 原生终端专项 10/10 通过（0.30 秒），覆盖 Unicode 写入/读回、resize、退出与回收确认；PTY 回收修复在 `18b75b75` 上仍成立。
- macOS helper/桌面/前台/服务调用方 10 个文件 233 项中 232 通过（20.44 秒）。唯一失败为既有基线问题：`origin/v2` 的 v2_0051 迁移新增两表（147），`test_v2_desktop_capture.py` 仍固定 145；在干净 `origin/v2` 工作树可复现，与本批无关。
- 未执行：隔离账号（本机只有一个非管理员账号）、睡眠/锁屏、LaunchAgent/Keychain 实机写入；TCC 授予在本日后续批次完成，见下节。原始日志与 probe 在 `temp/build/mac-2026-11-22/`，汇总见 [证据](../../../evidence/v2-macos-native-2026-11-22.json)。

### 2026-11-22 本机权限与语义写入验收

所有者在本机图形会话按交接命令依次授予四个 TCC 权限（辅助功能、屏幕与系统音频录制、输入监控、事件发送）。授予前 probe 四能力全部 `desktop_permission_denied` 且不可用；授予后 `window_identity`/`accessibility`/`screen_capture`/`foreground_control` 全部 `granted`。read 验收以 ScreenCaptureKit 捕获隔离样例 1040×556，AX 观察 9 个节点、密码字段已脱敏且 canary 未出现。

语义写入首次验收在真实权限下失败：所有动作在派发前被 `desktop_user_intervened` 拒绝（effect=none、verification=not_dispatched），包括所有者保持鼠标键盘静止的一轮。根因是 `InputWatch` 基线从未 arm（baseline=0），tap 安装后到首个检查之间的 fixture 激活与窗口焦点抖动被误计为用户介入。修复为安装后立即 `arm()`，并把系统禁用的监听区分成 `desktop_monitor_unavailable`（不再伪装成用户输入）；重建、重装到同一固定路径后重跑。修复后 `--mutate-owned-fixture` 通过：set_text `ok/applied/value_matches`，invoke `unknown/desktop_postcondition_unverified`，独立样例状态确认按钮触发一次，未重放。重装后首次 read 验收出现冷启动身份竞态（首次 verify 不匹配、立即重试通过），宿主保持 fail-closed，按测试竞态记录。

前台输入仍未完成：键盘输入与 Left/插入能改变样例文本与选区（回执按合同为 ok/unknown，绝不宣称成功）；定向鼠标点击被目标 tap 确认后未按下样例按钮（回执 unknown、clicks 仍 0），官方 `--foreground-owned-fixture` 在最后一步状态等待超时。原始失败保留，作为前台输入批的独立缺陷；后续前台点击已修复（见上节）。另据本日 TCC 归属实测，本节功能证据不能证明 helper 独立获权，见文末“TCC 归属实测”。证据：[权限与语义](../../../evidence/v2-macos-permissions-2026-11-22.json)。

### 2026-11-22 Keychain 与 LaunchAgent 基线

Keychain 隔离样例通过：worker 在随机 `macos-acceptance-<token>` 命名空间写入、完整读回、删除并核验不存在，`readback_verified` 与 `cleanup_verified` 均为 true、`cleanup_required=false`，worker 已回收、stderr 为空，未出现系统提示；独立 `security find-generic-password` 复核返回 44（条目不存在）。未读取、覆盖或删除任何既有条目，报告不含凭据值。

LaunchAgent 基线：本机当前没有 OmniOperator 用户服务 job，`~/Library/LaunchAgents`、`/Library/LaunchAgents`、`/Library/LaunchDaemons` 下也没有相关 plist（仅 Edge 更新器）。真实注册、登录激活、loaded/ready 观察与卸载仍需不可变部署包、隔离账号和所有者批准的 apply-operation，本批未执行；睡眠/锁屏/快速用户切换同样未触发（会中断当前图形会话），其注入路径已由前台专项覆盖。

证据：[Keychain 与 LaunchAgent](../../../evidence/v2-macos-keychain-launchagent-2026-11-22.json)。

### 2026-11-22 前台输入验收与定向点击修复

前台专项首次失败：键盘输入、Left 与中间插入都改变了隔离样例的文本/选区（回执分别为 ok/unknown），但定向鼠标点击只让目标 PID tap 确认两个事件、样例按钮 clicks 仍为 0，官方 `--foreground-owned-fixture` 在最后一步状态等待超时。

诊断：在样例内临时统计 NSEvent 本地事件（已回退），确认 mouseDown/mouseUp 各到达 1 次但 `windowNumber=0`；只加 down/up 间隔也无效。结论是 macOS 27 上 `CGEvent.postToPid` 的鼠标事件不携带窗口号，AppKit 不会把它路由到任何窗口；目标 tap 只能证明到达进程，不能证明路由或业务效果。

修复：键盘仍 `postToPid`；点击改为在投递前重新核验目标、确认点仍在目标窗口内，`CGWarpMouseCursorPosition` 移到已核验点后经 `.cghidEventTap` 投递同一有界 down/up 对，随即恢复原光标位置。窗口按 takeover 合同已在前台、AX 命中已确认点到控件，仍只有一个事件对且不调用激活 API。契约文字同步到 [V2_DESKTOP](../../contracts/V2_DESKTOP.md)。

修复后 read、semantic、foreground 三个显式验收全部通过：foreground 回执为 text_appended + unknown×3，独立样例状态的文本、选区与 clicks 全部符合，`replayed=false`，样例 clicks 0→1。交付 helper `8dbc0d3c` 上 read 截图 sha256 `8565ae96…`、语义 `value_matches` 复验通过。证据：[前台输入](../../../evidence/v2-macos-foreground-2026-11-22.json)。

### 2026-11-22 TCC 归属实测（重要，待决策）

撤回验收时发现权限并不属于 helper 自身：把已安装应用原样复制到新路径（从未授权）后，用与宿主相同的环境发 IPC 6 probe，四个能力仍全部 `granted`；tccd 日志显示 `responsible=com.deepseek.dsh`、`requesting=org.omnioperator.desktophelper`、`AUTHREQ_SUBJECT=com.deepseek.dsh`、`authValue=2`。`tccutil reset ScreenCapture/Accessibility org.omnioperator.desktophelper` 虽返回成功，新建 helper 仍报告 `granted`；所有者在系统设置里关闭 helper 条目也未产生拒绝。

结论：helper 作为宿主（本机为 DeepSeek Harness）的 POSIX 子进程运行，macOS 以 responsible process 判定 TCC，而不是 helper 自己的 bundle 身份。此前“授予/拒绝”的功能证据仍然成立，但不能证明 helper 独立获权；本页与 `V2_DESKTOP` 中“权限属于辅助应用自身”的说法在当前启动方式下不成立。

补充实测（同一日）：临时给 helper 加只读 `--self-test <json>` 入口（随后回退），用 `open -n ... --args --self-test` 经 LaunchServices 启动。结果 `bundle_id=org.omnioperator.desktophelper`、`ppid=1`、`__CFBundleIdentifier` 为自身，四个能力全部 `denied`（本批已重置 helper 条目）；tccd 的 `AUTHREQ_ATTRIBUTION` 只有 `requesting=org.omnioperator.desktophelper`、没有 `responsible` 项，即 helper 成为自己的 responsible process。

结论与建议：选 (a) 可行——用 LaunchServices/NSWorkspace（或 XPC 服务）启动 helper，使其真正拥有自己的 TCC；因为该启动方式没有可用的双向管道，需要一个常驻的只读诊断入口（如 `--diagnose <path>`）供验收。随后由所有者对 helper 自身授予辅助功能/屏幕/输入/投递权限。待办：该启动机制与只读诊断入口属合同/安全面，需 A 与所有者批准；ad-hoc 授权可能绑定 cdhash，最终授权必须在交付二进制上完成。未验：由 launchd 服务启动时的归属；TCC.db 受保护未读取。证据：[TCC 归属](../../../evidence/v2-macos-tcc-attribution-2026-11-22.json)。

设计合同与方案 A1（LaunchServices 启动 + 宿主 UDS 传输，原型已通：0.266 秒、IPC 6 帧原样、`LOCAL_PEERPID` 可取对端、tccd 无 `responsible` 项）：[V2_MACOS_HELPER_TCC](../contracts/V2_MACOS_HELPER_TCC.md)。实施前需所有者/A 批准；当前生产仍为宿主归属的 POSIX 启动。

### 2026-11-22 服务只读查询实机验证（absent 路径）

用已安装交付 helper（`966be5fc…`）直接运行 `--service-query` / `--system-service-query`，查询随机 `com.omnioperator.v2.<uuid>` 标签：用户域 `gui/501` 与系统域均返回 `complete=true`、`loaded=false`、`job=null`、`error_code=""`（协议 `omni-macos-service-query-2`）。这证明弃用的 `SMJobCopyDictionary`/`SMCopyAllJobDictionaries` 在 macOS 27 SDK 27.2 仍可用，且“不存在”由完整枚举正面证明，而不是 nil 查询或枚举失败。查询由普通所有者执行：无提权、无 TCC 提示、无服务变更。未验 registered/loaded/ready 路径与真实安装；未创建瞬时 `launchctl` job（那属于真实注册变更）。证据：[服务只读查询](../../../evidence/v2-macos-service-query-2026-11-22.json)。
