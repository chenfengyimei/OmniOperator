# V2 开发收尾清单与开发规划（2026-11-21 快照）

> 用途：给接手会话一份**可执行、可验收、可勾选**的收尾计划；也是「整个 V2 何时可以宣布开发完成」的唯一执行视图。
> 真值优先级：`docs/tasks/*.json` 与各合同 > 本文件 > HANDOFF 历史叙述。本文件与台账冲突时，以台账为准并在报告中说明。
> 快照：分支 `v2`，HEAD `18b75b75`（2026-11-21 17:19），工作区 40 个已修改 + 10 个未跟踪（并行会话在途）。
> 本文件不修改任何源码、测试、迁移或真值台账；新增本文件后**暂不**再生 `docs/index`（工作树有他会话未提交编辑，遵守既有「索引再生暂缓」纪律）。

---

## 0. 先读什么（接手顺序）

1. 仓库级纪律：[AGENTS.md](../../AGENTS.md)、[CONTRIBUTING.md](../../CONTRIBUTING.md)、[TESTING.md](../TESTING.md)
2. 状态与计划：[HANDOFF.md](../HANDOFF.md)（顶部当前段）→ 本文件 → [DEVELOPMENT_STATUS.md](../DEVELOPMENT_STATUS.md)
3. 真值台账：[v2-development.json](../tasks/v2-development.json)、[v2-agent-core-parity.json](../tasks/v2-agent-core-parity.json)、[v2-windows-candidate.json](../tasks/v2-windows-candidate.json)
4. 架构与合同：[ARCHITECTURE.md](../ARCHITECTURE.md)、[V2 导航](README.md)、`docs/v2/contracts/` 中与任务对应的合同
5. 平台断点：[Linux 交接](platforms/linux/V2_LINUX_HANDOFF.md)、[macOS 交接](platforms/macos/V2_MACOS_HANDOFF.md)、[Windows 验收](platforms/windows/WINDOWS_V2_ACCEPTANCE_2026-11-18.md)

---

## 1. 当前状态快照

### 1.1 规模与活跃度

| 指标 | 数值 |
|---|---|
| 源文件 / 模块 / API / 声明工具 | 833 / 65 / 458 / 94 |
| 测试文件 / 测试函数 | 531 / 4,515 |
| 合同 / 证据 JSON | 82 / 288 |
| 近 30 天提交 | 1,128 |
| strict 类型检查覆盖 | **424 个文件**纳入 include（最近批次报告 418 文件零错误/警告，差值来自其后新增文件） |
| 最近全量 | **5,998 项，仅覆盖 `7e64d348`（2026-11-17）**；之后一次全量在 `4f0815b7` 中途终止，无完整结果 |

> 注：`docs/index/project.json` 的计数来自最近一次再生；因并行未提交编辑，索引再生被暂缓，计数可能略滞后于工作树（以 §1.5 债务 D4 处理）。

### 1.2 任务层（真值）

| 任务 | 内容 | 状态 |
|---|---|---|
| G1 | 持久事件通知与工作台断线续接 | ✅ `implemented_scoped_passed` |
| G2 | 多 Agent 空间用户入口 | ✅ `implemented_scoped_passed` |
| G3 | Linux 持续窗口身份与显式前台控制 | 🟡 `development_in_progress`（L1 已过，L2 进行中） |
| G4 | Wayland 控制授权、撤回与输入暂停 | 🟡 `development_in_progress`（`input_authorized=false`） |
| G5 | macOS/Linux/无桌面/容器交付收口 | 🟡 `development_in_progress` |
| G6 | 剩余业务/扩展/运维生产链核对 | 🟡 `code_audit_in_progress`（含 K01–K18 对标） |
| G7 | 冻结后统一全量、缺陷修复与最终交接 | ⏳ `waiting_for_development_completion` |

历史里程碑 54 项：**26 待真实验收、15 开发中、10 完成（离线）、2 部分完成、1 进行中**。

### 1.3 内核对标 K01–K18

| 分组 | 状态 | 说明 |
|---|---|---|
| K01 系统前缀/缓存范围 | 🟡 设计合同已记录 | D3 跨 run 前缀方案待所有者选择：(b) 仅路由键注入可先落；(a)(c) 触及冻结前缀需批准 |
| K02–K12（11 项） | ✅ 生产接线、专项通过 | 回包协议、任务清单、委派、批次并行、run 内压缩、迭代守卫、后台学习、历史检索、澄清、停止验核、故障恢复 |
| K13 工具/渠道/cron | 🟡 审计完成，缺口部分实现 | 已落 search_files/patch/search 等；缺 browser_dialog、execute_code、CDP、cron 绑定、27 个渠道 |
| K14 Android/视觉子代理 | 🟡 审计完成，缺口未实现 | 视觉子代理、SoM、OCR、explore_app |
| K15 人格/遗忘/自主 | 🟡 审计完成，部分实现 | 衰减核心/评分/定时已实现；动机引擎、分层人格、作息反馈仍缺 |
| K16 视频分镜 | 🟡 审计完成，部分实现 | locate/cells/zoom 已实现；HSV 切点、Laplacian 选帧、自适应宫格仍缺 |
| K17 观影人文 | 🟡 partial | `reflection_document_journal` 今日完成（50 单测）；下一步媒体锚点 → 摘录/所有者决定 → 人格成长 |
| K18 B 站全集 | 🟡 审计完成，读广度已补 | search/whois/danmaku/feed/following/favorites/tags/history/msgfeed 已接；写广度、主动观看、分享、直播、风险暂停仍缺 |

### 1.4 平台层

| 平台 | 已完成 | 未完成 |
|---|---|---|
| **Windows** | W1–W8 全部完成；2026-11-18 实机专项（原生读取/ConPTY/安装备份恢复卸载/真实 ASR 与抽帧）；click 缺陷已复核为**样例问题**，生产未改，回执保持 unknown | 真实系统服务、干净机器、真实账号/设备/72h |
| **macOS** | 2026-11-20 arm64 helper 构建/签名/IPC 探测 + 233 项专项 | 桌面权限（AX/屏幕/输入）实测、PTY 退出清理失败 2 次、系统服务、Keychain、真实安装；分支 `56f0aff1` 在 Gitee 未查到，待核实 |
| **Linux** | G3.L1 窗口代次；Enter 状态批 `f539f21e`（194 专项）；AT-SPI/X11/Portal/EIS/PipeWire 源码与测试齐全 | 真实 Linux 未跑；通用提供方/控件关联、Tab 顺序、弹窗/关闭、业务提交后验；Wayland 业务输入未接 |
| **容器** | Dockerfile/Compose/断网核验工具/CI 任务 | **镜像从未真实构建或运行** |

### 1.5 质量与债务

- 当前源码**没有统一全量通过**；`current_full_passed=false`、`all_quality_gates_passed=false`。
- 已知观察项：并行全量下 `debug_sink` 一处时序失败（隔离 11 项全过）；`test_v2_todos::test_runtime_restart_reinjects_active_plan_only_into_its_own_session` 1 项竞态；5 个 pin `v2_0039` 的旧迁移测试滞后。
- `docs/index` 聚合索引因并行未提交编辑而**暂缓再生**。
- 无代码签名、无 macOS 公证、无正式发布；Linux 桌面打包在发布流程中被禁用。

---

## 2. 完成定义（DoD）

### 2.1 全局完成（项目自己的口径，不可放宽）

`whole_v2_complete=true` 必须同时满足：

1. K01–K18 **全部**达到「已实现并专项通过」或「所有者书面接受为不做（带理由与范围）」；
2. G1–G6 全部 `implemented_scoped_passed`；
3. 冻结源码上**统一全量通过**，且无未解释的 flake；
4. 三平台原生实机验收与容器真实运行完成；
5. 真实账号/模型质量/设备/干净机器/72 小时验收完成；
6. 发布链（签名/公证/版本/回滚）完成或所有者明确降级为「源码交付」；
7. `HANDOFF`、`DEVELOPMENT_STATUS`、`docs/index`、各证据与台账同步。

**任何一项缺失，都只能说「开发完成、待验收」，不能说「整个 V2 完成」。**

### 2.2 单批次 DoD（每批必须全部满足）

| # | 要求 |
|---|---|
| 1 | 先合同后实现；合同写清边界、拒绝路径、上限、错误码、不变式 |
| 2 | 生产接线（domain → application → adapters → composition），不留死代码 |
| 3 | 受影响模块 + 调用方专项通过；记录 nodeid/耗时；不跳过、不放宽 |
| 4 | `pyright` strict 覆盖新文件；ruff/格式通过 |
| 5 | 新表必有冻结迁移 + head 同步 + 迁移回滚/崩溃测试（若涉及） |
| 6 | 证据 JSON 落 `docs/evidence/v2-<topic>.json`，回填最终提交哈希 |
| 7 | 台账更新（`current_followup`/`evidence`/`requirement.status`），不改写历史 |
| 8 | 提交信息中文「领域：要点——细节」；不夹带他会话改动 |

### 2.3 需要所有者决策的事项（不决策不做）

| # | 事项 | 选项 | 不决策的后果 |
|---|---|---|---|
| 1 | K01 跨 run 系统前缀缓存范围 | (b) 仅路由键注入（可先落）／(a)(c) 前缀重构（需批准） | K01 停在设计合同 |
| 2 | K13 `execute_code` 代码沙箱 | 做（需隔离合同）／不做（明确接受缺口） | 与参考差距保留 |
| 3 | K15 主动动机引擎范围 | 全量／仅核心（需求→目标→驱动）／不做 | K18 主动观看被阻塞 |
| 4 | K16 自适应宫格/DP 打包 | 新设计合同／维持固定 2 列 | 分镜档位不对等 |
| 5 | K17 人格成长文档模型 | 做（需固定 reader 合同）／不做 | 观影人文链不闭合 |
| 6 | K18 主动观看与直播 | 全量／只做主动观看／都不做 | B 站「有日常生活」不对等 |
| 7 | K14 视觉子代理成本 | 允许视觉模型调用／只做离线桩 | 视觉子代理不落地 |
| 8 | 发布形态 | 签名+公证／仅源码交付 | C4 无法收口 |
| 9 | Linux 桌面打包 | 恢复发布流程／仅 CLI | Linux 交付范围不定 |

> 决策结果写入对应合同与台账；未决策项在报告中列为「待所有者决策」，不得自行默认。

---

## 3. 收尾工作分解（五个阶段）

> 阶段可并行：C0 基线、C1 功能缺口、C2 平台收口可同时推进；C3 需要所有者环境；C4 最后。

### 阶段 C0：基线冻结与全量分诊（不阻塞开发）

| 批次 | 内容 | 验收 |
|---|---|---|
| C0.1 | 建独立基线工作树（如 `D:\omni-baseline`，checkout `18b75b75`），装独立环境；**不动主工作树与用户服务** | 基线目录指纹与 HEAD 一致 |
| C0.2 | 在基线跑一次全量：`uv run --no-sync pytest -q`（默认最多 4 worker），保存 JUnit + 失败清单 | 有完整结果文件；不把「进度点」当结果 |
| C0.3 | 失败分诊：产品缺陷 / 测试不稳 / 迁移 pin 债务 / 环境差异，逐条落 debt 表（见 §6） | 每条有归属与处置 |
| C0.4 | 对当前 HEAD 跑 strict 与 ruff，记录数字 | 记录文件数与结果 |
| C0.5 | 工作树洁净后再生 `docs/index`（`uv run --no-sync python scripts/project_index.py`） | 索引与源码一致 |

**C0 完成判据**：有一份可复现的全量基线报告 + 分诊表；后续每批都能对比。

### 阶段 C1：功能缺口（K13–K18 + K01）

按依赖排序；每项一张卡（见 §4）。建议先做「可离线、无新表、无真实环境」的批次，把需要设计合同/所有者决策的项单列。

| 批次 | 目标 | 依赖 | 规模 | 需真实环境 |
|---|---|---|---|---|
| C1.1 `K17.media_anchor` | 绑定当前 run 实际读过的媒体产物/片段，接媒体擦除 | K16 媒体链 | S | 否 |
| C1.2 `K17.excerpt_decision` | 摘录 + 所有者决定/撤回，接反思选择 | C1.1 | M | 否 |
| C1.3 `K17.persona_growth` | 人格成长文档模型（1MiB 上限、hash、no-symlink、固定 reader） | 设计合同 | M | 否 |
| C1.4 `K13.browser_dialog` | 原生对话框处理工具（alert/confirm/prompt/beforeunload） | 浏览器 driver | S | 否 |
| C1.5 `K13.execute_code` | 代码沙箱（先设计合同；隔离/资源/超时/审计） | 所有者决策 | L | 否 |
| C1.6 `K15.motivation` | 需求→目标→驱动→情绪→动作，含外联硬闸 | 设计合同 | L | 否（质量另验） |
| C1.7 `K15.persona_layers` | 分层人格合成 + 渠道裁剪 + epoch 切换 | 设计合同 | M | 否 |
| C1.8 `K16.cut_detection` | HSV 切点检测 + 密度分段（V2 四层实现） | K16 媒体链 | M | 否 |
| C1.9 `K16.laplacian` | Laplacian 清晰度选帧 | K16 | S | 否 |
| C1.10 `K18.write_breadth` | fav/dynamic/danmaku 发送（已审表单 + 配额） | 发表审批链 | M | 是（送达） |
| C1.11 `K18.active_watch` | 夜间计划/随机唤醒/双日记/候选打分 | C1.6 | L | 是 |
| C1.12 `K18.share_policy` | ≤3/日、7 天去重、notify 多后端 | 投递链 | M | 是 |
| C1.13 `K18.live` | 直播弹幕 WebSocket（可选，所有者定范围） | 无 | M | 是 |
| C1.14 `K14.visual_subagent` | 有界视觉子代理（K04 + 视觉模型，无工具、单次、文本汇总） | K04 | M | 否 |
| C1.15 `K01.D3` | 缓存范围方案落地（先所有者选 a/b/c） | 所有者决策 | M | 否 |

### 阶段 C2：平台收口

| 批次 | 内容 | 平台/会话 | 验收 |
|---|---|---|---|
| C2.1 `G3.L2` | 通用提供方/控件原生窗口关联；Tab 顺序、弹窗/关闭、业务提交后验；Xinerama/多 root 布局 | Linux（Mac 会话） | 真实 X11 会话隔离窗口逐项验收 |
| C2.2 `G4.Wayland` | 合成器权威 PID/窗口/座席/区域与 AT-SPI 节点关联；非 evdev 介入合同；逐次业务许可与应用后验 | Linux（Mac 会话） | `input_authorized` 仅在完整证明后为真 |
| C2.3 `macOS.permissions` | AX/屏幕/输入权限申请与拒绝路径；锁屏/睡眠/切换用户 | Mac | 隔离账号实机 |
| C2.4 `macOS.pty` | PTY 退出后清理两次失败修复 | Mac | 复现→修复→回归 |
| C2.5 `macOS.services` | LaunchAgent/系统服务注册/加载/就绪/卸载 | Mac | `accept_macos_services.py` 只读+原生 |
| C2.6 `macOS.keychain` | Keychain 写入/回读/删除/清理 | Mac | `accept_macos_keychain.py` |
| C2.7 `Windows.native` | 真实系统服务安装/停止/升级；click 真机复核（保留 unknown 语义） | Windows | 原生服务真实安装 |
| C2.8 `container.real` | 真实 build/run、`--network none` 探针、重启恢复、UID/0700/0600 | Linux/CI | `check_v2_container.py` 真实报告 |
| C2.9 `install.matrix` | 干净机器 install→update→rollback→uninstall（三平台） | 全部 | 每平台一份 verified 报告 |

### 阶段 C3：真实环境验收（需要所有者窗口）

| 批次 | 内容 | 前置 | 产物 |
|---|---|---|---|
| C3.1 | 干净机器完整安装/更新/回滚/卸载 | C2.9 | 每平台报告 + SHA |
| C3.2 | QQ/NapCat 扫码、白名单、收发、断线重连 | 真实 QQ | 证据 + 人工核对 |
| C3.3 | B 站账号：读取、发表审批链、风控暂停 | 真实账号 | 证据（不含凭据） |
| C3.4 | 模型质量：人格一致性、信笺/反思/问题措辞 | 真实模型 | 评审记录 |
| C3.5 | 安卓设备：输入/Unicode/滚动/恢复 | 真实设备 | 设备矩阵报告 |
| C3.6 | 72 小时长跑 | 开发冻结 | `v2_soak.py` 通过报告 |
| C3.7 | 三平台原生桌面/终端/凭据 | C2 | 每平台 native 报告 |

### 阶段 C4：发布收尾

| 批次 | 内容 |
|---|---|
| C4.1 | 版本号/CHANGELOG/发布门禁 |
| C4.2 | Windows 签名、macOS 公证（或所有者明确降级为源码交付） |
| C4.3 | 升级/回滚演练（release/exchange 链） |
| C4.4 | HANDOFF/DEVELOPMENT_STATUS/索引/台账最终同步 + 交接提示词 |

---

## 4. 批次任务卡（执行粒度）

> 每张卡：目标 / 范围（改什么、不改什么）/ 验收 / 证据 / 完成判据。
> 未列出的实现细节以对应合同为准；没有合同的新能力先写合同。

### C0.1 基线工作树

- 目标：得到与当前 HEAD 完全一致的独立可跑环境。
- 做：`git worktree add <独立目录> 18b75b75`；在该目录 `uv sync --frozen`；用 `uv run --no-sync python -m omni.composition.cli --home <基线实例目录> init` 与 `doctor` 验证（V2 入口，不是 V1 的 `omni` 脚本）。
- 不改：主工作树、用户服务、运行中数据库。
- 验收：`git rev-parse HEAD` 与基线一致；`omni doctor` 通过。
- 证据：基线目录 path、HEAD、依赖锁哈希、doctor 摘要。

### C0.2 统一全量基线

- 目标：当前源码第一次完整全量结果。
- 做：基线目录执行 `uv run --no-sync pytest -q`（4 worker），JUnit 落盘。
- 验收：完整 JUnit；失败逐条列 nodeid。
- 证据：`temp/build/closeout-full-<date>.xml` + 摘要。

### C0.3 失败分诊

- 目标：把失败分成四类并归属。
- 做：产品缺陷→开批次；测试不稳→复现 3 次；迁移 pin 债务→记录；环境差异→标注。
- 验收：debt 表（§6）更新，无「未解释失败」。

### C0.4 静态质量基线

- 做：`pyright -p pyrightconfig.strict.json`、ruff check、format check。
- 验收：记录文件数/错误数/警告数。

### C0.5 索引再生

- 前置：工作树洁净（无他会话未提交编辑）。
- 做：`uv run --no-sync python scripts/project_index.py`。
- 验收：`docs/index` 与源码一致，无他会话改动夹带。

### C1.x 通用卡（每个功能缺口批次复用）

- 目标：实现合同中定义的缺口，生产接线。
- 范围：只改 `agent/src/omni/{domain,application,adapters,composition}` 与对应测试/合同/证据；不动 V1 `tools/` 遗留注册。
- 验收：
  - 专项：`uv run --no-sync pytest -q -n 0 agent/tests/v2/<分组>/test_<模块>.py`；
  - 调用方：跑受影响模块的调用方集合（合同/证据里列）；
  - strict/ruff：新文件纳入 include；
  - 回退：抽掉接线应红，恢复后绿（如适用）。
- 证据：`docs/evidence/v2-<topic>.json`（含命令、结果、回退证明、提交哈希）。
- 完成判据：合同边界全覆盖、拒绝路径有测试、台账更新、提交完成。

### C2.x 平台卡

- 共同要求：只在隔离账号/自有窗口操作；不做批量输入；unknown 不重放；证据不含正文/凭据/像素。
- Linux：`agent/scripts/accept_linux_desktop.py`、`accept_linux_portal.py`、`accept_foreground.py`。
- macOS：`accept_macos_desktop.py`、`accept_macos_services.py`、`accept_macos_keychain.py`。
- Windows：`accept_foreground.py` + 原生服务安装/停止演练。
- 容器：`scripts/check_v2_container.py`。
- 完成判据：真实环境报告 `sample_passed`（或明确失败与原因），并回填证据。

### C3.x 验收卡

- 共同要求：先冻结源码；记录环境（OS/架构/版本/账号脱敏）；每项独立证据；失败不得改判为通过。
- 72h：`agent/scripts/v2_soak.py run --home <实例> --url http://127.0.0.1:8766 --output <新目录> --limits <所有者批准文件>`；报告 `observational_soak_passed`。
- 干净机器：`scripts/check_v2_installation.py` + 更新/回滚/卸载链。

---

## 5. 执行顺序与并行建议

```
C0.1 ── C0.2 ── C0.3 ── C0.4 ── C0.5
  │
  ├─ C1.1 ─ C1.2 ─ C1.3        （Windows 主会话，串行）
  ├─ C1.4 ─ C1.9 ─ C1.8        （Windows 主会话，可并行）
  ├─ C1.6 ─ C1.7 ─ C1.11       （需先有设计合同）
  ├─ C1.10 ─ C1.12 ─ C1.13     （需真实 B 站）
  ├─ C1.14 ─ C1.15             （K04 已具备）
  └─ C2.1 ─ C2.2 ─ C2.3 ─ C2.4 ─ C2.6   （Mac 会话）
C3 只在 C2 与功能冻结后开始；C4 最后。
```

并行纪律：
- 同一时间**只允许一个会话推进迁移 head**（新增迁移必须串行）。
- `docs/index`、`HANDOFF`、`DEVELOPMENT_STATUS` 的最后更新**只在 C4 做**，避免多会话互踩。
- 工作树有他人在途时，全量用独立基线目录，不在主工作树跑全量。

**首日行动（建议）**：C0.1 + C0.2（建基线、拿全量结果）→ C1.1（K17 媒体锚点）→ C1.4（browser_dialog）。这三步互不依赖，能当天产生第一份可对比的基线和一个功能增量。

---

## 6. 已知债务与风险

| ID | 问题 | 影响 | 处置 |
|---|---|---|---|
| D1 | 5 个 pin `v2_0039` 旧迁移测试滞后 | 全量计数噪音 | 统一升级断言或标记为迁移历史测试 |
| D2 | `test_v2_todos` 重启竞态（后台评审与断言竞争） | 偶发全量红 | 夹具停自动评审或改断言时序 |
| D3 | `debug_sink` 并行时序 flake | 全量偶发 | 隔离复现 3 次，修夹具或加等待 |
| D4 | `docs/index` 暂缓再生 | 索引与源码可能不一致 | 工作树洁净后 C0.5 |
| D5 | Mac 分支 `56f0aff1` 在 Gitee 查不到 | macOS 基线不明 | Mac 侧核对推送/远端回读 |
| D6 | 无签名/公证/正式发布 | 不能对外分发 | C4 决策：签名或源码交付 |
| D7 | 并行会话 40 改 + 10 未跟踪（HANDOFF 28 处勿碰） | 冲突/误提交 | 不碰；只提交本任务文件 |
| D8 | 真实环境不可得 | 阻塞 C3 | 所有者安排窗口 |
| D9 | 全量长期未跑（最近 4 天 100+ 提交） | 回归风险累积 | C0.2 立即建基线 |

---

## 7. 进度追踪

- 每批次完成 → 更新 `docs/tasks/v2-development.json`（或对应台账）的 `current_followup`/`evidence`/`status`，并写证据 JSON。
- 勾选表建议放在会话内（不要多人同时编辑同一份 md）。
- 报告口径：
  - 「专项通过」只写具体 nodeid/文件与耗时；
  - 「全量」必须给 JUnit 文件与总数；
  - 「实机」必须给平台/账号脱敏/命令/结果；
  - 不得用「已完成」替代「待验收」。

---

## 8. 里程碑

| 里程碑 | 判据 |
|---|---|
| M1 基线绿 | C0.2 全量有结果、失败已分诊 |
| M2 功能缺口清零 | K01–K18 全部实现或所有者接受不做 |
| M3 平台收口 | C2 全部真实报告（Linux/macOS/Windows/容器） |
| M4 全量绿 | 冻结源码全量通过，flake 清零 |
| M5 真实验收 | C3 全部报告 |
| M6 发布/交接 | C4 完成，`whole_v2_complete=true` |

---

## 9. 总勾选表（复制到会话内使用）

> 勾选只代表「该批次 DoD 满足」，不代表整个 V2 完成。每勾一项须附证据路径与提交哈希。

### C0 基线
- [ ] C0.1 独立基线工作树与环境
- [ ] C0.2 全量基线（JUnit + 失败清单）
- [ ] C0.3 失败分诊（四类）
- [ ] C0.4 strict / ruff 基线
- [ ] C0.5 docs/index 再生（工作树洁净后）

### C1 功能缺口
- [ ] C1.1 K17 媒体锚点
- [ ] C1.2 K17 摘录/所有者决定
- [ ] C1.3 K17 人格成长文档（或所有者接受不做）
- [ ] C1.4 K13 browser_dialog
- [ ] C1.5 K13 execute_code（或所有者接受不做）
- [ ] C1.6 K15 动机引擎（或接受不做）
- [ ] C1.7 K15 分层人格
- [ ] C1.8 K16 HSV 切点/密度分段
- [ ] C1.9 K16 Laplacian 选帧
- [ ] C1.10 K18 写广度
- [ ] C1.11 K18 主动观看
- [ ] C1.12 K18 分享策略
- [ ] C1.13 K18 直播（或接受不做）
- [ ] C1.14 K14 视觉子代理
- [ ] C1.15 K01 D3 落地

### C2 平台收口
- [ ] C2.1 Linux G3.L2
- [ ] C2.2 Wayland G4
- [ ] C2.3 macOS 权限
- [ ] C2.4 macOS PTY 清理
- [ ] C2.5 macOS 系统服务
- [ ] C2.6 macOS Keychain
- [ ] C2.7 Windows 原生服务 + click 复核
- [ ] C2.8 容器真实构建/运行
- [ ] C2.9 三平台安装矩阵

### C3 真实验收
- [ ] C3.1 干净机器安装/更新/回滚/卸载
- [ ] C3.2 QQ/NapCat
- [ ] C3.3 B 站账号
- [ ] C3.4 模型质量
- [ ] C3.5 安卓设备
- [ ] C3.6 72 小时长跑
- [ ] C3.7 三平台原生

### C4 发布与交接
- [ ] C4.1 版本/CHANGELOG/门禁
- [ ] C4.2 签名/公证（或降级为源码交付）
- [ ] C4.3 升级/回滚演练
- [ ] C4.4 HANDOFF / DEVELOPMENT_STATUS / 索引 / 台账同步

---

## 附录 A：命令速查

```powershell
# 专项（开发期默认）
uv run --no-sync pytest -q -n 0 agent/tests/v2/<分组>/test_<模块>.py

# 只跑上次失败
uv run --no-sync pytest -q -n 0 --lf --last-failed-no-failures=none

# 全量（仅冻结后/基线；默认最多 4 worker）
uv run --no-sync pytest -q

# strict / ruff
.venv\Scripts\python.exe -m pyright -p pyrightconfig.strict.json
uv run --no-sync ruff check agent/src agent/tests

# 医生 / 备份 / 长跑
uv run --no-sync python -m omni.composition.cli --home <实例> doctor
uv run --no-sync python -m omni.system.backup --to <新目录>
uv run --no-sync python agent/scripts/v2_soak.py run --home <实例> --url http://127.0.0.1:8766 --output <新目录> --limits <文件>

# 安装 / 容器
uv run --no-sync python scripts/check_v2_installation.py --wheel <whl> --sha256 <sha> --dependency-bundle <dir> --dependency-sha256 <sha> --base-python <python> --output <新目录>
uv run --no-sync python scripts/check_v2_container.py --image omnioperator:v2-local --output <新目录>

# 平台验收
uv run --no-sync python agent/scripts/accept_linux_desktop.py --output <新目录>
uv run --no-sync python agent/scripts/accept_linux_portal.py --consent-window-share --output <新目录>
uv run --no-sync python agent/scripts/accept_macos_desktop.py --build <bundle> --output <新目录>
uv run --no-sync python agent/scripts/accept_macos_services.py --bundle <bundle> --expect inspection --output <新目录>
uv run --no-sync python agent/scripts/accept_macos_keychain.py --output <新目录>
```

## 附录 B：证据 JSON 模板

```json
{
  "topic": "v2-<topic>",
  "date": "YYYY-MM-DD",
  "source_head": "<40-hex commit>",
  "scope": "一句话范围，含明确不做项",
  "contract": "docs/v2/contracts/<CONTRACT>.md",
  "commands": [
    {"cmd": "uv run --no-sync pytest -q -n 0 <files>", "result": "N passed", "seconds": 0.0}
  ],
  "rollback_proof": "抽掉接线变红/恢复后绿的节点与结果（如适用）",
  "not_executed": ["真实账号", "真实设备", "72h"],
  "whole_v2_complete": false
}
```

---

## 附录 C：与现有台账的映射

| 本文件阶段 | 台账 |
|---|---|
| C0 | 无（新增基线记录） |
| C1 | `v2-agent-core-parity.json` K01–K18 |
| C2 | `v2-development.json` G3/G4/G5 + `v2-windows-candidate.json` release_followups |
| C3 | `v2.json` 待真实验收里程碑 + `v2-windows-candidate.json` acceptance |
| C4 | G7 |
