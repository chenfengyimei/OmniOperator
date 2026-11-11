# V2 打包、安装、更新与恢复（D7）

> **默认正式入口仍是 V1。** 本文件只记录 V2 已实际验证的构建/安装/更新/恢复链与仍未验收的部分，不把“能构建 wheel”写成“三平台已交付”。

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
- **仍未完成**：不下载/校验 wheel、不创建 venv、不切换运行目录、不执行迁移、不回滚、不卸载；macOS/Linux 与干净机器端到端未验收，默认入口仍是 V1。

## 4. 明确未完成

- **三平台**：仅在 Windows 主机验证纯 Python wheel 构建与隔离导入；macOS/Linux 未构建、未安装、未恢复，不得写“三平台已交付”。
- **容器镜像**：无 Dockerfile/镜像构建链与验收。
- **V2 安装器/更新器**：`scripts/install.ps1`、`update.ps1`、`installer_lib.ps1` 属于 V1 运行目录与 V1 组件安装，不是 V2 安装/更新链；V2 尚无独立安装器、升级器或卸载器。
- **代码签名/公证**：未做 Windows 签名、macOS notarization。
- **真实机器验收**：未在干净 Windows/macOS/Linux 机器执行“安装→升级→回滚→恢复→卸载”。
- **正式入口切换**：未满足 D7 门槛，`omni` 仍指向 V1。

## 4. 复现与门禁

```powershell
uv run --no-sync python scripts/check_package.py
uv run --no-sync pytest -q -n 0 agent/tests/test_v2_releases.py agent/tests/test_v2_release_drain.py agent/tests/test_v2_deployment_files.py agent/tests/test_v2_schema_release.py
```

产物在 `temp/build/packaging/{direct,sdist}/`，含 `evidence.json`（assets/sha256/chains/v2/v2_kernel_sha256）。

## 5. 下一步

1. 增加 V2 独立安装/更新/卸载链（安装目录、版本清单、原子切换、失败回滚），先在 Windows 实测。
2. 生成容器镜像与三平台构建矩阵；macOS/Linux 无环境时只记录“待真实验收”。
3. 干净机器端到端演练：安装→启动→升级→回滚→备份恢复→卸载，全部留证据后才评估正式入口切换。
