# OmniOperator V2 UI 迭代交接提示词（2026-11-20）

把本文件全文粘贴给新会话即可继续开发。

---

继续 OmniOperator V2 工作台的 UI 迭代开发。仓库根 `D:\xm\OmniOperator`，分支 `v2`，远程 `https://gitee.com/chenfengloveyuri/OmniOperator.git`。

## 总目标（所有者原话意译）
持续逐模块优化 UI 界面/布局/审美；设置中心面向普通用户简化，技术项渐进披露；一个模块一个界面地小步迭代，每阶段独立验收、独立提交。

## 七阶段计划与进度（计划文件 `C:\Users\cy\AppData\Local\Qianwen\User Data\qwen-agent\mSuJPulkUi\plans\20261119-211353-016-v2-ui-iteration.md`）
- ✅ P1 设计刻度地基（tokens 增 `--sp-1..4`/`--fs-title:16`/`--fs-headline:14`，h1/h2/h3 提档，间距归一）— 提交 `8297836b`，v164
- ✅ P2 聊天阅读体验（760px 阅读列居中、空态 4 张建议卡接线 `.chat-suggestions`、回底 FAB、::selection、入场淡入）— 提交 `b74b85ac`，v165
- ✅ P3 dock 渐进披露（默认只留 输入框+档位+「更多」芯片+发送；技能/图片 7 控件收 `#dock-extra` 内联面板；芯片激活计数「更多·N」；Esc 收起还焦；原 ID 全保留）— 提交 `fc83ee77`，v166
- ✅ P4 设置中心瘦身（5 处技术项收 `details.settings-advanced`：预算步数/逐档模型预留/配置编辑器 `#config-editor-wrap`/导入JSON/凭据核查 `#credential-review-wrap`；「去模型接入页」引导按钮 `#goto-models`；类型切换自动展开；凭据 unknown 态自动展开核查；可见输入 45→11；同批完成审批卡结构化重做 `renderApprovals`/`.approval-card`）— 提交 `741a8aff`，v168
- ✅ P5 schedules 频率向导（向导卡 `#schedule-wizard`：每天/每周/每月分段单选+`#schedule-wiz-time` 时刻+星期7/日期31多选芯片；`compileWizard` 写回 `schedule-kind`/`schedule-expression`、`parseWizard`/`syncWizard` 双向回读带焦点守卫；fresh 时区=`Intl…timeZone`；摘要行 `#schedule-wiz-summary-text` 实时回显；自定义规则/interval/once 让位或禁用+高级 `#schedule-advanced` 自动展开+点选接管；末位芯片守卫；原 ID 与草稿 CAS 全保留）— 提交 `f147ccbb`，**当前资源版本 `20261120v170`**；实测 54/54（每天 09:30→载荷 `30 9 * * *`、高级回读一致）+schedules 专项 9/9 零改动
- ✅ P6 deliveries 消息选择器 + 术语白话横扫（`#delivery-session`+`#delivery-message` select 从会话消息只读 API 选取、非 succeeded 回合禁用标注、两行正文预览；美元等值 hint 与运行报告 append；mcp/credentials/extensions 技术详情折叠+白话摘要；五页 intro/标签白话）— 提交 `6690b59c`，**当前资源版本 `20261120v171`**；delivery 8/8 含 refresh-race 双 repeat、四专项+守卫全绿、实测 19/19
- ✅ P7 集成收尾（唯一一次全量在 `NO_PROXY=*` 隔离下跑：8192 passed / 25 failed / 0 errors，43:11，-n 4，基线 `7cdd21a6`；25 例全归因并行批既有红/负载 flaky/环境，见证据 `ui_iteration_p7_integration`；首跑被 Windows 注册表 ProxyOverride→httpx InvalidURL 环境根因作废已记录）；Playwright 双主题×1440/760/390 巡 24 页 144 截图 147/147 检查过；提交见最近提交，资源版本仍 `20261120v171`（本批无资源改动）
- 七阶段完成。留下迭代候选：logs 分层+audit 并入（动导航三方契约，成本高）；右栏 seg-tabs 抛光；背景细网格纹理（原型 :103 有）；或所有者新指示。并行会话遗留红清单见证据 P7 归因（迁移 pin v2_0039→v2_0049 漏更新 9 例等），归其处置。

## 每阶段统一收尾（必须全部执行）
1. 整组升版本串：`sed -i 's/20261120v171/20261120vNNN/g'` 应用于 `web/ui/v2/*.js web/ui/v2/index.html` + 两份提示文档 + `docs/evidence/v2-ui.json`（**开工前先 grep 当前值防并行会话已升**：`grep -o "20261120v[0-9]*" web/ui/v2/index.html | head -1`；evidence 只升 `resource_version_final` 当前版本字段，历史条目的「版本」保留原值）。
2. `docs/evidence/v2-ui.json` 追加 `ui_iteration_pN_xxx` 键（需求/交付/验证/截图/版本）。
3. 新增/删除仓库文件后跑 `scripts/project_index.py`——必须用纯树法避免混入他会话未提交代码：
   `TREE=$(git write-tree) && rm -rf temp/build/_idx_tree && mkdir -p temp/build/_idx_tree && git archive "$TREE" | tar -x -C temp/build/_idx_tree && uv run --no-sync python scripts/project_index.py --root temp/build/_idx_tree --output temp/build/_idx_tree/docs/index && cp temp/build/_idx_tree/docs/index/MODULES.md temp/build/_idx_tree/docs/index/project.json docs/index/ && git add docs/index/MODULES.md docs/index/project.json` 单独 chore 提交。
4. `git add` 只加本阶段文件（工作区常有其他会话的 engine/capabilities/files_search 等未提交修改，严禁 `git add -A`），独立提交并 `git push origin v2`。**并行会话可能在本阶段同一文件里留未提交结构 WIP**（P5 期间 index.html 混入 #approvals 移入 chat、#model-live-status 删除+model-output.js 配套）：提交前逐 hunk 甄别（prettier 只改格式不会移动/删除元素），构造提交版=工作区−他会话 hunk，提交后再把备份恢复回工作区保留其 WIP。

## 硬约束（违反即测试红）
- 断点白名单仅 900/760/520（+901/761 互补 min）；字号 ≥10px（含 `font:` 简写）；hex 色值只准登记色且**注释里出现也算违规**；圆角/blur/seed/glass 全走 `web/ui/tokens.css` 单源 token；`var()` 不得悬空；light 块重定义 23 核心 token 且不得定义 `--glass-*`。守卫：`agent/tests/test_ui_glass_tokens.py`、`test_ui_design_tokens.py`（每阶段必跑）。
- 导航三方契约（本迭代不动导航；若必动）：index.html nav+section 成对、`test_v2_navigation_ui.py` GROUPS(8组)/count(24)、`test_v2_business_inventory.py`、`docs/v2/contracts/V2_BUSINESS_INVENTORY.md`；`<nav aria-label="工作台" inert>` 原文不能加 class；新按钮可访问名不得与既有页名撞 `exact=True`。
- `web/ui/v2/v1/` 是 V1 CSS 原样副本：改其声明须附注释理由并过全部全局守卫（有 min-height 先例）；V2 专属视觉只改桥接层 `web/ui/v2/workbench.css`；V1 原件 `web/index.html`、`web/ui/*.css`、`web/ui/app/*` 只读禁改。
- Playwright 测试触碰折叠/隐藏内控件前必须展开：用 `agent/tests/omni_test_support/v2/ui.py` 的 `reveal_settings(page)`（evaluate 打开 `#settings details`）；新折叠区块同此约定加对应 helper。不放宽任何断言、不缩超时。
- 切页入口一律 `nav[aria-label="工作台"] [data-page="x"]`，裸 `[data-page]` 会匹配 body 或页内快捷按钮（「记忆」已改名「记忆系统」，旧 `get_by_role("button", name="记忆", exact=True)` 已全部替换为 `nav[aria-label="工作台"] [data-page="memory"]` 限定写法——遇到同类旧选择器就照此修）。
- Chrome 156 注意：收起的 `<details>` 不再 `display:none`，量可见性用 `el.checkVisibility()` 而非 getBoundingClientRect。
- 版本纪律：任何 HTML/CSS/JS 改动整组升 `?v=`；mcp 测试版本断言已动态化无需改。
- Playwright+CSP：页面 `script-src 'self'` 无 unsafe-eval，`wait_for_function` 的轮询包装被拦——改 `evaluate` 轮询助手；`request.post_data_json` 是属性不是方法。
- 既有初始化竞态（非缺陷、守卫按设计工作）：`selectAgentBody` 尾段（main.js ~1598）会对当前页补一次 load，比切页 handler 晚约 0.5–1s 渲染；切页后立刻以「状态文本出现」为等待条件会被这次延迟 load 提前满足，此时点归档等确认类按钮，epoch 守卫会在对话框期间失效中止（空间或计划已变化）。实测脚本先显式「刷新计划列表」+expect_response+稳定窗再操作；真实测试用 expect 等渲染值且操作间隔充足，不命中。
- 必需 select + 异步选项序竞态（P6 真缺陷已修）：折叠/选择器选项由异步 fetch 填充时，**先填选项再赋 value**；若赋值早于填选项且 fetch 被 epoch 守卫中止，value 落空会被 `required` 静默拦提交（无报错无状态）。deliveries load 尾已改为 await syncMessages 后 renderDraft。
- 嵌套 details 会打破测试计数/strict 选择器：工具卡内再套 schema 折叠后 `#list details` 计数翻倍、`card.locator("summary")` 双匹配；测试改 `> details` / `> summary` 直子级（断言语义不变）。
- P4 遗留红：test_v2_routing_ui.py 曾漏 reveal_settings 导入（P6 补齐）；接手先跑四专项确认基线，别把既有红当本批回归。
- **Windows 注册表 ProxyOverride 环境陷阱（P7 首跑作废根因）**：注册表 ProxyOverride 被 `urllib.getproxies()` 读成 scheme `no` 的代理项，httpx 0.28.1 解析其 IPv6 覆写列表抛 `InvalidURL: Invalid port: ':1]'`，任何默认 `httpx.Client()` 构造即炸（V1 web/UI 组全灭）。本机跑全量/涉网测试一律加 `NO_PROXY="*"` 前缀隔离（env 非空即跳过注册表）；不改测试断言、不改产品 trust_env。
- pytest 缓存归因陷阱：`.pytest_cache/v/cache/lastfailed` 保留未被本次收集的历史失败（含 xuexi 等旧路径），且会被并行会话的运行覆盖污染；归因以本次运行日志为准（全量输出落盘再分析，别只 tail）。
- 并行批既有红（P7 勘定，归并行会话处置）：迁移测试 9 例仍 pin `v2_0039` 而 head 已 `v2_0049`；service_registration powershell 参数 6 例；todos/pruning/reference_release/android×2/media_staging×2 行为漂移；installer 隔离 python 1 例；V1 project_catalog 1 例（docs/index 置 HEAD 仍红）。

## 演示与验证环境
- 演示实例：`uv run --no-sync python -m omni.composition.cli --home temp/build/v2-demo-home serve --port 8790`（后台跑；就绪判定 `socket.connect_ex(('127.0.0.1',8790))==0`；重启需先 taskkill 旧 PID）。agent id `1b51394c-aebe-48fc-ba31-25e87f5b1b6a`，OpenCode Go 真实档可用（跑真实调用费用极小）。改后端需重启才生效；改前端只需刷新。
- 实测脚本放 `temp/build/`（参考 `_live_chain_check.py`/`_actual_tokens_check.py`/`_spin_probe.py` 模式：sync_playwright + 断言清单 + PASS/FAIL 汇总 + 截图到 `temp/build/v2-demo/`）。
- 测试命令一律 `uv run --no-sync pytest -q -n 0 <文件>`；全量只在 P7 跑一次；不同时启动多个 pytest；不为跑测试停用户服务。

## 关键文件速查
| 文件 | 行数 | 角色 |
|---|---|---|
| web/ui/v2/index.html | ~2110 | 24 页面分区；dock 区 ~331-390；settings ~1400-1660；schedules ~1882-1947（向导+高级折叠） |
| web/ui/v2/main.js | ~2260 | 页面逻辑：renderApprovals ~1196、renderMessages、dock-more/fab 初始化文件尾 |
| web/ui/v2/workbench.css | ~4100 | V2 桥接层（P1-P5 新样式都在文件尾部分节） |
| web/ui/v2/schedules.js / deliveries.js / configuration.js / credentials.js / models.js | 523/371/550/185/672 | P6 改造对象 deliveries；models.js 是简化范本；schedules.js 含向导 compile/parse/sync |
| web/ui/tokens.css | ~265 | 唯一 token 源（--sp-*/--fs-title/--fs-headline 已加） |

## 最近提交
`6690b59c` P6 选择器+白话(v171) ← `f147ccbb` P5 向导(v170) ← `23c31a0d` 会话标题省略号(v169，并行会话) ← `741a8aff` P4+审批卡(v168) ← `fc83ee77` P3(v166) ← `b74b85ac` P2(v165) ← `8297836b` P1(v164)。此前：`2dc8d887` 图标缺字形修复、`6bcb0e98` 调用链实时化、`a73ab321` 去估算显示实际 token。

## 未验收声明（不得当成已完成）
真实平台账号/72小时稳定性/三平台原生未验收；全部验证基于离线演示实例+本地 OpenCode Go 真实档。P7 全量已跑（NO_PROXY 隔离）：8192 passed / 25 failed / 0 errors，25 例均非本迭代回归（归因见证据）；全量全绿仍待并行批遗留红处置后复跑。

先读 `docs/HANDOFF.md`、`docs/ARCHITECTURE.md`、`CONTRIBUTING.md` 与 `docs/history/v2/V2_UI_PROMPT.md`（§3 契约、§4 踩坑 21 条）。七阶段已完成；从迭代候选或所有者新指示开始，涉网测试记得 `NO_PROXY="*"`。
