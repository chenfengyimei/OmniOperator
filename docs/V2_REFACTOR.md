# V2 架构重构：当前实施入口

> D6扩展链离线节点已完成：产物协议、产物工作台、本地市场只读扫描、市场更新工作台与受控宿主回调（context/log、白名单/预算、违规归UNKNOWN不重放）；MCP运行适配器（stdio/Streamable HTTP/OAuth/逐工具授权/工作台）已收口；基线26项历史测试失败已测试-only修复（5f3b46e）。136表/v2_0037，工作台v51；扩展产物/工作台与MCP合并228个不同节点最新全过（含6例390/1024浏览器），市场扫描62项、市场工作台25项与宿主回调93项全过。最终全量4583项：4580 passed / 3 failed / 0 errors（service_runtime身份、debug_sink、portal_ui各一处并行时序，同源码隔离36项全过并观察）。下一阶段D7/D8；真实市场源、真实第三方插件、MCP真实服务与三平台原生仍待，整个V2未完成，默认入口仍为V1。见[扩展合同](V2_EXTENSIONS.md)、[回调证据](evidence/v2-extension-callbacks.json)、[市场证据](evidence/v2-extension-market.json)、[扩展证据](evidence/v2-extension-artifacts.json)、[基线修复证据](evidence/v2-baseline-repair.json)与[MCP证据](evidence/v2-mcp.json)。

当前采用已确认的“三平台原生优先”顺序，完整范围保持不变。**V2未完成，默认入口仍为V1。**
任务真值为[任务台账](tasks/v2.json)，当前交付摘要由其生成：[开发状态](DEVELOPMENT_STATUS.md)。历史记录只证明所属源码版本，不能作为当前待办或当前测试结论。

## 当前实现与剩余交付

四层内核、单一omni.db（136张业务表，v2_0037）、有界事务与运行、审批费用、对象/人物/摘要/文档、持久调度投递、日志审计和维护清理已实现基础业务。Python扩展宿主首批、PTY、服务部署计划及只读窗口身份已接入；这些不代表整个阶段交付完成。

Windows UIA观察和窗口截图均已接入固定PowerShell/.NET MTA辅助程序、HTTP与工作台。UIA正文不缓存；截图经校验保存为Agent/会话对象引用，支持查看、释放引用及备份恢复，不自动发送给模型。Windows语义动作、显式前台接管源码与所选离线验证也已接入；真实键鼠按所有者安排稍后验收。Mac包等待Mac mini，Linux语义源码已接通，持续身份/前台/Portal控制仍待实现；终端独立核查、用户服务注册、Mac宿主及Linux系统服务独立root边界源码已接入；Mac默认ACL的statx/filesec证明和系统域只读已接源码、原生字段/编译及Linux root实机行为待核验，不可变版本离线准备、单目录选择及恢复计划已接通，显式启停、同boot就绪与正常排空也已接通，Windows/Linux/macOS用户与Linux系统交换协调源码已串联，Mac用户/Linux系统激活绑定、隔离救援、独立旧注册清理和在线排空到备份已接通；D4 Mac系统管理员、分阶段交接与跨schema链也已接源码，原生实机仍待。D5 QQ文本首批现已接入V2人物、独立会话、人设、事务入队、审批投递和连接监督；B站基础读取、评论/私信、真实mention组件及统一投递首批已接线；安卓所有者授权/观察/截图/审批输入与unknown核查已接线；模型本次运行权限与滑动/按键/长按/应用查询启动已接入，受控中文输入与恢复义务已接线，真实Android验收待完成。

剩余固定为D1 Windows原生、D2 macOS实现与交接、D3 Linux原生、D4平台运维、D5业务通道、D6模型扩展、D7产品切换和D8完整验收。每项分开记录开发、离线与平台实测，不把子批次完成代替平台完成。

macOS实现包完成后交接Mac mini上的Codex编译与验收；当前Windows不能证明Mac运行。Linux实机尚未提供；容器只验证无桌面核心。QQ文本、B站基础发布、安卓模型范围/Unicode、模型流式、低清图片、文档ONNX与档位已接源码及各自专项；本地记忆召回、相关事实人工核查、文本技能、媒体/视频理解与MCP运行适配器现已接线；真实业务/质量验收、真实MCP/OAuth供应商、视频ASR质量与完整插件市场仍待。

## 运行与开发门禁

开发入口：`uv run --no-sync python -m omni.composition.cli --home <全新绝对目录> serve`，默认端口8766；`auth`获取本地所有者令牌。令牌不进入日志或交接记录。旧配置和数据库不迁入V2，不双写。

开发中先完成生产链路，再运行相关模块和调用方的`pytest -q -n 0`；重型类型、浏览器和打包顺序执行。交付包收尾/提交前执行一次全量；文案或状态更新不重复全量。新增文件更新索引。当前没有全量通过结论，最新源码与专项范围见[MCP证据](evidence/v2-mcp.json)。

完整交付门槛仍包括业务对齐、正式入口切换、三平台安装恢复、真实功能、负载及各平台72小时。缺环境或授权不标记通过。

## D7 能力报告（2026-11-11）

D7 首个节点落地：`GET /api/v1/capabilities` 把实例健康快照投影为 24 个能力域的有界报告，只保留布尔/整数/≤80 字符短文本（列表/映射只取长度），路径、标识、凭据与自由文本不出现；`closing`、非空错误码或 `ready=false` 标为需注意，缺失域直接省略而不猜测。报告固定 `entrypoint=v1`、`v2_accepted=false`，`platform_acceptance` 原样透传（当前 `pending`），并附三条“未验收/不重放”注意事项。工作台新增“能力”页（390/1024），展示元信息、逐域状态/计数/限制与注意事项；资源版本 v52。合同见[能力报告](V2_CAPABILITIES.md)，证据见[证据](evidence/v2-capabilities.json)。

节点专项 17 项全过（域投影边界/attention 判定、HTTP 鉴权与泄漏检查、390/1024 页面）；能力/HTTP/架构/既有 UI 回归 86 项通过。

## D7 令牌管理（2026-11-11）

`GET /api/v1/owner-token/sessions` 返回最多 50 条会话的创建/过期/撤销/当前标记，不暴露令牌或哈希；`POST /api/v1/owner-token/rotate` 生成 30 天新令牌：先登记新会话，再原子替换 `run/owner.token`（0600、fsync、拒绝链接），最后撤销其余会话并注册日志脱敏，新令牌只在响应中返回一次、旧令牌立即 401。崩溃窗口最多留下一个额外有效会话，不会发布数据库不认的令牌；并发轮换由进程内锁串行化。CLI `auth --rotate [--url]` 可对运行实例轮换并打印新令牌。合同见[令牌管理](V2_OWNER_TOKENS.md)，证据见[证据](evidence/v2-owner-token.json)。

令牌/HTTP/架构/服务运行时回归 38 项全过。工作台“令牌”页（390/1024，v53）列出当前/历史会话，轮换需二次确认，新令牌只显示一次并可手动隐藏；轮换后立即把内存 token 与连接输入更新为新值、后续请求不中断，旧令牌立即 401，页面不持久化令牌。令牌工作台 16 项节点回归与 87 项 UI/HTTP 调用方回归通过。

## D7 业务清单（2026-11-11）

`docs/V2_BUSINESS_INVENTORY.md` 逐页列出 22 个工作台页面的业务能力、状态（仅“可用（离线）/需配置”）、配置依赖与真实验收边界，并单列实例级接口/CLI 与明确未实现项；`agent/tests/test_v2_business_inventory.py` 对导航、page section 与清单表格做双向一致守卫，并禁止“已验收”状态词。2 项守卫通过；证据见[证据](evidence/v2-business-inventory.json)。

导航收敛（v54）：侧栏 22 个入口按 7 个业务域分组（对话与记忆、知识与技能、工具与扩展、设备与平台、资料与媒体、自动化、系统与运维），仅加分组标题，不改变 `data-page` id、页面能力与既有测试选择器；守卫新增分组顺序与文档一致性断言，浏览器 390/1024 验证 7 组顺序、22 入口齐全、逐项页面可达。导航/清单/UI 11 项与工作台 73 项回归通过。

## D7 严格类型起步（2026-11-11）

`pyrightconfig.strict.json` 以 strict 模式纳入纯 domain 模块与 application/adapters 模块，当前为 **247 个文件**（53 个 domain + 43 个 application + 151 个 adapters；逐文件清单见 `docs/TYPE_CHECKING.md`），门禁 `uv run --no-sync pyright --project pyrightconfig.strict.json` 为 0 errors/0 warnings；`docs/TYPE_CHECKING.md` 记录 `1a18fae` 上 domain 全层 2321 个 strict 错误的基线、逐文件样本与推进规则，明确“全仓 strict 未完成”。守卫测试保证配置 include、文档表（三层）、文件存在与未完成声明一致。

第二批把 metering（19 个基线错误）与 release_fence（16 个）清零后纳入：release_fence 去掉对 service_deployment 私有辅助的跨模块依赖，改为自包含 UUID/64-hex/控制字符校验；metering 显式 `Mapping[str, object]`/`list[dict[str, object]]` 注解。

第三批把 memory_semantics（19 个）与 foreground（24 个）清零后纳入：foreground 自包含前台输入校验、去掉 desktop 私有依赖，`ready/permit` 接受 `Mapping`、version 原样透传；`desktop_actions.node_proof` 补返回注解。

第四批把 context（38 个）与 recovery_authority（27 个）清零后纳入：context 显式 `Sequence[Mapping[str, object]]`；recovery_authority 通过 service_deployment 新增的公共桥（deployment_fields/integer/path/sha/uuid）消除跨模块私有依赖，witness 行类型化并保留原错误码。

第五批把 model_stream（75 个基线错误）清零后纳入：payload/usage/choices/tool_calls 全部显式 `Mapping`/序列类型，JSON 解码结果 cast 为 `dict[str, JSON]`，chunk/字节/参数上限与错误分类不变。

第六批把 service_control（94 个基线错误）清零后纳入：ControlPlan/排空请求/回执全部显式类型，通过 service_deployment 新增 `deployment_created/deployment_plan` 公共桥消除私有依赖，嵌套字段有界读取，HMAC 与时间窗校验路径不变。

第七批首次进入 application/adapters：routing（显式 Record）、release_drain（deployment cast）、exchange_recovery_steps（`RecoveryAuthority | None` + `ports.witness` 返回 `list[dict[str, object]]`）、local_vectors，以及 adapters 的 build_identity/primitives/timezones；守卫与文档表同步扩到三层。

第八批加入 service_deployment（公共桥 `deployment_make/deployment_plan` + 显式 Record）、exchange_recovery（进度与授权类型化）与 adapters/metrics；`domain.service_exchange.progress` 参数改为 `Sequence[Mapping[str, object]]`，`ports.ExchangeRecoveryPort.rescue_plan` 使用 `RecoveryAuthority | None`。

第九批加入 application/service_cleanup（复用已类型化的进度）与 domain/terminal_review（parse_review 返回 dict[str, object]，键集合/决策/证据显式校验）。

第十批加入 application/model_output（`_Row` TypedDict、authority Mapping、预览截断/并发上限不变）。

第十一批加入 application/people（人物档案用例，strict 下本已干净、无忽略注释）。

第十二批加入 application/memory_tools（`DatabasePort` 回调显式 `UnitOfWork -> list[Record]`，搜索 data 显式 `dict[str, JSON]`）。

第十三批加入 application/action_review（`_pending` 显式 `Task[object]`、完成回调类型、`parse_review` 返回 `dict[str, object]`、page/history/_public 显式 `Record`）。

第十四批加入 application/operations（`_pending` 显式 `Task[object]`、完成回调 `Task[T]`、`_port_close` 显式 `Task[bool] | None`）。

第十五批加入 application/debugging 与 application/terminal_review（维护任务 `Task[None] | None`、终端核查回调 `Task[Any]`、`parse_review` cast `Record`）。

第十六批加入 application/objects（`_pending` 显式 `Task[object]`、完成回调 `Task[T]`、captures/page 返回 cast `Record`）。

第十七批加入 application/memory_organization（`_pending` 显式 `Task[object]`、完成回调 `Task[T]`、page 返回 cast `Record`）。

第十八批加入 application/exchange_steps（`registration_goal` 接受 `Mapping`、公开 `verify_operation_deployment` 桥、stage payload 显式 `Mapping` cast、close 结果 `list[bool]`）。

第十九批加入 application/credentials（`_pending` 显式 `Task[object]`、完成回调 `Task[T]`、`UnitOfWork` 回调 `prepare/commit` 显式 `tuple[Record, list[str]]`、page 返回 cast `Record`）。

第二十批加入 application/desktop_actions（绑定请求 `dict[str, object]`、有界任务 `Task[Any]`、完成回调 `Task[Any]`、target/node/request 嵌套字段 cast、foreground/act payload 显式 `Record`）。二十批专项 32 项（类型守卫 + 桌面动作/传输/HTTP 调用方）全过。

第二十一批加入 domain/scheduling 与 application/scheduling（cron/到期选择返回 `dict[str, object]`、`UnitOfWork` 回调与嵌套 progress/value 显式化、端口 materialize/enqueue 接受 `Mapping`）。二十一批专项 41 项（类型守卫 + 调度/故障/HTTP 调用方）全过。

第二十二批加入 domain/installation、domain/delivery 与 domain/maintenance（收据本已干净；出站摘要/配置/结果显式 `dict[str, object]`，维护请求 cast 与集合比较显式化，保留非字符串输入的 fail-closed 校验）。二十二批专项 62 项全过。

二十二批后全量复核：**4633 项，4632 passed / 1 failed / 0 errors**（`debug_sink` 并行时序，同源码隔离 11 项全过）；strict 门禁 41 文件 0 errors。**不是全量全绿**，也未做三平台或真实环境验收。D7 剩余门槛：其余 application/adapters/composition 严格化、三平台包/容器/安装更新恢复与正式入口切换。

## D7 打包验证（2026-11-11）

Windows 主机实测 `scripts/check_package.py`：离线构建 direct wheel 与 sdist→wheel，交叉验证 `domain/application/adapters/composition` 内核字节一致、116 个 web 资源一致（排除 node_modules）、隔离解释器导入 `omni`、fresh V2 实例（1 agent、ready、无 `omni.bootstrap`/`omni.memory.*`、关闭 complete）。发布/部署/drain/schema 恢复调用方 57 项全过。`docs/V2_PACKAGING.md` 明确未完成：macOS/Linux 构建安装、容器镜像、V2 独立安装器/更新器、签名公证、干净机器端到端；默认入口仍是 V1。证据见[证据](evidence/v2-packaging.json)。

D7 节点后全量复核：**4601 项，4599 passed / 2 failed / 0 errors**（`memory_organization_ui` 迟到详情与 `portal_ui` 各一处并行时序，同源码隔离 2 项全过）；历史并行时序还包括 service_runtime 身份与 debug_sink。仍不是全量全绿。证据见[证据](evidence/v2-d7-verification.json)。

## D7 安装收据与升级预检（2026-11-11）

`python -m omni.composition.cli --home <home> installation verify|seal|artifact|stage|prepare|switch-plan|backup|rollback-check|status|smoke`：verify 只读报告 home/收据/数据库/WAL/schema/期望 head 与是否需要升级；seal 显式写入一次性安装收据（schema==代码 head，幂等、原子写、权限收紧、异常 fail-closed），校验规则位于 `domain/installation.py`；artifact 只读校验本地候选 wheel（哈希/zip CRC/成员与解压上限/越界路径/重名/V2 资源与 METADATA）；stage 在 artifact 通过后原子复制到 `home/updates/<sha16>/artifact.whl` 并写 `staged.json`；prepare 仅对已暂存且哈希未变的 wheel 解包到 `tree/`，重校验后写 `prepared.json`（同 SHA 幂等、失败清理 partial）；switch-plan 只读校验候选/收据/数据库 schema/实例占用并输出 backup→stop→switch→migrate→seal→start→health 七步计划（executable=false）；backup 只执行计划中的备份步，在 home 之外创建一致快照并写 `backup.json`，同 SHA 幂等、不覆盖旧目录；rollback-check 将备份 restore 到 home 之外的新目录并用 `inspect_database` 校验完整性与 schema，写 `rollback.json`，同 SHA 幂等、不触碰 live home；status 只读汇总收据/数据库/候选阶段与 next_step；smoke 在子解释器中从候选树导入 `omni` 并校验资源路径；switch 在 smoke/backup/rollback 收据齐全、无待迁移且实例空闲时原子写 `runtime/current.json`（保留 `previous.json`）、`runtime/launch.py` 与 `omni.cmd`/`omni` 启动包装，同 SHA 幂等；install 串起 artifact→stage→prepare→smoke→backup→rollback-check→switch，存在待迁移时在改动前拒绝；current 只读运行指针。运行时打开实例会校验已存在收据：schema 不一致 `installation_mismatch`、损坏/链接 `installation_invalid`，无收据的历史实例保持兼容；升级后必须重新 seal。这仍不是完整安装器：不下载 wheel、不创建 venv、不停止/启动服务、不执行迁移或 live 回滚、不卸载；macOS/Linux 与干净机器端到端未验收，默认入口仍是 V1。

安装器节点后全量复核 **4624 项：4623 passed / 1 failed / 0 errors**（`debug_sink` 并行时序，隔离 11 项全过）。首次全量发现 `test_v2_architecture` 分层违规（`wheel_switch`/CLI 直接 import sqlite3），已在 `1a13f3c` 将只读 schema 读取移入 `adapters/persistence/maintenance.read_schema` 并在第二次全量确认；证据见[证据](evidence/v2-installer-verification.json)。

D7 切换执行节点：新增 `installation switch|install|current`。switch 只执行计划中的切换步，要求 prepared/smoke/backup/rollback 收据齐全、收据 schema 与数据库一致且无待迁移、实例未被占用，原子写运行指针与启动包装并保留上一指针；install 串起本地 wheel 的校验/暂存/解包/冒烟/备份/演练/切换，任一步失败即停止；current 只读。仍不建 venv、不停服/启动、不执行 live 回滚、不卸载，默认入口仍是 V1。

D7 迁移执行节点：新增 `installation migrate`，在同一组 prepared/smoke/backup/rollback 收据、收据 schema 一致且实例空闲时，用打包的 Alembic head 迁移数据库，校验到达 head 后重新 seal 收据并写 `migrate.json`；install 自动串入 migrate 再 switch，迁移失败由事务回滚并 fail-closed。仍不建 venv、不停服/启动、不执行 live 回滚或卸载，默认入口仍是 V1。

D7 候选卸载节点：新增 `installation uninstall [--sha256]`，实例空闲时删除指定 `updates/<sha16>` 与匹配的当前指针/启动包装，或只停用当前指针；保留 `data/`、安装收据与 home 之外备份，未知/非当前候选和实例占用均 fail-closed。仍不停服/启动、不执行 live 回滚或数据目录清理，默认入口仍是 V1。

D7 离线环境节点：新增 `installation environment`，为已暂存 wheel 创建 `venv`（`--system-site-packages` 离线模式）并用 `pip --no-index --no-deps` 安装，子解释器验证 `omni` 导入位于该 venv，写 `environment.json`，同 SHA 幂等；switch 检测到有效环境时把运行指针/启动包装指向 venv（`mode=venv`），否则保持宿主解释器+候选树（`mode=tree`）。仍不停服/启动、不执行数据目录清理，默认入口仍是 V1。

D7 live 回滚节点：新增 `installation rollback`，实例空闲时把运行指针换回 `previous.json` 并重写启动包装/`launch.py`，写 `rollback-live.json`；要求上一候选树存在且 schema 与当前数据库一致（否则 `rollback_schema_mismatch`），重复调用幂等。仅回滚代码指针，不恢复数据库备份；仍不停服/启动、不清理数据目录，默认入口仍是 V1。

D7 状态投影节点：`installation status` 增补运行指针（active/SHA/mode/python/previous/live-rollback）与候选的 environment/migrate/active 标志，`next_step` 在待迁移时指向 `migrate`；仍只读。

D7 显式清理节点：`installation uninstall --purge-data` 在实例空闲时删除 `data`/`objects`/`logs`/`updates`/`runtime`/`cache` 与安装收据、启动包装，保留 home 目录本身与 home 之外备份；不带 purge 仍保留数据；purge 与 SHA 并用或实例占用均 fail-closed。

第二十三批加入 domain/reference_release（计划键集合/目标列表/64-hex 指纹显式 cast，retention.body_fingerprint 改为 `Iterable[Mapping[str, object]]`）。二十三批专项全过。

第二十四批加入 domain/service_activation（改用 service_deployment 公共桥 deployment_fields/path/sha/uuid，启动绑定与预留返回显式 `dict[str, object]`）。二十四批专项全过。

第二十五批加入 domain/terminals（请求解析返回显式 `dict[str, object]`，`_exact` 先窄化再 cast，去掉恒真的 kind 类型检查）。二十五批专项全过。

第二十六批加入 domain/skills（标识列表/元数据/平台集合/文件清单显式类型，`ToolSpec` 参数 cast `dict[str, JSON]`）。二十六批专项全过。

第二十七批加入 domain/schema_upgrade（改用 service_deployment 公共桥 deployment_fields/integer/sha，运行时清单与修订行显式类型）。二十七批专项全过。

第二十八批加入 domain/routing 与 domain/settings（策略/设置显式 `dict[str, object]`，configuration.config_value 返回 `dict[str, object]`，去掉恒真类型检查）。二十八批专项全过。

第二十九批加入 domain/retention 与 domain/memory_organization（计划/请求/决定显式 `dict[str, object]`，UUID/整数/摘要逐项 cast）。二十九批专项全过。

第三十批加入 domain/service_isolation（新增公共桥 deployment_text，改用 deployment_fields/created/integer/sha/text/uuid，隔离计划与引用显式 `dict[str, object]`）。三十批专项全过。

第三十一批加入 domain/documents（分块/向量/合并结果显式类型，去掉恒真类型检查，保留文本与向量边界校验）。三十一批专项全过。

第三十二批加入 domain/android_unicode（配置/报文/解包显式 `dict[str, object]`，JSON pairs 钩子与常量拒绝显式类型）。三十二批专项全过。

第三十三批加入 domain/credential_ipc（帧解码/请求/控制/响应显式 `dict[str, object]`，JSON 钩子显式类型，保留重复键与非有限值拒绝）。三十三批专项全过。

第三十四批加入 domain/contracts 与 domain/history（动作结果默认工厂/状态检查显式化，历史游标集合比较与字符串检查收敛）。三十四批专项全过。

第三十五批加入 domain/local_vectors（清单/协议/请求/向量显式类型，JSON 钩子与边界校验收敛）。三十五批专项全过。

第三十六批加入 domain/service_operations（改用公共桥 deployment_fields/created/sha/uuid，操作步骤/操作指纹/前置条件显式类型）。三十六批专项全过。

第三十七批加入 domain/configuration（载荷 NUL 检查/名称/指纹显式类型，去掉恒真类型检查）。三十七批专项全过。

第三十八批加入 domain/service_cleanup（改用公共桥 deployment_fields/created/integer/path/sha/uuid，清理计划与校验显式类型）。三十八批专项全过。

第三十九批加入 domain/action_review（观察复核/证明来源显式类型，集合比较与工具名检查收敛）。三十九批专项全过。

第四十批加入 domain/qq（连接/会话授权/事件解析显式类型，消息分段逐项窄化）。四十批专项全过。

第四十一批加入 domain/images（图像引用/策略/消息引用/附接结果显式类型，去掉恒真类型检查）。四十一批专项全过。

第四十二批加入 domain/audit_retention 与 domain/event_retention（预览/计划参数显式 dict[str, object]，目标行逐项 cast，集合比较不再与 frozenset 直接比对）。四十二批专项全过。

第四十三批加入 domain/bilibili（WBI 签名参数显式 Mapping[str, object]、表单/提及逐项窄化，保留非字符串输入的 fail-closed 校验）。四十三批专项全过。

第四十四批加入 domain/service_exchange（改用 service_deployment 公开桥、参与者/证据/进度显式类型，保留交换身份与阶段顺序校验）。四十四批专项全过。

第四十五批加入 domain/desktop_actions（改用 desktop 公开桥，节点证据/语义动作/结果逐项窄化，保留观察不授予写权限）。四十五批专项全过。

第四十六批加入 domain/media（媒体源/字幕分段/帧表/转写载荷显式类型，行与时间戳逐项窄化，保留锚点与字符上限）。四十六批专项全过。

第四十七批加入 domain/desktop_portal（授权/命令/事件/帧收据字段显式 dict[str, object]，解码逐项校验，保留窗口共享授权边界）。四十七批专项全过。

第四十八批加入 domain/releases（改用 service_deployment 公开桥，发布计划/栅栏文件/路径分离逐项窄化，保留 schema 路线与恢复隔离校验）。四十八批专项全过。

第四十九批加入 domain/service_admin（管理员操作/阶段/请求/响应帧显式 dict[str, object]，嵌套部署目标逐项校验，保留命令白名单与 root 报告校验）。四十九批专项全过。

第五十批加入 domain/android（连接/设备/动作/权限请求显式类型，滚动校验逐项窄化，保留 ADB 文本边界与授权审批）。五十批专项全过。

第五十一批加入 domain/mcp（传输目标/目录行/工具授权/结果文本显式类型，schema 节点与内容分片逐项窄化，保留指纹撤权与 unknown 语义）。五十一批专项全过。

第五十二批加入 domain/extensions（清单/schema/产物元数据/授权显式类型，递归 schema 与产物描述逐项窄化，保留媒体类型白名单与字节上限）。五十二批专项全过。

第五十三批加入 domain/service_deployment（请求/运行时/计划/启动与公共桥显式类型，文件/分发/链接逐项窄化，保留路径、主体与指纹校验）。五十三批专项全过。

第五十四批加入 domain/desktop（目标/请求/能力/观察/响应帧显式类型，节点与边界逐项窄化，保留不可信 UI 数据校验与只读观察边界）。**domain 53 个文件全部纳入 strict**；五十四批专项全过。

第五十五批开始扩展 application：service_exchange 改用 service_deployment 公开桥，管理员参与者显式 dict[str, str]；五十五批专项全过。

第五十六批加入 application/memory_semantics（有界 asyncio 任务显式 Task[object]，召回行与返回 Record 显式窄化）；五十六批专项全过。

第五十七批加入 application/bilibili（读取/发布准备显式类型，ports 的 B 站表单与提及参数显式化，有界任务 Task[object]）；五十七批专项全过。

第五十八批加入 application/images（准入/上下文/线材物化显式类型，行与线材分片逐项窄化，保留图像预算与完整性校验）；五十八批专项全过。

第五十九批加入 application/configuration（配置用例显式 Record、ports 配置值显式化，保留图像策略与计数器诊断）；五十九批专项全过。

第六十批加入 application/qq（监听/会话授权显式类型，有界任务 Task[None]、状态投影显式化，保留账号歧义与冷却/授权语义）；六十批专项全过。

第六十一批加入 application/maintenance（作业/心跳/结算显式类型，有界任务 Task[None]、擦除派生记忆逐项窄化，保留结算重试与 unknown 语义）；六十一批专项全过。

第六十二批加入 application/model_calls（预检/授权/结算显式类型，PreparedModelStream 显式、线材消息逐项窄化，保留 known/unknown 与不重放语义）；六十二批专项全过。

第六十三批加入 application/documents（源文档/索引/检索显式类型，有界任务 Task[object]、擦除派生记忆与检索结果逐项窄化，保留向量降级与 erasure 语义）；六十三批专项全过。

第六十四批加入 application/context（压缩/摘要显式类型，历史请求与候选消息逐项窄化，保留摘要调用预算、擦除失效与 unknown 语义）；六十四批专项全过。

第六十五批加入 application/service_control（启动/停止/复核编排显式类型，部署计划显式 Record，保留 once-dispatch 与 unknown 不重放语义）；六十五批专项全过。

第六十六批加入 application/releases 与 application/service_operations（发布/操作编排显式类型，运行时与部署计划显式 Record，保留迁移、注册前置与 once-dispatch 语义）；六十六批专项全过。

第六十七批加入 application/mcp（连接/目录授权/运行期调用编排显式类型，传输端口有界，保留 unknown 不重放与目录漂移撤权语义）；六十七批专项全过。

第六十八批加入 application/extensions（安装/授权/运行期调用编排显式类型，有界任务集，保留产物校验、宿主预算与 unknown 不重放语义）；六十八批专项全过。

第六十九批加入 application/delivery（出站准入/发送/结算编排显式类型，有界任务与显式 Record，保留审批、unknown 不重放与传输关闭语义）；六十九批专项全过。

第七十批加入 application/terminals（终端会话/输入/缩放/关闭编排显式类型，有界任务与单写者租约，保留持久回执、输出缺口与 unknown 不重放语义）；七十批专项全过。

第七十一批加入 application/engine（运行准入/调度/工具执行/关闭编排显式类型，有界任务，保留审批、unknown 不重放与残余关闭报告语义）；七十一批专项全过。

第七十二批加入 application/desktop（桌面观察视图/目标/验证/捕获编排显式类型，短租约与显式 Record，保留只观察、无模型动作与 unknown 不重放语义）；七十二批专项全过。

第七十三批加入 application/desktop_portal（窗口共享授权/会话/捕获编排显式类型，有界任务与显式 Record，保留先审计后授权、无输入授权与 unknown 不重放语义）；七十三批专项全过。

第七十四批加入 application/media（媒体源/任务/转写/帧表编排显式类型，有界任务与显式 Record，保留字幕/ASR 锚点、unknown 任务结果与不重放语义）；七十四批专项全过。

第七十五批加入 application/skills（技能导入/编辑/启用/回滚/披露编排显式类型，有界任务与显式 Record，保留所有者审阅、捕获修订校验与 unknown 不重放语义）；七十五批专项全过。

第七十六批加入 application/ports（出站与 Android 端口协议的剩余裸 dict 边界改为显式 Record，端口合同与 unknown 不重放语义不变）；七十六批专项全过。

第七十七批把六个已清零的 adapters 模块（平台工作区/交换布局、持久化凭据谓词/历史序列/迁移配置/检索 schema）纳入 strict，范围守卫允许 adapters 子目录路径；七十七批专项全过。

第七十八批收敛并纳入 token_counter 与持久化 sqlite_access/deployed_instance/delivery_predicates（token 估算、含 WAL 的只读访问、部署校验与出站谓词语义不变）；七十八批专项全过。

第七十九批把其余 40 个已清零 adapters 模块（冻结迁移版本、对象存储、平台权限/发布/命令构造、wheel 产物与 darwin 探测）纳入 strict，范围守卫允许版本化文件名；七十九批专项全过。

第八十批收敛并纳入十一个小型 adapters（动作/历史/运维谓词与仓储、冻结 v0006/v0027、macOS 管理员命令与文件、systemd 解码、wheel 暂存/切换/状态），共享维护与仓储辅助窄化为 Record；八十批专项全过。

第八十一批收敛并纳入 repository_base 与 wheel 准备/备份/回滚/卸载（收据边界显式化，维护 backup/restore 返回 Record，保留含 WAL 备份与 fail-closed 语义）；八十一批专项全过。

第八十二批收敛并纳入 retention_repository/offline_plans/desktop_capture/macos_helper（会话失效、离线计划、截图身份与辅助指纹边界显式化）；八十二批专项全过。

第八十三批收敛并纳入 paths/服务管理员工件/macOS 服务查询（公共桥替代跨模块私有引用，管理员工件保持 stdlib-only、零 application 依赖）；八十三批专项全过。

第八十四批收敛并纳入 workers/terminal 与 history_protection（帧解码/固定 profile 校验、保留保护原因共享为 Record，剪枝与释放语义不变）；八十四批专项全过。

第八十五批收敛并纳入 sqlalchemy_access/linux_x11_socket/workers/desktop（DBAPI 连接回调、X11 cookie 解析与桌面帧 JSON 钩子显式化，事务/WAL 与授权语义不变）；八十五批专项全过。

第八十六批收敛并纳入 execution_pruning 与 workers/terminal_gate（保留计划返回 Record，ConPTY 控制台句柄与固定 argv 校验显式化，Job-before-start 语义不变）；八十六批专项全过。

第八十七批收敛并纳入 object_inventory 与 reference_release（离线对象登记/物理清单与引用释放计划显式化，不 unlink 对象、释放语义不变）；八十七批专项全过。

第八十八批收敛并纳入 images 编解码适配器（有界任务集显式化，Pillow 校验/解压炸弹拒绝/线材归一化语义不变）；八十八批专项全过。

第八十九批收敛并纳入系统凭据适配器（原生后端访问显式化，无明文回退与外键加密 vault 语义不变）；八十九批专项全过。

第九十批收敛并纳入 launchd 状态比较（有界 job 遍历与类型化定义比较显式化，仍保持 stdlib-only、不进入管理员工件依赖 application）；九十批专项全过。

第九十一批收敛并纳入管理员交接适配器（父会话与审批记录显式化，按序准入、源先于目标清理与原生锁释放语义不变）；九十一批专项全过。

第九十二批收敛并纳入凭据读写适配器（线程池 Future 与脱敏回调显式化，单线程后端访问/失效/关闭语义不变）；九十二批专项全过。

第九十三批收敛并纳入技能包解析适配器（有界解析任务集与 ZIP/Markdown 捕获类型显式化，仍不解压、不执行包内容）；九十三批专项全过。

第九十四批收敛并纳入服务控制日志适配器（公共部署文件桥、控制值 Record 与 Generator 上下文显式化，一次性启动预留与日志语义不变）；九十四批专项全过。

第九十五批收敛并纳入一次性启动预留（公共桥与 Record 化激活/部署绑定，实例锁、一次性预留与不启动进程语义不变）；九十五批专项全过。

第九十六批收敛并纳入冻结迁移 v0003（哈希辅助与遗留上下文行显式类型化，冻结 schema 与回填语义不变）；九十六批专项全过。

第九十七批收敛并纳入进程所有权、桌面辅助与服务查询（`spawn` 选项显式化，桌面与服务查询逐项收敛嵌套帧，输出上限、两阶段授权与未知语义不变）；九十七批专项全过。

第九十八批收敛并纳入离线审计/事件裁剪绑定（计划回调经 `UnitOfWork` 显式类型化，`Record` 计划在域边界转换，计划、备份与取消语义不变）；九十八批专项全过。

第九十九批收敛并纳入工作进程客户端（stderr 排空任务参数化、宿主回调帧逐项收敛、空宿主上下文共享常量，回调预算、能力校验与未知语义不变）；九十九批专项全过。

第一百批收敛并纳入服务交换/清理文件边界（改用部署公共桥、生成器返回类型与显式嵌套操作转换，日志、预留与旧未知记录语义不变）；一百批专项全过。

第一百零一批收敛并纳入 macOS 管理员策略与候选树冒烟（授权范围/操作边界显式类型化，受管字段统一转字符串，候选冒烟改为实例路径与 JSON 收据类型；根校验、argv 与冒烟语义不变）；一百零一批专项全过。

第一百零二批收敛并纳入本地向量包与库升级（清单行逐项收敛，升级路径改用部署公共桥并显式化 source/target 运行时，路由、重放与栅栏语义不变）；一百零二批专项全过。

第一百零三批收敛并纳入 Linux 服务管理代理（代理协议报告类型化、受管字段统一转字符串，注册前置条件、审计顺序与未知语义不变）；一百零三批专项全过。

第一百零四批收敛并纳入 macOS 原生探测与 X11 线协议（CoreFoundation 类型表与字典解码显式类型化，已校验套接字/回调与视觉表参数化，边界、只读范围与重连策略不变）；一百零四批专项全过。

第一百零五批收敛并纳入就绪证明与 Windows 桌面原生读取（HTTP 流客户端与签名报告显式类型化，ctypes 签名表/句柄/窗口回调参数化，证明与只读范围不变）；一百零五批专项全过。

第一百零六批收敛并纳入诊断与对象仓储（脱敏遍历、traceback 帧与报告字典显式类型化，对象仓储返回 `Record` 分页并在 JSON 边界转换；脱敏、预算与对象保护语义不变）；一百零六批专项全过。

第一百零七批收敛并纳入发布快照与恢复（数据库见证返回显式类型、发布侧收窄为字面量、改用新增的`restore_verified` 公共桥；锁归属、隔离与不重放语义不变）；一百零七批专项全过。

第一百零八批收敛并纳入媒体处理与固定凭据辅助（帧载荷与回复收窄显式类型化，管道与注入工厂参数化；许可顺序、无明文回退与未知语义不变）；一百零八批专项全过。

第一百零九批收敛并纳入 Linux 根策略与进程诊断钩子（授权范围/操作边界与主体四元组显式类型化，原生钩子签名分别标注；根校验、钩子归属与异常对象不留存语义不变）；一百零九批专项全过。

第一百一十批收敛并纳入诊断分页与 ADB 客户端（游标解码/分页与共享写锁显式类型化，流对与可选发送前回调参数化；边界、命令构造与不启动服务语义不变）；一百一十批专项全过。

第一百一十一批收敛并纳入 macOS 服务后端与扩展宿主池（快照/原生作业行与待决调用表显式类型化，spawn 启动回调参数化；launchctl 命令、吊销与容量语义不变）；一百一十一批专项全过。

第一百一十二批收敛并纳入发布日志文件边界（新增 `proof`/`walk`/`created` 公共桥，转换表与事件记录显式类型化，`ExchangeFiles.read` 公共桥替代私有读取；顺带删除既有 F811 重复别名）；一百一十二批专项全过。

第一百一十三批收敛并纳入候选环境与运行时切换（venv 收据与指针收窄显式类型化，重复回滚比较保持无序，`migrate_database` 返回 `Record`；不停服/不迁移库语义不变）；一百一十三批专项全过。

第一百一十四批收敛并纳入本地总线与会话归属（地址字段、recvmsg 钩子与 call 签名显式类型化，属性结果与列表收窄逐项转换；校验顺序与环境变量不信任语义不变）；一百一十四批专项全过。

第一百一十五批收敛并纳入受管终端 IPC（后端新增 io/track/admit/closing/hosts 公共桥，进程新增 closing/reclaimed/starting/start/ensure_cleanup 公共桥，跨类私有访问归零；容量与清理归属语义不变）；一百一十五批专项全过。

第一百一十六批收敛并纳入临时调试与冻结迁移 v0010（调试状态/清单显式类型化，迁移的连接与遗留采纳助手、JSON 行读取显式类型化；冻结 SQL 与回填顺序不变）；一百一十六批专项全过。

第一百一十七批收敛并纳入属主侧根代理客户端（父客户端新增 rpc/exchange/pending/failed/sequence/closed 公共桥，租约新增 holder/settle/closed 访问器，跨类私有访问归零；并修复一百一十一批引入的根工件回归——`darwin_service_admin` 不再导入 `omni.application`，stdlib-only 根工件恢复）；一百一十七批专项全过。

第一百一十八批收敛并纳入有界 D-Bus 信号（订阅队列、next 载荷与 receive 处理显式类型化，未知消息成员归零；匹配规则、溢出与无 fd 校验不变）；一百一十八批专项全过。

第一百一十九批收敛并纳入描述符接收与 Xorg 输入守卫（recvmsg/CMSG_SPACE/AF_UNIX/SCM_RIGHTS 经 getattr 适配Windows 存根，输入守卫的集合/拓扑行与注入工厂显式类型化；持有态拒绝语义不变）；一百一十九批专项全过。

第一百二十批收敛并纳入原生凭据宿主（宿主 readers/collected 任务、协议 query 与回复值转换显式类型化，未知任务成员归零；许可顺序、脱敏与未知结果规则不变）；一百二十批专项全过。

第一百二十一批收敛并纳入工作进程协议与扩展子进程（共享 IPC 的成对钩子、decode/request/action 字段收窄与响应编码显式类型化，扩展子进程的工件清单/握手比较/模式校验随之清零；帧边界与 fail-closed 语义不变）；一百二十一批专项全过。

第一百二十二批收敛并纳入 Wayland 门户会话（注入的信号工厂、属性行、请求实参与选项、流对收窄显式类型化；版本/光标模式校验与不开 PipeWire fd 的规则不变）；一百二十二批专项全过。

第一百二十三批收敛并纳入受审管理员工件物入口（严格 JSON 载荷、工件事物清单与所有者/管理员来源比对显式 `dict[str, Any]` 类型化，重复字段/格式/哈希/路径/大小校验不变）；一百二十三批专项全过。

第一百二十四批收敛并纳入 Linux 语义动作 worker 入口（单帧发送器、管道写入与注入参数显式类型化，`perform` 的请求/许可回调/响应同时收紧；初始帧不授权写入的规则不变）；一百二十四批专项全过。

第一百二十五批收敛并纳入离线只读运维快照：先修掉仓储聚合与 `UnitOfWork` 端口的两处真实不符（`android_grant` 的关键字参数应为 `device`，调度载荷改为 `Mapping`），只读 URI、schema 校验与不自动升级的语义不变；一百二十五批专项全过。

第一百二十六批收敛并纳入 Windows TCP 重置清理事件循环（`Future`/`extra` 显式参数化，私有传输成员改用局部 `Any` 桥接并删除仅供 cast 的协议；唯一私有导入的忽略已在 `docs/TYPE_CHECKING.md` 登记）。精确重置清理与不改全局 asyncio 类的规则不变；一百二十六批专项全过。

第一百二十七批收敛并纳入 PipeWire 串行取帧（Gst 消费者接口、帧执行器的 `Future[Any]`/回调与回执显式类型化，`O_NOFOLLOW` 改为跨平台 `getattr` 读取）；只取指定 serial 目标、不读根窗口/默认远程的规则不变；一百二十七批专项全过。

第一百二十八批收敛并纳入单写者数据库 actor（待处理 `Future` 集合、指标与完整性报告显式类型化；端口保持存储无关，具体连接在 actor 内以局部 `Any` 读取；`migration_snapshot` 返回 `Record`）。写入/读取预算、取消仍计费与 schema 校验不变；一百二十八批专项全过。

第一百二十九批收敛并纳入 QQ 受众/凭据权威（载荷与回执显式类型化，新增 `RepositoryBase.one` 公共单行读取取代模块级助手对受保护 `_one` 的访问，JSON 读取用 `cast` 窄化）。发送冷却、禁用 Agent 与撤销检查不变；一百二十九批专项全过。

第一百三十批收敛并纳入 Bilibili 发布权威（读取/发布/预览回执显式类型化，单行读取改用公共 `one`，正文、提及与形态表用 `cast` 从 JSON 窄化）。目标前缀、修订与已擦除来源检查不变；一百三十批专项全过。

第一百三十一批收敛并纳入异步模型适配器（准备参数、健康报告、工具调用清单与用量映射显式类型化；厂商 pydantic dump 经局部 `Any` 视图、工具载荷用 `cast` 桥接，价格查询保留 None 分支）。离线拒绝、复用守卫与计费待对账语义不变；一百三十一批专项全过。

第一百三十二批收敛并纳入人物/账号仓储（人物、分页与个人文档回执显式类型化，记忆 mixin 的 `_live_person`/`remember`/`revise_memory` 同步收紧，审计操作 id 用 `cast` 从 JSON 取 `str`）。所有者保护、修订冲突与擦除语义不变；一百三十二批专项全过。

第一百三十三批收敛并纳入能力子进程入口（`invoke`/文件/命令/浏览器处理器的 `args` 与 `context` 显式 `dict[str, Any]`，`argv` 与文件清单在 JSON 边界 `cast`，子进程管道断言非空）。工作区、参数与派发规则不变；一百三十三批专项全过。

第一百三十三批后全量复核（-n 2，2996 秒）：**4654 项：4593 passed / 25 failed / 36 errors**。24 项失败属并行会话进行中的工作台改版 UI/设计契约用例；1 项 `debug_sink` 并行时序隔离 11 项全过；36 errors 全部来自 macOS 激活夹具仍在补丁已公开的 `ExchangeFiles.homes`（第一百一十二批公开化），已由 `1b0bdb0` 修复并隔离 54 项全过。**这不是全量全绿，也不构成 UI 改版的产品失败判断**；证据 `docs/evidence/v2-typecheck.json`。

第一百三十四批收敛并纳入审计删除仓储（预览/候选/目标/计划回执与共享 `PruningCycle` 公共方法显式 `dict[str, Any]`，审计运行 id 用 `cast` 从 JSON 取 `str`）。身份保留与业务账本保护不变；一百三十四批专项全过。D7 剩余门槛：其余 application/adapters/composition 严格化、三平台包/容器/安装更新恢复与正式入口切换。

## D6 扩展产物协议（2026-11-11）

扩展工具可在manifest中声明≤4个产物媒体类型，声明进入目录/spec指纹，能力变化即触发漂移撤权。宿主为每个扩展给出唯一可写目录（OMNI_EXTENSION_ARTIFACTS），工具只回传name+media_type，子进程拒绝符号链接/越界/缺失并自行计算bytes+sha256；服务在派发后独立复读复核路径、大小与哈希，逐份发布为不可变对象，ActionResult.artifacts交给settle_action建立action_objects引用，模型文本只含媒体类型与字节数摘要。四类不可验证产物（越界/未声明/缺失/超限）一律归unknown、不发布、不重放；目录每次调用前后清理，宿主关闭时清理根目录。安装表写入显式挑列，避免新能力混入数据库列。

扩展产物专项146项最新全过（真实子进程、对象发布与动作引用、文本无正文、四类unknown、调用方/对象/worker/架构/HTTP回归）；与MCP批合并226个不同节点，全量另跑4548 passed/16 failed/10 errors（失败均为基线遗留）。未做宿主回调、市场更新/安装、产物工作台预览与真实第三方插件验收。详见[扩展合同](V2_EXTENSIONS.md)与[证据](evidence/v2-extension-artifacts.json)。

## D6 外部 MCP 运行适配器（2026-11-11）

MCP服务以既有配置修订（kind=mcp）为身份，不建第二套配置体系、不导入V1。新增v0037七表与16个触发器：运行代际、目录快照、逐工具授权（审阅审计外键）、每run封存、动作绑定与调用记录；状态转移白名单、终态不可变、授权必须匹配当前目录指纹与authority epoch。stdio经OwnedProcess持有真实JSON-RPC子进程，Streamable HTTP支持JSON/SSE与Mcp-Session-Id；已发出请求的超时/崩溃/取消归unknown且不重放，服务器isError是结果错误不断连。目录指纹=工具名+inputSchema（不含description），漂移即撤销授权并要求重新确认，旧页面确认得到mcp_catalog_changed。显式OAuth授权码+PKCE(S256)+单次state（120秒、仅回环回调），令牌入vault且只允许refresh；授权完成撤销旧授权、递增代际、要求重连。模型只在agent档位看到每run封存且已授权的mcp__工具，文本带服务/工具名且≤12000字符。工作台新增MCP分区：被动状态、显式连接/断开/重置、逐工具schema复查、OAuth发起与脱敏调用记录。

150项不同专项最新通过（MCP专项20、浏览器2、调用方54、守卫74），全量另跑4541 passed/16 failed/10 errors；失败均为基线遗留（history/event-pruning、schema-release固定旧schema、service启动效果、extensions UI假hosts），本批另修复媒体worker动态导入与两处迁移时间线3项。未做真实外部MCP/OAuth验收、自动重连策略、主包重打包或Mac/Linux结论。后续为完整扩展链。详见[MCP合同](V2_MCP.md)与[证据](evidence/v2-mcp.json)。

## D5 QQ文本首批：真实业务接入（2026-11-08）

QQ不再经V1 Runtime运行。独立会话授权和人物绑定接入统一事务、模型、上下文和审批投递；群聊仅明确@，只接文本，不继承所有者档案/全局工具。发送为单个文字段，前后核验凭据代次、账号和精确意图，回执不明进入unknown；账号冷却沿已有尝试持久化，不消耗排队尝试。工作台包含私聊/群聊人设、冷却和连接诊断，草稿跨分区保留。

105个不同专项最新通过；最初调用方5个失败/错误已按实际新能力声明及当前迁移目录修正并专项通过，过期的默认无适配器用例由实际安装/未配置拒绝用例替代。旧V25来源保持固定，升级目标随受审当前head，没有放宽目录完整性和源码匹配。17份默认类型、Ruff和前端排版通过。未全量/重打包/真实账号外发，当前业务能力仅QQ文本首批，不等于D5完成。B站/安卓继续开发，详见[QQ合同](V2_QQ.md)。

## D5 B站基础读取与页面发布首批（2026-11-08）

B站基础读取、评论/线程回复和私信、真实mention候选/组件、一次网络放行、回执及新增记录核验已接统一V2用例/凭据/审批/投递；不导入V1，也不建第二套发送状态。新增四表固定规则修订、浏览器模式、消息来源和投递关联；正文不复制，源删除后阻止发送，模型准备工具只返回引用。工作台提供来源选择、规则、读取、待审批真实目标及mention预览。

95个不同专项最新通过，含Windows实际受管进程/Playwright、SQLite与假服务；四视口双主题及14份默认类型、Ruff/格式通过。初次本地故障注入和投递选择用例已修正后通过，未放宽业务断言；网络写未知仍禁止重放。无真实账号/外发、全量、重打包或macOS/Linux结论。后续继续完整安卓接入，字幕/搜索/ASR/抽帧等归D6，不能把基础首批称为完整B站或D5完成，详见[B站合同](V2_BILIBILI.md)。

## D5 安卓所有者操作链（2026-11-08）

显式设备授权、bounded smart-socket辅助进程、设备/boot/用户/恢复代次绑定、界面观察、会话截图及共享owner命令审批已接工作台。ASCII追加和勾选改变需回读成立；一般点击保持unknown并经独立核查，不按ADB命令退出判断业务成功。临时文件、实际进程和租约由生命周期拥有。

48项专项通过，含真实受管子进程、本地假设备、四视口双主题、V28数据升级、恢复代次撤权、撤销/取消及桌面调用方；15份默认类型通过。未操作真实设备、全量或重打包。安卓仍缺模型受限授权、其余旧工具及中文输入，继续同一D5包，不用首批完成代替父阶段。详见[安卓合同](V2_ANDROID.md)。

## D5 安卓模型运行权限与扩展输入

模型通过审批申请本次运行的应用/动作范围，再以独立审批请求实际输入；权限绑定会话内容、策略、期限及设备，来源必须是完成的批准动作。范围拆为规范化包名/动作关系行与指纹，未知来源和权限变化均拒绝；越界应用在UI读取前阻止。共享派发判定不再以桌面命名覆盖其他所有者动作。工作台接权限查看/撤销、设备引用、应用分页及手势/按键/启动参数。滚动和启动有明确后置条件，其余不明输入保留unknown。

72项专项、17份默认类型与Ruff/排版通过；假设备/模型不能替代实机，未全量或重打包。Unicode辅助组件仍待，不将模型首批当作完整安卓/D5完成。见[模型合同](V2_ANDROID_MODEL.md)。

## D5 安卓受控Unicode与恢复义务（2026-11-09）

独立InputMethodService辅助应用与受管Python接通配对密钥、APK/证书、UID/用户/编辑器、单次令牌和完整追加核验。原IME/subtype及启用集合先持久登记，再允许临时选择；未知派发回收后只读，pending义务阻止同设备后续输入。恢复复用所有者动作审批，不依赖已撤销的原设备授权，也不改写原输入unknown。工作台只保存凭据引用和配置草稿，提供明确恢复状态。

57项专项与24项同源JVM核心检查、23份默认类型及Ruff/排版通过；API35 APK已实际编译并校验开发签名，未安装或设备验收。未全量或重打包主程序。D5选定QQ文本/B站基础发布/安卓输入代码范围接通，真实业务仍待；进入D6流式/多模态开发。详见[Unicode合同](V2_ANDROID_UNICODE.md)。

## D6 模型流式与临时预览（2026-11-09）

普通生产回合经显式流式端口，摘要保留非流式，统一预检、配置/凭据代次和结算。供应商原始帧与标准化帧均有界，确认结束后才组合工具，写工具继续审批；SDK补算不能释放费用。临时预览最多4份/各32KiB，不写正文副本或持久事件，重连以持久消息校准。取消等待实际关闭，失败保留容量/归属，生命周期不能先释放存储和锁。

82项专项、12份默认类型及Ruff/排版通过，含真实锁定SDK与隔离HTTP/SSE、四视口双主题和直接调用方。首轮81项后增加原始帧限额并在固定源码重验；非全量/主包或真实模型。图像附件、能力/计量与累计附图预算为下一批，其余D6保持原范围。详见[流式合同](V2_MODEL_STREAMING.md)。

## D6 图片附件与实际附图预算（2026-11-09）

上传/截图对象、原子消息入队、不可变模型图片策略、历史图片选择、低清计量及真实SDK载荷已接共享内核。调用前后授权、已登记/实际附加/未发送/未知状态、工具循环累计、真实压缩与快照复用、删除传播和有界线程回收完成所选离线验证。111表/v2_0032；工作台v43的图片草稿与策略编辑、历史/导入导出接线。

110项专项及类型收口后配置/HTTP/UI复验、17份默认类型及Ruff/排版通过；24例浏览器，四视口双主题。真实SDK只访问本地假服务，无付费或设备动作；未全量/主包重建、平台运行/72小时。仅所选低清图片链完成，不代表所有多模态、真实视觉质量、D6或V2完成。下一真实本地向量，沿现有文档索引接入。详见[图片合同](V2_IMAGES.md)。

## D6 本地ONNX与文档混合检索（2026-11-09）

显式模型选择/指纹、真实CPU子进程、前缀/分窗、版本化文档索引、来源屏障、准确降级和工作台状态已接。增加vectors可选依赖及独立跨平台CI模板，Intel Mac单独运行时不混用索引。61项专项，6项真实ONNX合成图、16例浏览器、8份默认类型及Ruff/排版/锁文件通过；主环境无ONNX的核心/全文链另验。没有下载预训练模型或访问外部模型服务；未执行远程CI、Mac/Linux、全量/主包重建或72小时。真实模型质量、记忆语义接线仍待。下一档位路由，完整D6/V2未完成。详见[向量合同](V2_LOCAL_VECTORS.md)。

## D6 档位、模型与实际工具快照（2026-11-09）

三档规则和分档模型/预留在入队原子捕获，工具交集、一次固定模型升档、同批拦截和常规审批已接线；调用实际准备清单关联原费用，工作台可保存策略并查看选择及逐次统计。93项不同专项最新通过，含16例浏览器；14份默认类型及Ruff/排版通过。首次1项SDK冷加载期限失败已将准备与热回合分开，保留原10秒期限及全部断言并复核通过；没有把重跑称为全量。116表/v2_0033，v45，未全量/重打包、真实收益/质量或其他平台。下一记忆向量召回与语义整理，详见[档位合同](V2_ROUTING.md)。

## D6 记忆向量、混合召回与相关事实核查（2026-11-09）

事实/人物档案/常驻记忆复用本地CPU推理及原来源修订；索引按页显式建立、跨版本隔离，混合召回在发布时再次核验并登记模型上下文依赖。相关候选固定同作用域事实，仅保存可逆人工关系，绝不以相似度合并或抑制原文；删除/恢复/人物及来源变化清空派生向量。120表/v2_0034，v46，81项不同专项最新通过，含7真实合成图、8新UI与1调用方UI；18份默认类型及Ruff/排版通过。新测试三处假设已修正，旧UI一次刷新失败原样保留、独立重跑通过但未认定已修复。无全量/重打包、预训练质量或其他平台结论；下一技能生产链，详见[记忆语义合同](V2_MEMORY_SEMANTICS.md)。

## D6 文本技能与关闭接续（2026-11-10）

对象化文本包、不可变修订/参考文件、历史回滚、明确审阅启用、回合封存/实际披露、模型审批写入与工作台已串联。Windows按orig_filename拒绝ZIP规范化前坏名称；共享关闭为已结束协调器仍遗留的短预算组件等待提供一次有界接续，已加确定性回归。125表/v2_0035，v47，78项不同专项最新通过，含16例UI；16份默认类型及Ruff/排版通过。初次10项失败原样保留，修复后复核对应技能/生命周期/终端/流式调用方；未重跑全量或主包，旧精确整理UI单次刷新问题仍待复核。下一媒体/视频理解，详见[技能合同](V2_SKILLS.md)。

收口复核补齐模型实际可见的编辑CAS修订、参考文件清单和写入ID/状态回执；原后台data不作为模型已获得信息的证据。完整技能/HTTP26项及两个模型写入闭环复核通过，原78个不同节点的最新结果均通过；修正前源码和证据另留独立恢复副本。

## 历史实施记录（以下按原版本保留，不作为当前待办）

## 本次继续修复

审批状态与到期时间改为单次关联查询，避免所有者恰好批准时被第二次查询误判过期。授权提交后取消也会将未结算动作记为unknown并关联审计，不遗留executing。未知工作台分区使用持续提示，切回有效分区后清除。

全仓测试新增进程内与子进程文件系统闸门，拒绝写入真实数据/配置/技能和连接真实数据库。旧运行时在注入data_dir时，默认技能目录随隔离目录注入；不再依赖运行中数据目录的mtime判断测试隔离。

## 上一基础批次验证

固定源码指纹 `6668da1c411e0c571c7ee2264f740285bb5f1b78f4fac133798a6163e9f1a98e`：Windows全仓离线 **2059项通过，497.45秒，0跳过**；V2专项43项通过。Ruff、459份Python格式、53个ES模块、前端排版、索引与任务摘要通过。37个V2源文件在Windows/Linux/Darwin目标下Pyright默认模式均无错误；不等同于全仓严格类型或对应平台运行。直接wheel、sdist→wheel及隔离启动包含87项资源并通过核验。宿主异常钩子在真实HTTP生命周期中完成安装、恢复和日志线程关闭。

原始失败报告与修复原因保存在[证据](evidence/v2-offline.json)，未改写历史测试数字。下一实施断点为`R2.objects`：对象的临时写入/落盘/指纹发布、引用与固定、受控回收和备份引用完整性；随后接人物与摘要。R3还需补完整审计、按记录年龄清理、HTTP未处理异常关联、调试、指标和诊断导出。

## R2对象批次实施

- 对象以Agent UUID与SHA256寻址；16MiB上限，临时文件fsync后发布，复核内容后才登记数据库。取消HTTP请求也会完成已经提交的文件/元数据流程，关闭先排空对象操作。
- 普通配置不保存文件路径真值；对象元数据、固定状态和revision由数据库管理，文件只按受控键定位。动作产物通过action_objects外键引用；消息、记忆来源、技能修订等对象引用从外键关系推导。
- 新文件工作区支持上传、分页、下载及固定CAS更新。界面上传默认固定保留，避免普通用户文件被过期回收；上传不代表模型已收到附件。
- 回收限停机独占执行：plan/apply重新核验实例、结构、引用、固定状态及指纹。先提交可回收元数据与审计，再删除物理文件；占用和崩溃残留由持久日志与doctor报告，相同计划可重试，重新登记会使旧删除意图失效。未登记及未完成文件有24小时保护期，非受控命名的文件保留。
- 快照只包含已登记对象；缺失、链接、大小或指纹不符时拒绝完成。恢复前核验清单与数据库对象集合，复制后复核并落盘，完成后再发布报告。结构升级前先生成含对应对象的验证快照，版本接口读取实际Alembic版本。

本批验证证据将记录在[对象批次证据](evidence/v2-objects.json)。下一实施项为人物、档案与常驻记忆统一，随后补摘要和删除传播；V2整体仍未完成。

## 上一对象批次验证（2026-11-03）

对象批次固定源码指纹 `4f1f8c9479a7cd466bc5c050638454a789ec8627dd25a8dbb959e4892fc22a9e`：Windows全仓离线 **2078项通过，493.30秒，0跳过**，其中V2用例62项、对象用例17项。46个V2源文件在Windows/Linux/Darwin目标下Pyright默认模式无错误；Ruff、469份Python格式、53个ES模块、前端排版与索引检查通过。直接wheel、sdist→wheel与隔离启动验证通过，包含87项界面资源。

离线SQLite连接现统一显式关闭：读取设置query_only与外键，备份/恢复在连接关闭后计算文件指纹，避免残留WAL影响重复恢复。恢复同时验证旧令牌撤销。HTTP未处理错误的请求关联已补齐。

R2.objects记录为开发及Windows离线验证完成、待其他平台真实验收；上传文件尚未进入模型上下文，原生工作器产物发布的业务接入仍待迁移。**下一断点为R2.people：人物、所有者档案和常驻记忆统一；默认入口仍为V1。** 证据见[本批记录](evidence/v2-objects.json)，上一基础批次数字保留为所属源码版本的历史证据。

## R2人物、所有者档案与常驻记忆

人物服务、独立API与工作台已接入；所有者人物在每个Agent内唯一并受数据库保护。账号关联目前明确标为人工记录，不授予实例权限，通道身份核验仍待R6。档案与常驻复用记忆条目、确定修订的来源和遗忘墓碑，UUID加revision共同防止旧草稿覆盖。

工作台支持人物创建/改名/遗忘、账号关联、档案与常驻编辑、UTF-8来源对象导入、已保存正文导出和有界修订历史。草稿按Agent和人物保留在有界内存中，冲突保留正文；迟到响应不能替换当前人物。初始化与保存重载竞态已增加回归。

运行准备捕获所有者文档的确定修订；普通编辑供新运行使用，遗忘在下一次模型请求前重新核验。上下文记录使用关系外键，不复制正文；原V2快照缺少修订的记录明确标为legacy_unresolved。遗忘清除全部修订正文、来源摘录、全文索引内容并释放来源对象引用，人物整体遗忘任一步失败都会回滚。墓碑只保存域分离指纹，模型不可重复提取相同指纹的正文或来源摘录；人工明确重新编辑允许建立新条目。源消息及文件本体不会随记忆遗忘被擅自删除。

迁移在真实子进程中注入中途退出，验证旧结构完整、验证快照存在且再次升级可成功。该演进仅针对新V2数据；不读取或导入V1数据。

固定源码指纹 `1d868c738d69b4d50d65d0cd63bad25023307ca88684ddc217f2b85e848ee59a`：Windows全仓离线 **2091项通过，524.64秒，0跳过**，其中V2用例75项、人物用例9项；V2专项门禁75项通过（96.60秒）。598份源码/测试/依赖/构建配置在全量前后指纹一致。51个V2源文件在Windows/Linux/Darwin目标下Pyright默认模式无错误；Ruff、475份Python格式、54个ES模块、前端排版与索引通过。直接wheel、sdist→wheel及隔离启动通过，包含88项界面资源；不等于对应平台运行或全仓严格类型通过。

R2.people记录为开发及Windows离线验证完成、待macOS/Linux运行验收。来源墓碑仅匹配归一化正文或来源摘录指纹，不是语义遗忘检测器。当前历史读取仍限100条，持久摘要、压缩与截断状态尚未接入，其他业务的删除传播也未完成。

该人物批次结束时的断点为R2.summaries：持久摘要、上下文压缩和派生内容删除传播。**整个V2仍未完成，默认入口仍为V1；业务全面迁移、原生平台适配及三平台真实验收继续待推进。** 版本、原始报告和限制见[人物批次证据](evidence/v2-people.json)，人工操作步骤见[开发部署说明](../deploy/v2/README.md)。

## R2持久摘要、上下文压缩与删除传播（历史批次）

已接入确定来源的持久摘要与父链、关系化上下文、单调快照序号、摘要费用用途、跨会话约束和中断对账。普通回合与摘要复用ModelCalls；压缩无工具、每轮最多3次，按完整回合选择来源，保留最近8个回合。摘要最长4000字符、单批最多64条/32000字符；序列化请求上限48000字符，不等同于供应商token窗口。

历史不再固定静默截断100条。压缩失败、预算不足、批次耗尽或完整旧回合过大时，保留有界最近回合并登记省略数与原因。当前输入、人设、记忆或工具过程超出容量时明确拒绝，不拆散工具对，不宣称成功。工作台显示登记状态与省略原因，支持查看摘要和显式遗忘消息。

删除传播采用保守会话边界：来源消息/动作及上下文依赖影响到的会话，其摘要正文清空、旧历史退出后续模型和摘要，关联派生事实递归遗忘。记忆搜索先登记依赖再返回正文。聊天原文保留，只有显式消息删除才清空指定原文并释放附件引用。受影响的活跃运行停止后续执行并报告context_invalidated；在途请求先回收，删除本身不主动取消供应商请求。已经发出的请求和外部动作不可撤回，返回后也不能把旧回复保存为成功，未知费用与动作仍待核查。

单测包含真实进程在摘要发布提交前后退出，以及v2_0004结构重建中断；数据库与费用分别保持已提交的真值，旧结构可重开，升级前验证快照仍可恢复。遗忘后的重启不重用旧摘要。本批未接入向量、来源文档、后台整理等未来派生关系。

固定源码指纹 `06fcdfba280194f4574b3ad748af802e28f9c92c29e21f4b05cb4e3596710bc9`：Windows全仓离线 **2117项通过，660.83秒，0跳过**，包含101项V2用例（18项摘要/删除后端及故障回归、8项新增摘要界面用例）。607份源码/测试/构建配置在测试前后指纹一致；57个V2源文件在Windows/Linux/Darwin目标下Pyright默认模式无错误。Ruff、483份Python格式、55个ES模块、前端排版、索引及打包验证通过，界面资源89项。类型检查不等于对应平台运行或全仓严格类型通过。

首次全量2115项通过、1项公共弹窗规范失败；已将原有OmniUI.ask提取为共享dialog.js，V1和V2复用，完整同步模块组/页面/延迟样式的缓存版本，守卫断言保留。另补删除前已排队输入的回归：返回context_invalidated，不误报容量不足，原文保留可明确重发。原始失败与最终通过报告同时保留在[证据](evidence/v2-context.json)。

本批记录为开发和Windows离线验证完成，真实模型质量、模型token容量及macOS/Linux运行仍待验收。字符边界不是供应商token窗口；来源登记也不等于逐项事实验证。删除采用保守历史边界，在途请求先回收；尚未接入的向量/文档/整理派生关系仍待补齐。

以上为摘要批次历史断点；配置批次当前进展见下节。

## R2.configuration：配置修订与模型预算

模型、通道、MCP、人设预设使用同一配置目录、类型化选项表和不可变修订；Agent偏好拆为结构化字段，工具禁用名单独建表。运行仅引用确定的偏好与模型修订，不再复制整份配置JSON。普通编辑用于新运行；工作区/工具权限变化和模型停用采用独立权限版本，模型重新启用不会恢复旧运行授权。配置创建、编辑、归档与偏好保存都写入同一事务的审计记录。

模型配置声明上下文容量、回答/摘要输出上限、安全余量及计量器版本。普通调用与压缩共用计量、授权、费用路径；发送前重新核验来源和模型权限。压缩按完整历史回合缩小来源批次，最终上下文按完整回合保留；当前输入无法容纳时明确失败。LiteLLM收到明确max_tokens；供应商报告输出超限时不能发布成功，已知费用仍结算。运行与调用登记计量方法、估算输入、输出上限和声明容量。

默认计量是序列化UTF-8字节量加固定信封余量的保守估算，不宣称等于供应商真实tokenizer，也不保证供应商隐藏提示。精确模式要求注入匹配模型的专用计量器；未安装、超时、身份或质量不匹配均拒绝发送，不自动下载模型资料或探测收费接口。容量来源明确为用户声明，真实容量和摘要质量仍待验收。

工作台设置提供唯一编辑入口，区分“导入为草稿”“保存配置”“应用预设到设置草稿”“保存设置”；导出读取已保存版本。草稿按Agent/类型/配置隔离，有界保存在当前页面内存，关闭或刷新网页会丢失未保存内容。并发覆盖保留草稿，重新载入等待在途写入，替换未保存的新配置需显式选择。通道和MCP当前仅持久化声明，尚不连接、认证或启动进程；OAuth归R6，不以配置项存在宣称已接入。

本批固定源码Windows完整离线验证2149项通过、0失败/0跳过（691.58秒），其中V2为133项、配置新增32项。63份V2 Python源码在三平台目标下默认模式类型检查通过；493份Python格式、Ruff、Prettier、56份ES模块语法、索引及直接wheel/sdist→wheel/隔离空库启动均通过。测试期间618份冻结文件指纹不变，详细报告见[配置批次证据](evidence/v2-configuration.json)。这不等同于严格类型全仓通过或三平台运行验收。v5升级仅演进既有V2开发实例，升级前一致快照与DDL事务保护仍保留；无法解析的旧V2运行标为中断、关联待执行工作标为unknown，不重放，不释放未核清费用。V1数据与默认入口保持独立。

配置批次结束时的断点为R2.documents，当前进展见下节。

恢复副本见`temp/private/v2-configuration-source-20261103T194740+0800/manifest.json`：796份源码、文档与交付声明完整保存，附完整测试报告、指纹和本地差异；不复制活跃运行数据或凭据。源码已核对压缩包逐项指纹，恢复时使用新目录，不覆盖当前工作树。

## R2.documents：来源文档、派生索引与删除传播

来源文档已接入独立应用服务、仓储、API和工作台。原文作为受控UTF-8对象保存，每次保存产生不可改写的来源修订、指纹、所有者操作审计和引用保护；编辑使用revision，网页新建使用稳定UUID防止响应丢失后的重复创建。正文最多256KiB/100000个Unicode字符；历史修订供查看，当前编辑有唯一入口。文件导入为网页草稿后仍需显式保存；API也支持从已登记的来源对象导入。草稿仅存在当前网页内存，冲突保留，导出读取已保存版本。

全文块与向量均为派生数据。保存建立1000字符确定分块及FTS，保留Agent、来源修订及精确字符范围；全文无结果时保留中文子串回退。重建全文从已验证对象读取，原子替换分块并失效旧向量。可注入LocalVectorizer端口，要求明确版本、1–512维、有限非零向量，每批最多8块，单次调用超时10秒。保存不计算向量，只有显式重建才调用本地端口；尚无随包安装的本地向量适配器，默认明确降级全文。向量查询最多扫描512候选块，达到上限明确标记，不宣称全库语义召回；真实组件质量与大规模ANN仍属R6验收。

文档检索/读取工具返回前，在同一事务登记运行、动作、文档修订和实际返回的字符区间。模型来源必须逐字匹配当前回合读过的区间；工具来源必须逐字匹配本回合成功结果。人工操作继续使用服务端生成审计身份，不能由模型伪造。来源锚定说明引用真实内容，不等于已验证事实真伪。

删除在一个事务中解除全部修订的对象引用、删除分块与向量、失效重建任务、递归遗忘来源事实、清空相关摘要并建立会话历史边界。已保存的文档工具结果去除原正文，迟到结算不能重新写入旧内容；在途模型请求无法撤回，返回后丢弃旧答复并保留真实费用。原始聊天、独立固定文件和外部备份仍遵循各自保留策略；物理磁盘擦除不在逻辑遗忘合同内。

删除先登记持久清理请求，正文清理失败则整体回滚、记录失败和审计，再次删除可重试。删除新修订时同时收口此前的清理请求，原始失败审计保留；迟到异常不能将已完成请求改回失败。进程退出留下pending请求，doctor以非零状态报告；不会当作已完成。文档任务队列最多8项、2个并发操作，HTTP取消不释放仍在执行的槽位，关闭有残留则明确报告。

新增`indexes check/rebuild`用于独占停机状态下的FTS一致性检查和重建，覆盖记忆与文档；检查使用回滚校验事务，重建与维护审计一起提交。它不改来源正文、不重新生成向量；缺失/损坏分块需先从文档页重建。实现依据[SQLite FTS5维护命令](https://www.sqlite.org/fts5.html#special_insert_commands)。

最终固定源码Windows完整离线验证 **2185项通过，0失败/0跳过，536.97秒**；其中V2为169项、本批新增36项（26项后端/API/故障、10项界面）。626份冻结文件在测试前后指纹一致，当前代码版本为`9b0b49fa9873027a8f853f1494b462ff3f4fe75f30458a8ba024363670b29dba`。Ruff、500份Python格式、67份V2源码的三平台目标Pyright默认模式、Prettier、57份ES模块语法、界面守卫、索引和打包通过，包含91项界面资源。不是全仓严格类型通过或macOS/Linux实机运行证据。

首轮2184项通过（559.11秒）对应的测试尚未覆盖独立核对复现的“旧修订清理失败、编辑新修订、删除新修订后旧请求残留”的问题。修复后补回归并重新全量，原报告及复现证据完整保留，只有最终固定源码结果代表当前状态，见[文档批次证据](evidence/v2-documents.json)。真实模型、本地向量组件质量、macOS/Linux运行仍待验收。

**后续断点：R4.scheduling，持久调度、到期去重、时区和资源租约。R2/R3/R4/R6仍有未完成流程，整个V2尚未完成，默认入口仍为V1；原生适配和三平台真实验收待推进。**

本批恢复入口：`temp/private/v2-documents-source-20261103T220143+0800/manifest.json`，805份源码/文档/交付声明逐项指纹与ZIP验证，附首轮及最终报告。副本不包含活跃运行数据和凭据；恢复到新目录，不覆盖在途工作树。


## R4.scheduling：持久调度、到期去重与执行所有权

日历规则、固定UTC秒间隔和单次计划使用同一个应用服务。计划头引用不可变修订，执行进度单独保存；每次到期固定指令、会话、时区数据版本和计划修订。新计划默认停用，启用后使用正常回合的模型配置、费用预算、上下文、工具审批和取消路径，不创建第二套执行器。尚未接入通道投递，完成结果保存在计划专用会话中。

日历规则支持五段数字、列表、范围和步长；日与周同时受限时采用OR。所有平台显式读取锁定tzdata包，不依赖系统时区库。夏令时缺失的本地分钟跳过，重复分钟仅登记fold=0；时区数据库版本变化后停止新登记，需核对并保存新修订。间隔计划保存UTC锚点，单次计划必须带时区。停机错过多个尚未登记周期时只保留最近一次，并记录合并起点，不伪造执行次数；已经登记的待执行指令保持不变。

到期身份在整个计划内唯一，不随编辑修订重置；进度高水位防止时钟倒退后重复登记。到期记录、消息、运行、工作项和审计共同提交；入队失败的savepoint回滚全部局部回合，但保留blocked到期记录供重试。每个计划最多一个待入队或未结束回合。停用/归档取消尚未入队的记录，已经入队的回合须单独取消。编辑不覆盖在途实例。

工作项使用事务内递增的全局排队序号；原子领取检查会话输入顺序、Agent启用状态和计划安全条件。领取持久记录启动身份、令牌、尝试次数与会话资源租约fence；排队状态与“已领取、尚未开始”分开。执行开始、模型调用、动作授权和最终输出核验租约。正常运行续约；过期调用不能发布迟到成功，也不会被另一执行者接管仍未结束的资源。进程重启只将明确没有外部意图的未开始领取放回队列；已经开始的运行中断，未知费用继续占用预算，未知外部动作和投递继续保留。

上一轮未完成，或存在尚未核查的模型调用、动作、投递时阻止后续到期与领取；执行检查点也再次核验，覆盖迟到未知结果。人工放行必须匹配当时待核查记录的指纹，并逐条持久记录对应实体与状态，不能只按“已核查到第几次”跳过所有旧记录。放行仅允许后续调度，不重试旧回合、不结算未知费用、不修改未知结果；同一运行后来新增的未知记录会重新阻塞。回归中发现并修复了序号放行过宽的问题。恢复快照会提升核查代次，保留旧核查记录但要求重新核查；指纹绑定代次，旧页面不能用旧指纹放行。

后台仅一个受管任务，每轮最多16个候选，循环游标避免前排阻塞计划饿死其他计划；实例最多启用256个计划、最多保存4096个计划。HTTP分页最多200条，工作台历史显示最多500条、草稿缓存最多64项。工作台提供默认停用草稿、修订冲突保留、IANA时区、下次UTC、来源修订、到期账本、取消、未知记录身份和显式核查。勾选“包含归档计划”后可只读查看归档定义，继续查看账本并取消其在途回合，归档不会隐藏处理入口。草稿只存在当前网页内存。

恢复到新目录先落盘未完成标记，最终校验和保护事务提交并发布恢复报告后才清除标记。标记存在时启动与离线诊断拒绝把副本当成可运行实例；报告发布后中断可通过重复验证完成清理，其余部分副本重新恢复到另一新目录。恢复将旧排队/活跃运行标为中断、工作项隔离为unknown，保留费用预留，待投递内容改为unknown，并暂停所有计划进度；不会重放备份后可能已经完成的任务。原始快照不修改，历史修订、原文、核查和审计保留。重新启用须核查外部结果与费用并显式保存计划。备份之后发生的账号变更与用量无法由旧快照推断。

最终固定源码Windows完整离线验证 **2233项通过，0失败/0跳过，559.85秒**；其中V2为217项、本批新增48项。638份冻结文件（含三份生成索引）测试前后指纹一致，代码版本为`2c2a795b26a5aa0df1393c06757727f858ea5a6f6de4cf4f004ec479ad54e6a4`。Ruff、508份Python格式、73份V2源码的三平台目标Pyright默认模式、Prettier、58份ES模块语法、界面守卫、索引、直接wheel/sdist→wheel及隔离空库启动通过，包含92项界面资源。详见[调度批次证据](evidence/v2-scheduling.json)。不是全仓严格类型通过或macOS/Linux实机证据。

首轮2226项通过、2项V1进程退出/取消测试失败（700.91秒），定点复核2项通过；原因未被确定，不把单独重跑视作修复或全量通过。归档入口补齐前的固定源码全量2232项通过（645.86秒），只代表该历史版本。完成归档定义只读查询及在途取消入口后，按最终源码重新全量，原报告均保留。没有缩短业务超时、跳过测试或放宽结果断言。

恢复副本见`temp/private/v2-scheduling-source-20261103T234105+0800/manifest.json`：815份源码、文档和交付声明逐项指纹与ZIP核验，附原始测试、源码指纹、打包证据和工作树差异；不包含活跃运行数据或凭据。恢复到新目录，不覆盖在途工作树。

**下一断点：R4.delivery，持久投递、逐次尝试和未知结果核查。** 先冻结端口与状态机，再实现原子领取、确定未发送时的有界退避、统一关闭与核查界面；R4先用隔离假通道验收，真实账号连接和发送仍归R6明确目标及授权后的验收。现有“允许后续调度”不代表旧消息已送达，不授权重发，也不能释放未知模型费用。整个V2仍未完成，默认入口仍为V1；真实向量组件、原生适配、macOS/Linux运行及三平台真实/72小时验收继续开放。


## R4.delivery：持久投递与共享关闭（开发及Windows离线验证完成）

新增投递意图、单次尝试、不可变回执及人工核查；当前63张声明表、Alembic v2_0008。正文只引用已保存的助手消息，固定实际上下文代次、正文SHA-256、发送账号、目标、适配器版本和通道修订。缺失上下文快照、来源失效、配置变更、凭据替换和审批超时均不能取得发送权限。没有V1数据迁入；v8只演进既有V2开发库，旧占位投递一律标为未验证未知结果，升级前仍生成一致快照。

每个意图需所有者批准精确指纹，授权300秒。全局最多4个活跃发送、64条等待；账号资源键跨Agent共享，严格按持久队列顺序派发。仅适配器明确证明未发送且允许重试时退避2/4秒，最多3次；异常、超时或发送开始后取消均进入unknown。适配器忽略取消时，实际任务、并发名额和账号租约仍保留；租约过期不构成接管依据。回执中的sent仅表示服务方接收，不能宣称对方已读。

核查在独立不可变账本保存证据，固定当前修订、恢复代次和结果版本；原始unknown与未知模型费用均保留。再次发送必须新建意图、重新审批；原投递可能已发送时还需明确确认重复风险。首次迟到观察会推进outcome_revision，使旧核查及调度放行失效；归档只改变列表展示修订，不改结果版本。仍持有本地发送资源时禁止人工核查。回合生成已完成也可单独取消外发；取消调度回合会同时取消其未结束投递。

最终输出、运行完成、可选投递意图与审计在同一事务提交。若固定通道在生成过程中撤权、正文超出发送上限或队列已满，保留生成结果并登记blocked意图；不能把已生成误报为已发送。普通工作台对话尚不自动路由到外部账号，R6通过此入口接入真实通道。

正常重启保留待人工审批意图，撤销未开始发送的旧授权并取消该意图；已经开始的转为unknown，均不自动重放。恢复备份则把所有未结束投递隔离为unknown、撤销审批、推进核查代次，沿用原恢复完成标记并暂停计划。外发来源检查接入消息、记忆、人物及文档的共同删除屏障。

组装根采用Lifetime管理部分启动回收和统一30秒关闭预算。先同时停止并排空回合、调度及投递，再关闭来源文档、对象存储、数据库、追踪、日志和实例锁。未结束关闭任务继续受管，重复关闭不会重复创建在途清理；仍有残留时保留数据库及OS实例锁，明确报告未完成。独占锁使用显式OS描述符，不能因Python对象丢失而提前释放。

`/api/v1/agents/{agent_id}/deliveries`提供有界分页、精确审批、尝试回执、取消、归档与人工核查，另有能力及通道查询。工作台增加持久投递分区和助手消息的投递草稿入口，区分生成、排队、服务方接收及未知；草稿按Agent隔离，迟到请求不能覆盖其他选择，归档后仍可取消、核查和建立新意图。自有资源缓存组递增v11，共享主题/对话框仍v7。

默认没有实际发送适配器。测试使用注入的可信假端口；R6的QQ、B站等真实账号与目标验收未完成。端口实现必须在动作前核验预期的实际发送账号，不能只信配置中的账号文本。三平台原生适配与真实/72小时验收仍待实施，默认入口继续V1。

本批固定源码Windows全仓 **2275项通过，0失败/错误/跳过，467.04秒**，其中V2为259项、新增42项；652份冻结文件前后一致，代码版本`d17c8e6fe68cb31aaca35cb572199d3a299b79c63ab5a7cd48dbea8ac5c15905`。521份Python格式、80份V2源码在三平台目标下默认模式Pyright、Ruff、59份ES模块、Prettier、界面守卫、索引、直接wheel/sdist→wheel与隔离空库启动通过，包含93项界面资源。恢复副本为`temp/private/v2-delivery-source-20261104T034238+0800/manifest.json`（829份源码/文档，排除运行库、凭据和无关用户文件）。前两次全量失败/关闭错误及具体修复均保留在[投递证据](evidence/v2-delivery.json)，不改写历史结果。后续优先补R3.observability：完整保留策略、审计/指标与脱敏诊断包；受管原生/插件宿主、完整业务接入和三平台验收仍保持开放。


R4关闭验收补充：Windows CPython 3.12 的 TCP 连接在回调收尾时可能因10054重置中断socket关闭及server detach。本项目的托管启动入口使用独立事件循环工厂，仅在已核验的CPython收尾代码帧出现该错误且资源仍归该transport所有时完成关闭与单次detach；不修改全局asyncio类、不改用缺失异步子进程支持的循环。协议回调自身的异常及其他错误继续报告。离线测试同时覆盖确定性重置、错误不被吞掉、重复回收、真实本地TCP重置及异步子进程。实现核查参考[CPython Proactor源代码](https://github.com/python/cpython/blob/v3.12.14/Lib/asyncio/proactor_events.py)，不代表其他平台原生桌面验收完成。

V2凭据写入与删除收口为`PUT/DELETE /api/v1/credentials?reference=...`，引用支持`fixture/key`等层次名称；请求在有界UTF-8 JSON与引用校验后，先撤销旧授权，再写凭据后端。旧V2开发路径不设转发。隔离加密凭据库回归验证斜线引用、撤权、非法引用、大请求及日志不含凭据，未接触系统真实凭据。


## R3.operations 运行诊断批次

1. 已接固定维度的进程内指标和持久状态汇总。耗时按请求、数据库读写、回合、模型、摘要、工具和投递分类，预留对象分类；结果复用成功、错误、拒绝、取消、未知五项领域枚举。最多45个序列，会话、运行、账号、目标和用户文本不进入指标标签；数据库汇总只返回计数及明确的费用预留/未知数量，不把未知费用显示为零。未知模型、异常写动作及迟到投递按业务结算状态计数。
2. 已接在线诊断与独占离线只读导出。包内只有版本/平台、现有组件健康、配置分类数量、数据库结构与聚合值、有界日志关联；默认排除聊天、业务正文、凭据、浏览器状态、自由格式异常文本和磁盘路径。压缩/解压总量2MiB，校验3成员/CRC，先私有临时写入和fsync再不覆盖发布。CLI拒绝远程源、重定向与HTTP内容编码，离线不自动迁移数据库。原生RSS/受管子进程采样仍为空值，不能当作零或实机支持证明。
3. 已接显式限时调试、Agent/boot/capture隔离、最多1小时、全体受管文件128MiB及24小时清理。启停有持久审计；审计失败不留下启用状态，停止审计失败保持停止并重试。DEBUG仅关联/固定状态/耗时，不读取message或异常值，共享有界队列和写线程。工作台可启停及有界查看；旧capture请求不能控制新捕获。普通日志每分钟也清理过期段。凭据脱敏覆盖字典键，注册表有界且不淘汰旧凭据；OpenTelemetry不采异常事件负载。
4. 保留与清理另建可审阅计划和可恢复工作项：执行正文30天、摘要/审计180天；未完成、unknown、未结算、固定或仍被引用内容必须保护。删除正文、派生索引及对象引用共同推进；中断后不能恢复成成功或从旧索引复活。这个步骤尚未实现，不能把现有诊断日志轮转当作完整业务保留策略。

每一步先运行相关回归及调用方；稳定批次再完成固定源码全量与交付检查。R4维护/整理、R5原生平台、R6真实通道/向量和业务迁移继续保留在总台账中。

业务保留的下一批先实现30天执行正文裁剪：保护整次被来源锚点/文档上下文引用的运行，保护未终态、未知动作/费用、待执行工作、未完成/未核查投递和持有资源；新增运行固定与裁剪标记。只裁剪动作参数/结果及事件载荷，保留消息、账本、身份、序号和指纹。离线plan/apply须先验证一致快照，执行前重新核验引用/版本并记录可续跑批次；恢复进入新目录。180天审计/摘要及对象引用释放随后接入，不复用遗忘barrier作为TTL清理。

本批固定源码Windows全仓 **2321项通过，0失败/错误/跳过，505.05秒**；V2为305项，新增46项，666份冻结文件前后一致，代码版本`487ea6aaa9deba61768d4fd6cfa0c694f3b76b3f7bd46e1ff0f722a1576796d6`。Ruff、534份Python格式、88份V2源码的三平台目标默认模式Pyright、60份ES模块、Prettier、界面守卫、索引及直接wheel/sdist→wheel/隔离空库启动通过，包含94项界面资源。恢复副本`temp/private/v2-operations-source-20261104T080029+0800/manifest.json`包含845份源码/文档，排除运行数据、真实凭据和无关用户文件。历史失败与修复保存在[诊断证据](evidence/v2-operations.json)。这不是全仓严格类型或macOS/Linux实机证据。

**下一断点R4.safety：统一受管异步凭据及撤权/发送前核验，修复worker响应超限与写后异常的副作用确定性；随后R3.retention。完整V2、默认入口切换、原生平台及三平台真实验收仍未完成。**

## R4.io_safety：实际I/O归属与副作用确定性

新增单个Instance所有的凭据执行器及串行修改服务，取消后追踪真实Future；模型准备与发送拆开，凭据等待后重新校验权限。实例级意图/结果审计归全局所有者，64表/v2_0009，不混入任意Agent。Native系统存储不选择第三方明文/链式后端；加密库更新/删除使用持久发布。worker协议升级v2，共享严格校验；写后异常与畸形结果不得改成无副作用，超限保留已知状态与产物引用。关闭的新预算不会误复用旧短预算的失败结论。

固定源码Windows全仓 **2371项通过，0失败/错误/跳过，489.71秒**；V2为355项，新增50项，674份冻结文件前后一致，版本`79e35ee47183186e0dafab3fa992282b4c0a29e1c6e930bb6c50f1cb4a912f65`。542份Python格式、Ruff、94份V2源码三平台目标默认Pyright、60份ES模块、Prettier、索引、94项资源的直接wheel/sdist→wheel/隔离启动通过。恢复副本`temp/private/v2-safety-source-20261104T182905+0800/manifest.json`共854份源码/文档，真实数据与凭据排除。详见[本批证据](evidence/v2-safety.json)。

**凭据业务权限仍需后续闭环**：审计已经保留未知结果，但还须独立的引用注册、操作状态、活跃租约与版本化核查，明确阻止未知凭据的后续使用/修改。该项作为下一断点R4.credentials_authority，不能宣称整个R4或V2已完成。随后R3保留清理；原生文件读取/身份、供应商凭据种类/环境发现/客户端、完整业务和三平台实机验收继续开放。默认入口仍为V1。

## R4.credentials_authority：业务授权与人工核查

67表/v2_0010新增引用、操作和不可变核查记录；未知状态阻止模型/投递与再次修改，配置保存不能解除。活跃操作绑定boot/执行令牌，核查绑定revision/恢复代次/策略指纹；取消后仍由实例拥有。普通重启不重放；恢复备份全部重新核验。完整合同见[凭据记录](V2_CREDENTIAL_AUTHORITY.md)。

固定源码Windows全仓 **2399项通过，0失败/错误/跳过，490.67秒**；383项V2、28项新增、678份冻结文件前后一致，版本`51a508f7b2bcc2b164c6c07eba4bf39106c2a35d3f922bd15e682631a7e03332`。Ruff、545份Python格式、96份V2源码三平台目标默认类型检查、61份ES模块、Prettier/索引/重复定义、95项资源的直接wheel与sdist→wheel/隔离启动通过；这不是严格全仓类型检查或其他平台运行验收。恢复副本`temp/private/v2-authority-source-20261104T195242+0800/manifest.json`，共860份源码/文档，排除真实数据和凭据。详见[本批证据](evidence/v2-authority.json)。

下一断点R3.retention：运行固定、30天执行正文保护与可恢复裁剪，再处理180天摘要/审计。原生文件上限/身份、平台报告、完整业务/扩展、供应商凭据与SDK生命周期及三平台实机仍开放；整个R4与V2未完成，默认入口仍为V1。

## R3.retention_preview：运行固定与只读保留预览

67表/v2_0011增加独立固定版本；API及工作台支持本次运行固定，审计与CAS同事务。新预览按50条上限检查旧终态运行及共同保护条件，只返回元数据与内容指纹。离线入口复用独占锁和中央只读事务；预览 can_apply=false，不删除聊天、执行正文或对象。完整规则见[日志与保留合同](LOGGING.md)。

固定源码Windows全仓 **2417项通过，0失败/错误/跳过，461.59秒**；401项V2、18项新增、682份冻结文件前后一致，版本`f2b598349f63ecd19324e39e8962a2f57f6b7e01218dce78876d57cc3513db1d`。Ruff、549份Python格式、99份V2源码三平台目标默认类型、61份ES模块、Prettier/索引/重复定义、95项资源的直接wheel与sdist→wheel/隔离启动通过。恢复副本`temp/private/v2-retention-source-20261104T204302+0800/manifest.json`共865份源码/文档，排除真实数据和凭据。详见[本批证据](evidence/v2-retention.json)。

下一断点R3.retention_apply：独立核验备份和执行计划、再次判定来源/费用/资源保护、显式正文裁剪标记、事务与中断幂等恢复；180天摘要/审计及对象引用释放仍开放。不能把本批只读预览写成完整保留清理。完整R3、R4和V2尚未完成，默认入口仍为V1。


## R3.retention_apply — Windows离线封存

固定源码 `3d29bf4ece5aa91cb72ebcb525fe925dbbf2b64ee83b142ba7b6fc4bb4ea80ab`（688份文件）全仓 **2457通过，0失败/错误/跳过，497.17秒**；其中441项V2，新增40项。已完成30天正文的独立计划、验证备份、逐运行事务、持久进度、进程中断续跑、明确标记和显式取消，补齐恢复目标锁/WAL核验。Ruff、101份V2生产文件三平台目标类型检查、前端约定、重复定义/索引和两条包链通过；目标类型检查不等于平台运行验收。详见[证据](evidence/v2-pruning.json)与[操作合同](V2_EXECUTION_RETENTION.md)。

对象引用暂不释放；下一断点为R3.retention_references。180天审计、剩余业务及原生平台/真实三平台72小时仍待推进。整个V2不标记完成，默认入口仍为V1。


## R3.retention_references — Windows离线封存

固定源码 `33a70e0d5b4f4854222bed761d1e14800d3e8ddd74d85ecf693253aa0fcc12fe`（697份文件）全仓 **2606通过，0失败/错误/跳过，565.82秒**；其中590项V2，新增149项。完成裁剪后的精确产物引用计划、验证备份、逐目标事务、真实进程中断续跑与部分取消，并与独立GC接线。共享消息/文档/其他动作引用继续保活；历史不建立对象外键，物理GC后仍能续跑。Ruff、106份V2生产文件三平台目标类型检查、前端/重复定义/索引与两条包链通过；不等于真实平台验收。详见[证据](evidence/v2-references.json)与[合同](V2_EXECUTION_RETENTION.md)。

下一断点为R3.retention_audit。原正文部分裁剪后取消的引用仍保守保留；180天摘要/审计、剩余业务、原生平台及三平台72小时仍待推进。整个V2不标记完成，默认入口仍为V1。


## R3.retention_audit.cursors — 开发完成，当前源码全仓待复验

源码 `c24a30d1871307140f2a70aa7dbb2a152978bfb061b97476a304764321faf16a`（708份文件）已保存可恢复开发副本。完成74表/v2_0014、持久分配/epoch/删行修订、三类cursor API及工作台；原地事件正文更新不计删除，180天预览和删除仍未接入。Ruff、110份V2生产文件三平台目标类型、前端/索引及源码/wheel链通过；不等于实机验收。

唯一全量在修复前源码 `87a3701b1beebfc4fc7b79c9f62285b7cda2c7d7c7c5f745760347ef72afc3c0` 上 **2659通过、1失败、0错误/跳过，568.14秒**，全部643项V2通过。ConPTY关闭确认竞态已修，相关24项通过（13.35秒）；不把专项写成全量全绿。按用户优先开发、减少全量频率的要求，当前源码全量复验合并下一开发阶段。冷领域node单次3.82→0.38秒，相关84项过，不推算全仓比例。[证据](evidence/v2-history.json)。

下一断点R3.retention_audit.preview，见[设计](V2_AUDIT_RETENTION_DESIGN.md)。整个V2未完成，默认入口仍V1；原生与三平台真实/72小时待验收。


## R3.retention_audit.preview — 只读候选已开发

源码 `9fa9ddbdb8fc88303f7c1a8957909cfeb240786a812e47df47a1e469bad3c998`（714份冻结文件）完成180天审计元数据候选、精确种类/主体注册表、完整裁剪依据及来源/费用/未知/维护保护，HTTP、离线CLI与工作台接线；游标固定时间和分配上界，业务保护逐页重查。**138个不同专项通过**，Ruff/格式、112份V2源码三平台目标默认类型、前端/索引及直接wheel、sdist→wheel、隔离启动通过。未重复全量；当前源码仍待阶段合并复验，不标全绿。详见[本阶段证据](evidence/v2-audit-preview.json)。

下一断点R3.retention_audit.apply，包含持久计划、验证备份、逐目标原子执行/取消与恢复；事件删除及语义摘要治理另行推进。整个V2未完成，默认入口V1；真实业务、原生和三平台/72小时验收待推进。


## R3.retention_audit.apply — 持久审计清理已开发

源码 `e3060cc92a0f92f766f718eafec50a58094e8cc056f2ca6218c372986f582224`（722份冻结文件）增加76表/v2_0015、严格有界计划、原备份核验、逐目标原子删除/证据、进程中断续跑、取消和恢复隔离。231个不同专项通过，Ruff/588份Python格式、115份V2源码三平台目标默认类型、前端/索引及两条包链通过；不等于实机或全仓验收。本阶段开发完成后集中专项，未重复全量，当前源码全仓仍待复验。详见[证据](evidence/v2-audit-apply.json)。

下一断点R3.retention_audit.events；运行、消息、来源、防重放和费用身份继续保留。整个V2未完成，默认入口V1；真实业务、原生及三平台/72小时待验收。


## R3.retention_audit.events — 事件明细清理已开发

源码 `5376218131a483ef8c35d574ba2e22b59daf0f8e286012b12f76bcff7595f780`（734份冻结文件）完成78表/v2_0016、事件独立政策/台账及预览/离线备份清理、取消与恢复隔离；审计/事件共用执行核心和来源保护，原裁剪与引用证明不改写。269个不同专项通过，Ruff/600份Python格式、121份V2源码三平台目标默认类型、前端/索引及两条包链通过。初次迁移测试漏收列级CHECK的4个失败已补齐完整集合比较后重跑通过；原报告保留。未重复全量，当前源码全仓仍待复验，详见[证据](evidence/v2-event-pruning.json)。

下一断点R4.maintenance：持久后台维护/整理工作项的所有权、容量、取消、恢复、健康与共同关闭。正在使用的语义摘要继续保护；真实业务、向量、原生及三平台/72小时仍待推进。整个V2未完成，默认入口V1。


## R4.maintenance.documents — 文档维护首批已开发

源码 `f30cf2f71dba260c38c28d695514511bacb5e2ca209356a799d8570b5cf9c243`（746份冻结文件）新增独立任务/尝试账本，总计80表/v2_0017。首批仅执行明确请求的全文索引重建和既有擦除授权的续跑：64未完成/2活动/3尝试，固定源修订、索引版本、boot/token/fence，同文档FIFO。发布或擦除/派生遗忘、任务成功与审计同事务。取消后的实际工作、失败结算及关闭残留继续持有归属；备份恢复取消排队和活动任务，不自动恢复擦除。离线TTL/GC/备份不进入线上队列。

工作台v23展示真实入队和终态、显式刷新/取消、原擦除请求续跑，保留草稿并隔离迟到Agent响应；普通输出不含请求key和执行token。252个不同专项通过，包括两处真实执行崩溃和三处DDL崩溃；初次6项测试/反射问题及类型合同问题已修复，原报告保留。Ruff、611份Python格式、125份V2源码三平台目标默认类型、前端/索引及两条包链通过。未跑全量、未完成三平台实机验收，详见[证据](evidence/v2-maintenance.json)。

下一断点R4.maintenance.memory：整理候选与人工核查作为持久工作项，固定来源/预算，提案与事实应用分离，防止来源删除后恢复旧内容。完整扩展宿主、业务、向量和原生适配仍待开发；整个V2未完成，默认入口仍V1。


## R4.maintenance.memory_exact — 事实整理首批已开发

源码 `319959d032d6af3d32b82a81af5191ff6de56162ce7f4d46306d30a2a13ccd56`（759份冻结文件）新增批次、固定来源和候选三表，共83表/v2_0018。所有者选择2至32份当前fact修订；原文与来源quote预算256KiB、每源最多8个anchor，原始UTF-8完全相同且人物/会话作用域一致才形成候选。只保存身份和指纹，不复制正文；原文从仍有效的真值读取。首批纯本地、无模型调用，不创建费用账。

提案与应用分开，所有者CAS绑定revision、指纹和恢复epoch。应用只建立可撤销的重复召回抑制，不调用forget、不新增或重写事实，保留全部来源和原ID；统一谓词用于FTS、中文回退和新上下文捕获，已有快照不改。拒绝链、环及重复抑制；来源修订/锚点/人物删除使关联失效。备份恢复取消排队、失效旧核查与已应用关联，不自动重放决定。纯本地生成在一个短事务内发布，继续由共同worker和phase0关闭持有实际归属。

工作台v24提供手工选择、显式列表/详情刷新、核查/拒绝/撤销及剩余提案取消；处理Agent与批次迟到响应，失效立即清空旧详情正文。156个不同专项通过，包含6个真实进程中断点、来源传播和恢复隔离。首个head报告1项旧指标上限断言45→准确50后指定复验通过；原报告保留。Ruff、623份Python格式、130份V2源码三平台目标默认类型、前端/索引与两条包链通过；未跑全量，也未重跑整套四视口双主题矩阵。详见[证据](evidence/v2-memory-organization.json)。

下一断点R4.extensions：完整受管扩展宿主及版本化能力目录。语义/模型辅助整理、业务/向量接入和原生实机验收仍待推进；整个V2未完成，默认入口仍为V1。


## R4.extensions.python — 可信插件宿主首批已开发

源码 `c43f7c8a058849891005d144205209f02db84b3d41c699d21fb288fede2ba0f3`（779份冻结文件）重做未接通的空extensions/grants占位，并新增版本、工具、安装回执、run/action绑定，共88表/v2_0019。静态清单和ZIP_STORED源码对象是版本真值；16工具/64文件/8MiB包、64KiB清单与16KiB参数schema有明确预算，拒远程ref/正则/递归等首批未开放形态。原目录改变不能替换捕获版本，真实对象FK纳入GC/备份。

所有者信任与每次动作审批分开，全工具按写能力处理。Agent在接收回合时固定授权工具及空目录标记；新版本/授权不能加入旧run，撤权与恢复立即使旧绑定失效。动作准备、审批及最后IPC核验版本、epoch与持久参数指纹；源码捕获、授权和静态握手不执行用户代码，-P可信bootstrap只在批准invoke后加载入口。全局4常驻/64在途，实际spawn/cleanup和Windows Job句柄持续被拥有；poisoned未回收不复用，关闭残留保留数据库/实例锁。插件是可信本机代码，不构成OS安全沙箱；首批无模型/凭据/DB/宿主回调或artifact SDK。

工作台v25提供登记/更新、手选目录授权、禁用及真实回收状态，按Agent和请求代次隔离迟到响应，保留路径草稿；无直接HTTP invoke。151个不同专项通过，包括批准后真实Python执行、运行中撤权unknown/实际回收、5个执行事务退出及3个DDL退出。初次4个Windows DirEntry链接数生产故障已用Path.stat修复并保留descriptor检查；3个中文边界夹具分配问题已修，原失败报告均保留。Ruff、642份Python格式、138份V2源码三平台目标默认类型、前端/索引与两条包链通过；未跑全量/整套UI矩阵，详见[证据](evidence/v2-extensions.json)。

下一断点R4.action_review：副作用未知动作的统一持久核查与原执行证据保护，核查不自动重发。真实插件依赖/市场/MCP、回调与产物、语义记忆、原生及三平台72小时仍待推进；整个V2未完成，默认入口仍V1。

## R4.action_review — 未知动作核查已开发

源码`5921a3c655cb5814ef26d29411b642fc8c304df52a1b7d96c3b1d5b64d82ca2c`（793份冻结文件）新增核查头、不可变观察和可擦依据三表，共91表/v2_0020。原动作结果保持unknown；所有者结论applied/not_applied/still_unknown独立登记，绑定revision、来源证明、会话与恢复代次，并保留幂等回执。实际执行器未回收时不能新登记；费用、投递、调度续跑确认仍各自独立。来源删除擦除依据正文并失效结论，合法正文裁剪保留指纹，恢复要求重新核查。

277个不同专项通过，含3个真实DDL退出、真实插件崩溃后核查、并发与重复登记、来源删除/正文裁剪、恢复和3个独立浏览器场景。初始失败及明确重跑均在证据保留；未跑全量/完整UI矩阵，类型目标检查不等于macOS/Linux运行支持。详见[开发证据](evidence/v2-action-review.json)。

下一断点R5.terminal：平台PTY/进程端口、受管交互会话及能力诊断。业务、语义记忆、原生桌面及三平台72小时继续待实施；整个V2未完成，默认入口仍V1。

## R5.terminal — 所有者终端首批已开发

源码`4a4f564b12a2a784aea65324442beb86d590763e2ba75401a2af4514a725627a`（808份冻结文件）新增终端会话/不可变操作两表，共93表/v2_0021。固定可信gate先登记原生身份、复核权限后释放；主进程不fork或直接调用阻塞PTY库。Windows同步ConPTY保留当前WriteFile真实字节回执、独立Job与CONIN$/CONOUT$；POSIX单独控制会话、目录与创建身份，Mac/Linux实际运行仍待验证。

所有者手动输入与模型工具审批分开；API不接受任意argv，不向模型提供原始输入。意图与审计先持久化，同ID只查回执；accepted只指PTY接收，不证明命令成功。单写者15秒租约，4受管host/32实际操作，输出512KiB和256块，有缺口即提示。取消等待保留实际操作，关闭未回收或未结算不释放数据库/锁；恢复不重放或按旧PID杀进程。

53个不同专项通过，含3个Windows原生样例和3个独立UI场景；全量、完整UI矩阵与三平台整体验收未运行。初始依赖计数错误、gate标准句柄隔离与页面自取消detach均已修复并保留失败证据，未放宽输入/回收断言。详见[开发证据](evidence/v2-terminals.json)和[终端合同](V2_TERMINALS.md)。

下一断点R5.services：三平台常驻服务计划、模板及权限诊断，实际安装单列确认。旧终端核查、业务、原生桌面与三平台72小时仍待推进；整个V2未完成，默认入口仍V1。

## R5.services — 常驻部署首批已开发

源码`c4342ab52a8be0f13e0d248b26b872a6baa59155d1ea53c9111ed8d959ed2606`（825份冻结文件），数据库仍93表/v2_0021、界面v28。严格合同、五目标模板、私密no-replace发布、源码预览拒执行、固定隔离runner与实例/结构核验已接通。原生管理器只读查询保持实际子进程及文件任务归属；定义未证实即unknown。

运行身份文件在排空开始后记录draining，实际清理与stopped发布完成才释放锁。就绪改用每boot私密key签名随机挑战，不向未知端口发送所有者Bearer；探测完整HTTP和线程均计有界队列、5秒预算与关闭结果。

本批106个不同专项通过，固定源码集成53项；Ruff/格式/索引/重复定义与三目标默认类型检查通过。初始失败及资源不足报告保留，不作为平台通过；本批未全量、打包或真实系统服务安装。详见[部署合同](V2_SERVICES.md)和[开发证据](evidence/v2-services.json)。

下一断点R5.desktop.foundation：统一原生能力、权限、目标身份和取消协议及桌面诊断。真实服务安装须具体方案确认，原生适配、业务迁移、Mac/Linux运行和三平台72小时仍待推进；整个V2未完成，默认入口V1。

## R5.desktop.foundation — 原生桌面只读基础已开发

源码`f4a821f0fc878c936095b69a3d5d6304098eeba3be86a79268621762046ab166`（838份冻结文件），维持93表/v2_0021，界面v29。四种能力报告、精确IPC、固定单次辅助进程、Agent/view/短时snapshot与派发前复核已接入。仅显式元数据发现及身份比对，无标题/界面正文、截图、输入或模型桌面工具；HWND复用不授予动作。

宿主4实际操作/2驻留，stdout+stderr合计64KiB、5秒总预算，关闭残留保留进程/存储/锁。应用最多8诊断视图/4快照，每份50目标、60秒有效；旧视图条件清理不伤新状态，重查权限失败废弃旧快照。元数据审计与固定desktop指标/trace接通，临时调试共用操作注册表。

本批98个不同专项通过，含一个真实隔离Windows窗口及3个独立UI场景；Ruff/格式/前端/索引/重复定义和三目标默认类型检查通过，直接wheel、sdist→wheel及隔离空库/关闭通过。资源不足、测试PID及CSP夹具失败保留原报告，未放宽断言；未全量、完整UI矩阵或三平台实机验收。详见[桌面合同](V2_DESKTOP.md)和[开发证据](evidence/v2-desktop.json)。

下一断点R5.desktop.windows_observation：有界UIA语义观察和本次目标生命周期，后续动作须独立意图审批。Mac固定Swift应用、Linux可信会话/AT-SPI/X11/Portal、真实服务安装、业务与三平台72小时仍待推进。整个V2未完成，默认入口V1。

## D1.windows.capture — 窗口截图与会话产物链路

源码`192f2043346086cf007e18bb8b24120ebf5ab288521e7f88b308bab36eea8381`（846份冻结文件），94表/v2_0022、工作台v31。固定原生辅助程序采集目标窗口；有界暂存、PNG/指纹核验、会话对象引用和审计提交、预览/释放、GC保护及备份恢复已接通。创建时间改为精确整数舍入与IEEE754位编码，解决Framework转换导致的身份误判；未放宽断言。

180个不同专项通过，含10004组数值回归、隔离Windows窗口及独立UI场景；8个受影响生产文件默认Pyright无错误。全量、完整包链、完整主题/视口矩阵与三平台实机未在本批执行。原始失败报告、修复与恢复副本见[证据](evidence/v2-capture.json)。D1仍开发中，继续语义动作审批、核验与前台接管；默认入口V1。

## D1.windows.actions — 所有者语义动作链路

源码`a58c3970768a2657d55e58caa7644d7a4e7f649f419773f4369e3e5c5f4b1c82`（854份冻结文件），95表/v2_0023、工作台v32。新增不调用模型的owner运行与命令去重，复用审批、动作、租约及独立unknown核查；命令参数原子移交，重启不重放，排空拒绝新命令。原生UIA重新定位并核验目标、输入与焦点，选择和文本设置检查后置状态；Invoke及不能证明的结果保持unknown。工作台支持提交、批准/拒绝、取消与显式状态查看。

122个不同专项通过，含真实隔离Windows控件的文本设置、选择、Invoke未知及进程身份变化拒绝、迁移回滚/恢复与两种视口主题。12个受影响生产文件默认Pyright无错误。全量、完整包链、四视口双主题、Mac/Linux与72小时未验收，原始失败与修复报告保留。详见[证据](evidence/v2-actions.json)及[合同](V2_DESKTOP.md)。

下一断点D1.windows.foreground：显式前台接管绑定运行、目标、观察及本次授权，用户介入/失焦/锁屏/撤权时停止后续输入；随后继续Mac实现包交接。整个V2未完成，默认入口仍V1。

## D1前台输入收口与D2 macOS源码接入

Windows新增独立的逐次前台授权策略，复用所有者运行、审批、租约及unknown核查；原生手动焦点就绪后经宿主再次放行，一个有界输入批次执行完即退出，不抢焦点、不重放未知结果。离线故障验证使用注入输入端口；真实系统键鼠因所有者要求稍后验收，本次未执行。原始焦点限制与CRLF帧修复报告均保留。

macOS新增固定Swift/C应用与受管宿主接线：audit/libproc身份、有限AX路径和内容读取、ScreenCaptureKit独立窗口、被动输入监听下的语义动作；来源指纹、固定安装、构建签名及隔离样例/证据脚本一并加入。原生源码纳入启动身份、部署捕获和包字节检查。macOS前台输入保持不可用，Swift/C未在当前Windows主机编译，不能据此宣称Mac支持完成。

本批95表/v2_0023、工作台v33、omni-desktop-ipc-5，不新增业务表。离线专项、打包与类型检查的实际数字见[证据](evidence/v2-native.json)。D1开发、离线范围与真实验收分别登记；D2仍开发中，下一步先在Mac mini编译核查，再继续前台输入、PTY/Keychain及实机验证。整个V2、业务迁移、默认入口切换和三平台72小时仍未完成。

## D2.macos.control — 前台输入与Mac交接源码

加入独立ForegroundCore端口控制器及9个注入XCTest用例、单对CGEvent目标投递、会话/目标事件监听、AX与工作区焦点/睡眠监测、静止代次证明及共用ready/permit。工作台和HTTP继续使用相同owner运行/审批/租约/核查，无新增业务表和兼容层。按键与点击不伪装成功，文本仅在精确追加及选区可证明时成功，未知结果不重放。

Mac构建脚本先运行Core离线用例，再编译arm64/x64及通用包、签名和固定安装；新增专用Mac CI构建任务，但本机未执行Swift编译或CI。隔离桌面验收增加显式前台模式，PTY复用原生专项；Keychain使用独立受管工作器、新测试命名空间、30秒等待/35秒生存上限和私有清理回执，诊断不导出凭据值或原生异常正文。系统Keychain未在本机调用。

Swift/C/H纳入正式源码索引，编译缓存不参与索引或源版本判断。本批实际数字、限定类型范围、包链及恢复清单见[证据](evidence/v2-macos-control.json)。Mac原生与9个Swift用例仍未验证；D2不能标记整包验收完成。交接Mac mini后可继续D3 Linux会话/总线身份，整个V2、业务迁移、默认入口切换和三平台/72小时仍未完成。

## D3.linux.session 当前开发记录

已完成logind/seat/UID/锁屏核验、认证前实际Unix socket对端与连接身份复核、禁用总线服务自动启动、AT-SPI窗口与有界观察。辅助进程不打开数据库，节点正文不进诊断/审计或验收包；Linux明确使用window坐标且无动作引用。新旧数据真值及95表/v2_0023不变。

Windows注入专项、限定类型与一次包链分别存档；没有全量和Linux实机结论。无logind会话映射的用户服务应用明确拒绝，不能以UID/环境替代证明。下一开发为X11/XRes身份、截图/对象及Portal授权生命周期，详见Linux交接和本批证据。Mac编译、Windows实机及三平台72小时继续待验收。

## D3.linux.capture 当前开发记录

新增独立本机X11传输与协议边界，认证前核验logind显示、实际Unix对端、受保护Xorg二进制与会话；Xauthority只作私有认证数据。窗口由XRes实际客户端PID/UID及创建时间核验，订阅本次结构生命周期，只命名已有Composite pixmap，拒绝root及普通窗口帧缓冲读取。

截图进入现有宿主暂存/核验、对象注册、会话引用和审计；工作台按目标能力禁用无效操作。v2_0024扩充Windows/Mac/X11截图来源约束并保留触发器，修复Mac来源原先会被旧约束拒绝的问题。迁移中断/恢复及引用保护专项使用隔离数据。

本批无真实Linux/键鼠/账号调用，不运行全量。下一断点为Portal授权、会话持有/撤回及受控帧；仍不开放Linux语义或前台动作。源码与原始验证报告见本批证据。


## D3.linux.portal 授权与会话控制面开发记录

已接通显式单窗口选择、同一已核验D-Bus连接的Request/Session信号、唯一Portal/进程身份、准备与再次放行、长驻helper、共享2-helper容量及有限状态IPC。新增所有者API与requested/authorized/closed元数据审计；视图替换/重查/离开/淘汰和排空撤回，失败回收保持容量与数据库所有权，关闭可重试。

当前仍未打开PipeWire FD或读Wayland帧，不提供RemoteDesktop输入。capture_available=false，工作台未新增画面按钮，95表/v2_0024、工作台v34及桌面IPC5不变。新增独立Portal IPC1和显式Linux授权样例；原生命令未在本机执行。限定回归、类型、打包、原始失败及源码恢复见[本批证据](evidence/v2-linux-portal.json)，没有当前全量或Linux实机通过结论。

D3.linux.portal保持开发中，下一步在同一会话接受控FD、serial绑定的PipeWire消费者、撤回与帧发布，再接Linux动作及输入暂停。Windows真实输入稍后，Mac源码与9个Swift用例未编译；业务迁移、默认入口切换及三平台完整/72小时继续待推进。


## D3.linux.portal 窗口单帧与工作台接通

同一已核验Portal连接现在可接收严格关联的单个Unix FD，helper内按非复用serial使用固定GStreamer单帧消费者；没有默认远端或node ID回退。原生调用在专属有界工作线程，主进程只收回执并用文件执行器核验PNG，复用对象/会话引用及审计事务。v2_0025扩充pipewire_window来源并保留约束/触发器；95表、桌面IPC5不变，Portal IPC2、工作台v35。

工作台手动选择、查询、保存、预览及撤回已接通。实际依赖/授权决定可用状态，缺少PyGObject/GStreamer不会阻止核心启动。Linux原生脚本增加独立--capture-window，默认仍不读像素；未执行真实Portal、GStreamer、系统输入或模型付费。相关注入、数据库、浏览器、限定类型和包链记录于本批证据，不作为全量或平台结论。

当前只读窗口共享源码链路接通，D3整包仍开发中。下一开发D3.linux.control：Linux语义动作、目标/焦点、用户输入暂停、RemoteDesktop独立控制授权及审批/unknown接线。Windows实机按所有者安排，Mac源码与9个Swift用例未编译；业务迁移、默认入口切换、三平台/72小时仍待推进。

## D4 Linux系统显式启动（2026-11-07）

普通所有者控制端与固定标准库管理员包分别批准同一系统start计划；root先持久派发见证，再提交固定无阻塞命令，仅返回接受状态。本次boot与HMAC、实际进程和管理器MainPID共同决定就绪，拒绝未知或不匹配状态。协议2/包2/审计2须重新捕获并生成；关闭容量及实际回收归属贯穿CLI、IPC与root子进程。180项不同专项最新通过，原失败记录保留；11份受影响源文件三目标默认类型、直接wheel/sdist→wheel/空目录核心启动通过，未全量或真实root服务验收。详见[系统启动合同](V2_SYSTEM_SERVICE_START.md)及[证据](evidence/v2-system-start.json)。Mac系统授权与激活绑定、旧注册清理、系统交换和跨schema未完成，D4继续开发。

## D4 Mac交换激活绑定（2026-11-07）

交换计划与预约为3，纯激活绑定为1；源与未预留目标的登录包装进程不能生成随机业务boot，注册后按精确控制预留启动。预约自身有内容指纹；intent后缺失预约拒绝重建，终态且子操作结清才释放。Mac源卸载与abandon解除后续登录限制的影响均纳入SHA审阅。新旧安装必须共享激活合同和启动消费者，旧安装/计划须重建。状态、启动和只读验收共用运行及PID判定，证据脚本新增--exchange-plan并等待实际查询回收。198个不同专项最新通过、14份三目标默认类型与两条打包/隔离核心启动通过，未全量。原救援路径硬编码旧预约导致两项费用连续性回归失败，已改为从完整验证前代计划生成并重跑通过，原失败报告保留；另验证Mac绑定不可丢弃。Swift/ACL/实际加载字典、登录和服务操作待Mac mini，详见[交接](V2_MACOS_HANDOFF.md)及[证据](evidence/v2-macos-activation.json)。D4与整个V2仍未完成。

## D4 隔离前代注册核查与清理（2026-11-07）

独立清理计划绑定完整isolated历史、见证、所选部署与uninstall，日志派生到原交换旁的受管目录；同原生键在两份home预约，未知阻止换侧/换计划重发。应用直接复用ServiceOperations，不创建第二套执行状态。旧部署统一静态核验，不运行旧环境，不读取SQLite；取消等待和实际回收未完成保持锁。只清理精确静止定义，原unknown/数据/闸门/交换预约保持；当前缺失不证明历史效果或永久不存在。46项专项含既有注册/交换及费用救援通过，11份默认三目标类型与两条打包/隔离核心启动通过。原夹具错误使用了未排序home，已按实际锁顺序注入第二目录失败；CLI联合JSON类型显式收窄，均未放宽断言或忽略类型。真实服务操作及全量未执行。详见[清理合同](V2_SERVICE_CLEANUP.md)和[证据](evidence/v2-service-cleanup.json)。后续继续系统交换/macOS系统授权及跨schema，D4与V2仍未完成。

## D4 Linux 系统服务交换（2026-11-07）

完整Exchange3批准绑定root父会话和精确三项原生子操作，目录选择使用只读租借端口，阶段settle只结清实际原生命令，父close才结清会话归属；取消、迟到响应或坏帧不提前释放交换日志锁。系统激活采用既有启动预约，阻止旧源/未预约目标在机器重启或提前激活时生成随机boot。系统隔离清理另批准nested uninstall，不复用交换会话。root stdlib包20项文件、协议/包/审计3、192帧，双端源码指纹严格匹配，不支持管理员代码自身升级。144项不同专项最新通过，18份三目标默认类型与两条打包/隔离启动通过；一处清理端口构造顺序回归及四处协议参数名类型诊断已修复，原报告保留。没有全量、浏览器或真实服务操作。详见[系统交换合同](V2_SYSTEM_SERVICE_EXCHANGE.md)及[证据](evidence/v2-system-exchange.json)。macOS系统管理员、跨协议/跨schema和真实平台仍待推进，D4与V2未完成。

## D4 macOS 系统查询与默认 ACL 前置（2026-11-07）

系统只读链改为固定原生入口、明确system域和普通进程身份，用户域继续要求GUI证明；查询协议2不能混域或复用旧响应。共同文件校验以成功的no-follow statx/filesec查询证明缺省ACL，不从NULL/ENOENT推断；正向ACL仍拒绝，前后身份保持。签名前8项NativeIdentityTests已写入构建/CI，但本Windows机未编译或执行Swift/C。142项不同Python专项最新通过、7份三目标默认类型及两条打包/隔离核心通过，含实际代码依赖/旧用户激活调用方。原10项开发失败涉及通用夹具硬编码agent.plist及异常时available误报，已修复；后续Windows路径夹具与排版问题已修复，原报告保留。不全量、不提权、不操作真实服务。macOS管理员变更后端及系统交换仍未实现；下一步继续该后端，不能把本只读前置当D4/V2完成。见[设计](V2_MACOS_SYSTEM_SERVICES.md)和[证据](evidence/v2-macos-system-inspection.json)。

## D4 macOS 独立管理员单操作（2026-11-07）

独立28文件标准库包新增Darwin root策略、受管命令和公开ServiceManagement探针，不运行用户Swift应用；普通调用方仍只走显式Unix端点。注册只发布定义/enable，卸载需证明闲置及卸载后缺失，start只提交且由所有者预约boot核验。共同审计3先登记派发、未知不重发。Darwin采用exec前许可、kqueue EXIT/FORK和未收割直属进程锚点；任何fork/句柄关闭不确定保留实际归属，不使用失效NOTE_TRACK或历史PID。220个不同Python专项最新通过，19份文件三目标默认类型零错误，两条包及隔离核心通过；原夹具路径/Windows缺失常量/时间戳与初次类型失败保留并修复。没有全量、提权、真实服务、Mac ABI/Swift/XCTest或72小时结论。系统交换仍未实现。见[合同](V2_MACOS_SYSTEM_SERVICES.md)和[证据](evidence/v2-darwin-admin.json)。

## D4 macOS 系统整体交换与启动预约（2026-11-07）

Mac系统目标复用完整交换3与root协议/审计3、三子操作及同实例父锁，独立管理员初始化全部子审计目录。普通所有者目录选择及双home预约仍是唯一数据真值；先注册不bootstrap，预留boot/释放锁后才启动，原生PID/HMAC必须一致。恢复源定义已被目标替换时，仅在结清查询后尝试已批准目标，不接受任意job。隔离清理要求另批nested uninstall，自动激活和abandon影响已声明。Mac只读工具可绑定完成交换及当前boot，不声称执行管理员动作或验证系统启用覆盖。171个不同Python专项最新通过（各报告106/139/13重叠），9份Darwin默认类型及Ruff通过；未重跑完整包/三目标类型/全量。Mac原生行为及三平台真实验收未完成。见[当前证据](evidence/v2-darwin-exchange.json)；下一步跨管理员版本/跨schema维护协调，随后业务迁移。

## D4 跨schema新目录准备与旧快照恢复（2026-11-08）

release计划3增加捕获绑定的完整线性迁移路线，持久层从V25起只在新目录事务升级；共享SQLAlchemy连接配置与Actor保持同样外键/WAL/FULL语义。源schema/身份、快照、对象和授权重新核验，迁移意图先于写入；迁移事务与schema.upgraded审计共同提交，完成报告另行封存。中断保留目标闸门、原库和验证备份，不重试部分目录。匹配旧安装可从原快照恢复另一新home，失败数据有新写入时拒绝，未知费用及工作不丢失；不反向迁移。48个不同专项最新通过，17份默认类型及Ruff通过；两项初始恢复夹具失败（复用单命令服务/未先建立SQLite只读WAL状态）与8个初次类型错误保留，已修复，未降低生产断言。没有全量/重打包/实机结论。管理员包新增纯合同为29文件；现有root系统交换继续同schema与双端源码限制，跨管理员版本/完整跨schema服务交接待下一批。见[合同](V2_SCHEMA_UPGRADE.md)和[证据](evidence/v2-schema-release.json)。

## D4 分阶段管理员版本交接与跨schema服务链（2026-11-08）

新增交换计划4和分角色批准1，协议/包/审计4、29文件标准库包，启动预约仍3/激活1。源、目标固定不同包指纹，只比对执行本方源码；单父模式保留双端源码与同schema限制，旧管理员协议3不隐式兼容。六阶段分别记录管理员关闭，实际命令回收且原生锁释放后才回执；目标缺失等待不派发，取消/迟到回执继续持有实际任务及预约。原cleanup/close证明缺失，新会话拒绝补造，要求核查。共享协议4的跨管理员代码/schema链及用户跨schema真实SQLite链已接通。124个不同专项最新通过，19份默认类型/Ruff/CLI通过；未全量/重新打包/真实服务。下一开发转D5 QQ→B站→安卓，D3控制及各平台原生/长期验收仍开放。见[合同](V2_ADMIN_HANDOVER.md)和[证据](evidence/v2-service-handover.json)。








