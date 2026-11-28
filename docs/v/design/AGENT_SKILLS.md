# 多 Agent 共享 UI/UX 技能安装记录

安装日期：2026-11-27（Windows 11，DSH 桌面版）
执行者：DSH（本机安装任务）；使用方：Codex、DSH 及任何兼容 Agent Skills 标准的工具。

## 1. 结论

四个 Skill 安装在 **多 Agent 共享目录**：

```
C:\Users\cy\.agents\skills\
├── agent-product-ui-ux\   (24 files, 1.00 MB)
├── ui-ux-pro-max\         (83 files, 3.68 MB)
├── impeccable\            (69 files, 2.23 MB)
└── playwright\            ( 9 files, 0.02 MB)
```

不装用户级 `~/.codex/skills`，不装项目级 `.codex/skills`，不创建符号链接。
本机第三方仓库快照保留在 `sheji/`（该目录已被 Git 忽略），用于离线重装/更新。

## 2. 为什么是共享目录（实测依据）

| Agent | 版本 | 发现的 skill 根 | 证据 |
| --- | --- | --- | --- |
| Codex | codex-cli 0.171.0（`C:\Users\cy\AppData\Roaming\npm`） | `r1 = C:/Users/cy/.agents/skills`（用户级），`r0 = C:/Users/cy/.codex/skills`（用户专属） | `codex debug prompt-input "ping"` 的 `<skills_instructions>` 段列出 Skill roots 与四条 `r1/<name>/SKILL.md` |
| DSH | 当前会话（Desktop 版，`DSH_PROFILE=desktop`） | `~/.agents/skills` 为用户级 skill 根；`<workspace>/.agents/skills` 为项目级根 | 安装后本会话可用技能目录立即出现四个技能；目录内原有 12 个 hyperframes 系列技能也来自该目录 |
| OpenCode（桌面版） | `@opencode-aidesktop`（`%LOCALAPPDATA%\Programs\@opencode-aidesktop`，数据目录 `%APPDATA%\ai.opencode.desktop`）；**CLI 不在 PATH** | 按其内置文档：全局 `~/.config/opencode/skill(s)/<name>/SKILL.md`；**External skills (auto-loaded)：`~/.claude/skills/<name>/SKILL.md`、`~/.agents/skills/<name>/SKILL.md`** | 桌面版 `app.asar` 内字符串：`External skills (auto-loaded) | ~/.claude/skills/..., ~/.agents/skills/...` 与 `Global skills | ~/.config/opencode/skill(s)/...` |

结论：共享目录对 Codex、DSH、OpenCode 都应生效，无需为任一 Agent 建独立副本（OpenCode 为文档级验证，见 §7）。

## 3. 四个技能的来源与本地补丁

| 技能 | 上游仓库 | 快照提交 | 上游 payload 目录 | 本地补丁 |
| --- | --- | --- | --- | --- |
| `agent-product-ui-ux` | https://github.com/asdlem/agent-product-ui-ux | `b9f5f8e` | `skills/agent-product-ui-ux` | 无（内部引用均为相对路径） |
| `ui-ux-pro-max` | https://github.com/nextlevelbuilder/ui-ux-pro-max-skill | `11ca2b3` | `.claude/skills/ui-ux-pro-max`（预构建成品，免 CLI 初始化） | SKILL.md 顶部补一段本机根目录与 `scripts/search.py` 绝对路径说明 |
| `impeccable` | https://github.com/pbakaus/impeccable | `2fc5e14` | `.agents/skills/impeccable`（构建产物；`skill/` 是带占位符的源码，不可直接用） | 无（脚本用 `%~dp0` 定位自身，位置无关） |
| `playwright` | https://github.com/openai/skills（`skills/.curated/playwright`） | `49f948f` | `skills/.curated/playwright` | SKILL.md 的 `PWCLI` 由 `$CODEX_HOME/skills/...` 改为本机共享路径，并补充 PowerShell 等价命令 |

未安装/未启用的部分（有意为之）：

- `impeccable` 的 hooks（`.codex/hooks.json`、`.agents/hooks.json`）与 Live/Apply 高权限路径：未安装、未启用。
  Windows 上 hook 支持不可靠，且 Live 的 Apply 会 spawn `codex exec --dangerously-bypass-approvals-and-sandbox`。
- `impeccable` 的 Rust 引擎：**已按需安装并验证**。2026-11-27 用其自带 `scripts/impeccable.cmd engine-probe` 触发下载，
  按 `scripts/engine-checksums.txt` 里 `0.1.31 impeccable-windows-x64.exe f6606bbf…ff47` 做 sha256 校验后落盘到
  `C:\Users\cy\.impeccable\bin\0.1.31\impeccable.exe`（24,753,032 B），引擎自报 `impeccable-engine 0.1.31`。
  同时把匿名遥测持久关闭：`C:\Users\cy\.impeccable\config.json` 为 `{"telemetry": false}`。
  未启用任何需登录的能力（login/health/roll 等）。
- `ui-ux-pro-max` 的 6 个配套技能（banner-design/brand/design/design-system/slides/ui-styling）：未安装，避免上下文膨胀；主技能可独立工作。
- 未使用 `npx skills add`、`npx impeccable install`、`npx ui-ux-pro-max-cli init` 等安装器（它们会写 `.agents/skills` 之外的位置、默认装 hook 或按 `--ai codex` 生成固定路径）。

## 4. 冲突与去重

- 共享目录原有 12 个技能（faceless-explainer、general-video、hyperframes 系列 9 个、media-use），与本次四个名称无重叠。
- `~/.codex/skills` 原有 mc-plan-development、mc-plan-orchestrator、vibe-knowledge-video，无同名。
- 安装过程中曾在项目内临时建过 `.codex\skills\`（探查用），已删除；仓库不保留重复副本。
- 四个技能名称互不冲突，且未与内置系统技能（dsh-doc、office、skill-installer 等）重名。

## 5. 安装/更新方式（幂等）

```powershell
# 安装缺失项（已存在则跳过，不覆盖自定义改动）
pwsh -File scripts/install-uiux-skills.ps1

# 查看将要做什么
pwsh -File scripts/install-uiux-skills.ps1 -DryRun

# 覆盖重装（会在复制后重新打本机路径补丁）
pwsh -File scripts/install-uiux-skills.ps1 -Force
```

脚本来源是 `sheji/` 下的离线克隆，不联网、不执行第三方脚本、不建链接。
上游更新流程：更新 `sheji/` 快照（`git -C sheji/<repo> pull --ff-only`，playwright 按 `sheji/README.md` 的稀疏克隆命令重取）
→ 运行 `-Force` → 重跑 §6 验收。

## 6. 验收结果（2026-11-27 本机实测）

| 检查项 | Codex | DSH | OpenCode |
| --- | --- | --- | --- |
| 发现四个 Skill | ✅ `codex debug prompt-input` 列出 4 条，路径均为 `r1/...` | ✅ 会话技能目录出现四个技能 | ⚠️ 内置文档声明自动加载 `~/.agents/skills`；桌面版当前未运行，无 CLI 可跑，运行时未验证 |
| 读取 SKILL.md | ✅ 只读 `codex exec` 实读并回显 impeccable / ui-ux-pro-max 的 frontmatter | ✅ 直接读取四个 SKILL.md 全文/片段 | ⬜ 未验证（同上） |
| 读取附属文件 | ✅ 只读 exec 实读 `references/interaction-guide.md`、`reference/critique.md`、`references/cli.md`、`scripts/playwright_cli.sh` 并回显首行 | ✅ 本机可直接读取 | ⬜ |
| 调用设计知识 | ✅ 只读 exec 实跑 `search.py … --design-system`，回显命中 TARGET 与 PATTERN 段 | ✅ 本机实跑，输出完整设计系统 | ⬜ |
| 调用审美评审 | ✅ 只读 exec 实跑 `impeccable.cmd engine-probe` → `impeccable-engine 0.1.31` | ✅ 引擎已装；实跑 `detect --json` 在临时 HTML 上报出 WCAG 低对比度问题；`--help` 命令分发正常 | ⬜ |
| 浏览器截图 | ⚠️ CLI 本身可用（宿主实测截图成功）；**Codex 沙箱内启动浏览器失败**（`Session closed`），见 §6.4 | ✅ 实拍 PNG（Edge 通道；本地文件与 example.com 均成功） | ⬜ 未验证（需其内置浏览器或本地 CLI） |

### 6.1 验收命令

```powershell
# Codex：技能发现（在仓库根执行，输出里应有 r1/<name>/SKILL.md）
codex debug prompt-input "ping" | Select-String "agent-product-ui-ux|ui-ux-pro-max|impeccable|playwright"

# DSH：重开会话或在会话技能目录中确认；也可直接读文件
Test-Path C:\Users\cy\.agents\skills\impeccable\SKILL.md

# 设计知识（任意 Agent 均可执行）
python C:\Users\cy\.agents\skills\ui-ux-pro-max\scripts\search.py "agent workbench dark glass" --design-system -n 3

# 浏览器截图（本机无 Chrome，用 Edge 通道；或用 `playwright-cli install-browser chrome` 后省略 --browser）
npx --yes --package @playwright/cli playwright-cli open https://example.com --browser msedge
npx --yes --package @playwright/cli playwright-cli screenshot
npx --yes --package @playwright/cli playwright-cli close
```

### 6.2 Codex 端到端证据（2026-11-27）

在 `%TEMP%` 下以只读沙箱运行 Codex，使其读取并执行共享技能内容，成功返回：

- `C:\Users\cy\.agents\skills\impeccable\SKILL.md` 的 frontmatter（name/description 原文回显）；
- `C:\Users\cy\.agents\skills\ui-ux-pro-max\SKILL.md` 的 `name: ui-ux-pro-max`；
- 附属文件 `references/interaction-guide.md`、`reference/critique.md`、`references/cli.md`、`scripts/playwright_cli.sh`
  的首行被原样回显（证明 Codex 能按 skill 指示读取 references/scripts）；
- 实跑 `impeccable.cmd engine-probe` → `impeccable-engine 0.1.31`；
- 实跑 `search.py "agent workbench dark glass" --design-system -n 1` → 输出 TARGET/PATTERN 设计系统；
- 附带发现：Codex 沙箱里 `py -B` 报 "No installed Python found!"，模型改用
  `C:\Users\cy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` 后成功——
  在 Codex 里调用 `search.py` 时要留意 Python 来源。

命令模板：

```powershell
codex exec -C $env:TEMP -s read-only --skip-git-repo-check --color never `
  -c model_reasoning_effort="low" `
  -o "$env:TEMP\codex-skill-verify.txt" `
  "读取 C:\Users\cy\.agents\skills\impeccable\SKILL.md 的前 3 行并原样输出；最后输出 DONE"
```

注意：`codex exec` 走真实模型调用，单次可能耗时数分钟；只用于抽查，不作为日常门禁。

### 6.3 DSH 侧实跑证据（2026-11-27）

- 设计知识：`python C:\Users\cy\.agents\skills\ui-ux-pro-max\scripts\search.py "agent workbench dark glass" --design-system -n 3`
  输出完整 PATTERN/STYLE/COLORS 段。
- 审美评审：`impeccable.cmd --help` 正常列出 detect/install/link/context/taste/foundations 等命令；
  `engine-probe` 返回 `impeccable-engine 0.1.31`；对临时 HTML 跑 `detect --json` 报出
  `low-contrast`（WCAG AA）等问题，证明检测引擎可实际工作（临时文件已删除，未扫描项目私有内容）。
- 隐私：`impeccable telemetry status` → `off ("telemetry": false in C:/Users/cy/.impeccable/config.json)`。

### 6.4 浏览器截图的三条边界（2026-11-27 实测）

1. **宿主/DSH 侧可用**：`playwright-cli open --browser msedge` + `goto` + `screenshot` 在本机成功；
   但 `playwright-cli` 禁止 `file:` 协议（`Access to "file:" protocol is blocked`），所以截图目标是
   `http://127.0.0.1:8766` 这类本地服务或 `data:`/`about:blank`，不能直接截本地 HTML 文件。
2. **Codex 沙箱内失败**：在 `-s workspace-write` + `sandbox_workspace_write.network_access=true` 下，
   Codex 能创建 HTML 与 `output/playwright/`，但 `playwright-cli open` 返回 `Session closed`，未生成 PNG——
   浏览器守护进程在 Codex 沙箱里起不来。**这不代表技能缺失**，而是 Codex 执行沙箱限制；
   不要为此关闭 Codex 审批（`--dangerously-bypass-approvals-and-sandbox` 属于禁止项）。
   正确做法：由宿主/DSH 执行截图，或由用户明确批准更宽的 Codex 沙箱后再跑。
3. 本机没有系统 Chrome：必须 `--browser msedge`（Edge 已装），或先 `playwright-cli install-browser chrome`。

因此 OmniOperator 的截图验收流程建议固定为：`uv run --no-sync omni-web` 起服务 → DSH 或宿主终端用
playwright-cli 截 `http://127.0.0.1:8766`（深浅双态、4 视口），截图存 `output/` 后删除或留本机。

## 7. 未验证项与已知限制

1. **OpenCode 只做静态验证**：桌面版已安装（`@opencode-aidesktop`），但其内置文档声明会自动加载
   `~/.claude/skills` 与 `~/.agents/skills`，而本机 `opencode` CLI 不在 PATH、桌面版当前未运行，
   所以没有运行时证据。若打开后未出现四个技能，把 `C:\Users\cy\.agents\skills\{agent-product-ui-ux,ui-ux-pro-max,impeccable,playwright}`
   复制到 `C:\Users\cy\.config\opencode\skills\`（该目录已存在并存放 12 个 hyperframes 技能），
   不要建符号链接，也不要重复安装到两处以免同名技能双份注入。
2. **impeccable 引擎已装，但完整评审未跑**：引擎 0.1.31 已按校验值安装，`detect` 静态扫描实测可用；
   `critique/audit/polish` 等是 SKILL.md 里的 agent 流程，真正执行会在目标项目写 `.impeccable/` 产物、
   且多数需要运行中的界面。本次只做最小实跑（engine-probe + detect），未在 OmniOperator 上调完整评审。
   引擎已缓存，后续无需再联网下载；若要换位置用 `IMPECCABLE_BIN`。
3. **Playwright 默认通道缺 Chrome**：`--browser` 默认找系统 Chrome（本机未装在该路径）；实测 `--browser msedge` 正常。
4. **`npx` 走 npmmirror 镜像**（`registry=https://registry.npmmirror.com`）；`@playwright/cli` 0.1.25 可用。
5. **DSH 技能目录会随目录变化热更新**：本次安装后当前会话立即看到四技能，无需重启 DSH。
6. **Codex 沙箱的 Python/网络差异**：Codex 只读沙箱里 `py` 不可用（"No installed Python found!"），需改用其内置运行时
   `C:\Users\cy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`；默认沙箱还限制网络，
   跑 Playwright/下载类命令需要 `-s workspace-write -c sandbox_workspace_write.network_access=true`。

## 8. 与其他文档的关系

- 调度规则：根 `AGENTS.md` 的「UI/UX Skill 调度」一节。
- 设计系统与视觉约束：`docs/v/design/MASTER.md`（token 唯一源仍是 `web/ui/tokens.css`）。
- 现有前端规范（更权威的工程约束）：`docs/v/reference/UI_DESIGN_SYSTEM.md`、`docs/v/reference/UI_QA.md`。
- 验收标准：`docs/v/design/UI_REVIEW.md`。
