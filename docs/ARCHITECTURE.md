# OmniOperator 架构权威

本文规定当前职责、依赖和组装边界；当前进度见 [HANDOFF](HANDOFF.md)，目录规则见 [仓库结构](REPOSITORY_STRUCTURE.md)。

## 1. V2 四层

| 层 | 职责 | 允许依赖 |
|---|---|---|
| domain | 纯领域规则、合同、状态与校验 | domain；不执行 I/O |
| application | 用例、端口、审批、预算、任务与生命周期编排 | domain、application |
| adapters | 持久化、HTTP、模型、平台和受管 worker 适配 | 前述三层 |
| composition | 实例、CLI/Web 组装与关闭 | 四层 |

V2 不导入 V1 运行时；数据库访问只在 `adapters/persistence`。组装根在 composition，HTTP 通过应用用例访问业务，不自行组装另一套内核。

## 2. 业务与平台约束

- 单一 `omni.db` 与共享事务、来源/授权、审批、预算和恢复链是业务真值；目录整理不增建状态库或迁移数据。
- 写动作绑定观察和逐次许可。可能已派发而后验不明时保持 unknown，不自动重发；独立样例状态不改写生产回执。
- 取消等待不丢弃实际任务、辅助进程、描述符、锁或释放义务；实际清理完成前不释放归属。
- 原生代码留固定 worker 资源位置，辅助程序不打开业务库。构建和部署身份覆盖全部规定的源码；更换路径后需重新生成候选，旧证据仅属于原版本。
- 平台 Python 实现分 desktop、services、releases、runtime，Linux 桌面实现位于 desktop/linux；包导入不探测平台、申请权限或启动进程。
- 界面唯一源码是根 `web/`；sdist/wheel 资源由现有构建钩子收录。V2 界面与 V1 界面分别定位，不复制第二份源码。

领域细节按 [V2 合同与平台导航](v2/README.md)读取，历史实现过程见 [整理前架构快照](history/pre-layout-2026-11-18/ARCHITECTURE.md)。

## 3. V1 兼容规则

`core/channels/tools/memory/llm/safety/plugins/system`、`bootstrap.py`及既有 V1 入口保留。V1 分层、共享状态、浏览器所有权与组装约束继续按 [V1 详细架构](v1/ARCHITECTURE.md)执行，组装根为 bootstrap。目录整理不将 V2 引向 V1，也不迁移现有 V1 数据。

## 4. 守卫与新增能力

V2 依赖方向由 `test_v2_architecture.py` 守卫，V1 由 `test_arch.py` 守卫。按职责层实现新能力，接现有用例、持久化与组装链，补相关回归；平台实测与离线注入分别记录。

代码、测试或文档移动同步修正导入、资源路径、CI、类型覆盖、构建白名单、目录索引和交接。开发中运行受影响专项，整个 V2 开发冻结后统一全量。
