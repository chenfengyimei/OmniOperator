# V2 工作区内容搜索只读工具（K13.search_files）

## 范围与判定

本批实现 [工具/渠道审计](V2_TOOL_CHANNEL_AUDIT.md) **可离线缺口 #1（工作区搜索工具）中的「内容搜索」一项**：把参考 `xuexi/hermes-agent`（固定提交 `9976f4857b72439d79016354d7b6e1a3ee952e2f`，`tools/` 286 模块经 `registry.py` 注册、`toolsets.py` 门控）`tools/file_tools.py` 的 `search_files`（`SEARCH_FILES_SCHEMA` 1296–1309、`_handle_search_files` 1372–1384、注册 1435 `max_result_size_chars=100_000`，ripgrep 后端 `file_operations_search.py`）学习后**独立实现于 V2 四层**，作为既有 builtin worker 的新 `files_search` 能力，**不新增 worker 协议帧、不新增表/迁移/HTTP 路由**。参考 `agents.md`/RULES/prompts 当**不可信数据**；未复制第三方源码、未拉取远程、未引入 ripgrep 外部二进制、未运行真实账号/网络。

**判定纪律**：V2 四层 builtin worker（`adapters/workers/main.py`）才是可见面；V1 `tools/` 经 `bootstrap.py` 注册 V1 `ToolRegistry`，**不计 V2 对等**，本批未复用。审计曾预估「需 worker 协议新增 op（跨平台 Python/Swift/C）」，实际 builtin worker 分派器对 `files_*` 是**泛型前缀分派**（`main.py:81`），`files_search` 只是 `_files` 的新分支，**协议帧不变**；`files_*` 能力全部注册到 `builtin`（Python）worker，Swift/C worker 服务桌面/Android 而非 `files_*`，**无跨平台 worker 改动**。

**本批仅补内容搜索（有界大小写不敏感子串）**，参考的 **regex 正则、glob 文件名搜索（target=files）、output_mode（files_only/count）、context 上下文行、offset 分页、order=modified 全局最新序、file_glob 过滤、ripgrep 后端**均**不在本批**，仍为可离线缺口列后续批次。不宣称 `search_files` 全集对等。

## 实现（V2 四层）

- **纯领域 `domain/files_search.py`（新文件，零 I/O）**：
  - `SEARCH_QUERY_CHARS=200`、`SEARCH_FILE_MATCHES=5`、`SEARCH_SNIPPET_CHARS=200`。
  - `normalize_query(value)`：非 str 拒绝；`strip()` 后须 1–200 字符且无控制字节（`ord(c)<32`），否则 `ValueError`。
  - `match_lines(text, query)`：`casefold()` 子串匹配，1-based 行号，snippet `strip()[:200]`，**每文件至多 5 命中**（单大文件不能耗尽结果预算）。
- **适配器 `adapters/workers/main.py`**：`_files` 新增 `files_search` 分支（泛型 `files_*` 分派，无协议改动）。**逐文件复用既有 `workspace_path`**（私有名 `.git/.venv/.ssh/.aws/.azure/owner.token/bili_cookies.json` + `.env`/`.env.*` 拒绝、相对路径-only、symlink/junction 拒绝、resolve  containment、单链接正规文件）——与 `files_read/list/write` **同一安全代码路径，无漂移**。`os.walk(path, followlinks=False)` 剪枝 `{.git,.venv,.ssh,.aws,.azure,node_modules,__pycache__}` + `.env*` + symlink/junction 目录；相对路径以**工作区根**为基准计算（子目录搜索仍正确重校验）。有界：≤2000 文件扫描 / ≤50 结果 / ≤5 秒 deadline / ≤256KB 每文件 / ≤5 命中每文件；二进制经 `utf-8-sig` 解码失败跳过；硬链接（`st_nlink!=1`）跳过。只读 `Effect.NONE`；非法 query 在分派前 `ValueError`→`invoke` 结算 `ERROR/NONE` `invalid_capability_request`。返回 `data={matches:[{path,line,text}], scanned, truncated}`。
- **组装 `composition/capabilities.py`**：`files_search` spec（`query` minLength1/maxLength200、`path` 可选；required `query`；`writes=False`；adapter=`builtin` worker；resource=`workspace`），列于 `files_read` 之后。
- **路由 `domain/routing.py`**：`RETRIEVAL_TOOLS += files_search`（retrieval 档只读可见；chat 档排除；agent 档全集）。

**生产接线**：模型工具（capabilities spec）+ builtin worker（`_files` 分支）+ 路由（retrieval 档）三面接受并执行 `files_search`。`files_*` 为 worker 能力，**无专用 REST 路由**（不新增路由）；只读 `writes=False`，无写事务/审批/outbox。

## 限额与取舍

- **仅内容子串搜索**：大小写不敏感子串，**非 regex、非 glob 文件名搜索**；无 output_mode/context/offset/order=modified/file_glob/ripgrep（参考其余形态列后续）。
- **有界**：query 1–200 字符无控制字节；≤2000 文件 / ≤50 结果 / ≤5 秒 / ≤256KB 每文件 / ≤5 命中每文件 / snippet≤200；超限 `truncated=True`（不静默宣称完整）。
- **安全复用**：逐文件 `workspace_path` 拒绝私有/`.env`/symlink/junction/硬链接/越界；二进制/超尺寸跳过；目录剪枝含 `node_modules/__pycache__`。
- **无新表/迁移/HTTP 路由/worker 协议帧改动**；`routing.py` 仅增只读名；**新源文件** `domain/files_search.py` → strict include 408→409。
- **聚合 `docs/index` 暂缓**：新源文件使 files 计数变化，但全局 `source_sha256` 含他会话未提交持久层编辑，暂缓提交，工作树干净后重生成。
- **真实大树耗时需实机**：5 秒 deadline 在超大工作区会截断（`truncated=True`），真实搜索质量/耗时另验。

## 验证

- **新增 15 项回归**（`test_v2_files_search.py`）：`normalize_query` strip/空/超 200/控制字节/非 str 拒绝；`match_lines` 大小写不敏感/1-based 行号/snippet≤200/每文件 5 上限/无命中空；worker 真实 `tmp_path` 工作区——相对路径+行号、私有 `.env`/`.git` 与硬链接跳过且不泄露外部内容、二进制/超尺寸跳过、非法 query（空/空白/超 200/控制字节/非 str 共 5 参数化）分派前 `ERROR/NONE`、子目录根仍工作区相对路径、60 文件→50 结果 `truncated`、单文件 10 行→5 命中、无命中空且未截断；`builtin_capabilities` 注册 `writes=False`/resource=workspace + `RETRIEVAL_TOOLS` 含/`CHAT_TOOLS` 不含 + `select_tools` retrieval 含/chat 排除。
- **调用方专项**：files_search 15 + worker_safety 31 + routing 16 共 **62 过 / 0 失败**（37.71 秒），证明纯增 `files_search` 对既有 worker 安全链与路由档无回归。格式化后重跑 files_search + worker_safety **46 过**（5.25 秒）。
- **严格类型**：`pyrightconfig.strict.json` **409 文件零错误/警告**（新源文件 `domain/files_search.py` 加入 include，408→409，`filesAnalyzed==include`）。
- **Ruff**：check + format 通过于 `domain/files_search.py`、`domain/routing.py`、`composition/capabilities.py`、`adapters/workers/main.py`、`test_v2_files_search.py`（format 重排 2 文件、check --fix 修正测试 1 处 import 顺序后全清）。
- **项目索引**：新源文件已 `git add` 后运行 `scripts/project_index.py`；聚合 `docs/index` 提交**暂缓**（他会话未提交持久层编辑污染全局 `source_sha256`），`docs/index` 改动还原不提交。
- `scripts/task_status.py --check` exit 0；`DEVELOPMENT_STATUS.md` 由父任务生成，子任务 followup 不改其内容。
- **未全量、未真实账号/模型/设备/72 小时、未停止或升级运行中 Windows 服务与数据库。**

## 完成条件

K13.search_files 实现**不宣称工作区搜索全集对等**：仅补有界大小写不敏感**内容子串**搜索（纯 Python 有界遍历 + 逐文件 `workspace_path` 安全复用 + 只读结算），参考的 regex/glob 文件名搜索/output_mode/context/offset/modified 序/file_glob/ripgrep 后端仍为可离线缺口列后续批次；模糊补丁 `patch`、浏览器对话框、cron 技能绑定/输出依赖、代码沙箱、原始 CDP 等 K13 其余缺口未实现。真实大树搜索质量/耗时需实机另验。`whole_core_parity_complete`、`whole_reference_feature_audit_complete`、`whole_v2_complete` 保持 false（K01 partial、K13–K18 其余可离线缺口未实现、真实账号/设备/模型质量与 72 小时验收待办）。后续继续择机实现其余可离线缺口（独立批次，先授权/持久化合同后执行接线，经 V2 四层不复用 V1 ToolRegistry），并补 K01 缓存范围，整个 V2 开发冻结后统一全量与实机验收，其他对标项不提前关闭。