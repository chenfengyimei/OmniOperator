# Windows V2 底座运行手册（演示/验收用）

> 目的：在 Windows 上用最短路径把 V2 底座跑起来给评审看，并如实说明已验证/未验证边界。
> 默认正式入口仍是 **V1**；本手册只描述 V2 开发入口（`python -m omni.composition.cli`）。

## 1. 前置

- 仓库根：`D:\xm\OmniOperator`（PowerShell）
- 已装好的虚拟环境；所有命令都用 `uv run --no-sync`（**不要** `uv sync`，会重建正在用的环境）
- 不要停止或重启用户正在运行的服务；演示请用**独立 home + 独立端口**

## 2. 冷启动（实测通过，2026-11-13）

```powershell
# 1) 建一个空实例（不调用模型）
uv run --no-sync python -m omni.composition.cli --home temp\demo-home init
# -> {"initialized": true, ..., "schema_version": "v2_0037", ...}

# 2) 只读体检（独占，起服务前跑）
uv run --no-sync python -m omni.composition.cli --home temp\demo-home doctor
# -> {"integrity": "ok", "foreign_key_errors": 0, "schema": "v2_0037", "tables": 148, "agents": 1, ...}

# 3) 起服务（前台；演示用独立端口，避免和现有实例冲突）
uv run --no-sync python -m omni.composition.cli --home temp\demo-home serve --port 8799

# 4) 另开一个窗口取所有者令牌（首次 serve 时创建）
uv run --no-sync python -m omni.composition.cli --home temp\demo-home auth
```

浏览器打开 `http://127.0.0.1:8799/`，把令牌粘进「所有者令牌」输入框，点「连接实例」。

## 3. 接口自检（实测）

```powershell
$t = (uv run --no-sync python -m omni.composition.cli --home temp\demo-home auth).Trim()
$h = @{ Authorization = "Bearer $t" }
Invoke-WebRequest http://127.0.0.1:8799/api/v1/version      -Headers $h -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8799/api/v1/capabilities -Headers $h -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8799/api/v1/agents       -Headers $h -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8799/                    -UseBasicParsing   # 工作台 UI
```

2026-11-13 实测：`version` 200、`capabilities` 200、`agents` 200、`/` 200（约 71 KB HTML）。

## 4. 停止

- 前台 `serve`：该窗口 `Ctrl+C`
- 后台进程：按 PID 结束（`Get-Process -Id <pid>` → `Stop-Process -Id <pid>`），**不要**用 `Get-Process python | Stop-Process` 一把杀

## 5. 演示时可讲 / 不可讲

可以讲（有证据）：

- 空实例冷启动 → schema `v2_0037`、148 张表、完整性检查 ok；
- `GET /api/v1/capabilities` 如实投影能力（可用/需配置/未验收），不是营销话术；
- 单一 SQLite、单写者 actor、审批与未知结果 fail-closed；
- 全量测试在本机 Windows 跑过：4654 项（详见 `docs/evidence/v2-typecheck.json`）。

不要讲：

- 「V2 已完成/已迁移」——默认入口仍是 V1；
- 真实模型、真实市场/插件/MCP、真实设备输入已验收——这些只做过注入测试；
- 三平台与容器已验收——macOS/Linux 未实机，容器镜像未构建。

## 6. 常见问题

| 现象 | 原因/处理 |
| --- | --- |
| `auth` 报 `owner_token_missing` | 还没 `serve` 过；先启动一次服务，令牌由运行时创建 |
| `serve` 端口占用 | 换 `--port`，不要杀掉用户正在用的实例 |
| `doctor` 报 `diagnostic_schema_mismatch` | home 是旧 schema；重新 `init` 一个空 home，不要手改库 |
| 页面能开但接口 401 | 令牌没粘或粘错；用 `auth` 重新取 |
