# OmniOperator 当前交接入口

> 当前正在实施V2底层重构；先读 [V2实施记录](V2_REFACTOR.md) 和 [V2任务台账](tasks/v2.json)。默认入口仍为V1，不能将新基础批次称为完整迁移。

项目统一整改以 [当前状态](DEVELOPMENT_STATUS.md) 和 [统一规则](UNIFICATION.md) 为入口。
架构唯一权威是 [ARCHITECTURE.md](ARCHITECTURE.md)，界面规范是 [UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md)。

1. 阅读当前状态，确定任务范围与依赖；每批只修改该任务所需内容，保留既有未提交成果。
2. Agent 后端在 agent/，前端源码在仓库根 web/；根 src/tests 是本机兼容联接。
3. config/data/.env/skills 是既有运行位置，不因重排源码而迁移。
4. 使用 `uv run --no-sync`，不要为测试停止用户服务或重建正在使用的虚拟环境。
5. 专项覆盖修改及调用方；任务收尾运行一次全量，默认离线，不将专项写成全量。
6. 新增/删除文件后运行 `uv run --no-sync python scripts/project_index.py`，提交前检查 `--check`。
7. 支付、发布、群发、加好友与无人值守行为继续遵循既有审批和 fail-closed 规则。
8. unknown 不等于失败，不自动重发；数据库使用一致快照，不能只复制 db 而漏掉 WAL。

历史能力、平台坑与各批次证据完整保留在 [历史交接](../temp/archive/docs/2026-11-01/HANDOFF.md)。
[jilu](jilu/) 是历史来源，不能替代当前任务状态；历史测试数只代表对应源码版本。
