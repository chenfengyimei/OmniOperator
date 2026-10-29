# 人工终端与命令任务

入口：工作台「终端」打开 `/terminal/`。人工 ConPTY 与 agent shell 工具隔离，
模型不能读取或输入人工会话。此处命令以当前 Windows 用户运行，不是沙箱。

## 人工终端 C02

- 多标签创建/切换/重命名/结束；固定 Shell：powershell、已安装 pwsh、cmd。
- 目录须位于 terminal.roots（默认项目根）；默认最多 4 个活会话，缓存 1 MiB/会话。
- 页面关闭只断开连接，默认 600 秒未连接回收；「结束终端」才主动终止。
- 刷新时恢复本配对身份的会话并重放有限输出；较旧输出丢失明确提示。
- v2 WS 只回放输出，不重发输入；每会话只允许一个写入连接。
- 多行粘贴需确认，Ctrl+C/清屏/复制为独立按钮；退出状态与退出码分别展示。

配置 terminal：max_sessions、roots、buffer_bytes、detach_ttl_s。全部由注入配置声明；
非 Windows/缺依赖时如实不可用。旧 `/api/terminal/ws` 保留原一次连接语义。

接口：GET options/sessions、POST sessions、PATCH/DELETE sessions/{id}（统一前缀
`/api/terminal/`），WS sessions/{id}/ws?after=seq。
消息包含 v=2 和 type=input/resize/output/exit/gap；未配对/外部 Host/Origin、
其它身份会话、第二个写者拒绝；输出序号仅用于续读，不能授权输入。

终端读/写/调整尺寸/关闭均放在工作线程；持续排空与 WebSocket 速度解耦，
关闭预算到期仍有会话或读线程时报告 residual，不宣称全部结束。

## 后续

C03 agent 后台命令任务尚在开发，不能把人工 PTY 当作命令任务 API。
路线及验证状态见 CAPABILITIES_PLAN.md。
