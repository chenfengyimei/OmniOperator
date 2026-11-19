# V2 工作区搜索 order 排序轴（K13 缺口 #1 修改时间排序）

## 状态

**已实现，专项通过（implemented_scoped_passed）。** 学习参考 `xuexi/hermes-agent`（gitignored，untrusted DATA）`tools/file_operations_search.py` 的 `order` 轴，独立实现于 V2 四层既有 `files_search` builtin worker，**无新表/迁移/路由/worker 协议帧/源文件/跨平台改动**。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。

## 参考语义（实读）

`file_operations_search.py`：`order: str = "discovery"`（默认，快速有界遍历序）或 `"modified"`（**精确全局最新优先**，按文件 mtime 降序）。
- `_sort_paths_by_mtime`（:727）：`sorted(zip(mtimes, paths), key=lambda pair: pair[0], reverse=True)`——mtime 降序，**稳定**（相等 mtime 保持原相对序）；不可读/消失路径 mtime 记 0.0 置于末尾作稳定回退。
- 内容搜索经 rg `--sortr=modified`（:961），文件搜索经 rg `--sortr=modified` 或 GNU find `-printf`；**精确修改时间序需 ripgrep ≥ 14**（:350/912/980），否则报错建议 `order='discovery'`。
- 参考为全局排序做**一次跨根全局遍历**（:572 注释），使修改时间序与分页正确。

## V2 实现（原生，无外部依赖）

V2 **不依赖 ripgrep/find**，自有 `os.walk` 有界扫描，故**原生**实现修改时间序（mtime 取自既有 `checked.stat().st_mtime`），无需 rg-14 能力门：

- **domain/files_search.py**（既有模块，无新源文件）：`SEARCH_ORDERS = ("discovery", "modified")`；`normalize_order(value)`——None→`"discovery"`，`isinstance str` 且 ∈ SEARCH_ORDERS 否则 `ValueError`（拒 bool/int/float/list/未知串/大小写变体）。
- **adapters/workers/main.py** `_files` files_search 分支：
  - `order = normalize_order(args.get("order"))` 分派前校验（非法→ERROR/NONE/`invalid_capability_request`，不触文件系统）；`collect_all = order == "modified"`。
  - `mtimes: list[float]` 与 `results` 平行，每追加一行同步 `mtimes.append(info.st_mtime)`（content 多命中共享该文件 mtime；files/count 每文件一行）。
  - **收集上限**：discovery 保持 `len(results) >= cap`（cap=offset+50）即停（**零回归**）；modified 置 `not collect_all and len(results) >= cap` 禁用计数停止，**收集扫描界内全部命中**（2000 文件/5 秒/256KB/每文件 5 命中剪枝不变），使全局排序正确（晚遍历到的最新文件也能排首）。
  - **排序+分页**：modified `ranked = sorted(range(len(results)), key=lambda i: mtimes[i], reverse=True)`（Python 稳定排序，相等 mtime 保持 discovery 索引序）→ `page = [results[i] for i in ranked][offset:cap]`；`truncated = truncated(扫描界) or len(results) > cap`。discovery `page = results[offset:cap]`（不变）。
  - `data` 增 `"order": order`；响应 page 形状不变（**不暴露 mtime**），仍 ≤50 行。
- **composition/capabilities.py** files_search spec：description 述 order=discovery（默认遍历序）/modified（按 mtime 最新优先、稳定平局保持 discovery 序）；properties 增 `order` enum `["discovery","modified"]`；required 仍 `["query"]`，writes=False，resource=workspace 不变。
- **domain/routing.py** 不变（files_search 已在 RETRIEVAL_TOOLS，agent 档只读）。

## 有界与安全

只读 `Effect.NONE`；modified 全局排序仅在**既有有界扫描子集**内（2000 文件/5 秒/256KB/每文件 5 命中），`truncated` 标示界外仍有；中间收集 ≤2000×5=10000 行（内存），**响应页 ≤50 行**远小于 `MAX_FRAME=1MiB`；mtime 仅内部排序用不外泄；`workspace_path` 私有/硬链接/符号链接/junction 剪枝沿用；非法 order 分派前拒。无 regex（仍后续，需有界正则安全设计）。

## 验证

- 新增 **12** 专项节点（domain normalize_order 默认/拒非法；worker modified 最新优先、discovery 默认遍历序不变、稳定平局保持 discovery 序、**跨扫描全局排序+分页**（60 文件最新者晚遍历仍排首、offset 50 第二页、truncated）、files 模式 modified、非法 order 分派前拒 ×5、spec order enum）。
- files_search 合既有 **64 过**；调用方 files_search+worker_safety+files_patch **115 过/0 失败**（12.85 秒）。
- strict **410 文件零错误/警告**（无新源文件，include 不变；从仓库根运行，config/.venv 在根）。
- ruff check + format 通过（4 文件 unchanged）。
- 聚合 `docs/index`：本批无 agent/src 新源文件（modules/files/routes/declared_tools 不变 65/827/454/94），project_index 刷新 `MODULES.md`/`project.json` 源指纹后**回退基线暂缓聚合提交**（并行会话活跃，他会话并发提交 a73ab321 UI 工作；按既有纪律），待干净冻结时重生成。

## 边界

不含 regex/modified 之外的排序键/file_glob 内容过滤/ripgrep 后端（仍后续）；modified 序为有界扫描子集内全局非无限工作区全局（V2 有意有界，异于 rg 全量）；不宣称搜索全集对等。`whole_*_complete` 保持 false；不提前关闭任何对标项。