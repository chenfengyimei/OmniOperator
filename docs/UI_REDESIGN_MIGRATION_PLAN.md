# UI 重设计迁移方案

> 目标：把 `prototype-html/prototype-workbench-redesign.html`（设计稿）的视觉落地到
> `src/omni/channels/web_static/index.html`（真实应用），**功能零回归、JS 业务逻辑零重写**。
>
> 状态（2026-10-29 更新）：**P0–P4 已落地**——备份与契约门禁就绪（`scripts/check_ui_contract.py`
> + `tests/test_ui_contract.py`），新视觉以 `ui/skin.css`（token+覆盖层）与 `ui/icons.css`
> （内联字体）接线，rail 骨架、新 header、输入坞、消息区（头像/时间/悬停操作）、Markdown 渲染
> （`ui/markdown.js`）、右栏分段 tab（真实计数徽标）、设置中心重排均已进 `index.html`。
> P5 增值项（命令面板 / 主题切换）未排期。素材与回滚基准在 `docs/ui-migration/`（tokens /
> override / shell / baseline-index）。

---

## 一、先说清现状：这是两套东西

| | 设计稿 `prototype-html/` | 应用 `web_static/index.html` |
|---|---|---|
| 是否被服务 | **否**，全仓库无任何入口引用 | **是**，`channels/web.py:408` 唯一挂载 |
| 数据 | 写死的假数据 | 真实接口，约 20 个 `/api/*` |
| 代码量 | 2691 行（HTML+CSS+少量交互 JS） | 2159 行（740 行结构/样式 + 1416 行 JS） |
| 交互 | 只有 `toast()` 等演示函数 | 51 个函数、100 处内联事件绑定 |
| 图标 | Material Icons（已内联 base64） | emoji + 文字 |

`web.py` 只挂一个静态目录：

```python
_STATIC_DIR = Path(__file__).parent / "web_static"
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")
```

所以打开服务地址必然是 `index.html`。**不是缓存问题，是设计稿从来没有接线。**

---

## 二、不可破坏的契约（红线）

迁移只允许改「结构 + 样式」，以下东西一个都不能少：

| 契约 | 数量 | 说明 |
|---|---|---|
| `$('#id')` 选择器引用 | **119 个 id** | `$=s=>document.querySelector(s)`（第 741 行），全部靠 id 定位 |
| 内联事件绑定 | **100 处** `onclick/oninput/onchange/onkeydown` | 重排 DOM 时必须连同属性一起搬，函数名、`this`、参数顺序不变 |
| 全局函数 | **51 个** | `load*` / `render*` / `save*` 系列，函数体不碰 |
| `addEventListener` | 3 处 | 窗口尺寸、键盘等 |
| 后端接口 | 20+ 个 `/api/*` | 不改、不新增 |

**唯一必须碰 JS 的地方**：`render*` / `load*` 函数里拼 HTML 的模板字符串（列表项长什么样）。这部分算视觉，但要按阶段单独做、逐个函数核。

---

## 三、差异清单

### 3.1 布局骨架

| 区域 | 现有 | 原型 | 迁移动作 | 风险 |
|---|---|---|---|---|
| 左侧图标栏 | **无** | 8 项 rail | 新增，占据 54px | 中：需重算三栏宽度 |
| Header | brand + chips + 4 个跨页链接 + 终端/手机按钮 | crumb + chips + 搜索框 + 主题点 + 设置中心 | 重写结构，`#chips` 原位保留 | 中 |
| 左栏 | 会话 / 状态 / 服务 | 会话 / 服务 / 上下文用量 | 状态面板并入「上下文用量」 | 低 |
| 中栏 | `#messages` + `#composer` | 同 + dock 快捷条 + 模型选择 | 保留 id，加外围装饰 | 低 |
| 右栏 | **9 个 tab** | **6 个 tab** | 见 3.2 | 中 |

### 3.2 功能缺口（原型缺、应用有）

**必须补齐，否则等于砍功能：**

| 缺失项 | 现状位置 | 建议 |
|---|---|---|
| 产物（artifacts）tab | `#tab-artifacts` `#artifacts-grid` | 补成第 7 个 tab |
| 桌面（desktop）tab | `#tab-desktop` `#desktop-windows/tree` | 补成第 8 个 tab |
| 发布草稿（publications）tab | `#tab-publications` `#publication-list` | 补成第 9 个 tab |
| 项目与能力 / 后台命令 / MCP 服务 / 能力诊断 | header 4 个链接，**是 4 个独立页面** | rail 底部「更多」或直接列 4 个次级入口 |
| 状态面板 | `#status` | 并入上下文用量卡片 |
| 设置中心 2 个分区 | `sec-usage`（用量）、`sec-mcp` | 必须保留 |

**原型多出来的（新增能力，需你点头）：**

| 新增项 | 说明 |
|---|---|
| 主题切换（3 套配色） | 需要 `localStorage` 持久化 |
| 命令面板 Ctrl+K | 需要接 `/api/skills`、`#sessions` 做搜索源 |
| 全局 tooltip | 已做过一版（10-27），可直接复用 |
| 设置中心 `sec-android`、`sec-safety` | 原型有，应用没有对应后端配置项，**建议先不做** |

### 3.3 共享样式的坑

`index.html` 第 352 行引了 `/ui/base.css`，**6 个子页（project/jobs/mcp/capabilities/terminal/android）共用**。
→ 新视觉的 token 和组件样式一律写进 `index.html` 自己的 `<style>`，**不要动 `base.css`**，否则子页会一起变脸且无法回归验证。

---

## 四、分阶段计划

每个阶段独立可提交、可回滚，前一阶段验收不过不进下一阶段。

### P0 护栏（先做，不做完不动 UI）

- 备份现值：`docs/ui-migration/baseline-index.html`（并记录 `git status`）
- 新增 `scripts/check_ui_contract.py`：静态扫描 `index.html`
  - 断言 JS 里每个 `$('#xxx')` 都能在 markup 里找到同 id
  - 断言每处内联 `onclick="fn("` 里的 `fn` 都有 `function fn` 定义
  - 输出 `119/119`、`51/51` 的通过率
- 基线：`uv run pytest -q` 全绿（以开工时实际结果为准，记入台账）+ 手点冒烟清单落纸

**验收**：脚本跑通且全绿，作为后续每阶段的门禁。

### P1 只换皮肤（DOM 一动不动）

- 把原型的 `:root` 三层 token（seed → surface → text/line/radius/shadow/ease）搬进 `index.html` 的 `<style>`
- 用现有 class 名重写外观：`header` `main` `.panel` `.tabs` `#messages` `.msg` `#composer` `input` `textarea` `button`
- 图标仍用现有 emoji/文字，先不动结构

**验收**：119 id 全命中、JS 零改动、冒烟清单全过、肉眼已是新配色新质感。
**价值**：投入最小、风险最低，先拿到 80% 的「看起来像新版」效果。

### P2 骨架重排

- 内联 Material Icons（复用 10-27 的做法；建议单独放 `/ui/icons.css` 而不是塞进主文件）
- 新增 rail：工作台 / 记忆库 / 定时任务 / 技能库 / 终端 / 手机 / 审批 / 设置中心 + 跨页入口
- header 改 crumb + chips + 搜索 + 主题 + 设置中心
- 三栏宽度按 rail 重算；`#sessions` `#messages` `#input` `#send` `#composer` `#chips` 原位保留

**验收**：发消息、切会话、切 tab、开终端、开手机、审批横幅弹出——六项冒烟全过。
**风险最高的一步**：结构大改，必须逐条核对 100 处内联属性是否随元素一起搬走。

### P3 右栏卡片化

- 9 个 tab 外壳统一成原型的 `seg-tab` 样式，补齐原型缺的 3 个 tab
- 逐个改 `render*` 里的列表项模板（这是唯一碰 JS 的地方，一次一个函数）

**验收**：9 个 tab 全部能加载真实数据、控制台无报错。

### P4 设置中心

- 10 个 `sec-*` 按原型的卡片 + 左侧导航重写结构
- **所有 `cfg-*` / `cronf-*` 输入框 id、`saveHub(scope)` 调用、`hubPrecheck()` 全部原样保留**

**验收**：沿用 10-29 定的幂等标准——连续保存两次，`config.yaml` 逐字节相同；每页独立保存互不牵连。

### P5 增值项（可选，独立开关）

tooltip / 命令面板 / 主题切换。每项单独提交，坏了能单独撤。

---

## 五、风险与对策

| 风险 | 对策 |
|---|---|
| 内联事件依赖元素位置/`this` | 重排时把整段元素（含属性）一起搬，禁止重新手写 |
| 改坏 `/ui/base.css` 波及 6 个子页 | 新样式只进 `index.html` 配套的 `ui/skin.css` |
| `index.html` 有并行未提交改动 | 开工前先 `git status` + 备份，禁止覆盖；不混入他人改动 |
| `render*` 模板改动引入 XSS | 改模板时保留 `esc()` 包裹 |
| 前端回归靠什么兜底 | `tests/test_ui_flows.py`（真实 Chromium 走关键流程，含 390px 移动端契约）+ `tests/test_ui_contract.py`（DOM/加载顺序守卫）；首批迁移正是被前者的两个移动端测试抓住回归，见 §八 |
| 中途发现某 tab 渲染依赖具体 DOM 层级 | 该 tab 降级为「保留旧结构、只换配色」 |

---

## 六、明确不做

- 不重写 JS 架构、不引入任何框架、不拆文件
- 不改 4 个跨页子站的视觉（另立一批）
- 不改任何后端接口与数据契约
- 不删任何现有功能（原型没画到的，一律保留并适配）

---

## 七、开工前需要你定三件事

1. **4 个跨页入口**（项目与能力 / 后台命令 / MCP 服务 / 能力诊断）放哪？
   建议：rail 底部收成「更多」，或直接列 4 个次级图标（它们是独立页面，点开就跳走）。
2. **原型缺的 3 个 tab**（产物 / 桌面 / 发布草稿）保留吗？建议保留，补成同样样式。
3. **主题切换**（3 套配色）要不要？现有只有一套深色。

确认后我从 **P0** 开始，先交契约脚本和备份，再动第一行样式。

---

## 八、P0–P2 落地记录（2026-10-29）

三个决策的采纳结果：4 个跨页入口**直接列在 rail**（新标签打开）；原型缺的 3 个 tab
**全部保留**（本轮只换皮不动结构）；主题切换**不做**（P5 也不排期，等用户再提）。

接线产物与素材方案的偏差（都有理由，改素材时注意别回退这些决策）：

| 接线点 | 方案原定 | 实际落地 | 原因 |
|---|---|---|---|
| token/覆盖层位置 | 写进 index.html 内联 `<style>` | 独立 `ui/skin.css`，加载在 base.css 之后 | base.css 有一批 `body[data-ui-page="workbench"]` 规则，内联写法在级联里输给它；独立文件最后加载才压得住 |
| 素材与产物关系 | —— | `docs/ui-migration/{tokens,override}.css` 是唯一来源，`skin.css` 是拼接产物（头部有注明），`tests/test_ui_contract.py` 固化该纪律 | 改样式必须走素材，产物可随时重建 |
| shared.js 注入的「设置」按钮 | 未提及 | **按宽度二选一**：≥1001px 隐藏 `.ui-settings`（新 header 的「设置中心」是唯一入口）；≤1000px 反过来收起 `.hub-open`，沿用 shared.js 的紧凑「设置」 | 初版一刀切藏掉 `.ui-settings` 被 `test_ui_flows` 的 390px 契约测试抓住——窄屏正式设置入口就是它，任何时候不能藏死 |
| header 的终端/手机按钮 | 未提及 | 保留在 DOM（JS 靠 id 绑 `.onclick`，删了初始化会抛错），视觉上仍被 base.css 的 `.ui-ready` 规则藏住 | 入口已收进 rail；元素只为绑定而活 |
| header 高度 | 原型是定高 | `min-height:52px` 而非 `height:52px` | 定高在窄屏 flex-wrap 换行时让 `.ui-panes` 溢出到 main 底下、点击被 `#messages` 拦截——390px 契约测试抓住；桌面单行时视觉不变 |
| rail 的 4 个子站链接 | 未提及 | `target="_blank"` | 与 shared.js 注入导航在 workbench 上的行为一致，别把工作台顶掉 |
| 输入坞（第二批，2026-10-29） | 原型 dock 结构 | `#composer` 内加 `.dock` 包装（无边框 textarea + `.dock-bar` 工具条 + 聚焦流动描边），占位文案对齐原型 | 用户反馈「输入风格内容不一样」；`$('#composer').before()`（运行进度面板）与 `#input` 的 value/keydown 绑定都不受影响 |
| 发送键图标 | 原型 `<span class="material-icons">arrow_upward</span>` 写在按钮里 | 用 `#send::before` + SVG mask 画在左侧，**不能写进按钮标签** | JS 每回合会 `$('#send').textContent='思考中…'/'发送'`，标签里的图标首次发送即被冲掉且不再回来；disabled（思考中）时用 `:disabled::before{display:none}` 收起 |
| 输入坞的 skill 芯片 | 原型有 bilibili-watch / nightly-tidy / 待办 3 | **不加** | 原型里是演示假数据；真实应用没有「按技能附发消息」的通道，只动不做的芯片等于假 UI |
| tooltip（P5 项） | 可选后置 | 提前进本批 | rail 是纯图标栏，没有提示就没法用；`#tip-layer` 顶层浮层 + `data-tip` 委托 |
| 审批队列入口 | 原型没有 | rail「审批队列」= 立即拉一次 `pollApprovals()`，无待审批时 toast 提示 | 审批只有横幅没有页面，给个手动触发点 |
| 消息区（第三批，2026-10-29） | 原型 turn 结构 | 助手：`.a-head`（渐变头像+昵称+时间）+ 无气泡正文（左内边距 26px）；用户：右对齐渐变气泡 + `.turn-foot` 时间行；复制键保留 `.copy-msg` 类（事件委托 `closest('.msg')` 不改） | 用户反馈「聊天界面不一样」；昵称取 `/api/state` 的 `persona`（默认小鱼），时间取 `messages.created_at`（本地时区 ISO） |
| Markdown 渲染 | 无（纯文本） | 新 `ui/markdown.js`（零依赖、先整体转义再转换、链接只放行 http(s):// 与 /），仅助手消息启用；用户消息保持逐字转义 | 助手回复里的 `**加粗**`、列表、表格此前显示裸符号；`tests/test_ui_markdown.py` 7 项钉住 XSS 边界 |
| 右栏 tab 徽标 | 无 | `setTabBadge()` 只显示真实计数（记忆/未完成待办/技能/产物），0 就摘掉 | 原型徽标是演示数字（28/3），不能照抄 |
| 设置中心（第四批，2026-10-29） | 原型 hub 布局 | header 加 `.hub-mark` 图标块；196px 分组导航（8.5px 大写标签、11.5px 条目、活动态左侧渐变条）；卡片 max-width 820 + 卡片滚动定位 | 结构与原型已接近，主要是密度/字号对齐；桌面布局规则包在 `@media (min-width:861px)` 里，≤860px 沿用内联式折叠导航，不与之抢权重 |
| 缓存防错配（第五批，2026-10-29） | 无 | 三个自产资源加 `?v=` 版本串（icons / skin / markdown.js）+ `test_ui_assets_carry_cache_busting_versions` 守卫；素材重生成时必须同步递增 v | 用户实机「界面/icon/样式全错、页面错乱」实为浏览器缓存了「新版标记+旧版皮肤」的中间态错配——标记与皮肤分步改版存在空窗；版本串让任何缓存组合都自洽 |
| 会话列表（第六批，2026-10-29） | 原型 `.sess` 四层选中态 + 搜索框 + meta 行 | 选中态全量实现（accent 底 + 内发光 + 左渐变条 + 流动虚线描边 `arc 9s`）；面板加 `.s-search` 真实过滤（缓存内过滤不发请求）；每行加 meta 行（时钟图标 + 相对时间 + 会话号，数据来自 `/api/sessions` 早已返回的 `created_at`，此前前端未使用） | 用户指出「选中的会话没有原型那种效果」——初版只做了底色+描边两层，漏了内发光/渐变条/动画；`relTime()` 沿用 fmtTime 的容错风格，解析失败不显示 |
| 调用链（第七批，2026-10-29） | 原型 `.scaffolds` 工具调用链 | **后端**：`tool` 事件补 `args`（240 字截断）/`excerpt`（400 字结果摘录）/`elapsed_ms`（单工具耗时，串行并发两路径共用 `timed()` 包装）——只进 state.db 权威账本，events.jsonl 观测口是字段白名单不会带出（模型上下文全文本就在 state.db，不新增隐私面）。**前端**：`scaffoldHTML()` 卡片化（模型调用步合并行、工具行带状态字形/参数/耗时/可展开 `.scaf-out`、图片/压缩/花费/终态行），实况轮询重建渲染；**历史挂链**：`attachHistoryRuns()` 按 `run.created_at` 认领其后第一条助手消息（时间升序防撞、runChainCache 切会话不重拉），头行 meta 追加「N 步 · Ns · 终态」 | 用户反馈「看不见完整的思考链/工具调用链」——旧版一行式进度条回合结束即消失，历史会话完全看不到中间过程；事件本就持久化（D09 断线续读），挂链只是把账本读出来。测试：`test_tool_events_carry_args_excerpt_and_elapsed`（载荷）+ `test_ui_runs.py`（真实 runtime 种子 run 后浏览器断言挂链/展开/meta） |

已知让步（同权重冲突里 base.css 赢的部分，属既有响应式设计，不再对打）：
`.tabs` 在 workbench 保持 nowrap+overflow-x（9 个 tab 换行会爆高）；≤760px 的
composer 间距沿用 base.css 的移动端调整。

**冒烟清单**（改完刷新页面即可，服务不用重启——静态文件按请求读盘）：
发消息 / 切会话 / 切右栏 9 个 tab / rail 记忆库·技能库·定时任务·审批队列 / 终端·手机新标签 /
设置中心打开与各分区保存 / 审批横幅（有 agent 等待时）/ 窄屏（≤1000px）rail 收起后面板切换可用。
