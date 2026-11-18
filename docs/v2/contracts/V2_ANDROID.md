# V2 安卓设备合同

所有者链已接通；模型本次运行权限、滑动/长按/按键、应用查询和启动也已接线，见[模型与扩展输入](V2_ANDROID_MODEL.md)。没有操作真实设备，中文输入及真实验收待完成。整个D5和V2未完成，默认入口仍V1。

## 接入

在V2工作台「配置」建立并启用channel配置，再进入「安卓设备」：

```json
{"driver":"android","endpoint":"http://127.0.0.1:5037","account":"明确的设备序列号","credential_ref":""}
```

地址表示现有本机ADB smart-socket服务，实际使用TCP协议，不发送HTTP请求；端口须显式指定，主机固定127.0.0.1。不会启动/重启/杀死ADB，不自动发现网络设备、配对、安装、root或改变输入法。USB调试及主机授权由所有者在设备和平台工具中完成，保存配置不产生设备授权。

所有者先核验指定设备，再授权展示身份。探测凭证有效60秒，绑定Agent和配置修订。持久授权绑定序列号、transport ID、Android boot UUID、当前Android用户、构建指纹及实例历史代次。设备重启、重连改变transport、切用户、配置变更/禁用及备份恢复后须重新核验授权。本机ADB是所有者选择并信任的服务；这些身份字段不宣称抵抗控制ADB服务的本机恶意代码。

页面加载只读取授权列表，不访问设备。离开页面清除观察、图片预览和计时器，不撤销持久设备授权；草稿按Agent隔离。模型只能使用单独审批的本次运行权限，不能直接复用所有者节点或创建永久设备授权。

## 分层与存储

- `domain/android.py`定义纯身份、配置与输入合同。
- `application/android.py`拥有授权、观察、截图及动作编排。
- `adapters/android.py`拥有受管进程及受控PNG；主进程不执行ADB命令。
- `workers/android_adb.py`实现有限smart-socket/shell-v2帧，`workers/android.py`执行固定观察、截图和ready/commit。子进程不打开数据库、不导入V1、不接受任意shell。
- 组装根负责构造与关闭；所有者能力不注册到模型工具表；模型使用独立的受限合同。

设备首批新增`android_grants`（v2_0029）；当前规范化运行权限接入后为106表/v2_0030，详见[模型合同](V2_ANDROID_MODEL.md)。授权具有Agent/配置修订外键、指纹、历史代次和revision；禁止改绑，仅允许撤销。`owner_commands`扩展允许`android_input`，仍在动作建立时把参数原子移交`actions`并清空原参数。不新建第二套运行、审批、执行、结果或核查状态。

截图复用`session_captures → objects`，方法为`adb_screencap`。对象、会话引用和审计使用原有事务及删除传播。迁移采用冻结DDL并保留已有命令/截图、约束和触发器，不导入V1数据。

## 观察与截图

观察前后核验设备、用户、前台应用；XML上限256KiB，禁止DTD/实体。最多128节点、8层、单字段512字符、总正文16KiB，最终IPC小于64KiB。只返回当前前台包节点；密码控件不返回文本、描述或动作。路径和证明仅保存在服务内存，对外节点UUID只属于60秒观察，不授权写操作。

截图为**完整设备屏幕**，可能包含通知或其他应用内容。每次由所有者显式请求、绑定会话内容代次，采集前后核验身份。PNG上限16MiB、单边8192、总计1600万像素，经宿主解码及尺寸校验后发布。不会自动附加模型，不宣称模型已看过。

## 审批与结果

审批展示设备序列号、Android用户、应用、资源ID、观察指纹、动作及文本。当前支持：

| 动作 | 条件 | 结果 |
|---|---|---|
| append_ascii | 当前聚焦、非密码EditText | 回读同一控件等于原文加批准文本 |
| toggle | 可点击、可勾选且非密码 | 回读同一控件checked改变 |
| tap | 可点击且非密码 | 坐标输入已派发也为unknown，需人工核查业务效果 |

追加仅允许ASCII字母/数字/空格及`. , _ @ + -`，不清空、不按Enter、不把Unicode降级成错误字符。输入前须明确证明屏幕唤醒、keyguard未显示且输入未受限；厂商字段缺失则输入不可用。

流程为：意图入队→统一审批→资源租约→子进程重新观察/ready→宿主复核授权、策略与运行→一次commit→再核验身份/原树→单次输入→后置回读。节点、令牌不能跨运行/Agent/辅助进程复用。原树、目标、用户、boot或授权变化时拒绝。点击或提交后的异常不当作普通失败重发。

同序列号存在执行中或未核查unknown动作时，拒绝后续输入；换Agent、重新授权或重新观察不能绕过。只读观察仍可用于核查。核查沿现有不可变账本，原unknown不改写；执行器未静止不能核查。核查后仅允许新的审批动作，不重放旧动作。

## 资源与接口

socket期限8秒，辅助用例20秒，宿主IPC预算22秒，工具总期限25秒。服务最多接受8项操作，宿主最多4项等待任务，一个实际ADB操作串行。设备和网络调用不在数据库事务内。

取消等待不取消实际工作；受管进程、临时文件及租约在真实回收后释放。关闭预算耗尽报告残留，保留数据库和实例锁，由共同`close_until_quiescent`完成实际关闭。只删除本次PNG；旧启动遗留有界清点并报告，100项停止新截图，不自动清理用户无关文件。临时文件及目录私密，日志/指标不包含界面、截图或完整输入；授权/撤权和截图具有元数据审计。

所有者认证的`/api/v1/agents/{agent_id}/android`：

- `GET /grants`：UUID游标，最多100项。
- `POST /probe`：config_id、consent。
- `POST /grants`：probe_id、consent。
- `POST /grants/{id}/revoke`：revision。
- `POST /grants/{id}/observe`：consent。
- `POST /grants/{id}/capture`：consent、session_id。
- `DELETE /observations/{id}`：释放短期观察。
- `POST /actions`：观察/节点、会话、请求UUID、动作和文本；仅入队，不自动批准。

请求上限4KiB，严格字段与布尔确认，不接受原生地址、任意shell或宿主路径。撤权及取消不能保证撤回已派发输入。

## 剩余与验收

模型受限授权/运行绑定及滑动、按键、长按、应用查询启动已接入，当前继续D5受控中文输入辅助组件。任意坐标、安装或IME切换不作隐式回退。不能将本批称为完整安卓或D5完成。

离线使用真实SQLite、实际受管Python和本地假ADB；界面用独立BrowserContext/数据库。Windows本地假设备通过不等于真实Android、macOS或Linux已验收。实机需核验厂商window/keyguard字段、shell-v2、XML、截图、文本/勾选、撤权、用户切换、重连和睡眠。真实操作按具体可审阅场景另行授权；全量、打包、三平台及72小时待验收。

参考[Android官方ADB说明](https://developer.android.com/tools/adb)及[AOSP shell-v2帧](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/shell_protocol.h)。
