# UI 重构 Phase 3D：全页面迁移收尾与视觉精修

日期：2026-11-27 ｜ 依据：`UI_REFACTOR_PHASE03_2026-11-27.md`
范围：仅 `web/ui` 前端；未改后端 API/数据库/内核；保留 V1 冻结副本（`web/index.html` + 根 `web/ui/*` 未动）。

## 1. T2 监控页面结构化

| 页面 | 实施 |
| --- | --- |
| Logs | `#log-rows` 改为结构化 `<table>`（时间/级别/来源/消息/操作，吸顶表头、等宽时间/来源、行内复制）；新增关键字输入（本地过滤已加载记录）、复制当前结果、清空条件；原有等级/组件/运行/时间筛选保留；原始 JSON 仍在折叠区 |
| Capabilities | 分类列表 + 详情 Inspector：左列能力域（状态徽章），右侧显示名称、状态、限制、计数表（缺值为"未知"）、能力域 ID；空报告显示明确空态 |
| Worker/Runs | Worker 任务/子运行沿用统一 `worker-badge` 状态徽章；时间线状态文本沿用同一状态语义；Ticket/运行 ID 继续可追踪（时间线、delegations、审批证据互链） |
| 数据真实性 | 只读既有 API；空数组显示空态，不生成占位数据；缺失字段显示未知 |

## 2. T4 审计页面

- 左侧审计事件时间线：时间 / 来源 kind / 操作对象 / 结果（成功/失败/未知，色+文本）。
- 右侧结构化详情：事件时间、来源、操作对象、结果、记录 ID；原始 JSON 收纳在 `<details>`。
- 工具栏：搜索（动作/对象/来源/结果）、结果筛选（成功/失败/未知）、重新加载/加载更多、复制选中事件；详情内可复制单条 JSON。
- 空数据/无匹配均显示明确空态；保留原 `/audit` API 与分页/游标逻辑（`operations.js` 增加可选 `renderNode`，不改变默认渲染）。

## 3. 26 页迁移清单（截图：`output/playwright/phase3d-verify/`）

| # | 页面 | 结构（唯一 H1/无 390 溢出） | 视觉结论 |
| --- | --- | --- | --- |
| 1 | chat | ✅ | T1 三栏稳定；Phase 1/2 组件在位 |
| 2 | context | ✅ | 继承 V3 排版/宽度；只读统计页 |
| 3 | skills | ✅ | T3 表单/列表规则 |
| 4 | memory | ✅ | T3；内容宽度收敛 |
| 5 | documents | ✅ | T3 |
| 6 | reflections | ✅ | T4 类审批列表 |
| 7 | persona-growth | ✅ | T3 只读 |
| 8 | extensions | ✅ | T2 类列表（表格化留批次 2） |
| 9 | mcp | ✅ | T2 类 |
| 10 | action-reviews | ✅ | T2/T4 混合，Phase 2 审批语义保留 |
| 11 | qq | ✅ | T3 |
| 12 | bilibili | ✅ | T3 |
| 13 | android | ✅ | T2 设备操作 |
| 14 | desktop | ✅ | T2 |
| 15 | terminals | ✅ | T2，xterm 内嵌保留 |
| 16 | people | ✅ | T3 |
| 17 | objects | ✅ | T3 |
| 18 | media | ✅ | T3 |
| 19 | schedules | ✅ | T3 向导表单 |
| 20 | deliveries | ✅ | T4 类 |
| 21 | models | ✅ | T3 |
| 22 | settings | ✅ | T3；重复设置栏已移除；顶部条已对齐 |
| 23 | capabilities | ✅ | **结构化为分类列表+详情** |
| 24 | tokens | ✅ | T3 |
| 25 | logs | ✅ | **结构化为表格** |
| 26 | audit | ✅ | **结构化为时间线+详情** |

所有页面：深/浅主题截图各 1 张（共 52 张），1440 无横向溢出、390 无溢出。

## 4. 设置页对齐修复

非对话页顶部连接条与内容同宽同边距：`workbench-side > .instance-panel` 采用 `--content-max` + `--page-pad` 居中并横排；Playwright 实测顶部条左边距 = 页头左边距（±2px），原连接/Agent 切换/重命名逻辑未动。

## 5. V1/V2 基线治理

- **V1 回滚参照保留**：根 `web/index.html`、`web/ui/*.css` 未删除/未重写；仅共享 `tokens.css` 追加 V3 语义 token（旧变量保留，V1 行为不变）。
- **V2 独立检测范围**：检测以 `web/ui/v2/index.html` + 其实际加载的 `assets/ui/v2/**`、`assets/ui/{tokens,base,icons}.css` 为范围（临时镜像目录运行），不把 V1 整站与子站纳入 V2 结论。
- **历史残留 vs 当前问题**：剩余 6 条 finding 中 5 条来自 V2 所加载的 V1 冻结副本历史规则（`web/ui/v2/v1/*.css`）与动态 `<img>`；均分类说明，**未添加 ignore 文件**。

## 6. 修改文件清单

| 文件 | 变化 |
| --- | --- |
| `web/ui/v2/index.html` | 日志工具栏（关键字/复制/清空）；审计页改为工具栏+`#audit-list` 结构化容器；版本 `?v=20261127p3d` |
| `web/ui/v2/main.js` | 日志表格渲染/关键字过滤/复制/清空；审计时间线+详情+搜索筛选+复制（`renderAudit`）；审计面板接入 `renderNode` |
| `web/ui/v2/operations.js` | 审计通用分发支持可选 `renderNode`（默认行为不变） |
| `web/ui/v2/capabilities.js` | 分类列表 + 详情 Inspector（状态徽章、计数表、空态） |
| `web/ui/v2/workbench.css` | 日志表格、能力分栏、审计时间线/详情、设置页顶部条对齐、时间线徽章样式；390 折叠规则 |
| `docs/v/design/UI_REFACTOR_PHASE03D_2026-11-27.md` | 本文档 |

未触碰：后端、数据库、其他会话未提交文件（含新增的 `test_v2_omniweave_campus.py`）。

## 7. 测试结果（隔离实例 + 真实前端）

`output/playwright/phase3d-verify/verify.mjs`：**69/69 checks 通过，52 张截图，0 脚本错误**。
覆盖：26 页唯一 H1、26 页 390 无横向溢出、设置页顶部条对齐、日志表格/关键字过滤/复制按钮、能力分类列表+详情切换、审计空态 + mock 数据的左右分栏/详情/搜索过滤/原始 JSON、Phase 1/2 组件在位。

回归：`check_frontend.py` 93 模块语法通过；`test_v2_navigation_ui.py` 2 passed；`test_v2_ui.py::test_workbench_chat_memory_and_theme` 8 passed。

## 8. Impeccable 检测（V2 范围）

| 指标 | Phase 3 | Phase 3D |
| --- | --- | --- |
| findings | 6 | 6 |

剩余分类：`broken-image ×2`（动态注入 src 的预览图，静态扫描为空，运行验证无坏图）、`nested-cards ×1`（页面面板内的状态容器，结构性嵌套）、`dark-glow ×1`、`undersized-ui-text ×1`、`repeating-stripes-gradient ×1`（均来自 V2 加载的 V1 冻结副本历史规则，V2 新样式已覆盖；批次 2 收敛副本时清理）。未使用 ignore。

## 9. 未解决问题

1. 审计/日志表格的空数据实例未覆盖真实大数据量渲染（mock 仅 3 条）；批次 2 用真实数据回归。
2. 剩余 5 条历史样式来自 V1 冻结副本，需在“V2 自有基线”收敛批次处理（不能删副本，需替换为 V2 规则）。
3. extensions/mcp/action-reviews 等 T2 页仍是卡片/表单结构，表格化留批次 2。
4. 其他页面的局部微调（空态文案层级、个别表单按钮层级）未逐项精修，仅保证全局规范与截图验收。
5. 390 下部分页面内容较长（设置/调度表单），无横向溢出但需 Phase 4 重排。
