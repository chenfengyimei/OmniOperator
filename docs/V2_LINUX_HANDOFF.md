# Linux 原生接入与验收交接

共享内核当前为96表/v2_0026、工作台v36，桌面IPC6/Portal IPC2/凭据IPC1。Linux AT-SPI/X11读取、Portal窗口帧、受控语义与系统凭据helper源码已接通；本轮独立接通D4终端核查，不扩大Linux输入授权。Linux/GStreamer/Portal、原生凭据与PTY仍未实测，持续窗口身份和前台/RemoteDesktop控制仍待实现；整个V2未完成，默认V1。

## 已实现的生产链路

`DesktopBackend → 固定 Python 辅助进程 → SessionInspector → LocalBus / AtspiDesktop`；Linux语义写入使用独立固定`omni.adapters.workers.linux_action`，按ready/permit逐次放行。主进程不加载 D-Bus、AT-SPI 或平台 API；核心不安装桌面依赖也可启动。请求总期限仍为 5 秒，辅助进程内部操作预算 4 秒；宿主最多 4 个实际操作、2 个常驻辅助进程。取消等待不会丢弃实际宿主，失败回收继续保留健康状态。

- 先核验 `/run/dbus/system_bus_socket`，随后查询 root 所有的 logind 唯一服务连接。核验当前 UID、活动本地 x11/wayland 用户会话、未锁屏、会话创建标记及 seat 的 ActiveSession。
- 辅助进程没有 logind 会话时，只接受 logind `User.Display`；已处于 TTY/SSH 会话时不回退到其他图形会话。root 不开放桌面能力。未取得 logind 证明时报告 unknown/unavailable，不能因缺少 DISPLAY 而报告已证明 headless。
- 用户总线固定 `/run/user/<uid>/bus`。AT-SPI 地址只取已核验的 `org.a11y.Bus.GetAddress`，接受受保护运行目录中的单个 Unix socket 或有限的 Linux abstract 地址；拒绝 TCP、多个地址、未知字段及跨 UID 路径。
- 每次连接在发送 D-Bus 认证数据前核验父目录、socket 所有者、设备/inode 和实际连接的 `SO_PEERCRED`。后续复核对端 PID/UID、创建时间及路径身份。使用本次已核验的 socket，不依赖环境变量指定总线，不重新连接。所有 RPC 禁用自动启动、固定唯一连接、检查响应来源及签名。
- `dbus-next==0.2.3` 是当前锁定实现；其受保护扩展点用于预连接、认证行及二进制帧限额，其他版本明确拒绝。认证行 4 KiB、单帧 64 KiB、每条连接最多 2048 条消息与 4 MiB 接收量。RPC 分别有超时；无数据库访问、服务激活或自动开启辅助功能。
- 通过 AT-SPI registry 列举最多 32 个应用、128 个顶层候选，返回最多 50 个窗口。目标绑定总线代次、唯一服务、实际进程创建身份、程序路径及图形会话；观察前后复核，变化即废弃。
- 观察最多 128 节点、8 层、单字段 512 字符、合计 16 KiB UTF-8；密码控件及子树不读取 Name/Text。跨连接子树、环、过量树明确截断。Linux响应必须带`coordinate_space: "window"`。仅在X11输入监测证明建立后提供AT-SPI语义动作候选；Windows/AX引用、全局坐标及前台输入候选均拒绝。观察引用本身不授权写入。
- UI 正文仅在观察响应短暂传递，不进入诊断、指标、元数据审计、视图缓存或验收包。异常、权限及清理失败使用静态错误码，不把空树作为成功。

## 当前限制与下一开发合同

同UID不足以证明应用属于所选图形会话。AT-SPI语义后端仍要求应用PID由logind `GetSessionByPID`证明归属；许多systemd用户服务应用没有这个映射，会返回`desktop_target_session_unverified`。X11采集后端使用另一路证明：先证明Xorg服务器属于当前活动会话，再由服务器XRes核验实际客户端PID、UID与创建时间。两种目标不按PID或几何互相映射，不能用X11证明直接解除AT-SPI限制。

窗口共享只读链路和X11监测支持的AT-SPI语义动作已接入生产源码；D3整包仍开发中。D3.linux.control继续实现X11显式前台输入及Wayland RemoteDesktop独立授权与输入暂停证明。Wayland语义及全局输入目前均保持不可用；窗口共享不授予控制权。原生Linux行为尚未验证。


## X11监测支持的AT-SPI语义动作（原生待验收）

沿用`POST /api/v1/agents/{agent_id}/desktop/actions`及通用工作台，客户端只提交当前view/snapshot/target/observation/node与session/request UUID、kind、text。节点候选只属于当前观察，不把总线名、对象路径或原生句柄暴露为客户端写授权，不新增业务表。

观察与证明使用同一次有界Name/Text读取；仅完整、未清洗或截断、非密码节点产生候选。证明包含所选窗口下最多7个子索引、祖先身份/名称链以及角色、名称、完整文本和动作目录SHA。执行在准备阶段与permit后重新定位、核验每级Parent和唯一应用连接；对象路径/索引仍不能证明提供方永久对象代次，禁止将证明跨视图持久复用。需要logind证明目标进程属于本图形会话，不能借X11的另一套PID证明绕过。

文本设置要求EditableText/Text与enabled/sensitive/showing/visible/editable；单选要求Selection父容器及可选子节点，拒绝多选父容器；调用仅接受唯一机器动作click/press/activate。文本最多512字符，超长控件保持可读但不提供动作。密码及祖先密码路径不读取正文，也不提供动作。

输入守卫另持有经过既有会话、Xorg进程、socket对端及Cookie核验的连接。要求XI2>=2.2、唯一启用主键盘/指针对、能证明释放的键鼠状态；触摸/手势、浮动键盘、多个主设备或未知类别保持不可用。只订阅原始事件与设备变更并丢弃载荷，跟踪当前焦点窗口的焦点/结构变化；不监听输入文本，不把keycode、坐标、设备名写日志。固定250ms静止期前后复核，事件/焦点/设备/锁屏变化、按键未释放、超限或协议异常均拒绝或停止后续动作。

辅助进程准备完成仅发送ready；主进程再次核验动作审批、运行、策略、资源租约、视图期限与会话代次后才发permit。初始动作帧不表示已派发。permit后重新核验完整节点证明和输入守卫才调用一次AT-SPI变更RPC，不设置焦点、不使用XTest/全局输入、不重试。当前焦点窗口只用于变化监测，未按PID/坐标映射到AT-SPI目标。

RPC前失败为denied；发出后失联、用户介入、目标漂移、清理失败或无后置证明为unknown。SetTextContents必须完整回读匹配，SelectChild必须回读IsChildSelected；DoAction返回也保持unknown。unknown进入现有核查账本，不自动重发。输入监测不可用时观察保留只读树、不暴露动作候选；Wayland始终如此，不能由XWayland或环境变量降级获得控制权。

该链路仍遵守宿主5秒总期限、辅助进程4秒预算、最多4项实际工作/2个helper和64KiB帧限额；初始帧与permit合计也受限。事件单条<=4KiB、最多256条、连接接收<=1MiB；所有进程/总线/socket回收由原生命周期持有，回收未证实保留容量、数据库和实例锁。主进程不调用X11或AT-SPI。

Linux实机先在隔离账号下打开临时GTK/Qt测试窗口，再通过V2工作台选择AT-SPI窗口，逐次观察、设置临时文本、人工审批并核对回读；另测列表单选和只在隔离窗口调用。无自动批量输入。分别验收密码、大树、目标退出/重建、审批撤回、用户输入、焦点变化、锁屏/睡眠、设备热插拔、未知结果和回收。当前Windows注入用例和浏览器不替代这些实测，也不代表输入守卫在实际Xorg/设备布局上已可用。

依据：[AT-SPI Action](https://github.com/GNOME/at-spi2-core/blob/main/xml/Action.xml)、[EditableText](https://github.com/GNOME/at-spi2-core/blob/main/xml/EditableText.xml)、[Selection](https://github.com/GNOME/at-spi2-core/blob/main/xml/Selection.xml)、[XInput协议结构](https://xcb.freedesktop.org/manual/xinput_8h_source.html)、[XISelectEvents](https://xorg.freedesktop.org/archive/X11R7.5/doc/man/man3/XISelectEvents.3.html)。

## Wayland Portal：授权、受控FD与窗口单帧

生产链路为`DesktopPortalService → PortalBackend → 固定Python长驻helper → PortalSession/SignalBus → 已核验的本机用户总线`。应用层保存短期Agent/view/boot/grant UUID归属，先持久登记请求审计，再通过prepared/permit复核视图后弹出系统授权；授权成功审计提交前API不报告authorized。等待用户选择在事务外。

沿用logind、socket实际对端和进程创建身份核验，固定Portal唯一服务；不自动启动服务或使用任意环境总线地址。先订阅精确sender/path/signature再调用，处理Response早于方法返回；拒绝伪造handle、签名、跨连接或多窗口结果。只请求一个WINDOW、隐藏光标、persist_mode=0，无restore token。要求ScreenCast>=6及有效pipewire-serial；不将可复用node ID当作永久身份。提供旧版Portal的发行版可能暂不可用，必须如实报告portal_version_unavailable。

每项选择最多90秒，helper总生存150秒，prepared放行5秒、心跳中断8秒关闭。主机共享桌面2个实际helper容量，Portal最多4个实际入场、32份有界元数据；每个Agent串行。Portal IPC2单帧16KiB、总256KiB、最多512事件；子进程不读数据库，原生对象、总线路径、stream信息和异常正文不进HTTP或审计。

监听Session.Closed，并复核活动会话、锁屏、Portal唯一连接与创建身份。显式关闭、换视图、重查权限、视图淘汰和实例排空都撤回会话；关闭Request/Session并回收实际进程。回收失败继续保留宿主与容量、显示cleanup_pending；后续关闭重试回收，再结算审计，不提前关闭数据库与实例锁。

所有者API为POST/GET/DELETE `/api/v1/agents/{agent_id}/desktop/portal`。POST只接受`{"consent":true,"view_id":"<UUID>"}`；GET带view_id；DELETE带view_id和grant_id。拒绝客户端提供native handle、PID、monitor类型或restore token。新增POST `/api/v1/agents/{agent_id}/desktop/portal/capture`，只接consent=true、view_id、grant_id、session_id。客户端不能提交FD、node ID、serial或输出路径。授权审计未提交、视图变化、跨Agent会话、依赖缺失或撤回时拒绝读取；每个共享最多4帧，同Agent串行，最多4个在途保存。input_authorized始终false。

主进程和HTTP不接触FD或GStreamer。只有专用Portal用户总线协商Unix FD；已核验的同一连接发送OpenPipeWireRemote，并按唯一sender、reply_serial、h签名、零索引和恰好一个描述符收取。设置不可继承，拒绝截断/多余描述符；部分帧、迟到响应、错误与关闭都保留清理义务。系统/AT-SPI普通连接仍禁止FD。

helper内单个有界工作线程调用固定GStreamer pipeline：pipewiresrc → 尺寸限制 → videoconvert → RGB → appsink。source只设置Portal FD、完整十进制pipewire-serial和dont-reconnect，不设置可复用node ID或默认远端；只接受必要属性均存在的插件。上游缓冲2、appsink 1、转换线程1；单帧最多3秒采样，边长4096、1600万像素、原始64MiB、PNG16MiB。RGB步长/偏移、平面、映射、均匀内容和NULL状态回收均核验；未能证明回收时不发布成功。

PNG只写宿主创建的私有UUID目录/固定frame.png，独占创建并fsync；大载荷不进IPC。宿主核验PNG/指纹，再由既有对象服务共同提交对象、会话引用与审计。v2_0025新增pipewire_window来源，保持不可变、作用域外键及遗忘触发器。取消HTTP等待不提前释放实际工作；撤回或会话内容变化使帧失效。文件清理失败保留暂存义务，关闭返回未完成并保护存储与实例锁。

工作台仅手动选择、查询、保存和结束共享，不自动轮询或抓图。预览/移除引用复用现有截图区；离开、换Agent/视图、重查权限均撤回。画面不自动发给模型，也不授权键鼠输入。状态是上次查询结果，每次保存仍由服务端重新核验。

Linux原生授权样例使用隔离Wayland账号和临时窗口：

```sh
uv run --no-sync python agent/scripts/accept_linux_portal.py --consent-window-share --hold-seconds 1 --output temp/private/linux-portal-unique
```

这个命令只检查授权，不读取像素。consent_sample_passed仅说明该次授权样例持有结束仍有效且资源已回收，不代表业务审计或平台通过。本机没有执行此原生命令。

需要安装帧组件，并显式增加像素授权才检查一帧：

```sh
uv sync --frozen --extra desktop-linux --extra desktop-wayland
uv run --no-sync python agent/scripts/accept_linux_portal.py --consent-window-share --capture-window --output temp/private/linux-portal-frame-unique
```

安装只在新的Linux验收环境进行。desktop-wayland锁定PyGObject 3.48.2及其依赖；另需系统GLib/GObject Introspection、GStreamer>=1.20的Gst/GstApp/GstVideo typelib、videoconvert/appsink和带fd/target-object/dont-reconnect属性的PipeWire插件。Debian/Ubuntu包名与实际插件属性在实机核验后记录；不假定系统自带GI可被虚拟环境导入。缺少或不匹配时明确降级，不改变核心安装依赖。

验收者在系统选择器中只选择临时窗口；分别检查颜色、缩放、多屏、最小化/销毁、锁屏、撤回和Portal重启。capture_sample_passed只证明该次原生单帧样例；输出仅尺寸/方法/大小/指纹，不把像素存入证据。读取结果未知时字段为null，不写成未发生。它仍不覆盖业务审计、工作台和完整Linux验收；这些必须另行真实执行。

依据：[ScreenCast](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)、[Request](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Request.html)、[Session](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Session.html)。后续消费者依据[PipeWire远端FD示例](https://docs.pipewire.org/page_tutorial5.html)实现并验证，不以当前授权通过替代帧读取证据。

## X11采集源码合同与限制

`linux_x11_socket.py`只连接logind给出的`:display.screen`对应`/tmp/.X11-unix/Xn`，不使用环境DISPLAY或远程TCP。认证前核验socket及父目录、SO_PEERCRED、进程创建身份和logind归属；服务器必须为root保护的真实Xorg二进制及目录。Xwayland、代理、自定义服务器、无法证明会话或二进制时拒绝，不能以同UID替代。这里尚无真实Xorg运行结论。

随后读取当前用户私有Xauthority，只接受固定家目录`.Xauthority`或当前`/run/user/<uid>`下的无链接路径、独占普通文件及受限权限，最大64KiB。默认尝试gdm文件和`.Xauthority`；可显式指定受管范围内的XAUTHORITY作为文件提示，不能据此授权服务器。只接受本机hostname、显示编号和唯一FamilyLocal MIT-MAGIC-COOKIE-1记录；通配/远程/歧义记录拒绝。Cookie不进IPC、日志或报告。发行版使用其他布局/认证时明确列为限制。

`linux_x11_wire.py`是有限读取协议：setup/元数据64KiB，最多1024请求、2048消息，图像单次64MB、总接收66MB。逐帧校验长度/序号，错误不导出服务器正文。要求XRes1.2；EWMH列表仅为最多128个候选，所有权以XRes实际客户端PID为准，不读取_NET_WM_PID作为证明。跨用户、远程客户端无本机PID、隐藏/override-redirect或无法核验的目标拒绝。

截图要求Composite>=0.2且目标已有重定向pixmap。只命名该已有图像，不执行RedirectWindow/Subwindows；没有普通窗口GetImage、根窗口、全屏或其他窗口回退。订阅当前窗口结构生命周期，退出/隐藏/重挂载/尺寸改变等通知使结果失效。仅TrueColor标准RGB掩码、24/32位和32位行对齐；超过8192边长、1600万像素、16MiB PNG或均匀图像拒绝。RGB缓冲不含鼠标与管理器装饰，实际完整性需人工核验。

截图前后复核服务器、会话、客户端PID/创建身份及目标结构；临时文件必须为宿主生成的私有UUID目录中的`frame.png`，独占创建并fsync。宿主在现有文件执行器内校验指纹/尺寸/编码，再由对象服务原子登记对象、会话引用和审计；取消或过期不得发布迟到内容。资源由FreePixmap/连接关闭及既有宿主回收，失败显式报告。跨调用XID与相同元数据不能证明永久对象代次，不授权写动作。

工作台列表中语义和采集目标分别携带`can_observe/can_capture`，禁用不支持的操作；同一应用可能有两种目标，不自动映射或自动执行。AT-SPI失败不会因其可选连接残留阻塞已核验X11；清理失败仍阻止成功。

PTY、Secret Service 与服务部署还需分别完成本机闭环。Linux 容器只证明无桌面核心，不能替代 X11/Wayland 桌面、权限撤回、登录切换、睡眠恢复或 72 小时验收。

## 在 Linux 运行的步骤

使用已登录、非 root 的隔离测试账号及独立工作目录。正式入口仍是 V1；通过当前 V2 开发入口或下列独立原生检查工具验证。依赖安装在新验收环境中完成，不能修改正在运行实例的环境：

```sh
uv sync --frozen --extra desktop-linux
uv run --no-sync pytest -q -n 0 agent/tests/test_v2_linux_bus.py agent/tests/test_v2_linux_desktop.py agent/tests/test_v2_linux_acceptance.py
uv run --no-sync python agent/scripts/accept_linux_desktop.py --output temp/private/linux-probe-unique
```

第三条只做真实会话/能力诊断；`probe_completed` 表示诊断完成，能力是否可用以报告字段为准，不等于桌面验收通过。输出目录必须不存在。报告默认不含 PID、程序路径、会话 ID、节点正文或截图。

在临时目录手工打开单窗口 GTK 样例，填入非敏感测试文本；取得实际窗口所有进程 PID。明确选择该样例后读取：

```sh
uv run --no-sync python agent/scripts/accept_linux_desktop.py --fixture-pid <实际样例PID> --consent-read-ui --output temp/private/linux-observe-unique
```

工具只接受匹配 PID 的一个可见顶层窗口，记录前后身份核验、节点数量、密码节点数、截断、坐标空间和回收结果；不执行动作或保存原文。`sample_passed` 只证明该窗口的只读样例。应用无法证明会话归属时记录限制，不修改断言放行。多窗口、目标退出、锁屏、总线重启、权限撤回及阻塞仍需分别实测。

X11截图样例必须显式选择采集后端及像素读取；不要选择用户日常应用。只保留图像大小、方法与指纹，不将像素加入验收包：

```sh
uv run --no-sync python agent/scripts/accept_linux_desktop.py --fixture-pid <实际样例PID> --consent-read-ui --native-backend x11 --capture-window --output temp/private/linux-x11-unique
```

该样例要求已登录X11、XRes与已运行的Composite管理器；不自动开启服务或更改桌面。无Composite、用户服务会话、Xauthority布局、目标销毁、重挂载、遮挡、缩放、多屏和资源回收分别记录实测结果。`sample_passed`不代表完整Linux支持，也不替代工作台与数据库的真实集成验收。

## 回传证据

保留源码与锁文件指纹、构建/内核版本、发行版、架构、登录方式、X11/Wayland、辅助功能服务及 `dbus-next` 版本。分别回传专项原始报告、脱敏的 `evidence.json` 和复现步骤。另行记录是否真实测试了 socket 认证、对端变化、目标退出、密码控件、大树、阻塞超时、取消和残留进程。

不得回传聊天数据库、应用正文、屏幕图片、环境变量全集、D-Bus 内容转储、Cookie 或凭据。未执行的项目标为待真实验收；不以 Windows 注入测试或容器结果更新 Linux 支持状态。

协议依据：[logind D-Bus](https://freedesktop.org/software/systemd/man/latest/org.freedesktop.login1.html)、[AT-SPI Accessible](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/class.Accessible.html)、[dbus-next 0.2.3 transport](https://github.com/altdesktop/python-dbus-next/tree/v0.2.3/dbus_next)、[RemoteDesktop Portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)。

X11依据：[Xserver XRes客户端身份](https://github.com/mirror/xserver/blob/master/Xext/xres.c)、[Composite命名已有窗口图像](https://github.com/mirror/xserver/blob/master/composite/compext.c)、[Python-Xlib核心协议字段](https://github.com/python-xlib/python-xlib/blob/master/Xlib/protocol/request.py)。项目实现没有复制这些库的源码。

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

