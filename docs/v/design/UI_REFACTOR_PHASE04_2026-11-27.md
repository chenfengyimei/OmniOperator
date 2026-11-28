# UI 重构 Phase 4：移动端适配与无障碍专项

日期：2026-11-27 ｜ 依据：`docs/v/design/UI_AUDIT_2026-11-27.md` 与 Phase 01/02/03/03D 记录
范围：仅 `web/ui/v2`（V2 前端）；未改后端 API/数据库/内核；V1 回滚副本未动。

## 1. 移动端布局（4A）

- 新增 `viewport-fit=cover` + `interactive-widget=resizes-content`；`--vvh` 由 `visualViewport` 同步，软键盘弹出时主会话收缩，输入框不被遮挡。
- **手机 ≤760**：主会话单栏默认；顶部保留可滚动 44px 图标导航条（不挤压内容），新增 44px「导航」按钮 → 打开左侧全屏标签抽屉（280px，图标+文字+分组，触控 44px）；会话列表与 Inspector 改为**全屏抽屉**（而非纵向堆叠），带独立关闭按钮；弹窗 `max-width: calc(100vw-24px) / max-height: 85dvh`；安全区 `env(safe-area-inset-*)` 应用于顶栏、抽屉与输入区。
- **平板 761–1180**：保留会话+主会话两栏，Inspector 改为右侧 380px 滑出面板（不再挤压主会话）。
- **桌面 ≥1181**：维持三栏（260 / 弹性 / 320）。
- 旋转/宽度变化由 media query 重算；无 JS 固定像素状态；滚动容器互斥（body 锁定，抽屉内部各自滚动）。

## 2. 移动端核心交互（4B）

- 主会话默认可见；输入区随软键盘收缩与恢复。
- Worker 状态条、右栏「任务」页签、审批卡、输入队列在手机上均保留，Inspector 抽屉中可查看 Worker/审批/时间线，不隐藏任务状态。
- 审批卡、队列条目、时间线、日志/审计详情均在小屏可达；消息操作按钮在触控下常显（不再依赖 hover）。

## 3. 触控与响应式组件（4C）

- ≤760 或 `pointer: coarse`：button/select/input/rail/.approval-btn/.cap-item/.audit-event 最小高度 **44px**；危险操作组间距 12px；审批按钮最小宽 120px 防误触。
- hover 专属操作（消息操作）在触控下 `opacity:1; pointer-events:auto`。
- 桌面样式未变（媒体条件限定）。

## 4. 键盘与焦点（4D）

- 首屏新增「跳到主内容」链接（聚焦可见，Enter 跳转 `#main-workspace`）。
- 移动抽屉：打开后聚焦第一个导航项；Esc 只关闭最上层（dialog 优先 → 导航抽屉 → Inspector → 会话抽屉），关闭后焦点回到触发按钮。
- 全站 `:focus-visible` 2px 焦点环；Tab 顺序沿用 DOM；无正数 tabindex。
- 图标按钮自动补可访问名称、无标签输入自动补 `aria-label`（兜底，不改视觉与业务）。

## 5. 屏幕阅读器与动态状态（4E）

- `#messages` 移除整体 `aria-live`；模型流 `#model-live aria-live="off"`。
- 新增 `#live-status`（polite，视觉隐藏）做**摘要播报**：审批数量、运行终态、队列失败阻塞等，流式正文不逐字播报。
- 状态徽章均为「文字 + 颜色」，不依赖颜色单一通道。

## 6. 视觉可访问性（4F）

- 关键文字对比度实测（Playwright 计算）：页标题/副标题等 ≥4.5:1（见 p4-report.json）。
- 200% 等效缩放（720 CSS px）无横向溢出。
- `prefers-reduced-motion: reduce` 下过渡/动画降为 0.001ms。
- 深浅主题均截图验证；成功/失败用徽章文字+语义色，不单靠红绿。

## 7. 长表单与数据表（4G）

- 设置/调度等长表单在手机上单列分组，保存按钮保持在内容流中可达；`actions` 自动换行。
- Logs：≤760 隐藏「来源」列，保留时间/级别/消息/复制；表格容器局部滚动，不产生整页横向滚动。
- Audit：时间线与详情上下堆叠（时间线 45dvh、局部滚动），原始 JSON `pre-wrap + anywhere`。
- 长路径/ID/JSON 统一 `overflow-wrap:anywhere`。

## 8. 视口与测试结果

隔离实例 + 真实 V2 前端，`output/playwright/phase4-verify/verify.mjs`：**38/38 checks 通过**，截图：390/430/768/1024 × 深/浅主会话、390 导航抽屉、390/430 Inspector、390 设置/日志/审计、200% 缩放。

覆盖：四视口无横向溢出、主会话默认可见、移动导航抽屉（标签/点击后关闭）、Inspector 全屏抽屉开关、关键触控 ≥44px、唯一 H1/main/命名导航、图标按钮与输入框命名、无正数 tabindex、消息区无整体 live、摘要播报生效、对比度、reduced-motion、200% 缩放、Phase 1/2 组件在位、无脚本错误。

回归：`scripts/check_frontend.py` 93 模块语法通过；`test_v2_navigation_ui.py` 2 passed（26 页可达，390/1024——手机默认顶部导航条保证守卫兼容，抽屉仅在点「导航」后接管）。

## 9. 修改文件清单

| 文件 | 变化 |
| --- | --- |
| `web/ui/v2/index.html` | viewport、skip-link、移动菜单、抽屉关闭按钮、`#live-status`、移除消息区 aria-live、版本 `?v=20261127p4b` |
| `web/ui/v2/main.js` | 移动导航/抽屉开关与焦点恢复、Esc 层级、visualViewport 同步、MutationObserver 摘要播报、无障碍兜底命名 |
| `web/ui/v2/workbench.css` | 手机单栏+全屏抽屉、平板滑出 Inspector、触控 44px、弹窗/表格/审计适配、安全区、减少动效、焦点环、hover 触控替代 |
| `docs/v/design/UI_REFACTOR_PHASE04_2026-11-27.md` | 本文档 |

未触碰：后端、数据库、Agent 内核、V1 回滚副本、其他会话未提交文件。

## 10. 检测器

Impeccable 引擎对 V2 范围（Phase 3D 基线）剩余 6 findings 分类不变：broken-image×2（动态预览图）、nested-cards×1（结构性）、dark-glow/undersized-ui-text/repeating-stripes-gradient 各 1（V1 冻结副本历史规则，V2 未使用）。本阶段未新增装饰性缺陷，未添加 ignore。

## 11. 未解决问题

1. 软键盘实测仅用 visualViewport 模拟，未在真机 iOS/Android 验证。
2. 屏幕阅读器仅做 DOM/ARIA 语义与动态播报检查，未用 NVDA/VoiceOver 真读测试。
3. 平板 768 竖屏的 Inspector 滑出层与页面滚动配合需真机复核。
4. 200% 缩放通过布局宽度等效验证，未覆盖浏览器文字放大独立设置。
5. 移动端长列表虚拟化/性能未测。
6. 剩余 5 条历史样式 finding 仍需“V2 自有基线”收敛批次处理。

## 12. 回滚方案

- 删除/还原 `web/ui/v2/{index.html,main.js,workbench.css}` 到 Phase 3D 版本（`?v=20261127p3d` 对应文件），移除移动/无障碍规则与 JS 钩子；
- 无后端/数据库变更，不影响会话、审批、运行数据；纯前端回滚。
