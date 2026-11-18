# V2 分阶段管理员交接

本页定义 D4 的新源码合同：在两份不可变安装之间，源、目标管理员各自批准有限操作，所有者应用负责数据与顺序。已在 Windows 使用注入原生管理器/Unix传输验证 Linux、macOS 系统链路，并另以真实 SQLite 验证跨 schema 用户服务链；不代表 Linux/macOS 原生服务已验收。默认入口仍为 V1。

## 固定合同

- 交换计划为 `omni-service-exchange-4`，显式包含 `administrators`；普通用户或同源码单管理员模式为 null。
- 分阶段系统模式分别保存 source/target 的独立 UUID、`omni-service-admin-4` 和管理员包 SHA256。整个交换 SHA 包含这些字段。
- 导出的批准文件为 `omni-service-admin-phase-1`，包含完整交换、角色和包指纹，并有独立批准 SHA。source 仅授权 `remove_source`；target 仅授权 `install_target` 和 `start_target`。角色间不能改派动作或部署。
- 管理员协议、包、说明及审计升级为4；标准库包仍含29项文件记录。源管理员源码只与源安装比对，目标只与目标安装比对；另一侧仅作为已批准元数据，不在 root 执行。
- 单父会话模式仍要求管理员源码同时匹配两份安装且 schema 相同；不能把带参与方的完整交换直接交给该模式绕过分角色授权。
- 启动预约继续使用独立合同3/激活1，保留共享启动消费者；它与交换计划4属于不同文档。新旧安装的闸门/消费者仍须一致。

本轮不保留旧管理员协议3的隐式兼容。分阶段交接支持共享协议4的不同管理员源码/包版本；不声称任意不兼容协议之间可以在线桥接。首次正式交付需以新合同重新构建、捕获安装并生成计划与管理员包，不能直接使用旧断点包。

## 审阅与命令

先按[数据准备合同](V2_SCHEMA_UPGRADE.md)创建验证备份与 prepared release。系统管理员版本不同时，从各自安装生成并独立审阅管理员包。所有者提供参与方文件，指纹占位符必须替换成真实64位十六进制值：

```json
{
  "source": {"protocol": "omni-service-admin-4", "bundle_sha256": "SOURCE_BUNDLE_SHA256"},
  "target": {"protocol": "omni-service-admin-4", "bundle_sha256": "TARGET_BUNDLE_SHA256"}
}
```

以下为命令形状，本批未执行真实服务或 root 操作：

```text
TARGET_PYTHON -I -m omni.composition.cli exchange plan RELEASE_PLAN NEW_JOURNAL EXCHANGE_JSON --administrators PARTICIPANTS_JSON
TARGET_PYTHON -I -m omni.composition.cli exchange admin-phase EXCHANGE_JSON source NEW_SOURCE_PHASE_JSON
TARGET_PYTHON -I -m omni.composition.cli exchange admin-phase EXCHANGE_JSON target NEW_TARGET_PHASE_JSON
```

两个 admin-phase 命令只发布私密批准文件，返回角色批准 SHA、包 SHA 与固定 socket，不连接或启动管理员。管理员分别审阅对应角色文件，用该角色的受信 root Python、清空环境和 `-I -S` 启动相应不可变包。root 的 `--approve` 是角色 SHA；所有者 apply/resume 的 `--approve` 是完整交换 SHA。

```text
ROOT_SOURCE_PYTHON -I -S ROOT_SOURCE_BUNDLE/service-admin.py --operation SOURCE_PHASE_JSON --approve SOURCE_PHASE_SHA256 --owner-uid OWNER_UID
TARGET_PYTHON -I -m omni.composition.cli exchange apply EXCHANGE_JSON --approve EXCHANGE_SHA256
```

source socket 使用源角色 UUID，target 使用目标角色 UUID；Linux 目录为 `/run/omnioperator-service-admin`，Mac 为 `/private/var/run/omnioperator-service-admin`。端点由计划推导，分阶段 apply/resume 不接受外部 socket 或包 SHA 覆盖。

目标管理员须在源阶段释放实例锁后接手：

```text
ROOT_TARGET_PYTHON -I -S ROOT_TARGET_BUNDLE/service-admin.py --operation TARGET_PHASE_JSON --approve TARGET_PHASE_SHA256 --owner-uid OWNER_UID
TARGET_PYTHON -I -m omni.composition.cli exchange resume EXCHANGE_JSON --approve EXCHANGE_SHA256
```

信任安装、内核对端身份、账号和原生路径前提沿用[系统服务](V2_SERVICES.md)与[Mac管理员](../platforms/macos/V2_MACOS_SYSTEM_SERVICES.md)。没有自动提权、root stop/restart/force 或任意命令。

## 顺序与持久状态

六个固定阶段为：源移除 → 数据选择 → 源管理员关闭 → 目标注册 → 目标预留启动 → 目标管理员关闭。关闭阶段各自绑定角色批准 ID/SHA。root 协议4只在命令实际回收并释放原生实例锁后返回全局 close；子 settle 继续只释放子操作，不释放父锁。

源、目标管理员顺序持有同一实例锁。目标可用性检查发生在源关闭证明完成之后；目标未启动时，所有者持久登记 `awaiting_administrator`，保留预约和阶段进度，不登记该步骤的派发，也不创建目标子操作意图。重复等待不重复写日志，resume 只继续尚未派发的阶段。日志仍最多64项、RPC最多192帧且保留最终关闭帧。

数据选择、备份/迁移和业务账本仍由所有者应用用例管理。源服务核查使用已批准源定义的只读子端口，root 不打开数据库。目标启动仍先写入一次性 launch-ticket、释放实例锁，再执行原生提交；本次 boot/HMAC/PID 才能证明就绪。两个管理员关闭阶段完成后才写 finished 并释放预约。

## 未知、取消与恢复

调用方取消不取消实际 RPC、阶段或清理。缺少关闭回执、坏帧、原生命令回收失败时，原任务与交换锁继续被拥有；目标管理员不能借源锁已在系统层释放而提前执行。

已知效果但无法确认结果时，只有既有子回收证明成立，才允许新会话只读核查原动作；原动作 UUID 与 root dispatch 账本不变，不重新派发。源关闭已登记完成时，恢复不再连接源管理员。

**原子操作缺少回收证明，或关闭步骤已派发却没有回执时，新会话不能替原工作补造清理证明。** resume 返回 `service_exchange_administrator_cleanup_unresolved` 或 `service_exchange_administrator_close_unresolved`，保留 unknown、活动标记和预约，需要管理员核查；不能通过删日志、换新操作 UUID 或弱化断言解锁。原会话仍活着时，应等待它的实际回执。隔离与新目录救援继续遵守原有独立批准，不自动补发。

## 当前证据边界

专项覆盖两平台不同包指纹、角色越界拒绝、仅核验本方源码、关闭顺序、目标缺失、未知安装、错误 boot、只读恢复不重发、取消/迟到回执和缺失原回收证明。真实 SQLite 用户服务专项覆盖 V25→V26 的准备、选择、注册与预定启动，保留额度预留。

本批没有真实 root/launchd/systemd、原生 Unix peer、Mac编译、真实进程终止/磁盘故障、全量、重新打包或72小时结论。平台验收继续待完成；后续开发转入 D5 业务通道迁移，当前 V2 不标记完成。
