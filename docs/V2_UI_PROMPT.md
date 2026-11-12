# V2 工作台 UI 开发交接提示词

> 交给下一个开发 Agent 的完整上下文与任务书。先读本文，再动手；所有结论以当前源码和测试为准。

## 1. 你的任务

把 OmniOperator **V2 的 Web 工作台**（`web/ui/v2/`）继续做成"V1 的成熟度 + Hermes 的排版/配色质感"：

- 保留现有功能与测试契约（22 个页面、7 组导航、所有 id 钩子）；
- 按本文第 8 节的顺序补齐剩余 UI；
- 每完成一块：截图自检 → 跑相关测试 → 只提交自己改的文件。

**不要重写架构，不要动 Python 业务代码，不要改 V1 的 `web/index.html`。**

---

## 2. 仓库与运行环境

| 项 | 值 |
|---|---|
| 工作区 | `D:\xm\OmniOperator`（Windows / PowerShell） |
| 分支 | `codex/architecture-v2`（远端 gitee，已推送） |
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
nav button[data-page]             ← 必须 22 个，可访问名 = 页面中文名（aria-label）
section.page[id]                  ← 22 个页面分区，id 与 data-page 一一对应
```

- 页面切换只允许通过 `[data-page]` 按钮点击触发；`test_v2_navigation_ui.py` 会在 390/1024 两个宽度逐项点击并断言目标分区可见。
- 任何新增可见按钮，如果文案与既有页面名相同（如"设置"），必须换名或用 `title/aria-label` 区分，避免 `get_by_role(..., exact=True)` 命中两个元素。
- 资源版本串：`index.html` 与所有模块 import 都是 `?v=20261112v56`。**任何 HTML/CSS/JS 改动都要整体升级版本号**（v57、v58…），并同步：
  - `agent/tests/test_v2_mcp_ui.py` 里的 `assert "main.js?v=..." in page.content()`
  - `docs/V2_AI_CONTINUATION_PROMPT.md` 的版本说明
  否则浏览器缓存会让人看到旧界面（已经被这个坑坑过一次）。

---

## 4. 当前 UI 状态（已完成）

- 左栏四段式：连接卡 → Agent 空间 → 会话（弹性滚动）→ 服务（真实健康数据）→ 上下文用量
- 中间：消息列（880px 居中）、用户气泡（炭黑+青渐变）、助手气泡、时间戳 `HH:MM`、悬停复制按钮、Enter 发送 / Shift+Enter 换行
- 工具调用卡：`调用过程 · N 步 · M 个工具` 折叠块，逐条 `check_circle + 工具名 + 参数 + 耗时`
- 底部：dock 输入区（消息框 + 青色发送胶囊 + 档位/技能/图片/截图工具条）
- 右侧：`.panel` + `.tabs`（待办/技能/产物/桌面），待办接审批计数
- 设置中心：覆盖主工作区的整屏面板（rail 保持可点），面包屑"设置中心"、900px 内容宽、返回对话
- 主题：`data-theme=dark` 为 V1 炭黑+青色（`#070809` / `#3ddbd9`）；`light` 保持 tokens 浅色
- 响应式：≤1180 隐藏右栏；≤900 rail 变横向图标条、左栏堆叠；≤620 页头副标题隐藏

**已知遗留问题（你可以顺手修）**：

1. `workbench.css` 里为了对齐 V1/Hermes 叠加了大量 override 块（文件已 2000+ 行），建议按"壳层 / 面板 / 对话 / 设置 / 移动端"合并去重，删除失效规则。
2. 右侧"技能/产物"页签只有空态文案，未接数据（见第 8 节）。
3. 390 宽度下设置页是普通文档流（不是覆盖层），属于有意为之。
4. 控制台有一条 CSP 报错来自 `web/ui/terminal-theme.js` 的 `probe.style.cssText`（终端主题探测），与 V2 无关，可忽略。

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
├── index.html          壳层、22 个 <section class="page">、dock、右栏、设置面板
├── main.js             连接/轮询/消息渲染/会话/工具卡/面板数据（约 1000 行）
├── workbench.css       全部样式（含 V1 组件移植与主题覆盖）
├── capabilities.js …   各页面模块（只负责自己分区的渲染与事件）
```

- 新增页面：`index.html` 加 `<section id="x" class="page" hidden>` + nav 按钮（必须成对，守卫会查）+ `docs/V2_BUSINESS_INVENTORY.md` 表格一行 + 在 `main.js` 的页面切换 `if (selectedPage === 'x')` 分支加载。
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
- 更新 `docs/V2_BUSINESS_INVENTORY.md`（新增页面/状态）、`docs/V2_AI_CONTINUATION_PROMPT.md`（版本串与当前段）。
- 写 `docs/evidence/v2-ui.json`：本次改动的范围、截图路径、测试命令与结果、未完成项（不要写"已验收"）。

---

## 9. 协作纪律

- 本仓库可能有**并行会话**在改 Python 领域代码。提交前 `git status`，只 `git add` 你自己的文件（`web/ui/v2/**`、相关测试、相关文档），**绝不 `git add -A`**。
- 不要 `git push --force`、不要 reset/clean、不要删除未跟踪文件。
- 每个节点一次提交，信息写清"做了什么 + 影响面"；提交后推送 `codex/architecture-v2`。
- 不修改 V1 的 `web/index.html`、`web/ui/frame.css` 等；V2 只读参考。
- 不把"能构建/能截图"写成"已验收"；真实平台、真实账号、72 小时仍属未验收。

---

## 10. 现有测试入口速查

| 测试文件 | 覆盖 |
|---|---|
| `agent/tests/test_v2_ui.py` | 主工作台 73 项（双主题 × 四视口） |
| `agent/tests/test_v2_navigation_ui.py` | 7 组 22 项导航、页面可达、无横向溢出 |
| `agent/tests/test_v2_business_inventory.py` | 导航/分区/业务清单三方一致 |
| `agent/tests/test_v2_capabilities_ui.py` | 能力页 |
| `agent/tests/test_v2_mcp_ui.py` | MCP 页 + 资源版本断言 |
| `agent/tests/test_v2_skills_ui.py` / `test_v2_images_ui.py` | 技能选择、图片附件 |
| `agent/tests/test_v2_owner_token_ui.py` | 令牌页轮换 |

跑单个用例：`uv run --no-sync pytest -q -n 0 "agent/tests/test_v2_ui.py::test_workbench_chat_memory_and_theme[light-1440]"`。

---

## 11. 交付标准

1. 22 个页面在 1440 / 1024 / 390 下均可操作，无横向滚动、无重叠；
2. `test_v2_ui.py` 73 项全过（或失败项有独立复现记录）；
3. 深浅主题都可读，对比度不低于 AA；
4. 资源版本号已升级并被测试覆盖；
5. 截图存放在 `temp/build/`，证据写入 `docs/evidence/v2-ui.json`；
6. 提交推送到 `codex/architecture-v2`，不包含他人未提交改动。
