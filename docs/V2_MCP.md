# V2 外部 MCP 接入（D6.mcp）

本文是 V2 MCP 子系统的设计权威：服务身份、有界传输、目录指纹与逐工具授权、模型可见文本、显式 OAuth、HTTP 与工作台契约。批次的原始报告与限制见 `docs/evidence/v2-mcp.json`。

Windows离线候选默认入口为V2，显式V1回退保留。MCP及整个V2后续开发继续推进，真实外部MCP服务与真实OAuth供应商另记“待真实验收”。

## 1. 生产路径与真值

```
配置修订（configuration kind='mcp'：transport/command/arguments/endpoint/auth_mode/credential_ref）
  → 所有者显式 connect：mcp_servers 进入 starting（必要时递增 authority_epoch 并撤销旧授权）
  → 受管传输：stdio JSON-RPC 子进程 或 Streamable HTTP（JSON/SSE）
  → tools/list 分页发现（≤16 页 / ≤256 项）→ mcp_catalogs + mcp_tools 不可变快照
      fingerprint = 工具名 + inputSchema 的稳定哈希（不含 description）
  → 目录指纹变化：撤销该服务全部 mcp_grants、authority_epoch+1、要求重新确认
  → 所有者逐工具授权（read_only 必须显式声明）→ mcp_grants（审阅审计 id）
  → 每回合 mcp_run_tools 封存本 run 可用工具（未授权/已撤权工具对模型不可见）
  → 模型调用 mcp__<server>__<tool>：动作行 → 二次校验 → 传输 → mcp_calls 记录
```

- MCP 服务身份是**配置修订**，不新增第二套配置体系；不迁 V1 `config.yaml`，不 import V1 `tools/mcp`。
- 真值：`<home>/data/omni.db`。凭据只在 vault（`AsyncSecretPort`），模型文本/HTTP/日志/审计不含令牌本体。
- 外部描述与返回值是不可信数据：不授予权限、不自动打开链接、不转发 base64 媒体。

## 2. 数据模型（schema v2_0037）

新增 7 张表，均由数据库触发器维持不变量（不改写 v0036）：

- `mcp_servers`：每 `(agent_id, config_id)` 一行运行态；`state IN (idle, starting, ready, failed, unknown)`；准入必须 `idle/epoch=1/空指纹`；转移白名单 `idle→starting`、`starting→ready/failed/unknown/idle`、`ready→idle/failed/unknown`、`failed|unknown→starting/idle`；身份与 `authority_epoch` 只增不减；`ready` 必须已有同指纹的目录快照。
- `mcp_catalogs`：目录快照（`Unique(agent_id, server_id, fingerprint)` 复用同指纹快照，保持工具 id 稳定）；不可变。
- `mcp_tools`：`name/exposed_name/description/input_schema/tool_fingerprint/position`；`Unique(catalog_id, name)`、`Unique(catalog_id, exposed_name)`；不可变。
- `mcp_grants`：逐工具授权；`Unique(server_id, authority_epoch, tool_id)`；部分唯一索引保证每工具至多一条 active；插入触发器要求服务 `ready`、目录指纹与代际一致、工具指纹匹配；`review_audit_id` 外键到审计（保留策略保护）；除 `revoked_at` 外不可变。
- `mcp_run_tools`：每 run 封存（`Unique(run_id, name)`、`Unique(run_id, tool_id)`）；插入要求 live grant 且 `read_only = NOT writes`；不可变。
- `mcp_action_bindings`：动作与 run 封存的绑定（`Unique(action_id)`）；插入要求动作与绑定同 run；不可变。
- `mcp_calls`：调用记录；`state IN (prepared, dispatched, done, failed, denied, unknown, cancelled)`；准入只允许 `prepared` 且计数为零；转移白名单；终态不可变；`done/failed/denied/unknown/cancelled` 必须 `finished_at`；`unknown/failed/denied` 必须带错误码。

跨 Agent 隔离由 `(id, agent_id)` 组合外键与查询条件同时维持；仓储不自建连接、不建表。

## 3. 传输、进程与结果分类

- **stdio**（`adapters/mcp/stdio.py`）：每服务一个 `OwnedProcess`；`initialize` → `notifications/initialized` → `tools/list`（cursor 分页）→ `tools/call`；请求串行、id 严格匹配；握手 20s、目录总预算 30s、单调用默认 60s（工具规格上限 600s）；stderr 持续排空并只记元数据（字节数），不落外部正文。
- **Streamable HTTP**（`adapters/mcp/http.py`）：POST JSON-RPC，`Accept: application/json, text/event-stream`；支持 JSON 与 SSE 响应、`Mcp-Session-Id` 回带、`MCP-Protocol-Version`；连接 10s、读超时随调用预算；不跟随重定向、`trust_env=False`；`401/403` 明确失败且**不**自动开始 OAuth；`HTTP 5xx`/超时/断连在请求已发出后归 `unknown`，`4xx` 归确定失败。
- **失败分类**：传输未发出 → `failed`（无副作用）；已发出但超时/崩溃/取消/协议错误/5xx → `unknown`（可能已执行，不自动重放）；服务器 `isError=true` 是**结果错误**（连接保持，`mcp_tool_error`），不等同传输失败。
- **取消**：调用取消会终止/回收会话；已发出的调用记 `unknown`，未发出记 `cancelled`；进程/连接实际未回收时 `health()` 如实报 `cleanup_pending`，`reset` 显式重试。
- **重连**：本批不做自动指数退避；失败后由所有者显式 `connect` 或 `reset`，失败不会自动重放写操作。

## 4. 目录、指纹与授权

- `catalog_fingerprint = sha256(按名排序的 [工具名, 工具指纹])`；`tool_fingerprint = sha256({"name", "inputSchema"})`，**不含 description**。
- 目录变化（工具增删或 inputSchema 变化）→ 连接/重新发现时撤销全部授权、`authority_epoch+1`、置 `needs_confirmation`；旧页面提交旧指纹得到 `mcp_catalog_changed` 409。
- 未授权工具不进入 run 封存，模型不可见；授权必须带当前指纹、所有者审阅证据（写入审计并外键保护）。
- 只读白名单只能由所有者逐工具显式声明，且只对已授权工具生效；未声明按写处理，走既有审批链；不读取服务自报 `readOnlyHint`。
- 工具暴露名 `mcp__<server_slug>__<tool>`（前缀防撞；跨服务重名在封存时返回 `mcp_tool_collision`）。
- 运行中撤权：`mcp_grants.revoked_at` 或代际变化后，派发前 `before_send` 二次校验失败 → 拒绝，不使用旧授权。

## 5. 模型暴露与文本

- 每回合 `tools_for_run`：`mcp_run_capture` 幂等封存，返回 `ToolSpec(name, description, inputSchema, writes=not read_only, timeout_s=60, resource="mcp:<server_id>", binding_id)`。
- MCP 工具只在 `agent` 档位对模型可见（档位过滤是权限之外的另一层限制；`chat/retrieval` 的固定白名单不含外部工具）。
- 结果只经 `ActionResult.text`：首行 `mcp server=<name> tool=<tool> is_error=<bool>`，正文只拼接 `content[].text`；非文本内容替换为省略标记；总长 ≤12000 字符，超限显式截断标记。
- `ActionResult.data` 只含 `server/tool/is_error/chars/truncated`；不回传凭据、头部、完整异常或原始外部载荷；外部文本不解读为指令。

## 6. 显式 OAuth（`adapters/mcp/oauth.py`）

- 仅 `streamable_http + auth_mode='oauth'`；只有所有者点“授权远程服务”才发起，`401` 不会触发授权。
- 发现：RFC 9728 受保护资源元数据 → `authorization_servers[0]` → RFC 8414 授权服务器元数据；端点必须 HTTPS（回环允许 http）、不含账号/片段。
- 客户端：优先复用 vault 中的 `mcp.oauth-client.<server_id>`；否则用动态客户端注册（RFC 7591，`token_endpoint_auth_method=none`）。
- 授权码 + PKCE(S256) + 单次 `state`；回调限 `http://127.0.0.1|localhost|::1/oauth/callback`，窗口 120 秒；重复/错配/过期 state 一律 409；回调页不回显 code/token。
- 令牌入 vault（`mcp.oauth.<server_id>`：access/refresh/expires_at/token_endpoint/client_id）；过期只允许 refresh，refresh 失败返回空令牌（连接失败），不自动重新授权。
- 授权完成后撤销旧授权、`authority_epoch+1`、置 `idle`：必须重新连接并复查目录；换账号/机器需重新授权。

## 7. HTTP 契约（/api/v1，Bearer 所有者）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/agents/{id}/mcp/servers` | 服务列表（被动状态 + 运行时健康），不启动进程 |
| GET | `/agents/{id}/mcp/servers/{config_id}` | 详情（状态/指纹/授权计数/错误码） |
| POST | `.../servers/{config_id}/connect` | 显式连接 + 发现；body `{revision}`；失败返回 `connected=false` + 错误码，不重试 |
| POST | `.../servers/{config_id}/disconnect` | 断开并回收；未回收如实报 `reclaimed=false` |
| POST | `.../servers/{config_id}/reset` | 显式重置到 idle；不自动重放调用 |
| GET | `.../servers/{config_id}/catalog` | 目录分页（`after`=position、limit ≤100）+ 授权状态 |
| PUT | `.../servers/{config_id}/authorization` | 批量逐工具授权（全量替换；空列表=撤销全部） |
| PUT | `.../servers/{config_id}/tools/{tool_id}/authorization` | 单工具授权/撤销/改只读 |
| POST | `.../servers/{config_id}/oauth/start` | 显式 OAuth；返回 `authorization_url` 与到期；回调限回环 |
| GET | `.../servers/{config_id}/calls` | 脱敏调用记录（时间/工具/状态/错误码/字符数/耗时） |
| GET | `/agents/{id}/mcp/health` | MCP 运行时健康（会话/待处理/OAuth 待定） |

根级 `GET /oauth/callback` 免 Bearer（浏览器回调），只接受单次 state，返回固定文案 HTML。

## 8. 工作台（web/ui/v2/mcp.js）

- 新分区「MCP」：服务列表、被动状态、目录指纹、逐工具 `<details>` 完整 inputSchema、授权/只读勾选、调用记录。
- 连接/断开/重置/撤销全部授权/发起 OAuth 都先确认目标；执行中禁用重复点击；失败只显示错误，不自动重放。
- 未连接时不显示目录；旧指纹保存得到 409 时提示刷新；390/1024 视口可用（浏览器用例覆盖）。
- 资源版本串整组原子递增（本批 `20261110v48 → 20261110v49`）。

## 9. 上限与错误码（择要）

工具数 ≤256、目录页 ≤16、工具名 1..64（`[A-Za-z0-9_-]`）、暴露名 ≤100、描述 ≤1024、schema ≤64KiB/深度 16/节点 2048、参数 JSON ≤256KiB、结果文本 ≤12000 字符、会话 ≤8、待处理调用 ≤32、OAuth 待定 ≤4/每服务、OAuth state TTL 120s。

`mcp_configuration_revoked`、`mcp_not_connected`、`mcp_catalog_changed`、`mcp_catalog_drift`（审计字段）、`mcp_not_authorized`、`mcp_tool_collision`、`mcp_cleanup_pending`、`mcp_call_timeout`、`mcp_connection_lost`、`mcp_unauthorized`、`mcp_server_error`、`mcp_tool_error`、`mcp_outcome_unknown`（unknown 类）。

## 10. 验证口径与未完成

- 离线：假 stdio JSON-RPC 子进程（真实进程）与假 HTTP 服务覆盖中文、分页、会话复用、串行、isError、崩溃、超时、取消、SSE、401/5xx；真实链路覆盖配置→连接→授权→模型调用→漂移撤权→工作台。
- 守卫：四层依赖（`test_v2_architecture.py`）、冻结迁移与列/FK 对账、`test_v2_http.py` 端点目录一致、worker 进程所有权回归。
- 未完成/待真实验收：真实外部 MCP 服务（含真实服务端 OAuth 供应商）、后台自动重连策略、跨进程 MCP 会话共享、D6 完整插件链/市场与 D7 入口切换。

## 11. 完整业务任务与失败会话归属（2026-11-18）

所有者查询、连接、目录、授权、重置、OAuth、运行工具封存和恢复共14个入口及模型调用共用64项完整业务任务上限。关闭或第65项在首次数据库/传输操作前拒绝，分别为mcp_closing/mcp_busy；所有者HTTP返回503/no-store，模型收到结构化ERROR。原8会话/32传输调用上限保持。

所有者请求取消只结束等待；已准入的实际连接/授权/查询及最后提交继续归服务持有。OAuth交换后必须等待原授权代次事务交付，不重放交换。模型取消在数据库边界记录请求，等原读取/准备/派发事务实际交付；传输阶段只请求一次取消，不能在清理中被后续close再次截断。派发前二次授权与dispatched提交同样保留原任务；取消后不发送，但已提交派发意图仍保守记unknown。完整回包后的实际结果提交继续完成，回包处理失败保留unknown，不声称未执行。

失败启动、旧代次、显式stop/close均先将原会话标为retiring。close返回false或异常时仍保留相同对象、代次及原握手，health即使底层未报告也显示cleanup_pending；目录/调用拒绝，不用旧握手恢复，不创建替代连接。只有原close确认成功才移除。等待全局锁或旧会话关闭后再次检查closing，防止关闭后创建新会话。

关闭先拒绝新业务并排空完整任务，再启动原transport-close任务；取消等待或预算耗尽不取消/复制实际close。原close真正结束为false/异常后，下次显式close才可再试。quiescent同时核对业务任务、传输调用、会话busy/starting/retiring及关闭结果；数据库和人工动作核查不能因传输计数暂为零而提前释放。

37新增及65调用方/合同，分阶段102不同专项通过：主组101（56.44秒），末次仅HTTP关闭与模型满队列2项（4.58秒）。运行2文件与主组字节相同，strict零错误/警告；386仅登记。原所有者用例正文14项AST一致，最终其他新测试正文与主组一致。保留旧源码3失败、测试随机目标导致停滞、字段误用2失败及1类型诊断；未缩短期限、放宽断言或以补跑冒充全量。实际隔离SQLite/HTTP及既有stdio/回环HTTP/OAuth链已验，新增时序端口为注入；真实外部服务/供应商、平台/账号/设备/72h仍待。见[归属证据](evidence/v2-mcp-ownership.json)。
