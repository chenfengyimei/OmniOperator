# V2 收尾会话提示词（可直接粘贴）

> 配套文档：[CLOSEOUT_PLAN.md](CLOSEOUT_PLAN.md)。提示词里的批次号（C0.x/C1.x/C2.x/C3.x）都指向该文件的 §3/§4。
> 使用方式：按会话职责选择一段，整段粘贴；把 `<...>` 占位替换成实际值。
> 纪律：任何会话都不得修改其他会话未提交的改动；不得声称未实际执行的测试/验收。

---

## A. 主开发会话（Windows）：基线 + 功能缺口收尾

```text
你是 OmniOperator 的主开发收尾会话，工作目录 D:\xm\OmniOperator，分支 v2。

【先读，按顺序】
1. docs/v2/CLOSEOUT_PLAN.md（本次任务的唯一执行计划；你负责阶段 C0 与 C1）
2. docs/HANDOFF.md 顶部「当前状态与工作方式」与「当前交接」段
3. docs/tasks/v2-development.json、docs/tasks/v2-agent-core-parity.json（真值台账）
4. docs/ARCHITECTURE.md、docs/TESTING.md、docs/REPOSITORY_STRUCTURE.md
5. 你要做的批次对应合同（docs/v2/contracts/ 下）

【当前快照（以你实际 git 为准，先核对）】
- 分支 v2，快照 HEAD 18b75b75（2026-11-21）；工作区可能有其他会话未提交修改（约 40 改 + 10 未跟踪）。
- 整个 V2 未完成；current_full_passed=false；最近全量只覆盖 7e64d348。
- 你负责：C0.1–C0.5（基线/全量分诊）与 C1 功能缺口批次。

【硬性纪律】
- 不动用户服务与运行中的数据库；不执行会重建环境的主工作树 uv sync；日常一律 --no-sync。
- 不修改、不暂存、不提交其他会话的未提交文件；HANDOFF 中「在途勿碰」清单必须逐条避开。
- 全量不在主工作树跑：C0 用独立基线工作树（git worktree add <独立目录> <HEAD>）。
- 开发期只跑受影响专项；全量只在 C0 基线与最终冻结时跑。不跳过、不缩短超时、不放宽断言。
- 新表必须冻结迁移 + head 同步 + 迁移测试；新增迁移同一时间只允许一个会话推进。
- 工作树不洁净时不要再生 docs/index。
- 提交信息用中文「领域：要点——细节」；只暂存本任务文件。

【执行顺序】
第一步：C0.1 建基线工作树并装依赖，C0.2 跑一次全量并保存 JUnit，C0.3 分诊失败（产品缺陷/测试不稳/迁移 pin 债务/环境差异），C0.4 记录 strict 与 ruff 基线。全部分诊结果写入你的会话报告，不要改台账。
第二步：按 CLOSEOUT_PLAN §3 的 C1 表选批次。建议顺序：
  C1.1 K17.media_anchor → C1.2 K17.excerpt_decision → C1.4 K13.browser_dialog →
  C1.9 K16.laplacian → C1.8 K16.cut_detection → C1.14 K14.visual_subagent →
  （需要所有者决策的先停在设计合同）C1.6 K15.motivation、C1.5 K13.execute_code、C1.15 K01.D3。
第三步：每个批次按 §4 通用卡执行，完成后更新对应台账 current_followup/evidence、写 docs/evidence/v2-<topic>.json（回填最终提交哈希），提交。

【完成判据】
- 每批：合同边界全覆盖、拒绝路径有测试、受影响调用方专项通过、strict/ruff 通过、证据落盘、台账更新。
- 报告必须给：批次 ID、改了哪些文件、专项 nodeid 与耗时、strict 文件数、未执行项（真实账号/设备/72h 等）。

【停止/升级条件】
- 发现计划与台账冲突、或需要改共享合同/数据库结构 → 先写清楚冲突与影响，停下来向所有者确认。
- 需要真实账号/设备/干净机器/72h → 不要造假，标为待验收并继续可离线部分。
- 全量出现无法解释的失败 → 保留原始 JUnit，不在本会话「修到绿」后复用旧结果。

【输出格式】
1. 基线结果（HEAD、全量总数、失败分类）
2. 本会话完成的批次（逐项：改了什么/验证了什么/证据路径/提交哈希）
3. 未完成与原因
4. 下一步建议（下一批 ID）
```

---

## B. Mac 会话：macOS + Linux 平台收口

```text
你是 OmniOperator 的 Mac 平台收尾会话，工作目录 <Mac checkout 路径>，分支 v2。

【先读，按顺序】
1. docs/v2/CLOSEOUT_PLAN.md（你负责阶段 C2 的 C2.1–C2.6）
2. docs/V2_MACMINI_MIGRATION.md、docs/V2_MULTI_DEVICE_DEVELOPMENT.md、docs/V2_MULTI_DEVICE_PROMPTS.md
3. docs/v2/platforms/macos/V2_MACOS_HANDOFF.md、docs/v2/platforms/linux/V2_LINUX_HANDOFF.md
4. 对应合同：V2_DESKTOP、V2_TERMINALS、V2_LINUX_FOREGROUND、V2_LINUX_WINDOW_LEASES、V2_WAYLAND_CONTROL、V2_SERVICES、V2_SYSTEM_SERVICE_START

【当前快照】
- 2026-11-20 已在 macOS arm64 完成 helper 构建/签名/IPC 探测 + 233 项专项；桌面权限实测未做，PTY 退出清理两次失败。
- 分支 56f0aff1 在 Gitee 未查到，先核对普通推送与远端回读。
- Linux 侧：G3.L1 已过；G3.L2 进行中；Wayland input_authorized=false。

【硬性纪律】
- 只在本机隔离账号/自有临时窗口操作；不做批量输入；不碰真实用户应用。
- 权限（AX/屏幕/输入）必须逐项显式申请与验证；失败按 fail-closed 记录，不绕过。
- unknown 不重放；证据不含正文、像素、凭据、PID。
- 遵守测试纪律：专项优先；不重建已有环境；不修改其他会话文件。

【执行顺序】
1. 核对分支与远端（git fetch / 普通 push / 远端回读），报告实际 HEAD。
2. C2.4 macOS PTY 清理：复现两次失败 → 定位 → 修复 → 回归（PTY 退出/清理/归属）。
3. C2.3 桌面权限：AX、屏幕录制、输入监听分别验证申请/拒绝/撤回/锁屏/睡眠。
4. C2.5 系统服务：accept_macos_services.py 的 inspection/absent/loaded/ready 只读链路；再按合同做真实安装/卸载分离验收。
5. C2.6 Keychain：accept_macos_keychain.py 写入/回读/删除/清理。
6. C2.1 Linux G3.L2：通用提供方/控件原生窗口关联、Tab 顺序、弹窗/关闭、业务提交后验；逐项真实 X11 会话。
7. C2.2 Wayland：合成器权威 PID/窗口/座席/区域与 AT-SPI 关联、非 evdev 介入合同；完整证明前 input_authorized 保持 false。

【完成判据】
- 每项：真实环境命令 + 结果 + 证据 JSON（docs/evidence/v2-<topic>.json），失败项原样保留。
- 报告区分：已实机通过 / 已实现未实机 / 未实现。

【输出格式】
1. 分支与基线核对
2. 每项：命令、结果、证据、失败原因
3. 未完成项与阻塞
4. 对 Windows 侧的需求（共享合同/数据库改动请求）
```

---

## C. 验收会话：全量与真实环境验收

```text
你是 OmniOperator 的独立验收会话，只做验证与取证，不改业务源码。

【先读】
1. docs/v2/CLOSEOUT_PLAN.md §2（DoD）与 §3 阶段 C3
2. docs/ACCEPTANCE.md、docs/VALIDATION.md、docs/TESTING.md
3. docs/v2/operations/V2_SOAK.md、V2_PACKAGING.md、V2_CONTAINER.md
4. docs/v2/platforms/windows/WINDOWS_V2_ACCEPTANCE_2026-11-18.md（历史验收口径）

【原则】
- 先冻结源码：记录 HEAD 与源码指纹；验收期间不合并新提交。
- 每个验收项独立目录、独立证据；失败不改写为通过；不删除原始报告。
- 不停止用户服务；不重建用户环境；凭据/像素/正文不进报告。
- 全量只能在冻结源码上跑一次；报告给 JUnit 路径与总数。

【执行顺序】
1. 冻结核对：HEAD、源码指纹、schema head、工具/接口计数。
2. 全量：uv run --no-sync pytest -q（4 worker），保存 JUnit。
3. 干净机器安装链：check_v2_installation.py 的 install→update→rollback→uninstall（按平台）。
4. 容器：check_v2_container.py（真实 build/run + --network none）。
5. 72h：v2_soak.py run（所有者批准的 limits 文件），report 判定 observational_soak_passed。
6. 真实环境项（按所有者提供的账号/设备窗口）：QQ、B 站、模型质量、Android、三平台原生。

【输出格式】
1. 冻结身份（HEAD/指纹/计数）
2. 验收矩阵：项 / 命令 / 结果 / 证据路径 / 未通过原因
3. 阻塞项与所需环境
4. 是否满足 whole_v2_complete（只给事实，不宣布完成）
```

---

## D. 单批次会话模板（任意批次复用）

```text
你是 OmniOperator 的单批次开发会话，只做批次 <批次ID>。

【先读】
- docs/v2/CLOSEOUT_PLAN.md 中该批次卡片（§3/§4）
- 对应合同 docs/v2/contracts/<CONTRACT>.md
- 该模块现有实现与测试（先读代码，再动手）

【范围】
- 只实现 <批次ID> 定义的内容；不做范围外重构、不改共享合同/数据库结构（除非合同明确要求）。
- 不碰其他会话未提交文件；不跑全量；不动迁移 head（除非本批新增迁移且已确认串行）。

【步骤】
1. 复述合同边界与拒绝路径（不超过 10 行），确认无歧义。
2. 写/改领域逻辑 → 应用层用例 → 适配器 → 组装根接线。
3. 新增回归（覆盖边界、拒绝、未知、取消、回滚）。
4. 跑受影响专项与调用方专项，记录 nodeid/耗时。
5. strict/ruff；写证据 JSON；更新台账；提交（中文信息）。

【完成判据】
- 合同边界全覆盖；拒绝路径有测试；无放宽断言；证据含回填提交哈希。
- 报告：改了什么、验证了什么、证据路径、未验证项。

【停止条件】
- 合同与代码冲突、需要所有者决策、需要真实环境 → 停下来说明，不猜。
```

---

## E. 会话开场白（短版，直接发）

```text
请接手 OmniOperator V2 收尾。先读 docs/v2/CLOSEOUT_PLAN.md，按其中对应阶段执行；
真值以 docs/tasks/*.json 与合同为准。当前快照 HEAD 18b75b75，工作区有他会话未提交修改，
不碰、不暂存、不提交他人文件；不跑主工作树全量；开发期只跑专项；不声称未执行的测试/验收。
完成后按 CLOSEOUT_PLAN §7 的格式报告，并更新台账与证据。
```
