# V2 类型检查（分模块严格化）

> **全仓严格类型尚未完成。** 本文件记录已纳入 strict 的模块、当前基线例外与推进规则；默认入口为V2（Windows离线候选），真实平台与整个V2仍待验收。

当前显式清单为 444 个文件，包含能力注册表、模型、渠道和平台提供者、技能与插件 HTTP 路由及共用输入基类。下方历史基线按当时的提交保留。


## 1. 门禁

```powershell
uv run --no-sync pyright --project pyrightconfig.strict.json
```

要求输出 `0 errors, 0 warnings`。该命令只检查已纳入 strict 的文件；普通 `uv run --no-sync pyright` 仍按默认模式检查改动文件。

strict 配置显式将 `agent/src` 加入导入根，隔离源码副本也解析该副本的 V2 模块，避免沿已有环境的 editable 安装误指向另一份源码。

## 2. 已纳入 strict 的模块

| 文件 | 纳入原因 |
| --- | --- |
| `agent/src/omni/domain/delegation.py` | 委派请求上限和实际工具快照继承 |
| `agent/src/omni/adapters/persistence/delegation_authority.py` | 父会话来源、租约和删除闭包 |
| `agent/src/omni/adapters/persistence/delegation_repository.py` | 子运行准入、家庭预算、持久关系与重启恢复 |
| `agent/src/omni/domain/todos.py` | 修订化计划数据、层级/字节限制和有界恢复通知 |
| `agent/src/omni/application/todos.py` | 运行授权内的计划控制，事务和调用方数据边界 |
| `agent/src/omni/adapters/persistence/todo_repository.py` | 同库计划快照、修订冲突和会话来源隔离 |
| `agent/src/omni/domain/turn_prompt.py` | 单个运行的系统前缀冻结与尾部状态/技能通知 |
| `agent/src/omni/domain/model_reply.py` | 所有普通模型回包在动作前统一核验身份、JSON与大小边界 |
| `agent/src/omni/application/desktop_control.py` | 独立控制同意的所有者视图、持久审计、后台归属和关闭结算 |
| `agent/src/omni/adapters/platform/desktop/desktop_control.py` | 独立控制父进程的实际归属、同绑定许可、共享额度和超量管道排空 |
| `agent/src/omni/domain/desktop_control.py` | 独立控制同意目的/设备种类、精确绑定与不授予输入的状态协议 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_xkbcommon.py` | libxkbcommon实际编译/键义与原线程及C对象归属 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_eis_key_packet.py` | EI分帧成对按键与原部分写入/同步/释放归属 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_eis_keyboard.py` | EI原观察/键图/修饰代次绑定的内部特殊键计划 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_eis_devices.py` | 显式EI seat绑定、设备/区域/修饰与代次后验 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_wayland_peer.py` | 原EI与显式Wayland端点同进程/私有FD归属 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_wayland_wire.py` | 只读Wayland分帧、消息/字节寿命预算与FD拒绝 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_wayland_windows.py` | wlroots窗口代次/原活动对象与关闭任务守卫 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_udev_input.py` | libudev当前seat完整设备目录及原内核/udev通知FD归属 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_evdev_input.py` | 原只读evdev FD身份、全键域/触点释放及非消费就绪核验 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_seat_input.py` | 内部seat输入守卫、原executor/监听/关闭任务归属 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_eis_keymap.py` | EI键图有界复制及实际线程/描述符归属 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_eis.py` | Portal独立EI核心握手、同步回读和实际描述符归属 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_remote_desktop.py` | 独立RemoteDesktop临时设备许可、会话身份/撤回和读取共享权限隔离 |
| `agent/src/omni/adapters/workers/desktop_control.py` | 独立控制许可worker、prepared后逐次permit、有界通信和实际关闭回执 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_text.py` | 人工聚焦AT-SPI文本末尾的单次Unicode插入、逐次许可和完整回读 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_foreground.py` | 连续窗口与原生焦点祖先链、服务器及实际接收客户端的只读证明 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_keyboard.py` | 当前服务器核心键盘/修饰键映射的有界读取与焦点绑定复核，不授予输入权 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_xkb.py` | 同连接XKB状态、控制、按键动作/行为预检及配置变化订阅，不派发输入 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_xkb_map.py` | 完整GetMap八部件的有界规范化身份，不解释按键、不改映射或派发 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_xkb_copy.py` | XKB兼容、名称、指示器定义与当前几何的有界身份，不写配置 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_key_switch.py` | 已拥有按键的精确XKB/XI切换证明和复制后验，不派发或修改配置 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_window_binding.py` | 提供方显式XID与实际XRes进程、持续窗口代次及原生焦点的绑定，不猜测或派发 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_node_point.py` | AT-SPI真实命中/物理边界与持续X11几何的双读关联 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_click_action.py` | 控件绑定的逐次鼠标许可、移动/成对按钮与实际状态后验 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_button_events.py` | RawButton与两级映射XKB指针通知的八步精确归属 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_button_packet.py` | 单次成对按钮派发、物理/逻辑释放回读与取消后的实际归属 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_motion_events.py` | 两条同请求RawMotion的源/主顺序、FP32.32及精确归属 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_motion_packet.py` | 单次移动字节账本、原查询归属及取消后实际结清 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_pointer_switch.py` | 指针来源类复制的预计算、通知/完整后验与最终采用 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_pointer_motion.py` | 真实轴/变换的有理逆映射、整数像素搜索及原始/处理后坐标计划，不发输入 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_pointer_layout.py` | 原生单root RandR资源/实际scanout、通知与坐标计划连续复核，不写布局 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_pointer_config.py` | 真实主从指针轴/滚动/标签、反馈与变换矩阵的有界规范化和变化失效，不派发输入 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_pointer_actions.py` | 同连接主从XKB按钮动作与释放状态/控制预检、规范化无动作和变化订阅，不派发输入 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_button_mapping.py` | 原生虚拟鼠标/主设备两级按钮映射与变化订阅，只读且不授予点击 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_pointer_target.py` | 同连接物理窗口点的Xorg形状命中/遮挡路径、接收客户端与结构变化预检，不移动或点击 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_focus_navigation.py` | 完整有界AT-SPI焦点树双读与同窗口导航后验 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_key_action.py` | 手工聚焦节点的逐次许可、原生按键包与明确文本/光标后验，不重放或假报成功 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_xtest.py` | Xorg虚拟输入设备的同连接只读身份预检，不豁免事件或派发输入 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_keystroke.py` | 同连接单键成对字节派发与实际释放责任；业务目标/许可接线待完成 |
| `agent/src/omni/domain/linux_window_leases.py` | 持续窗口身份的精确有界私有 IPC 与目标字段绑定 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_window_leases.py` | 同连接生命周期订阅、窗口代次及读取前后屏障 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_lease_client.py` | 持续只读辅助进程、串行 RPC、有界通信及真实回收归属 |
| `agent/src/omni/adapters/workers/linux_window_lease.py` | Linux 持续窗口证明的固定原生辅助进程与限额 |
| `agent/src/omni/adapters/platform/runtime/workbench.py` | 仅显式浏览器启动、本次进程与HMAC就绪证明、探测线程归属 |
| `agent/src/omni/adapters/platform/releases/dependency_bundle.py` | 本机依赖包清单/哈希与平台核验、有界流式捕获和仅本地安装输入 |
| `agent/src/omni/adapters/platform/releases/candidate_installer.py` | 独立离线候选安装、分离维护解释器与实例、逐步核验和失败保留 |
| `agent/src/omni/domain/action_review.py` | 未知写动作的观察复核/证据指纹纯规则 |
| `agent/src/omni/domain/agents.py` | 所有者空间的名称、UTF-8、容量和修订上界纯规则 |
| `agent/src/omni/domain/soak.py` | V2连续采样覆盖、所有者资源上限、注入/短跑拒绝与不可自动关闭的失败 |
| `agent/src/omni/domain/android.py` | Android 身份/观察/输入/权限请求纯校验 |
| `agent/src/omni/domain/android_unicode.py` | 输入助手配置/认证报文纯校验 |
| `agent/src/omni/domain/audit_retention.py` | 审计保留主体/冻结预览/计划纯校验 |
| `agent/src/omni/domain/bilibili.py` | WBI 签名/字幕与流地址/表单/提及区间纯校验 |
| `agent/src/omni/domain/capabilities.py` | 能力报告纯投影，需保持无 `Any` 泄漏 |
| `agent/src/omni/domain/credential_ipc.py` | 原生凭据帧解码/请求/响应纯校验 |
| `agent/src/omni/domain/credentials.py` | 凭据引用与权限指纹纯函数 |
| `agent/src/omni/domain/delivery.py` | 出站意图身份/受限文本/可信结果纯规则 |
| `agent/src/omni/domain/desktop.py` | 桌面目标/请求/能力/观察/响应帧纯校验 |
| `agent/src/omni/domain/desktop_actions.py` | 语义桌面动作/节点证据/结果纯校验（经公共桥） |
| `agent/src/omni/domain/desktop_portal.py` | 窗口共享授权/命令/事件/帧收据纯校验 |
| `agent/src/omni/domain/documents.py` | 源文本/分块/向量归一化与合并纯规则 |
| `agent/src/omni/domain/event_retention.py` | 事件保留冻结预览/计划纯校验 |
| `agent/src/omni/domain/extensions.py` | 扩展清单/schema/产物元数据/授权纯校验 |
| `agent/src/omni/domain/history.py` | 历史游标编码/解码与代际校验 |
| `agent/src/omni/domain/images.py` | 图像引用/策略/低细节计量纯规则 |
| `agent/src/omni/domain/installation.py` | 安装收据校验与 schema 一致性纯规则 |
| `agent/src/omni/domain/objects.py` | 对象类型/上限纯规则 |
| `agent/src/omni/domain/people.py` | 人物档案纯规则 |
| `agent/src/omni/domain/metering.py` | 请求形状/记忆提示/令牌估算纯函数 |
| `agent/src/omni/domain/qq.py` | OneBot 文本事件/连接/会话授权纯校验 |
| `agent/src/omni/domain/release_fence.py` | 启动互斥栅栏纯校验 |
| `agent/src/omni/domain/releases.py` | 发布/恢复计划、栅栏文件与路径分离纯校验（经公共桥） |
| `agent/src/omni/domain/routing.py` | 能力档位/路由策略/工具筛选纯规则 |
| `agent/src/omni/domain/run_recovery.py` | 安全接续的固定上限与参数指纹 |
| `agent/src/omni/domain/settings.py` | Agent 设置与运行配置纯校验 |
| `agent/src/omni/domain/scheduling.py` | 数值 cron/下次到期/到期选择纯规则 |
| `agent/src/omni/domain/local_vectors.py` | 本地向量包清单/协议纯校验 |
| `agent/src/omni/domain/memory_semantics.py` | 派生记忆索引/相关事实合并纯规则 |
| `agent/src/omni/domain/foreground.py` | 前台输入授权与有界输入纯校验 |
| `agent/src/omni/domain/maintenance.py` | 维护请求/认领/租约时间纯校验 |
| `agent/src/omni/domain/media.py` | 媒体源/字幕分段/帧表/转写载荷纯校验 |
| `agent/src/omni/domain/mcp.py` | MCP 传输身份/目录指纹/授权/结果文本纯校验 |
| `agent/src/omni/domain/memory_organization.py` | 精确事实整理请求/决定纯校验 |
| `agent/src/omni/domain/retention.py` | 执行正文保留策略/计划纯校验 |
| `agent/src/omni/domain/contracts.py` | 执行合同/状态机/动作结果纯类型 |
| `agent/src/omni/domain/configuration.py` | 配置载荷/名称/指纹纯校验 |
| `agent/src/omni/domain/context.py` | 对话压缩/批次/尾部预算纯规则 |
| `agent/src/omni/domain/cronjob.py` | 模型侧调度工具合同：可判定日程语法/有界请求与投影纯规则 |
| `agent/src/omni/domain/recovery_authority.py` | 恢复授权与源见证校验（经 service_deployment 公共桥） |
| `agent/src/omni/domain/reference_release.py` | 工件引用释放计划结构与指纹纯校验 |
| `agent/src/omni/domain/schema_upgrade.py` | 前向 schema 路由与双运行时清单校验（经公共桥） |
| `agent/src/omni/domain/model_stream.py` | 有界流式装配与工具调用解析纯规则 |
| `agent/src/omni/domain/service_control.py` | 服务启动/排空身份与 HMAC 消息纯规则（经 service_deployment 公共桥） |
| `agent/src/omni/domain/service_deployment.py` | 服务请求/运行时/计划/启动纯校验与公共桥 |
| `agent/src/omni/domain/service_cleanup.py` | 隔离注册清理计划纯校验（经公共桥） |
| `agent/src/omni/domain/service_exchange.py` | 同 schema 服务交换/管理员阶段/证据纯校验 |
| `agent/src/omni/domain/service_isolation.py` | 中断交换隔离计划与启动隔离校验（经公共桥） |
| `agent/src/omni/domain/service_operations.py` | 离线服务安装/卸载操作指纹纯规则 |
| `agent/src/omni/domain/service_activation.py` | 无人值守启动绑定/预留纯校验（经公共桥） |
| `agent/src/omni/domain/service_admin.py` | 管理员操作/阶段/请求/响应帧纯校验（经公共桥） |
| `agent/src/omni/domain/skills.py` | 技能元数据/文件路径/文档/补丁/清单纯规则 |
| `agent/src/omni/domain/terminal_review.py` | 终端未知结果核查与证据指纹纯规则 |
| `agent/src/omni/domain/terminals.py` | 终端打开/输入/缩放/关闭请求纯校验 |
| `agent/src/omni/application/android.py` | 安卓设备授权/观察/输入派发/IME 恢复用例（显式 Record/Payload、有界任务、租约与守卫收窄） |
| `agent/src/omni/application/routing.py` | 档位配置读取/保存用例（显式 Record） |
| `agent/src/omni/application/scheduling.py` | 单调度器保存/到期入队（UnitOfWork 回调与嵌套字段显式化） |
| `agent/src/omni/application/release_drain.py` | 发布排空绑定既有 once-only 启动控制 |
| `agent/src/omni/application/releases.py` | 发布计划/准备/恢复编排（显式 Record） |
| `agent/src/omni/application/exchange_recovery_steps.py` | 隔离/救援计划复用发布快照与栅栏 |
| `agent/src/omni/application/local_vectors.py` | 本地 ONNX 向量器选择/状态无 I/O 规则 |
| `agent/src/omni/application/maintenance.py` | 维护作业编排/心跳/结算（有界任务、显式 Record） |
| `agent/src/omni/application/mcp.py` | MCP 连接/目录授权/运行期调用编排（显式 Record、有界传输端口） |
| `agent/src/omni/application/media.py` | 媒体源/任务/转写/帧表编排（有界任务、显式 Record） |
| `agent/src/omni/application/images.py` | 图片准入/上下文选择/线材物化（有界任务、显式 Record） |
| `agent/src/omni/application/service_deployment.py` | 服务部署准备/验证/状态组合（公共桥 + 显式 Record） |
| `agent/src/omni/application/service_operations.py` | 服务安装/启动/移除操作编排（显式 Record） |
| `agent/src/omni/application/skills.py` | 技能导入/编辑/启用/回滚/披露编排（有界任务、显式 Record） |
| `agent/src/omni/application/service_exchange.py` | 交换计划/管理员阶段/应用编排（公共桥） |
| `agent/src/omni/application/model_output.py` | 有界实时预览（TypedDict 行、authority Mapping） |
| `agent/src/omni/application/model_calls.py` | 模型调用预检/授权/结算（显式 Record、流对象） |
| `agent/src/omni/application/people.py` | 人物档案用例（显式 Record/类型化调用） |
| `agent/src/omni/application/ports.py` | 应用层端口协议（显式 Record/任务/回调边界） |
| `agent/src/omni/application/qq.py` | QQ 监听/会话授权/健康状态（有界任务、显式 Record） |
| `agent/src/omni/application/memory_tools.py` | 记忆工具用例（UnitOfWork 回调显式类型） |
| `agent/src/omni/application/action_review.py` | 动作复核用例（有界任务集/Record 返回显式化） |
| `agent/src/omni/application/bilibili.py` | B 站读取/发布准备（有界任务、显式 Record） |
| `agent/src/omni/application/configuration.py` | 配置读取/创建/保存/预览/诊断（显式 Record） |
| `agent/src/omni/application/context.py` | 上下文压缩/摘要编排（显式 Record、边界窄化） |
| `agent/src/omni/application/operations.py` | 运维查询/诊断导出用例（有界任务集与关闭任务显式化） |
| `agent/src/omni/application/debugging.py` | 显式调试会话语义（维护任务显式 Task[None]） |
| `agent/src/omni/application/delivery.py` | 出站投递准入/发送/结算编排（有界任务、显式 Record） |
| `agent/src/omni/application/desktop.py` | 桌面观察视图/目标/验证/捕获编排（短租约、显式 Record） |
| `agent/src/omni/application/desktop_portal.py` | 窗口共享授权/会话/捕获编排（显式 Record、有界任务） |
| `agent/src/omni/application/terminal_review.py` | 终端核查用例（有界任务/Record 显式化） |
| `agent/src/omni/application/terminals.py` | 终端会话/输入/缩放/关闭编排（有界任务、单写者租约） |
| `agent/src/omni/application/objects.py` | 对象发布/访问用例（有界任务与分页返回显式化） |
| `agent/src/omni/application/memory_organization.py` | 记忆整理用例（有界任务/分页返回显式化） |
| `agent/src/omni/application/memory_semantics.py` | 记忆索引/混合召回/关联评审（有界任务与显式 Record） |
| `agent/src/omni/application/exchange_steps.py` | 服务交换步骤编排（公开部署校验桥/显式 Mapping） |
| `agent/src/omni/application/extensions.py` | 扩展安装/授权/运行期调用编排（显式 Record、有界任务集） |
| `agent/src/omni/application/credentials.py` | 凭据变更/审阅用例（UnitOfWork 回调与有界任务显式化） |
| `agent/src/omni/application/cronjob.py` | 模型侧调度工具薄封装（save/get/page/archive 复用，错误码映射与有界投影） |
| `agent/src/omni/application/desktop_actions.py` | 语义桌面动作用例（绑定请求显式化/有界任务与守卫回调） |
| `agent/src/omni/application/documents.py` | 源文档/索引/检索编排（有界任务、显式 Record） |
| `agent/src/omni/application/engine.py` | 运行准入/调度/工具执行/关闭编排（有界任务、显式 Record） |
| `agent/src/omni/application/exchange_recovery.py` | 交换隔离/救援计划（进度与授权类型化） |
| `agent/src/omni/application/service_cleanup.py` | 隔离定义审查/清理计划复用 |
| `agent/src/omni/application/service_control.py` | 服务启动/停止/复核编排（显式 Record） |
| `agent/src/omni/adapters/metrics.py` | 运行指标观测适配器 |
| `agent/src/omni/adapters/android.py` | 受管安卓 worker 与有界 PNG 加载（显式 Record/回调类型、暂存清单、进程回收） |
| `agent/src/omni/adapters/android_unicode.py` | 受管 Unicode/IME 事务流（显式 Record/回调类型、凭据端口、未知不重放） |
| `agent/src/omni/adapters/build_identity.py` | 构建身份常量与代码后缀 |
| `agent/src/omni/adapters/primitives.py` | 时钟/ID 原语 Protocol 与实现 |
| `agent/src/omni/adapters/timezones.py` | 时区数据库标识读取边界 |
| `agent/src/omni/adapters/platform/services/exchange_deployments.py` | 交换部署布局/路径校验（平台适配） |
| `agent/src/omni/adapters/platform/runtime/workspaces.py` | 工作区路径解析/校验（平台适配） |
| `agent/src/omni/adapters/persistence/credential_predicates.py` | 凭据谓词 SQL 片段（持久化适配） |
| `agent/src/omni/adapters/persistence/history_sequence.py` | 历史游标序列持久化辅助 |
| `agent/src/omni/adapters/persistence/maintenance.py` | 备份/校验/恢复与隔离（清单与报告 Record、条目路径与字节收窄、WAL 与指纹保全不变） |
| `agent/src/omni/adapters/persistence/maintenance_repository.py` | 维护准入/围栏尝试/业务结算（对齐端口 Record、Claim\|Record 双口径、boot/token/fence 校验不变、不执行不做 IO） |
| `agent/src/omni/adapters/persistence/migration_config.py` | 迁移配置读取边界 |
| `agent/src/omni/adapters/persistence/search_schema.py` | 检索 schema 定义 |
| `agent/src/omni/adapters/persistence/deployed_instance.py` | 已部署实例校验（只读，不迁移） |
| `agent/src/omni/adapters/persistence/delivery_predicates.py` | 出站待处理/未决谓词（持久化适配） |
| `agent/src/omni/adapters/persistence/skill_repository.py` | 不可变技能版本/审阅状态/逐运行绑定（对齐端口 Record、清单宽字典边界 cast、代次与容量收窄、不触包与文件系统） |
| `agent/src/omni/adapters/persistence/sqlite_access.py` | 离线 SQLite 连接（只读/备份，含 WAL） |
| `agent/src/omni/adapters/persistence/terminal_repository.py` | 终端意图事务与不可变回执（对齐端口 Record、修订号 int 收窄、不启原生进程不存正文） |
| `agent/src/omni/adapters/persistence/terminal_review_repository.py` | 终端 unknown 观测复核与可抹证据（对齐端口 Record、来源三元组显式、回执与原生生命周期不变、不授权重放） |
| `agent/src/omni/adapters/token_counter.py` | 本地保守 token 估算（不取模型元数据） |
| `agent/src/omni/adapters/images.py` | 图像编解码/校验（有界任务、Pillow 校验） |
| `agent/src/omni/adapters/platform/runtime/secrets.py` | OS 凭据库/外键加密 vault 适配 |
| `agent/src/omni/adapters/platform/services/macos_service_state.py` | launchd 定义/覆盖纯比较（stdlib-only） |
| `agent/src/omni/adapters/platform/services/service_handover.py` | 双阶段管理员交接（按序打开、原生锁释放） |
| `agent/src/omni/adapters/credentials.py` | 凭据读写线程池/失效与关闭（有界 Future 集） |
| `agent/src/omni/adapters/skill_packages.py` | 内存内技能 Markdown/ZIP 解析（不解压/不执行） |
| `agent/src/omni/adapters/platform/services/service_control_files.py` | 服务启停日志/一次性启动预留 |
| `agent/src/omni/adapters/platform/services/service_launch.py` | 一次性启动预留（实例锁下，不启动进程） |
| `agent/src/omni/adapters/persistence/migrations/versions/v0003.py` | 冻结迁移版本 v0003（人物/证据/记忆上下文） |
| `agent/src/omni/adapters/platform/runtime/processes.py` | 受管进程组与 Windows Job Object（不终止未校验 PID） |
| `agent/src/omni/adapters/platform/desktop/backend.py` | 桌面辅助进程（有界单帧、两阶段授权、不探测即构造） |
| `agent/src/omni/adapters/platform/services/service_query.py` | 只读系统服务查询（有界输出与保留进程所有权） |
| `agent/src/omni/adapters/persistence/audit_pruning.py` | 离线审计裁剪绑定（计划/应用/取消，备份校验归共享设施） |
| `agent/src/omni/adapters/persistence/event_pruning.py` | 离线事件裁剪绑定（计划/应用/取消，备份校验归共享设施） |
| `agent/src/omni/adapters/persistence/extension_repository.py` | 不可信插件版本/授权/执行绑定（对齐端口 Record、修订与代次 int 收窄、存活授权查询显式 Select、不执行插件代码） |
| `agent/src/omni/adapters/workers/client.py` | 有界 JSON IPC 工作进程客户端（能力清单与宿主回调预算） |
| `agent/src/omni/adapters/platform/services/exchange_files.py` | 服务交换见证与维护预留（公共桥，不触库/不原生调用） |
| `agent/src/omni/adapters/platform/services/service_cleanup_files.py` | 服务清理日志与双家预留（保留旧未知记录与栅栏） |
| `agent/src/omni/adapters/platform/services/darwin_admin_policy.py` | macOS 管理员策略（本地账户校验与受保护根工件） |
| `agent/src/omni/adapters/platform/releases/wheel_smoke.py` | 候选树子进程冒烟（不安装/不切换/不迁移） |
| `agent/src/omni/adapters/vector_files.py` | 本地向量包文件校验与启动选择（不导入模型/不下载） |
| `agent/src/omni/adapters/persistence/schema_upgrade.py` | 受审迁移目录与锁定栅栏内的库升级（不重放） |
| `agent/src/omni/adapters/platform/services/service_admin.py` | Linux 系统服务注册/启动代理（不执行属主代码、不触业务库） |
| `agent/src/omni/adapters/platform/services/darwin_admin_probe.py` | macOS 管理员原生只读探测（CoreFoundation/SM 有界读取） |
| `agent/src/omni/adapters/platform/desktop/linux/linux_x11_wire.py` | X11 只读线上协议（已校验套接字，无发现/输入/重连） |
| `agent/src/omni/adapters/platform/services/service_readiness.py` | 每次启动的 HMAC 就绪证明（不读不传属主凭据） |
| `agent/src/omni/adapters/platform/desktop/desktop_native.py` | Windows 桌面只读诊断与进程/窗口身份（不读界面内容） |
| `agent/src/omni/adapters/platform/desktop/macos_launch.py` | macOS helper 独立 TCC 身份的 LaunchServices 启动与宿主 UDS 传输（校验对端身份，不触业务库） |
| `agent/src/omni/adapters/diagnostics.py` | 有界单写 JSON 诊断（脱敏与载荷预算） |
| `agent/src/omni/adapters/persistence/object_repository.py` | 对象元数据/固定与真实外键引用（不触文件） |
| `agent/src/omni/adapters/persistence/releases.py` | 发布一致快照与新目录隔离恢复（调用方持实例锁） |
| `agent/src/omni/adapters/persistence/schedule_repository.py` | 不可变调度定义/持久到期游标/原子占位准入（对齐端口、save 宽字典与 materialize Mapping 口径、unknown 阻塞与复核保真不变） |
| `agent/src/omni/adapters/media.py` | 可选媒体处理子进程（有界帧与预算，不触业务库） |
| `agent/src/omni/adapters/workers/credentials.py` | 固定单次 OS 凭据辅助进程（无明文回退） |
| `agent/src/omni/adapters/platform/services/service_admin_policy.py` | Linux 服务管理员根策略（本地账户与受保护根工件） |
| `agent/src/omni/adapters/process_diagnostics.py` | 进程级日志与异常钩子（不保留异常对象/不重复链式） |
| `agent/src/omni/adapters/log_query.py` | 有界诊断分页与跨重命名游标（共享写锁） |
| `agent/src/omni/adapters/workers/android.py` | 固定观察/输入 worker（层级遍历闭包、许可绑定与后置条件显式载荷收窄） |
| `agent/src/omni/adapters/workers/android_adb.py` | 有界 ADB smart-socket 客户端（不启动服务/不配对） |
| `agent/src/omni/adapters/workers/android_unicode.py` | 固定 IME 事务与认证 localabstract IPC（显式载荷/连接字段、未知不重放） |
| `agent/src/omni/adapters/platform/services/darwin_service_admin.py` | macOS 注册与单次启动根后端（固定 launchctl，不强制重启） |
| `agent/src/omni/adapters/extensions/hosts.py` | 扩展宿主池（有界进程归属与吊销，不触库/凭据） |
| `agent/src/omni/adapters/platform/releases/release_files.py` | 发布日志/安装字节与启动栅栏（不触库、不原生注册） |
| `agent/src/omni/adapters/platform/releases/wheel_environment.py` | 离线候选 venv 环境（无网络安装暂存 wheel） |
| `agent/src/omni/adapters/platform/releases/wheel_install.py` | 运行时切换/迁移/回滚指针（不停服、不迁移库） |
| `agent/src/omni/adapters/platform/desktop/linux/linux_bus.py` | 已校验本地 D-Bus 传输（无环境选择/网络总线） |
| `agent/src/omni/adapters/platform/desktop/linux/linux_session.py` | logind 会话与无障碍总线归属（不信环境变量） |
| `agent/src/omni/adapters/platform/runtime/terminal.py` | 受管终端 IPC（原生 PTY 仅在固定辅助进程内运行） |
| `agent/src/omni/adapters/temporary_debug.py` | 启动绑定的临时调试捕获（仅元数据、有界配额） |
| `agent/src/omni/adapters/persistence/migrations/versions/v0010.py` | 冻结迁移版本 v0010（凭据授权状态与不可变审阅） |
| `agent/src/omni/adapters/platform/services/service_admin_client.py` | 属主侧固定根代理连接（不提权、不自动重试） |
| `agent/src/omni/adapters/platform/desktop/linux/linux_signals.py` | 已认证本地连接上的有界 D-Bus 信号（无激活/无 fd） |
| `agent/src/omni/adapters/platform/desktop/linux/linux_fds.py` | 受控接收描述符与有界 SCM_RIGHTS（无总线/桌面策略） |
| `agent/src/omni/adapters/platform/desktop/linux/linux_input.py` | Xorg 输入变更守卫（不抓取/不合成/不存内容） |
| `agent/src/omni/adapters/native_credentials.py` | 固定单次原生凭据宿主（不落明文、不自动重发） |
| `agent/src/omni/adapters/workers/protocol.py` | 宿主与能力进程共享的有界 IPC 记录（不分发/不 IO） |
| `agent/src/omni/adapters/extensions/worker.py` | 受信扩展子进程握手与调用（不开主库） |
| `agent/src/omni/adapters/platform/desktop/linux/linux_portal.py` | 固定 Wayland 门户的显式窗口选择（不开 PipeWire fd/不派发输入） |
| `agent/src/omni/adapters/object_store.py` | 受控对象文件存储（无链接/越界） |
| `agent/src/omni/adapters/persistence/migrations/env.py` | Alembic 环境入口（迁移配置） |
| `agent/src/omni/adapters/persistence/migrations/versions/v0001.py` | 冻结迁移版本 v0001 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0002.py` | 冻结迁移版本 v0002 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0004.py` | 冻结迁移版本 v0004 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0005.py` | 冻结迁移版本 v0005（类型化配置与不可变运行引用；搬迁闭包与容器标注，SQL 字面量多重集与 HEAD 一致） |
| `agent/src/omni/adapters/persistence/migrations/versions/v0007.py` | 冻结迁移版本 v0007 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0008.py` | 冻结迁移版本 v0008 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0009.py` | 冻结迁移版本 v0009 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0011.py` | 冻结迁移版本 v0011 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0012.py` | 冻结迁移版本 v0012 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0013.py` | 冻结迁移版本 v0013 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0014.py` | 冻结迁移版本 v0014 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0015.py` | 冻结迁移版本 v0015 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0016.py` | 冻结迁移版本 v0016 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0017.py` | 冻结迁移版本 v0017 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0018.py` | 冻结迁移版本 v0018 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0019.py` | 冻结迁移版本 v0019 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0020.py` | 冻结迁移版本 v0020 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0021.py` | 冻结迁移版本 v0021 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0022.py` | 冻结迁移版本 v0022 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0023.py` | 冻结迁移版本 v0023 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0024.py` | 冻结迁移版本 v0024 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0025.py` | 冻结迁移版本 v0025 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0026.py` | 冻结迁移版本 v0026 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0028.py` | 冻结迁移版本 v0028 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0029.py` | 冻结迁移版本 v0029 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0030.py` | 冻结迁移版本 v0030 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0031.py` | 冻结迁移版本 v0031 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0032.py` | 冻结迁移版本 v0032 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0033.py` | 冻结迁移版本 v0033 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0034.py` | 冻结迁移版本 v0034 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0035.py` | 冻结迁移版本 v0035 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0039.py` | 上传媒体对象来源及原作业/产物外键图事务升级 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0036.py` | 冻结迁移版本 v0036 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0037.py` | 冻结迁移版本 v0037 |
| `agent/src/omni/adapters/platform/services/darwin_admin_peer.py` | macOS 管理员对端帧校验 |
| `agent/src/omni/adapters/platform/runtime/permissions.py` | 平台权限检查边界 |
| `agent/src/omni/adapters/platform/runtime/publication.py` | 发布文件写入边界 |
| `agent/src/omni/adapters/platform/services/service_admin_commands.py` | 服务管理员命令构造 |
| `agent/src/omni/adapters/platform/releases/wheel_artifact.py` | wheel 产物校验 |
| `agent/src/omni/adapters/workers/darwin_admin_probe.py` | macOS 管理员探测 worker |
| `agent/src/omni/adapters/persistence/action_predicates.py` | 动作复核当前解析谓词（持久化适配） |
| `agent/src/omni/adapters/persistence/action_review_repository.py` | 所有者观测台账（对齐端口 Record、计算列显式 Column[int]、详情宽字典返回收窄、unknown 保留不改原动作） |
| `agent/src/omni/adapters/persistence/context_repository.py` | 修订绑定摘要与上下文来源仓储（对齐端口 Record、领域 Mapping 结果写库前收窄、代次/序号 int 收窄） |
| `agent/src/omni/adapters/persistence/credential_repository.py` | 凭据引用/操作状态/复核仓储（显式 Record、标识收窄、执行令牌不外泄） |
| `agent/src/omni/adapters/persistence/image_repository.py` | 图像登记/策略/逐调用引用仓储（对齐端口 Record、预算与上限 int 收窄、不读文件不编码） |
| `agent/src/omni/adapters/persistence/lease_repository.py` | FIFO 认领与栅栏租约仓储（显式 Record、查询条件闭包类型化、栅栏递增收窄） |
| `agent/src/omni/adapters/persistence/configuration_repository.py` | 配置修订与 Agent 偏好仓储（显式 Record、领域 dict[str, object] 口径、权限代次收窄） |
| `agent/src/omni/adapters/persistence/repository.py` | 事务仓储聚合根（24 个 mixin 组装，回执显式 Record、标识/序号收窄、未纳入 mixin 局部 Any 桥接） |
| `agent/src/omni/adapters/persistence/delivery_repository.py` | 出站权威与 FIFO 归属仓储（对齐端口 Record 契约、标识/序号/时间戳收窄、未知不重放保留） |
| `agent/src/omni/adapters/persistence/document_repository.py` | 源修订/有界检索/派生索引仓储（对齐端口 Record、分块区间 int 收窄、查询语句显式 Select、不读文件） |
| `agent/src/omni/adapters/persistence/schema.py` | 关系表结构定义（列/约束辅助函数显式 SchemaItem 与列泛型，DDL 不变） |
| `agent/src/omni/adapters/persistence/android_repository.py` | 设备授权/运行访问仓储（显式 Record、跨模块聚合与设置 Any 桥接） |
| `agent/src/omni/adapters/persistence/android_unicode_repository.py` | 输入助手配置/IME 恢复义务仓储（显式 Record、跨模块聚合 Any 桥接） |
| `agent/src/omni/adapters/persistence/event_retention_repository.py` | 退役事件预览与裁剪仓储（对齐 Record、投影列显式 ColumnElement/Label、序号与代次 int 收窄、不删运行身份） |
| `agent/src/omni/adapters/persistence/execution_retention_repository.py` | 执行保留固定/预览/裁剪仓储（对齐端口 Record、领域计划校验前 cast 宽字典、保护判定与身份不可删不变） |
| `agent/src/omni/adapters/persistence/history_pruning_cycle.py` | 历史裁剪单事务算法（计划/目标 Record、目标键 str\|int 收窄、基类写入 Any 桥接） |
| `agent/src/omni/adapters/persistence/history_repository.py` | 历史游标分页仓储 |
| `agent/src/omni/adapters/persistence/migrations/versions/v0006.py` | 冻结迁移版本 v0006（文档来源与索引） |
| `agent/src/omni/adapters/persistence/migrations/versions/v0027.py` | 冻结迁移版本 v0027（QQ 入站归属） |
| `agent/src/omni/adapters/persistence/operations_repository.py` | 运维聚合与凭据审计仓储 |
| `agent/src/omni/adapters/platform/services/darwin_admin_commands.py` | macOS 管理员命令构造 |
| `agent/src/omni/adapters/platform/services/darwin_admin_files.py` | macOS 管理员文件身份校验 |
| `agent/src/omni/adapters/platform/services/systemd_state.py` | systemd 状态解码边界 |
| `agent/src/omni/adapters/platform/releases/wheel_stage.py` | wheel 暂存收据 |
| `agent/src/omni/adapters/platform/releases/wheel_switch.py` | wheel 切换计划 |
| `agent/src/omni/adapters/platform/releases/wheel_status.py` | 安装状态汇总 |
| `agent/src/omni/adapters/persistence/repository_base.py` | 仓储基类（审计/记录/游标辅助） |
| `agent/src/omni/adapters/platform/releases/wheel_backup.py` | wheel 更新备份收据 |
| `agent/src/omni/adapters/platform/releases/wheel_prepare.py` | wheel 候选解包与准备收据 |
| `agent/src/omni/adapters/platform/releases/wheel_rollback.py` | wheel 回滚演练收据 |
| `agent/src/omni/adapters/platform/releases/wheel_uninstall.py` | 候选卸载与显式清理 |
| `agent/src/omni/adapters/persistence/offline_plans.py` | 离线维护计划读取/校验 |
| `agent/src/omni/adapters/persistence/retention_repository.py` | 保留/擦除会话失效仓储 |
| `agent/src/omni/adapters/persistence/routing_repository.py` | 不可变档位策略/运行选择/升档/实际工具清单（对齐端口 Record、嵌套设置收窄、升档仅向上一次、不调模型不做 IO） |
| `agent/src/omni/adapters/persistence/run_checkpoint_repository.py` | 安全接续、工作队列与回执校验 |
| `agent/src/omni/adapters/platform/desktop/desktop_capture.py` | 桌面截图文件身份校验 |
| `agent/src/omni/adapters/platform/desktop/macos_helper.py` | macOS 辅助源指纹与文件校验 |
| `agent/src/omni/adapters/platform/services/macos_service.py` | macOS launchd 查询边界（原生身份校验） |
| `agent/src/omni/adapters/platform/runtime/paths.py` | 实例路径与启动栅栏互斥 |
| `agent/src/omni/adapters/platform/services/service_admin_bundle.py` | 管理员工件物化（不安装/不提权） |
| `agent/src/omni/adapters/persistence/history_protection.py` | 历史保留保护原因（运行/引用/审计） |
| `agent/src/omni/adapters/workers/terminal.py` | 终端 worker 帧解码/打开校验 |
| `agent/src/omni/adapters/persistence/sqlalchemy_access.py` | SQLAlchemy 引擎事务/PRAGMA 设置 |
| `agent/src/omni/adapters/platform/desktop/linux/linux_x11_socket.py` | X11 授权 cookie 解析 |
| `agent/src/omni/adapters/workers/desktop.py` | 桌面 worker 帧解码 |
| `agent/src/omni/adapters/persistence/execution_pruning.py` | 执行正文保留计划（读/建/应用/取消） |
| `agent/src/omni/adapters/persistence/mcp_repository.py` | MCP 运行代际/目录快照/逐工具授权/调用记录（对齐端口 Record、宽字典授权结果收窄、存活授权查询显式 Select、不开 socket 不启进程不取凭据） |
| `agent/src/omni/adapters/persistence/object_gc.py` | 独占计划/应用回收（entries/残留显式 Record、写回调用 UnitOfWork、先提交元数据后解链不变） |
| `agent/src/omni/adapters/persistence/memory_organization_predicates.py` | 已批准重复唯一谓词（别名 NamedFromClause、条件 ColumnElement、Select/Exists 显式；SQL 与 HEAD 逐字节一致） |
| `agent/src/omni/adapters/persistence/memory_organization_repository.py` | 有界精确重复提案与所有者复核（对齐端口 Record、计算列 Column[int]、先量字节后取正文、不改不忘不复制真相） |
| `agent/src/omni/adapters/persistence/media_repository.py` | 媒体来源/有界任务/不可变产物行（对齐端口 Record、_latest 显式 Connection/Table、不读文件、重启对账 unknown 不重放） |
| `agent/src/omni/adapters/persistence/memory_repository.py` | 记忆真相/证据/修订与抹除（对齐端口 Record、来源序号与区间 int 收窄、墓碑不重放保留） |
| `agent/src/omni/adapters/persistence/memory_semantic_repository.py` | 派生向量与来源绑定相关事实决策（对齐端口 Record、父类捕获 Any 桥接、只关联不抑制不改写事实） |
| `agent/src/omni/adapters/persistence/object_inventory.py` | 离线对象登记/物理清单校验 |
| `agent/src/omni/adapters/persistence/reference_release.py` | 引用释放计划（读/建/应用/取消） |
| `agent/src/omni/adapters/persistence/reference_release_repository.py` | 已审阅退役动作-对象引用释放（对齐端口 Record、领域计划校验前 cast 宽字典、只删引用不删对象不改过期） |
| `agent/src/omni/adapters/workers/terminal_gate.py` | Windows ConPTY 启动门（Job 归属后放行） |
| `agent/src/omni/adapters/workers/service_admin_boot.py` | 受审管理员工件物的隔离 root 入口（按持有 fd 降层校验） |
| `agent/src/omni/adapters/workers/linux_action.py` | Linux 语义动作 worker 单帧入口（初始帧不授权写入） |
| `agent/src/omni/adapters/persistence/operations_read.py` | 离线只读运维快照（只读 URI、schema 校验、不自动升级） |
| `agent/src/omni/adapters/platform/runtime/event_loop.py` | Windows TCP 重置清理事件循环（不改全局 asyncio 类） |
| `agent/src/omni/adapters/platform/desktop/linux/linux_pipewire.py` | 串行目标 GStreamer 取帧（不开默认远程/不读根窗口） |
| `agent/src/omni/adapters/persistence/database.py` | 单写者数据库 actor（隔离读连接、关闭与完整性检查） |
| `agent/src/omni/adapters/persistence/qq_authority.py` | QQ 受众/凭据权威（上下文、模型与外发共用检查） |
| `agent/src/omni/adapters/persistence/qq_repository.py` | QQ 准入与权威（对齐端口 Record、会话目标不可变、事件幂等与冷却收窄、无网络无额外真值库） |
| `agent/src/omni/adapters/persistence/bilibili_authority.py` | Bilibili 发布/预览共用权威（读取、来源与形态校验） |
| `agent/src/omni/adapters/persistence/bilibili_repository.py` | 不可变表单修订与发布（参数按端口 dict[str, object]、回执 Record、派发授权逐项校验不变） |
| `agent/src/omni/adapters/models.py` | 异步模型适配器（凭据与厂商异常不出端口） |
| `agent/src/omni/adapters/persistence/people_repository.py` | 人物与手工账号身份（个人文档复用记忆仓储） |
| `agent/src/omni/adapters/persistence/audit_pruning_repository.py` | 审计删除计划/批次/恢复回执（保留身份与业务账本） |
| `agent/src/omni/adapters/persistence/audit_retention_repository.py` | 审计保留预览与候选资格（只读元数据，不删行） |
| `agent/src/omni/adapters/mcp/hosts.py` | 按服务代次持有 MCP 会话（不访问数据库/模型） |
| `agent/src/omni/adapters/__init__.py` | adapters 包标记（仅包文档字符串） |
| `agent/src/omni/adapters/extensions/__init__.py` | extensions 包标记（仅包文档字符串） |
| `agent/src/omni/adapters/mcp/__init__.py` | mcp 包标记（仅包文档字符串） |
| `agent/src/omni/adapters/persistence/__init__.py` | persistence 包标记（仅包文档字符串） |
| `agent/src/omni/adapters/persistence/migrations/__init__.py` | migrations 包标记（仅包文档字符串） |
| `agent/src/omni/adapters/platform/__init__.py` | platform 包标记（仅包文档字符串） |
| `agent/src/omni/adapters/workers/__init__.py` | workers 包标记（仅包文档字符串） |
| `agent/src/omni/composition/serving.py` | 单 worker HTTP 进程组合（手动/固定服务启动） |
| `agent/src/omni/composition/service_cleanup.py` | 离线服务清理命令组装（显式审批，不注册原生服务） |
| `agent/src/omni/composition/service_operations.py` | 离线服务操作命令组装（计划/检查/应用/对账/取消） |
| `agent/src/omni/composition/service_runner.py` | 固定部署的服务入口（仅启动已校验的不可变部署） |
| `agent/src/omni/composition/capabilities.py` | 内建能力目录（工具 spec 与 worker 装配） |
| `agent/src/omni/composition/channel_providers.py` | 渠道端口绑定、发送覆盖与共享传输归属 |
| `agent/src/omni/composition/model_providers.py` | 普通调用、请求预备、流式端口与关闭归属 |
| `agent/src/omni/composition/platform_providers.py` | 主机选择、原生端口注入、共享并发池与启动回滚 |
| `agent/src/omni/composition/releases.py` | 已审阅版本发布命令组装（计划/准备/完成/取消） |
| `agent/src/omni/composition/service_exchange.py` | 整机交换命令组装（两侧部署与管理员通道） |
| `agent/src/omni/composition/lifecycle.py` | 有界关停与启动回滚的生命周期协调（不分派业务） |
| `agent/src/omni/composition/instance.py` | 隔离实例组装根（身份/健康投影/有界关停装配，显式 Record 与端口收窄） |
| `agent/src/omni/composition/http.py` | HTTP 组合与所有者令牌引导（只读访问器、令牌/排空/关停回执显式 Record） |
| `agent/src/omni/composition/cli.py` | V2 开发入口（安装/体检/对象回收/诊断回执显式 Record，未类型化适配器返回精化为 dict[str, object]） |
| `agent/src/omni/composition/service_control.py` | 服务生命周期命令组装（管理员操作收窄为 Record、关闭回执显式 Record） |
| `agent/src/omni/composition/__init__.py` | composition 包标记（仅包文档字符串） |
| `agent/src/omni/adapters/workers/main.py` | 能力子进程入口（文件/命令/浏览器能力，不导入数据库或 Runtime） |

| `agent/src/omni/adapters/workers/local_vectors.py` | 本地向量 worker（IPC/清单显式 Payload、受检字段收窄、窗口归属带类型；模型图校验、分窗、池化及错误码不变） |

| `agent/src/omni/adapters/local_vectors.py` | 本地向量宿主（IPC 显式 Payload、状态对齐 Record、启动/计算/回收 Task 泛型化；取消归属、实际回收及全文降级不变） |

| `agent/src/omni/adapters/extensions/packages.py` | 插件包与快照（清单/文件证明显式 Payload、市场回执对齐 Record、异构 I/O Future 有界归属；零执行捕获、路径校验及原子不覆盖发布不变） |

| `agent/src/omni/adapters/model_stream.py` | 模型流式适配器（SDK 迭代器/关闭回调/计费载荷显式类型；供应商结束与用量证据、分片限额、取消后实际关闭及未知费用不变） |

| `agent/src/omni/adapters/diagnostic_bundle.py` | 诊断包（来源/调试回执对齐 Record、关联项白名单显式 Payload、I/O Future 泛型化；脱敏边界、容量、取消归属及不覆盖导出不变） |

| `agent/src/omni/adapters/onebot.py` | OneBot 传输（配置/帧显式 Payload、监听回调对齐 object、上下文管理器 AsyncGenerator、连接与 Task 归属显式；发送前授权复核及 unknown 不重放不变） |

| `agent/src/omni/adapters/workers/media.py` | 媒体 worker（IPC Payload、音频/图像 SDK 边界显式类型、固定可选依赖加载；离线缓存、VAD 空结果回退及采样字节不变） |

| `agent/src/omni/adapters/workers/bilibili.py` | Bilibili 浏览器 worker：类型化上下文所有权、WebSocket 拦截与协议输入；发布约束不变 |

| `agent/src/omni/adapters/bilibili.py` | Bilibili 网关：类型化有界读取、授权凭据、单次发布与取消回收 |

| `agent/src/omni/adapters/http.py` | HTTP 适配器：Record 端口、原 Pydantic schema 的 Payload、泛型输入工厂及路由边界 |
| `agent/src/omni/adapters/http_routes/__init__.py` | HTTP 严格请求基类 |
| `agent/src/omni/adapters/http_routes/extensions.py` | 插件 HTTP 接口 |
| `agent/src/omni/adapters/http_routes/skills.py` | 技能 HTTP 接口与请求模型 |
| `agent/src/omni/adapters/http_events.py` | 有界 SSE 历史通知、游标续接、持续鉴权与连接回收；不推送事件正文 |
| `agent/src/omni/adapters/soak.py` | 本机签名boot采样、受限HTTP读取和私有连续证据，不调用V1或模型 |

| `agent/src/omni/adapters/platform/desktop/linux/linux_atspi.py` | AT-SPI 只读适配器：native 引用、状态、节点与语义证明回调；原生调用守卫不变 |

| `agent/src/omni/adapters/platform/desktop/linux/linux_x11.py` | X11 目标核验与采集适配器类型化，原生证明和像素边界不变 |

| `agent/src/omni/adapters/platform/services/deployment_files.py` | 部署文件类型化与公共发布入口，保留私有注入点及原子文件语义 |

| `agent/src/omni/adapters/platform/services/service_templates.py` | 服务模板类型化，固定服务命令与产物字节不变 |

| `agent/src/omni/adapters/platform/runtime/runtime_identity.py` | 运行身份、Task/Future所有权与受控drain回执类型化 |

| `agent/src/omni/adapters/platform/services/service_operation_files.py` | 离线服务日志类型化，保留连续链、unknown与单所有者文件锁 |

| `agent/src/omni/adapters/platform/services/service_registration.py` | 用户服务注册类型化，命令、身份复核与中断归属不变 |

| `agent/src/omni/adapters/workers/portal.py` | Portal worker类型化，显式授权、帧请求与清理时限不变 |

| `agent/src/omni/adapters/platform/desktop/linux/linux_semantic.py` | Linux 语义动作类型收官，输入屏障、再证明与unknown语义不变 |

| `agent/src/omni/adapters/platform/desktop/portal.py` | Portal文件任务、采集与回收类型收官，授权和配额不变 |

| `agent/src/omni/adapters/workers/linux_desktop.py` | Linux桌面worker收官，真实工厂签名与语义调用类型贯通 |

| `agent/src/omni/adapters/mcp/stdio.py` | MCP stdio类型化，保留有界、取消与进程销毁边界 |

| `agent/src/omni/adapters/mcp/http.py` | MCP HTTP与SSE类型化，未知写入不重放 |

| `agent/src/omni/adapters/mcp/oauth.py` | MCP OAuth类型收官，V2四层合同范围332文件，真实验收仍待 |

| `agent/src/omni/domain/repetition.py` | 模型输出重复退化检测纯规则，保守判定且无 I/O |

| `agent/src/omni/domain/files_search.py` | 工作区搜索规则（内容子串/正则与文件名 glob）有界，不访问文件系统 |

| `agent/src/omni/domain/files_patch.py` | 模糊搜索替换补丁规则有界，不访问文件系统与存储 |

| `agent/src/omni/domain/media_locate.py` | 转写定位纯规则，锚点只取自源转写、不猜测 |

| `agent/src/omni/domain/tool_batch.py` | 工具批次分段规划，仅只读能力可并行 |

| `agent/src/omni/domain/clarify.py` | 结构化澄清纯规则：问题归一、答案清洗与结果态 |

| `agent/src/omni/domain/session_search.py` | 模型侧历史召回纯规则：模式推断、有界整形与去重 |

| `agent/src/omni/domain/background_review.py` | 轮次后复盘提案与所有者决策的有界规则，不做模型调用与写入 |

| `agent/src/omni/domain/verification_stop.py` | 停止前验证门纯规则：已应用写入缺少验证证据 |

| `agent/src/omni/domain/model_recovery.py` | 模型空回复恢复纯规则：确定性空判定与成本感知预算 |

| `agent/src/omni/domain/memory_decay.py` | 记忆衰减与整理纯规则：保留评分、清晰化步骤与追赶，无 I/O |

| `agent/src/omni/domain/memory_scoring.py` | 记忆重要性评分启发式纯规则，无 I/O |

| `agent/src/omni/application/clarify.py` | 面向所有者的澄清用例，模型侧等待留在引擎运行协程 |

| `agent/src/omni/application/session_search.py` | 模型侧历史召回能力，共享应用事务与擦除屏障 |

| `agent/src/omni/application/background_review.py` | 每个已结束运行一个有界复盘器，提案在所有者决策前保持待定 |
| `agent/src/omni/application/capabilities.py` | 能力插件注册、回合工具快照和适配器生命周期归属 |

| `agent/src/omni/adapters/persistence/clarify_repository.py` | 结构化澄清请求持久化：暂停当前运行并在同一运行上恢复 |

| `agent/src/omni/adapters/persistence/session_search_repository.py` | 共享会话/消息存储上的有界只读历史召回 |

| `agent/src/omni/adapters/persistence/background_review_repository.py` | 复盘提案与所有者审阅持久化，应用仍走记忆/技能链 |

| `agent/src/omni/adapters/persistence/migrations/versions/v0047.py` | 冻结迁移版本 v0047（记忆衰减列与按代理衰减游标；幂等加列，沿用 v0045 先例） |

| `agent/src/omni/adapters/persistence/migrations/versions/v0048.py` | 冻结迁移版本 v0048（`event_retention_targets` kind 白名单与领域 KINDS 对齐；事务重建保留全部证据行、索引与触发器） |

| `agent/src/omni/adapters/persistence/migrations/versions/v0049.py` | 冻结迁移版本 v0049（日程技能绑定与输出引用两表 + `continuity` 列；身份与禁删触发器，`downgrade` 拒绝） |

| `agent/src/omni/adapters/persistence/migrations/versions/v0050.py` | 冻结迁移版本 v0050（工具结果图像账本：`model_call_images.message_id` 可空重建；`call_image_scope` 双分支——消息分支 v0032 原 SQL 逐字保留、工具分支要求注册对象与新鲜 part_id；run 终态释放触发器，释放≠擦除；账本不可删不变，`downgrade` 拒绝） |

| `agent/src/omni/adapters/persistence/migrations/versions/v0051.py` | 冻结迁移版本 v0051（反思信笺、文档/媒体已读锚点与待审队列；新表与身份/禁删触发器，`downgrade` 拒绝） |

| `agent/src/omni/adapters/persistence/migrations/versions/v0052.py` | 冻结迁移版本 v0052（反思媒体锚点、修订绑定审核与所有者日槽；身份/禁删触发器，`downgrade` 拒绝） |
| `agent/src/omni/adapters/persistence/migrations/versions/v0053.py` | 内核策略与模型回退的增量字段 |

| `agent/src/omni/domain/reflection.py` | 反思信笺纯领域：8 字段正文、证据锚点、摘录选择与修订状态约束 |

| `agent/src/omni/application/reflections.py` | 反思控制与所有者审核用例：观察/保存/读取/摘录/跳过，共享同一事务账本 |

| `agent/src/omni/adapters/persistence/reflection_repository.py` | 反思审核持久化：范围/来源/修订 CAS、精确摘录与原子所有者决定 |

| `agent/src/omni/adapters/persistence/reflection_media.py` | 反思媒体证据收据：媒体动作/产物锚点与擦除联动 |

| `agent/src/omni/domain/persona_growth.py` | 人格成长派生视图纯领域：已批准句确定性渲染、换行折叠、1 MiB/2000 行截断与固定空态 |

| `agent/src/omni/adapters/persona_files.py` | 人格成长受管缓存：逐字节校验、原子替换、符号链接/硬链接拒绝、临时残留清理与不可逆失效 |

| `agent/src/omni/application/persona.py` | 人格成长只读用例：账本→期望字节→缓存校验/重建，仅所有者读取，不进入模型上下文 |

| `agent/src/omni/domain/visual_subagent.py` | K14 有界视觉子代理纯领域：任务/焦点/帧 id 校验、≤60 保序等距含首尾、固定 prompt 与纯文本输出边界 |

| `agent/src/omni/application/visual_subagent.py` | K14 离线视觉子代理用例：父 run/模型授权、帧源顺序核验、单次调用/取消/并发、零写与仅文本汇总 |

| `agent/src/omni/adapters/visual.py` | K14 生产视觉适配：授权图像对象解析与一次 ModelCalls 预算/账本视觉请求；无 master 历史/工具 |

| `agent/src/omni/domain/som.py` | K14 Android SoM 纯领域：节点准入/密码跳过/边界裁剪/≤50 稳定编号与有界映射 |

| `agent/src/omni/adapters/som.py` | K14 SoM PNG 渲染：同尺寸叠加、仅数字入像素、确定性编码与输出上限；不做采集/发布 |

| `agent/src/omni/domain/android_explore.py` | K14 explore_app 纯领域：结构 skeleton 哈希（不含文本/密码）、屏幕/动作边去重、200 屏/1000 边上限与截断 |

`pyrightconfig.strict.json` 的 `include` 必须与本表完全一致；`agent/tests/v2/core/test_v2_typecheck_scope.py` 会双向校验。

## 3. 历史基线与当前范围

在 `1a18fae` 源码上测得（仅记录，不构成门禁）：

| 范围 | strict 错误数 |
| --- | --- |
| `agent/src/omni/domain`（全部 53 文件） | 2321 |

`service_control.py`（94）已在纳入 strict 前清零；`model_stream.py`（75）、`context.py`（38）、`recovery_authority.py`（27）、`memory_semantics.py`（19）、`foreground.py`（24）、`metering.py`（19）与 `release_fence.py`（16）同样已清零，基线表中不再列逐文件例外。`application/` 已全部纳入（44/44 个模块）、`adapters/` 222 个模块；`composition/` 已全部纳入（13/13 个模块，守卫允许层已加入 composition）；`agent/tests/` 尚未开始 strict。

### 3.1 adapters 类型收口（第 214 批）

全部 **271 个 adapters 已纳入 strict**；合同范围为 **386 文件**（domain 57、application 45、adapters 271、composition 13）。新增事件通知、空间管理、V2独立长跑工具、Linux持续窗口/焦点/显式提供方窗口绑定/物理点命中预检/按键映射、XKB服务器/XTEST来源、完整映射与其余复制配置比对、精确设备切换后验及成对派发内核、受限前台文本及独立控制许可worker/父进程/业务用例后，历史335文件结论仅代表原固定候选；开发中只复核修改范围，整范围复验留整个V2开发完成后的验收。原 P4/P5/P6 的 24 个待办模块已完成，不再是当前施工清单。

统计以本页逐文件表为准；domain/application 各一个仅含说明文字的包初始化文件未列入该合同范围。V1 与 tests 尚未纳入，因此**全仓严格类型尚未完成**。

## 4. 推进规则

1. 每次只把一个模块或一组强相关纯函数纳入 strict；纳入前先清零该模块错误，不把“忽略注释”当清零。
2. 新增的 strict 模块必须同时更新 `pyrightconfig.strict.json`、本文件表格与证据；守卫测试会拦截漂移。
3. 基线例外数字只代表对应源码版本；源码变化后需重测，不沿用旧数字。
4. 不允许用 `# type: ignore`/`# pyright: ignore` 掩盖真实错误；确需忽略时必须在类型检查文档写明原因与范围。
   - 目前唯一的忽略：`agent/src/omni/adapters/platform/runtime/event_loop.py` 中 `from asyncio.proactor_events import ...` 一行带 `# pyright: ignore[reportPrivateUsage]`。CPython 未公开 proactor 传输类，绕过导入就无法子类化该传输；范围仅限该导入行，同一文件的其余私有成员访问用 `Any` 桥接（协议仍保留形状说明）而不是忽略。
5. 全仓 strict 完成前，任何报告不得写“全仓严格类型已通过”。
6. 历史边界与施工注意（当前是否纳入以本页门禁表为准）：
   - 部署文件适配器已使用 find_spec("omni") 定位包，避免绝对 import 的缺失 stub，并保留不执行包初始化的边界；第 201 批已纳入 strict。后续不要重新引入绝对导入或忽略注释。
   - composition/http.py 依赖 composition/instance.py 的公开面（数据库/日志/身份/关停）：纳入顺序必须先 instance.py 再 http.py，否则只能靠 Any 桥接，不符合本文件的清零口径。
   - 仓储 mixin 体系（`repository.py` 及其 70 余个 mixin）依赖 `RepositoryBase` 的公共方法；新增跨模块助手时应使用 `one` 等公共方法，而不是访问 `_one` 等受保护成员。


## 5. 验证

- strict 门禁 0 errors / 0 warnings。
- `agent/tests/v2/core/test_v2_typecheck_scope.py`：配置与文档表一致、文件存在、只允许 `domain`/`application`/`adapters` 前缀、无目录/通配批量纳入、文档保留“全仓严格类型尚未完成”。
- 相关调用方回归：各批守卫 + 对应模块调用方测试。
