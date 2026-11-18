# V2 独立长跑观察

V2使用`python scripts/v2_soak.py`，不复用仍读取V1状态的`soak_monitor.py`。工具只观察明确指定的现有V2实例：从私有`runtime.json`和`readiness.key`核验当前HMAC启动证明，再以私有所有者令牌读取`/api/v1/system/metrics`。只发GET，不启动/停止服务，不打开业务数据库，不调用模型、设备或外发通道。

```powershell
uv run --no-sync python scripts/v2_soak.py run `
    --home D:\omni-v2-instance --url http://127.0.0.1:8766 `
    --output D:\v2-acceptance\soak-01 --limits D:\v2-acceptance\approved-limits.json
uv run --no-sync python scripts/v2_soak.py report --output D:\v2-acceptance\soak-01
```

URL必须是明确回环IP和端口，不支持URL令牌、代理环境、跳转、远程地址或DNS名称。输出必须是实例home之外的新目录；续采使用同一`--output`、`--home`、`--url`及`--resume`，沿原上限、间隔、启动身份和收集器/规则指纹。凭据和HMAC密钥不写入证据。安装后可用同版本Python环境执行随交付提供的`agent/scripts/v2_soak.py`。

所有者须提供一个至多4KiB的JSON上限文件，精确包含以下8个非负整数字段；工具不猜测本机可接受的上限：

| 字段 | 含义 |
| --- | --- |
| `main_rss_bytes` | 主进程采样RSS上限 |
| `descendant_rss_sum_bytes` | 当前后代进程RSS相加上限；共享页可能重复计数，不冒充物理峰值 |
| `threads` | 主进程线程数上限 |
| `descriptors` | Windows句柄或POSIX文件描述符数上限 |
| `children` | 后代数上限；原生盘点最多64个 |
| `cleanup_pending` | 相关组件未完成回收项与关闭残留合计上限 |
| `new_failed_runs` | 相对初始计数的新失败运行上限 |
| `new_unknown_model_calls` | 相对初始计数的新未知模型调用上限 |

默认每60秒采样，允许10至3600秒的明确间隔；默认运行72小时，最长一次168小时。只保留数字、身份及固定失败码，不保存正文、路径、响应原文或令牌。HTTP响应至多256KiB、有期限且不解压；私有证据行至多8KiB、最多65000条。证据串联前项SHA和连续序号，写入加独立文件锁并fsync；同一写者追加只缓存有文件身份约束的尾部，续采/文件变动时完整核对链。中断尾行、硬链接、替换、未知字段或坏指纹均拒绝，不截短、覆盖或自动修复旧记录。

`observational_soak_passed`要求首末覆盖至少实际72小时、单调时钟与墙钟一致、没有超过2.5个采样间隔的缺口，没有失败采样、计数回退或上限超出。旧失败、缺口和未知不会因续采而清除；仅最后一次变绿不能使旧窗口通过。注入采样器或时钟明确标记，合成72小时数据仍不能通过原生监测门槛。实例、boot、程序、schema或进程创建身份变化时记失败，不静默换观察对象。

`run`和`report`只有监测通过才返回0；有效但未通过返回2，坏输入/证据返回1，中断返回130。Ctrl+C保留证据并只结束观察器。报告始终保持`whole_v2_complete=false`、`production_release_ready=false`，不自动关闭旧unknown，也不代替业务负载、真实账号、设备、费用或原生平台验收。当前开发只做局部验证；实际72小时和全量留整个V2开发完成后执行。
