# V2 会话交接（2026-11-13，Windows）

> 用途：把当前会话的全部可验证状态、剩余工作与施工规范交给下一个会话。
> **默认正式入口仍是 V1**；`omni`/`python -m omni`/`omni-web` 都还是 V1。V2 只有开发入口
> `python -m omni.composition.cli --home <home> ...`。任何"V2 已完成/已迁移"的说法都不成立。

## 1. 一句话现状

Windows 上 V2 底座**可冷启动、可自检、可服务、可跑全量测试、可离线打包**；strict 门禁 **282 文件 0 错误**
（domain/application/composition 三层全层完成，P2 Android 全部完成，P3 已起步）；但 **50 个 adapters 未类型化**，
且**真实模型/MCP/市场/设备/安装态/三平台验收全部未做**，入口切换未评估。

## 2. 已核验的事实（可直接引用，不必重测）

| 项 | 数值 | 证据/命令 |
| --- | --- | --- |
| strict 门禁 | **282 文件 / 0 errors / 0 warnings** | `uv run --no-sync pyright --project pyrightconfig.strict.json` |
| strict 分层 | 53/53 domain、**44/44 application 全层**、172/222 adapters、**13/13 composition 全层** | `docs/TYPE_CHECKING.md` |
| 未纳入 strict | 50 个 adapters（P3 已纳入 schema/credential/lease/configuration/repository/delivery）（domain/application/composition 已全层纳入） | `temp/build/probe_strict.py`（survey-remaining.py 已不在库） |
| 全量测试 | 4656 项：**4654 passed / 2 failed / 0 errors**（`-n 2`，56 分钟，第 15 节点） | `temp/build/v2-full-155.xml`、`docs/evidence/v2-typecheck.json` 的 `full_run` |
| 失败性质 | 2 项：串行复跑 **1 过 1 败**（skills_ui[dark-760] 为抖动）；剩 `test_ui_embed_layer` 为**并行 UI 会话提交 `0497fe8` 引入的真实守卫违规**（`ui/v2/v1/frame.css` 副本截至第 15 节点仍未移除），归 UI 会话修，非本批回归 | 见 §5「验收口径」；两份报告留档 |
| 默认入口 | `V1 until R6 parity gate` | `docs/tasks/v2.json` 的 `default_entrypoints` |
| 台账 | `status=d7_typecheck_batch_158`、`checkpoint_commit=8a7e688`、源码指纹 `06a8edd3…`（1206 文件） | `docs/tasks/v2.json`、`docs/evidence/v2-typecheck.json` |
| 工作树 | 干净，`codex/architecture-v2` 与 origin 同步（HEAD `e2f72a0` 之后的文档提交） | `git status -sb` |

## 3. 现在能用什么（Windows，实测）

```powershell
uv run --no-sync python -m omni.composition.cli --home temp\demo-home init
uv run --no-sync python -m omni.composition.cli --home temp\demo-home doctor
uv run --no-sync python -m omni.composition.cli --home temp\demo-home serve --port 8799
# 另一个窗口（令牌由首次 serve 创建）
uv run --no-sync python -m omni.composition.cli --home temp\demo-home auth
```

实测结果：`init` → schema `v2_0037`、`api_version 1`；`doctor` → `integrity ok`、`fk 0`、148 表、1 个 agent；
`GET /api/v1/version|capabilities|agents` 均 200；`GET /` 工作台 200（约 71 KB）。
冷启动守卫：`agent/tests/test_v2_windows_cold_start.py`（init → doctor → auth 指引）。

## 4. 剩余工作（按顺序，含文件与实测错误数）

### 4.1 P1 收尾：composition —— **已全部完成（13/13）**

1. ~~`composition/instance.py`（58 错）~~ —— 第一百四十二批，提交 `9f8f1f2`。
2. ~~`composition/http.py`（实测 25 错）~~ —— 第一百四十三批，提交 `0164f79`。
3. ~~`composition/cli.py`（实测 39 错）~~ —— 第一百四十四批，提交 `ea7e8c2`（含 5 个支撑适配器返回类型纯精化）。
4. ~~`composition/service_control.py`（5 错）+ `__init__.py`~~ —— 第一百四十五批，提交 `456c59f`，composition 全层完成。

**P1 composition 与 P2 application/android.py 均已完成；下一节点是 P2 剩余 android 适配器/仓储/worker，见 §4.2。**

### 4.2 P2 Android（业务缺口最大）

- ~~`application/android.py`（475 错）~~ —— **已完成**（第一百四十六批，提交 `46bbaa3`，application 44/44 全层）。
  设备授权/观察/输入派发/IME 恢复用例显式 Record/Payload，有界任务泛型化，AndroidPort 协议精化。
- ~~`persistence/android_unicode_repository.py`(84)~~ —— 第一百四十七批，提交 `fa5f171`。
- ~~`persistence/android_repository.py`(93)~~ —— 第一百四十八批，提交 `8fdbc73`。
- ~~`adapters/android_unicode.py`(88)~~ —— 第一百四十九批，提交 `3d8b7a8`。
- ~~`adapters/android.py`(97)~~ —— 第一百五十批，提交 `c1d03aa`（协议口径对齐）。
- ~~`workers/android_unicode.py`(105)~~ —— 第一百五十一批，提交 `2906dc0`。
- ~~`workers/android.py`(246)~~ —— 第一百五十二批，提交 `142eff1`。
- **P2 Android 7 个模块全部完成。下一步 P3 持久化 34 文件，见 §4.3。**

### 4.3 P3 持久化（34 文件，6840 错里的大头）

建议切片顺序（每片 1 个节点，单文件或 ≤60 错）：
1. `repository.py`(188) + `schema.py`(58)；
2. `configuration_repository` / `credential_repository` / `lease_repository` / `delivery_repository`(220)；
3. `qq_repository`(41) / `bilibili_repository`(63) / `media_repository` / `image_repository`(59) /
   `document_repository` / `context_repository`；
4. `memory_repository`(47) / `memory_organization_predicates` / `memory_organization_repository` /
   `memory_semantic_repository`；
5. `history_pruning_cycle`(45) / `object_gc`(65) / `maintenance` / `maintenance_repository` /
   `reference_release_repository` / `v0005.py`；
6. `mcp_repository`(318) / `terminal_repository` / `terminal_review_repository`(64) / `skill_repository` /
   `routing_repository`(53) / `schedule_repository`(58) / `action_review_repository` / `extension_repository`。

### 4.4 P4 业务大适配器

`bilibili.py`(350，拆 3) → `workers/bilibili.py`(300，拆 2) → `workers/local_vectors.py`(62) /
`media.py` / `local_vectors.py` / `model_stream.py`(76) / `http.py` / `onebot.py` / `diagnostic_bundle.py` /
`extensions/packages.py`(73)。

### 4.5 P5 platform

`linux_atspi.py`(110，拆 2) → `linux_x11` / `linux_semantic` / `portal` → `service_templates`(64) /
`service_registration` / `service_operation_files` / `runtime_identity` / `deployment_files`(47)。

### 4.6 P6 MCP

`mcp/stdio.py`(67) → `mcp/http.py` → `mcp/oauth.py` 剩余方法。

### 4.7 P8 验收闭环（可与上面并行）

1. 真实模型端到端（成功/失败/限流/中断/成本对账）；
2. 真实 MCP 服务 + 真实市场/第三方插件；
3. Windows 真实前台键鼠与桌面捕获人工确认（用户已明确延后，不要自行弹窗）；
4. 干净机器 `install → update → rollback → uninstall`（含 WAL 一致快照）；
5. 容器镜像 + macOS/Linux 构建；
6. 24h 长跑（并发回合、队列、fd/内存）；
7. **入口切换门槛评估**（默认 V1，切换必须单独立项评审）。

## 5. 施工规范（每个节点照做，不可跳）

1. `uv run --no-sync python temp/build/probe_strict.py <file>` 实测错误 → 逐项清零，**不加 `pyright: ignore`**
   （确需忽略必须写进 `docs/TYPE_CHECKING.md` §4）。
2. 同步三处：`pyrightconfig.strict.json`（排序）、`docs/TYPE_CHECKING.md` 表格与分层计数、证据。
3. 门禁：`pyright --project pyrightconfig.strict.json` 0/0；专项与其调用方 `-n 0` 通过；
   改动文件默认模式 pyright **不新增**错误（`temp/build/compare_default.py`）；`ruff check` 通过。
4. 源码提交 `refactor(v2): 第NNN批 …` → 证据/台账/四份交接文档提交 → `project_index.py` + `--check` → 推送。
5. 每 5 个节点跑一次全量 `-n 2`，结果写进 `docs/evidence/v2-typecheck.json` 的 `full_run`。

### 验收口径（重要）

`-n 2` 下 Playwright 重度用例会因资源争用偶发失败；**必须**：先并行跑，再把失败项用 `-n 0` 串行复跑，
两边报告都留档。禁止把"串行通过"写成"并行全绿"，也禁止把抖动写成功能缺陷。

## 6. 已知风险与红线

| 风险/红线 | 处理 |
| --- | --- |
| 另一会话正在改 `web/ui/v2/*`、`docs/V2_UI_PROMPT.md`、`docs/evidence/v2-ui.json` | **只提交自己显式路径**，绝不 `git add -A`；其未提交改动必须保留 |
| 设计系统守卫（强调色/圆角/断点/契约） | 已把 `workbench.css` 裸值收敛到 `tokens.css` 的 `--v2-*`；UI 会话若再写裸值会打挂 `test_ui_glass_tokens` |
| 数据库一致性 | 只用一致快照接口（含 WAL），禁止裸复制 `omni.db` |
| 未知结果 | unknown ≠ 失败，不自动重发 |
| 支付/发布/群发/加好友/无人值守 | 继续走审批与 fail-closed |
| 用户服务 | 不要停用户服务、不要 `uv sync`；演示用独立 home + 独立端口 |
| 入口 | 默认 V1；不要在未评审前切换或删除 V1 |

## 7. 首次开工的三条命令

```powershell
git status -sb                                   # 必须干净；脏的话先确认不是自己的改动
uv run --no-sync pyright --project pyrightconfig.strict.json   # 期望 0/0（282 文件）
uv run --no-sync python temp/build/probe_strict.py agent/src/omni/adapters/android.py  # 以实测为准
```

domain/application/composition 已全层纳入（第一百四十二～一百四十六批），P2 Android 7 个模块全部纳入（第一百四十七～一百五十二批）。**P3 已起步**：`persistence/schema.py`(58) 已纳入（第一百五十三批，提交 `eacf4dc`，136 张表 DDL 与 HEAD 逐字一致 sha256 `e49d4152`）。`persistence/credential_repository.py`(80) 已纳入（第一百五十四批，提交 `ae6d07d`）。`persistence/lease_repository.py`(88) 已纳入（第一百五十五批，提交 `8d859ba`）。`persistence/configuration_repository.py`(94) 已纳入（第一百五十六批，提交 `1813ff9`；`configuration_get` 保持原 `dict[str, Any]` 口径以兼容 4 个已纳入调用方）。`persistence/delivery_repository.py`（实测 207）已纳入（第一百五十八批，提交 `8a7e688`）：回执对齐端口 `Record` 契约，`result` 参数保持 `Record` 符合逆变。P3 剩余为 §4.3 其余分片。每节点同步三处。
