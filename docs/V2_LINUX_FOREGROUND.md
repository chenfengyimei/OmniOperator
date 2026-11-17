# Linux 显式前台控制开发

整个 Linux 前台控制仍在开发，未开放通用键盘、鼠标或 AT-SPI/X11 自动窗口映射。本批补所选连续 X11 窗口与实际键盘焦点的原生只读证明，作为后续输入派发的前置条件；没有改变工作台可执行动作。

`platform/linux_foreground.py` 的 `WindowFocus` 持有外部 `WindowLeases` 和自己拥有的独立 `X11Input` 连接。先核对原窗口持续代次，再建立已有物理输入释放/拓扑监测和 250ms 静默窗口。两条连接必须来自同一个 SessionInspector，具有相同 Xorg 进程、socket inode、screen/display 及 root。另在监测连接核验 XRes1.2，实际焦点接收客户端的 PID、进程创建身份、可执行文件和 UID 必须属于所选窗口。

同一 PID 不能替代窗口关系：GetInputFocus 的实际窗口必须等于所选窗口，或沿 QueryTree 的有界父链到达所选窗口。最多 16 层，每次最多 4096 个子项；root、循环、链外同应用窗口、远程客户端、窗口管理器代理或损坏响应拒绝。控件坐标、窗口标题和应用自报 PID 不参与这个判定。

原输入守卫订阅焦点窗口的 FocusChange/StructureNotify；新增一次性的祖先链订阅只加 StructureNotify，不覆盖焦点窗口已有订阅。订阅前登记允许的窗口，屏障期间接到祖先重挂载/销毁等通知也会锁存输入变化。之后每次检查都重验连续窗口身份、物理输入/焦点、服务器和完整父链，并再做输入屏障。检查失败后即使恢复为相同元数据也不能复活证明；准备失败不能作为已准备证明使用。关闭只回收自己拥有的监测连接，不关闭外部窗口租约；失败继续返回未完成。

18 项新增离线回归覆盖实际二进制协议的直接/子孙焦点、同 PID 链外窗口、循环/深度/根/坏长度、不同客户端、服务器/会话不符、订阅中重挂载、未聚焦祖先变化、输入按住、进程复用、窗口代次变化和失败锁存。原输入、语义动作、持续租约和架构调用方另作专项。均使用注入 Xorg/logind，不发送真实输入，也没有 Linux 原生验收结论。见[证据](evidence/v2-linux-foreground-proof.json)。

后续仍需把控件身份与所选窗口的关联、逐次业务授权、Unicode/键映射、鼠标遮挡和派发确定性接入实际 worker。AT-SPI 的对象树与 Component 坐标接口不能单独证明 X11 窗口句柄，不能因为本批焦点证明通过而启用通用输入。Wayland 控制另走独立 RemoteDesktop 系统许可，不能复用 ScreenCast 读取授权。

协议依据：[X11 Protocol](https://xorg.freedesktop.org/archive/X11R7.7/doc/xproto/x11protocol.html) 的 GetInputFocus、QueryTree、ChangeWindowAttributes；[AT-SPI Accessible](https://gnome.pages.gitlab.gnome.org/at-spi2-core/devel-docs/doc-org.a11y.atspi.Accessible.html) 与 [Component](https://raw.githubusercontent.com/GNOME/at-spi2-core/main/xml/Component.xml)。
