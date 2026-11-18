# Windows V2 版本验收记录（2026-11-13）

> 结论先行：**Windows 上的 V2 底座可冷启动、可自检、可服务、可跑全量测试、可离线打包**；
> **但 V2 尚未完成迁移，默认正式入口仍是 V1**，真实模型/真实市场与插件/MCP/真实设备输入/三平台与容器未验收。
> 本文件只记录 Windows 底座验收，不构成「V2 完成」结论。

## 1. 验收范围

| 项 | 是否覆盖 | 证据 |
| --- | --- | --- |
| 冷启动（空 home → schema → 健康体检） | ✅ | 本文 §2；`agent/tests/test_v2_windows_cold_start.py` |
| 服务启动与 API/UI 可达 | ✅ | 本文 §3 |
| 全量测试（Windows，`-n 2`） | ✅ 4656 项：4655 passed / 1 failed / 0 errors（唯一失败为 portal 采集时序抖动，隔离通过） | `docs/evidence/v2-typecheck.json` 的 `full_run` |
| 离线打包（wheel/sdist + 116 资产 + 隔离导入） | ✅ | `docs/evidence/v2-packaging.json`（5/5 gate、57 调用方） |
| 安装器链（verify/seal/artifact/stage/prepare/switch/install/migrate/uninstall/environment/rollback/purge） | ✅ 离线节点 | 各节点证据 + `docs/evidence/v2-typecheck.json` |
| 设计系统守卫（强调色/圆角/断点/契约） | ✅ | `test_ui_glass_tokens.py`、`test_ui_contract.py` 31 项 |
| 真实模型供应商 | ❌ | 全部 fake/`OMNI_TEST_OFFLINE` |
| 真实市场/第三方插件/真实 MCP 服务 | ❌ | 仅注入测试 |
| 真实前台键鼠/桌面捕获 | ❌ | 用户明确延后 |
| 干净机器完整 install→update→rollback→uninstall 实执行 | ❌ | 只有离线计划/演练 |
| 容器镜像、macOS/Linux 构建 | ❌ | 未做 |
| 24h+ 长跑 | ❌ | 未做 |
| 正式入口切换 | ❌ | 默认入口 V1，R6 parity 门槛未达成 |

## 2. 冷启动（实测）

```powershell
uv run --no-sync python -m omni.composition.cli --home temp\demo-home init
# {"initialized": true, "schema_version": "v2_0037", "api_version": 1, ...}
uv run --no-sync python -m omni.composition.cli --home temp\demo-home doctor
# {"integrity": "ok", "foreign_key_errors": 0, "schema": "v2_0037", "tables": 148, "agents": 1, ...}
```

## 3. 服务与接口（实测）

```powershell
uv run --no-sync python -m omni.composition.cli --home temp\demo-home serve --port 8799
uv run --no-sync python -m omni.composition.cli --home temp\demo-home auth   # 首次 serve 后才有令牌
```

| 请求 | 结果 |
| --- | --- |
| `GET /api/v1/version` | 200 |
| `GET /api/v1/capabilities` | 200（如实投影可用/需配置/未验收） |
| `GET /api/v1/agents` | 200 |
| `GET /`（工作台 UI） | 200，约 71 KB |

## 4. 本轮为验收补的修正

1. `auth` 在未 `serve` 时给出 `owner_token_missing` 与下一步（原为 `filesystem_error`）。
2. 新增冷启动守卫测试（init → doctor → auth 指引）。
3. V2 工作台把 18 个裸色值收敛到 `tokens.css` 的 `--v2-*` 种子、14px 圆角改用 `--r-lg`、620/1180 断点收敛到设计系统允许的 520/900；三处设计系统守卫由失败转全过。
4. 新增 `docs/v2/platforms/windows/WINDOWS_V2_RUNBOOK.md`（演示命令、停止方式、可讲/不可讲、常见问题）。

## 4.1 本轮验收修复汇总

24 项 UI 回归（会话行 role、`nav.inert` 残留、会话面板隐藏、图标字体契约）→ 修；
workbench 测试把二进制字体当文本读导致 goto/close 挂起 → 修；
debug 会话共享 `omni.v2` logger 导致并行串用 → 按测试隔离；
设计系统守卫（18 个裸色值、14px 裸圆角、620/1180 断点）→ 收敛到 `tokens.css` 与 520/900；
MCP UI 硬编码资源版本 `v66` → 改为从 `index.html` 派生。

## 5. 演示口径

可以讲：空实例冷启动与健康体检、148 表 / `v2_0037`、能力报告如实、单写者 SQLite + 审批 + 未知结果 fail-closed、全量测试在本机 Windows 跑过、离线 wheel 打包通过。

**不要讲**：V2 已完成或已迁移；真实模型/市场/插件/MCP/设备已验收；三平台与容器已验收。

## 6. 若要继续推进（优先级）

1. 干净 Windows 机器实跑完整 install→update→rollback→uninstall（含 DB 备份恢复）；
2. 真实模型 + 真实 MCP 各一次端到端回合（含失败/限流/中断）；
3. 真实前台键鼠与桌面捕获人工确认；
4. 24h 长跑（并发回合、队列、fd/内存）；
5. 把 adapters 剩余 62 文件与 composition 剩余 12 模块纳入 strict，再做入口切换门槛评估。
