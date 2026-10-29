# 架构重构记录与后续顺序

架构规则唯一权威是 [ARCHITECTURE.md](ARCHITECTURE.md)，目录导航见
[PROJECT_INDEX.md](PROJECT_INDEX.md)，具体提交与验证见 [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md)。
重构逐批落地，每批包含兼容验证、索引和文档，不以搬文件数量判定完成。

2026-10-30 存储基础层：`system/agent_spaces` 按 layout（纯解析）、catalog（CAS 配置）、
inspection（只读运维选择）、inventory（用途声明）分类，统一全项目和 Agent 路径。
Runtime/CLI 共用解析，HTTP 不构造仓储，文件工具按实际私有路径保护；记忆和档案仍共用现有数据库真值。
本批不搬动源码、真实数据库或服务控制文件；详细结构与兼容约定见 [STORAGE.md](STORAGE.md)。

## 已落地的职责边界

| 批次 | 具体改动 | 兼容边界 |
|---|---|---|
| 934618f：模块归包 | personal/skills/scheduling/delegation/system；终端 backend/session/pool/routes；社区 client/tools/server | 保留原注册入口、主要公开 import、工具名和终端 WS；新代码用正式能力包 |
| 本批：Web 领域路由 | sessions/conversation/runs/memory/skills/artifacts/approvals/mcp/operations/automation/publications/devices | 保留 API 方法/路径、输入校验、响应与审批；生命周期与统一鉴权仍在应用入口 |
| 本批：请求契约 | schemas.py 统一 21 个原有请求模型；web.py 显式转发旧名字 | 已发布 OpenAPI schema、参数、requestBody、response 与 operationId 不变 |
| 本批：索引与查询 | 各领域独立分类；请求契约与聚合器分别分类；API 可以按所属模块筛选并跳转 | 真实路由仍与 OpenAPI 对照，静态声明仍与本进程注册能力区分 |

本批 `web.py` 的已发布代码从 1766 行减到 405 行；共享工作区另有配对与密钥修改，
该工作区入口从 1816 行减到 453 行。并行修改保留在工作区，不作为本批交付内容。
剩余配对/设置接口与生命周期依然清楚可见，不需要新增功能时继续向这个入口堆业务。

## 验证合同

`tests/fixtures/web_api_v1.json` 从 **已发布 934618f 的匹配源码**、独立临时运行态中提取。
只生成 OpenAPI，不调用模型、设备、外部服务或真实目录。冻结 90 个 HTTP 操作和 23 个 schema。

- 旧接口的方法、路径、参数、请求/响应模型、operationId 必须保持；新的接口可以增补。
  OpenAPI tags 用于改善分组，属于本批允许变化的界面元数据。
- 12 个领域都复用配对与 Host 检查；写方法额外遵循原有 Origin/CSRF 规则，读取方法不另造策略。
- 普通/流式对话继续复用会话闸门、跨进程租约、运行账本和协作取消；SSE 格式与断开行为由原测试覆盖。
- 创建两个应用必须使用各自 Runtime 与库，不能用模块级捕获的单例路由保存会话状态。
- `/api/skills/pending` 等静态路径优先于 `/{name}`；查看草稿不触发发布；设备动作保持原观察绑定。
- AST 守卫拒绝领域路由反向导入 web.py，也拒绝请求契约导入运行态；核心对象仍只在 bootstrap 构造。
- 全量 pytest、目录索引 --check、目标目录类型检查和真实 Chromium 导航验证共同记录。

OpenAPI 合同检查接口形状；既有业务用例仍须运行，它不能代替实际模型、账号和设备验收。

## 后续按价值推进

| 顺序 | 代码证据与目标 | 必须保留的验证 |
|---|---|---|
| 1 | memory/state.py 约 1600 行：先分离迁移/schema 声明，再考虑按账本职责拆分薄门面 | schema 升级/拒绝降级、短事务、跨进程 claim、费用与额度原子性、只读恢复 |
| 2 | plugins/host.py 约 800 行：包读取/类型校验、隔离状态、受信装载生命周期分别维护 | 校验零执行、激活人工确认、前缀注册、防篡改、失败回滚、原子状态与锁范围 |
| 3 | Web 配对/设置和主 index.html 有并行工作：合入后再整理独立 settings/auth 适配及前端专题资源 | 配对/密钥三态与脱敏、Host/Origin/cookie、原有操作入口；不能覆盖并行改动 |
| 4 | Runtime 的生命周期与状态查询继续按真实依赖收窄；组装根保持唯一 | 路径注入、关闭预算与残留、会话作用域、跨入口一致性，不提前增加空接口层 |

这些是后续候选批次，尚未实施。每批先核实工作区归属和合同，再改一个职责边界。
真实设备/账号/模型、72 小时常驻和对外发布依然按 [ACCEPTANCE.md](ACCEPTANCE.md) 单独验收。

## 能力完善 C01–C06

在原分层上实现人工终端会话、后台命令仓储/执行器、本地 MCP 有界管道与队列、
按设备的安卓观察/应用操作/共享帧；专题前端分别维护，统一身份仍归 Web 应用入口。
state 主体与插件 host 的后续职责拆分仍未实施，不因本轮能力交付而标完成。
