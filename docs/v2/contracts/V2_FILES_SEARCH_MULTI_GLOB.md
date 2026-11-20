# V2 工作区搜索多包含 glob 列表 file_glob（K13.search_multi_glob）

状态：已实现专项通过（2026-11-20，合同先于接线）。承接 [工作区搜索文件过滤](V2_FILES_SEARCH_GLOB.md)（K13.search_file_glob，单包含字符串）与 [搜索否定排除](V2_FILES_SEARCH_EXCLUDE.md)（K13.search_exclude，≤8 字符串列表排除）；[K13 工具/渠道审计](V2_TOOL_CHANNEL_AUDIT.md) 与 [总对标](V2_AGENT_CORE_PARITY.md) 明列「多包含 glob 列表」仍后续。学习参考 hermes-agent `file_operations_search.py` rg `--glob` 多包含 OR 语义（user-facing `file_glob:634` 仅包含，多个非否定 glob 之间是「命中任一即纳入」），独立实现于 V2 既有四层 files_search 链，复用既有 `match_path_glob`，无新依赖/源文件。

## 缺口与动机

V2 `files_search` 的 `file_glob` 仅接受**单个**包含 glob 字符串（`normalize_file_glob(value) -> str | None`），而参考 rg 支持多个 `--glob` 包含模式（如 `--glob '*.py' --glob '*.md'`，文件命中**任一**即被搜索）。排除侧 `file_glob_exclude` 已支持 ≤8 字符串列表，包含侧却仍单串，不对称。多包含让所有者一次限定多种文件类型（`['*.py','*.pyi']`、`['src/*.ts','tests/*.ts']`），是读面发现的自然增量。

## 范围（本批）

1. **domain/files_search.py**（既有模块，无新源文件）：
   - 常量 `SEARCH_MAX_INCLUDE_GLOBS = 8`（与排除对称的包含 glob 上限）。
   - `normalize_file_glob(value) -> tuple[str, ...]`（**返回类型由 `str | None` widen 为元组**）：`None`→`()` 禁用；`str`→单元素列表；`list`/`tuple`→`cast(list[object] | tuple[object, ...], value)` 惯用法（`images.py:35`/exclude 同款，避 strict `reportUnknownArgumentType`）；其余→`ValueError`。`len(items) > 8`→拒。逐元素须 `str`（否则拒）、`strip()`、`1-200` 字符且无控制字节（`ord(c) < 32`）否则拒。**不剥前导 `!`**（包含是正向模式，`!` 否定属排除侧 `file_glob_exclude` 语义；与既有单串 `normalize_file_glob` 不剥 `!` 一致）。返回 `tuple(patterns)`。
2. **adapters/workers/main.py** `_files` files_search 分支：`file_glob = normalize_file_glob(...)` 现得元组；include 过滤由 `if file_glob is not None and not match_path_glob(..., file_glob): continue` 改为 `if file_glob and not any(match_path_glob(Path(relative).as_posix(), inc) for inc in file_glob): continue`（**命中任一包含 glob 即纳入**，OR）。空元组默认不过滤（全搜索）。include 仍在 exclude **之前**、`target=files` 分支 `continue` **之后**（即 files 目标忽略 include），读内容之前。分派前校验非法→ERROR/NONE/`invalid_capability_request` 零扫描（沿用）。
3. **composition/capabilities.py** files_search spec：`file_glob` schema 由 `{"type":"string","minLength":1,"maxLength":200}` widen 为 `{"type":["string","array"],"items":{"type":"string","minLength":1,"maxLength":200},"maxItems":8}`（**镜像 file_glob_exclude**）；描述 `file_glob restricts ... matches a case-sensitive glob`→`matches any of up to 8 case-sensitive include globs (a string or list of strings, e.g. '*.py' or ['*.py','*.md'])`。`required`/`writes=False`/`resource=workspace` 不变。
4. **routing/ports/http 不变**：files_search 已在只读 agent 档；泛型 `files_*` 分派无协议帧/跨平台改动；无新 operation/路由/表/迁移。

## 语义与零回归

- **单串字节等价**：`normalize_file_glob("*.py")` → `("*.py",)`，worker `any(...)` 单元素 OR 与既有单 glob `match_path_glob` 结果**完全一致**；既有全部单串 worker 用例（filters/path_anchored/none_default/files_with_matches+count/ignored_for_files/regex 组合）**零回归**。
- **空默认**：`None`/`[]`/`()`→`()`，`if file_glob` 为假→不过滤，全搜索（同既有 `None` 默认）。
- **多包含 OR**：文件命中**任一**包含 glob 即纳入；与排除组合时**先包含后排除**（既有顺序不变：include 过滤→exclude 过滤→读内容）。
- **非法分派前拒**：空串/纯空白/>200/控制字节/非 str 非 list/列表含非 str/列表含空串/>8 → `ValueError`→worker ERROR/NONE/`invalid_capability_request` 零扫描。既有 `test_worker_search_rejects_invalid_file_glob_before_dispatch` 参数化 `["", "   ", "x"*201, "a\nb", 5]` **全部仍非法**（无列表元素），零回归。
- **正交组合**：与 order/offset/context/output_mode/use_regex/file_glob_exclude 正交（modified 全局排序在过滤后子集内；include∩exclude 后子集）。
- **有界**：≤8 包含 glob、每个 ≤200 字符无控制字节、`match_path_glob`（`fnmatch.fnmatchcase` 大小写敏感、工作区相对 POSIX 路径、**线性时间无灾难性回溯 DoS**）；既有 2000 文件/5 秒/256KB/每文件 5 命中/50 页剪枝 + `workspace_path` 安全沿用；响应页 ≤50 远小于 MAX_FRAME 1MiB。

## 受影响的既有测试（恰好 2，类型 widen 保真更新非弱化）

- `test_normalize_file_glob_defaults_and_rejects_invalid`：`is None`→`== ()`、`== "*.py"`→`== ("*.py",)`、`== "x"*200`→`== ("x"*200,)`；bad 列表移除 `["*.py"]`（现为**合法**单元素列表），改加列表非法例（含非 str/含空串/>8）。
- `test_files_search_spec_exposes_file_glob`：schema 断言由单 string 改为 `[string,array] items maxItems8`（镜像 exclude）。
- 其余既有 file_glob 用例（worker 单串/非法参数化/match_path_glob）**不变**。

## 明确不做（列后续）

- ripgrep 后端（V2 原生 `os.walk`+`fnmatch`，无 rg 依赖）；高级 gitignore（`**` 锚定细则/目录级 `.gitignore`/`.ignore` 文件读取）；其他排序键；包含 glob 的 `!` 否定（属排除侧，已由 file_glob_exclude 覆盖）。不宣称 rg 全语义对等。

## 验证计划

domain normalize_file_glob（None/[]/()→空、str→单元组、list/tuple→多元组、strip、≤8、拒非 str 非 list/列表含非 str/空串/>200/控制字节/>8）；worker 多包含 OR（`['*.py','*.md']` 纳入两类、排除第三类）、单串零回归、空列表全搜索、路径锚定多包含、与 exclude 组合（先包含后排除）、files_with_matches/count 同过滤、target=files 忽略、与 regex+order+offset 组合、非法分派前拒；spec schema widen。调用方 worker_safety+files_patch+capabilities+business_inventory+routing 零回归。strict 本批零新错误（无新源文件，余 5 既有 repository.py 不追逐）；ruff。未全量；whole_*_complete 保持 false。

## 验证结果（2026-11-20，base_commit 2608f592）

- **专项** `uv run --no-sync pytest -q -n 0 tests/v2/core/test_v2_files_search.py` → **140 passed**（128 既有 + 12 新：多包含 OR、路径锚定多包含、与 exclude 组合先包含后排除、files_with_matches/count 同过滤、target=files 忽略、空列表全搜索、与 regex 组合、非法列表分派前拒 ×5；既有 658 默认值/763 spec 两测试随类型 widen 保真更新）。
- **调用方回归** worker_safety + files_patch + capabilities + business_inventory + integrations/routing → **141 passed / 0 failed**（35.00 秒；单串 file_glob 行为字节等价，worker 写管线/路由/档位零回归）。
- **strict** 仓库根 `pyright -p pyrightconfig.strict.json`（pyright 1.1.414）→ **414 files / 5 errors / 0 warnings**，5 错误全为既有 repository.py（L633/659×2/710×2，他会话已提交 6bcb0e98，不追逐），**本批零新错误**（无新源文件，未改 include；`normalize_file_glob` 返回类型 `str|None`→`tuple[str,...]` 唯一调用方 worker 已同步，strict 全绿）。
- **ruff** domain/files_search.py、adapters/workers/main.py、composition/capabilities.py、test_v2_files_search.py 四文件 check + format 全通过（test 一长行折行）。
- 未跑全量（本批为既有 files_search 读链包含参数 widen，专项 + 调用方已覆盖受影响面）；whole_core_parity_complete / whole_reference_feature_audit_complete / whole_v2_complete 保持 **false**。

### 指纹（sha256）

- domain/files_search.py `92ca6aa5b2583b5b0ee779eac5877cf8047ef2885088774104956e37851db1eb`
- adapters/workers/main.py `1381b7d1e800210125c2efa201f36ad4512c1c591d0be9c9c02d7cb248a7c753`
- composition/capabilities.py `8fd809d0b07c879941c6481a81196b1ea7874b87ee082e4a988a939756502414`
- test_v2_files_search.py `8b77f66d4bcbe275ab3f74d7d3fc0951d89921bb25f14cef015f467534f371de`

（合同自身指纹见证据 `docs/evidence/v2-files-search-multi-glob.json` source_files，避免自指。）