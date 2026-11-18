# V2 打包、安装、更新与恢复（D7）

> **默认入口为 V2（Windows离线候选）。** 真实平台与其他系统仍待验收。 本文件只记录 V2 已实际验证的构建/安装/更新/恢复链与仍未验收的部分，不把“能构建 wheel”写成“三平台已交付”。

> **当前接续（2026-11-15）**：源码 `92fbf78a` 的 direct wheel、sdist→wheel、隔离导入、新实例建库及正常关闭已离线复验，123 份资源一致；报告见[静态质量证据](../../evidence/v2-static-quality.json)。下面 2026-11-11 的资源数量和 `installation` 命令限制属于对应历史记录。

当前另有已接源码的不可变部署链：`service capture/materialize` 捕获并物化固定安装，`release prepare` 在新目录恢复一致快照；单独批准的 `release prepare-online` 可正常排空已证明身份的旧 boot，`exchange apply/resume` 串联源注册移除、目录选择、目标注册及本次启动就绪核验。隔离救援保留未知回执和最新数据，跨 schema 与分阶段管理员交接也有独立合同。因此，“没有停服/启动或数据恢复源码”已不代表当前项目。命令与边界以[版本准备](V2_RELEASES.md)、[服务交换](V2_SERVICE_EXCHANGE.md)、[恢复](V2_EXCHANGE_RECOVERY.md)、[结构升级](V2_SCHEMA_UPGRADE.md)及[管理员交接](V2_ADMIN_HANDOVER.md)为准；旧 `installation` 命令仍保持下文所列范围。

这些源码与离线注入结果尚不证明干净机器 install→update→rollback→uninstall、真实系统服务、macOS/Linux 构建、容器、签名或长跑完成。本轮没有安装、切换或启停用户服务，V1保留显式回退，Windows候选默认入口为V2。

## 跨平台新装检查入口（2026-11-17 开发接续）

`scripts/check_v2_installation.py`接收固定应用wheel、固定本地依赖包、未使用的绝对输出目录及可选基础Python。控制器使用当前开发环境，实际安装器由`-I`基础解释器执行，先确认其不能导入Omni；随后创建独立维护环境和候选环境，核对解释器/包路径与隔离状态，执行生成的启动脚本、读取当前候选，再从外部维护解释器卸载。全程不注册服务、不下载依赖或访问账号/模型/设备。示例：

```powershell
uv run --no-sync python scripts/check_v2_installation.py --wheel D:\release\omnioperator-0.1.0-py3-none-any.whl --sha256 <wheel的SHA256> --dependency-bundle D:\release\dependencies --dependency-sha256 <manifest.json的SHA256> --output D:\release\fresh-install-proof
```

检查目录主动包含空格、中文、emoji、百分号、感叹号和shell字符。POSIX启动脚本使用完整引用，Windows使用脚本相对的候选路径并关闭延迟展开；两个入口均显式使用`-I -X utf8`，安装/环境子命令也显式UTF-8，避免`-I`忽略`PYTHONUTF8`后回落旧代码页。启动时另注入私有`sitecustomize`探针，任何探针执行都失败。卸载前后比较本次停止实例的数据库SHA与备份清单SHA；这不是运行中数据库的备份实现。

控制器记录有界管道的长度/SHA、退出码、实际子进程组回收及各阶段状态，不把原始输出写入报告；输出超量、超时或取消均等待实际回收，迟到spawn仍由控制器持有。失败目录与报告保留，不能覆盖重试，也不能从残留安装自动推断成功。该入口覆盖新装、启动、状态及保留数据卸载；更新/回滚/恢复沿已有独立交付合同继续核验。

`.github/workflows/v2.yml`新增Windows/macOS/Linux新装矩阵：构建应用wheel，按`uv.lock`导出hash并取得本平台依赖wheel，生成固定本地包，再由基础Python执行上述离线安装检查，保存阶段报告。CI中依赖获取是显式联网步骤，检查本身不联网。接入CI不代表三平台原生运行通过，当前专项与实际Windows尝试分别见[本批证据](../../evidence/v2-portable-installation.json)。整个V2仍在开发，统一全量与整体验收留开发冻结后。

本批实际Windows新目录已通过独立安装、双环境隔离、真实cmd启动和当前指针核对。首次Unicode导入失败已修复，第二次检查器在大环境卸载时达到30秒观察上限；修正阶段上限后的新装检查仍在卸载返回`uninstall_cleanup_pending`，原报告保持失败。随后由外部维护器按原SHA续跑完成清理，卸载前记录的数据库/备份清单SHA保持，候选目录和启动入口均移除。该续跑有单独报告，不能将它与此前阶段拼成首次新装检查全绿。受影响103个不同专项节点已通过（首轮99过/4项临时树重命名拒绝，原节点重跑4过）；检查器最后独改后23项另过，未重跑整批或全量。Windows文件访问拒绝的系统原因未定。

## Windows候选独立依赖环境（2026-11-17 开发接续）

新增本机依赖包生成和安装路径，默认离线测试不下载依赖、不访问真实模型或账号：

```powershell
uv run --no-sync python scripts/build_v2_dependencies.py --wheels D:\release\wheelhouse --output D:\release\dependencies
uv run --no-sync python -m omni.composition.cli --home D:\omni-v2-test installation environment --sha256 <应用wheel的SHA256> --dependency-bundle D:\release\dependencies --dependency-sha256 <manifest.json的SHA256>
```

生成工具只接收平面本机wheel目录，输出平台/Python/架构绑定的`omni-dependency-bundle-1`清单和固定wheel字节；不安装或导入输入wheel，不自动下载。每包最多512个wheel、单wheel最多512 MiB、总量最多2 GiB；使用前重新核对清单SHA、文件大小和SHA。依赖闭包由安装后的`pip check`验证，不能用生成成功代替安装通过。

传入依赖包时创建`isolated-wheelhouse`环境，不继承宿主或用户site-packages；pip使用隔离配置、仅本地wheel和逐项hash，不接受外部requirements指令或URL。候选wheel及安装缓存副本均重验应用SHA。整个创建流程持有实例锁，运行实例不能被替换；venv使用最终固定路径，避免重命名后控制台命令仍指向旧解释器。失败只清理本次新建环境，不发布成功收据；崩溃留下的不完整环境拒绝自动覆盖。Windows的pyvenv.cfg显式收紧DACL，原私密文件校验保持。

已有环境收据必须匹配候选SHA、模式和固定解释器；有坏收据时不能静默退回宿主。冒烟优先使用候选解释器并将收据SHA绑定到结果，切换拒绝过期冒烟证明。启动脚本直接运行时也会先选择指针指定的解释器，再导入程序。未提供依赖包的原`system-site-packages`模式仅保留为开发兼容路径，不能作为独立候选交付的证据。

源码`06f7e4c6`已加入独立安装入口：`scripts/install-v2.ps1`或可单独分发的`agent/scripts/install_v2.py`，只要求Python 3.12+、应用wheel及固定依赖包。入口先捕获并验证应用SHA才导入其中的安装代码；维护环境与实例目录分开，后续可从维护解释器卸载候选，避免删除正在执行的Windows解释器。新装拒绝覆盖既有home/manager，失败保留诊断目录而不发布成功收据。操作示例见[Windows候选交付](../platforms/windows/V2_WINDOWS_CANDIDATE.md)。

实际产品与110个锁定依赖wheel已经通过基础Python独立安装（该解释器不能导入Omni、SQLAlchemy或platformdirs，164.61秒），安装态命令、同schema开发wheel切换/重放/回滚、SQLite与对象快照恢复已有证据。卸载首次三次遇到文件访问拒绝，私有卸载收据保留精确SHA及清理权限；之后从外部维护解释器续跑成功，数据库字节和备份保持。当前源码以`uninstall_cleanup_pending`/409明确提示重试，`installation status`显示待清理状态；有新候选被选择时旧卸载不能续跑。原始失败保留，文件访问拒绝的环境原因未定，不能称首次卸载全绿。

相关集成专项100通过，清理状态调用方另26通过；修改的8个运行模块strict零错误/警告。测试和实际产品包分别绑定各自SHA，后续路径规范化、流式hash与状态提示由增量回归覆盖；最终固定候选的打包、整范围strict和全量尚待，不能把阶段证据拼成一次完整候选全绿。未改变用户服务、未做干净Windows机器/真实服务/72小时验收。见[安装证据](../../evidence/v2-windows-installation.json)及[Windows候选任务](../../tasks/v2-windows-candidate.json)。

## 1. 已实际验证（2026-11-11，Windows 主机）

命令：

```powershell
uv run --no-sync python scripts/check_package.py
```

该入口（实现位于 `agent/scripts/check_package.py`）离线构建两条链并交叉验证：

| 链 | 验证内容 |
| --- | --- |
| direct wheel | `uv build --offline --package omnioperator --wheel` |
| sdist → wheel | 先 `uv build --offline` 生成 sdist，再从 sdist 构建 wheel |
| 隔离导入 | 在临时目录解压 wheel，`python -I` 导入 `omni`，检查 `omni/web_assets` 路径 |
| V2 内核字节一致 | `domain/application/adapters/composition` 的源码哈希与 wheel 内一致（内核 SHA `9c99…`，见证据） |
| 界面资源一致 | 根 `web/` 与 wheel `omni/web_assets/`、sdist `_web/` 的 116 个资源哈希完全一致；明确排除 `node_modules` |
| 隔离 V2 实例 | 在临时 home 打开 `open_instance`：fresh schema、1 个 agent、`health()["ready"]`、无 `omni.bootstrap`/`omni.memory.*` V1 模块、关闭 complete |

最近一次结果：`{"assets": 116, "chains": ["direct wheel", "sdist to wheel", "isolated import"], "v2": ["packaged assets", "isolated fresh schema", "no V1 imports", "clean shutdown"]}`（完整 SHA 见 `docs/evidence/v2-packaging.json`）。

## 2. 安装/更新/恢复合同（离线测试已覆盖）

| 合同 | 实现位置 | 调用方测试 |
| --- | --- | --- |
| 不可变发布记录、来源/目标操作证明 | `adapters/persistence/releases.py`、`domain/releases.py` | `test_v2_releases.py` |
| 部署文件校验、路径/权限/链接约束 | `adapters/platform/deployment_files.py` | `test_v2_deployment_files.py` |
| 优雅 drain、发布状态机、快照发布 | `domain/service_*`、`application/service_*` | `test_v2_release_drain.py` |
| schema 升级/中断回滚/恢复来源快照 | `adapters/persistence/schema_upgrade.py`、迁移 v2_00NN | `test_v2_schema_release.py` |
| 数据备份/恢复（含 WAL 一致快照） | CLI `backup` / `restore`、`maintenance.py` | 相关恢复测试与 schema release 调用方 |

上述四组调用方 57 项在本次节点全过（`temp/build/v2-packaging-callers.xml`）。

## 3. 安装收据与升级预检（2026-11-11 新增，仍非完整安装器）

```powershell
python -m omni.composition.cli --home <home> installation verify
python -m omni.composition.cli --home <home> installation seal
```

- `verify` 只读：报告 home 是否存在、`installation.json` 是否存在/有效/与当前 schema 一致、`data/omni.db` 与 `-wal` 是否存在、数据库当前 schema、代码期望 schema、是否需要升级；不写文件、不改数据库。
- `seal` 显式写入一次安装收据（`installation.json`：format/created_at/schema_version/code_head），要求数据库 schema 等于代码 head，已存在且同 head 时幂等返回 `written=false`，不同 head 时 fail-closed；使用 `durable_json` 原子写并收紧权限。
- 收据校验为纯 domain 规则（`domain/installation.py`），拒绝链接、缺字段、非 UTC 时间、非法 schema。
- 运行时打开实例时校验已存在的收据：schema 与数据库不一致 → `installation_mismatch`；内容损坏或被替换为链接 → `installation_invalid`；没有收据的历史实例保持兼容。升级后必须重新 `seal`。
- `installation artifact <wheel> --sha256 <hex>` 只读校验候选 wheel：SHA256、zip CRC、成员数/解压体积上限、绝对路径/反斜杠/`..` 越界、重名、缺少 `omni/web_assets/index.html`/`ui/v2/index.html` 或 METADATA 均 fail-closed；只报告，不解压安装。
- `installation stage <wheel> --sha256 <hex>` 在 artifact 校验通过后把 wheel 原子复制到 `<home>/updates/<sha 前16>/artifact.whl`，写 `staged.json`（`extracted=false`、`installed=false`）；同 SHA 重放幂等 `written=false`，损坏收据 fail-closed；不解压、不建 venv、不切换、不迁移。
- `installation prepare --sha256 <hex>` 仅对已暂存且哈希未变的 wheel 解包到 `<home>/updates/<sha 前16>/tree`：重新校验 SHA/CRC/成员上限/越界/重名/V2 资源，成功后写 `prepared.json`；同 SHA 重放幂等 `written=false`，失败清理 `.tree.partial`；仍不建 venv、不切换、不迁移。
- `installation switch-plan --sha256 <hex>` 只读生成切换计划：要求候选已 prepare、安装收据有效且与当前数据库 schema 一致、实例未被占用；输出 backup/stop/switch/migrate/seal/start/health 七步与 `executable=false`、`requires_owner_approval=true`，不执行任何步骤。
- `installation backup --sha256 <hex>` 只执行计划中的备份步：复用同一前置校验，调用既有 `backup` 在 home **之外**创建一致快照（拒绝覆盖已存在目录），在暂存目录写 `backup.json`（含 manifest SHA256）；同 SHA 重放幂等 `written=false`；不停服、不切换、不迁移。
- `installation rollback-check --sha256 <hex>` 只做回滚演练：把 `backup.json` 指向的快照 restore 到 home 之外的**新目录**，用 `inspect_database` 校验完整性与 schema，写 `rollback.json`；同 SHA 幂等、目标已存在则拒绝；不触碰 live home。
- `installation status` 只读汇总：收据有效性、数据库/WAL/当前与期望 schema、是否需要升级、当前运行指针（active/SHA/mode/python/previous/live-rollback），以及 `updates/<sha16>` 下每个候选的 staged/prepared/environment/backup/rollback/migrate/active 与 `next_step`（待迁移时为 `migrate`）；不创建目录、不写文件。
- `installation smoke --sha256 <hex>` 在子解释器中把候选树加入 `PYTHONPATH`，导入 `omni` 并断言导入路径位于候选树、V2 资源存在；成功写 `smoke.json`（幂等），失败 fail-closed；不安装、不切换、不迁移。
- `installation switch --sha256 <hex>` 执行计划中的**切换步**：要求候选已 prepare、smoke 已 verified、backup 与 rollback 演练收据齐全、收据与当前 schema 一致且已无待迁移（否则 `switch_migration_required`）、实例未被占用；原子写 `<home>/runtime/current.json`（保留上一指针为 `previous.json`）、`runtime/launch.py` 与 `omni.cmd`/`omni` 启动包装；同 SHA 重放幂等 `written=false`；不停止/启动服务。
- `installation migrate --sha256 <hex>` 执行待应用的 Alembic head：要求同一组 prepared/smoke/backup/rollback 收据、收据与当前 schema 一致且实例未被占用；迁移后校验到达 head 并重新 seal 安装收据，写 `migrate.json`；同 schema 重放幂等 `written=false`；失败由 SQLite/Alembic 事务回滚并 fail-closed。
- `installation install <wheel> --sha256 <hex>` 串起 artifact 校验→stage→prepare→smoke→backup→rollback-check→migrate→switch，任一步失败即停止；迁移后自动重 seal，再切换运行指针；输出 `omni-install-1` 汇总；仍不建 venv、不停服/启动。
- `installation current` 只读读取运行指针：active 表示候选树仍存在、wheel SHA、schema 与上一指针；不写文件。
- `installation uninstall [--sha256 <hex>] [--purge-data]` 在实例空闲时卸载候选：带 SHA 时删除 `updates/<sha16>`（含暂存 wheel/树/收据），若它正是当前指针则同时移除 `current.json`/`previous.json`/`launch.py` 与 `omni.cmd`/`omni` 包装；不带 SHA 时只停用当前指针；`--purge-data` 显式删除 `data`/`objects`/`logs`/`updates`/`runtime`/`cache` 与安装收据、启动包装（保留 home 目录本身与 home 之外备份）；不带 purge 时保留数据，报告 `data_kept`/`backup_kept`；未知/非当前候选、purge 与 SHA 并用、实例占用均 fail-closed。
- `installation environment --sha256 <hex>` 为已暂存候选创建**离线** venv：`python -m venv --system-site-packages` 后用 `pip --no-index --no-deps` 安装暂存 wheel（按 METADATA 生成 pip 可识别的文件名），在子解释器中验证 `omni` 导入路径位于该 venv，写 `environment.json`；同 SHA 重放幂等 `written=false`，失败清理 `venv.partial`。这是离线开发/验收模式，依赖宿主 site-packages。
- `installation switch` 在候选存在有效 `environment.json` 时把运行指针的 `python` 指向该 venv、`mode=venv`，`omni.cmd`/`omni` 与 `runtime/launch.py` 按指针选择解释器（venv 模式不再插入候选树 `sys.path`）；没有环境时保持宿主解释器 + 候选树（`mode=tree`）。
- `installation rollback` 执行**代码级 live 回滚**：实例空闲时把运行指针换回 `previous.json`（上一候选），重写启动包装与 `launch.py`，写 `rollback-live.json`；要求上一候选树仍存在且其 `schema_version` 与当前数据库一致，否则 `rollback_schema_mismatch`；同一切换对的重复调用幂等 `written=false`；不恢复数据库快照。
- **仍未完成**：不下载 wheel（artifact/stage 只处理本地文件）、不停止/启动服务、不恢复数据库备份；三平台 venv 与干净机器端到端未验收，默认入口仍是 V1。

## 4. 明确未完成

- **三平台**：仅在 Windows 主机验证纯 Python wheel 构建与隔离导入；macOS/Linux 未构建、未安装、未恢复，不得写“三平台已交付”。
- **容器镜像**：已有 [`deploy/v2/Dockerfile`](../../../deploy/v2/Dockerfile)、非root私有卷/只读Compose和独立断网核验工具，见[容器合同](V2_CONTAINER.md)。2026-11-17 本机复核 Docker CLI 存在、Docker 引擎仍不可连接，仅做静态解析和隔离控制器/HTTP专项；未构建或运行镜像，也未启动 Docker Desktop，配方存在不等于容器已交付。
- **Windows候选独立安装**：已有固定依赖、独立维护解释器、新装/同schema更新/回滚/快照恢复/保留数据卸载与故障续跑的离线证据；真实系统服务与干净机器仍待验收。跨schema/服务链继续遵循release/exchange合同。`scripts/install.ps1`、`update.ps1`、`installer_lib.ps1` 仍是显式V1组件，不能代替V2证据。
- **代码签名/公证**：未做 Windows 签名、macOS notarization。
- **真实机器验收**：未在干净 Windows/macOS/Linux 机器执行“安装→升级→回滚→恢复→卸载”。
- **默认入口**：Windows离线候选的`omni`、`omni-web`和`python -m omni`指向V2；`omni-v1`、`omni-web-v1`与`start-v1.bat`保留显式旧入口。

## 4. 复现与门禁

```powershell
uv run --no-sync python scripts/check_package.py
uv run --no-sync pytest -q -n 0 agent/tests/test_v2_releases.py agent/tests/test_v2_release_drain.py agent/tests/test_v2_deployment_files.py agent/tests/test_v2_schema_release.py
```

产物在 `temp/build/packaging/{direct,sdist}/`，含 `evidence.json`（assets/sha256/chains/v2/v2_kernel_sha256）。

## 5. 下一步

1. 补齐 V2 安装/更新执行链：建 venv、停服/启动与 live 回滚，先在 Windows 实测；数据目录清理与卸载保留为显式人工步骤。
2. 生成容器镜像与三平台构建矩阵；macOS/Linux 无环境时只记录“待真实验收”。
3. 干净机器端到端演练：安装→启动→升级→回滚→备份恢复→卸载，全部留证据后才评估正式入口切换。
