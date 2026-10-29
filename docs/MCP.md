# 本地 MCP 工具接入

OmniOperator 通过已安装的官方 Python MCP SDK 接入 **stdio** 服务。学习 Hermes 的
按需发现、环境变量白名单和会话所有权设计，保留本项目的 Registry 与人工审批边界。
本轮不提供 HTTP/SSE、OAuth、资源订阅、自动安装或服务器请求模型推理的能力。

## 配置

在 `config/config.yaml` 加入以下配置，换成自己审核过的服务可执行文件及工具名，然后重启使用它的入口：

```yaml
mcp:
  servers:
    local-notes:
      enabled: true
      command: "D:/tools/notes-mcp/.venv/Scripts/python.exe"
      args: ["D:/tools/notes-mcp/server.py"]
      cwd: "D:/tools/notes-mcp"
      timeout_s: 30
      env:
        NOTES_TOKEN: "${NOTES_MCP_TOKEN}"
      allowed_tools: [search_notes, update_note]
      read_only_tools: [search_notes]
```

`command` 是可执行文件，参数逐项放 `args`，不会经过 shell 拼接。未配置 `cwd` 时使用
项目根目录，相对 `cwd` 也以项目根目录为基准。默认没有任何外部服务，不会启动子进程。
如果服务不需要 token，可省略 `env`；需要时把值放 `.env`，配置里仅引用 `${变量名}`。
缺失变量使连接失败；不会自动透传 LiteLLM 等其它凭据。Python 子进程默认启用 UTF-8。

`allowed_tools` 省略表示所有已发现工具都可申请批准，空数组表示全部禁止。
`read_only_tools` 必须由用户审核确实无副作用后配置，并且是 `allowed_tools` 的子集；
它赋予无人值守调用权限，不能根据服务自报的 `readOnlyHint` 自动生成。

每次实际调用前重新发现目录并检查授权指纹。命令、参数、工作目录、环境配置、白名单或
参数 schema 变化会撤销旧 SAFE 授权；失败或授权库不可用同样停止只读调用。到工作台查看
完整参数 schema 后按当前指纹确认；旧页面不能批准后来变化的目录。首次配置显式只读
白名单后建立基线；从旧的仅 schema 指纹升级后需要重新确认一次。逐次人工批准的调用
不会顺带恢复整个服务的 SAFE 权限。

环境过滤不是操作系统沙箱。已配置的服务器仍以本机用户身份运行，可访问该用户的文件，
因此只配置自己信任的程序；模型不能通过这些工具安装服务或修改命令、环境变量和权限。

## 使用

1. `mcp_list_tools({})` 列出已启用服务，此时不启动进程。
2. `mcp_list_tools({"server":"local-notes"})` 建立并复用会话，返回工具名、`inputSchema` 和审批要求。目录支持 `offset` / `limit` 分页。
3. `mcp_read_tool({"server":"local-notes","name":"search_notes","arguments":{"q":"日报"}})` 仅允许配置中的只读工具。
4. `mcp_call_tool({"server":"local-notes","name":"update_note","arguments":{...}})` 一律人工确认。Web 会显示完整调用参数；smart 不代批，QQ/心跳拒绝。

所有接入工具均属于 `mcp` 工具组。外部工具描述与返回值是不可信数据，不能授予权限；
结果最多保留 12000 字符，不自动读取链接或转送图片/音频 base64。服务器 `isError` 会
明确呈现为错误；不把外部异常中的密钥或原始载荷回传模型。

## 生命周期与故障

每个服务一个持久会话和所有者线程，同一服务的调用串行化；并发入口不会交叉退出
AnyIO 取消域。连接、发现、调用均有时间预算，最多接受 16 页 / 256 项的目录。

超时或连接失败会停用该连接并回收子进程，**不重放失败调用**；仅后续显式请求可按 5/10/20/40/80 秒有界重连，5 次用尽后须人工 reset。写入结果可能
未知，应先核对外部实际状态，再手动重连。旧连接未确认退出时禁止替换；管理器进入关闭状态后禁止 reset 或新调用。服务器自己的 `isError` 返回不会断开连接。

stderr 日志在 `data/mcp_logs/<服务名>.log`；`build_runtime(data_dir=...)` 可完整隔离
日志与其它运行数据。正常进程退出有回收钩子，Web lifespan 退出也会关闭 MCP；嵌入式
调用方应在结束时调用 `runtime.close_mcp()`。强制终止宿主进程不等同于正常退出。

验证使用临时目录中的真实 JSON-RPC 子进程，覆盖中文、分页、会话复用、并发串行、
凭据隔离、人工审批、超时、异常退出、正常退出与 Web 收尾；尚未连接用户的外部生产服务。

实现依据：[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)、
[MCP 工具协议](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。

## C04 本地 stdio 完善

独立页面 `/mcp/` 提供被动状态/诊断、显式连接检查、断开、reset、完整分页目录、
目录指纹复查及有限日志。只读授权只由用户白名单与当前指纹决定；配置仍在 YAML，
配置与当前实例不一致时停止新调用，需从原入口重启，不热修改命令或权限。

每服务 max_waiting 默认 8；等待与握手/发现/调用共用请求总预算。
排队超时/取消保证不派发工具调用，不能取消同服务正在执行的另一请求；在途取消/超时
封闭连接并报告 unknown，不自动重放。服务代际拒绝旧目录授权和旧成功回调覆盖新状态。
父回合取消从注入 RunRef 传递，协议层不引用 core。

SDK stderr 通过持续排空的管道落有限轮转文件：log_bytes 默认 5MiB，log_segments 默认 3。
显式环境值跨块脱敏；超长/多行凭据时抑制日志正文；磁盘写入失败仍排空，面板显示 log_error。
日志接口最多读取当前文件末尾 32KiB，不提供任意路径。环境解析失败时不展示旧日志。
本批授权身份增加解析后环境的哈希，升级后旧 SAFE 指纹需用户重新复查一次。

新增 health/logs/connect/disconnect/catalog 接口均受原鉴权；status/health 不启动程序。
本轮不做远程传输、OAuth、在线命令/密钥编辑或自动安装。独立源码先 983 项全绿，合入安装器基线后再验证 990 项全绿；未连接生产服务。
