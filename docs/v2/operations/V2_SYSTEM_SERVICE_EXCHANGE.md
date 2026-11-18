# V2 Linux / macOS 系统服务交换

本页定义 D4 的 Linux/macOS 系统交换源码合同。它复用同 schema release、注册、启动与隔离清理，新增完整计划绑定的 root 会话；没有新建业务表。默认入口仍为 V1，真实管理器、root 文件及进程回收尚待两平台独立测试账号验收；Mac 前提与端点见[Mac 管理员合同](../platforms/macos/V2_MACOS_SYSTEM_SERVICES.md)。

本页下文描述单父会话模式；不同管理员源码或schema时，使用[分阶段交接](V2_ADMIN_HANDOVER.md)，分别批准source/target，源与目标关闭证明均持久登记。当前协议/包/审计为4，交换计划4与独立启动预约3分开。

## 审阅与执行

先按[交换合同](V2_SERVICE_EXCHANGE.md)在目标不可变安装生成 prepared release 和 `omni-service-exchange-4`。源、目标必须同实例、同所有者、同 schema，目标须为同平台系统服务（`linux-system` 或 `macos-system`）。root 不准备备份、不选择目录、不访问应用数据库或凭据；这些仍由普通所有者应用用例负责。

从该安装生成 `service admin-bundle NEW_DIRECTORY`。当前包为 `omni-service-admin-bundle-4`，含 29 项文件记录（清单本身另存），仅使用标准库；协议为 `omni-service-admin-4`。管理员另行审阅、转交至 root 所有且不受用户写入的位置，以受信 Python 3.12+、清空环境和 `-I -S` 启动。完整 root 文件、账号、解释器及 socket 前提见[服务管理员合同](V2_SERVICES.md)。

**包中所有管理员源码的 SHA/大小必须同时匹配源和目标捕获。** 该限制允许主应用其他模块升级，不支持管理员代码或协议本身改变的版本交换。两份不可变安装、运行捕获、部署和管理员包均须重新构建核验；不能混用本次代码与旧捕获，也不能删派发见证绕过限制。共享协议4的不同管理员源码及跨schema交接改用[分阶段合同](V2_ADMIN_HANDOVER.md)；本页单父会话仍受上述限制，不隐式兼容旧协议3。

```text
# 以下为审阅后的命令形状，本批未实际执行 root 或原生服务操作
/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 ROOT_OWNED_PYTHON_3_12 -I -S ROOT_BUNDLE/service-admin.py --operation OWNER_EXCHANGE_JSON --approve EXCHANGE_SHA256 --owner-uid OWNER_UID

OWNER_PYTHON -I -m omni.composition.cli exchange apply OWNER_EXCHANGE_JSON --approve EXCHANGE_SHA256 --admin-socket /run/omnioperator-service-admin/EXCHANGE_UUID.sock --admin-bundle-sha256 ADMIN_BUNDLE_SHA256

# 中断后重新审阅原计划，创建新的同计划 root 会话；仅核查已派发步骤
OWNER_PYTHON -I -m omni.composition.cli exchange resume OWNER_EXCHANGE_JSON --approve EXCHANGE_SHA256 --admin-socket /run/omnioperator-service-admin/EXCHANGE_UUID.sock --admin-bundle-sha256 ADMIN_BUNDLE_SHA256
```

管理员批准完整交换 SHA；所有者另行批准同一完整计划并固定管理员包 SHA。`apply/resume` 必须同时提供端点与包指纹；遗漏、混用或用于其他命令/目标均拒绝，不自动提权或回退到普通管理器。文件型 inspect、隔离、救援和 abandon 不要求启动 root 会话。执行前先以只读探针核验会话可用并结清探针，才建立所有者交换意图。

## 单会话与子操作归属

root 在整个连接持有同实例锁，避免多个管理员进程竞争。每个请求绑定完整交换 ID/SHA、子键、精确子操作 ID/SHA、包 SHA、序号与请求 UUID。只能选择计划中固定的 `remove_source`、`install_target`、`start_target`；不能替换部署、动作或启动身份。`select_target` 由所有者执行，其源注册查询复用单独的只读子端口。

`ExchangeAdministrator` 拥有唯一连接；每个子用例拥有自己的 `AdministratorLease`。同一时刻只允许一个子端口实际工作；只读端口不能派发。`settle` 等待该子操作的命令与进程树实际回收，完成后释放子端口，保留父会话。所有子用例及父会话共同挂入 `ExchangeSteps.close`；全局 `close` 才结清连接归属。父会话关闭未完成时，交换日志锁和实际任务继续被拥有。

每连接最多 192 帧、每帧 64 KiB，最后一帧仅供全局 close。子端口最多 8 个，不并发或流水派发。超时或调用方取消只结束等待；RPC、settle 或 close 不重复创建。坏帧、未可信响应、管道断开及进程回收失败都不能提供清理证明，也不能自动重连或重发。

root 审计为 `omni-service-admin-audit-4`：保留完整批准 ID/SHA、子键、子操作、部署、账号、boot、阶段及固定元数据；每个子操作仍在其原 ID 目录中无覆盖登记 dispatch。重启管理员会话不会获得重发资格。root 不保存业务正文或原生命令输出。历史批准和新会话的观察分开记录。

## 顺序、激活与恢复

1. 普通所有者持久登记步骤，root 核验并移除精确、静止的源定义；未知只观察。
2. 子操作 settle 后，所有者取得短双实例锁、复核源缺失与数据连续性，再选择目标；root 会话保留。
3. root 注册精确、静止的目标，不隐含启动。所有者持有目标实例锁期间不会执行 start。
4. 所有者写入一次性启动预留并释放实例锁，Linux root 提交固定无阻塞 start，Mac root 才执行 bootstrap/不带强制参数的 kickstart；所有者核验预定 boot 的签名就绪和原生匹配 PID。提交成功不能冒充就绪。

Linux 系统与 Mac 用户/系统交换共用 `omni-service-activation-1`。预约在选择目标前发布：旧源不能启动；目标没有精确 launch-ticket 时不能因机器重启、自动服务激活或开发入口而生成任意 boot。领取只允许本次控制计划与一次 boot。完成并结清后移除预约；显式 abandon 也会解除激活限制，因此审阅计划的 `releases_activation_on_abandon=true`。放弃不回滚已发生的注册、数据或目录选择。

失败恢复仍使用原交换日志与子用例，只观察已派发效果。注册错误、旧 boot、PID 不匹配或证据不足保持 unknown，不能重新 start。无法结清时可另行审阅隔离和新目录救援，原 unknown、费用、数据和闸门保留。

已隔离的系统定义清理采用[独立清理合同](V2_SERVICE_CLEANUP.md)：所有者批准外层 cleanup SHA；root 单独批准其中的 `operation` 卸载计划，socket 使用该操作 UUID。`apply-cleanup/reconcile-cleanup` 必须显式指定对应 root 端点与包 SHA；不得复用完整交换会话或把历史 unknown 改成失败。

## 验证边界

专项采用真实应用协调器和受控传输、注入 systemctl/root IO，覆盖成功、注册未知、错误 boot、恢复不重发、取消、父/子回收、授权前置检查、两份安装指纹、启动闸门和独立清理授权。普通用户交换、Mac 激活和原清理调用方一起回归。

这不证明真实 systemd/root 文件/Unix 身份/pidfd 已可用。Linux 真实隔离安装、拒绝及故障恢复，Mac 编译/ACL/登录、macOS 系统管理员实际验收、不兼容协议桥接、业务迁移和三平台各 72 小时仍待完成，不能据此标记 D4 或 V2 完成。
