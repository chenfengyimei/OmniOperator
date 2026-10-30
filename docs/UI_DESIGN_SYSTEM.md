# OmniOperator 前端设计系统与架构规范（UI Design System）

> 本文是**前端视觉与代码的唯一权威规范**（2026-10-30 起）。任何 CSS/JS/HTML 改动先读本文；
> 与本文冲突的旧文档（`UI_REDESIGN_MIGRATION_PLAN.md` 的接线记录、各文件内的临时注释）以本文为准。
> 视觉基调：工作台原型风——`#3ddbd9` 青 + `#7c8cf8` 紫渐变强调、暗色玻璃表面。
> 技术路线：**无构建**。原生 ES 模块 + 分层 CSS，静态目录按请求读盘，改完浏览器刷新即生效。

---

## 1. 设计原则（先记住六条）

1. **一个 token 源**：颜色/字号/间距/圆角/阴影只准从 `ui/tokens.css` 取，任何 CSS 文件禁止定义 `--seed-*`、`--ui-*`、`--shell-*`。
2. **一套组件**：能用 `ui/components.css` 里的组件就不写新样式；新样式先问「这是不是一个新组件」。
3. **导航单源**：全站页面导航数据只在 `ui/shared.js` 的 `NAV_ITEMS` 一处（工作台 rail 的动作按钮除外，见 §7）。
4. **状态四件套**：每个数据区必须有 空态/加载/错误/成功 四种呈现，用统一组件（§6.4）。
5. **字号下限 10px**：小于 10px 的文字全站禁止；正文 13px，信息密度靠层级不靠缩小。
6. **离线自洽**：不引入 CDN、构建器、框架；`?v=` 版本串是缓存防错配闸（§10）。

## 2. 文件结构与职责

```
web_static/
  index.html               工作台骨架：纯标记 + <script type="module">，无内联 JS；内联 CSS 归零
  <page>/index.html         子站骨架（7 个：capabilities/jobs/mcp/terminal/android/storage/project）
  <page>/<page>.css         子站布局样式（只写本页布局，控件一律用组件类）
  <page>/<page>.js          子站逻辑（import ui/app/dom.js 的工具；请求一律 OmniUI.request）
  ui/
    tokens.css              ★唯一 token 源（三层，§3）
    base.css                OmniUI 基础 chrome：通知/对话框/面板切换/徽章/焦点环（不含色板）
    icons.css               Material Icons 内联字体（base64，离线；Phase 7 评估结论：不做子集化——本地盘加载零网络成本，动态注入图标的静态扫描易漏丢字形）
    components.css          ★共享组件库（§6 全部组件类）
    frame.css               ★应用壳：rail、子站顶栏、内容节奏、移动端折叠
    workbench.css           工作台专属：聊天轮次/调用链/输入坞/会话列表/设置中心
    shared.js              OmniUI 库 + NAV_ITEMS + renderRail()（经典 script，子站共用）
    markdown.js             安全 Markdown 渲染器（经典 script）
    app/                    工作台 ES 模块（§9）：main.js 唯一入口
  vendor/                   xterm（不动）
docs/ui-migration/          迁移素材与历史快照（差分基线职责已于 Phase 7b 退役，仅作素材与回滚源）
```

加载顺序（全部页面）：`tokens.css → base.css → icons.css → components.css → frame.css →（工作台）workbench.css /（子站）<page>.css`。
**后三层为页面专属层，公共层之间用「只写自己的选择器」避免级联战争，不靠加载顺序压人。**

## 3. Token 体系（tokens.css，三层）

```css
/* 第 1 层：种子（唯一允许原始色值的地方） */
--seed-accent:#3ddbd9; --seed-accent2:#7c8cf8;
--seed-bg:#070809;     --seed-fg:#eae7e2;

/* 第 2 层：派生（color-mix 计算，组件只准用这层） */
--surface-0（页面底） --surface-1（面板） --surface-2（次面板） --surface-3（浮起）
--surface-accent（强调底） --surface-inset（凹陷底：代码块/输入内衬）
--text-1（主文） --text-2（次文） --text-3（辅助） --text-4（装饰/占位）
--line-1/2/3（边线三级） --line-accent
--ok/--warn/--danger 及各自 -bg/-bd 变体
--grad（135° 强调渐变） --sh-1/2/3（阴影三级）
--font-sans / --font-mono
--r-sm:6px --r-md:8px --r-card:12px --rail-w:54px
--ease-out:cubic-bezier(.2,.8,.2,1)
--fs-body:13px --fs-secondary:12px --fs-small:11px --fs-caption:10.5px

/* 第 3 层：兼容别名（Phase 7 评估结论：长期保留——子站 css 大量引用，
   删除需全量改写子站、零收益高风险；除 tokens.css 外任何文件仍禁止定义这些变量） */
--bg/--panel/--panel2/--border/--text/--dim/--accent/--accent2/--user/--assistant…
--ui-bg/--ui-panel/--ui-border/--ui-muted/--ui-accent/--ui-text…
--shell-bg/--shell-surface…/--shell-accent…
```

规则：
- 强调色**全站唯一**：青 `--seed-accent` + 紫 `--seed-accent2` 渐变 `--grad`。禁止任何页面出现第二种强调色。
- 状态只用 `--ok/--warn/--danger` 三支语义色；「提示」用 accent。
- 半透明表面统一走 `color-mix`（玻璃质感基准）；扁平色块只允许在 tokens 派生层出现。
- 守卫：`tests/test_ui_design_tokens.py` 断言 token 单源 + 字号下限。

### 3.5 分层玻璃（glass）

全站视觉主线是**分层毛玻璃**：底层极光、中层卡片、顶层浮层，三档强度。

新增第 2 层 token（`--glass-*`，18 个 + `--on-accent`）：

| 档 | 用途 | 挂 `backdrop-filter`？ |
|---|---|---|
| `--glass-strong-*` | rail、顶栏、设置中心覆盖层、浮层、弹窗 | ✅ blur 20px + saturate 165% |
| `--glass-mid-*` | 卡片、面板容器、度量卡、工具条 | ❌ 靠「半透明底 + 内高光 + 细边线」表达 |
| `--glass-weak-*` | 列表行、徽章、按钮、页签 | ❌ 同上 |

**硬规则**：
1. **玻璃的颜色成员必须由 `--surface-*` / `--line-*` / `--seed-fg` 经 `color-mix` 派生**，禁止写 `rgba(255,255,255,x)` 白色薄纱——白纱在浅色主题下等于隐形，这是浅色模式瞎掉的根因。
2. **`backdrop-filter` 只准挂在壳层**。Chromium 的 Backdrop Root 语义决定：嵌套玻璃互相截断（内层模糊的是外层已模糊的结果，看不到页面），且滚动容器挂 blur 会每帧重算。单页 `backdrop-filter` 元素 **≤ 8 个**。
3. **凹陷区不走玻璃**：代码块、终端、输出、输入内衬一律用 `--surface-inset` 实色。
4. `blur` 与 `saturate` 必须同档配套，不得出现 `blur(20px) saturate(145%)` 与 `blur(20px) saturate(150%)` 两套值并存（历史遗留，已在 Phase 8 收敛）。
5. **浮层必须走顶层**：`.rail` / `.panel` 的 `backdrop-filter` 会生成层叠上下文并成为 `position:fixed` 后代的包含块，`overflow:hidden` 还会裁掉浮层。因此提示浮层一律挂在 `body` 直管的 `#tip-layer`，下拉与悬浮菜单一律用原生 `<dialog>`。
6. 守卫：`tests/test_ui_glass_tokens.py` 断言 token 单源、三档齐备、无写死 blur、无悬空变量、浅色块不重复定义玻璃。

### 3.6 主题（暗色 / 浅色）

token 三层结构天然支持主题切换：换第 1 层种子，第 2 层派生自动跟随。

- 实际生效值写在 `<html data-theme="dark|light">`；CSS **只认** `[data-theme="light"]`，不写 `@media (prefers-color-scheme)`——避免「跟随系统」与「手动」两套 CSS 分叉。
- `ui/theme.js` 是**同步阻塞的引导脚本**，必须放在所有 `<link>` 之前：否则浅色用户会先闪一下暗色。
- 浅色主题块里**不写任何 `--glass-*`**：玻璃三档的颜色成员都指向 `--surface-*` / `--seed-fg`，这两个在浅色块里已被重定义，玻璃因此自动翻面。若浅色块另写一份玻璃，就会出现「玻璃不跟随主题」的第二真源。
- 浅色下的强调色是**同一色相的压深变体**（`#0e7490` / `#4f46e5`）：原色 `#3ddbd9` 在白底正文上对比度仅约 1.9:1，不可读。


## 4. 排版刻度

| 用途 | 值 | 说明 |
|---|---|---|
| 页标题（子站 h1） | 18px/700 | 顶栏内 |
| 区块标题 | 15px/700 | section-heading h2 |
| 卡片标题 | 13px/680 | h3 / panel-title |
| 正文 | 13px/1.7 | 聊天气泡、表单说明 |
| 次级文本 | 12px | 列表条目主行、按钮 |
| 辅助文本 | 11px | 描述、meta 次行 |
| 注解/徽标 | 10.5px | eyebrow、badge、chip |
| 等宽 meta | ≥10px | id/时间/指纹（下限） |

- **全站字号下限 10px**（含 CSS 里任何 font-size 声明；守卫扫描 <10px 白名单为空才算过）。
- 中文为主；英文 eyebrow 用 10px/650/字距 0.12em，不小于 10px。
- 行高：正文 1.6-1.78；紧凑列表 1.4-1.55。

间距刻度：`4 / 8 / 12 / 16 / 24 / 32`（面板内边距 12-14，卡片 14-20，区块间 16-24）。
动效刻度：hover 120ms、面板切换 180ms、弹窗 240ms，一律 `--ease-out`；`prefers-reduced-motion` 必须全量关闭（frame.css 全局兜底）。

## 5. 应用壳（frame.css）

- **rail（全站左侧图标栏）**：工作台与子站**同一套规则**（同宽、同间距、同激活态：accent 底 + 左渐变条 + 内发光）。工作台 rail 含动作按钮（记忆库/技能库/定时任务/审批队列=页面内跳转），子站 rail 纯链接。窄屏 ≤900px 折叠为顶部横条。
- **顶栏**：工作台=品牌标题+chips+设置中心按钮（品牌标=`ui/brand-mark.svg` 官网同款小鱼图形，全站只在 rail 顶部出现一次，顶栏不再重复图标，也不带副标题）；子站=`← 工作台`返回 + 图标页名 + `LOCAL` 徽章 + 页内动作。子站顶栏结构由 shared.js `addPageChrome` 统一注入样式类。
- **内容节奏**：子站内容区最大宽 1480px 居中、左右 24px；区块 = `.section-heading`（eyebrow+标题+右侧 meta）+ 内容卡。
- **移动端**：390px 契约（test_ui_flows 钉住）——工作台 `.ui-panes` 三面板切换、子站 rail 折顶、触控目标 ≥36px、无横向溢出。
- 响应式断点：`900px`（壳折叠）、`760px`（单列）、`520px`（紧凑）。

## 6. 组件库（components.css）

组件类名是**契约**：JS 与 markup 按类名取样式，改名必须过全量测试。

| 组件 | 类名 | 变体 | 取代的旧实现 |
|---|---|---|---|
| 按钮 | `.btn` | `.primary`（渐变实心）/`.danger`/`.ghost`/`.small` | `.svc-btn .p-btn .tiny-btn .key-btn .header-action .outline .doc-button`（旧名已于 Phase 7b 清零，别名选择器删除、使用面全站归一） |
| 输入 | `.field>label>input/select/textarea` + `.search-field` | — | 三套表单样式合一 |
| 开关 | `.switch`（checkbox 皮肤）+ `.check-row` | — | index.html 两处重复定义合一 |
| 卡片 | `.card`（+`.card-head`） | — | `.hub-card .console-card .storage-panel .cap-card .stat .flow .mcp-item .slot-card` |
| 度量卡 | `.metric-card`（+`.is-on`） | — | shell 的 metric 体系 |
| 列表行 | `.row`（+`.active`） | `.row-tight` | `.cron-row .preset-row .svc-row .file-row .check-row-item` |
| 徽章 | `.ui-badge`（状态徽章，data-state 驱动色） | ok/warn/danger | `.sc-badge .mc-tag` project `.badge` |
| 胶囊 | `.chip`（顶栏元信息） | `.ok` | — |
| 空态 | `.empty`（虚线盒）/ `.empty-rich`（图标+标题+说明+可选动作） | — | **合并 `.ui-empty`/`.rich-empty`** |
| 弹窗 | 原生 `<dialog>` + `.ui-dialog`（OmniUI.ask） | — | 工作台自制 `.modal/.modal-box` 全部淘汰 |
| 通知 | `.ui-notices`（OmniUI.notify） | info/error | alert() 全禁 |
| 标签页 | `.tabs`（3×3 grid 定版）+ `.badge`（真实计数，0 摘除） | — | skin 滚动条方案与 shell grid 冲突的定版 |
| 区块标题 | `.section-heading` + `.eyebrow` | `.compact` | — |
| 提示 | `#tip-layer` + `data-tip` | — | rail/图标提示 |
| 表格 | `.table-wrap>table` | — | project 表格/聊天 md-table 各自保留视觉，结构类统一 |

按钮语义：**主操作每屏最多一个 `.primary`**；危险操作必须 `.danger` 且配确认；次要动作用默认 `.btn`；「刷新/查看」类用 `.ghost`。

## 7. 导航模型（全站唯一）

```js
// ui/shared.js —— 全站页面导航的唯一数据源
NAV_ITEMS = [
  ['/',            'forum',        '对话工作台'],
  ['/capabilities/','fact_check',  '能力总览'],
  ['/jobs/',       'checklist',    '后台任务'],
  ['/mcp/',        'network_check','MCP 服务'],
  ['/android/',    'smartphone',   '安卓控制'],
  ['/project/',    'account_tree', '项目索引'],
  ['/storage/',    'folder',       'Agent 与文件'],
  ['/?settings=1', 'settings',     '设置中心'],
]
```

- **子站 rail**：由 `shared.js renderRail()` 注入渲染（markup 不写 nav，禁止复制粘贴）；`aria-current` 按路径标记。
- **工作台 rail**：markup 保留（含 4 个动作按钮），但其链接项与 NAV_ITEMS 同源同序，样式与子站 rail 同一套 frame.css 规则。
- **跳转语义（写死，不再靠猜）**：
  - 工作台 rail → 工具子站：**新标签**（`_blank`，不顶掉正在进行的聊天）。
  - 子站 ↔ 子站：**同页跳转**（工作台是会话型页面，工具页之间互跳不损失状态）。
  - 设置中心：工作台内全屏覆盖层（hub）；子站入口经 `/?settings=1` 深链打开；storage 兼作 hub 内嵌 iframe（`?embed=1`）。
  - **设置中心侧边栏分组**（`index.html` 的 `.hub-nav`，四组固定）：
    | 分组 | 分区（`data-go`） |
    |---|---|
    | 核心系统 | `sec-context` 上下文系统、`sec-skills` 技能系统、`sec-memory-system` 记忆系统 |
    | 对话与模型 | `sec-persona` 人设、`sec-models` 模型、`sec-memory` 记忆（回合整理与审批） |
    | 平台与通道 | `sec-media`、`sec-browser`、`sec-qq`、`sec-cron` |
    | 系统与扩展 | `sec-storage`、`sec-usage`、`sec-mcp`、`sec-about` |
    规则：三大子系统（上下文/技能/记忆系统）必须同在「核心系统」组，不得散进其它组；
    导航标签 = 卡片 `h3` 标题；记忆系统作为原生分区直接挂载内容，保留设置侧栏，不跳转、不嵌 iframe。
    改动分组或导航目标须同步 `tests/test_ui_contract.py` 的两条守卫。
- 新增页面 = NAV_ITEMS 加一行 + `<page>/` 目录三件套；**不改任何其它导航代码**。

## 8. 四态规范（每个数据区必须完整）

| 态 | 组件 | 文案要求 |
|---|---|---|
| 空 | `.empty-rich`（图标+标题+一句引导，可带动作链接） | 说清「为什么空 + 怎么让它不空」，禁止只写"暂无数据" |
| 加载 | `.ui-empty` 占位文案（「正在读取…」）或 metric-grid 的 pulse 占位 | 永远要有占位，禁止白屏等待 |
| 错误 | 原位 `.empty-rich`（danger 图标）+ `OmniUI.notify(err,'error')` | 显示服务端 detail；**不得回显用户输入的敏感值**（422 契约） |
| 成功 | 局部 `.save-msg.ok` / notify | 说「保存了什么 + 何时生效」，如「要重启对应服务才生效」 |

轮询纪律：列表重渲染前先算 signature（jobs.js 的 dataset.signature 模式）；迟到响应必须校验版本号（selectionVersion 模式），绝不覆盖新选择——这是 test_ui_flows 钉死的行为契约。

## 9. JS 架构规范（无构建 ES 模块）

```
ui/app/
  main.js          入口：import 清单即模块清单；全局输入绑定与初始化顺序唯一编排处
  dom.js           $ / esc / fmtTime / relTime / usd（子站也 import）
  api.js           api(path, opts)：OmniUI.request 包装 + 超时策略单一出口
  state.js         S：currentSession / busy / 版本计数 / drafts / personaName
  modals.js        原生 dialog 开合 + 关闭钩子注册（qr 轮询清理走钩子）
  header.js        顶栏状态：/api/state 聚合 chips/工具清单/上下文用量/费用徽章
  其余按域拆：chat / runs / sessions / tabs / memory / skills / artifacts /
             publications / desktop / approvals / services / rail / tips /
             hub / hub-models / hub-persona / hub-cron / hub-usage / hub-mcp /
             hub-context / hub-skills
```

- **绑定方式（Phase 4 第二批已落地）**：markup 与模板串一律 `data-action="域:动作"`；各域模块自带 `export const actions={}` 注册表，main.js 合并后挂**一个** document 级委托分发器（click/change/input 按类型路由：表单控件上的 click 不分发；checkbox/radio 只认 change、文本输入只认 input——防连发双触发）。**全站禁止内联 `on*=` 属性**（工作台 markup 与 app 模板已清零，守卫钉死）。
- **data-action 参数约定**：参数走 `data-*` 属性（`data-id`/`data-name`/`data-rev`…）；dataset 值一律是字符串，数字/布尔参数必须在 handler 里显式转换（`Number(el.dataset.id)`、`==='true'`），漏转换会让 `x.id===S.currentSession` 这类严格比较静默失效。
- **import 清单闭合**：main.js 的 import 闭包必须覆盖 `ui/app/` 下全部模块——不被引用的模块不会求值，其 window 导出与事件绑定全部落空（契约门禁按文件扫描发现不了 import 图断链）。守卫：`test_app_modules_are_all_reachable_from_entry`。
- **渲染**：innerHTML 模板必须过 `esc()`（dom.js 唯一实现，禁止各文件重复定义）；动态 id 一律走 dataset/查找，不拼字符串选择器。
- **全局**：app 模块不挂 `window.*`（迁移期临时挂、Phase 4 第三批删除）；跨页共享只经 `window.OmniUI`。
- **状态**：会话归属判断永远带版本号（selectionVersion/messagesVersion 模式）；轮询器必须可停止（beforeunload/页面隐藏）。
- **子站**：`'use strict'` + import dom.js/api；project.js 也走 OmniUI.request。
- 安全红线：渲染模型输出前必转义；链接只放行 http(s) 与站内 /（markdown.js 契约）；确认框文案必须展示完整参数与目标。

## 10. `?v=` 缓存纪律（防错配事故重演）

任何 `tokens/base/icons/components/frame/workbench/shared.js/markdown.js` 与子站自产 css/js 改动，必须同步递增引用处的 `?v=` 版本串（统一日期+序号格式 `20261030a`）。守卫：`test_ui_assets_carry_cache_busting_versions`。**素材与产物分离的旧纪律（skin.css 由 docs/ui-migration 生成）自本文生效起由「token 单源 + 守卫」取代**。

`ui/app/` 模块组是**整组原子递增**：任一模块改动，全组 import 说明符与 index.html 入口标签的 `?v=` 一起换新（一次搜索替换）。ES 模块按 URL 确定实例身份，同一文件两个版本串会实例化两份（状态分裂、事件双绑），守卫：`test_app_module_import_versions_are_atomic`。

## 11. 可访问性

- 焦点环：`focus-visible` 全站 accent 2px outline（base.css 统一，禁止局覆盖盖）。
- 键盘：Esc 关 hub/dialog；终端 Ctrl+Shift+T 新建；tab 按钮带 `role=tab + aria-selected`（现状保持）。
- 对比度：10px 文本对比 ≥4.5:1（--text-3 以上级别才允许用于正文内容）。
- 触控：移动端可点击目标 ≥36px。

## 12. 禁止事项（红线）

1. 禁止在任何 CSS 定义 `--seed-*/--ui-*/--shell-*`（守卫钉死）。
2. 禁止出现第二种强调色/自造渐变。
3. 禁止 `alert()/confirm()/prompt()`（用 OmniUI.ask/notify）。
4. 禁止内联 `onclick=`（data-action 委托）。
5. 禁止 font-size <10px。
6. 禁止 CDN/外部字体/构建器引入。
7. 禁止在子站 markup 里写导航（renderRail 单源）。
8. 禁止渲染未经 esc() 的模型/用户输出。
9. 禁止新增自制 modal（一律 `<dialog>`）。
10. 禁止删功能换美观：所有既有能力（9 tab、审批横幅、hub 13 分区、产物/桌面/发布草稿）永久保留。

## 13. 新页面 / 新组件接入 checklist

**新子站页面**：
1. `web_static/<page>/`：index.html（含 `data-ui-page` + `<page>.css` + `<page>.js` 三件套，样式只写布局）
2. NAV_ITEMS 加一行；受影响测试同步（`.ui-global-nav a` 计数断言）
3. 四态齐备 + 390px 无横向溢出（test_ui_flows 参数化用例加路径）
4. `?v=` 串 + 全量 pytest + 4 视口截图

**新组件**：
1. 先查 §6 表——是变体就加变体，是新组件才进 components.css
2. 类名即契约：语义命名（`.tag` 不叫 `.green-pill`），变体用修饰类
3. 用 token 不写色值；过 `test_ui_design_tokens` 字号扫描

## 14. 验收清单（每个 UI 批次交付前必过）

- [ ] `uv run --no-sync pytest -q` 全量绿（含 14 项 Chromium 行为测试）
- [ ] `python scripts/check_ui_contract.py` 无新增违规
- [ ] 8 页 ×（390/760/1024/1440px）截图人工核验（Playwright 实机）
- [ ] 手动冒烟：发消息/切会话/9 tab/审批横幅/设置各分区保存/扫码/调用链折叠/跨页跳转语义
- [ ] `?v=` 已递增；无 <10px 字号；无内联 onclick 新增

---

## 附：迁移对照（历史资产处置）

| 旧资产 | 处置 |
|---|---|
| `ui/skin.css`（素材拼接产物） | Phase 1 重构为 `ui/workbench.css`，来源纪律改为 token 单源 + 守卫 |
| `ui/shell.css` | Phase 1 解构：token 重定义删除；应用壳 → `frame.css`；页面段 → 各子站 css |
| 子站旧 css（jobs/mcp/capabilities/terminal/android.css） | 重写为本页布局样式（现为被覆盖的死代码） |
| `docs/ui-migration/` | 保留为历史素材与回滚源；`baseline-index.html` 的契约差分基线职责已于 Phase 7b 退役（门禁转绝对模式，任何缺失/重复/未注册即失败） |
| `UI_REDESIGN_MIGRATION_PLAN.md` | 顶部标注由本文接管，仅留作历史记录 |
| index.html 内联 `<style>`/`<script>` | Phase 1/4 分别清零（样式入 workbench.css，JS 入 ui/app/） |
