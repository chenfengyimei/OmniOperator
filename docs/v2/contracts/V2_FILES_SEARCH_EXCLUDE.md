# V2 工作区搜索 glob 否定排除（K13.search_exclude）

状态：已实现专项通过（2026-11-20，合同先于接线）。承接 [files_search 包含 glob](V2_FILES_SEARCH_GLOB.md)（K13.search_file_glob，明列「glob 否定（`!`）……仍后续」），本批补 ripgrep `--glob '!pattern'` **否定排除**语义。V2 不依赖 ripgrep，自有 `os.walk` 有界扫描 + `fnmatch`（线性时间，无正则回溯 DoS）。**纯 additive**：新增独立 `file_glob_exclude` 参数，既有 `file_glob` 单包含字符串行为/契约/测试**零回归**。

## 范围（本批）

1. **domain/files_search.py**（既有模块，无新源文件）：
   - 常量 `SEARCH_MAX_EXCLUDE_GLOBS = 8`（排除 glob 数上限，有界）。
   - `normalize_file_glob_exclude(value) -> tuple[str, ...]`：None→`()`（禁用排除）；str→单元素；list/tuple of str→多元素；其余 ValueError。元素数 >8 → ValueError。逐元素：须 str；strip；**剥离前导 `!`**（rg 否定标记，本参数即排除通道，`!` 冗余但兼容）后再 strip；`1<=len<=SEARCH_QUERY_CHARS(200)` 且无控制字节（`ord<32`）否则 ValueError。返回排除 glob 元组。
   - 排除匹配复用既有 `match_path_glob(relative, pattern)`（`fnmatch.fnmatchcase`，大小写敏感，工作区相对 POSIX 路径，线性时间）；文件被排除当且仅当匹配**任一**排除 glob。
2. **adapters/workers/main.py** `_files` files_search 分支：`file_glob_exclude = normalize_file_glob_exclude(args.get("file_glob_exclude"))` **分派前校验**（非法→ERROR/NONE/`invalid_capability_request`，不触文件系统）；**仅内容模式**（target≠files）在既有 include 过滤之后加 `if file_glob_exclude and any(match_path_glob(Path(relative).as_posix(), exc) for exc in file_glob_exclude): continue`（读内容前跳过被排除文件，高效）；**target=files 忽略 file_glob_exclude**（与 file_glob 一致，排除过滤位于 files 分支 continue 之后）。空元组默认不排除（零回归）。
3. **composition/capabilities.py** files_search spec：description 述 file_glob_exclude 排除内容搜索文件（相对路径大小写敏感 glob，string 或 ≤8 字符串数组，`!` 前缀兼容，target=files 忽略，与 file_glob 包含组合）；properties 增 `file_glob_exclude`（type `["string","array"]`，items string minLength1 maxLength200，maxItems8）；required 仍 `["query"]`，writes=False，resource=workspace 不变。
4. **domain/routing.py** 不变（files_search 已在 RETRIEVAL_TOOLS，只读）。

## 语义（rg --glob 否定近似）

- 包含+排除组合：`file_glob='*.py'` + `file_glob_exclude='test_*.py'` → 搜 *.py 但排除 test_*.py（rg `--glob '*.py' --glob '!test_*.py'`）。
- 仅排除：`file_glob_exclude='*.lock'`（file_glob 省略）→ 搜全部但排除 *.lock（rg `--glob '!*.lock'`）。
- 多排除：`file_glob_exclude=['*.lock','dist/*']` → 排除匹配任一者。
- `!` 前缀：`file_glob_exclude='!*.lock'` 等价 `'*.lock'`（剥离冗余否定标记）。
- 排除在包含之后：文件须先通过 include（若指定）再不被任何 exclude 命中。大小写敏感 fnmatch，`*.py` 任意深度、`src/*.py` 根锚定（同 include）。

## 有界与安全

只读 `Effect.NONE`；file_glob_exclude ≤8 个、每个 1-200 无控制字节、分派前校验；`fnmatch` 线性时间**无灾难性回溯 DoS**；排除在读取内容前跳过（高效）；既有 2000 文件/5 秒/256KB/每文件 5 命中/50 页剪枝沿用；`workspace_path` 私有/硬链接/符号链接/junction 剪枝不变；响应 page 形状不变 ≤50 行远小于 `MAX_FRAME=1MiB`；与 order/offset/context/output_mode/use_regex/file_glob 各轴正交组合（modified 全局排序在过滤后子集内）。

## 明确不做（列后续）

- 多包含 glob 列表（file_glob 仍单包含字符串；include+exclude 经两参数组合）；高级 gitignore 语义（`**` 锚定细则/目录级忽略文件）；ripgrep 后端；其他排序键。不宣称 rg 全语义对等。

## 验证计划

domain normalize_file_glob_exclude（None→()、str→单元组、list→多元组、`!` 剥离、拒非 str/非 list、拒空/超 200/控制字节、计数 >8 拒）；worker（exclude-only 排除匹配文件、include+exclude 组合、多排除任一命中、`!` 前缀等价、files_with_matches+count 同排除、target=files 忽略、空默认全搜索零回归、非法分派前拒 ×N、spec file_glob_exclude）；调用方 files_search+worker_safety+files_patch 零回归。strict 本批零错误（无新源文件，余 5 既有 repository.py 不追逐）；ruff。未全量；whole_*_complete 保持 false。

## 验证结果（2026-11-20）

- **专项**：`uv run --no-sync pytest -q -n 0 tests/v2/core/test_v2_files_search.py`（agent/）→ **128 passed**（新增 18 节点：10 函数 + 1 参数化 ×8；既有 110 全过零回归）。
- **调用方**：`test_v2_worker_safety.py`（31）+ `test_v2_files_patch.py`（68）→ **99 passed**；合计 227 passed / 0 failed。
- **strict**（仓库根，`pyrightconfig.strict.json`，pyright 1.1.414）：filesAnalyzed **414**、errors **5**、warnings 0；5 个均为另一会话已提交 `adapters/persistence/repository.py`（L633/L659×2/L710×2，commit 6bcb0e98）**既有错误，本批未触碰**。本批初现 `files_search.py:98` `reportUnknownArgumentType`（`list(value)` 于 `list[Unknown]|tuple[Unknown,...]`），按本仓 `images.py:35` 既有 `cast(list[object]|tuple[object,...], value)` 惯用法修正，复跑本批源零新错误。
- **ruff**：check + format 4 文件全过（domain/files_search.py、adapters/workers/main.py、composition/capabilities.py、test_v2_files_search.py）。
- **无新源文件**：函数追加既有 domain/files_search.py，strict include 列表不变（414）；无新表/迁移/路由/worker 协议帧/跨平台改动。
- 证据：[docs/evidence/v2-files-search-exclude.json](../../evidence/v2-files-search-exclude.json)。未全量；whole_core_parity_complete / whole_reference_feature_audit_complete / whole_v2_complete 保持 **false**。