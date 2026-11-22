# V2 工具调用代码沙箱设计合同（K13 缺口 #5：execute_code / code_kernel / RPC，决策 D2）

状态：**设计合同（design_contract_recorded，纯文档，无源码/测试/迁移/路由/worker 改动，2026-11-23）**。**未实现**。承接 [K13 审计](V2_TOOL_CHANNEL_AUDIT.md) 组 3 与 [剩余差异决策登记](V2_REMAINING_DECISIONS.md) D2；本册把参考 hermes-agent 的 `execute_code`/`code_kernel`/`code_execution_env`/`code_execution_rpc` 映射到 V2 四层、worker 协议与写审批纪律，并明确**V2 不宣称真正安全隔离**。前缀重构（D1）、CDP（D3）、写扩面（D4）不并入本册。

## 参考事实（固定 `9976f485`，仅作设计数据）

- **execute_code = Programmatic Tool Calling**：模型写 Python 脚本，经 RPC 调用工具，只有 stdout 回给模型；本地后端是**每会话持久 kernel**（`tools/code_kernel.py`，Unix socket，Windows 用 loopback TCP），远端正向回退为每调用脚本投递。
- **允许清单**：`SANDBOX_ALLOWED_TOOLS = {web_search, web_extract, read_file, write_file, search_files, patch, terminal}` ∩ 会话已启用工具集，生成 stub；即沙箱内可调用**含 terminal 的写工具**。
- **限制**：`DEFAULT_TIMEOUT=300s`、`DEFAULT_MAX_TOOL_CALLS=50`、stdout 50 KB（40% head/60% tail，超出 spill 到 cache/exec 文件且 ≤5 MB）、stderr 10 KB。
- **kernel**：每 `(owner, mode, interpreter, cwd, tool-set)` 一个子进程，状态跨调用保留；与单调用**同一安全信封**（`_build_child_env` 清洗、RPC token+每 cell 工具预算、ANSI 剥离+秘密脱敏），只延长生命周期；卡死即杀进程树并丢注册项；spawn 时冻结环境直到 `reset=true`；wire 协议每行 JSON + 每 kernel 随机 SENTINEL；`max_session_kernels` LRU；委派子 kernel 以 `<parent-owner>::child::<child-session-id>` 键控并在 cleanup 释放。
- **env/RPC**：`code_execution_env` 负责 child env 清洗与 interpreter/cwd 解析；`code_execution_rpc` 提供 UDS/loopback RPC server、token、poll loop 与调用预算。
- **耦合**：`browser_exec` 因为是任意宿主 Python，要求同会话具备 `terminal` surface（`_rewrite_browser_exec`）；即参考把「任意代码执行」视为 terminal 面的一部分。

## V2 现状

- `shell_execute`：一次性**已审 argv**（owner 逐次批准）、worker 执行、有界 stdout/stderr；**无任意宿主 Python、无持久会话、无工具 RPC**。
- worker：`adapters/workers/main.py` 内置 worker 提供 files/shell/browser op；协议帧有界；**无 Python exec op**。
- 工具调用由引擎 `/execute` 生命周期（schema→authorize→资源锁→invoke）驱动；沙箱内 RPC 若要复用写工具审批，会出现「模型脚本等待异步 owner 审批」的阻塞语义，需要专门设计。
- 依赖：`websockets`/`httpx` 等已有；沙箱不引入新依赖也可行（子进程 + stdio 协议）。

## 设计选项（需所有者选定）

### S1 不移植（推荐先明确边界）

V2 已有 `shell_execute`（显式 argv + 审批）与 `files_*`，多数「写脚本处理数据」可拆成多次受审调用；**不新增任意宿主 Python**。审计登记「参考特有、V2 不移植」，不宣称对等。优点：零新攻击面；缺点：多步数据加工/循环调用工具无法一次完成。

### S2 受限计算沙箱（次推荐，真正可离线）

只提供**计算 + 文件读**的 Python 子进程（非 PTC）：

- 每次调用一个受管子进程（`python -I`，固定 interpreter，**不含 RPC 工具桥**）；输入脚本 + stdin JSON，输出仅 stdout/stderr；
- 独立的临时 cwd（调用结束删除）、**清洗环境**（沿用 `build_subprocess_env` 语义：剥离 launch profile `.env`/凭据/代理；只用 python 自身）；
- 资源上限：墙钟 ≤30s（可配置）、stdout ≤64 KB、stderr ≤8 KB、内存/进程数尽力限制（平台能力差异，不宣称强隔离）；
- 文件访问：默认**无 workspace 写**；如需读文件，由调用方把内容作为 stdin 传入（模型先 `files_read`），避免沙箱直接触盘；
- 网络：不提供出网 API，且清理代理变量；**明确声明这不是安全边界**——恶意代码在宿主进程模型下仍可能触网/触盘，需所有者接受「信任模型输出」的前提；若要求强隔离需容器/VM（不在本册）。
- 模型面：新能力 `execute_code`（只读、`writes=False`、agent 档、resource=workspace? 建议独立 resource）；**不含 terminal/写工具**。
- 无持久状态、无新表/迁移；无 worker 协议改动（本地 Python 子进程由 adapter 直接管理）或可放 builtin worker（二选一，若后者 → 协议加性 op，A 协调）。

### S3 完整 PTC（不推荐首批）

在 S2 之上加 RPC 工具桥 + 持久 kernel：

- RPC 允许清单只含只读工具（`files_list/read`、`memory_search`、`session_search`、`bilibili_read`、`media_list/transcript`、`files_search`）；**写工具一律不桥接**（避免脚本内异步审批与 unknown 语义）；
- 每 cell 工具调用预算 ≤50、总调用 ≤50、每次调用沿用既有 authorization/resource lock（同步脚本内阻塞等待不允许——只读无锁可同步；任何写都要退出脚本由模型正常调用）；
- 持久 kernel 生命周期：按 `(agent, session, interpreter, cwd)` 一个，LRU ≤4，空闲 ≤10 分钟回收，卡死杀进程树；重启不恢复（state loss 明示）；
- worker 协议：需要 RPC 帧（请求/响应/预算）→ **共享接口变更，必须 A 协调**；
- 风险：任意宿主 Python + 工具桥即便只读也可读 memory/文档/会话/工作区并外带（若模型恶意），且无 OS 隔离；需所有者明确接受。

### S4 远程/容器沙箱

docker/ssh/modal/daytona/vercel 等后端：**需账号/网络与独立部署合同**，不在本册；与 D7 远程后端同类。

## 决策问题

1. 选 S1（不移植）、S2（受限计算沙箱）还是 S3（完整 PTC）？S4 是否另立合同？
2. 若 S2：是否允许模型指定 interpreter（建议固定当前 Python）、墙钟/输出上限、是否需要读 workspace 文件（建议 stdin 传入）、是否允许 `import` 任意第三方库（建议限制为 stdlib + 已打包依赖，或完全允许但声明非隔离）。
3. 若 S3：RPC 白名单、kernel 生命周期/回收、与 K05 并行/资源锁/取消的交互、委派子 kernel 归属、审计事件。
4. **安全前提**：V2 必须公开声明「进程级约束不是安全边界」；是否需要 owner 逐次批准 `execute_code` 调用（建议 writes=False 但默认 OFF，owner 启用后每次仍可审计）。
5. 与 D3 的关系：`browser_exec`（任意宿主 Python over CDP）**不在本册**，且不得以 S2/S3 为跳板绕过 D9 URL floor。

## 实现要点（获批准后独立批）

- 领域：`domain/code_sandbox.py` 纯规划/校验（脚本长度 ≤64 KB、stdin ≤256 KB、超时/输出上限常量、拒绝 NUL）；无 I/O。
- 适配器：`adapters/workers/sandbox.py`（S2 子进程：`asyncio.create_subprocess_exec` + 环境清洗 + 临时 cwd + 超时 kill + 输出截断 + 清理）；`Bilibili` 无关，资源名 `code`；不写业务库。
- 能力：`execute_code` ToolSpec（writes=False、agent 档、非 RETRIEVAL_TOOLS；默认 OFF 由 owner 设置或部署开关）。
- 无新表/迁移；S3 若做 → worker 协议加性帧 + A 协调；kernel 状态不落库。
- 回退：摘掉能力注册后新增用例全红。

## 测试计划（实现批）

- 领域：脚本/stdin 上限、NUL、超时/输出常量、非法参数。
- 适配器：子进程执行最小脚本；env 清洗负例（host `OPENAI_API_KEY`/`.env` 不在子进程环境、`PYTHONPATH` 不透传）；临时 cwd 不残留；超时 kill 进程树；stdout/stderr 截断；非零退出→ERROR；取消→进程回收；**网络负例**（若接受非隔离前提则记录实际行为，不宣称阻断）。
- 应用/装配：能力注册档位、默认 OFF、隔离测试不启用、审计事件、调用方（engine/执行/架构/类型守卫）；S3 另加 RPC 白名单/预算/kernel 回收/委派子 kernel。
- 真实模型脚本质量与真实逃逸行为另验；未实现前不宣称组 3 对等。

## 明确不做

- `browser_exec`/`browser_cdp`（D3）；远程/容器后端（S4 另立）；把 terminal/写工具桥进沙箱；持久 kernel 落库；真实模型质量；全量；72 小时。

## 关联

- K13 审计：[V2_TOOL_CHANNEL_AUDIT](V2_TOOL_CHANNEL_AUDIT.md) 组 3。
- 决策登记：[V2_REMAINING_DECISIONS](V2_REMAINING_DECISIONS.md) D2。
- 相关合同：K05 `V2_TOOL_BATCH`（并行/资源锁）、K04 `V2_DELEGATION`（子 kernel 归属）、[V2_BROWSER_CDP](V2_BROWSER_CDP.md)（D3）。
- 证据：[v2-code-sandbox-design.json](../../evidence/v2-code-sandbox-design.json)。
