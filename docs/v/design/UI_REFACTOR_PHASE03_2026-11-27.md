# UI 重构 Phase 3 实施记录：壳层、Design System V3 与页面统一

日期：2026-11-27 ｜ 依据：`UI_AUDIT_2026-11-27.md`、Phase 01/02 实施记录
范围：仅 `web/ui`（V2 前端）；**未改后端 API/数据库/Agent 内核**。
状态：3A 壳层与导航完成；3B Design System V3 完成；3C 已完成四类模板的代表页面统一，其余页面按模板继续迁移（见 §4）。

## 1. 起始状态检查

- 读取 Git 工作区：本阶段前已有 Phase 0–2 未提交改动；其他会话新增了 `agent/tests/v2/http/test_v2_omniweave_campus.py`，**未触碰**。
- 盘点：V2 `workbench.css` 4963 行、`index.html` 21 个 `<h1>`、26 个无标签 rail 图标、非对话页重复 196px 设置 rail、内容区无最大宽度。
- 注意：共享技能目录 `~/.agents/skills` 在本阶段前被外部清理，`impeccable` 命令不可用；改用其已缓存的引擎（`~/.impeccable/bin/0.1.31/impeccable.exe`）完成检测，未重装技能。

## 2. Phase 3A：壳层与导航

| 审计问题 | 处理 |
| --- | --- |
| 26 个无标签图标 | rail 可展开：`#rail-toggle`（底部箭头）切换 compact（54px）/expanded（216px）；展开时用 `aria-label` 渲染文字标签、分组标题可见；选择持久化到 `localStorage['omni_rail']` |
| 重复导航 | 非对话页隐藏 `#settings-rail`（第二套导航退场），唯一导航源 = 左 rail |
| 双 H1 | 20 个内容区 `<h1>` 降级为 `<h2 class="page-section-title">`；页头 `#page-title` 成为唯一 H1；与页头同名的 section 标题自动加 `.is-duplicate-title` 隐藏 |
| 内容宽度失控 | `.settings-content/.page/.page-head` 统一 `--content-max: 1180px` + `--page-pad: 24px` 居中 |
| 页面结构不统一 | 页头（唯一 H1 + 副标题 + 页级操作）统一；设置类页面取消第二栏；三栏对话保持 Sessions(260)/Thread/Inspector(320) |
| 390 基础导航 | ≤900px rail 变顶部横条（图标滚动，无文档横向溢出）；展开态不影响移动布局、标签与 toggle 隐藏 |
| 主会话/Inspector 布局 | 保留；≥1200px 对话三栏改为 260 / 弹性 / 320 |

## 3. Phase 3B：Design System V3

唯一 Token 源仍是 `web/ui/tokens.css`（旧变量全部保留，避免破坏 V1/子站）。新增语义层：

### 3.1 Token 变化（新增，不改旧值）

| 类别 | 新增 Token |
| --- | --- |
| 表面 | `--surface-hover`、`--surface-selected` |
| 文本 | `--text-primary/secondary/muted`（映射 text-1/2/3） |
| 边线 | `--line-divider`、`--line-focus` |
| 品牌/状态 | `--primary/secondary/success/warning/danger-color`、`--info/-bg/-bd`、`--unknown/-bg/-bd`、`--running/-bg/-bd`、`--queued/-bg`、`--paused/-bg`（均含浅色主题变体） |
| 字体 | `--fs-display 20`、`--fs-page-title 18`、`--fs-section-title 14`、`--fs-body-v3 13`、`--fs-secondary-v3 12`、`--fs-meta 12`、`--lh-body-v3 1.65`、`--lh-tight 1.45` |
| 布局 | `--content-max 1180`、`--page-pad 24`、`--rail-w-compact 54`、`--rail-expanded-w 216`、`--inspector-w 320`、`--sessions-w 260`、`--toolbar-h 40`、`--row-h 34`、`--space-section 20` |
| 层级 | `--z-sticky 10`、`--z-drawer 40`、`--z-dialog 70`、`--z-tooltip 90` |

V2 作用域内功能文字下限提升：`--fs-small: 12px`、`--fs-caption: var(--fs-meta)` = 12px；V1 与子站保持原刻度。

### 3.2 视觉规则

- 去掉 40px 两轴网格底纹（原 `body::before` 与 V1 副本 `frame.css`），仅保留两处极光；`mask` 收窄。
- 去掉渐变文字（`.scaf.legend` 标签改为纯色）、重复条纹背景（V1 副本两处 → `background:none`）、无限脉冲（thinking dots / chat-loading 动画移除）、装饰性零偏移 glow（改为 1px 边线环）。
- 保留品牌青/紫仅用于主操作、选中、进度；状态色走语义 token。
- 组件复用：Phase 1 Worker/Approval/Queue/Timeline 全部复用，未重写；新增样式仅补壳层/排版/状态语义。

## 4. Phase 3C：页面迁移（四模板）

| 模板 | 代表页面 | 状态 | 说明 |
| --- | --- | --- | --- |
| T1 对话工作台 | chat | ✅ 完成 | 三栏宽度、rail、唯一 H1、Phase 1/2 组件保持 |
| T2 监控工作台 | logs、capabilities | ✅ 壳层/排版完成，表格化待批次 2 | 内容宽度、标题层级、工具栏对齐统一；行/表格组件后续统一 |
| T3 配置工作台 | settings、memory | ✅ 设置页完成；memory 等按同一规则 | 删除重复设置栏、单列内容宽度、section 标题统一 |
| T4 审计工作台 | audit | ✅ 壳层完成，结构化展示待批次 2 | 当前仍为原始 JSON 呈现；下一步换时间线/表格 |

其余 20 个页面已继承全局壳层、Token、排版与宽度规则（未逐一重写），按模板在后续批次迁移。未删除任何页面、路由或功能。

## 5. 修改文件清单

| 文件 | 变化 |
| --- | --- |
| `web/ui/tokens.css` | 追加 Design System V3 语义/布局/层级 Token（含浅色变体） |
| `web/ui/v2/index.html` | 20 个 h1→h2.page-section-title；新增 `#rail-toggle`；4 个 inspector h4→h3；资源版本 `?v=20261127p3` |
| `web/ui/v2/main.js` | rail 展开/收起与持久化；每页唯一 H1 的重复标题隐藏 |
| `web/ui/v2/workbench.css` | 追加 3A/3B 覆盖层（导航、宽度、排版、h3）；移除网格/渐变文字/脉冲/glow；10px 功能字号提升 |
| `web/ui/v2/v1/{frame,workbench,components}.css` | 清理 V2 所加载副本中的网格、条纹、glow、10px 字号、loading 脉冲 |
| `web/index.html` + 8 子站 + `web/ui/v2/index.html` | 共享 tokens 版本串统一 `20261127p3` |
| `docs/v/design/UI_REFACTOR_PHASE03_2026-11-27.md` | 本文档 |

未触碰：后端、数据库、其他会话新增测试文件。

## 6. 修改前后截图

目录：`output/playwright/phase3-verify/`（17 张）

| 文件 | 内容 |
| --- | --- |
| `p3-00-nav-expanded-dark.png` | 导航展开（标签 + 分组标题） |
| `p3-{chat,settings,logs,audit,capabilities,memory}-1440-{dark,light}.png` | 六页深/浅主题 |
| `p3-settings-{1440,1024,768,390}-dark.png` | 四视口 |
| `phase2-verify/*.png`（参考） | Phase 2 功能态基线 |

对比要点：唯一 H1、无重复设置栏、内容宽度收敛、网格底纹消失、导航标签可展开、390 无横向溢出。

## 7. Playwright 测试结果

`output/playwright/phase3-verify/verify.mjs`：**12/12 通过**

- 唯一 H1（仅 `#page-title`）、导航 8 分组、网格底纹已移除；
- 导航展开：216px、标签可见、分组可见、持久化 `expanded`；
- 1440/1024/768/390 设置页均无横向溢出；
- Phase 1/2 组件在位（Worker 状态条、审批宿主、队列、重试、时间线、6 个 Inspector 页签、Worker 页签）；
- 无页面脚本错误。

回归：`check_frontend.py` 93 个 ES 模块语法通过；`test_v2_navigation_ui.py` 2 passed（390/1024，26 页可达）；`test_v2_ui.py::test_workbench_chat_memory_and_theme` 8 passed（宽/窄 × 深/浅）。

## 8. Impeccable 检测结果

同一引擎（0.1.31）对 V2 index + 实际加载 CSS：

| 指标 | Phase 3 前（审计基线） | Phase 3 后 |
| --- | --- | --- |
| findings 总数 | 271 | **6** |
| undersized-ui-text | 248 | 1 |
| tiny-text | 9 | 0 |
| codex-grid-background | 1 | 0 |
| gradient-text | 1 | 0 |
| dark-glow | 2 | 1 |
| pulsing-dot | 2 | 1 |
| repeating-stripes-gradient | 1 | 1 |
| skipped-heading | 3 | 0 |
| flat-type-hierarchy / nested-cards / broken-image | 1/1/2 | 0/1/2 |

剩余 6 条分类处理（未添加任何 ignore 文件）：

| finding | 处理 | 理由 |
| --- | --- | --- |
| broken-image ×2 | 接受 | `<img>` 由 JS 运行时注入 `src`（截图预览），静态扫描时为空；运行验证无坏图 |
| nested-cards ×1 | 接受 | 页面内 `.empty-rich` 等状态容器位于页面面板内，属于结构性嵌套，不是装饰性卡片墙 |
| undersized-ui-text ×1（"等待会话" 10px） | 待修 | 位于 V1 副本 `.panel-meta` 规则；V2 新样式已覆盖为 12px，检测仍读旧规则文本；批次 2 清理副本后消除 |
| dark-glow ×1 | 待修 | V1 副本中一处 focus/状态环写法，非 V2 新规则；批次 2 统一为 `--line-accent` 环 |
| pulsing-dot ×1 | 待修 | 同上，V1 副本 `chat-loading` 定义残留（V2 已无对应 DOM） |
| repeating-stripes-gradient ×1 | 待修 | 同上，V1 副本装饰性渐变块残留 |

## 9. 功能回归结果

Phase 1/2 功能元素与行为保持：Worker/Ticket 状态条、右栏任务页签、审批宿主、输入队列、重试/续跑说明、执行时间线、6 个 Inspector 页签全部在位；导航可达性 26 页通过；深浅主题切换正常；390 无横向溢出。未改业务逻辑与数据。

## 10. 剩余问题

1. 4 类模板中 T2/T4 仅完成壳层与排版，表格/时间线结构化展示留待批次 2（不一次性重写所有页面）。
2. V1 副本（`web/ui/v2/v1/*.css`）仍携带 4 类历史装饰规则（检测剩余 5 条中的对应项），建议批次 2 收敛为 V2 自有基线。
3. 设置页顶部「实例已连接」条与内容左对齐不一致（宽度未纳入 `--content-max`），批次 2 修正。
4. 其他页面（memory/documents/people/…）尚未逐页精修，仅继承全局规则。
5. 真实数据/真实 Worker 状态下的视觉未回归（隔离实例为空数据）；Phase 5 全量验收覆盖。
