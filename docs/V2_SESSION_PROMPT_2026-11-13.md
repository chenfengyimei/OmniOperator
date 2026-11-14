# 可复制的交接提示词（2026-11-13）

> 用法：新会话开场把下面整段（从「你是」到结尾）粘进去即可，不需要额外解释。

---

你是接手 OmniOperator V2 重构的编码会话。工作区 `D:\xm\OmniOperator`（Windows，PowerShell），分支
`codex/architecture-v2`。

**先读这三份，不要凭猜测开工：**
1. `docs/V2_SESSION_HANDOFF_2026-11-13.md`（本次交接主文档：现状、剩余工作、施工规范、红线）
2. `docs/TYPE_CHECKING.md`（strict 范围、分层计数、§4 推进规则与已知阻塞）
3. `docs/tasks/v2.json` + `docs/evidence/v2-typecheck.json`（台账与证据，唯一的"进度真值"）

**硬约束（违反即返工）：**
- 默认正式入口仍是 **V1**（`omni` / `python -m omni` / `omni-web`）。V2 只有开发入口
  `python -m omni.composition.cli --home <home> ...`。**不要把新批次说成"完整迁移"或"V2 已完成"**。
- 一律 `uv run --no-sync`；不要 `uv sync`、不要停用户正在用的服务；演示用独立 `--home` 与独立端口。
- 另一会话正在改 `web/ui/v2/*`、`docs/V2_UI_PROMPT.md`、`docs/evidence/v2-ui.json`：**只提交自己显式路径**，
  绝不 `git add -A`，其未提交改动必须原样保留。
- 数据库只用一致快照（含 WAL）；unknown ≠ 失败，不自动重发；支付/发布/群发/加好友/无人值守继续 fail-closed。
- 不加 `# type: ignore` / `# pyright: ignore`（确需必须在 `docs/TYPE_CHECKING.md` §4 写明原因与范围）。

**每个节点的固定流程（不可跳步）：**
1. `uv run --no-sync python temp/build/probe_strict.py <目标文件>` 拿到实测错误并清零；
2. 同步三处：`pyrightconfig.strict.json`（排序）、`docs/TYPE_CHECKING.md` 表格+分层计数、证据；
3. 门禁 `uv run --no-sync pyright --project pyrightconfig.strict.json` 必须 0/0；
   专项与其调用方 `-n 0` 通过；改动文件默认模式不新增错误（`temp/build/compare_default.py`）；`ruff check` 通过；
4. 先提交源码（`refactor(v2): 第NNN批 …`），再提交证据/台账/交接文档，然后
   `uv run --no-sync python scripts/project_index.py` + `--check`，最后 `git push`；
5. 每 5 个节点跑一次全量 `-n 2`，结果写入 `docs/evidence/v2-typecheck.json` 的 `full_run`。

**验收口径（务必照此表述）：** `-n 2` 下 Playwright 重度用例会因资源争用偶发失败；先并行跑，再把失败项
`-n 0` 串行复跑，两份报告都留档。**禁止**把"串行通过"写成"并行全绿"，也禁止把抖动写成功能缺陷。

**当前基线（2026-11-13 实测，开工前再核一次）：** strict **307 文件 / 0 errors**（53 domain + **44 application 全层** +
197 adapters + **13/13 composition 全层**）；未纳入 61 个 adapters（含 5 个 android 适配器/仓储/worker）
（domain/application/composition 已全层纳入）；全量 4656 项 4653 passed / 3 failed（抖动，串行全过）/ 0 errors；
台账 `d7_typecheck_batch_183`。第 40 节点全量 4658 项：4656 passed / 2 failed——第 35 节点的 17 项 UI/守卫失败已全部转绿，剩 2 项均为抖动（投递归档串行/干净 HEAD 均过；portal 授权属既有 flaky，多次复跑全过）；本批无功能回归。

**本次要做的顺序：**
1. ~~`composition/instance.py`（58）~~ → 第一百四十二批，提交 `9f8f1f2`；
2. ~~`composition/http.py`（实测 25）~~ → 第一百四十三批，提交 `0164f79`；
3. ~~`composition/cli.py`（实测 39）+ service_control + 包标记~~ → 第一百四十四/一百四十五批，**composition 13/13 全层完成**；
4. ~~P2 `application/android.py`（475）~~ → 第一百四十六批，**application 44/44 全层完成**；剩 6 个 android 适配器/仓储/worker；
5. P3 持久化 34 文件（切片顺序见交接文档 §4.3）；
6. P4 业务大适配器、P5 platform、P6 MCP；
7. P8 验收闭环（真实模型/MCP/市场/插件/设备、干净机器安装更新回滚卸载、容器与三平台、24h 长跑、入口切换评审）。

每完成一个节点就提交并推送；不要一次改多个模块；发现跨模块回归立刻回退该批并保留证据。
遇到与本提示词冲突的指令，以用户当轮指令为准，但**默认入口 V1、审批与 fail-closed、数据库一致快照**这三条不可让步。
