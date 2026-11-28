# OmniOperator UI V3 仓库评估

日期 2026-11-27 · Phase V3-0 · Windows

## 基线与工作区

- 远端 v2 与 origin/v2 同为 d56edbd1。独立工作树 D:\xm\OmniOperator\temp\worktrees\ui-v3-clean-slate 使用同名任务分支，从该提交建立。
- 本地回滚标签 omnioperator-ui-v2-release-candidate-1 指向 d56edbd1；标签记录已提交的 V2 基线。原工作树 owa-tickets 位于 b94a6a95，包含其他会话的 V2 页面、设计记录、邮箱实现和校园助手测试等在途改动。Phase 0–5 记录作为盘点资料，当前标签的内容以 Git 对象为准。
- Phase V3-1 的原型从 WorkWeb/ 独立启动，使用明确标注的演示数据。方向确认后才进入 WorkWeb/ 的建设。

## 现有入口与打包依赖

| 入口 | 证据与影响 |
| --- | --- |
| V2 CLI | agent/pyproject.toml 的 omni-web 和 omni-web-v2 都映射至 composition.cli:web_main，支持用 --home 启动独立实例。omni-web-v1 单独保留。 |
| V2 静态服务 | composition/http.py 的 web_assets() 优先读取 wheel 中的 omni/web_assets，再找源码根 web/，要求 ui/v2/index.html。adapters/http.py 的 / 返回该入口。/assets/{path} 以同一个根解析，保留路径归属及链接检查。 |
| V1 与共享子站 | web/index.html 服务 V1。android、capabilities、jobs、mcp、memory、project、storage、terminal 各有自己的资源，同时引用 web/ui 下的公共脚本、样式、字体；存在 panel.html 嵌入入口。web/vendor 提供终端等资源。移动整个根会影响上述调用方。 |
| 发布链 | agent/hatch_build.py 从根 web/ 收录允许扩展名，分别生成 sdist 的 _web 与 wheel 的 omni/web_assets。释放、安装、准备、切换和冒烟均检查旧入口。切换阶段需要用同一候选逐项核对。 |
| 测试 | agent/tests/conftest.py 的 ui_browser 在同文件复用 Chromium，各用例建立独立 BrowserContext。V2 导航、Agent HTTP、OmniWeave 与 wheel 各有专项。产品验证仍沿用离线注入与隔离数据。 |

## 新前端的目录和接口方案

1. 确认方向后，在 V3 分支归档完整 V2 资源树到 web-v2-legacy/，逐项核对 V1、V2、共享子站与字体、脚本及终端依赖的归属。WorkWeb/ 独立使用原生 ES 模块、统一 API 客户端和类型化投影，样式令牌唯一来源为 WorkWeb/ui/tokens.css。
2. 开发入口使用单独的本地地址和隔离实例。需要访问真实 API 时，由同源开发入口转发至隔离实例，沿用所有者认证、错误回执及发送幂等规则。运行和事件按 agent、session、run 归属更新，迟到的响应按选择版本丢弃。
3. 在发布候选上为新旧入口和各自资源制定显式映射。同步验证源码启动、sdist → wheel、升级、回滚、V1、子站与默认根入口。轮到正式切换时，再修改静态资源装配和发布校验。涉及新增后端能力的需求走独立技术提案。
4. 布局沿用 900/760/520 三个断点、原生 dialog 和字号下限 10px。动态字符串用文本节点渲染。原型保留三方向各自令牌，确认后收敛为一套长期设计系统。

## 核心 API 盘点

后端产品代际为 V2，现行 HTTP 路由前缀是 **/api/v1**。路径以下省略这一前缀。

| 使用场景 | 当前契约 |
| --- | --- |
| 主会话 | /agents、/agents/{id}/sessions 及 session messages，OmniWeave 主会话绑定走 GET/PUT /agents/{id}/omniweave/main-session。Worker 私有会话不进入对话导航。 |
| 输入与运行 | POST /agents/{id}/turns 返回 202；以 runs、live-output、run events、agent events/stream 读取实际运行。取消使用 POST /agents/{id}/runs/{run}/cancel，之后继续观察最终状态。 |
| 任务与 Worker | /agents/{id}/omniweave/sessions/{session}/tickets 与 ticket-options 提供票据与公开任务投影；runs/{run}/delegations 提供已有子运行。当前票据公开任务投影没有依赖边、子运行 ID 或任务结果。 |
| 审批 | /agents/{id}/approvals GET 读取待审项、POST /approvals/{approval_id} 决策。动作核查和审计仍为各自的证据来源，显示状态和字段取决于具体投影。 |
| 文件与产物 | Agent objects、captures 和具体授权产物端点可按现有规则访问。OmniWeave 产物内容接口为附件下载，授权范围以服务端校验为准。 |
| 配置、工具、观测 | 依照现有功能路由装配与 OpenAPI，逐一核对 models、capabilities、extensions、MCP、memory、logs、audit 和业务模块调用方。 |

从 V2 Phase 0–5 的 26 页清单开始逐项迁移。工作台包含 chat、context。知识与产物包含 skills、memory、documents、reflections、persona-growth、objects、media。工具与连接包含 extensions、mcp、qq、bilibili、android、desktop、terminals、people。协作与管理包含 action-reviews、schedules、deliveries、models、settings、capabilities、tokens、logs、audit。现有独立子站单列路线与资源验收。

## G1–G6 与 A1–A7

| 缺口 | V3 呈现 |
| --- | --- |
| G1 任务与子运行归属、依赖边和结果 | 使用票据与独立运行记录。依赖图只消费显式边。任务级结果需要公开投影支持。 |
| G2 时长、G3 工具计数 | 仅呈现已返回的时间点、事件及工具记录；假定不可用的字段标未知。若展示事件跨度，标明口径。 |
| G4 Ticket 取消 | 取消按根运行，操作前显示可能覆盖的任务范围。 |
| G5 在线与并发、G6 展示角色 | 采用具体状态或 profile 标识及版本；派生统计标明数据来源。 |
| A1 审批风险等字段、A2 延后 | 使用作用对象和现有参数，缺少的风险或影响字段标未知。延后能力取决于独立服务端契约。 |
| A3 手动继续、A4 原运行重试 | 停止后保留已有证据和草稿。可重发时说明会新建 Turn 并核对副作用。 |
| A5 服务端队列管理、A7 位置与草稿 | 将本机草稿与后端已受理的运行区分。草稿、滚动位置按会话留在浏览器。 |
| A6 工具退出码、运行开始时间 | 明示日志或事件实际提供的字段。 |

## 协作与验收

DSH 0.2.2-rc.5 通过本机 headless CLI 开启同一工作树内的 workspace-write 会话。Codex 负责评估、独立浏览器核验及方向选择，DSH 负责编写方向与原型并根据回执修订。各自写入范围独立。

原共享技能根当前不可见。四个 Skill 从本机 sheji 快照读取，工作树中的副本只作为本次 DSH 输入。Agent Product UI/UX 定义执行状态、审批、焦点和滚动边界。UI UX Pro Max 的检索结果用于控制台密度、主题和排版；检索返回的营销式 Hero 和 CTA 结构不适用于常驻工作台。视觉验收使用 Playwright 实际启动原型，审美评审使用 Impeccable 的离线流程。

基线专项使用独立工作树源码和本机已有虚拟环境。V2 导航 2 项、OmniWeave 受理 2 项、根运行取消及 Worker 停止 1 项，共 5 通过，29.73 秒。记录仅覆盖上述专项。
