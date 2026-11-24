# 项目存储、工作目录与 Agent 空间

本说明覆盖整个项目的运行数据。分层与对象组装以 [ARCHITECTURE.md](ARCHITECTURE.md) 为准，Agent 文件中心原有合同见 [AGENT_STORAGE.md](AGENT_STORAGE.md)。

## 目录的三个层次

1. **固定安装与控制区**：源码、`config.yaml`、`.env`、目录注册表、工作台身份、服务 PID 和入口日志。更改业务数据根不会搬动这些路径，也不会遗失仍运行的服务身份。
2. **全局业务布局**：存储根、默认工作目录和 Agent 空间池。默认空间继承该布局，独立 Agent 在空间池内各有自己的目录。
3. **模块路径**：bootstrap 启动时从同一份配置快照生成冻结的 `RuntimePaths`，再注入记忆、技能、文件、后台命令、插件、浏览器、安卓、日志与备份。模块不能通过当前进程工作目录猜测保存位置。

源码位置由安装决定，不能在设置页移动程序。配置文件和模型凭据仍为整个安装共享，不承诺每 Agent 拥有独立模型配置、全局统一费用上限或操作系统沙箱。

## 设置与目录预览

工作台的设置入口或 `/storage/` 提供“全局与 Agent 目录”“当前 Agent 的文件”“存储用途索引”。

全局字段：

| 字段 | 设置含义 | 相对路径基点 |
|---|---|---|
| `application.data_root` | 全局业务存储根 | 启动控制数据根的父目录；生产默认是项目根 |
| `application.workspace` | 默认空间的工作目录，供文件工具、Shell 和终端使用 | 有效业务存储根 |
| `application.agents_root` | 独立 Agent 空间池 | 有效业务存储根 |
| `agents[].home` | 一个独立 Agent 的根；留空使用空间池/标识 | 有效业务存储根 |
| `agents[].directories.*` | 工作、文件、资料、记忆、技能、产物、缓存、日志、备份目录 | 该 Agent 的根 |

全局值留空继承已有安装布局；不将老用户的平铺数据自动迁移。显式切换到另一个业务根后采用下述分类布局。预览不创建目录，保存使用 revision CAS、跨进程文件锁与原子写。旧客户端若遗漏已经配置的 `application` 会被拒绝，避免默默重置全局设置。

```text
<新业务存储根>/
  workspace/                 # 若默认工作目录设置为 workspace
  files/                     # 用户文件与交付文件
  documents/                 # 参考资料：人工可上传，模型只读
  memory/
    memory.db                # 事实、档案、会话、摘要、FTS、向量
    MEMORY.md                # 常驻笔记
    history/                 # 常驻与技能修订
  skills/                    # 技能与参考文件
  runtime/
    state.db                 # 任务、费用、额度、产物索引、迁移
    file_history/            # 文件编辑回滚副本
    plugins/                 # 插件宿主、隔离包、社区配置
  private/                   # Cookie、浏览器登录态
  cache/
    asr/models/              # 懒下载的本地转写模型缓存
    asr/audio/               # 任务音频临时文件，成功/异常均清理
    android/                 # 安卓可再生缓存
  artifacts/                 # 产物与截图
  logs/{system,commands,mcp}/
  backups/
  agents/<id>/               # 每个独立 Agent 重复上述业务分类
```

默认全局工作目录留空时仍继承已有 `config.files.root` 或项目根；新 Agent 的工作目录默认为自身 `workspace/`。空间中不需要的目录按需创建。

## 长期记忆与档案的保存方式

| 内容 | 实际存储 | 管理入口 |
|---|---|---|
| 长期事实、用户档案、会话与消息、摘要、检索索引 | `paths.memory` 指向的同一个 `memory.db` | 记忆/档案/会话 API 与工具 |
| 常驻工作笔记 | `paths.resident`，通常 `memory/MEMORY.md` | 常驻记忆工具与页面 |
| 常驻笔记与技能的修订 | `paths.history` | 历史查询与回滚 API |
| 原始参考文件 | `paths.documents` | 文件中心人工上传；模型按文件策略读取 |
| 用户文件与交付结果 | `paths.files` | 文件工具和文件中心 |
| 自动任务、额度、账本等运行状态 | `paths.state` | 对应领域 API |

“档案”是记忆库中的结构化用户资料，不再另外生成一份容易失步的文本副本。原始文档属于资料目录，不会因上传就自动进入模型上下文。向量索引随原事实位于同一记忆库；切换目录不会隐式调用 embedding 模型。

用途索引只读冻结路径和用途声明，不扫描文件内容、不暴露凭据、不打开数据库。文件中心只开放当前运行空间的 workspace/files/documents；记忆库、配置、其他 Agent、登录态与内部目录不能通过目录设置混进公开文件根。不同 Agent 的持久目录互不覆盖。

## 生效、备份与已有数据

保存前展示完整有效路径并确认。保存只影响**下次启动**，当前运行实例继续用原来的数据库、终端根与文件区；界面同时显示“当前运行”和“下次设置”。全局变更要重启所有受影响入口。配置不会自动复制、移动、合并或删除数据。

建议操作：生成当前空间备份 → 校验目标路径 → 在新目录恢复和核对副本 → 配置目标业务目录 → 重启受影响入口 → 用用途索引核对实际路径。请保留旧数据直到核验完毕。**当前恢复命令输出兼容的平铺副本，不会自动按新分类布局重排**；恢复后可通过每个目录设置或显式运维路径定位副本，不能将“保存目录设置”视为“迁移完成”。

常规备份包括记忆库、状态库、常驻笔记、技能、修订历史、产物、命令输出、文件和资料，配置默认脱敏。它不包含工作区源码、`.env`、Cookie、浏览器登录态、插件包/社区凭据、缓存、MCP 日志或目录注册表；因此不等同于整个安装的灾难恢复镜像。固定控制区不随业务备份恢复而接管原有服务。

## 入口、环境与隔离

显式 `build_runtime` 参数优先；没有注入 `data_dir` 时使用 `OMNI_STORAGE_BASE`、`OMNI_CONFIG_PATH`、`OMNI_AGENT_CATALOG`、`OMNI_AGENT_ID`，再回落到安装默认值。Agent ID 的优先级是显式参数 → 环境 → 注册表 active。`db_path` / `skills_root` 是可信调用者的显式覆盖，优先于布局；文件中心不会开放这种覆盖能力。

生产注册表默认位于配置旁的 `agents.json`；注入 `data_dir` 的测试默认用该根的 `control/agents.json`，忽略机器的 Agent/config/catalog 环境选择，配置覆盖路径不得逃出注入根。配置损坏、未知 Agent、交叉覆盖、网络设备路径、目录穿越、junction/符号链接均拒绝，不回退到另一个真实数据空间。

服务子进程继承原控制根、配置源、注册表和启动时 Agent ID。切换业务根不会改变服务 PID 与入口日志的控制位置。入口读取 `.env` 时跟随 `OMNI_CONFIG_PATH` 所选配置树，既有非空环境变量仍优先。

`omni-doctor`、备份、升级副本演练和 72 小时采样使用同一纯解析函数 `system.agent_spaces.inspection.load_runtime_paths`，不为诊断构造 Runtime、不启动模型或服务、不创建数据库。它们支持 `--agent` / `--directory-catalog`；原 `--data-dir` / `--config` / `--skills` / `--memory-db` 保留。恢复操作不要求当前注册表有效。

```powershell
uv run --no-sync omni-doctor --agent writer
uv run --no-sync python -m omni.system.backup --agent writer --to D:\OmniBackups\writer-20261030
uv run --no-sync python -m omni.system.backup --restore D:\OmniBackups\writer-20261030 --into D:\OmniRestored\writer
```

## 接口与代码索引

- `GET /api/storage`：`application` 保存配置、`application_resolved` 下次全局布局、`running_application` 当前冻结布局、`running` 当前 Agent、`resolved` 各 Agent 布局、`restart_required`、`inventory` 用途索引、`runtime_paths` 与 `public_roots`。
- `POST /api/storage/preview`：接收 revision/catalog，返回有效全局与 Agent 目录，不写盘。
- `PUT /api/storage`：接收 revision/catalog/acknowledge，CAS 保存，当前 Runtime 不切换。
- 文件列表、预览、创建目录、上传沿用原接口，受原配对/Host/CSRF与路径策略保护。

`system/agent_spaces/layout.py` 负责纯路径解析与边界；`catalog.py` 负责配置模式、校验、原子 CAS；`inspection.py` 负责运维入口选择；`inventory.py` 负责用途声明。对象构造仍唯一位于 bootstrap；HTTP 和页面只消费注入的服务及冻结路径。

验证覆盖新旧布局、重启前后路径、跨 Agent 边界、显式测试注入、CLI 哨兵库、ASR 缓存注入/异常清理和 Chromium 全局表单/目录索引。实际测试数量与截图见本轮交接及证据；真实数据未迁移，真实模型和 72 小时验收仍独立待办。
