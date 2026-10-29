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

## agent 后台命令 C03

工具 shell_job_start/list/read/cancel；启动和取消均 CONFIRM+requires_human，只读工具
按当前 SessionRef 限定会话。工作台 /jobs/ 可查任务、续读日志和取消，不提供绕过审批的启动 API。
正常回合结束可继续；取消父回合、手动取消、超时及 Runtime.close 都请求回收。
Windows 经身份核验终止进程树，无法确认时 unknown，不以裸 PID 授权。

默认每运行态并发 2、等待 4，默认 1800 秒/上限 7200 秒；command_jobs 配置可调整。
后台与人工 PTY 完全隔离；固定 Shell，cwd 限定 files.root，启动仍过原硬黑名单。
命令/工作目录/身份/状态在 state.db v14；日志由 RuntimePaths.command_logs 注入。
每任务最近 2MiB JSONL 环，模型单次至多 16KiB，较早缺失有 gap；日志是不可信数据。
日志保留 7 天、总额 64MiB，在新任务前清理已完成任务旧日志；仍排空超限输出。
重启只对已失去所有者的遗留任务记录 unknown/未启动记录 cancelled，不重跑、不自动认领。

queued/running/cancelling/succeeded/failed/cancelled/timed_out/unknown 与副作用状态分开；
取消/超时后可能已产生副作用，禁止直接重跑。跨入口活跃任务须从原入口取消。
本轮不做跨入口进程托管接管。独立提交源码 976 项测试通过，真实临时父子进程取消、日志与账本副本恢复通过。
路线及验证状态见 CAPABILITIES_PLAN.md。
