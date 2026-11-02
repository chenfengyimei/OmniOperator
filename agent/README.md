# 本地 Agent 产品

这里是 OmniOperator 本地 Agent 的Python产品源码位置（界面源码在仓库根web/）。跨会话接手先读
[交接](../docs/HANDOFF.md) 与 [架构规则](../docs/ARCHITECTURE.md)，完整目录规则见
[仓库结构](../docs/REPOSITORY_STRUCTURE.md)。

```text
agent/
  pyproject.toml       产品依赖、打包、omni/omni-web/omni-qq/omni-heartbeat
  src/omni/
    bootstrap.py       唯一组装根
    core/              Agent 内核、上下文、review、调度
    channels/          CLI/QQ/心跳/Web、终端与可视化界面
    tools/             文件、命令、浏览器、安卓、MCP 等能力域
    memory/            记忆、档案、会话和运行账本
    llm/               模型、embedding 与用量协议
    safety/            审批、风险与无人值守策略
    plugins/           插件宿主和扩展注册
    system/            平台原语、服务、目录与索引
    config.py          声明配置与路径锚点
  tests/               产品全部测试及隔离 fixtures
  config/              可提交的配置模板
  scripts/             产品校验、评测、诊断与开发脚本的正式实现
```

从仓库根执行 `uv run pytest`、`uv run omni-web` 等原命令仍然有效；也可
`cd agent` 后执行。锁文件与虚拟环境由根 uv 工作区统一管理，无须建立第二套环境。

用户配置、`.env`、记忆/资料/日志和运行技能仍由仓库根及已配置的 RuntimePaths 管理；
`agent/config/` 只有模板。代码目录重排不会迁移真实数据。安装/启动脚本在根 `scripts/`，
其中旧开发命令只有转发器；开发新能力修改这里的正式实现。
