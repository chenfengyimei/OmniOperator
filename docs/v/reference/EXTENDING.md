# 怎样给 Agent 增加能力

| 我要让 Agent 做什么 | 使用机制 | 所有者入口 |
|---|---|---|
| 记住一套可重复的方法，修订已有步骤 | 文本技能 Skill | 工作台 → 核心系统 → 技能系统 |
| 在本机用新代码提供一个工具 | 可执行插件 Extension | 工作台 → 工具与扩展 → 可信插件 |
| 使用另一个服务提供的工具 | MCP | 工作台 → 工具与扩展 → MCP |

在仓库根运行 `uv run --no-sync python scripts/project_index.py --feature 技能`、`--feature 插件` 或 `--feature mcp`，可定位对应接口、应用服务、存储、组装和专项命令。独立 V1 社区插件另查 `--feature v1-plugins`。

源码维护者用 [V2 能力插件与组装](CAPABILITY_PLUGINS.md) 挂载内建能力，核对模型与所有者入口及关闭归属。

## 技能的创建、复核和回滚

1. 在技能系统导入 Markdown、ZIP 或编辑正文。Agent 在对话中可以提议使用 `skill_create`、`skill_rewrite`、`skill_patch`。技能写入沿用该回合的动作审批。保存后检查停用的修订、正文、资源和清单。
2. 所有者选择具体修订并启用。启用请求绑定修订号、修订 ID 和清单指纹。新回合选择已启用技能，按运行 ID 查看实际采用的版本。执行文件工具等动作时仍按该工具的授权规则办理。
3. 在版本历史中选择要恢复的版本并回滚。回滚生成新的停用修订，核对内容后重新启用。若工作台报告修订冲突，刷新详情并重新审阅。
4. 回合后复盘可以将技能修改暂存为候选。所有者用已认证的 `GET /api/v1/agents/{agent_id}/memory/reviews` 列出批次，再读取 `/memory/reviews/{batch_id}` 的候选和依据。向 `POST /api/v1/agents/{agent_id}/memory/reviews/candidates/{candidate_id}/decision` 提交观察到的 `revision`、`fingerprint`、`epoch` 及 `decision`（`apply`、`reject` 或适用时的 `undo`）。应用技能修改后，在技能页复核修订与启用状态。工作台的反思信笺另有独立的审阅流程。

[技能接口](../../../agent/src/omni/adapters/http_routes/skills.py) · [复盘用例](../../../agent/src/omni/application/background_review.py) · [复盘回归](../../../agent/tests/v2/core/test_v2_background_review.py)

## 试做一个可执行插件

[示例目录](../../../agent/examples/extensions/text-summary) 含清单和入口两个文件。工具 `ext__text_summary__summarize` 接受最多 4096 个字符，返回首个非空行（最多 120 字符）、非空行数和原文字符数。算法确定，可在无网络、无模型服务的环境复现。

1. **准备草稿。** 在 Agent 设置中选定工作区。所有者先在其中建立空子目录 `text-summary/`，现有 `files_write` 要求父目录已存在。可以给 Agent 下达下方任务，把示例两文件的文本随任务提供；分别审批写入，并保留返回的文件 SHA-256。

   > 请在当前工作区的 text-summary 目录编写一个本地文本摘要插件。按提供的 extension.json 和 plugin.py 写入，逐个请求文件写入审批。完成后报告市场目录、包目录、插件 ID、版本、公开工具名和两个文件的指纹，交给我审阅。
2. **扫描并登记。** 打开「可信插件」，输入**草稿的父目录绝对路径**作为本地市场目录。扫描直接子目录，选择登记该版本。核对工作区入口源码、清单中的工具及参数，然后核对登记详情里的包指纹、工具目录指纹和版本。扫描静态读取文件，登记捕获固定快照。这时工具仍未授权。
3. **选择工具并授权。** 勾选摘要工具，明确「信任并授权所选工具」。**Python 宿主拥有当前本机账户可用的权限。** 在这里审阅包内全部可执行代码。授权绑定当前包、工具目录、修订和历史代次。插件合同要求清单声明 `writes: true`，包括这个仅计算文本的示例。
4. **批准一次调用。** 开始一个新 Agent 回合，请它用 `ext__text_summary__summarize` 处理两行文本。核对参数后批准本次动作，宿主才执行已登记的代码。模型收到可解析的 JSON 文本，动作结果的 `data` 保留同一份结构化字段。例如输入 JSON 字符串 `"  第一行\n\n第二行"` 返回 `{"headline":"第一行","nonempty_lines":2,"characters":10}`。
5. **升级和停用。** 将清单版本改为 `2.0`；覆盖文件时传入当前文件的 `expected_sha256`。重新扫描，同一插件选择「登记为新版本」。原授权失效。重新核对新指纹、勾选工具并授权，再开新回合批准调用。停用后新回合无法调用该工具。停用期间如宿主显示正在回收，以实际回执为准。

### 在干净的临时实例离线重放

从已准备依赖的仓库根执行：

```powershell
uv run --no-sync pytest -q -n 0 agent/tests/v2/integrations/test_v2_extension_example.py
```

此用例以脚本化模型发起真实回合，执行文件工具、所有者 HTTP 接口、逐次人工批准的测试回执和本地 Python 子进程，校验两个版本的结构化结果。未授权、升级后未重新授权、停用后三次均主动请求同一工具并检查拒绝结果；另以旧修订和旧包指纹尝试授权，均应返回冲突。工作台按钮和反馈另由 [市场 UI 测试](../../../agent/tests/v2/ui/test_v2_extension_market_ui.py) 覆盖。

[清单格式](../../../agent/examples/extensions/text-summary/extension.json) · [入口代码](../../../agent/examples/extensions/text-summary/plugin.py) · [静态捕获](../../../agent/src/omni/adapters/extensions/packages.py) · [插件宿主](../../../agent/src/omni/adapters/extensions/worker.py)

## 接入独立 MCP 服务

在当前 Agent 的配置中建立 MCP 档案并选定传输方式。在 MCP 页打开连接，审阅服务身份和发现的工具目录，按服务与具体工具授予权限；使用 OAuth 的服务还需完成对应的回环授权。用新回合调用，再核对调用记录与断开回收。服务目录或配置发生变化时，按页面显示的修订和权限状态重新审阅。离线开发可使用注入传输验证目录、调用和撤销；真实服务互通单列验收。

[MCP 用例](../../../agent/src/omni/application/mcp.py) · [传输适配器](../../../agent/src/omni/adapters/mcp) · [调用流程测试](../../../agent/tests/v2/integrations/test_v2_mcp_flow.py) · [MCP 工作台测试](../../../agent/tests/v2/ui/test_v2_mcp_ui.py)
