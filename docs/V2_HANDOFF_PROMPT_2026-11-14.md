你接手 OmniOperator V2 的持续重构开发。请实际读代码并接着实现，不要只回复计划，也不要重做已完成的部分。

## 工作区与入口

- 工作区 `D:\xm\OmniOperator`，Windows / PowerShell（用 `;` 不用 `&&`）。分支 `codex/architecture-v2`，上游 `origin`（gitee）。
- **默认正式入口仍是 V1**：`omni` / `python -m omni` / `omni-web` 都是 V1。V2 只有开发入口
  `uv run --no-sync python -m omni.composition.cli --home <全新绝对目录> serve`（默认 127.0.0.1:8766）。
  任何「V2 已完成 / 已迁移 / 全仓严格类型」的说法都不成立，未经评审不得切换或删除 V1。
- 一律 `uv run --no-sync`；**不要 `uv sync`**，不要为跑测试停止用户服务；演示用独立 home + 独立端口。

## 按顺序读

1. `AGENTS.md`、`docs/HANDOFF.md`、`docs/ARCHITECTURE.md`、`CONTRIBUTING.md`
2. **`docs/V2_HANDOFF_2026-11-14.md`** —— strict 任务当前权威完整交接：已核验事实、24 模块实测路线图、
   每节点施工九步、13 类类型化实战坑、验收四禁与干净 HEAD 对照法、红线。**本文的数字与规范以它为准。**
3. `docs/V2_AI_HANDOFF.md`（架构合同、目录地图、扩展/MCP 先例锚点）、`docs/tasks/v2.json`、`docs/DEVELOPMENT_STATUS.md`
4. `docs/TYPE_CHECKING.md`、`docs/TESTING.md`；改 UI 才读 `docs/V2_UI_PROMPT.md`。
   `docs/MCP.md` 是 **V1 先例，只读参考，禁止 import**。

## 已核验的断点（2026-11-14 实测，不要重复摸底）

- strict 门禁 **308 文件 / 0 errors / 0 warnings**：domain **53/53**、application **44/44**、
  adapters **198/222**、composition **13/13**。三层全层完成，**P2 Android 7 模块、P3 持久化 34 模块全部完成**。
- 源码提交 `aa294ef`（第 184 批 P3 收官），源码指纹 `d36a1847…`（1206 文件），台账 `status=d7_typecheck_batch_184`。
- 结构 **136 表 / 迁移头 `v2_0037`**。
- **第 45 节点全量已收割验收**（`-n 2`，2819.15s）：4658 项 **4652 passed / 6 failed / 0 errors**。
  串行复跑 5 过 1 败 → 仍败项隔离复跑过 → 干净 HEAD 代表对照 **4/4 过**。
  6 败全部归**并行会话未提交**的「模型接入/连接测试」改版，本批（181～184，只改 persistence）**无功能回归**。
  其中 HTTP 路由契约与导航计数两项串行转绿是**他会中途补齐代码**（非抖动），已如实记录。
  四份报告留档：`temp/build/v2-full-184{,-serial,-ui-isolation,-clean-head}.xml`。
  **并行口径仍记 6 failed，不得写成全绿。**
- 唯一开放项 `test.portal_ui_flaky_on_clean_head`（既有 flaky，第 45 节点未复现，保留待用例负责人加固）。
- 证据台账：`docs/evidence/v2-typecheck.json`（`full_run` / `full_run_history` / `targeted_reports` / `open_findings` / `validation.scope` 逐批范围）。

## 下一步：剩 24 个 adapters，实测合计 2763 错

动手前仍用 `temp/build/probe_strict.py <file>` 复测（交接里的旧错误数一律不可信）。每节点 1 个模块，≥150 错的拆 2–3 节点。

- **P4 业务大适配器（10 模块 / 1396 错）**：`bilibili.py` 350(拆3)、`workers/bilibili.py` 300(拆2)、`http.py` 177(拆2)、
  `workers/media.py` 99、`onebot.py` 90、`diagnostic_bundle.py` 90、`local_vectors.py` 79、`model_stream.py` 76、
  `extensions/packages.py` 73、`workers/local_vectors.py` 62。**建议从 62 起，先小后大。**
- **P5 platform（11 模块 / 1077 错）**：`platform/portal.py` 202(拆2)、`linux_semantic.py` 159(拆2)、
  `service_registration.py` 116、`linux_atspi.py` 110、`runtime_identity.py` 92、`linux_x11.py` 86、
  `service_operation_files.py` 84、`workers/portal.py` 71、`service_templates.py` 64、`deployment_files.py` 47、
  `workers/linux_desktop.py` 46。**建议从 46 起。** Linux/portal 无实机，只做类型与离线守卫，真实 ABI 记「待真实验收」。
- **P6 MCP（3 模块 / 290 错）**：`mcp/stdio.py` 67 → `mcp/http.py` 87 → `mcp/oauth.py` 136。
  真实 I/O 边界，不得改动有界/可取消/崩溃即销毁、in-flight 归 unknown 不重放、OAuth 仅用户显式发起等语义。
- **下一全量节点：第 50 节点 = 第 189 批。**

## 每个节点的施工九步（照做，不可跳）

1. `uv run --no-sync python temp/build/probe_strict.py <目标.py>` 实测（可一次多文件）。
2. 只改该模块，逐项清零至 `errorCount: 0`；**禁止 `# type: ignore` / `# pyright: ignore`**
   （唯一既有例外：`event_loop.py` 的 proactor import，已记入 `TYPE_CHECKING.md` §4）。
   留意 `reportUnnecessaryCast` / `reportUnnecessaryIsInstance` / `reportUnusedImport`。
3. 同步三处：`pyrightconfig.strict.json` 的 include（层内字母序）、`docs/TYPE_CHECKING.md` 表格行 + §3 分层计数、证据。
   守卫 `test_v2_typecheck_scope.py` **双向**校验配置与文档必须完全一致。
4. `uv run --no-sync pyright --project pyrightconfig.strict.json` → 期望 `0 errors, 0 warnings`。
5. `ruff format <file>` → `ruff check --fix` → `ruff check` → `pyright <file>`（默认模式不新增错误）。
6. `uv run --no-sync pytest -q -n 0 agent/tests/test_v2_typecheck_scope.py agent/tests/test_arch.py <该模块全部相关套件>`
   `--junitxml=temp/build/v2-typecheck-NNN-callers.xml`。**先 Glob 确认真实测试文件名**，别猜。
7. 源码提交（**路径限定**）`git commit -m "refactor(v2): 第NNN批 …" -- <自己的显式路径>`；
   提交后算指纹 `temp/build/source_version.py` + 报告 SHA256。
8. 文档/证据提交（**路径限定**）：台账三状态 + 交接文档 + 证据 → `scripts/project_index.py` 与 `--check` →
   `scripts/task_status.py` → `git push origin codex/architecture-v2`。
9. 每 5 个节点跑一次全量 `-n 2`，按下面口径收割并写入证据 `full_run`（旧的移入 `full_run_history`）。

## 必须遵守的红线

- **有其他会话并行改 `web/ui/v2/*`、`adapters/http.py`、`composition/http.py`、`composition/instance.py`、
  `docs/V2_UI_PROMPT.md`、`docs/V2_BUSINESS_INVENTORY.md` 等，且有未跟踪的 `PPT/`、`docs/competition/`。
  只提交自己显式路径，绝不 `git add -A`；提交前 `git diff --cached --name-only` 核对暂存集；其未提交改动必须完整保留。**
  历史上有一次不限定路径的提交误带入 12 个他会文件。
- **索引陈旧是已知状态**：`project_index.py --check` 报 `MODULES.md/API.md/project.json` 陈旧，
  根因是他会**未提交**的 `configuration_probe` 路由与新增 `web/ui/v2/models.js`。
  **不要据此代刷并提交索引**（会把描述他人在途代码的派生产物入库），由该会话提交时一并刷新。
- 只改其他模块的**签名**，绝不改其函数体（会把它的潜在错误拖进本批范围）；误改用 `git checkout HEAD -- <file>` 还原。
- 四层单向：适配器 → 应用 → 领域与端口；领域无 I/O，仓储不建表不自连（`test_arch.py` AST 守卫）。
  V2 单实例、单所有者、多 Agent、单一 `omni.db`；不迁 V1 数据、不双写、不加兼容层。
- **unknown ≠ 失败**，不自动重放写操作；审批 fail-closed；数据库只用含 WAL 的一致快照接口，禁止裸复制 `omni.db`。
- 支付/发布/群发/加好友/无人值守继续走审批。不跳过闸门、不缩短被测超时、不放宽断言换提速；默认套件始终离线。
- 不同时启动多个全量 pytest；小范围验证用 `-n 0` 单文件。

## 类型化最容易踩的坑（完整 13 类见交接文档 §7）

- **协议参数逆变**：实现必须与端口**完全同宽**，更窄更宽都破门禁（dict 本身不变）。动手前先读端口签名。
  已知宽口径：bilibili `forms=dict[str,object]`/`mentions=list[dict[str,object]]`；maintenance `claim=Claim|Record`；
  schedule `save value=dict[str,object]`、materialize/enqueue `Mapping[str,object]`；mcp `tools=Sequence[Record]`；
  image `model_image_policy_add value=Record`。
- `Select` 定长列元组不能标注成变长元组 → return 处 `cast(Select[tuple[object,...]], stmt)`。
- `.alias()` 返回 `NamedFromClause`（泛型有 bound，**不要填 `[object]`**）；计算列需 `cast(Column[int], ...)`。
- 模块已 `from sqlalchemy import cast` 时 typing 用别名（先例 `action_review_repository` 用 `tcast`）。
- HTTP 边界 isinstance 静态报冗余时用 `isinstance(cast(object, x), str)` **保留运行时防御**，不要删检查。
- 领域函数比 Record 宽（如 `validate_retention_plan(dict[str,object])`）→ 调用处 cast；返回 `list[Mapping]` 的逐行收窄。
- 拆分 isinstance 复合条件必须沿用**原错误码与原消息**。
- 冻结迁移/纯 SQL：字面量按**多重集**比对（AST 顺序会变）+ 常量相等；纯 SQL 另用 sqlite 方言编译逐字节比 SHA256。
- 未纳入的父类用局部 `x: Any = self` 桥接，**父类纳入后回头清理桥接**。
- 批量改文档用一个 Python 脚本做多处 `str.replace` + `json.load` 校验；替换前 `assert t.count(a)==1`。

## 验收口径（四禁）

先并行跑 → 从 XML 提取失败 nodeid 串行复跑 → 仍败的 UI 项隔离复跑 → 需要定归属时做干净 HEAD 对照
（每个症状家族取 1 个代表；**单次对照不能作结论**）。全部报告留档。
收割脚本已就绪并校验：`temp/build/_harvest_full.py <report.xml> <argfile.txt>`。
干净 HEAD 对照：`git worktree add temp/build/wtNNN HEAD --detach`，cwd 切副本，
`$env:PYTHONPATH="<repo>\temp\build\wtNNN\agent\src"`，
**必须用主解释器绝对路径** `& "<repo>\.venv\Scripts\python.exe" -m pytest …`（副本没有 .venv，直接 `uv run` 会失败），
用完 `git worktree remove --force`。

**禁止**：① 把串行/隔离通过写成并行全绿；② 把抖动写成功能缺陷；
③ 把他方未提交工作区导致的确定性失败或转绿写成抖动；④ 把其他会话引入的问题写成本批回归。
他方改动可能在验收期间变化，按并行时点快照记录并注明时点差异。

## 真实验收现状（不要自行销账）

真实模型端到端、真实 MCP/市场/第三方插件、Windows 真实前台键鼠（**用户已明确延后，不要自行弹窗**）、
干净机器 install→update→rollback→uninstall、容器镜像与 macOS/Linux 构建、24h 长跑、入口切换评审
—— 全部**未做**，记「待真实验收」。**不要因为后续需要真实验收就停下当前可完成的工作。**

## 首次开工的三条命令

```powershell
git status -sb                                                  # 与 origin 对账；脏文件先确认不是自己的
uv run --no-sync pyright --project pyrightconfig.strict.json     # 期望 0/0（308 文件）
uv run --no-sync python temp/build/probe_strict.py agent/src/omni/adapters/workers/local_vectors.py   # P4 起步，实测 62
```

先用一段简短说明报告你核对出的当前断点与工作区状态，然后开始写代码，持续推进同一交付包，每完成一个节点提交并推送一次。
只有用户要求重新规划、出现无法绕开的缺失信息、或需要具体真实操作授权时才停下来询问。
