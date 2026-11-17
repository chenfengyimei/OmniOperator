# Wayland 独立控制许可开发

独立 RemoteDesktop 原生许可、固定 worker、父进程归属、持久审计和 HTTP/工作台入口已经接通。真实输入暂停、目标证明与实际派发仍在开发，当前没有开放 Wayland 输入。系统设备许可与业务动作授权分别记录；所有回执的 `input_authorized` 始终为 false。

`domain/desktop_control.py` 使用独立 `omni-desktop-control-1` 协议。打开请求必须带交互输入目的、显式同意、键盘/鼠标位图及 Agent/view/boot/grant 的精确 UUID 绑定。仅接受 open/permit/close；窗口共享协议、恢复令牌和任何输入命令都不能作为控制请求。系统许可阶段名为 `system_authorized`，不使用共享会话的 `active` 冒充动作权限。

`linux_remote_desktop.py` 在独立的当前 UID 用户总线连接核验 Portal 唯一服务进程、活动未锁屏的本地 Wayland 会话及 RemoteDesktop>=2。明确请求键盘、鼠标或两者，不依赖“默认全部设备”。新建自己的随机 Session，经 RemoteDesktop.CreateSession/SelectDevices/Start 获取系统同意；persist_mode=0，不使用恢复令牌、剪贴板、屏幕源或 EIS。返回设备种类必须与请求相同，部分/额外设备、剪贴板开启、非空屏幕流、恢复令牌或未知扩大字段均拒绝。

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
