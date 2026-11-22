# 贡献指南（CONTRIBUTING）

感谢你愿意为 OmniOperator（小鱼）做贡献。本项目是 Windows 上的常驻个人 agent，
**安全边界是产品的一部分**——贡献的代码不能为了"跑通"绕过审批、预算或路径边界。

## 0. 先读什么

1. [README](README.md) —— 定位、安全模型与模块地图；
2. [docs/HANDOFF.md](docs/HANDOFF.md) —— 当前交接入口，链接历史能力与平台坑；
3. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) —— 分层与组装根**唯一权威**；
4. [docs/DEVELOPMENT_STATUS.md](docs/DEVELOPMENT_STATUS.md) —— 当前进度与证据台账。

## 1. 环境

- Windows（当前验收平台；通用逻辑用 pathlib，平台差异隔离在能力域内部，不要把未验证平台写成已支持）；
- Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)：`uv sync --frozen`；
- 浏览器能力：`uv run python -m playwright install chromium`；
- 常驻服务（omni-web/omni-qq/心跳）运行时会锁住 `.venv` 里的入口 exe——此时一律用
  `uv run --no-sync …`，**不要为了跑测试停止用户服务**，也不要在服务运行中执行 `uv sync`。

## 2. 测试纪律（硬门槛）

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/<分组>/test_<模块>.py  # 开发中：相关模块与调用方
uv run --no-sync pytest -q          # 多模块集成收尾或所有者明确要求：全量离线测试，默认最多 4 进程
```

- 持续开发优先实现与受影响模块/调用方的专项验证，不为每个小模块或每次提交启动全量；全量留到多模块集成收尾或所有者明确要求。每批明确专项范围及当前源码是否被全量覆盖，失败优先重跑对应 nodeid。同一源码、测试、依赖和测试配置的全量通过结果可复用；代码变化后不能把旧全量当作新源码通过。详细命令与隔离策略见 [TESTING.md](docs/TESTING.md)。
- 默认测试套件**离线**：禁止真实付费、真实社交消息、真实设备动作；
- 测试隔离：任何落盘数据都要支持路径注入（`build_runtime(data_dir=…)`、
  `ServiceManager(run_dir=…, log_dir=…)`），不得污染真实 `data/`；
- 每个新模块必须有单测；`agent/tests/test_arch.py` 会用 AST 强制分层与组装根规则；
- `agent/tests/test_community_docs.py` 会校验 README 模块地图与 catalog 分类一致——
  在 `system/catalog/schema.py` 增删模块后，记得同步 README 模块地图；
- 新增/删除仓库文件后运行 `uv run --no-sync python scripts/project_index.py` 更新索引。
- 静态检查：`ruff check agent/src/omni`、`ruff format --check agent/src/omni agent/tests agent/scripts agent/hatch_build.py`；工具版本由uv.lock锁定。
- 前端：在`web/`运行`npm ci`和`npm run format:check`（Prettier固定版本），再执行`scripts/check_frontend.py`与UI契约守卫。
- `scripts/run_pyright.py`按已收口领域检查，失败返回非零；当前范围见脚本，不能称为全仓类型通过。

## 3. 架构纪律

- V2 采用 `domain → application → adapters → composition` 四层允许依赖集合，组装根在 composition，禁止导入 V1 运行时；目录与现行合同见 [V2 导航](docs/v2/README.md)。下列旧分层及 bootstrap 规则只适用于保留的 V1。
- 依赖方向自上而下：`channels`/`bootstrap` → `core` → `tools`/`llm`/`memory`/`safety` → 叶子层；
  改坏会直接测试红，不靠自觉；
- Gateway/MemoryStore/ToolRegistry/Agent 等组装根对象**只能在 `bootstrap.py` 构造**；
- 新能力接入（工具/通道/记忆/插件）按 [ARCHITECTURE §5](docs/ARCHITECTURE.md) 的 checklist 走：
  `tools/<域>/` 建包 → `register_xxx_tools(registry, deps)` → `bootstrap.build_runtime` 接线 → 单测；
- 工具风险分级：读=SAFE / 写=CONFIRM / 高危=DANGEROUS；写工具声明 `requires_human=True`，
  无人值守一律 fail-closed，"结果未知"的写操作不自动重试。

## 4. 代码与提交约定

- Python 3.12+ 全量类型标注 + `from __future__ import annotations`；
- 模块 docstring 写「职责 + 明确不做」；注释写"为什么"不写"是什么"；
- 前端无构建原生 ES 模块，纪律以 [docs/UI_DESIGN_SYSTEM.md](docs/UI_DESIGN_SYSTEM.md) 为唯一权威
  （改动 css/js 必须递增 `?v=` 版本串）；
- 提交信息：中文，`领域：要点——细节`，一条 commit 一个完整交付；
- 提交前自查：`git status` 逐文件核对改动归属（本仓库存在并行 AI 开发会话，
  **不要把不属于自己的在途改动卷进提交**）；
- **绝不提交真实凭据**：`.env`、`config/config.yaml`（真实白名单）、`data/` 均已 gitignore——
  演示/示例一律用占位值。

## 5. Pull Request 流程（Gitee）

V2 双设备的分工、分支同步与交接位置见 [双设备协作开发规范](docs/V2_MULTI_DEVICE_DEVELOPMENT.md)。

1. Fork 或建分支，一个 PR 聚焦一件事；
2. PR 描述说明改动、原因、受影响的调用方与专项结果；已执行全量时附被测提交和结果摘要；
3. 涉及行为变更的，先在 PR 里说明与 [docs/REFACTORING.md](docs/REFACTORING.md)
   兼容合同的符合性（HTTP 契约、公开 import、旧命令兼容）；
4. 维护者评审通过后合并；发布与对外宣传由所有者统一决策（见 [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)）。

## 6. 行为准则

参与本项目即表示你接受 [行为准则](CODE_OF_CONDUCT.md)。安全问题请走
[安全政策](SECURITY.md) 的报告渠道，不要开公开 issue 讨论。
