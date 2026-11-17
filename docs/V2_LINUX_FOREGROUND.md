# Linux 显式前台控制开发

整个 Linux 通用前台控制仍在开发，已接通人工聚焦的 AT-SPI 编辑器末尾 Unicode 追加。通用键盘、鼠标及 AT-SPI/X11 自动窗口映射仍未开放。此前补所选连续 X11 窗口与实际键盘焦点的原生只读证明，供后续全局输入派发使用；当前文本追加直接寻址 AT-SPI 对象，不按 PID 或几何位置把它映射到 X11 窗口。

`platform/linux_foreground.py` 的 `WindowFocus` 持有外部 `WindowLeases` 和自己拥有的独立 `X11Input` 连接。先核对原窗口持续代次，再建立已有物理输入释放/拓扑监测和 250ms 静默窗口。两条连接必须来自同一个 SessionInspector，具有相同 Xorg 进程、socket inode、screen/display 及 root。另在监测连接核验 XRes1.2，实际焦点接收客户端的 PID、进程创建身份、可执行文件和 UID 必须属于所选窗口。

同一 PID 不能替代窗口关系：GetInputFocus 的实际窗口必须等于所选窗口，或沿 QueryTree 的有界父链到达所选窗口。最多 16 层，每次最多 4096 个子项；root、循环、链外同应用窗口、远程客户端、窗口管理器代理或损坏响应拒绝。控件坐标、窗口标题和应用自报 PID 不参与这个判定。

原输入守卫订阅焦点窗口的 FocusChange/StructureNotify；新增一次性的祖先链订阅只加 StructureNotify，不覆盖焦点窗口已有订阅。订阅前登记允许的窗口，屏障期间接到祖先重挂载/销毁等通知也会锁存输入变化。之后每次检查都重验连续窗口身份、物理输入/焦点、服务器和完整父链，并再做输入屏障。检查失败后即使恢复为相同元数据也不能复活证明；准备失败不能作为已准备证明使用。关闭只回收自己拥有的监测连接，不关闭外部窗口租约；失败继续返回未完成。

18 项新增离线回归覆盖实际二进制协议的直接/子孙焦点、同 PID 链外窗口、循环/深度/根/坏长度、不同客户端、服务器/会话不符、订阅中重挂载、未聚焦祖先变化、输入按住、进程复用、窗口代次变化和失败锁存。原输入、语义动作、持续租约和架构调用方另作专项。均使用注入 Xorg/logind，不发送真实输入，也没有 Linux 原生验收结论。见[证据](evidence/v2-linux-foreground-proof.json)。

后续仍需把控件身份与所选窗口的关联、逐次业务授权、Unicode/键映射、鼠标遮挡和派发确定性接入实际 worker。AT-SPI 的对象树与 Component 坐标接口不能单独证明 X11 窗口句柄，不能因为本批焦点证明通过而启用通用输入。Wayland 控制另走独立 RemoteDesktop 系统许可，不能复用 ScreenCast 读取授权。

## 已接通的受限文本追加

`linux_text.py` 只向选定 AT-SPI 非密码可编辑对象执行一次 `EditableText.InsertText`。观察时须能完整读取最多 511 字符原文、核验 CaretOffset 位于末尾及 GetNSelections 为零；上下文进入原节点 actions 指纹，支持的节点才提供 `type_text`。不支持这些可选方法的提供者继续保留原语义动作。

执行沿用 DesktopActions 的人工单动作审批、观察绑定、单次消费和资源租约。固定 `linux_action` worker 对前台请求使用既有 grant/run/operation UUID 绑定的 ready/permit，初始帧不许可写入。最多等待 10 秒由用户手工聚焦目标窗口和控件；活动 AT-SPI 窗口和聚焦控件都需成立，再经过原 X11 物理输入/拓扑及 250ms 静默守卫。父进程重新核验业务权限后才发 permit，随后完整重读节点/原文/光标/选区与焦点，再执行一次插入。

每次最多 128 字符，原文加结果最多 512 字符。插入位置为 Unicode 字符偏移，长度参数为 UTF-8 字节数，保留中文和补充平面字符。没有键盘布局改写、剪贴板、焦点获取、删除选区或补发。提供者可能保留原光标或将其移到结果末尾，两者可接受；完整原文前缀和追加结果、对象/祖先/名称/动作及输入守卫必须匹配，才能报告 `text_appended`。写入前失败为 denied，派发后回执丢失/回读不符/用户介入为 unknown，均不自动重放。

Linux 桌面协议只放行 AT-SPI 的这一前台动作；`press_key`、`click`、X11/Portal 采集目标和 Wayland 均不能借此获得全局输入。能力探测单独核验 X11 输入守卫，清理成功后才报告这一受限能力；探测不 arm、不写。通用键鼠及原生窗口关联继续开发，不能把受限文本追加视为整个 G3 完成。

本批 25 项原生注入/通信/隔离 SQLite 回归、2 个浏览器场景、114 个当前调用方节点及 2 项类型合同守卫，共 143 个不同专项已通过。首份调用方报告原未参数化的浏览器节点由当前 `set_text/type_text` 两场景替代，不重复计数。修改 6 后端文件 strict 零错误/警告；没有真实 Linux 输入或全量。见[文本专项证据](evidence/v2-linux-text.json)。

文本协议依据：[EditableText.InsertText](https://raw.githubusercontent.com/GNOME/at-spi2-core/main/xml/EditableText.xml) 和 [Text](https://raw.githubusercontent.com/GNOME/at-spi2-core/main/xml/Text.xml) 的 CaretOffset/GetNSelections。真实 GTK/Qt 的焦点标志、Unicode 位置和插入后光标行为仍需 Linux 正式验收。

## 当前服务器的核心按键映射预检

`linux_keyboard.py` 已接到 `WindowFocus.prepare_key/verify_key`：在同一持续窗口、实际焦点客户端及输入守卫成立时，才从监测连接读取本次 Xorg 的完整核心键盘/修饰键映射。连接 setup 保留并验证真实 min/max keycode，GetKeyboardMapping 每键最多 16 个符号、GetModifierMapping 每组最多 32 个条目；只接受领域既有 11 个特殊键，不使用固定美式 keycode、字符猜测、组合键或布局改写。

目标符号须只出现在一个物理 keycode 行，首项匹配，其余项只能为同符号或 NoSymbol；仅修饰层出现、行内混合符号、多个候选或该 keycode 本身作为修饰键均拒绝。修饰键表须完全有界且有效，QueryPointer 只检查逻辑修饰键/按键掩码为零和同一 root，不保存坐标。物理释放不能替代 CapsLock 等逻辑锁存状态。

准备时以焦点/输入屏障夹住两次完整读取，之后使用前再次核对全表指纹。其他键或修饰键的变化、MappingNotify、物理输入、焦点或连续窗口身份变化都会使本次证明锁存失效；恢复成旧元数据不能恢复旧计划。映射对象不拥有外部连接，关闭仍由原 WindowFocus 回收自己的监测连接，并保留外部窗口租约。

这是只读核心映射预检。X11 核心规范明确映射由客户端解释，不能据此证明应用的 XKB action、实际事件路由或按键已处理。因此本批没有开放 press_key、Unicode 键盘生成或 XTest，不把这层预检当作通用输入完成。控件与原生窗口的可靠关联、XKB 及派发/介入证明仍需继续实现。

31 项新回归和固定副本 150 项受影响调用方通过，修改 3 后端文件 strict 零错误/警告。受限环境曾在 Windows asyncio 建立内部 socketpair 的 accept 阶段停住，尚未进入被测逻辑；保留诊断栈后同一离线专项在获准环境执行通过，未改测试/生产超时、网络闸门或断言。没有真实 Linux 输入或全量，见[映射证据](evidence/v2-linux-keyboard.json)。

## 同连接 XKB 服务器预检

核心按键预检现要求同一已核验输入连接上的 XKB 1.0。`linux_xkb.py` 协商 XKEYBOARD，固定使用核心键盘，不创建/切换设备；先安装事件解析器，再订阅键盘更换、映射、状态、控制和兼容映射的完整变化。任何这些通知都会使原证明失效，既有 256 事件/1 MiB 总接收上界保持，不记录输入内容。

服务器状态、控制及按键描述的 device ID 必须一致。读取 effective/base/latched/locked、compatibility/lookup/grab 修饰状态、组状态与按钮，要求均释放/无变换；不能仅凭核心 QueryKeymap 的物理释放推断。拒绝 SlowKeys、BounceKeys、StickyKeys、MouseKeys、Overlay 及未知控制，原设置不改动。控制读取精确 92 字节，前后重新核对有意义字段，协议填充不进指纹。

GetMap 只请求该候选 keycode 的 symbols/actions/behaviors/explicit/真实与虚拟修饰映射，最多 1024 字节、64 个符号。精确核对设备、键码范围、请求部件、各返回范围和长度；所有实际 XKB 符号位置必须为同一已审批特殊键，不推测客户端的键类型或组翻译。只接受隐式零动作或显式 SA_NoAction；重定向、切组、控制变化等动作、非默认行为及修饰绑定均拒绝。读取前后再次核对状态/控制，并与原双读完整指纹和焦点/输入屏障组合；恢复旧值不能复活旧计划。

这补齐服务器侧检查，仍不证明某应用如何处理按键，也不发送 XTest。窗口/节点关联、自身输入区分、实际派发与释放仍在开发。专项和类型结果见[XKB证据](evidence/v2-linux-xkb.json)，没有真实 Linux、原生输入或全量结论。

协议依据：[X11 Protocol](https://xorg.freedesktop.org/archive/X11R7.7/doc/xproto/x11protocol.html) 的 GetInputFocus、QueryTree、ChangeWindowAttributes、GetKeyboardMapping、GetModifierMapping、QueryPointer 和 MappingNotify；[XKB协议](https://xorg.freedesktop.org/releases/X11R7.7/doc/kbproto/xkbproto.html)与[XCB生成布局](https://xcb.freedesktop.org/manual/xkb_8h_source.html)；[AT-SPI Accessible](https://gnome.pages.gitlab.gnome.org/at-spi2-core/devel-docs/doc-org.a11y.atspi.Accessible.html) 与 [Component](https://raw.githubusercontent.com/GNOME/at-spi2-core/main/xml/Component.xml)。

## 服务器虚拟输入设备身份

`linux_xtest.py`的`XTestSources`借用已arm的X11Input连接，由`WindowFocus.prepare_sources/verify_sources`夹在持续窗口/焦点核验中。只协商XTEST>=2.2、读取已存在的`XTEST Device`原子和XIGetProperty，不创建属性、禁用设备、修改挂接或发送FakeInput。仅有版本协商不会取得业务输入权。

原有有界设备解析现可返回设备ID、类别、挂接、启用和类别集，另保留原摘要接口；名字、按键/坐标内容仍不外传或存储。逐一查询全部最多64设备，包括禁用、浮动及主设备。属性缺失只表示该设备不是候选；属性存在时必须是完整INTEGER/8位/单项真值，不允许剩余字节、额外数据或类型猜测。两项候选必须为唯一启用slave pointer/keyboard，正确挂接唯一主设备对，且具备相应Button/Key类别。名称不能取代属性；额外伪标记、主设备标记、禁用/脱离候选、缺少类别均拒绝。

依据Xorg的[虚拟设备实现](https://raw.githubusercontent.com/XQuartz/xorg-server/master/Xext/xtest.c)，真正XTEST设备具有不可删除/改写标记，核心虚拟对不可删除；读取全部设备并拒绝重复，避免把另一个伪标记设备选作真实候选。协议格式来自[XCB XIGetProperty布局](https://xcb.freedesktop.org/manual/xinput_8h_source.html)。准备时双读并夹住原守卫，使用前再次重验；XI_PropertyEvent现在也被订阅并锁存失效，恢复旧值不能复活旧证明。协议填充不参与候选判断。

全部XTEST客户端共享同一虚拟设备对。因此来源身份本身不能证明原始事件由本动作产生。未安排本连接动作时，原始输入仍全部使守卫失效；没有按名称或共享来源豁免。下一节的内部派发器另外约束实际请求序号、方向和精确事件数量，不把这层身份预检当作应用已处理按键的证明。

24项新增回归及207项现有输入、焦点、键映射/XKB、文本/语义/桌面与架构/类型合同专项，共231不同节点通过；3个修改后端文件strict零错误/警告。结果来自固定源码及注入Xorg/logind，不代表Linux原生、真实输入或全量通过，见[来源专项证据](evidence/v2-linux-xtest.json)。

## 内部成对按键派发与释放责任

`WindowFocus.key_packet`在已准备的特殊键与虚拟来源上重新核验窗口、完整键映射和来源身份，创建`linux_keystroke.KeyPacket`。派发前序号必须保持不变、无旧租约或变化；同连接一次性安排两个36字节XTEST FakeInput请求，仅含既有特殊键code，按下后抬起、零延迟/坐标/core device，不写布局或修饰键。一次对象只能派发一次。当前业务worker、domain/IPC及UI未接入这一路，不因此获得通用按键能力。

Xorg的[XTEST请求处理](https://raw.githubusercontent.com/XQuartz/xorg-server/master/Xext/xtest.c)同步处理生成事件；[事件写出](https://raw.githubusercontent.com/XQuartz/xorg-server/master/dix/events.c)按接收客户端当前请求序号标记。结合[slave先于master的事件路径](https://raw.githubusercontent.com/XQuartz/xorg-server/master/mi/mieq.c)和[RawEvent布局](https://raw.githubusercontent.com/XQuartz/xorg-server/master/dix/eventconvert.c)，内部守卫只消费准确四条：按下请求的slave/master RawKeyPress、抬起请求的slave/master RawKeyRelease。同一审批code、来源ID、设备ID、序号、方向和顺序必须完全一致；每对时间相同，flags为零，最多8字mask且全零，不允许额外valuator内容。共享来源、额外/缺失/乱序、物理来源、不同键或SendEvent不能通过；其余原守卫保持锁存失效。通知内容不落盘。

实际发送采用非阻塞socket.send返回的字节数，派发前登记连接所有的租约；未结清流禁止普通查询插入。回收时，按下请求不足36字节则关闭流丢弃该未完整请求；已经发满按下时，只补72字节中尚未发送的抬起后缀，不能重复按下或重新发送完整包。发送及释放回读各保留0.5秒上界。QueryKeymap精确回读40字节且全部键释放才结清，仍按住可在同一已对齐流再读，绝不再次派发。

取消等待保留实际释放/回读任务和同一连接。活跃发送期间关闭返回false，避免两个发送者；回读发送/读取超时或协议位置不明后保留未结清身份，不另建查询掩盖丢失回执。`X11Input.close`先结清该租约，未完成不能声称连接回收成功。内部emit完成只说明传输事件匹配和释放读回，不生成业务ok、也不证明应用处理了按键。

该次成对内核节点尚未处理合法slave/XKB通知，后续实现见下文。29新回归及232现有输入/来源/焦点/键映射/XKB、文本/语义/桌面、架构与类型合同，共261不同专项通过；首轮注入流缺少关闭状态的失败及原源码留档后修复。4个修改后端文件strict零错误/警告，354为当时登记范围，未整范围复验/全量。无真实Xorg连接、真实输入或Linux原生验收，见[成对内核证据](evidence/v2-linux-keypair.json)。

## Xorg切换前的完整映射比对

Xorg在slave切换时通过[CopyKeyClass/DeepCopyKeyboardClasses](https://raw.githubusercontent.com/XQuartz/xorg-server/master/Xi/exevents.c)复制键盘映射，随后发送NewKeyboardNotify和DeviceChanged。仅核对候选单键不能约束其他按键、类型和修饰映射。`WindowFocus.key_packet`现要求主键盘与已核验XTEST来源的完整GetMap八部件、控制和释放状态相同，才创建内部按键包。

`linux_xkb_map.py`解析最多65536字节返回数据，精确验证真实setup范围、设备、full部件、类型与各按键范围、符号/动作总数、稀疏项唯一递增键码和尾部完整性。规范化类型、KTMap/Preserve、全部符号及动作、非默认行为、16个虚拟修饰绑定、显式/真实/虚拟修饰项；仅排除响应序号/设备身份、文档填充和NoAction的未使用字节。其他动作描述保持完整，未见部件不能推断为空。该解析器只输出SHA，不解释键类型、不改布局或发送输入。格式与顺序依据[XKB GetMap服务器实现](https://raw.githubusercontent.com/XQuartz/xorg-server/master/xkb/xkb.c)和[XCB布局](https://xcb.freedesktop.org/manual/xkb_8h_source.html)。

同一XKB连接先订阅来源键盘，再依次读取主/从/主/从，期间分别夹住无锁存状态与完整GetControls，任何通知或不一致都拒绝；来源回复不覆盖原核心设备ID。`KeyboardMapping`绑定完整身份，主从后来共同变化也不能更新旧计划；最后再复核原候选、窗口与输入守卫。读取取消/失败使证明锁存失效，尚无派发/释放租约或按键字节。原受限AT-SPI文本路径不新增这些要求。

该次仅补齐GetMap/状态/控制核验；其余复制配置与合法通知由下一节接续，生产worker仍未开放通用按键。37项新回归、29项原成对派发及232项受影响调用方/合同，共298不同专项通过，修改4后端文件strict零错误/警告；没有整范围类型、全量、真实输入或Linux原生验收，见[专项证据](evidence/v2-linux-keymap-clone.json)。

## 完整复制配置与本次键盘切换

`linux_xkb_copy.py`补GetCompatMap的全部符号解释/组修饰、GetIndicatorMap的定义/物理掩码、GetNames的组件/类型/级别/键/别名/组等，以及GetGeometry的当前完整几何。几何包含字符串、属性、颜色、形状/轮廓/点、section/row/key、overlay及五种doodad，不以整块带填充的内存生成身份。所有解析均受原64KiB回复限制，精确计数、引用与尾部；仅排除协议填充和NoAction未用字节。没有KeyNames时忽略响应保留的请求范围，不能误读为实际负载。几何只请求当前描述（atom=0），不查找命名几何或加载配置；先证明组件名称存在。无当前几何的明确空响应可用。

上述身份与GetMap、状态、控制一同主/从/主/从四读并绑定旧证明。新增IndicatorMap/Names通知订阅；共同变化、不完整名称、损坏几何和来源不一致均拒绝。依据[Xorg配置复制](https://raw.githubusercontent.com/XQuartz/xorg-server/master/xkb/xkbUtils.c)、[原生回复写出](https://raw.githubusercontent.com/XQuartz/xorg-server/master/xkb/xkb.c)及[XKB结构定义](https://sources.debian.org/src/x11proto-kb/1.0.3-2/XKBproto.h)。

`linux_key_switch.py`读取现有完整设备拓扑，要求主/来源键盘均只有一个相同KeyClass（除sourceid），来源指向实际XTEST设备；预计算只将主KeyClass.sourceid改为该来源的唯一允许指纹。Xorg内部last.slave与lastSlave不同，不能由QueryDevice断言必有通知。若发生切换，必须在首条RawKeyPress前按顺序收到同一按下请求序号的NewKeyboardNotify和XI_DeviceChanged：旧新主设备、旧新键码范围、XKB major/SetMap minor、Keycodes及存在几何时的Geometry位、SlaveSwitch原因、单一来源KeyClass完整负载均须精确一致。重复、缺失、逆序、迟到和其他来源保持介入，未派发时没有通知豁免。原始事件仍精确四条。

释放屏障结清后，再以两次拓扑读取夹住原完整配置身份核对，随后检查全部键释放、已核验连接和原焦点。只有这些检查全部通过，才采用预计算指纹；没有切换时仍要求原指纹。其他设备/名称/挂接/类别变化不能随主键盘来源一并被接受，独立changed永不重置。派发后核验失败或取消使计划锁存失效，不重放按下；实际释放与外部窗口租约的归属保持原规则。依据[Xorg复制与通知顺序](https://raw.githubusercontent.com/XQuartz/xorg-server/master/Xi/exevents.c)和[NKN字段](https://raw.githubusercontent.com/XQuartz/xorg-server/master/xkb/xkbUtils.c)。

内部emit仍只说明传输、释放及复制配置核验，不能报告应用成功。业务worker/domain IPC尚未接通；原生窗口与语义节点关联、逐次permit、应用结果及鼠标/Wayland输入仍继续开发。固定副本相关专项及修改7后端文件strict结果见[本批证据](evidence/v2-linux-key-switch.json)，当前没有Linux原生、真实输入、全范围类型或全量结论。

## 显式提供方窗口绑定基础

`linux_window_binding.py`的`WindowBinding`从已经验证的AT-SPI顶层对象读取`Accessible.GetAttributes`，只接受有源码依据的原生窗口字段。当前配置为`toolkit=Avalonia`及`avalonia-window-handle`的规范十进制XID；属性表最多128项/16KiB，单值512字符，拒绝十六进制、符号、前导零、Unicode数字、root、溢出和未知提供方字段。该属性在[Avalonia原生实现PR](https://github.com/AvaloniaUI/Avalonia/pull/22687)中只由`RootAtSpiNode`且平台`HandleDescriptor=XID`时提供，已核对合并源码；未证明任意稳定版本或现有应用已提供。

提供方声明不单独授权。模块在同一已核验X11 SessionInspector下建立自己的`X11Desktop/LeaseWire`，对明确XID读取XRes实际客户端；PID、创建时间和可执行程序必须与AT-SPI总线进程证明全部一致，再创建独立连续窗口代次和`WindowFocus`。原生焦点须确在该窗口或其有界子孙内；另一应用句柄、同PID链外焦点、窗口管理器代理和Wayland均拒绝。描述的双读夹住连续窗口/焦点核验，变更后恢复原值不能复活旧绑定，调用方修改原target也不改变已捕获身份。没有标题、几何、任意`window-id`或ACTIVE位猜测。

绑定只拥有新建原生窗口连接及输入监测，不拥有外部AT-SPI/logind会话。准备失败或取消仍保留实际对象供关闭；关闭立即撤销自身和焦点证明，先结清原输入释放责任，失败时两条原生连接都保留，成功才关闭自己的窗口连接。原SessionInspector继续由外层worker关闭。

43项新回归以实际二进制XRes/QueryTree/结构事件和注入AT-SPI组合验证，另202项依赖、原文本/语义/桌面及架构/类型合同通过，共245不同专项；新增1文件strict零错误/警告。初轮字典Unknown类型和42通过报告/源码均保留，显式收窄并新增异进程所有者回归后验证当前源码。没有真实Linux、真实输入、全范围类型或全量，见[绑定证据](evidence/v2-linux-window-binding.json)。

这是显式提供方的关联基础，尚未接入业务press_key或语义节点结果核验。GTK、Qt的通用AT-SPI Component/Accessible接口当前不能由这些证据推导出原生句柄；保留现有直接寻址语义/文本能力，通用关联继续开发。后续将已验证的窗口与具体节点、逐次业务permit和真实应用后置条件接通，不能把本节或按键传输通过当作整个前台控制完成。
