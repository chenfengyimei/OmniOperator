# V2 工作台 UI 开发交接提示词

> 交给下一个开发 Agent 的完整上下文与任务书。先读本文，再动手；所有结论以当前源码和测试为准。

## 1. 你的任务

把 OmniOperator **V2 的 Web 工作台**（`web/ui/v2/`）继续做成"V1 的成熟度 + Hermes 的排版/配色质感"：

- 保留现有功能与测试契约（23 个页面、7 组导航、所有 id 钩子）；
- 按本文第 8 节的顺序补齐剩余 UI；
- 每完成一块：截图自检 → 跑相关测试 → 只提交自己改的文件。

**不要重写架构，不要动 Python 业务代码，不要改 V1 的 `web/index.html`。**

---

## 2. 仓库与运行环境

| 项 | 值 |
|---|---|
| 工作区 | `D:\xm\OmniOperator`（Windows / PowerShell） |
| 分支 | `v2`（远端 gitee，已推送） |
| UI 源码 | `web/ui/v2/`（`index.html` / `main.js` / `workbench.css` / 约 30 个页面模块） |
| 设计 token | `web/ui/tokens.css`（唯一颜色/字号/圆角来源）、`web/ui/base.css` |
| 图标 | `web/ui/icons.css` + `web/ui/MaterialIcons.woff2`（字体已外置，勿改回 data URI，CSP 会拦） |
| V1 参照 | `web/index.html` + `web/ui/{frame,components,workbench}.css`（**只读参考，不要 import 到 V2**） |
| Hermes 参照 | `xuexi/hermes-agent/web/src/index.css`（**只学设计语言**：LENS_0 配色、15px 字号、0.5rem 圆角；不要复制其代码/资源） |

**启动 V2 实例**（全新目录或已有目录均可）：

```powershell
uv run --no-sync python -m omni.composition.cli --home D:\xm\omni-v2-verify serve
# 默认 http://127.0.0.1:8766 ；令牌：D:\xm\omni-v2-verify\run\owner.token
```

**V1 对照实例**（看"成熟形态"用）：`http://127.0.0.1:8000`，配对链接写在 `data/run/pair_url.txt`。

---

## 3. 不可破坏的契约（测试即规格）

以下钩子被 `agent/tests/test_v2_*.py` 直接依赖，**改名/删除会导致大面积失败**：

```
#token #connect #agents #health #theme
#sessions（ul，子元素 li[role=button]，内含 .s-title/.s-id）
#messages #send（form） #message（textarea） #cancel #pin-run
#open-settings #back-to-chat
nav[aria-label="工作台"][inert]    ← 原文必须保留，守卫用正则匹配这串
nav p.nav-group                   ← 必须 7 个，文本固定
nav button[data-page]             ← 必须 23 个，可访问名 = 页面中文名（aria-label）
section.page[id]                  ← 23 个页面分区，id 与 data-page 一一对应
```

- 页面切换只允许通过 `[data-page]` 按钮点击触发；`test_v2_navigation_ui.py` 会在 390/1024 两个宽度逐项点击并断言目标分区可见。
- 任何新增可见按钮，如果文案与既有页面名相同（如"设置"），必须换名或用 `title/aria-label` 区分，避免 `get_by_role(..., exact=True)` 命中两个元素。
- 资源版本串：`index.html` 与所有模块 import 当前为 `?v=20261120v165`。**任何 HTML/CSS/JS 改动都要整体升级版本号**（v58、v59…），并同步：
  - `agent/tests/test_v2_mcp_ui.py` 里的 `assert "main.js?v=..." in page.content()`
  - `docs/history/v2/V2_AI_CONTINUATION_PROMPT.md` 的版本说明
  - 本文第 3 节这里的版本号
  否则浏览器缓存会让人看到旧界面（已经被这个坑坑过一次）。

---

## 4. 当前 UI 状态（已完成）

Portal共享的系统同意与业务授权审计独立完成。`authorization_pending=true` 时显示“正在确认共享授权，请稍后查询共享状态”，保存按钮保持不可用；审计完成后由所有者点击查询更新，不增加后台轮询或自动截图。该字段仅描述确认状态，不能替代 `authorized`、截图许可或来源检查；审计失败继续显示不可用并回收辅助进程。

**直接复用 V1 界面**（2026-11-13/14，资源版本 `20261120v165`）：按所有者要求，不再自创视觉，
把 V1 的 `components.css` / `frame.css` / `workbench.css` **原样复制**到 `web/ui/v2/v1/`。
**声明内容一字未改**，只做三类必要映射：

1. 页面标识 `data-ui-page="workbench"` → `"workbench-v2"`
2. 资源路径 `/ui/` → `/assets/ui/`
3. **元素标识对齐 V2 结构**：`#input` → `#message`（输入框）、`#send` → `#send-btn`（发送键）

第 3 项是本轮最关键的修复，必须做：V1 的 `#send` 是**按钮**，V2 的 `#send` 是**form 容器**。
不映射会造成四个连锁症状——按钮渐变涂满整个输入区、输入框露出浏览器白底黑字并溢出容器、
真正的发送键反而完全没有样式（灰底凸边框）、容器被按钮的 27px 高度压扁。
另为 `.rail` 追加等效属性选择器别名（V2 的 `nav` 不能带 class，守卫正则依赖标签原文）。
V1 原文件保持只读，未被修改。

**本机启动即用（自动连接）**：V1 靠配对 Cookie 自动携带凭证（`credentials:'same-origin'`），
起服务就能用；V2 换成所有者令牌 + `Authorization: Bearer` 后，前端一度**没有任何获取通道**，
使用者每次都要去实例目录翻 `run/owner.token` 手工粘贴——这是相对 V1 的体验退化，已修复。

- 后端 `GET /api/v1/owner-token/local`（`src/omni/adapters/http.py`）。**必须注册在 `app` 上**，
  不能挂到 `router`——那个 router 整体带 `Depends(authenticate)`，会被它要引导的鉴权自己拦住。
- 三重闸门，缺一不可：① 装配时提供 `local_owner_token` 回调，未提供直接 503；
  ② 客户端 IP 必须是回环，且带 `X-Forwarded-For`/`X-Real-IP`/`Forwarded` 一律 403；
  ③ 有 `Origin` 且不等于 `base_url` 一律 403。**其余 API 鉴权完全不变**。
- 前端 `bootstrapLocal()` **先读公开的 `/openapi.json` 探测能力**，确认存在该路径才发引导请求——直接请求会让没有该端点的旧实例在控制台留 404。探测后：新后端自动连接、旧后端静默回退，两者零失败请求。
- 取到的令牌**只赋内存变量，
  绝不写 `localStorage`/`sessionStorage`**（项目既有安全约定）。
- **手动优先**：用户一旦 `input`/`focus`/`pointerdown` 令牌框或提交表单，
  `manualConnectStarted` 置真，自动连接在请求前后各检查一次并让位。
  缺这道让位会清空用户刚填的令牌、把按钮改成「重新连接」，曾因此让 4 个既有测试失败。
- v128 补齐初始化期间的让位：自动流程已开始加载 Agent 时，手动输入也立即取消旧请求并保留草稿；连接卡仅在初始化结束后收起。连接与 Agent 初始化分别核对当前归属，旧流程退出不得解除新流程的导航和发送屏障。切后台只中断请求，没有新流程接管时必须恢复自己拥有的交互屏障，不能只比较请求代次而永久锁住导航。
- 连接成功后连接卡收成 38px 状态条（绿点 + 实例已连接 + 重新连接），
  点「重新连接」展开令牌框，刷新自动还原完整表单。
- 契约测试：`test_v2_owner_token_ui.py::test_local_bootstrap_connects_without_pasting_and_stays_loopback_only`
  与 `::test_manual_token_entry_wins_over_local_bootstrap`；初始化延迟回归见 `::test_manual_input_during_bootstrap_initialization_is_preserved`（三视口双主题）、`::test_superseded_bootstrap_cannot_unlock_manual_connection` 及 `::test_interrupted_agent_selection_releases_its_own_controls`。

V2 结构已改用 V1 类名，让复制来的样式真正命中：

- `nav[aria-label="工作台"]` + `.rail-brand` + `.rail-btn`（分组标题视觉隐藏但保留在 DOM，守卫依赖）
- `aside.workbench-side` + `.panel.session-panel` / `.service-panel` / `.context-panel`
- `#chat > #messages` + `#composer > form.dock > textarea#message + .dock-bar`
- `.dock-bar` 分两层：`.dock-picks`（两个 `.pick-group`：档位+技能、图片+截图）+ `.dock-actions`（键位提示、取消、发送）
- 右栏 `.panel > .tabs`（**5 列** grid：运行/待办/技能/产物/桌面，选中用 `.active`）+ `.tab-body`
- 助手 `.a-head > .a-avatar(svg) + .a-name + .a-meta + .spacer + .copy-msg + .msg-actions`；用户 `.bubble` + `.turn-foot > .ts + .msg-actions`
- 调用链 `.chain-slot > .scaffolds.open > .scaf-toggle + .scaf(+ .scaf-out)`，**挂在助手正文上方**，默认展开
- 消息操作是头像行/时间行右侧的 `.act` 图标，悬停显现；名称走 `aria-label`（测试按可访问名点击）

`web/ui/v2/workbench.css` 已降级为**桥接层**，只补 V1 没有的部分（23 个 `section.page`、页头、
连接卡与 Agent 选择、设置覆盖层、右栏数据行、运行技术详情、窄屏抽屉），不再定义面板/气泡/dock/rail/tabs 外观。

**踩过的坑（勿重犯）**：

1. 不要用 CSS 嵌套（`body[...] { 原样V1规则 }`）包裹副本——实测 664 条规则只解析出 1 条，样式几乎全失效。原样复制即可。
2. `.page { display:flex }` 会压过 `[hidden]`，导致 21 个隐藏页全部显示、把 `#chat` 挤成 2px、输入框被顶出视口。桥接层里 `[hidden]` 必须 `display:none !important`。
3. `base.css` 的 `body[data-ui-page]:not([data-ui-page="workbench"]):not([data-ui-page="project"])` 会给 V2 加 22px 内边距与 1500px 限宽，必须用**同等特异性**覆盖。
4. V1 用 `body { padding-left: var(--rail-w) }` 给 fixed rail 让位；V2 改用 grid 骨架，别再叠 margin-left。
5. 设置包裹层 `.settings-layout` / `.settings-content` 在对话模式必须成为满高容器（grid 单行），否则高度链断裂、dock 贴不到底。
6. **副本里做别名替换不要盲替换**：曾把注释里的 `.rail` 一起改掉，连带把导航阴影规则整条串坏（规则数 663→664 才暴露）。改完要 diff 原件逐条核对。
7. **工具条不要单层 flex-wrap**：控件总宽 914px 装进 770px 容器会散成 4–7 行、发送键位置飘忽。必须拆 `.dock-picks` / `.dock-actions` 两层，并按语义分 `.pick-group`。分组竖线作为 flex 项在两组分行时会自己占一行，改用组间距表达。
8. **右栏页签必须 `flex:0 0 auto`**：窄屏抽屉里它被压到 23px（内容需 44px），页签实际位置超出容器，点击会落到下方内容层上（`elementFromPoint` 命中 `.run-inspector`）。
9. **不要用负外边距抵消容器留白**：会让内容层向上溢出盖住页签。改为容器 `padding:0`。
10. **窄屏抽屉断点要与页头入口一致（都是 900）**：只把布局改到 900 而入口留在 520，会让 760 宽下按钮宽高为 0、面板打不开；反之右栏仍按桌面常驻会把对话挤到 65px。
11. **浅色主题只覆盖了 `--text-3/4`**，`--text-1/2` 沿用深色定义，所以浅色下 `--text-3` 反而比 `--text-2` 更淡。运行面板说明文字实测 `--text-4`=2.76、`--text-3`=3.07、`--text-2`=3.35 全部低于 4.5，只有 `--text-1` 达标。
12. **技术详情不要同时出现在对话区和右栏**：对话内的调用链只留真实工具步骤，档位/模型计量归右栏「运行」页签。
13. **原样复制 V1 CSS 不等于整份照搬**：`frame.css` 末尾的「Embed 降级层」有全站唯一性契约（`test_ui_embed_layer::test_embed_rules_live_only_in_frame_css`：通用 embed 降级规则只准 `ui/frame.css` 一处）。抄进副本即红。该层服务于被 iframe 内嵌的子页，V2 是顶层页面用不到，副本里**有意不复制**。复制前先扫一遍 `agent/tests/test_ui_*.py` 里的「只准/全站只有/only_in」类契约。
14. **切页入口不要用裸 `[data-page="x"]` 选择器**：`body` 也带 `data-page`，`querySelector` 会先匹配到 body，点它不触发切页。必须写成 `nav[aria-label="工作台"] [data-page="x"]`。
15. **程序化跳转要走导航同一条路径**：只改 `page` 变量与 `.page.hidden` 会漏掉页头标题、导航选中态、`body.dataset.page`，出现半切换状态。直接 `.click()` 对应导航按钮。
16. **剪贴板写入必须 try/catch**：`navigator.clipboard.writeText` 在非安全上下文、用户拒绝、无头环境会 reject，不兜底就是未捕获异常。兜底做法：选中正文 + 明确提示按 Ctrl+C。
17. **分区页表单系统作用域必须排除 #chat**：对话页也是 section.page，.page 作用域的表单规则（width:100%/min-height:32px/label display:grid）会泄漏进 dock 工具条——技能多选被撑成流内高黑块、图标与控件拆成上下两行、文件选择露原生控件（所有者截图取证）。统一用 .page:not(#chat)；.page>form、.file-picker、窄屏按钮尺寸同样要排除。
18. **设置层布局必须两种模式都定义**：`.settings-layout` / `.settings-content` 此前只在 `.chat-mode` 下有布局，进设置（非 chat-mode）后完全缺失——工作区还是 `position:fixed` 且 `left` 写死 `rail+230+24`，与 grid 骨架（左栏实际 196px）错位，整页被拉长到 3395px、页头与分类导航滚走。正确做法：非对话模式工作区 `position:static` + flex 列满高、`settings-content` 独立内滚、分类导航吸顶。**不要再用 fixed 覆盖层**。
19. **守卫三件套别踩**：注释里写字面色值（`#767676`）会被颜色守卫扫到；`var(--r-xs, 3px)` 的 `--r-xs` 未登记算悬空变量；`blur(10px)` 字面模糊值只准 `tokens.css` 定义，组件层用 `blur(var(--glass-mid-blur))`。
20. **非对话模式不要 `display:none` 左栏**：`#agents` 在左栏里，`two_agents` 草稿隔离测试会在**任意页面** `select_option(#agents)`，隐藏左栏即 14 项回归。~~宽屏保留左栏~~ **2026-11-17 所有者要求改全屏**：左栏（连接卡/会话/服务/上下文）属于会话界面，非对话页一律 display:contents 让位、内容占满整宽（.page>* 不再限 980px），宽窄屏同一方案；已连接态 #connect 有 body[data-connected] 更高特异性 display:flex，须显式覆盖隐藏。窄屏左栏 `display:contents` 不占列、其余子级隐藏、`#agents` 用 `grid-row:1` 排成顶部独立行、工作区 `grid-row:2`。**`display:none` 会连带隐藏子级，必须用 `display:contents`**。**也不要给 `#agents` 用 `position:fixed` 浮在内容上**——它会拦截 portal 等页面按钮的点击（曾致回归），顶栏行内布局才不遮挡。
21. **给 `model_options` 加列会波及全部冻结基线迁移测试**：仓库读回走 `select(model_options)`（全列），当前 `ModelOptions` 载荷始终带 `extra_headers`，所以 v13–v37 各冻结基线在用当前仓库种子时会因缺列报错。统一做法：迁移脚本 `v0038` 的 `upgrade()` **幂等**（先 `PRAGMA table_info` 判断列是否存在再加），并在每个冻结基线 `command.upgrade(...)` 之后调用 `v2_configuration_helpers.add_model_headers_column(connection)` 补列——**不要紧跟 `connection.commit()`**（多数测试在 `engine.begin()` 上下文内，显式 commit 会关闭事务报 `Can't operate on closed transaction`；SQLite DDL 本身自动提交）。补列导入须放在 `from __future__` **之后**。

**量取方法教训（虚警都由此而来）**：

- **不要用 `getComputedStyle` 的字符串自己解析颜色**：`oklab()` / `color(srgb …)` / `color-mix()` 解析不了就会把浅底当白底，曾把浅色输入框算成 1.63（真实像素 15.62）。判定对比度请**直接采样渲染后的像素**（见 `temp/build/_ct_real.py`）。
- **`element.focus()` 不触发 `:focus-visible`**：用它判定焦点可见性会全部误报不可见。必须用真实 `keyboard.press("Tab")`。
- **异步挂载的中间态不是重复渲染**：调用链数量在等待期间会从 5 涨到 10，判重要按「每条消息的 `.chain-slot` 数 ≤1」和「同角色同 runId 是否重复」，而不是看总数变化。
- **选择器先读源码再写**：`#objects-list`（实际 `#object-list`）、`[data-settings="persona"]`（实际 `[data-settings-page="settings"]`）都是猜错导致的假失败。
- **整页缩图里小字号中文的「删除线」是摩尔纹**：设置页标签/按钮曾看似被横线穿过，1:1 裁剪确认文字干净、全祖先链 `text-decoration:none`。判定文字装饰请用原始分辨率裁剪或计算样式，不要看缩略整页图。
- **`backdrop-filter` 会让 Playwright 稳定性判定误报元素不可见**：分类导航吸顶加玻璃模糊后 `.click()` 超时，但元素实际可见可点。核查脚本改用 DOM `element.click()` 更可靠。
- **像素对比度按「固定阈值选离背景最远色」会误选装饰色**：小字号等宽文字（如请求头 textarea，10.5px mono）笔画像素仅 40~50 个，被高频的边框/抗锯齿色（数百~上千像素）压过。曾把 dark 边框 (30,31,31)、light 抗锯齿 (230,230,231) 当文字，算出 1.19/1.24 虚警；真实文字色 dark (225,222,217) 对比 14.7、light (28,32,41) 对比 16.17。正确判据：取**出现频次前 20 的非背景色**中离背景最远者（文字笔画色必在其中）。采样前先 `blur()` 避开焦点环青色边缘。
- **双主题共享同一实例 home 会撞唯一约束**：dark 先建了 opencode-go 配置，light 的 `findConfigByRef` 异步竞态未找到就走 POST 新建 → 409 `data_constraint`。这是测试设计缺陷不是产品缺陷（产品正确拒绝重复）。保存类核查**每主题用独立实例**。另：`#error` 全局共享且 `wrap` 只设 `hidden=true` 不清文本，读错误须查 `hidden` 而非 `inner_text()`，否则读到先前操作的残留。
- **option 底色判定须排除选中项**：单选下拉唯一选项必为 `:checked`，Chromium 对选中项渲染强制高亮（CSS 无法完全接管），会误报「白底/底色不符」。豁免单选项或注入临时第二选项判定普通底色。
- **颜色比较须 canvas 归一化**：同色 `getComputedStyle` 可能返回 `rgb()`/`#hex`/`oklab()`，字符串直比虚警。用 canvas `fillStyle` 归一化为 RGB 分量再比。
- **浅色主题白底是设计值**：`--surface-1` 浅色=#ffffff，「禁白底」判据必须主题感知（与面板底色一致），不能一律禁白。

**已知遗留问题**：

1. 390 宽度下设置页是普通文档流（不是覆盖层），属于有意为之。
2. 真实模型/平台/72 小时稳定性未验收；本批不构成验收。
3. `-n 4` 下偶发超时（已见 `test_v2_portal_ui`、`test_v2_skills_ui`、`test_installer_scripts`、`test_v2_terminal_native`、`test_v2_memory_organization_ui`），隔离单跑均通过（并行负载抖动）。判定回归归属用 `git stash` 暂存本批改动后跑同一用例对照。

---

## 5. 设计基准（照这个做）

**布局**：左侧 rail（54px，Material 图标）→ 工作台三栏（会话 312 / 对话弹性 / 右栏 320）。

**颜色（暗色）**：底 `#070809`；面板 `#101215`；二级面 `#16191d`；描边 `#23272c`；正文 `#eae7e2`；次要 `#b8b6b1`；强调 `#3ddbd9`；次强调 `#7c8cf8`。

**排版**：正文 15px / 行高 1.6；面板标题 12px 大写字距 0.1em；代码用 `--font-mono`；圆角 6/8/12/16。

**视觉规则**：

- 面板 = 面 + 1px 低对比描边 + 标题栏，不堆阴影；
- 消息列限制 880px 并居中，避免 4K 下拉成一条；
- 按钮三种：默认（面 + 描边）、主（青色底 + 深色字）、危险（红字红边）；
- 空态一律"图标/标题 + 一句引导 + 下一步按钮"，不留纯空白；
- 所有图标用 `<span class="material-icons">`，不要 emoji。

---

## 6. 文件结构与改动方式

```
web/ui/v2/
├── index.html          壳层、23 个 <section class="page">、dock、右栏、设置面板
├── main.js             连接/轮询/消息渲染/会话/工具卡/面板数据（约 1000 行）
├── workbench.css       全部样式（含 V1 组件移植与主题覆盖）
├── capabilities.js …   各页面模块（只负责自己分区的渲染与事件）
```

- 新增页面：`index.html` 加 `<section id="x" class="page" hidden>` + nav 按钮（必须成对，守卫会查）+ `docs/v2/contracts/V2_BUSINESS_INVENTORY.md` 表格一行 + 在 `main.js` 的页面切换 `if (selectedPage === 'x')` 分支加载。
- 数据请求统一走 `main.js` 的 `api(path)`（自动带 owner token、15s 超时、错误冒泡到 `#error`）。
- 页面模块返回 `{ load, reset, ... }`，在 `main.js` 顶部 `mountXxx({ api, wrap, getAgent, getGeneration })` 注册。
- **CSP**：`style-src 'self'`。禁止 `setAttribute('style', ...)` / `style.cssText`；`el.style.prop = ...` 在 Chromium 可用但请优先用 class。

---

## 7. 本地验证流程（每个小步都做）

```powershell
# 1) 只跑相关 UI 测试
uv run --no-sync pytest -q -n 0 agent/tests/test_v2_ui.py
uv run --no-sync pytest -q -n 0 agent/tests/test_v2_navigation_ui.py agent/tests/test_v2_business_inventory.py

# 2) 改版后截图（Playwright，参考 temp/build/hermes_shot.py 的写法）
#    - 强制主题：add_init_script("localStorage.setItem('omni_theme','dark')")
#    - 连接：fill('#token') → click('连接实例')
#    - 尺寸：1440x900、1024、390 各一张；深浅各一张

# 3) 门禁（改动收尾）
uv run --no-sync pyright --project pyrightconfig.strict.json   # 若动了 Python 才需要
uv run --no-sync python scripts/project_index.py --check
```

**截图自检清单**：无横向滚动、无元素重叠、标题层级正确、空态有引导、390 下可操作。

全量 `pytest -q`（约 15 分钟）只在交付收尾跑一次；不要在别的会话跑全量时并发跑第二个全量。

---

## 8. 剩余任务（按顺序做）

### 8.1 右栏数据（0.5 天）
- 技能页签：`GET /agents/{id}/runs/{run}/routing` 或 `#skill-run-status` 的文本，渲染"本轮技能"列表（名称 + 启用态）。
- 产物页签：`GET /agents/{id}/objects?limit=12`，渲染 `media_type · 字节 · sha 前 10 位`，点击可下载（复用 `main.js` 的 `api(..., {binary:true})`）。
- 桌面页签：列出本会话截图（`#image-capture` 的 option 数据），点击预览到右栏。

### 8.2 对话细节（0.5 天）
- 助手消息头像/名称行（"小鱼 · HH:MM · N 步"），与 V1 对齐。
- thinking 动画：运行中在最后一条消息下显示三点脉冲（CSS keyframes，`prefers-reduced-motion` 时静止）。
- 工具卡按 step 分组：同一 run 内连续工具调用折叠成一组，摘要显示"调用过程 · N 步 · M 个工具"。

### 8.3 设置中心完善（1 天）
- 左侧分类页签（7 组）与内容区联动，选中态同步到 rail。
- 每页顶部统一"标题 + 一句话说明 + 返回对话"，表单控件统一为深色样式（已有基础）。
- 弹层关闭：Esc、点击遮罩、返回对话三个入口。

### 8.4 移动端（0.5 天）
- 390：rail 横向滚动、左栏折叠为"会话"抽屉、右栏隐藏、dock 贴底。
- 768：两栏（会话 + 对话）。

### 8.5 收口（0.5 天）
- 合并 `workbench.css` 的重复 override，删除失效规则。
- 更新 `docs/v2/contracts/V2_BUSINESS_INVENTORY.md`（新增页面/状态）、`docs/history/v2/V2_AI_CONTINUATION_PROMPT.md`（版本串与当前段）。
- 写 `docs/evidence/v2-ui.json`：本次改动的范围、截图路径、测试命令与结果、未完成项（不要写"已验收"）。

---

## 9. 协作纪律

- 本仓库可能有**并行会话**在改 Python 领域代码。提交前 `git status`，只 `git add` 你自己的文件（`web/ui/v2/**`、相关测试、相关文档），**绝不 `git add -A`**。
- 不要 `git push --force`、不要 reset/clean、不要删除未跟踪文件。
- 每个节点一次提交，信息写清"做了什么 + 影响面"；提交后推送 `v2`。
- 不修改 V1 的 `web/index.html`、`web/ui/frame.css` 等；V2 只读参考。
- 不把"能构建/能截图"写成"已验收"；真实平台、真实账号、72 小时仍属未验收。

---

## 10. 现有测试入口速查

| 测试文件 | 覆盖 |
|---|---|
| `agent/tests/test_v2_ui.py` | 主工作台 73 项（双主题 × 四视口） |
| `agent/tests/test_v2_navigation_ui.py` | 7 组 23 项导航、页面可达、无横向溢出 |
| `agent/tests/test_v2_business_inventory.py` | 导航/分区/业务清单三方一致 |
| `agent/tests/test_v2_capabilities_ui.py` | 能力页 |
| `agent/tests/test_v2_mcp_ui.py` | MCP 页 + 资源版本断言 |
| `agent/tests/test_v2_skills_ui.py` / `test_v2_images_ui.py` | 技能选择、图片附件 |
| `agent/tests/test_v2_owner_token_ui.py` | 令牌页轮换 |

跑单个用例：`uv run --no-sync pytest -q -n 0 "agent/tests/test_v2_ui.py::test_workbench_chat_memory_and_theme[light-1440]"`。

---

## 10.5 模型接入页（models，v126 新增）

设置中心新增「模型接入」页（系统与运维组首位，导航/分区/清单/契约同步为 23 页）：
**9** 供应商卡片（含 **OpenCode Go** 预设：端点 `https://opencode.ai/zen/go/v1`、
模型建议 `openai/qwen3.8-flash` 等）自动预填端点/模型/凭据引用；API Key 密码输入+显隐切换；
一键保存并启用、可选设为默认；已配置列表带诊断/启停/设默认/测试连接。密钥只经 `PUT /credentials`
写服务端凭据库，配置只存引用名，前端保存即清空、不落浏览器存储。新增 `POST /configurations/{id}/probe`
复用真实适配器做最小调用（输出压到 16 token），先经 diagnose 校验授权与启用。
`load()` 只按空间+连接代次守卫（并发刷新不互相取消），并并行读详情补全列表 value。

**自定义请求头**（v132）：高级折叠区可逐行填 `Name: Value`，随配置存入 `model_options.extra_headers`
（schema v0038 新增 JSON 列，默认 `{}`）。`ModelOptions` 校验：≤20 个、名为 token 字符、
**禁止覆盖 `authorization`/`x-api-key`/`cookie`/`host`**、值≤2000 且无可打印控制字符。适配器据此
附加头；OpenCode Go 的 `x-opencode-session` 由适配器自动加，无需手填。

验收：离线本地兼容端点端到端 17/17；UI 25/25；离线闸门明确拒绝。**真实外部供应商已验收**：
OpenCode Go（qwen3.8-flash）端到端 8/8——UI 填 Key→服务端凭据库→真实适配器→真实端点→流式对话回复渲染。
**72 小时长稳仍未验收**。迁移契约：加列后所有冻结基线种子测试需经 `add_model_headers_column`
补列（v0038 幂等，见 §4 第 21 条）。

## 11. 交付标准

1. 23 个页面在 1440 / 1024 / 390 下均可操作，无横向滚动、无重叠；
2. `test_v2_ui.py` 73 项全过（或失败项有独立复现记录）；
3. 深浅主题都可读，对比度不低于 AA；
4. 资源版本号已升级并被测试覆盖；
5. 截图存放在 `temp/build/`，证据写入 `docs/evidence/v2-ui.json`；
6. 提交推送到 `v2`，不包含他人未提交改动。
