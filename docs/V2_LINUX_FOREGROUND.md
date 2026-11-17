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

协议依据：[X11 Protocol](https://xorg.freedesktop.org/archive/X11R7.7/doc/xproto/x11protocol.html) 的 GetInputFocus、QueryTree、ChangeWindowAttributes、GetKeyboardMapping、GetModifierMapping、QueryPointer 和 MappingNotify；[AT-SPI Accessible](https://gnome.pages.gitlab.gnome.org/at-spi2-core/devel-docs/doc-org.a11y.atspi.Accessible.html) 与 [Component](https://raw.githubusercontent.com/GNOME/at-spi2-core/main/xml/Component.xml)。
