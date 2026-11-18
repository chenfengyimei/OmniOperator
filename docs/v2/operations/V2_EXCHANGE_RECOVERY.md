# V2 交换隔离、救援与在线准备

本页定义 D4 的恢复与准备合同。当前交换源码覆盖 Windows/Linux/macOS 用户目标，Mac 新增持久激活绑定；离线注入验证与真实平台验收分别记录。Mac 原生、恢复和实际登录行为仍待实机验证，默认入口仍是 V1，不自动升级或回滚真实服务。

## 明确区分结果与隔离

未知的安装、删除或启动不能当作普通失败重发。原生后置条件无法核清时，可以单独审阅离线隔离：禁止旧目录继续启动，保留原操作、未知回执和活动标记，再为新目录和新原生注册名称建立救援计划。

隔离不证明外部动作成功或没有发生，也不停止当前应用。只要源或目标还持有实际实例锁，隔离就失败；调用者须先核清并正常关闭相应实例。正在运行的交换或未回收完的管理器仍持有维护/交换锁，也不能被隔离命令越过。没有按历史 PID 强杀、删除未知记录或盲目移除服务的路径。

## 命令

开发入口为 `python -m omni.composition.cli`：

| 命令 | 行为 |
| --- | --- |
| `exchange isolation-plan EXCHANGE OUTPUT --reason TEXT` | 只读绑定中断交换的 ID/SHA、事件数量、完整历史指纹和私密理由；不封锁目录 |
| `exchange apply-isolation ISOLATION --approve SHA` | 在交换锁、维护锁和两个实例锁下封住旧源/目标；保留未知回执和旧预约 |
| `exchange rescue-plan EXCHANGE REQUEST OUTPUT` | 仅接受已隔离交换；在当前审阅的不可变安装中生成新 home、bundle、备份与 journal 的救援计划；不恢复、不注册 |
| `release prepare RESCUE --approve SHA` | 验证旧隔离见证、源指纹及快照，保存失败版本一致备份，再在新 home 恢复最新数据并隔离执行权限 |
| `exchange plan RESCUE JOURNAL OUTPUT` | 对已准备的救援版本生成新的交换计划 |
| `exchange apply NEW_EXCHANGE --approve SHA` | 保持旧源封锁，以独立注册名称安装、启动并核验新 boot；不重发旧未知操作 |
| `release online-plan SOURCE_BUNDLE REQUEST OUTPUT` | 只读核验旧安装和本次 boot，生成包含正常排空动作的版本计划 |
| `release prepare-online PLAN --approve SHA` | 先登记排空意图，只发送一次正常排空；证明对应 boot 已关闭且实际清理结束后，才进入一致备份和目标准备 |

REQUEST 与普通 release 一样，包含 `target`、`backup`、`journal`，均采用新路径。所有有副作用的命令要求批准完整计划指纹；本批开发和测试不会代为执行真实注册、排空或设备动作。

## 隔离见证与中断恢复

隔离计划 `omni-service-isolation-1` 明确 `blocks_startup=true`、`starts_service=false`、`stops_service=false`。理由只进入私密计划，不写入普通诊断日志或交换元数据事件。

交换先持久写入 `isolating`，再按既有启动闸门封住两个 home，最后登记 `isolated`。见证引用精确隔离计划和原历史；单侧闸门已落盘、另一侧尚未完成时，只能继续相同计划。普通 apply/resume/abandon 拒绝继续已隔离交换。日志预留两个隔离事件的容量，不能因重复只读核查耗尽隔离所需空间。

旧活动标记、启动领取、操作日志和交换预约全部保留。救援不占用旧 home 的维护身份，也不将 unknown 结清成成功；它在旧隔离见证、两份锁和唯一注册名称的共同约束下使用新 home。

## 救援保留最新数据与费用

救援计划将原 prepared 快照作为验证锚点，并另外绑定失败目录当前 DB/WAL/journal 指纹。执行前重新比较，发生变化则拒绝继续，不静默更换恢复来源。

救援的 `restore_policy=latest_failed`：先创建并验证失败目录的最新一致快照，以该快照恢复新目录。消息、现有用量、费用预留、未知调用和未知投递继续保留；恢复后的执行、授权、凭据和定时任务仍按共同隔离规则处理。原升级前快照、失败目录和新备份都保留，凭据密钥不自动复制。

普通 `release recovery-plan` 的 `original_snapshot` 只有在失败目录仍匹配原 prepared 目标指纹时才能使用；一旦发生新数据写入，执行拒绝，不能用旧账覆盖新费用或未知动作。此时应审阅保留最新数据的救援方案。两种策略均不进行跨 schema 数据兼容。

救援目标的原生名称加入完整部署 UUID。原生管理器的旧未知请求即使迟到，也只针对旧名称；旧 home 已被启动闸门封住。后续版本继续使用独立名称，防止重新占用仍有不确定性的稳定名称。旧注册现有独立的[显式核查与清理](V2_SERVICE_CLEANUP.md)入口；它保留原未知记录，成功只表示对应时间观察到缺失，不因新版本就绪而假报已清理。

同一隔离源只能放行一个救援版本。选择时在源实例锁下无覆盖发布 `run/rescue-selection.json`，绑定 release ID/SHA 和目标部署 SHA；开放目标前再次核验。其他计划即使已准备，也不能再开放第二个 home；相同计划的中断恢复仍可继续。该见证与原启动闸门共同保留，不按日志年龄清理。

## 在线排空与版本准备

计划将旧部署、stop 操作及精确 boot 共同纳入 SHA，明确 `stops_service=true`。普通离线 prepare 不能绕过未完成的排空记录。

顺序为 `drain_intent → drained → intent → snapshot → restored → prepared`。未知结果记录 `drain_unknown`，不拷贝、不创建目标业务库、不再次 POST；之后相同命令只观察原操作。即使子意图尚未写入，也不能据此补发排空；仅同 boot 的关闭元数据与实际锁释放可作为后置证明。

外层准备任务和内层控制任务都保留实际归属，取消等待不取消已派发的正常关闭；回收未完成时保留准备互斥。只有实际关闭完成后才做备份。若源在准备前或之后自行重启、数据或源码发生变化，实例锁及连续性检查拒绝推进，不能沿用旧快照宣称更新成功。

排空阶段未完成、准备中断或权限不可用时保留记录供核查；不自动重启旧服务，不覆盖部分目标，不重复发送。Mac 登录激活绑定、Linux 系统交换/启动及隔离定义清理已接线；系统交换使用完整计划批准的独立 root 会话，见[系统交换合同](V2_SYSTEM_SERVICE_EXCHANGE.md)。macOS 系统授权、跨管理员协议、跨 schema、真实服务安装与故障恢复、三平台常驻验收仍待推进。

## 文件合同版本

服务计划与 release 计划为 `omni-service-plan-2`、`omni-release-plan-3`，当前交换为 `omni-service-exchange-3`，预约包含可校验的激活绑定。服务计划指纹包含 `isolated_registration`；release 明确 `source_stop`、恢复策略、源指纹及隔离授权。旧计划不补默认值、不进入执行；需从实际安装重新 capture、plan、materialize，并重新生成对应管理员包。运行描述仍是 `omni-service-runtime-2`，数据库仍为 96 表 / v2_0026。
