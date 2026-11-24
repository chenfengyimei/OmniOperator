# V2 开发部署

当前 Windows 离线候选默认入口为 V2，显式 V1 回退保留；整个 V2 仍在开发。最新部署状态以 [交接记录](../../docs/v/HANDOFF.md) 为准，下文旧批次素材保留历史范围，不能据其“尚未接入”推断当前代码缺失。

工作台“桌面能力”首批支持显式能力检查、短时窗口元数据与身份复核；原生库只在受管辅助进程执行，默认启动不扫描桌面。Windows仅当前用户交互会话，Mac/Linux适配未接入时明确不可用；只有显式读取所选窗口时才返回有限UIA内容，密码控件不读取；不截图、不发送输入。权限、视图释放及后续原生适配边界见[桌面合同](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/contracts/V2_DESKTOP.md)。

常驻服务提供 `service capture / plan / materialize / verify / status` 生成独立部署包与 Windows 登录任务、LaunchAgent/LaunchDaemon、systemd 模板；这些命令仅准备和只读核验，源码安装只能预览。另有显式批准的注册、启停及 prepared release 用户交换命令，见[部署合同](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/operations/V2_SERVICES.md)、[服务控制](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/operations/V2_SERVICE_CONTROL.md)和[交换合同](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/operations/V2_SERVICE_EXCHANGE.md)。真实安装、原生管理器及升级恢复须另行验收，默认入口仍为 V1。

所有者终端首批使用Windows原生ConPTY或POSIX PTY，不依赖桌面会话；V2不需要pywinpty，现有`terminal`可选包仍供V1使用。当前Windows已有隔离的中文输入、尺寸、退出与Job回收样例证据；macOS/Linux实际运行、其他Shell和长期常驻验收仍待进行，不能据源码或类型检查宣称已支持。详细权限、回执及恢复边界见[终端合同](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/contracts/V2_TERMINALS.md)。

在独立测试Agent配置明确工作目录后，进入“所有者终端”，选择服务端列出的Shell并确认启动权限。显式连接后用输入框发送临时目录测试命令，分别核对输入回执、实际输出和进程状态。切换分区不会关闭Shell；结束必须使用显式回收，并刷新确认`exited`且无`cleanup_pending`。响应不明时只查询原操作回执，不重新发送旧输入；恢复不会重新执行终端命令。

在新 checkout 与独立环境中使用 `uv sync --frozen`，随后运行：

```sh
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance init
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance serve
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance auth
```

在 `http://127.0.0.1:8766` 输入本机 `auth` 命令输出的令牌。请勿将令牌放进URL、截图或公开日志。
Windows路径同样使用显式的绝对新目录；默认V2数据目录与现有V1数据隔离。

当前令牌有效期30天；令牌续期、原生平台服务安装和不可变版本切换仍属于未交付项。
无桌面环境保存模型凭据时，使用 `--master-key-file` 指向实例目录之外、仅当前用户可读的Fernet密钥文件。
密钥由部署者独立保管；程序不会自动在密文旁边生成明文主密钥。

容器开发入口现为[独立交付说明](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/operations/V2_CONTAINER.md)：非 root 私有卷、只读根文件系统、显式 HTTPS 反代 Compose 和断网新实例核验工具已经接源码。配方默认仅监听容器内回环，远程监听仍须明确 origin。服务端 Chromium、模型、密钥与原生设备均须另外显式准备；镜像构建、运行与其他平台真实验收仍未完成，不能将模板或控制器专项当成容器已交付。

2026-11-15 只读复核：Docker CLI 已安装，但 Docker 引擎不可连接；本轮未启动 Docker Desktop 或构建镜像。当前配方和待验收边界见[打包状态](../../docs/v/reference/V2_PACKAGING.md)。

离线备份：停止该V2实例后执行 `... --home /absolute/new-instance backup /absolute/new-backup`。
恢复：`... restore /absolute/new-backup /absolute/new-restored-instance`。目标必须是新目录。
恢复会撤销备份中的登录令牌，不恢复凭据、浏览器登录态、缓存和诊断日志。

对象维护要求V2实例已停止，不会自动停止现有服务：

```sh
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance objects check
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance objects gc-plan /absolute/new-collection-plan.json
# 核对候选、引用保护和指纹后，应用同一份计划；中断后使用同一命令重试
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance objects gc-apply /absolute/new-collection-plan.json
```

新工作台的文件入口支持16MiB以内文件，上传默认固定保留。取消固定后，过期且无引用的文件才会进入停机回收候选；上传文件当前不自动作为模型附件。doctor会报告对象缺失/损坏及未完成回收，不能将残留当作正常清理完成。Alembic升级前快照保存在data/schema-backups中；恢复仍进入新目录。

人物与档案的开发验收可在上述独立实例进行：

1. 连接后选择“人物与档案”，核对所选Agent的所有者；分别保存档案与常驻正文，查看修订号和修订来源。
2. 修改正文但不保存，切换页面再返回，核对草稿仍在。另一个窗口修改同一文档后，旧草稿保存应提示冲突并保留正文；“放弃草稿并重载”才读取新版本。
3. 在文件页上传UTF-8文本，在人物页选择该文件并“导入并保存”。未保存草稿会阻止导入；导出读取已保存正文，来源文件不是另一份可同时编辑的档案。
4. 创建普通人物、人工关联账号；重复关联同一账号到另一人物应拒绝，不自动转移。人工关联不授予所有者权限。
5. 清空文档并保存，核对其已遗忘；普通人物勾选确认后可连同账号、记忆一起遗忘，所有者人物不允许删除。来源文件或消息本体仍保留，文件解除引用后按固定和回收策略处理。

所有者档案与常驻内容供本Agent的新运行使用，普通人物档案不会自动注入。运行详情中的上下文登记只说明捕获了哪些修订；已发出的模型请求无法撤回。遗忘后相关活跃运行会以context_invalidated停止，已返回但尚未发布的旧回复不会保存为成功。真实模型与通道验收仍须另行记录。

摘要与上下文的开发验收：

1. 在独立测试会话累积足够历史，继续对话后核对“上下文登记”显示持久摘要，查看覆盖的输入范围。摘要是模型生成内容，来源登记不等于逐项验证事实；真实模型会产生费用，默认测试仅使用假模型。
2. 检查压缩次数、费用待核查次数及省略提示。每轮最多压缩3批，失败不会悄悄当成完整历史。超长当前输入或工具过程会明确失败，原文仍保留；当前48000字符请求上限不是供应商token窗口保证，较小窗口现由模型配置的声明容量、输出预留和计量器进一步约束；默认字节估算仍不构成供应商token保证。
3. 从对话页选择“遗忘消息与派生内容”，先取消，再确认。确认会删除指定原文与附件关系，清除关联派生事实和摘要，停止使用受影响会话的已有历史上下文；其他聊天原文仍可查看。新消息从新的范围继续，界面显示删除边界。
4. 遗忘一个已被检索的人工事实，核对相关活跃运行停止。已经发出的模型请求与外部动作不能撤回；返回费用照常结算，unknown继续保留，不能因为删除就再次发送。
5. 停止该V2实例后备份，恢复至全新目录，再核对摘要引用和删除边界。旧令牌仍按恢复合同撤销。不要为此停止正在使用的V1服务。

运行查询`GET /api/v1/agents/<agent>/runs/<run>/context`提供来源快照、省略原因和最多3次压缩尝试（含费用状态）；摘要查询`GET /api/v1/agents/<agent>/summaries/<summary>`需要所有者认证。结构化诊断日志不包含正文，审计与业务库保存关联编号和用量。

配置与模型预算的开发验收：

1. 连接后选择“设置”，在“配置目录/模型”填写模型标识、凭据引用、声明容量和输出上限，保存配置；上方Agent偏好选择该模型后保存设置。凭据值通过单独凭据入口保存，不能写入普通配置。
2. 为费用与真实接口准备明确测试预算后，再人工发起模型验证。本批离线验证不发真实请求，保存模型配置也不会探测供应商。要求精确计量但没有匹配计量器时，显示不可用并拒绝发送。
3. 在另一窗口保存同一配置，原窗口旧修订保存应冲突并保留草稿。查看修订历史，导出应得到已保存版本。草稿只在当前网页内存保留，刷新前请保存或导出已保存版本。
4. 导入schema=1的UTF-8 JSON（最多256KiB）仅生成配置草稿；保存后才进入目录。人设预设的“应用”仅填入Agent设置草稿，再保存设置才生效。
5. 停用模型后，已有运行不能继续用旧授权请求；重新启用供新回合使用。普通模型参数编辑供新运行使用，历史运行可从`/api/v1/agents/<agent>/runs/<run>/configuration`审阅确定修订。
6. 通道/MCP只保存类型化配置，能力诊断明确尚未连接；不要据此认为QQ、远程MCP或OAuth已通过真实验收。

来源文档与派生检索的开发验收：

1. 进入“来源文档”，填写标题和UTF-8正文后保存，修改后再次保存，查看旧修订（只读）。同一文档另一窗口保存后，旧草稿应冲突并保留；导出始终读取已保存版本。
2. 文件导入只生成网页草稿，保存后才建立来源修订与索引。最大256KiB/100000字符；草稿只在当前网页内存保留。
3. 检索应显示来源修订与字符范围。默认没有向量组件，明确使用全文；重建全文从原始对象验证后替换分块，旧向量失效。
4. 本批仅提供LocalVectorizer注入端口（版本、1–512维、异步embed/close），尚无随包本地向量适配器或安装界面。不得把假向量专项当作真实语义模型验收；端口不允许网络或计费。其最大512候选扫描不是全库ANN。
5. 删除先取消、再确认。核对相关事实和摘要失效，旧工具结果脱敏、在途回复不保存，原始会话和独立固定文件仍按各自合同保留。失败的清理请求可在文档索引状态/审计中查看，再次删除重试。
6. 停止本V2实例后执行下面命令；命令要求独占锁，不会替你停止服务。不要为测试停止V1或其它用户服务。

```sh
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance indexes check
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance indexes rebuild
```

`check`比较FTS与内容表，在回滚事务中完成，不保存维护变更；`rebuild`原子重建记忆/文档FTS并记录审计，不改原文或向量。它不是原始对象完整性验证或分块修复的替代品；原始文件仍由objects check/doctor检查，分块修复从来源文档页执行。进程中断时不能提交半个重建结果。doctor遇到未完成清理或构建会报告非零状态。


持久调度的开发验收：

1. 在“持久调度”新建草稿，默认停用。支持五段数字日历规则、UTC秒间隔、带时区的单次时间；保存后建立专用会话。启用会自动使用本空间当前模型与费用预算，工具写入仍按原审批路径处理。真实供应商验收须使用指定测试账号和预算；离线假模型通过不等于真实模型通过。
2. 核对IANA时区与下次UTC。夏令时缺失分钟跳过，重复分钟只执行第一次；日和周均受限时采用OR。时区数据采用随依赖锁定的tzdata，版本变更后需重新保存计划，不能静默改变将要执行的时间。
3. 停机后跨过多个周期，再启动同一隔离实例：仅登记最近一个尚未登记的周期，账本显示合并起点。修改定义不改变已登记指令；停用/归档只取消尚未入队记录，已入队或执行中的运行请单独取消。勾选“包含归档计划”可查看归档定义和到期账本；定义只读，在途回合仍可取消。
4. 在到期账本查看运行身份与状态。上一轮尚未结束或有未知费用/动作/投递时，后续工作应被阻塞。核查时确认对应记录身份与实际结果，再明确允许后续调度；旧运行不会重试，未知费用不会释放，未知结果不会被改成成功。同一运行后来新增未知记录应再次阻塞。
5. 检验崩溃恢复：尚未执行且没有外部意图的领取回到队列，已开始的回合变为中断；过期租约不得发布迟到答复。离线自动回归覆盖这些边界，真实睡眠恢复、模型与设备副作用仍需实机验收。
6. 备份是某个时点的业务快照，不能证明备份后外部账号上发生了什么。恢复后的自动调度、排队工作和待投递内容需要先隔离并重新核查，不能凭旧快照重新发送；费用与外部账号记录需单独对账。恢复保护的最终实现和验证证据见当前调度记录。

持久投递工作器已实现，真实发送适配器、真实本地向量组件及完整三平台原生桌面仍待接入。默认入口继续为V1。

运行保留预览（预览本身只读，实际裁剪须另审计划并验证备份）：

```sh
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance retention-preview --limit 20
# 有下一页时用输出的 next_after 继续，最大 limit=50
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance retention-preview --after 完整运行UUID --limit 20
```

该命令要求实例停止并取得独占锁，不自动停止服务或升级数据库。工作台对话区可固定本次运行；固定只保护执行正文保留，不改变显式遗忘或费用状态。输出不含聊天和工具正文，can_apply=false，不能作为删除计划执行。计划、备份后逐运行裁剪、断点续跑、显式取消与新目录恢复见[执行正文保留合同](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/contracts/V2_EXECUTION_RETENTION.md)。

已裁剪动作的产物引用维护（R3.retention_references，批次基线71表/v2_0013）：

```sh
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance retention-refs-preview --limit 20
# 按 next_after 继续时增加 --after 完整引用UUID；每页最多50条引用
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance retention-refs-plan /absolute/new-reference-plan.json --limit 20
# 审核精确引用/动作/对象、版本、SHA-256、大小和原裁剪批次后执行
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance retention-refs-apply /absolute/new-reference-plan.json --backup /absolute/new-reference-backup
# 需要取消尚未提交目标时执行；已释放引用不会重建
uv run --no-sync python -m omni.composition.cli --home /absolute/new-instance retention-refs-cancel /absolute/new-reference-plan.json --reason "保留剩余引用，重新审核"
```

只释放原正文批次已完成、裁剪标记一致且没有业务保护的精确 action_objects 关系。新增来源、未知费用、审批、资源或对象固定可使计划失效；其他消息、文档、记忆、技能及动作引用保持。命令不删除对象或改到期时间。释放成功后须另生成并审核新的 `objects gc-plan`，再执行 `objects gc-apply`；固定、未到期或仍有其他引用的共享对象不会进入回收。

应用前创建并验证实例外的新快照。中断使用原计划与原绑定备份位置继续；已经释放的目标即使经过 GC 也可依据历史完成续跑，不重复释放审计。取消只撤销 pending，不恢复已释放引用。引用备份中正文已经裁剪，恢复至新目录只能找回释放前引用和对象；需要原文须恢复独立的正文裁剪前备份。doctor 单独报告未完成引用批次与 GC 残留。180天摘要/审计、完整业务、原生适配及三平台真实/72小时验收仍待推进，整个 V2 未完成，默认入口仍为 V1。

持久历史游标基础（R3.retention_audit.cursors，当前74表/v2_0014）：

- 工作台“审计记录”可重新加载或继续加载当前 Agent 的记录；“诊断日志”中的实例维护审计独立分页。列表最多保留最近读取的 200 条，未知类型显示通用维护标签。
- 历史因删除或备份恢复而使游标失效时，界面明确提示并清空旧记录；点击重新加载建立新查询。新查询仍提示部分历史已清理，不能把空页视为完整历史。
- HTTP 的 Agent 审计、运行事件和实例审计均使用 cursor/limit，默认100、最多200条。响应为 `items/next_cursor/has_more/stream/history_trimmed`；stream 给出高水位、清理修订、累计删除数和最后清理时间。初次省略 cursor，后续原样传 next_cursor；这三个接口拒绝旧 after 参数（422），错作用域为422，过期为410 `history_cursor_expired`。

```text
GET /api/v1/agents/{agent_id}/audit?limit=100
GET /api/v1/agents/{agent_id}/runs/{run_id}/events?limit=100
GET /api/v1/system/audit?limit=100
```

上述接口仍要求所有者认证。普通重启保留历史 epoch；验证备份恢复到新目录时，在业务隔离的同一事务更换 epoch，使旧游标失效，分配高水位不由剩余行重算。事件清理作用域为整个 Agent，运行筛选不缩小失效范围。180 天审计和事件可通过下述独占离线计划执行，V2 持久 WS/SSE 入口仍待开发。这不改变 V1 默认入口或三平台待验收状态。

180 天 Agent 审计只读预览已接入工作台“诊断日志”和以下入口：

```text
GET /api/v1/agents/{agent_id}/audit/retention-preview?limit=20
uv run --no-sync python -m omni.composition.cli --home <独立V2目录> retention-audit-preview --agent <Agent UUID> --limit 20
```

HTTP 要求所有者认证；离线 CLI 取得独占锁，在线实例拒绝。默认 20、上限 50 条，后续传原样 next_cursor，可传响应 as_of；刷新不传游标以建立新截面。截面固定时间和记录上界，保护条件每页重新查询；counts 仅计本页，can_apply=false。工作台最多显示 200 条，410 清空后显式刷新。仅返回审计/主体元数据和保护原因，不导出 data，不构造删除计划。详见[审计保留设计](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/contracts/V2_AUDIT_RETENTION_DESIGN.md)。

独立审计清理命令（同样使用上面的 CLI 前缀）：

```text
retention-audit-plan <实例外新计划.json> --agent <Agent UUID> --limit 20
retention-audit-apply <原计划.json> --backup <实例外新备份目录>
retention-audit-cancel <原计划.json> --reason <取消依据>
```

先审核计划，再执行；已有备份不当作新的首轮备份复用，续跑绑定原备份及指纹。取消只解除未提交目标；恢复原审计使用清理前的验证备份进入新目录，恢复会撤销旧计划和游标。doctor 与工作台对待收尾审计批次显示状态，诊断不包含备份路径或审计原文。当前开发、专项及全仓验收分别记录，不能据命令存在宣称完整V2或三平台已交付。

78表/v2_0016 增加事件明细的独立只读预览与离线清理（使用相同 CLI 前缀）：

```text
GET /api/v1/agents/{agent_id}/events/retention-preview?limit=20
retention-events-preview --agent <Agent UUID> --limit 20
retention-events-plan <实例外新计划.json> --agent <Agent UUID> --limit 20
retention-events-apply <原计划.json> --backup <实例外新备份目录>
retention-events-cancel <原计划.json> --reason <取消依据>
```

首版按九种明确事件及来源/正文/未知结果保护，不清除恢复 interrupted 或未知种类；终态运行的历史进度不重新编号。删空后也保留 last_seq、原裁剪指纹、运行/消息/去重/费用身份。审计与事件同 Agent prepared 计划互斥；引用释放与事件维护互相保护，事件删行后未执行旧引用计划须重新审查。工作台“诊断日志”提供独立事件预览，显示全局 sequence 和运行内 seq，最多200条，410清空后显式刷新。事件删除推进 events 清理修订，维护审计写 audit；恢复同时撤销旧事件计划和游标。详见[合同](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/contracts/V2_AUDIT_RETENTION_DESIGN.md)。
## 开发版可信 Python 插件

在部署机器准备独立目录，至少包含 `extension.json` 与入口 `plugin.py`。在 V2 工作台的“可信插件”中填写该目录的绝对路径登记，再手动选择工具、核查包和目录指纹后授权。登记与授权不加载代码；工具在新运行中出现，仍须逐次人工审批。原目录改变不会修改已捕获版本，更新需重新登记到原安装槽并重新授权。

最小 `extension.json`：

```json
{"format":"omni-python-extension-1","id":"example","version":"1","entry":"plugin.py","tools":[{"name":"echo","description":"Return the approved text","parameters":{"type":"object","properties":{"value":{"type":"string","maxLength":1000}},"required":["value"],"additionalProperties":false},"writes":true,"timeout_s":30}]}
```

入口仅需提供同步或异步 `invoke(local_name, arguments, context)`，返回结构化结果：

```python
async def invoke(local_name, arguments, context):
    return {
        "status": "ok", "text": arguments["value"], "effect": "none",
        "error_code": "", "retryable": False, "artifacts": [],
        "verification": "", "data": {},
    }
```

示例向模型注册为 `ext__example__echo`。首批全部插件工具必须声明 `writes: true`，包括该示例，以覆盖入口加载的潜在副作用；支持的组合工具名最长64字符。包最多64个普通单链接文件、单文件1 MiB、总量8 MiB，清单最多64 KiB，单工具参数schema规范UTF-8最多16 KiB。拒绝路径穿越、链接、大小写冲突、启动钩子和私人运行文件，不自动安装依赖；首批没有宿主模型/凭据/数据库回调或文件产物协议。

插件是所有者主动信任、具备当前用户权限的Python代码，进程隔离不构成操作系统安全沙箱。禁用先撤权，再查询实际回收状态；若仍有残留，可用同一已禁用槽的当前revision再次核查回收。派发后崩溃或取消保留unknown，不自动重发。备份保留源码对象，缓存可重建；恢复会撤销授权，需要重新核查。三平台真实安装、原生能力与完整V2验收仍待推进。
