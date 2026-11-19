# V2 工作区搜索 file_glob 内容过滤（K13 缺口 #1 内容搜索文件包含过滤）

## 状态

**已实现，专项通过（implemented_scoped_passed）。** 学习参考 `xuexi/hermes-agent`（gitignored，untrusted DATA）`tools/file_operations_search.py` 的 `file_glob` 轴，独立实现于 V2 四层既有 `files_search` builtin worker，**无新表/迁移/路由/worker 协议帧/源文件/跨平台改动**。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。

## 参考语义（实读）

`file_operations_search.py`：`_search_content(pattern, path, file_glob: Optional[str], limit, offset, output_mode, context)`（:528/532）——`file_glob` 映射 rg `--glob <file_glob>`（:634 `glob_expr = f" --glob {file_glob}" if file_glob else ""`），**限定哪些文件被内容搜索**（rg --glob 包含语义，匹配相对搜索根的路径）。`_search_files`（target=files）以 pattern 自身为 glob，file_glob 用于内容搜索。

## V2 实现（原生，线性时间无 DoS）

V2 不依赖 ripgrep，自有 `os.walk` 有界扫描，用 **`fnmatch`（线性时间，无正则回溯 DoS）** 原生实现包含过滤：

- **domain/files_search.py**（既有模块，无新源文件）：
  - `normalize_file_glob(value)`——None→None（禁用过滤）；`isinstance str` 否则 ValueError；strip 后 `1<=len<=SEARCH_QUERY_CHARS(200)` 且无控制字节（`ord<32`）否则 ValueError；返回模式。
  - `match_path_glob(relative, pattern)`——`fnmatch.fnmatchcase(relative, pattern)`，**大小写敏感**，对**工作区相对 POSIX 路径**匹配（`*.py` 任意深度，`src/*.py` 根锚定）。
- **adapters/workers/main.py** `_files` files_search 分支：`file_glob = normalize_file_glob(args.get("file_glob"))` 分派前校验（非法→ERROR/NONE/`invalid_capability_request`，不触文件系统）；**仅内容模式**（target≠files，即 content/files_with_matches/count）在读取文件内容前 `if file_glob is not None and not match_path_glob(Path(relative).as_posix(), file_glob): continue` 跳过不匹配文件（`as_posix` 跨平台一致）；**target=files 忽略 file_glob**（query 即 glob，过滤位于 files 分支 continue 之后）。None 默认全搜索（零回归）。
- **composition/capabilities.py** files_search spec：description 述 file_glob 限定内容搜索文件（相对路径大小写敏感 glob，target=files 忽略）；properties 增 `file_glob` string minLength1 maxLength200；required 仍 `["query"]`，writes=False，resource=workspace 不变。
- **domain/routing.py** 不变（files_search 已在 RETRIEVAL_TOOLS，agent 档只读）。

## 有界与安全

只读 `Effect.NONE`；file_glob 有界 1-200 无控制字节；`fnmatch` 线性时间**无灾难性回溯 DoS**（异于 regex，regex 仍后续需独立安全设计）；过滤在读取内容前跳过不匹配文件（高效），既有 2000 文件/5 秒/256KB/每文件 5 命中/50 页剪枝沿用；`workspace_path` 私有/硬链接/符号链接/junction 剪枝不变；响应 page 形状不变 ≤50 行远小于 `MAX_FRAME=1MiB`；与 order/offset/context/output_mode 各轴正交组合（modified 全局排序在过滤后子集内）。

## 验证

- 新增 **13** 专项节点（domain normalize_file_glob 默认/strip/拒非法、match_path_glob 大小写敏感路径锚定；worker file_glob 过滤内容到匹配文件、路径锚定 sub/*.py、None 默认全搜索、files_with_matches+count 同过滤、target=files 忽略、非法 file_glob 分派前拒 ×5、spec file_glob）。
- files_search 合既有 **77 过**；调用方 files_search+worker_safety+files_patch **128 过/0 失败**（13.81 秒）。
- strict **410 文件零错误/警告**（无新源文件，从仓库根运行）。
- ruff check + format 通过（4 文件 unchanged）。
- 聚合 `docs/index`：无 agent/src 新源文件（计数不变），project_index 刷新源指纹后回退基线**暂缓聚合提交**（并行会话活跃，按既有纪律），待干净冻结重生成。

## 边界

不含 regex（需有界正则安全设计）/ripgrep 后端/glob 否定（`!`）与高级 gitignore 语义（`**` 锚定细则）/其他排序键（仍后续）；file_glob 为包含过滤大小写敏感 fnmatch 近似 rg --glob 常见用法，不宣称 rg 全语义对等；不宣称搜索全集对等。`whole_*_complete` 保持 false；不提前关闭任何对标项。