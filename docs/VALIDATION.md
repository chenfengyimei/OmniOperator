# Windows 验证与离线评测（D26）

默认 `uv run pytest` 全离线。`tests/conftest.py` 禁止未替换的模型/embedding 传输，
Python TCP/UDP 连接只允许回环，非本地 DNS 查询拒绝。Python 子进程继承 sitecustomize；
闸门命中写入本轮临时证据，即使业务捕获异常也让测试失败。此机制防止测试意外出网，
不是恶意代码沙箱；Chromium/其他原生进程需在具体测试中拦截网络（B站与工作台测试均如此）。
真实设备/账号测试走 `scripts/manual_*`，不混入默认套件。单纯 skip 不计通过。

## 本地与 CI

```powershell
# 新的 Windows checkout：uv 已安装，允许安装锁定依赖与 Chromium。
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/ci.ps1
# 常驻主环境不重建已被锁住的入口 exe：
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/ci.ps1 -SkipSync
```

入口执行 `uv sync --frozen`、Chromium 安装、compileall、全套 pytest 和三轮核心场景。
产出 `.pytest_cache/ci/pytest.xml`、各轮 JUnit/log/report。Windows 是主支持测试环境，
不能据此宣称 Linux/macOS 全功能通过。静态检查目前覆盖语法与架构，未启用全仓严格类型检查。

仓库提供 Windows `Jenkinsfile`，可在用户的 Jenkins 上配置 Gitee SCM 与 Windows agent、
安装 uv 后执行。语法依据 [Jenkins Declarative Pipeline 文档](https://www.jenkins.io/doc/book/pipeline/syntax/)。
**当前没有已接通的托管 CI 运行证据**；配置文件与本地验证不能当成 CI 已上线。

架构守卫覆盖静态/相对/常量动态导入、导入与构造别名、模块属性构造、跨能力域依赖、
不可静态解析的动态执行与重复定义。它是保守 AST 检查，不是完整 Python 数据流证明。
`system` 仅允许配置和平台内部依赖；memory 对 system 只允许共享 atomic/filelock。

## 冻结场景

`tests/fixtures/core_scenarios_v1.json` 固定 24 个场景，每个绑定可执行契约，涵盖记忆、
上下文、会话隔离、取消、审批、委派、费用、文件冲突、技能、cron、outbox、MCP、
安卓、B站、桌面、向量遗忘与恢复校验。

```powershell
uv run --no-sync python scripts/eval_core.py --output .pytest_cache/core-本次唯一目录
```

先预热一轮，再用同一 `scripted-fixtures-v1` 配置重复三次；任何失败、skip、缺失或超时
立即保留现场并返回失败。报告记录数据集 hash、源码版本/是否有未提交修改、环境、
每个场景结果/耗时、预期 ok/error/unknown/denied/cancelled 分布及需人工审批的场景数。
这里“通过”指**契约验证通过**，包括确认失败与拒绝行为正确。模型 usage 是测试夹具数据，
真实 token/费用和真实任务成功率均为 null，不能把契约通过率称为模型智能水平或 Hermes 对比。

首轮预热后 24×3 契约全部通过，场景耗时中位数 0.116 秒、P95 1.104 秒、最大 4.027 秒。
证据见 `docs/evidence/core-offline-v1.json`（记录候选源码、dirty 状态）。这些是本机测试
过程的样本值；真实模型延迟、72 小时 RSS/句柄增长门槛需要 D27 预热后单独冻结。

## 日志与诊断

`data/system_logs/events.jsonl` 仅记录 run_id/session_id/event_id、事件类型、工具名、
状态和错误码，不复制对话、工具参数、完整结果或凭据。每文件 2 MB，最多 3 个备份，
14 天到期文件在下次写入时清理；跨进程文件锁轮转。DB 仍是权威账本，日志失败不改执行结果，
doctor 暴露丢弃/失败计数。

由新 ServiceManager 启动的 QQ/心跳 Python stdout/stderr 同样受容量与保留期控制，
按完整行脱敏常见 token、Authorization、Cookie 和 URL 参数；不继承占用日志的文件句柄。
原生库直接写 OS 标准句柄会进入 NUL；Python 入口执行前的导入错误也不保存，依赖问题用
doctor 定位。**NapCat 自身/旧版服务日志由外部程序管理，暂不承诺其持续大小受控**。
手动控制台不自动重定向。无磁盘空间/锁冲突时保留 DB 事件并报告日志丢弃。

doctor 额外聚合最近 1000 次运行、2000 个事件中的状态/错误码/工具结果，显示活跃运行、
审批等待、review 队列。窗口计数不是历史总量；诊断不导出原始 run error_text 或私人会话。
