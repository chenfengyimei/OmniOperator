# MASTER.md —— OmniOperator 全局设计系统

> 本文件是「设计系统入口索引 + 硬约束摘要」。**token 的唯一真源是 `web/ui/tokens.css`**，
> 本文件不复制变量值、也不新增第二份 token 文件；组件与壳层规则仍以
> `docs/v/reference/UI_DESIGN_SYSTEM.md`（权威工程规范）为准，本文件只做多 Agent 共用的索引与决策摘要。

## 1. 唯一来源（Single source of truth）

| 层 | 文件 | 作用 |
| --- | --- | --- |
| 设计令牌 | `web/ui/tokens.css` | 种子色/派生表面/文本/边线/语义色/玻璃三档/圆角/字号/间距/缓动；**唯一允许写色值的文件** |
| 基础 chrome | `web/ui/base.css` | 通知、对话框、焦点环、面板切换、徽章 |
| 图标 | `web/ui/icons.css` + `MaterialIcons.woff2` | 离线 Material Icons 字体 |
| 共享组件 | `web/ui/components.css` | `.btn/.field/.card/.row/.ui-badge/.chip/.empty/.empty-rich/.tabs/.section-heading/.table-wrap` 等 |
| 应用壳 | `web/ui/frame.css` | rail、顶栏、内容节奏、断点、Unified controls、embed 降级 |
| V1 工作台 | `web/ui/workbench.css` | 对话轮次、调用链、输入坞、会话列表、设置中心 |
| V2 工作台 | `web/ui/v2/workbench.css` + `web/ui/v2/v1/*.css` | V2 壳层（V1 样式冻结副本 + V2 覆盖层） |
| 规范正文 | `docs/v/reference/UI_DESIGN_SYSTEM.md` | 分层、玻璃、主题、导航、组件契约、禁止事项、验收清单 |

规则：**任何 CSS 不得定义 `--seed-*` / `--ui-*` / `--shell-*` / `--glass-*`，组件层不得写死颜色/圆角/断点。**
守卫测试：`agent/tests/*/test_ui_design_tokens.py`、`test_ui_glass_tokens.py`、`test_ui_theme.py`、`test_breakpoints_are_only_900_760_520` 等。

## 2. 令牌分组（命名约定，取值见 tokens.css）

- 种子：`--seed-accent` / `--seed-accent2` / `--seed-bg` / `--seed-fg`（浅色块只重写种子与方向相反的项）。
- 表面：`--surface-0..3`、`--surface-accent`、`--surface-inset`、`--terminal-bg`。
- 文本：`--text-1..4`（数字越小越亮/越重）；边线：`--line-1..3`、`--line-accent`。
- 语义：`--ok / --warn / --danger` + 各自 `-bg` / `-bd`。
- 玻璃：`--glass-strong-*`（20px blur，仅壳层）、`--glass-mid-*`（12px 值但默认不挂 blur）、`--glass-weak-*`（6px 值但不挂 blur）。
- 排版：`--font-sans`、`--font-mono`、`--fs-body/secondary/small/caption/title/headline`。
- 尺寸：`--r-sm/md/card/lg/full`、`--rail-w:54px`、`--sp-1..4`（4/8/12/16）。
- 动效：`--ease-out`（cubic-bezier(0.2,0.8,0.2,1)）；时长目前为约定值（120/180/240ms），未 token 化。

## 3. 布局与响应式（硬约束）

- 断点只有 **900px**（壳折叠）、**760px**（单列）、**520px**（紧凑）；`min-width:901/761` 是与 max-width 成对的桌面写法，不算新断点。
- 移动端契约：390px 无横向溢出，触控目标 ≥36px。
- 工作台 V2 壳：`54px rail + 会话栏（196px）+ 阅读列 + 检查器（250px）`；对话模式三栏，非对话页收左栏、隐藏检查器。
- 子站内容区最大宽 1480px 居中、左右 24px（`frame.css`）。
- 滚动容器各自独立（`#messages`、`.settings-content`、`.settings-rail` 等）；不要在滚动容器上加 `backdrop-filter`。

## 4. 组件与状态

- 组件必须复用 `components.css`：按钮变体（`.primary` 每屏最多一个、`.danger` 必须带确认、`.ghost` 用于刷新/查看）、`field` 表单、`card`、`row`、`ui-badge`、`empty`/`empty-rich`、`tabs`、`section-heading`、原生 `<dialog>`。
- 四态齐全（空/加载/错误/成功），文案要求见 `DESIGN.md` §7；禁止 `alert/confirm/prompt`、禁止自制 modal、禁止内联 `on*=`。
- 状态色只用三支语义色；「未知/待核查」必须有独立视觉表达，不得复用成功色。

## 5. 主题

- 三态：`auto`（跟随系统，默认）/ `light` / `dark`，存 `localStorage['omni_theme']`，写 `<html data-theme>`。
- 引导脚本 `web/ui/theme.js` 必须在所有 `<link>` 之前同步执行（防闪帧），且必须是外部文件（便于 `?v=`）。
- 浅色块**不写任何 `--glass-*`**；浅色强调色是同一色相压深变体（`#0e7490` / `#4f46e5`）。

## 6. 渲染与缓存纪律

- `?v=` 版本串是缓存防错配闸：公共 CSS/JS 与子站自产资源改动后递增；`ui/app/` 模块组整组原子递增（V2 侧对应 `ui/v2` 资源组）。
- 渲染模型/用户输出必须 `esc()`；链接只放行 http(s) 与站内 `/`。
- 迟到响应必须校验版本号（selectionVersion / generation / epoch 模式），不得覆盖新选择。

## 7. 新增页面 / 组件接入（本仓库流程）

1. 先查 `docs/v/reference/UI_DESIGN_SYSTEM.md` §6 组件表：是变体就加变体，是新组件才进 `components.css`。
2. 子站页面：`web/<page>/` 三件套 + `ui/sections.js` 注册分区 + 四态 + 390px 无溢出 + `?v=`。
3. 工作台页面（V2）：`web/ui/v2/index.html` 加 `<section class="page">` + `main.js` 的 `PAGE_META` 与切页加载分支 + 独立模块 `ui/v2/<name>.js`（`mount`/`load`/`reset` 约定）。
4. 改完跑：相关 pytest（`uv run --no-sync pytest -q -n 0 agent/tests/v2/...`）、`python scripts/check_ui_contract.py`、
   必要时的 4 视口截图（见 `UI_REVIEW.md`），并按 `AGENTS.md` 更新 `scripts/project_index.py` 索引（若动了源码文件）。

## 8. V2 现状要点（改之前必读）

- V2 是**单页 26 个 `section.page` + 内存切页**，无 URL 路由、无深链、不记忆上次页面；不要假设有 `?settings=1` / `?embed=1`。
- V2 先加载 `ui/v2/v1/{components,frame,workbench}.css`（V1 冻结副本）再叠 `ui/v2/workbench.css`；**V1 壳层改动不会自动进入 V2**。
- 权限与真实状态由后端决定，前端禁用按钮只是减少误操作；不要用界面状态替代服务端真相。
- 详细债务清单与改进顺序见 `docs/v/design/UI_REVIEW.md` §5。
