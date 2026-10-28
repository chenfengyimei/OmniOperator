# 运维、诊断与恢复

## 只读诊断

```powershell
uv run --no-sync python -m omni.channels.doctor
uv run --no-sync python -m omni.channels.doctor --data-dir <运行目录> --config <配置文件> --memory-db <可选独立记忆库>
```

CLI 对话中 `/doctor`、工作台“只读诊断”调用相同检查。独立 doctor 命令不 build_runtime，
不建库、不迁移、不连接 NapCat/设备/MCP、不调用模型或下载依赖。报告只有安装版本、
计数、兼容性和进程身份状态；不包含密钥、完整对话或工具参数。

2026-10-28 早先真机只读报告见 [doctor 证据](evidence/doctor-2026-10-28.json)：当时 state v9，
当时程序要求 v12；NapCat/QQ 为旧记录，不能授权自动停止。当时进程的 PATH 未解析到
adb，这不表示 D:/tools/platform-tools 中没有安装。模型连通、设备与账户登录未验证。

D27 实际备份时源库已为 v13，恢复副本两次打开通过；完整采样与升级操作见
[验收手册](ACCEPTANCE.md)。历史报告不能代替当前 doctor。新服务记录另含源码指纹和
子进程确认，旧记录仍按原有身份规则停止，但不能冒充本版 72 小时验收样本。

## 服务身份与停止

新的 data/run/*.pid 为 JSON：服务名、PID、FILETIME 创建时间、exe 完整路径、启动 nonce。
启动/停止/入口登记使用同一服务文件锁，旧入口退出只清理自己的 nonce。Omni 服务直接
用当前 Python 启动模块，减少 uv/入口包装层；保持后台无窗口运行。

停止先核验身份。Omni 服务写入绑定本次启动的 .stop 请求，最多等待 12 秒协作退出；
超时再核验并终止已观察的进程树。NapCat 直接走核验后的终止流程。Windows 查询和终止
使用同一保留句柄，防止 PID 重用；后代创建时间/身份不可核验则中止，部分停止或新生
后代会报告残留，不宣称成功。默认测试全部使用假终止器。

旧裸 PID 和仅由端口发现的外部进程只显示状态，停止按钮禁用。首次升级需要用户确认
并从原启动终端/任务管理器关闭旧进程，核对结束后清理旧记录，再从新工作台启动登记。
不会自动“认领”旧 PID。macOS 尚无可信服务终止后端，不宣称已支持。

手动隔离验收：

```powershell
uv run --no-sync python scripts/manual_process_smoke.py --run --output <新报告文件>
```

只创建并回收短寿命 Python 父子进程，不访问常驻服务。2026-10-28 的
[父子进程证据](evidence/process-tree-2026-10-28.json) 确认两者退出；真实 NapCat/QQ
升级和多次启停仍需在用户扫码条件下验收。

## 备份与恢复

```powershell
uv run --no-sync python -m omni.system.backup --to <新备份目录>
uv run --no-sync python -m omni.system.backup --restore <备份目录> --into <新恢复目录>
```

工作台可“创建备份（含私人记忆）”，输出到注入运行根的 backups/日期-随机标识。
支持 --data-dir、--config、--skills、--memory-db；独立 db_path 不再漏备份。
覆盖 SQLite、配置、常驻笔记、技能、修订历史、文件回滚历史与产物。

- SQLite Connection.backup 使用只读源连接、有界进度与 integrity_check；源和目标
  相同/目标已有文件直接拒绝，绝不先删原库。在线每个库分别一致；需要跨库/技能同一
  业务时点的恢复，应停止所有写入入口后备份，不能把在线快照当成全局事务。
- 默认配置去除密钥、认证头、MCP 环境变量/启动参数、URL 凭据与查询部分。恢复后
  重新配置这些值；--include-credentials 仅在显式调用时保留配置凭据。
  .env、B站 Cookie、浏览器 profile、日志/缓存不打包；备份本身含私人记忆，需妥善保管。
- 恢复只接受新/空目录，不再支持 --overwrite；严格限定清单路径与类型，拒绝重复、
  穿越、ADS、symlink/junction。先验证所有文件/目录的大小和 sha256、数据库版本与
  完整性，再写目标；恢复后再核验。未来版本 state.db 拒绝恢复。
- BACKUP_IN_PROGRESS / RESTORE_IN_PROGRESS 表示未完成；失败目录不可用于启动。
  成功恢复产生 RESTORED.json。文件在恢复中变化会失败并保留临时目录供核查，不能
  将其当作成功恢复。恢复不会切换真实数据或启动服务。

## 关闭与剩余验收

Runtime.close 有总等待上限：停止接收新回合、取消运行和审批、并行等待 review、
检索、MCP、浏览器；仅在全部收尾后关闭 DB。残留返回明确组件名，保留 DB 与启动
记录，支持稍后再核验。工作台另回收终端池；阻塞读不再持有写锁。终止失败的终端仍
占用池名额并报告残留，不能用清零计数假装回收成功。

真实登录浏览器、持续运行下的异常关闭与迁移、账号服务反复启停、72 小时常驻留给
D27。证据中的临时 Python 进程成功不等于这些场景已经通过。

实现依据：[GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes)、
[PROCESSENTRY32W](https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/ns-tlhelp32-processentry32w)、
[TerminateProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)。
