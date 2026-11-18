# V2 不可变版本准备、选择与恢复

本页描述 D4 的版本准备合同，不代表自动更新、真实服务切换或三平台验收完成。正式入口仍为 V1；开发入口为 `python -m omni.composition.cli release`。离线命令不提权、注册或启停；单独批准的 online-plan / prepare-online 可以正常关闭已证明身份的旧 boot，然后进入准备，不调用模型。

## 当前可执行范围

两个程序版本分别安装在独立、非 editable 的目录与 Python 环境中。新版本从旧部署包读取固定计划，核验旧程序、解释器链接链及环境配置文件；新版本自身必须与当前完整 capture 一致。对旧环境的静态文件核验不等于在旧解释器下验证全部依赖；普通 recovery-plan 回到原不可变安装执行，隔离救援则使用重新审阅的独立安装。当前计划为 `omni-release-plan-3`，完整声明 source_stop、恢复策略、源指纹和隔离授权；旧计划需重新生成。

要求相同实例 ID、平台、服务目标和所有者；同 schema 直接准备，跨 schema 必须携带完整迁移路线。当前V25起的前向准备、新目录迁移和严格旧快照恢复已接源码，见[结构升级合同](V2_SCHEMA_UPGRADE.md)。两份安装必须包含内容一致的 `domain/release_fence.py`、`adapters/platform/runtime/paths.py`、`domain/service_activation.py` 和 `adapters/platform/service_launch.py`，以证明共同采用目录闸门与交换激活消费者。新增合同后须重新构建并捕获两份固定安装；没有该合同的历史 V2 安装、闸门实现变化或未批准的跨 schema 升级会被拒绝，不能据当前实现宣称任意版本均可升级。完整跨 schema 服务协调、跨管理员版本与闸门协议演进仍待扩展。

旧 home、新 home、备份、维护日志、部署包、程序与环境目录不得重叠；新 home、备份、日志和目标部署包均不能覆盖既有内容。工作目录和凭据引用仍由已审阅的服务请求显式指定。整个过程保留旧 home 的数据库、对象和凭据，不把数据文件搬入程序目录。

| 命令 | 行为 | 系统服务影响 |
| --- | --- | --- |
| `plan SOURCE_BUNDLE REQUEST OUTPUT` | 从目标安装捕获当前运行环境，生成固定新旧部署、路径、步骤合同及 SHA 的计划 | 无 |
| `online-plan SOURCE_BUNDLE REQUEST OUTPUT` | 只读核验旧 boot，产生包含正常排空动作的完整版本计划 | 不关闭；计划明确 stops_service=true |
| `prepare-online PLAN --approve SHA` | 持久登记排空，再证明对应 boot 已关闭、资源已回收，最后进入一致备份和准备 | 仅一次正常排空；未知只核查，不重发 |
| `prepare PLAN --approve SHA` | 取得两份 home 的实例锁；一致快照、对象验证、恢复到新 home、生成并验证目标部署包 | 无；新 home 保持禁止启动 |
| `inspect PLAN` | 返回有界元数据历史及最后阶段；不查询管理器或模型 | 无；ready 始终为 false |
| `select PLAN --approve SHA` | 核验数据连续性、目标及旧服务确实缺失；退役旧 home，再开放新 home | 只查询；不会移除旧服务或安装新服务 |
| `abandon PLAN --approve SHA` | 放弃尚未进入选择的暂存工作；保留全部文件及目标启动闸门 | 无；已选择或选择中断不能借此重新开放旧 home |
| `recovery-plan ORIGINAL_PLAN REQUEST OUTPUT` | 在原安装中为另一份新 home 生成恢复计划，绑定原快照与指纹 | 无；仍需单独 prepare、select 和注册 |

`REQUEST` 是 UTF-8 JSON，字段必须完整，不能包含额外字段：

```json
{
  "target": {
    "format": "omni-service-request-1",
    "target": "linux-user",
    "instance_id": "11111111-1111-4111-8111-111111111111",
    "home": "/home/owner/omni-instances/release-b",
    "working_directory": "/home/owner/omni-workspace",
    "bundle_dir": "/home/owner/omni-deployments/release-b",
    "principal": {
      "name": "owner",
      "id": "1000",
      "home": "/home/owner"
    },
    "host": "127.0.0.1",
    "port": 8766,
    "external_url": null,
    "master_key_file": null,
    "stdout_logs": true
  },
  "backup": "/home/owner/omni-backups/release-a-before-switch",
  "journal": "/home/owner/omni-maintenance/release-a-to-b"
}
```

实例 ID、账号与平台路径须替换为真实捕获值。Windows 使用绝对盘符路径与 SID，macOS 使用实际普通 UID；示例不授予真实安装、启停或外发权限。

## 数据与单目录启动保证

prepare 要求旧实例已经按正常流程关闭。正在运行、未完成回收或其他维护持有实例锁时会拒绝，不按 PID 文件停止程序。先对新 home 建立独占锁和私密 `run/release-fence.json`，再写入数据；普通启动、init 和其他使用实例锁的维护均不能绕过该闸门。

备份复用 SQLite 一致快照及注册对象验证，不直接复制活跃 db/WAL。恢复复用现有隔离逻辑：待执行任务、未知外部结果、费用、投递、终端及权限按备份恢复规则保留或进入核查；旧令牌撤销、历史代次更新。凭据、主密钥、浏览器登录态和旧 owner.token 不复制，不能把“数据恢复完成”当作“账号授权已恢复”。

prepared 记录旧 home 的主库、WAL 和回滚日志指纹，以及目标恢复库、对象与恢复报告指纹。select 重新取得两份锁并检查这些证明；旧实例在准备后重新运行或发生数据写入，会使旧证明失效，需要新计划和新目录，不能继续选用过期副本。WAL 整理即使没有业务变化也可能保守触发拒绝；不会为通过核验而 checkpoint 或丢弃未知状态。

选择前必须用既有、单独审阅的服务操作移除旧的静止注册。select 的管理器查询必须明确证明 `available=true`、`installed=false`、`definition_present=false`、`state=stopped`；未知、残留文件、仍注册或查询失败都不开放目标。系统服务的管理员边界仍按[服务合同](V2_SERVICES.md)执行。

用户服务复用注册端口的只读 inspect，使用与移除相同的原生缺失判定，不以普通状态接口里缺少的字段补造成功。Linux 系统服务需要管理员显式启动绑定原卸载操作的 broker，并在 select 同时指定 `--source-operation ORIGINAL_UNINSTALL_JSON --admin-socket EXACT_SOCKET --admin-bundle-sha256 SHA`。三项必须齐全，部署及动作必须对应原源部署的 uninstall；此处只调用 inspect/close，不再次 change。未配置管理员或原生证据不足时，选择保持不可用。

文件切换顺序为：持久登记 `selection_intent` → 给旧 home 加入同一 release 的源闸门 → 持久登记 `selection_committed` → 解除目标闸门 → 登记 `selected`。默认实例锁不允许启动暂存或退役 home；只有绑定同一 release ID/SHA 的协调器可进入维护。两个 home 不会同时因选择步骤而开放。

`selected` 只表示目标数据目录被选择。随后由所有者另行审阅、注册和启动目标部署，再通过独立的本次运行 HMAC 身份及管理器状态判断 ready。[显式服务启停](V2_SERVICE_CONTROL.md)已接入预定启动、正常排空和后置证明；[用户交换协调器](V2_SERVICE_EXCHANGE.md)进一步串联 Windows/Linux 用户注册和目标就绪。无法核清的结果、其他目标和完整失败恢复仍待推进，不自动重发任何一步。

## 中断、取消及恢复

外部目录中的维护日志保存精确计划和最多 32 个连续、哈希相连、每项最多 16 KiB 的事件。事件只含 UUID、指纹、阶段和必要路径元数据，不保存聊天、工具正文、原生 stderr 或凭据。

准备步骤中断后不覆盖现存目标或重复拷贝到不明目录。可以 inspect；在未出现选择意图时 abandon，保留暂存文件和闸门，再用全新路径制定新计划。仍在执行的异步管理器查询由实际任务拥有；调用者取消或查询回收失败时，实例锁继续保留，不能提前放行另一次选择。

选择中断后只有两份锁、源退役闸门、备份、数据连续性和服务缺失均再次验证，才能由新的 select 命令完成尚未完成的文件步骤。它不重放任何原生动作。若目标已运行并改变了数据，即使日志停在 `selection_committed`，也不依据旧指纹静默宣称选择成功。

普通 recovery-plan 必须验证原安装完整 capture，并绑定原 prepared 目标的数据指纹；数据有新写入时拒绝使用较早快照覆盖。失败版本已经产生新消息、费用或未知动作时，使用[显式隔离救援](V2_EXCHANGE_RECOVERY.md)：封住旧目录，以失败版本的最新验证快照在新目录恢复，保留预留、用量及未知账目，使用独立原生注册名称；原升级前快照继续作为验证锚点。两种策略都保留旧 home、失败数据和快照，不自动补发。

CLI 链路、故障注入和 SQLite 验证在隔离临时目录执行，管理器使用注入实现。三平台真实安装、结构跨版本、凭据重新授权、服务启停交换、睡眠/重启恢复及 72 小时验收仍待完成。

结构迁移报告绑定prepared证明；原恢复报告保留原快照的隔离过程，迁移报告另行记录新schema。旧schema恢复必须使用原安装和原快照且失败数据无新增写入，不执行downgrade。详见[结构升级](V2_SCHEMA_UPGRADE.md)。
