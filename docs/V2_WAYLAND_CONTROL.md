# Wayland 独立控制许可开发

本批完成独立 RemoteDesktop 原生许可和固定 worker 基础。父进程后端、业务审计、HTTP/工作台入口及真实输入暂停/派发仍在开发，当前没有开放 Wayland 输入。系统设备许可与业务动作授权分别记录；所有 worker 回执的 `input_authorized` 始终为 false。

`domain/desktop_control.py` 使用独立 `omni-desktop-control-1` 协议。打开请求必须带交互输入目的、显式同意、键盘/鼠标位图及 Agent/view/boot/grant 的精确 UUID 绑定。仅接受 open/permit/close；窗口共享协议、恢复令牌和任何输入命令都不能作为控制请求。系统许可阶段名为 `system_authorized`，不使用共享会话的 `active` 冒充动作权限。

`linux_remote_desktop.py` 在独立的当前 UID 用户总线连接核验 Portal 唯一服务进程、活动未锁屏的本地 Wayland 会话及 RemoteDesktop>=2。明确请求键盘、鼠标或两者，不依赖“默认全部设备”。新建自己的随机 Session，经 RemoteDesktop.CreateSession/SelectDevices/Start 获取系统同意；persist_mode=0，不使用恢复令牌、剪贴板、屏幕源或 EIS。返回设备种类必须与请求相同，部分/额外设备、剪贴板开启、非空屏幕流、恢复令牌或未知扩大字段均拒绝。

ScreenCast 与 RemoteDesktop 各自持有新会话、匹配订阅和关闭义务。共享方法只新增固定子类接口选择，原 ScreenCast 路径不改变。控制适配器允许的请求方法只有三个许可方法，不能通过通用请求机制调用 Notify*；打开 PipeWire 被明确拒绝。系统许可本身无法发送按键、鼠标或像素。

固定 `workers/desktop_control.py` 验证初始控制协议后才构造原生对象。原生元数据准备成功并发 prepared 后，才接受一次同绑定 permit，再打开系统选择器。提前、重复、跨绑定、未知命令、EOF 或父进程关闭都会终止；初始帧不能许可系统弹窗。最多三个输入帧、262144 字节、150 秒进程寿命；启动与 permit 等待各 5 秒，选择器 90 秒，心跳/输出有界。状态严格迁移，干净关闭回执必须在 Session、未完成 Request、订阅和总线实际关闭后发出；失败或超时不能声称 clean。

原生会话持续核对 Portal owner/创建身份、logind 会话/锁屏和 Closed 通知，变化或到期清除设备许可且不复活。选择器仍等待时收到关闭，会取消等待并关闭预定 Request/Session；未自动重放 Start。原生对象、匹配规则与管道清理失败会保留失败结果，父进程实际回收归属是下一开发步骤。

41 项新增离线原生协议/worker 回归、39 项窗口共享调用方及 2 项类型合同，共 82 个不同专项已通过。新增分离会话用例曾漏导入，修正测试后指定节点通过，原失败报告保留；4 个修改后端文件 strict 零错误/警告。没有真实 Wayland、系统选择器、EIS、输入或全量验收。见[专项证据](evidence/v2-remote-desktop.json)。

后续先接父进程的受管进程、迟到创建和关闭归属，再接业务持久审计与显式用户入口。实际输入必须另有逐次业务许可、输入释放/人工介入证明、目标关联和不确定结果结算。InputCapture 是由合成器触发并改变输入路由的捕获机制，不能当成通用被动物理输入监听器。缺失暂停证明时保持输入不可用；窗口帧共享不能提供这份证明。

协议依据：[RemoteDesktop](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html)、[Request](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Request.html)、[Session](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Session.html) 和 [InputCapture](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.InputCapture.html)。
