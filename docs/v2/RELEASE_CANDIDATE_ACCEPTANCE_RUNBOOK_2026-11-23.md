# V2 发布候选验收执行手册（H，2026-11-23）

> 用途：R1/R2/R3 修复进入主线后，H 按同一候选完成服务链、官方安装链、最终全量与 72 小时。当前主线 `5c8550ca` 仍有 R3（start 控制无法收口），本手册是修复后的执行清单，不代表当前已通过。

## 0. 冻结核对（先做）

```powershell
git fetch origin
git rev-parse HEAD
git rev-parse origin/v2          # 期望相同
git status --porcelain=v1       # 仅允许已知 temp 未跟踪；不夹带他会话改动
```

记录：

- 冻结 SHA：`________________________________`
- wheel SHA256：`________________________________`（由该 SHA 干净 worktree 构建）
- dependency manifest SHA256：`ddc242906492a815e1893505f0453f922f2c218dda441ad0088effb9f5e1225f`
- schema：期望 `v2_0052`；`omni doctor` integrity=ok、163 表

构建：

```powershell
git worktree add --detach C:\v2-acceptance\final-source <SHA>
cd C:\v2-acceptance\final-source
uv build --offline --package omnioperator --wheel --out-dir C:\v2-acceptance\final-release
Get-FileHash C:\v2-acceptance\final-release\omnioperator-0.1.0-py3-none-any.whl -Algorithm SHA256
```

## 1. Windows 服务完整链（R3 修复后先跑）

新建独立 home，不复用旧演练目录：

```powershell
install_v2.py --wheel <wheel> --sha256 <wheel-sha> `
  --dependency-bundle <deps> --dependency-sha256 ddc242… `
  --manager <home>\..\manager --home <home>
```

依次：

1. `service capture`
2. `service plan request capture plan.json`
3. `service materialize plan.json bundle`
4. `service verify bundle`（exit 0）
5. `service operation-plan bundle install op.json` + `apply-operation --approve <sha>`：期望 `succeeded`、`definition_matches=true`
6. `service control-plan bundle start cp.json` + `apply-control --approve <sha>`：期望 `succeeded`、`observed.ready=true`、boot 与计划一致
7. `service status bundle`：**期望 manager `state=running`、`definition_matches=true`、`pid` 非空且等于 runtime pid、overall `ready=true`**
8. `service control-plan bundle stop cp2.json` + `apply-control`：期望 `closed=true`、锁释放
9. `service operation-plan bundle uninstall op2.json` + `apply-operation`：期望任务删除

失败口径：

- `pid=null` 或 `service_action_unverified` → 必须 `ready=false`；若 `ready=true`，保留 status/helper/psutil 证据，判 R2 回归。
- `start` unknown / `reconcile` unresolved → 保留 control receipt、`run/service-controls/active.json`、helper JSON、任务状态；判 R3 回归。
- 任何失败都保留原始目录，不删除、不覆盖重试。

证据：新建 `docs/evidence/v2-windows-service-<sha>-<date>.json`，包含 wheel/SHA、环境、每步 exit/耗时、status JSON、任务查询、清理结果。

## 2. 官方安装链（同候选）

用 `C:\v2-acceptance\release-…\accept_chain.ps1` 或 H 的 chain 脚本：

- 上一版 wheel 新装 → 当前 wheel stage/prepare/environment/install → rollback → doctor → 卸载上一版 → switch → 卸载当前版。
- 首次卸载 `uninstall_cleanup_pending` 必须保留，等待 30s 由外部维护器重试；最终 `cleanup_pending=false`、`candidates=[]`、`omni.cmd` 移除、数据/备份保留。
- 记录两处 attempt 次数/耗时/错误原文。

## 3. 官方 wheel 安装检查

`check_v2_installation.py --wheel <wheel> --sha256 <sha> --dependency-bundle … --dependency-sha256 … --output <新目录> --base-python <Python312>`：

- 六阶段 verified 后，首次卸载仍可能确定性失败（150B stderr，SHA `98b1d782…`）。
- 等待 40s 由 manager venv 续跑；核对 DB/备份 manifest SHA 前后一致。
- 报告 `verified=false` 时不得写成通过；保留首次失败与续跑两份记录。

## 4. 最终全量（冻结后只跑一次）

```powershell
uv run --no-sync pytest -q --junitxml=temp/build/final-<sha>.xml
```

- 默认最多 4 worker；不与其它全量并行。
- 保存 JUnit、总数/通过/失败/跳过、耗时。
- 任何失败必须分诊并保留原始 nodeid；不能用旧 `b395cd97` 结果代替。

## 5. 72 小时长跑

前置：最终冻结候选；所有者批准 8 字段 limits；机器 72h 不休眠/不换候选。

```powershell
python scripts/v2_soak.py run `
  --home <实例> --url http://127.0.0.1:<端口> `
  --output <新目录> --limits <批准文件>
python scripts/v2_soak.py report --output <新目录>
```

- 期望 `observational_soak_passed=true`；中断用 `--resume` 沿原指纹续采。
- 报告仍是 `whole_v2_complete=false`；不代表账号/设备/模型质量。

建议 limits（待批准）：RSS 512MiB、后代 1GiB、线程 64、句柄 2048、子进程 8、cleanup 0、new_failed_runs 0、new_unknown_model_calls 0。

## 6. 干净机交付包

用最终 wheel 重建：

- `omnioperator-<sha>-py3-none-any.whl`
- 上一版 wheel
- `dependencies/`（110 wheels）
- `install_v2.py`、`accept_chain.ps1`、`SHA256SUMS.txt`、README
- 在干净 Windows VM 上先校验 SHA，再执行；记录 OS/Python/机器标识（脱敏）。

## 7. C4.2/C4.3/C4.4

- C4.2：A 与应用所有者决定签名/公证或源码交付；交付物附 SHA256 与构建命令。
- C4.3：`release prepare/select` 与 `exchange apply` 真实演练；保留旧 home、快照与回执。
- C4.4：HANDOFF/DEVELOPMENT_STATUS/台账/`docs/index` 同步；更新本手册与验收矩阵；生成交接提示词。

## 8. 当前阻塞（2026-11-23 22:40）

- **R4（主阻塞）**：b29409d0 最终全量 8655 项 / 8623 通过 / **22 失败** / 10 跳过；20/22 单跑复现，主因默认 `context_tokens=32768` 触发 `token_budget_exceeded`（C/D/E/G 后提示词/工具 schema 增长）；另有 shell 进程树回收、UIA act、media zoom 待分诊。全量绿前不得冻结/72h。
- C2.7 服务生命周期、官方安装链/安装检查已在 b29409d0 通过（检查仍需卸载重试）。
- 干净 Windows VM、QQ/B 站账号、安卓真机、兼容视觉模型仍缺。
- 72h 尚未启动；H 的干净机包 `release-b29409d0` 已就绪，但需先修复全量。

