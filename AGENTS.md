# 开发与测试

先读 `docs/v/HANDOFF.md`、`docs/v/reference/ARCHITECTURE.md` 和 `CONTRIBUTING.md`。本仓库可能有其他会话的未提交修改；保留它们，只修改本任务所需内容。

Windows 与 Mac mini 按 [双设备分工与交接步骤](docs/v/HANDOFF.md) 在同一 `v2` 主线迭代。任务分支直接使用任务短名（如 `macos-helper-tcc`），创建和推送都使用同名分支，省略工具名或命名空间前缀。

项目介绍放在 `docs/README.md`。所有新开发文档、计划和交接统一写在 `docs/v/`。当前进度维护在 `docs/v/tasks.json`，运行 `uv run --no-sync python scripts/task_status.py` 再生状态页。旧文档归入本机 `docs/temp/`，该目录由 Git 忽略。

## 测试执行纪律

- 开发中每个小步骤只运行相关测试，跨模块改动需覆盖调用方。不能把专项通过写成全量通过。
- 小范围验证使用 `uv run --no-sync pytest -q -n 0 agent/tests/v2/<分组>/test_<模块>.py`，避免少量用例支付多进程启动、重复收集和导入的成本。
- 修复失败优先指定失败 nodeid；也可用 `uv run --no-sync pytest -q -n 0 --lf --last-failed-no-failures=none`，没有失败缓存时不要自动跑全量。失败重跑不代替新增行为的回归。
- 按所有者最新要求，持续开发优先实现与受影响模块/调用方的专项验证，不为每个小模块或每次提交启动全量；多个模块集成收尾或所有者明确要求时再运行一次 `uv run --no-sync pytest -q`。每批明确记录专项范围和当前源码是否已被全量覆盖。全量默认最多 4 个 worker，按文件调度；诊断并发问题用 `-n 0`，显式 `-n 8` 可调整并行度。
- GitHub Actions 仅在需要云端全量验证时手动启动 `V2 full validation`。日常提交、推送和 PR 的验证在本地执行。
- 同一源码、测试、依赖和测试配置已全量通过时，引用该次结果和耗时，不在每次文案修改、状态汇报或小步骤后重复全量。相关内容变化或存在未解决故障才重新验证；其他会话改了代码也不能复用旧结果。
- 不同时启动多个全量 pytest。不要为了跑测试停止用户服务或执行会重建运行环境的 `uv sync`；已有环境使用 `--no-sync`。
- 默认套件始终离线；保留网络/模型闸门，不跳过测试、不缩短被测超时、不放宽断言来获得提速。数据、设备和服务必须使用隔离注入。
- UI 测试优先使用 `ui_browser`（同文件共享浏览器进程），每个用例建立并关闭独立 BrowserContext，保留独立临时数据库。不要跨无关模块共享同步 Playwright 事件循环。

新增或删除仓库文件后运行 `uv run --no-sync python scripts/project_index.py` 更新索引；详细命令见 `docs/v/reference/TESTING.md`。

## UI/UX Skill 调度（Codex / DSH / 其他 Agent 共用）

本仓库的 UI/UX 设计、实现、评审与视觉验收统一使用共享技能系统。四个技能安装在多 Agent 共享目录
`C:\Users\cy\.agents\skills\`（Codex 与 DSH 均已验证可发现；DSH 以该目录为用户级技能根，Codex 以它为额外 skill 根）。
安装记录、版本与更新方式见 `docs/v/design/AGENT_SKILLS.md`，设计系统与验收标准见 `docs/v/design/`。

| 技能 | 职责 | 何时调用 | 何时不用 |
| --- | --- | --- | --- |
| `agent-product-ui-ux` | Agent 产品交互架构：主会话/Worker 状态、工具时间线、审批、Stop/Resume/Retry、队列、文件工作区、滚动与焦点 | 设计或评审工作台的交互结构与状态机 | 普通 CRUD 页面、营销页 |
| `ui-ux-pro-max` | 视觉方向、配色、字体、排版、组件与 Design System 生成 | 需要确定视觉方向或产出设计系统 | 只改文案、纯后端 |
| `impeccable` | 设计评审与精修（critique / audit / polish / typeset / layout / colorize / animate / distill） | 已有界面需要诊断与精修 | 从零搭建交互架构（先用前两个） |
| `playwright` | 真实浏览器运行、截图、交互与响应式验收 | 每次 UI 改动的最终验收 | 不用它做设计决策 |

调度规则：

1. 按任务阶段调用技能，不一次性注入全部技能文档；同一时刻最多展开两个技能。
2. 设计决策优先级：用户明确要求 > 现有产品架构与功能约束 > `agent-product-ui-ux` 交互规范 > 本仓库设计系统
   （`docs/v/design/MASTER.md`）> 通用视觉风格建议。
3. 禁止多个技能并行产出互相冲突的设计系统；冲突时按上述优先级取舍，并在交付说明中写明。
4. 技能产出必须服从本仓库既有约束：无构建、token 单源（`web/ui/tokens.css`）、断点只有 900/760/520、
   字号下限 10px、原生 `<dialog>`、无内联 `on*=`、模型输出必须转义。
5. 视觉验收按 `docs/v/design/UI_REVIEW.md` 执行；截图只存本机（`output/` 或临时目录），不上传、不入库。
   Codex 的默认沙箱里浏览器起不来（`playwright-cli open` 报 `Session closed`），截图由 DSH 或宿主终端执行；
   `playwright-cli` 禁止 `file:` 协议，必须截 `http://127.0.0.1:8766` 这类本地服务，且本机需 `--browser msedge`。
6. 安全边界：不启用 `impeccable` 的 hook 与 Live/Apply 高权限路径；不关闭任何 Agent 的审批与沙箱；
   不修改业务逻辑、Agent 调度、Worker 协议、数据库与 API。
7. 技能更新使用 `scripts/install-uiux-skills.ps1`（可重复执行、默认不覆盖已有目录），更新后重跑
   `docs/v/design/AGENT_SKILLS.md` 中的验收命令。
