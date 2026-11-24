# 本地 Agent 验收与发布准备（D27）

当前属于**受控内测**。本轮交付副本恢复演练、只读常驻采样、证据校验与操作手册；
没有完成 72 小时常驻，也没有发布稳定版本。全部命令从仓库根目录执行。
对外发布必须在具体版本和证据齐备后由所有者明确批准；提交开发代码不等于发布。

## 1 本轮已经取得的证据

- [D27 独立环境回归](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/clean-windows-d27.json)：2026-10-29，精确暂存源码导出、
  全新 Python 3.12.14 venv、锁定依赖离线安装，817 项零失败/零跳过；24 场景预热后
  3 轮全通过。真实模型调用为 0，不表示实际模型任务率或长时间稳定性。
- [真实数据副本恢复](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/restore-rehearsal-2026-10-28.json)：备份时现场 state 已是 v13，
  两次打开恢复副本均为 v13、无降级、两库完整性通过；16 个会话、42 条消息、6 条事实、
  3 条档案逐行保持。任务/执行/预算原表为空，不能据此证明非空任务迁移。
  v9→v13 的非空任务、执行账本、预算保持由 `tests/test_acceptance.py` 独立验证。
- [短时只读采样](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/soak-preflight-2026-10-28.json)：不足 72 小时、缺少预热基线、
  服务身份未核验、没有实际定时执行增量，**明确未通过**。没有开启真实任务或发送消息。
- [临时进程回收](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/evidence/process-tree-d27-2026-10-28.json)：仅脚本创建的 Python 父子进程，
  两者身份与退出均确认。不是 NapCat/QQ/浏览器长期运行验收。

以上是采集时工作树的历史证据，不自动转为新版本的发布凭证。当前另有并行前端修改，
必须在正式验收前固定最终源码、配置与依赖。源数据没有由演练脚本修改，服务没有被切换。
备份副本留在 `.pytest_cache/upgrade-live-v9-d27/`（目录名沿用预定计划；报告实际为 v13），
含私人记忆，禁止提交整个目录。仓库只保存脱敏报告。

## 2 先固定版本并验证恢复

1. 读 HANDOFF、ARCHITECTURE 和开发台账，确认当前差异归属。固定待验收版本，记录 `git rev-parse HEAD`。
   运行 `uv run --no-sync pytest -q --junitxml=<私有证据目录>/pytest.xml`，保留完整输出。
   不把旧提交的全绿报告移植到新版本。
2. 执行只读 doctor；按 [运维手册](OPERATIONS.md) 处理旧 PID，核验旧启动入口后再关闭，
   不通过裸 PID 或端口认领进程。维护窗口内确认各写入入口已退出后另做升级备份。
3. 先做副本演练，指定一个不存在、且在真实 data 目录之外的路径：

```powershell
uv run --no-sync python scripts/rehearse_upgrade.py --run --output D:\omni-private-evidence\restore-check
```

支持 `--data-dir`、`--config`、`--skills`、`--memory-db`。脚本使用 SQLite 一致快照、恢复到新目录，
只在副本中经 bootstrap 构造 Runtime；关闭 review/向量后台 worker，使用 DenyApprover，
不运行 Agent、MCP 工具、调度器或模型。默认不复制凭据、Cookie 和浏览器 profile。
检查 report.json 的 passed、原版本、两次打开、关键表保持与两库完整性。
两个在线库各自一致，不保证跨库/技能处于同一个业务时点；严格切换前须停写重备份。

4. 实际切换由所有者在维护窗口执行。备份与旧代码保留；失败时用匹配的旧代码与完整旧备份
   恢复到新目录，不能让旧程序写入 v13 库，也不能把旧备份覆盖进正被使用的 data。
   登录状态没有打包，须重新扫码或配置。新 QQ/心跳服务启动后登记创建时间、exe、nonce、
   源码指纹，并由子进程确认源码；只有这些记录才能计入本轮常驻验收。

## 3 30 分钟预热与 72 小时采样

采样器是独立的只读观察程序。它不会启动/停止服务、改变模型预算、调用模型或发消息。
先配置并核查真实运行实例的预算、白名单和授权，采样器的费用上限**只是验收判据，不是扣费拦截器**。
示例上限不是花费授权，也不是已经验证的资源阈值。

```powershell
# 先复制并根据本机条件、已授权预算修改示例；目标目录必须事先建立。
Copy-Item docs/v/reference/soak-limits.example.json D:\omni-private-evidence\warmup-limits.json
uv run --no-sync python scripts/soak_monitor.py run --output D:\omni-private-evidence\warmup --limits D:\omni-private-evidence\warmup-limits.json --hours 0.51 --interval 60
uv run --no-sync python scripts/soak_monitor.py calibrate --output D:\omni-private-evidence\warmup --limits D:\omni-private-evidence\limits.json
# 检查生成阈值；保留整份 warmup 证据目录，正式采样每次会核验它。
uv run --no-sync python scripts/soak_monitor.py run --output D:\omni-private-evidence\soak --limits D:\omni-private-evidence\limits.json --hours 72 --interval 60
uv run --no-sync python scripts/soak_monitor.py report --output D:\omni-private-evidence\soak
```

预热至少 30 个样本、覆盖 30 分钟、同源码/同数据目录且 QQ/心跳身份连续可核验。
生成的私有内存与句柄阈值取预热峰值的 1.5 倍，再加 128 MiB/128 句柄余量。
私有内存增长、费用和日志上限沿用事先设置的值。需要修改阈值必须在正式 campaign 开始前完成，
不得为了消除超标事后改清单或删坏样本。费用单位为百万分之一美元。

默认观察 `omni-qq`、`omni-heartbeat` 的根进程和当时可核验的后代：私有内存、工作集、
句柄、累计 CPU；只读查询数据库完整性、版本、运行/事件/定时执行/模型调用数量、费用、
发布与投递 unknown；统计 system_logs 直接文件总量。报告不含对话正文或模型参数。
Windows 资源含义依据 [GetProcessMemoryInfo](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo)
与 [PROCESS_MEMORY_COUNTERS_EX](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters_ex)。

每条样本有哈希链，绑定初始清单。检查使用单调时间累计，墙钟只用于发现跳变：
间隔超过约定的 2.5 倍、时钟差超过 30 秒、源码变化、服务身份不明、账本计数回退、
已观察旧进程仍残留、资源超标、最终仍有未知投递/发布、期间费用未知都会阻止通过。
只有空闲样本、没有 run 和定时执行增量也不通过。暂停/重启机器不能用修改墙钟补足时长。
Ctrl+C 只停止采样；`--resume` 可续写证据，但超长空档不会被抹除。坏链保留调查，另开新 campaign。

边界：采样不是操作系统审计或防篡改签名；有本机写权限的人可重造证据，因此仍须具名人工验收。
60 秒采样不能证明间隔内从未出现短命进程；Web、NapCat 外部进程、独立终端、整机资源
不在两个服务树的保证范围。其关闭和残留需在故障场景单独验证。
目前闸门采用严格连续标准：若故障演练造成服务漏采，保留失败 campaign，修复后另跑完整 72 小时。
常驻时安排已经授权的小范围真实任务，不能为了增加计数而自动群发或制造付费请求。

## 4 必须逐项完成的真实场景

将 `docs/v/reference/acceptance-manifest.example.json` 复制到私有证据目录。每条填实际时间（带时区）、
具名验收者、同版源码指纹、相对证据路径和 SHA256；状态没有通过就保持 pending。
源码指纹可从固定版本新建的 campaign.json 取得。每份证据至少记录环境/版本、
授权范围与预算、输入目标、run_id/观察 id、动作、结果回读、失败/unknown 和人工处理。
脱敏截图不能包含 Cookie、密钥或不相关私人对话。

| ID | 必须观察到的行为 |
|---|---|
| northstar_a | 所有者 QQ 发 B站链接，有依据回复；无 Cookie/字幕如实降级；保存端到端 run 与回执 |
| northstar_b | Web 多步调查，todo/文件/网页/产物可追踪，中途取消后不启动新工具，未完成部分如实说明 |
| northstar_c | 夜间整理→早报引用具体成功 run；重启调度、掉线 outbox 补投、重复事件不重发，unknown 人工核查 |
| northstar_d | 真安卓观察→批准→动作→新观察；换设备/旋转/页面变更拒绝旧令牌，中文输入与原输入法恢复，MJPEG 锁帧 |
| northstar_e | 用户受信 MCP 生产服务读取与单次确认写；目录漂移撤销 SAFE，超时/关停无盲目重放 |
| desktop_notepad | 记事本临时文档读取、中文受控编辑、截图/回读、旧观察拒绝，保存本版闭环 |
| desktop_second_app | 所有者选定第二应用，完成支持能力读取及受监督流程；不支持的操作明确拒绝 |
| desktop_edge_cases | 双窗口/相似控件/弹窗/退出/失焦/最小化/多屏 DPI/锁屏/管理员窗口；不越界、不抢未授权焦点 |
| qq_group_isolation | 私聊及两个测试群不串历史/档案；重复/撤回/重连正确，撤销白名单、安静时段、一键停止有效 |
| bilibili_write | 用户选定测试账号/目标/内容，核验实站选择器，全文批准后单次发送和结果回读；验证码/失效登录拒绝 |
| embedding_local | 固定本地模型、版本/维度，用冻结样本报告 FTS/混合 Recall@k、MRR、延迟和内存；验证删除和 scope |
| embedding_api | 固定 API 模型与已授权预算，报告同样指标和实际费用；不可用回退 FTS，不以 mock 代替质量结果 |
| backup_restore | 本版真实数据副本恢复、完整性/关键行保持、旧版本回滚路径与登录重建；在线跨库一致性限制已确认 |
| fault_restart_cancel | 断网/限流/模型失败/取消/服务重启/浏览器与终端关闭，未知副作用不重发，无未处理残留 |
| offline_regression | 本版完整 pytest、架构守卫、固定 24 场景三轮及干净环境复现；真实模型任务质量另列，不能写成离线通过率 |

记录文件哈希示例：`Get-FileHash -Algorithm SHA256 <证据文件>`。确认本版安全/数据阻断项
全部关闭后，才将 blocking_issues 填为 `[]`。将多个真实场景合在一份报告时允许引用同一文件，
但必须在报告中分别有证据。脚本检查文件与版本完整性，无法判断人工陈述是否真实。

```powershell
uv run --no-sync python scripts/release_gate.py --manifest D:\omni-private-evidence\acceptance.json --soak D:\omni-private-evidence\soak --output D:\omni-private-evidence\release-review.json
```

未通过退出码为 1，列出阻断项。通过也只是 `ready_for_human_review=true`，
`publishing_authorized` 永远为 false。提交具体变更、回滚方法和证据给所有者后，
对外版本发布或公告仍需单独明确批准。

## 5 完成范围与真实剩余项

本地/API embedding 两种接口已交付；记事本首个真实闭环已做；安卓全动作绑定、
B站受控写、QQ 场景与可靠投递、运维恢复、离线回归均已有实现与测试。
它们不能替代上表的真实验收，更不能据此声称已达到 Hermes 的任务质量。

仍有明确的代码边界：通用桌面前台接管未实现，仅支持经验证的后台语义方法；
MCP HTTP/OAuth 后置（当前 stdio）；外部技能安装通道未开放，因此隔离安装也未实现。
托管 Jenkins 尚未接入，当前验证是本机 Windows 和独立环境执行。默认预算仍须由所有者
设置；跨进程取消、旧字符串工具结果、模型摘要质量与供应商真实账单仍按计划核验。
这些限制在扩展范围改变时必须明确处理，不能把“待真实验收”写成“所有开发计划已完成”。
