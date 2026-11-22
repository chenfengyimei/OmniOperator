# K17 人格成长派生视图与固定 reader 设计合同

2026-11-22 · design_contract_recorded · 纯文档，无源码/测试/迁移改动。

**本版所有者决策（2026-11-22 确认）**：做「已批准反思 → 人格成长派生视图」，固定 reader **仅所有者只读页/HTTP**；不注入系统前缀、不进入模型上下文、不参与记忆检索或缓存键。交付范围只到「派生 + 所有者只读」，不含 inbox/月卷、人格修订/重建、personality shadow、观影后提问与外联。

## 关键决策（D1–D10）

| 决策 | 内容 | 落点 |
|---|---|---|
| D1 | 已批准反思账本是唯一真值；`growth.md` 只是派生缓存，不可反写 | §1 |
| D2 | 每个 agent 固定落 `<instance>/persona/<agent_id>/growth.md`，不进对象存储（避免 TTL/GC）；agent 目录互不共享 | §2 |
| D3 | GET 从账本重算期望字节并逐字节校验，陈旧/缺失即 409，绝不返回旧文 | §4 |
| D4 | 实例锁内临时文件 + fsync + `os.replace` 原子重建；拒绝 symlink/junction/硬链接/非常规文件 | §2 |
| D5 | 确定性渲染：`approved_day DESC, journal_id ASC`、CR/LF 折单空格、固定空态 | §3 |
| D6 | ≤1 MiB 且 ≤2000 行；超出按最新条目截断并加固定 ASCII 标记 | §2/§3 |
| D7 | 固定 reader 仅所有者 GET/POST；不进系统前缀、模型上下文、记忆检索或缓存键 | §5 |
| D8 | 撤回/修订/擦源立即改变期望字节使 reader 拒绝旧文；物理清除由擦除钩子保证 | §6 |
| D9 | 无新表、无迁移；构建元数据只取文件 mtime/hash/bytes | §8 |
| D10 | inbox/月卷、人格修订重建、shadow、观影后提问、自动注入明确不做 | §9 |

## 0. 一句话

已批准的反思账本是唯一真值；`persona/growth.md` 只是确定性派生缓存。每次读取都从账本重算期望字节并逐字节校验，**撤回、修订或擦源后绝不返回旧文**；重新构建只在实例锁下原子替换，且不新增表、不新增迁移。

## 1. 真值与派生边界

1. 唯一输入是 `reflection_slots`/`reflection_candidates`/`reflection_journals` 中的**当前正式条目**：
   - `reflection_journals.state='pending'` 且 `body IS NOT NULL`；
   - `reflection_slots.withdrawn=0` 且 `active_candidate` 指向该信笺；
   - `reflection_candidates.state='approved'` 且 `journal_revision = journals.revision`。
2. 行文本 = `body[field][start:end]`，即批准时核验过的原句；不重新截取、不引用来源正文/锚点/指纹。
3. 不做逐行 `_live_journal` 复验：文档修订不可变、媒体源删除与文档擦除已在同库触发器把信笺置 `erased` 并删除候选/读取/锚点；修订绑定（`journal_revision`）已排除编辑后的旧候选。本批以这些不变量为准，并用撤回/擦源/编辑用例锁定。
4. 派生文件不得成为第二真值：不提供从文件反写账本的入口，任何构建失败都不改变账本。

## 2. 文件位置与安全

| 项 | 决策 |
|---|---|
| 目录 | `<instance>/persona/<agent_id>/`（新增 `InstancePaths.persona`，`initialize()` 建 0700；agent id 必须规范 UUID；与 `objects/`、`logs/` 同级；不进对象存储，避免 TTL/GC 删除所有者视图；不同 agent 不共享缓存） |
| 文件名 | 每个 agent 目录内固定 `growth.md`；临时文件 `growth.md.tmp-<uuid4>`，同目录 |
| 目录内容 | 只允许 `growth.md` 与 `growth.md.tmp-*`；其他文件不读、不删 |
| 链接拒绝 | 目录及每一级、目标文件、临时文件都拒绝 symlink/junction；读前 `lstat`+`O_NOFOLLOW`，`fstat` 必须 `S_ISREG` 且 `st_nlink == 1` |
| 权限 | 复用实例目录权限与实例锁；文件 0600；不返回绝对路径给 HTTP/UI/日志 |
| 原子重建 | 同目录 `O_CREAT|O_EXCL|O_NOFOLLOW` 写临时文件 → `flush+fsync` → `os.replace` → 目录 `fsync`（复用 `adapters/object_store.py` 的 `publish_file`/`sync_directory` 语义；Windows 用 `MoveFileExW(WRITE_THROUGH, REPLACE_EXISTING)`） |
| 崩溃残留 | 每次构建前清理本目录 `growth.md.tmp-*`（仅普通文件、非链接）；清理失败中止构建并保留旧文件 |
| 上限 | 内容 ≤ 1 MiB（1,048,576 bytes）且 ≤ 2000 行；超限按第 4 节确定性截断 |

## 3. 内容格式（确定性）

UTF-8、LF 换行，固定头部：

```text
# 人格成长（派生视图）

> 由已批准的反思条目生成；不是事实来源，不读取来源正文，不进入模型上下文。
```

条目按 `approved_day DESC, journal_id ASC` 排序，每条一行：

```text
- [YYYY-MM-DD] <sentence>
```

规则：

1. `sentence` 中的 CR/LF 折为单个空格后再写入；账本仍保留逐字原文，本文件是展示视图。
2. 同一句只在账本已去重；派生层不再二次去重，避免同一句因来源日不同被静默合并。
3. 空账本输出固定空态（头部 + `（暂无已批准的反思）`），不含任何私人文本。
4. 截断：从最新条目开始累计，若下一条会使字节或行数超限则停止；被截断时末尾加固定 ASCII 标记 `<!-- truncated: N more approved entries -->`（N 为剩余条数）。截断规则只依赖账本与常量，保证同一账本永远得到同一字节。
5. 不写候选摘要、内部 id、指纹、锚点、时间区间、模型推断或任何来源正文。

## 4. 新鲜度与读取语义

`GET` 的黄金规则：**从账本重算期望字节，逐字节/哈希比对文件；不一致就拒绝，绝不返回旧文。**

- 期望字节按第 3 节生成（成本 ≤ 1 MiB 输出 + 一次有界账本查询）。
- 文件缺失、超限、非普通文件、`st_nlink != 1`、链接、哈希不符、账本期望变化 → `409 persona_growth_stale`（`rebuild_required=true`，只带 `approved_count`/`truncated` 等有界元数据，不带正文）。
- 校验通过才返回 `state="ready"` 与内容；返回的 `sha256`/`bytes` 必须与文件实际字节一致。
- 撤回/修订/擦源在 DB 提交后立即改变期望字节，因此**即使物理文件尚未重建，reader 也不会复活旧句**；物理清除由第 6 节保证。
- `built_at` 取文件 `mtime`（UTC ISO），只是展示信息，不作真值。

## 5. 所有者接口（待 A 确认后接线）

| 方法 | 路径 | 语义 |
|---|---|---|
| GET | `/api/v1/agents/{agent_id}/persona-growth` | 所有者只读：重算并校验后返回 `{state, content, sha256, bytes, approved_count, built_at, truncated}`；陈旧/缺失 → 409 `persona_growth_stale` |
| POST | `/api/v1/agents/{agent_id}/persona-growth/rebuild` | 所有者显式原子重建；幂等（同账本重复调用返回相同 hash）；陈旧/链接/超限 → 4xx/5xx 显式错误码 |

- 沿用现有 owner token 鉴权；无模型工具、无 `/turns` 注入、无 `context`/系统前缀读取。
- 工作台新增只读页「人格成长」：显示内容、`sha256` 前 12 位、`approved_count`、`truncated` 与陈旧提示；只提供「刷新」「重建」，不提供编辑、不下载到用户选择路径（客户端 Blob 导出与既有文档导出一致）。
- 架构守卫：`application/context.py`、`application/engine.py`、`domain/routing.py` 不得导入或读取 reader；以测试钉住「不进入模型上下文」。

## 6. 擦除、撤回与重启

1. 撤回：`reflection_decide(retract)` 已使 `withdrawn=1`，期望字节立即变化；reader 返回 409，直到所有者重建。
2. 文档擦除（实现按 fail-closed 排序）：`DocumentService.erase` 先 `invalidate` 受管缓存——清除失败则擦除不开始、账本不变；随后 `begin`（提交，信笺置 erased）→ 删除源（提交）→ 再 `invalidate`（关闭并发重建竞态）→ `finish(done)`。第二次清除失败：`finish(error="persona_growth_cleanup_failed")`、job 保持可重试；**文件未确认清除前不得报告擦除完成**。重试幂等（begin 复用 failed job、删除源已删返回空、再清除后 finish）。
3. 媒体删除：`MediaService.delete` 先 `invalidate`，再删除源，最后再 `invalidate`。第一次失败不删除源；第二次失败返回 503 `persona_growth_cleanup_failed`（源已删、缓存已先失效，reader 因期望变化仍拒绝旧文；所有者重建会覆盖残留）。
4. 重启/崩溃：临时文件由下一次构建清理；崩溃在「DB 提交后、第二次清除前」时，erasure job 保持 failed/pending，重试继续清理；重启后 reader 因账本变化拒绝旧文，不会复活。
5. 擦源后管理文件若因外部原因重新出现旧字节，reader 的期望字节校验仍会拒绝；重建只从仍有效的已批准账本生成。

## 7. 批内实现顺序与离线验收

| 批次 | 内容 | 放行用例 |
|---|---|---|
| 1. 纯渲染 + 文件存储（离线，无 HTTP） | `domain/persona_growth.py` 确定性渲染；`adapters/persona_files.py` 受管文件读写；`reflection_repository.persona_growth_entries` 只读查询；`application/persona.py` 服务 | 空/单条/多条/排序/CRLF 折叠/Unicode 边界/240 码点；字节与行数截断确定性；符号链接/junction/硬链接/非常规文件/超限拒绝；临时残留清理；原子替换失败保留旧文件；hash/bytes 与文件一致；同账本重建幂等 |
| 2. HTTP + 工作台只读页（接口先交 A） | 两个所有者路由、`HttpRuntime`/`instance` 接线、只读页与导航 | 401/404/agent 隔离；陈旧 409 与重建后 200；不返回路径/正文以外的内部 id；390/1440 无横向溢出；架构守卫「不进入模型上下文」 |
| 3. 擦除失效钩子（共享服务变更，先交 A） | `DocumentService.erase`/`MediaService.delete` 前后 invalidate+核验；失败可重试 | 擦除失败→job failed→重启→重试清除；擦除与读取竞争（先读后擦、先擦后读）；媒体删除同验；临时文件残留清理 |

每批：受影响模块与调用方专项（`uv run --no-sync pytest -q -n 0`）、strict/ruff、索引与证据、回退验证（抽掉校验或 unlink 应变红）。真实模型人格质量、真实 B 站来源与 72 小时不在本项。

## 8. 需要 A 协调的共享面

1. **HTTP**：两个新路由（第 5 节）——属共享接口，接线前提供接口与调用方清单给 A，由 A 确认路由与鉴权。
2. **擦除钩子**：`DocumentService.erase` 顺序调整与 `MediaService.delete` 清理属共享应用服务；先提交接口与调用方，A 合入后平台/UI 再依赖。
3. **无迁移**：本项不新增表、不改 v0052，复用既有反思账本；文件是派生缓存，因此不需要迁移编号。
4. **不进入模型上下文**：若将来要注入回合上下文，须另立合同（授权、预算、脱敏、撤回传播、缓存键），本版明确不做。

## 9. 明确不做

- 不做 inbox/月卷导出与哈希核验（另批）。
- 不做人格修订/删除/重建与 personality shadow。
- 不做自动注入、不做跨会话共享、不做多 profile。
- 不把来源正文、锚点、指纹或模型推断写入人格成长文件。
- 不把 `growth.md` 放进对象存储或导出到用户任意路径。
- 不宣称真实模型人格一致性或观影人文全链对等。

## 实现进度

**批 1（离线核心，2026-11-22）已落地**：

- `domain/persona_growth.py`：已批准句的确定性渲染（`approved_day DESC, journal_id ASC`、CR/LF 折单空格、Unicode 原样、固定空态、1 MiB/2000 行按最新条目截断 + ASCII 标记、sha256/bytes）。
- `adapters/persona_files.py`：受管 `persona/<agent_id>/growth.md` 缓存（规范 UUID 目录、跨 agent 不共享）；逐字节/长度校验、`O_NOFOLLOW` + 硬链接与非常规文件拒绝、同目录临时文件 + fsync + `os.replace` 原子替换、临时残留清理、`invalidate()` 幂等清除。
- `adapters/persistence/reflection_repository.py::persona_growth_entries`：只读当前正式条目（`state=approved`、`journal_revision = journals.revision`、slot 未撤回、信笺 pending）。
- `application/persona.py`：`read` 每次从账本重算期望字节并要求缓存完全一致，否则 `409 persona_growth_stale`；`rebuild` 原子重建；`invalidate` 供批 3 擦除钩子使用。
- `InstancePaths.persona` 与 `initialize()` 建 0700 目录；strict include/`docs/TYPE_CHECKING.md` 已登记三个新文件。
- 离线专项覆盖：空/排序/同日均稳定/换行折叠/240 码点边界/字节与行截断确定性/发布失败保留旧文/超限 413/链接目录/硬链接/文件链接/撤回/编辑替换/擦源/重启后有效缓存与变更后 stale/「不进入模型上下文」守卫。
- **待批 3**：文档/媒体擦除后的 `invalidate` 钩子（共享服务变更先交 A）；真实模型质量、真实 B 站来源与 72 小时另验。

**批 2（HTTP + 工作台只读页，2026-11-22）已落地**：

- 所有者路由：`GET /api/v1/agents/{agent_id}/persona-growth`、`POST /api/v1/agents/{agent_id}/persona-growth/rebuild`；`HttpRuntime`/`Instance` 装配 `PersonaGrowthService(database, PersonaGrowthFiles(paths.persona))`。401 未授权、404 未知 agent、409 陈旧/缺失、200 ready 均按既有错误包络返回。
- **实现勘定修正（多 agent 隔离）**：实例可承载多个 agent，派生缓存不能是实例级单文件；改为 `persona/<agent_id>/growth.md`，`PersonaGrowthFiles` 对 agent 做规范 UUID 校验并拒绝跨 agent 读取。合同 D2/§2 已同步。
- 工作台新增「人格成长」只读页（`web/ui/v2/persona.js`，导航 25→26）：显示正文、sha256 前 12 位、条数、截断与构建时间；409 时只显示「需重建」并清空旧内容，重建按钮显式调用 POST；不提供编辑。
- 专项覆盖：HTTP 401/404/200/409→rebuild→200、撤回后 409 与空态重建、删除缓存文件后 409、未知 agent 404；UI 390/1440 已同步→撤回→刷新显示过期→重建为空；导航 26 页可达；工作台原有页面纳入 `#persona` 碰撞修正（页面 id 为 `persona-growth`）。
- **批 3**：文档/媒体擦除后的 `invalidate` 钩子（共享服务变更先交 A）；真实模型质量、真实 B 站来源与 72 小时另验。

**批 3（擦除失效钩子，2026-11-22）已落地**：

- `application/persona.py` 新增窄端口 `PersonaGrowthInvalidator`（`async invalidate(agent) -> bool`）；`DocumentService` 与 `MediaService` 增加**可选 keyword** `persona=`，未注入时保持原有行为（既有调用方零改动）。
- `DocumentService.erase`：先 invalidate（失败即中止，账本与源不变）→ begin → 删除源（提交）→ 再 invalidate → finish(done)；第二次失败写 `finish(error=persona_growth_cleanup_failed)` 并抛出，job 可重试；`done` 重放同样先 invalidate。
- `MediaService.delete`：invalidate → 删除源 → invalidate；第一次失败不删源，第二次失败返回 503（源已删、缓存已先失效）。
- `composition/instance.py` 在 `documents`/`media` 之前构造 `persona_growth` 并注入两服务；其余调用方（HTTP 路由、测试夹具）签名不变。
- 专项覆盖：生产实例文档擦除后缓存消失且反思详情失效；缓存清除失败时擦除 fail-closed、无 job、源保留；删除源后清除失败→job failed→重试 attempt=2 done；媒体删除前后各一次 invalidate、首次失败不写库、二次失败写库后报错。
- **共享面交接 A**：新增可选构造参数与 `PersonaGrowthInvalidator` 端口；受影响调用方仅 `composition/instance.py`（已接），HTTP 路由与既有 `DocumentService(db, objects, vectorizer)`/`MediaService(...)` 构造保持兼容。
- **待后续**：真实模型人格质量、真实 B 站来源与 72 小时；inbox/月卷、所有者正文编辑页、shadow/观影后提问不在本项。
