# Wayland 独立控制许可开发

独立 RemoteDesktop 原生许可、固定 worker、父进程归属、持久审计和 HTTP/工作台入口已经接通。真实输入暂停、目标证明与实际派发仍在开发，当前没有开放 Wayland 输入。系统设备许可与业务动作授权分别记录；所有回执的 `input_authorized` 始终为 false。

`domain/desktop_control.py` 使用独立 `omni-desktop-control-1` 协议。打开请求必须带交互输入目的、显式同意、键盘/鼠标位图及 Agent/view/boot/grant 的精确 UUID 绑定。仅接受 open/permit/close；窗口共享协议、恢复令牌和任何输入命令都不能作为控制请求。系统许可阶段名为 `system_authorized`，不使用共享会话的 `active` 冒充动作权限。

`linux_remote_desktop.py` 在独立的当前 UID 用户总线连接核验 Portal 唯一服务进程、活动未锁屏的本地 Wayland 会话及 RemoteDesktop>=2。明确请求键盘、鼠标或两者，不依赖“默认全部设备”。新建自己的随机 Session，经 RemoteDesktop.CreateSession/SelectDevices/Start 获取系统同意；persist_mode=0，系统许可路径不使用恢复令牌、剪贴板或屏幕源，也不自动打开EIS；内部按需EI连接接续见下节。返回设备种类必须与请求相同，部分/额外设备、剪贴板开启、非空屏幕流、恢复令牌或未知扩大字段均拒绝。

ScreenCast 与 RemoteDesktop 各自持有新会话、匹配订阅和关闭义务。共享方法只新增固定子类接口选择，原 ScreenCast 路径不改变。控制适配器允许的请求方法只有三个许可方法，不能通过通用请求机制调用 Notify*；打开 PipeWire 被明确拒绝。系统许可本身无法发送按键、鼠标或像素。

固定 `workers/desktop_control.py` 验证初始控制协议后才构造原生对象。原生元数据准备成功并发 prepared 后，才接受一次同绑定 permit，再打开系统选择器。提前、重复、跨绑定、未知命令、EOF 或父进程关闭都会终止；初始帧不能许可系统弹窗。最多三个输入帧、262144 字节、150 秒进程寿命；启动与 permit 等待各 5 秒，选择器 90 秒，心跳/输出有界。状态严格迁移，干净关闭回执必须在 Session、未完成 Request、订阅和总线实际关闭后发出；失败或超时不能声称 clean。

原生会话持续核对 Portal owner/创建身份、logind 会话/锁屏和 Closed 通知，变化或到期清除设备许可且不复活。选择器仍等待时收到关闭，会取消等待并关闭预定 Request/Session；未自动重放 Start。原生对象、匹配规则与管道清理失败会保留失败结果。

41 项新增离线原生协议/worker 回归、39 项窗口共享调用方及 2 项类型合同，共 82 个不同专项已通过。新增分离会话用例曾漏导入，修正测试后指定节点通过，原失败报告保留；4 个修改后端文件 strict 零错误/警告。没有真实 Wayland、系统选择器、EIS、输入或全量验收。见[专项证据](evidence/v2-remote-desktop.json)。

## 父进程、业务和显式入口（2026-11-17）

`adapters/platform/desktop_control.py` 只启动固定私有 worker，与原桌面/窗口共享共用两个实际进程名额，最多四项未结清准入。打开在 prepared 后重新核验当前视图才发 permit。迟到创建、调用者取消、坏帧、提前授权和设备变化均停止许可；输出最多 512 帧、262144 字节，超量后继续有界排空管道直至实际回收。停止读 stderr 曾使 Windows 管道无法退出，原始失败与修复验证分别留档。原生 clean 与实际进程/管道/Job 回收同时成立才发布 clean；回收失败保留同一归属及名额，后续 close 重试，不新造清理所有者。

`application/desktop_control.py` 将授权绑定 Agent/view/boot/grant，最多四项打开、八个保留空间。复用原诊断审计：请求审计落盘后才打开后端；系统许可审计完成后才显示“许可已记录”。审计/视图失败立即撤回，不把 OS 授权当成业务动作授权。关闭审计未落盘时保留业务任务和数据库；离页与启动审计并行时返回 `portal_cleanup_pending`，后台完成实际关闭及审计后才移除旧视图记录。

所有者 API 沿用 V2 内核现有 `/api/v1/agents/{agent_id}/desktop/control`：POST 明确提交 view_id、purpose=`interactive-input`、consent=true 和 devices=1/2/3，GET 查询当前 view，DELETE 精确指定 view_id/grant_id 撤回；鉴权与 no-store 保持。工作台独立显示键盘/鼠标种类、未默认勾选的同意框、申请/查询/结束按钮，改设备后清除同意，离页撤回，迟到回执不能恢复视图。共享窗口入口不能替代此许可。

固定副本 93 个不同专项节点通过，包括 21 个新增节点、72 个原调用方及架构/类型合同。离页待结清用例首轮误将待处理回执当完成，修正后同时核对 503、最终实际进程归零、授权记录归零和幂等回执，原报告保留。修改 8 个后端文件 strict 零错误/警告；两种屏宽/主题截图已检查。实际 Windows 受管管道/Job、SQLite 和浏览器用于验证，系统 Wayland 许可为隔离注入；没有真实系统弹窗、键鼠输入、Linux 实机或全量结论。UI 首先随并行提交 `49899757` 落地，专项固定该提交及本批后端，后续并行资源版本不冒充本次截图来源。见[父进程与业务证据](evidence/v2-control-owner.json)。

后续实际输入必须另有逐次业务许可、输入释放/人工介入证明、目标关联和不确定结果结算。InputCapture 是由合成器触发并改变输入路由的捕获机制，不能当成通用被动物理输入监听器。缺失暂停证明时保持输入不可用；窗口帧共享不能提供这份证明。

协议依据：[RemoteDesktop](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)、[Request](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Request.html)、[Session](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Session.html) 和 [InputCapture](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.InputCapture.html)。

## 独立EIS描述符与EI核心握手（2026-11-18）

`ControlSession.prepare_eis`仅在原独立系统许可成功、设备种类仍精确相等后允许一次ConnectToEIS。固定总线FD方法复用原serial/owner/返回签名及唯一SCM_RIGHTS关联、非继承Unix流验证和累计额度；只扩RemoteDesktop/ConnectToEIS固定方法，不开放Notify*。旧ScreenCast/OpenPipeWireRemote正文按固定参数代换的AST相同，读取调用方另验。调用者取消不会丢失原任务或再连接，迟到FD先登记，再验证同一Portal/logind会话。

`linux_eis.py`按EI原生字节序处理16字节头、有界对齐帧和部分收发，单帧4096字节、双向各累计64KiB、接收128消息不重置。额外SCM_RIGHTS全部关闭拒绝，不把未来键图FD偷偷接受到核心阶段。首次事件必须是有效handshake_version；选v1、显式sender2、固定产品名，仅声明connection/callback/pingpong三个v1接口后finish。新connection对象必须处于递增server ID空间且版本1，初始serial不猜固定值；实际sync要求递增client回调及done0，server ping须新ID/版本1，回pong0。握手前后复核原Portal，失败/取消/到期锁存，不重连或切回Notify。

真实握手3秒、活跃同步1秒，所有预算均由当前任务持有；close不抢先释放仍忙的任务/连接。socket包装器detach失败保留原FD，成功后再关闭额外rights与原描述符，失败回执不等同clean。系统会话关闭先结清同一EI任务和实际FD，再清原Session/订阅；EI失效清除设备许可，外部SessionInspector仍由原所有者关闭。

默认核心档为内部原生连接API，固定生产控制worker尚未调用；该核心档没有声明seat/device或发送输入，后续设备档见下一节。它不证明输入释放、物理介入、目标关联、映射区域、应用结果或业务动作授权，所有公开`input_authorized`仍为false。下一继续设备/区域/键图、pause/remove、释放与人工介入证明，再接原逐次业务许可；不能把核心握手完成等同Wayland控制完成。

54新与128直接调用方/合同共182专项通过（22.09秒）；3修改运行文件strict零错误/警告，374只为登记范围。初次冻结入口和调用方路径错误均未执行测试，原日志保留；46初始新用例通过伴随缓存rename警告，关闭复核后新增8项，最终组无警告。所有EI/Portal/输入为注入，使用真实隔离FD与业务SQLite；无真实Wayland/EIS/设备/账号/模型/72h或全量验收。见[本批证据](evidence/v2-wayland-eis.json)。

协议依据：[RemoteDesktop连接生命周期](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)、[EI线格式](https://libinput.pages.freedesktop.org/libei/doc/overview/index.html)、[类型/对象ID](https://libinput.pages.freedesktop.org/libei/doc/types/index.html)、[握手](https://libinput.pages.freedesktop.org/libei/interfaces/ei_handshake/index.html)和[核心同步](https://libinput.pages.freedesktop.org/libei/interfaces/ei_connection/index.html)。

## 显式EI设备档、键图与生命周期观察（2026-11-18）

`ControlSession.prepare_eis(with_devices=True)`沿原独立设备许可、单次连接和实际归属选择`EisDevices`。它先只读列出当前EI seat，不按第一个、名称或logind标签猜测选择；调用方须显式提供当前连接内准确seat ID。仅声明系统原请求允许的键盘/指针接口，以实际服务器分配的非零、不重叠稀疏能力掩码做一次bind，不把1/2业务位当作EI能力位。默认核心档及所有公开IPC/HTTP/业务许可保持，生产worker尚未调用设备档。

seat v1和虚拟device v1/v2元数据均有界核验，服务端对象ID递增不重用；最多4个生命周期seat/设备、每设备16区域。区域逻辑边界、有限正scale及v2 mapping ID按原值保留，不推断屏幕像素/原生窗口。键盘、相对/绝对指针、按钮和滚动接口只可来自已绑定能力；未声明/重复接口、物理receiver设备、无区域绝对设备和done后元数据均拒绝。新设备默认暂停，实际resume/pause、设备/接口/seat移除、修饰通知更新不可变观察及代次，旧观察即使在pause/resume后状态看似恢复也不能复活。只读观察允许记录暂停或缺可选键图的设备，不因此允许输入。

`ReceivedFds`旧默认队列/单次仍为1，EI绑定键盘窗口才允许最多4个FIFO描述符批次，原寿命总数4不变。支持批次中先到rights、后到设备/键图帧；每份键图只按FD顺序领取。非键盘窗口额外FD拒绝关闭，孤立FD不用于其他对象，原3秒预算结束后拒绝观察并关闭。原核心4096帧、64KiB双向累计、128消息预算不变，不重置。

`linux_eis_keymap.py`将实际读取移到原executor Future，单键图声明/真实文件均不超过1MiB；使用有界pread保持共享offset，读取前后复核文件身份/尺寸/时间戳，核验NUL尾、无内部NUL及UTF-8，再保留原字节/哈希。没有mmap截断SIGBUS路径，但这只是字节复制/校验，不证明XKB语法或键码语义。调用者或实际任务取消仍等待原读取完成，FD不能在线程下被提前关闭；待结清close保留键图、EI连接及原Session，实际完成后才关闭并清正文。

59新增设备/键图与182原核心/总线/Portal/宿主/业务/架构合同分阶段241不同节点通过；主237节点后运行源码未变，仅加强不同键图FIFO与新增4节点，补查5节点通过，没有重复整组。精确1MiB、四份不同键图批次、16区域、部分读取、截断/变更/失败、原线程/FD取消及孤立FD真实3秒已验。5修改运行文件strict零错误/警告，376只为登记范围；首37通过与1项bytearray类型诊断、原源码保留，未放宽断言或缩短期限。全部EI/输入注入，真实隔离文件/FD与SQLite；Windows的pread端口明确注入，不是Linux原生证据。见[设备专项](evidence/v2-wayland-eis-devices.json)。

仍需实现实际XKB解释、分帧按下/抬起与释放结算、合成器物理介入合同、目标/区域关联及逐次业务许可/应用后验。EI设备pause不是通用物理事件监听保证，逻辑区域/相同mapping ID也不能单独证明某个原生窗口；本批没有发start_emulating、key/button/motion/frame。整个V2仍开发，真实平台/账号/模型/设备/72h和全量验收均待完成。协议依据：[seat能力](https://libinput.pages.freedesktop.org/libei/interfaces/ei_seat/index.html)、[设备/区域/生命周期](https://libinput.pages.freedesktop.org/libei/interfaces/ei_device/index.html)、[键图与修饰](https://libinput.pages.freedesktop.org/libei/interfaces/ei_keyboard/index.html)。
