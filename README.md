# OmniOperator · 小鱼

运行在 Windows 上的个人 agent，QQ / Web 工作台 / CLI / 心跳共用内核。
Python 3.12+、uv、LiteLLM 网关、SQLite 记忆和运行账本。本地运行不表示模型请求不出机。

**2026-10-29 状态：受控内测，第二批 Web 领域重构完成；提交源码 952 项、工作区 999 项全绿。** 核心能力与 D27 验收工具已交付，
真实账号/设备/模型闭环和 72 小时常驻仍未全部通过；尚未发布稳定版。
macOS/Linux 全功能、通用桌面接管、视频上传均不属于已验证交付。

开发接手先读 [HANDOFF](docs/HANDOFF.md)，再读唯一架构权威 [ARCHITECTURE](docs/ARCHITECTURE.md)。
当前证据与剩余项见 [开发台账](docs/DEVELOPMENT_STATUS.md)，详细任务见
[完整计划](docs/LOCAL_AGENT_PLAN.md) 和 [开发大纲](docs/DEEPSEEK_DEVELOPMENT_OUTLINE.md)。

## 项目与模块导航

工作台顶栏 **项目与能力** 打开 `/project/`：模块分类、源码/符号/依赖、API 请求字段与调用链、
当前注册工具及参数、插件隔离/启用状态均可搜索查看。目录和调用约定见
[项目导航](docs/PROJECT_INDEX.md)、[模块索引](docs/index/MODULES.md)、[API 索引](docs/index/API.md)。
`uv run --no-sync python scripts/project_index.py --check` 检查生成索引是否过期。
工具按域归包；交互终端拆为 backend/session/pool/routes；插件社区拆为 client/tools/server。
旧 import 和启动命令保留兼容，分层规则仍只有 ARCHITECTURE 一份权威。
第二批将 Web 业务拆为 12 个领域路由、21 个共享请求模型；已发布入口从 1766 行缩到 405 行。
API 支持按所属模块筛选与跳转，重构过程和后续顺序见 [重构记录](docs/REFACTORING.md)。

## 开始使用

Windows 一键安装：双击仓库根目录的 `install.bat` 打开**图形安装器**，点「开始安装」；
完成后点「启动工作台」（或双击 `start.bat`），浏览器会自动打开配对链接。
模型密钥、QQ 白名单、B站 Cookie 等配置都在工作台「设置中心」完成；
镜像选项、失败降级与手动方案见 [安装器](docs/INSTALLER.md)。

手动安装（等价步骤）：

```powershell
# 首次安装；现有常驻服务占用 .venv 入口时，先按运维手册安排维护窗口。
uv sync --frozen
# 使用浏览器能力时安装 Chromium。
uv run python -m playwright install chromium

# 复制 .env.example 为 .env，配置实际要用的供应商密钥；
# 或在 config/config.yaml 选择已安装的本地 Ollama 模型。
# 启用付费/无人值守前，明确配置模型预算和所需权限。
uv run omni-web
# 使用控制台打印的配对链接进入工作台，密钥在设置中心填写。

uv run omni                # CLI
uv run omni-qq             # 需 NapCat 登录、配置 WS 与白名单
uv run omni-heartbeat      # 调度已配置的任务；写操作仍受审批约束
```

常驻主环境需要避免 uv 同步时，按 [运维手册](docs/OPERATIONS.md) 使用 `uv run --no-sync …`。
不要为了跑测试停止用户服务。`uv run omni` 是实际 CLI 入口名称。

QQ 私聊和群分别隔离，空白名单拒绝访问。主动 QQ 提醒默认关闭，需所有者明确启用并设置
额度与安静时段。B站 Cookie 可在工作台设置；安卓需 USB 调试、adb 和中文输入适配。
桌面先以应用白名单内的记事本临时内容试用。付费、发布、群发、加好友必须人工确认；
无人值守 fail-closed，结果未知的写操作不自动重试。

## 已交付能力与使用边界

| 能力 | 当前实现与说明 |
|---|---|
| 内核 | 工具循环/grace call、todo、运行事件、取消、受限委派、持久摘要、费用预留与结算；[架构](docs/ARCHITECTURE.md) |
| 记忆和技能 | 事实/档案/常驻记忆、作用域/额度/遗忘、异步 review、技能 CAS/历史/回滚与待审阅差异 |
| 本机与网页 | 受限文件读写、只读 shell 白名单与人工确认命令、公开网页抓取、产物与有隐私开关的图像输入 |
| 调度与 QQ | cron 编辑/时区/成功运行依赖/并行限制、持久 outbox 与回执、群场景隔离；[QQ 场景](docs/QQ_SCENARIOS.md) |
| 浏览器与 B站 | Playwright 所有者线程、预算与审批；B站读四件套、评论/私信草稿与受控 UI 发送；[B站写入](docs/BILIBILI_WRITING.md)（默认关闭，实站选择器待核验） |
| 安卓 | 观察绑定动作、锁帧坐标、设备互斥、焦点核验/输入法恢复、MJPEG；[安卓手册](docs/ANDROID.md)（真机待验） |
| Windows 桌面 | 原生 UIAutomation、观察令牌、窗口租约、后台语义动作；[桌面手册](docs/DESKTOP.md)（记事本首个真实闭环通过，第二应用待验） |
| 插件与社区 | manifest/隔离/人工激活、命名空间工具注册、启停卸载、社区客户端与参考服务器；[插件手册](docs/PLUGIN_SYSTEM.md)（实际部署待提供机器） |
| MCP | stdio、管理面板、schema 漂移撤权、人工确认；[MCP 手册](docs/MCP.md)（HTTP/OAuth 后置） |
| 混合检索 | Ollama/API embedding 双接口、sqlite-vec + FTS、异步索引与删除联动；[检索手册](docs/RETRIEVAL.md)（默认关闭，实际效果待评测） |
| 运维与验证 | 进程身份、有界关闭、只读 doctor、备份恢复、日志轮转、离线测试、证据与常驻检查；[运维](docs/OPERATIONS.md)、[验证](docs/VALIDATION.md)、[验收](docs/ACCEPTANCE.md) |

CLI 支持 `/new`、`/list`、`/use <id>`、`/switch <model>`、`/tools`、`/memory`、
`/skills`、`/doctor`、`/help`、`/quit`。工作台提供会话、进度与停止、审批、产物、技能编辑、
任务与待投递、服务、设备、用量和设置。

## 数据与验证

配置在 `config/config.yaml`，凭据在 `.env` 或专用本地配置；
`data/memory.db` 保存会话与记忆，`data/state.db`（v13）保存任务、额度、运行和迁移账本。
`data/run` 是服务启动身份，`data/system_logs` 是诊断日志；路径均可注入。
备份含私人记忆，默认不含 Cookie/.env/浏览器 profile，不应提交到仓库。

```powershell
uv run --no-sync pytest -q
uv run --no-sync python -m omni.channels.doctor
uv run --no-sync python scripts/rehearse_upgrade.py --run --output <不存在的私有副本目录>
```

默认测试使用隔离数据与离线模型，禁止真实付费、社交消息和设备动作。
Windows CI 入口为 `scripts/ci.ps1`，Jenkins 配置已提供，托管 CI 尚未接入。
固定 24 场景三轮通过是软件契约验证，不是模型任务完成率或 Hermes 效果对比。
正式验收按 [ACCEPTANCE.md](docs/ACCEPTANCE.md) 完成 15 项证据、30 分钟预热及 72 小时常驻；
发布闸门通过也仍需所有者明确批准对外发布。

学习资料在 `xuexi/`（不入库），包括 Hermes Agent 等；历史评估保留在完整计划中，
不能用最初的差距表覆盖最新交付状态。
