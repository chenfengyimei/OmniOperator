# Windows 桌面读取与受控编辑

采用原生 Windows UIAutomation 后端：无需安装额外 MCP 服务，控件能力可直接回读。
参考 Hermes 的 backend/observation 契约，未复制其跨平台实现。静态 PowerShell 辅助程序
隐藏运行，参数只走 stdin JSON；所有调用有超时。

```yaml
desktop:
  enabled: true
  allowed_apps:
    - C:/Windows/System32/notepad.exe
  max_nodes: 100
  observation_ttl_s: 120
  timeout_s: 15
  max_writes_per_day: 30
```

空白名单全拒，必须配置真实 exe 完整路径。Store 记事本可能由 System32 启动器重定向
到 WindowsApps；打开后用 `Get-Process notepad | Select-Object Id,Path` 核对路径再配置。
应用升级路径变化后重新审核。配置需重启入口，不会修改系统权限或绕过 UAC/锁屏。

工作台“桌面”页提供窗口列表与文本观察。聊天工具：

- `desktop_windows`：允许窗口，含 HWND、PID、启动身份、exe。
- `desktop_observe(window)`：有界 UI 树、父节点、文本、控件能力、DPI/物理边界及 observation。
- `desktop_screenshot(observation)`：PrintWindow 后台截图，经产物与视觉隐私闸门处理。
- `desktop_action(...)`：set_value 覆盖文本、invoke 激活、select 选择标签。人工审批展示
  完整应用路径、窗口标题、目标和内容；预览与实际窗口不符直接拒绝。

观察绑定会话、窗口/进程创建身份、树版本与 TTL；动作前和辅助程序内再次复核。
跨进程窗口租约、独立每日预算；一次 observation 仅可尝试一次写，结果 unknown 不能重放。
set_value 精确回读原控件全文；invoke 不因接口成功就宣称业务目标完成。
密码框、不可见/禁用/最小化、内容截断均拒写。群聊禁用全部桌面工具。

没有通用 SendInput 或前台接管。缺少所需 UIAutomation Pattern 时报告不支持，由用户
手动操作；不因截图成功宣称能控制任意软件。

## 验收与限制

2026-10-28 Store 记事本 `11.2607.14.0`、96 DPI：临时中文内容读取、后台 ValuePattern
编辑、精确回读、重复 observation 拒绝、PNG 生成（28303 字节）、临时标签关闭已验证。
[真机证据](evidence/notepad-2026-10-28-store.json)；首次 System32 白名单无法读取重定向
窗口的拒绝结果保留在 [首次证据](evidence/notepad-2026-10-28.json)。未修改用户现有文件。

```powershell
# 显式手动套件，仅新建临时文档，不进入默认 pytest
uv run --no-sync python scripts/manual_desktop_smoke.py --run --app <真实exe> --output <结果路径>
```

离线覆盖过期树、跨会话、撤销配置、PID/树变化、最小化、密码框、预算、领取竞争、unknown
与审批预览伪造。第二个实际目标应用、多屏/混合 DPI、实际锁屏和管理员窗口待验收。
不把 mock 条件当作真机通过；PNG 生成也不能证明所有应用后台截图均正确。

依据：[Microsoft TextPattern](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-textpattern-overview)、
[ValuePattern 编辑](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/add-content-to-a-text-box-using-ui-automation)。
