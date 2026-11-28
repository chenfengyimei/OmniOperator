# UI_REVIEW.md —— OmniOperator 视觉与交互验收标准

> 自动化守卫能证明「样式契约成立」，证明不了「看起来对」。本文件定义人工/截图验收的矩阵、检查项与证据要求。
> 工具链：`playwright` 技能（`npx --package @playwright/cli playwright-cli`，本机用 `--browser msedge`）。

## 1. 验收矩阵（每批 UI 改动必跑）

| 维度 | 取值 | 说明 |
| --- | --- | --- |
| 视口 | 1440×900、1024×768、760×900、390×844 | 桌面主用 1440；390 为移动契约 |
| 主题 | dark、light | 两态都要看，不只暗色 |
| 界面 | 改动页 + 相邻 2 页 + 对话页 | 防止公共层回归 |
| 状态 | 空 / 加载 / 错误 / 成功；有数据/无数据 | 四态至少各看一眼 |
| 交互 | 切页、开合面板、审批、取消、刷新、Esc | 键盘与鼠标各一遍 |

截图存放：`output/playwright/`（已忽略入库）或系统临时目录；**不得上传到任何外部服务**。
自动化测试（pytest + Chromium）证明行为契约；截图证明观感，两者都不能互相替代。

## 2. 检查项清单（逐项勾）

### 布局与响应式
- [ ] 无横向溢出（含 390px）；长标题/长 ID/长 URL 有截断或换行策略。
- [ ] 三栏/单栏切换正确；rail 折叠后图标可点、触控 ≥36px。
- [ ] 滚动容器独立且回顶正确；sticky 工具条不遮内容。
- [ ] 面板开合不跳动、不残留上一次页面滚动位置。

### 视觉层级与质感
- [ ] 页面/卡片/浮层三层表面可区分；玻璃只在壳层挂 blur（单页 blur 元素 ≤8）。
- [ ] 全站只有一个强调色系；状态色只有 ok/warn/danger；无第二套色板。
- [ ] 字号 ≥10px，正文 13px；标题与正文有明确层级差。
- [ ] 圆角只用 5 档 token；阴影只用 sh-1/2/3；无写死色值。
- [ ] 暗/浅两态都维持同样的层级关系（浅色下不出现「白纱隐形」）。

### 交互与状态
- [ ] 空态说明「为什么空 + 怎么不空」；加载有占位；错误显示服务端 detail 且不回显敏感值。
- [ ] 未知/待核查状态不被画成成功色；迟到响应不会覆盖新选择（切换会话/Agent 后重点看）。
- [ ] 审批、停止、恢复、重试可见且语义正确；破坏性操作有确认。
- [ ] 键盘：Tab 焦点可见、Esc 关闭浮层、Enter/Space 可激活列表项。
- [ ] `prefers-reduced-motion` 下无持续动效；动效时长 120/180/240ms 档。

### 一致性
- [ ] 复用 `components.css` 类名，无新增同义组件（第二套按钮/弹窗/徽章）。
- [ ] 与 `docs/v/design/DESIGN.md` 的视觉方向一致；与 `docs/v/design/MASTER.md` 的令牌/断点约束一致。
- [ ] 既有能力没有因为视觉改动被删（26 页、审批横幅、右栏检查器、终端、四态）。

## 3. 截图与对比流程

```powershell
# 1. 启动工作台（按需；不要停用户正在用的服务）
uv run --no-sync omni-web           # http://127.0.0.1:8766

# 2. 用 playwright 技能截图（本机无 Chrome，走 Edge 通道）
npx --yes --package @playwright/cli playwright-cli open http://127.0.0.1:8766 --browser msedge
npx --yes --package @playwright/cli playwright-cli resize 1440 900
npx --yes --package @playwright/cli playwright-cli screenshot
npx --yes --package @playwright/cli playwright-cli close
```

注意（已实测）：

- `playwright-cli` 禁止 `file:` 协议，截图目标必须是 `http://127.0.0.1:8766` 这类本地服务（或 `about:blank`），
  不能直接截本地 HTML 文件。
- Codex 的默认沙箱里浏览器守护进程起不来（`Session closed`）；**截图由 DSH/宿主终端执行**，
  不要为了截图关闭 Codex 审批或沙箱。
- 本机无系统 Chrome，务必带 `--browser msedge`（或先 `playwright-cli install-browser chrome`）。

- 改动前后同一视口/同一页面各截一张，先看差异再看单张。
- 深浅两态各截一遍：顶栏主题三圆点切换（dark/auto/light）。
- 移动端可用 `resize 390 844` 后截图，无需真机；真机只在专项验收时使用。
- 截图文件名建议：`<页面>-<视口>-<主题>-<before|after>.png`。

## 4. 判定与记录

- **通过**：§2 全项勾选 + 矩阵视口截图齐全 + 相关 pytest 专项通过（记录测试文件名与结果）。
- **有条件通过**：仅视觉细节待定（例如色彩微调），需列出待办与负责人。
- **不通过**：出现溢出、功能缺失、状态失真、深浅态不一致、a11y 退化。
- 证据记录写入批次说明（`docs/v/STATUS.md` 或对应任务的 evidence JSON），截图路径写本机路径，**不提交截图本身**。

## 5. 已知设计债务（2026-11-27 读码结论，供重构排序）

优先级从高到低：

1. **V1 样式冻结副本**：`web/ui/v2/v1/*.css` 是 V1 样式的冻结拷贝，V1 壳层改动不进入 V2；改壳层要两处同步。
2. **令牌贯通不全**：`--sp-1..4` 在大量间距声明中未被引用，动效时长未 token 化，`--ease-out` 覆盖不全，裸 px/裸 cubic-bezier 仍存在。
3. **双份分派/映射**：V2 `main.js` 初始化路径与切页路径各写一遍 26 页加载；上下文中文映射有两份词表。
4. **可访问性**：会话列表项只有 click 无键盘处理；`#messages` 的 `aria-live` + 整表重渲染会重复播报；部分页面出现两个 `h1`。
5. **无 URL 状态**：26 页切换无 hash/pushState，刷新回默认页；深链与「复制当前页面链接」不可用。
6. **死代码/死令牌**：`--v2-*`/`--hermes-*`/`--terminal-bg`/`.chat-loading` 等无引用；`settings-rail` 内 `links/reset` 为死状态。

重构时应先做 1–3（结构性与一致性），再做 4–6（体验与清理）；每批保持四态与既有能力不降级。
