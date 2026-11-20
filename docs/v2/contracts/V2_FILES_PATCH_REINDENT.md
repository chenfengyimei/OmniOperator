# V2 工作区补丁替换侧缩进重锚（K13.patch_reindent）

状态：已实现专项通过（2026-11-20，合同先于接线）。承接 [补丁漂移纠正/守卫](V2_FILES_PATCH_DRIFT.md)（K13.patch_drift/K13.patch_drift_guards，明列「参考 `_reindent_replacement` 非精确匹配替换侧缩进重锚（仍后续）」）。学习参考 hermes-agent `tools/fuzzy_match.py` `_reindent_replacement`/`_match_starts_mid_line`/`_first_meaningful_line`/`_leading_whitespace`/`_apply_replacements` 纯逻辑，独立实现于 V2 既有 `domain/files_patch.py`，不复用 V1 ToolRegistry，stdlib 无新依赖。

## 缺口与动机

非精确匹配（line_trimmed/line_whitespace/escape_normalized/unicode_normalized/block_anchor/context_aware）命中后，V2 现状把 `new_string` **逐字**拼接到匹配区域。当模型供给的 `old_string` 基准缩进与文件实际缩进**漂移**（如模型用 4 空格、文件用 tab；或模型漏掉基准缩进），`new_string` 携带的模型缩进会**覆盖文件的缩进风格**，把 tab 缩进的文件改成空格缩进、或丢失嵌套基准。参考 `_reindent_replacement` 在非精确匹配后把 `new_string` 的基准缩进**重锚**到文件区域的实际基准缩进，保留相对嵌套，使替换保真于文件既有缩进风格。

## 范围（本批）

1. **domain/files_patch.py**（既有模块，无新源文件）新增纯函数：
   - `_leading_whitespace(line) -> str`：`line[:len(line)-len(line.lstrip(" \t"))]`（仅空格/ tab 前导，不含 `\r`）。
   - `_first_meaningful_line(text) -> str | None`：`text.split("\n")` 中首个 `line.strip()` 非空者，否则 None。
   - `_match_starts_mid_line(content, start, end) -> bool`：匹配区域首非空行之前是否有**非空白文本**（行中表达式无可靠基准缩进）。前导换行/空行不算行中（首代码行仍可整行捕获并重锚）。逐区域行推进绝对偏移 `first_line_start`，遇首非空行取 `line_start=content.rfind("\n",0,first_line_start)+1`，返回 `bool(content[line_start:first_line_start].strip(" \t\r"))`。
   - `_reindent_replacement(file_region, old_string, new_string, *, match_starts_mid_line) -> str`：
     - `new_string` 空或 `match_starts_mid_line` → 原样返回（保留 supplied 缩进）。
     - `old_first`/`file_first` 任一 None → 原样返回。
     - `old_indent=_leading_whitespace(old_first)`、`file_indent=_leading_whitespace(file_first)`；相等 → 原样返回（**零回归闸**）。
     - 逐 `new_string.split("\n")` 行：空行原样；前导空白 `startswith(old_indent)` → `file_indent + line[len(old_indent):]`（换基准前缀，保留相对嵌套）；否则（浅于基准）→ `file_indent + line.lstrip(" \t")`（锚定文件基准）。`"\n".join`。
2. **patch_replace 拼接环**：仅 `name != "exact"` 时，在既有 `_detect_post_match_drift`（拒）与 `_maybe_unescape_new_string`（解转义）**之后**，**逐区域**（`for start,end in reversed(spans)`，`region=content[start:end]` 用原始 content）计算 `adjusted=_reindent_replacement(region, old_string, effective_new, match_starts_mid_line=_match_starts_mid_line(content,start,end))` 再拼接。exact 策略**不重锚**（逐字，old_indent==file_indent 本就恒等）。多匹配 replace_all 每区域独立重锚（各区域缩进可能不同）。
3. **worker/composition/routing/ports 不变**：files_patch 写管线（指纹 CAS、staging 复核、原子 publish、unknown 不重放、所有者审批、agent 档）完全沿用；spec 描述补「非精确匹配把替换缩进重锚到文件基准缩进（保留相对嵌套，行中匹配保留 supplied 缩进）」；无新参数/schema 形状变化。

## 语义与零回归

- **exact**：不重锚（逐字拼接不变）。
- **非精确且 old_indent==file_indent**：`_reindent_replacement` 提前返回原样（绝大多数模型供给正确缩进的常见情形零回归）。
- **非精确且无缩进**（old/file 首行均顶格）：old_indent==file_indent=="" → 原样（unicode/similarity/escape 顶格用例零回归）。
- **非精确且行中匹配**（escape/unicode 字节精确跨度可能起于行中）：`_match_starts_mid_line` True → 保留 supplied 缩进。
- **非精确且缩进漂移**：重锚 fires，`new_string` 基准前缀换成文件基准，相对嵌套保留，浅行锚定文件基准。**这是本批的行为变更**：替换保真于文件缩进风格（tab 文件保持 tab，不再被模型空格覆盖）。

## 既有测试影响（行为变更，保真增强非弱化）

恰好 3 个 indent-drift 用例期望输出随重锚更新（断言意图不变且更强——额外验证文件缩进风格保真）：
- `test_worker_patch_line_trimmed_indent_drift`：`b"    return 2\n"` → `b"\treturn 2\n"`（文件 `\treturn 1` 首行 tab 基准保持）。
- `test_indentation_drift_subsumed_by_line_trimmed`：`bar\n` → `\tbar\n`（仍证 line_trimmed 触发，indentation_flexible 被涵盖结论不变）。
- `test_post_match_trivial_single_run_doubling_not_rejected`：`    z\\\\w\n` → `\tz\\\\w\n`（仍证平凡单串不被 drift 拒，反斜杠内容不变仅基准缩进重锚）。

`test_line_trimmed_matches_indent_drift`（`def f():\n\treturn 1` vs old `def f():\n    return 1`）**不变**：重锚只比较**首非空行**基准缩进，二者首行 `def f():` 均顶格（old_indent==file_indent==""）→ no-op，次行缩进漂移不触发重锚（参考保守语义；补注释说明）。其余 exact/顶格/同缩进/行中/拒写用例**零回归**（重锚 no-op 或 drift 先拒）。

## 有界与安全

drift 守卫在重锚**之前**对原始 old/new 运行（重锚只改前导空白，不新增反斜杠/引号/字面 `\n`，不绕过守卫）；unescape 在重锚之前（重锚见最终 effective_new）；`_reindent_replacement` 线性逐行无正则无 DoS；old/new 既有 `PATCH_STRING_CHARS=64KiB` 上限沿用；拼接环 reversed(spans) 偏移有效（region/mid-line 用原始 content）；写管线指纹 CAS/审批/原子 publish/unknown 不重放不变；近似命中仍禁 replace_all。无新表/迁移/路由/worker 协议帧/源文件/跨平台改动。

## 明确不做（列后续）

- V4A 多文件格式补丁；`_preserve_unicode_in_replacement`（unicode_normalized 匹配保留区域原排版 em-dash/智能引号，仅施加 old→new 编辑——独立缺口，本批 unicode 仍逐字拼接 new_string）；替换侧其他 shaping。不宣称参考全集对等。

## 验证计划

domain `_reindent_replacement`（空 new/行中保留、old/file 首行 None、同缩进 no-op、换基准保留嵌套、浅行锚定、空行原样、tab↔空格互换）；`_match_starts_mid_line`（整行跨度 False、行中字节跨度 True、前导空行仍 False）；`_leading_whitespace`/`_first_meaningful_line`；patch_replace 集成（line_trimmed 缩进漂移重锚、多区域 replace_all 各异缩进、exact 不重锚、顶格 no-op、行中 escape 保留、drift 先拒不受重锚影响）；4 既有 indent-drift 用例更新；调用方 files_patch+worker_safety+files_search 零回归。strict 本批零新错误（无新源文件，余 5 既有 repository.py 不追逐）；ruff。未全量；whole_*_complete 保持 false。

## 验证结果（2026-11-20）

- **专项**：`uv run --no-sync pytest -q -n 0 tests/v2/core/test_v2_files_patch.py`（agent/）→ **79 passed**（新增 11 节点：helper 单元 5 + patch_replace 集成 5 + worker 1；既有 68 中 3 个 indent-drift 用例期望随重锚更新、1 个 `def f():` 顶格用例补注释不变，余零回归）。
- **调用方**：worker_safety + files_search + capabilities + business_inventory + integrations/routing → **180 passed / 0 failed**（53.56 秒）。
- **strict**（仓库根，`pyrightconfig.strict.json`，pyright 1.1.414）：filesAnalyzed **414**、errors **5**、warnings 0；5 个均为另一会话已提交 `adapters/persistence/repository.py`（L633/L659×2/L710×2，commit 6bcb0e98）**既有错误，本批未触碰**；本批 domain/files_patch.py + capabilities.py **零新错误**（无新源文件，include 不变）。
- **ruff**：check + format 3 文件全过（domain/files_patch.py、composition/capabilities.py、test_v2_files_patch.py；新增一长 assert 行经 format 折行）。
- **首轮修正**：`test_line_trimmed_matches_indent_drift` 与 worker 重锚用例初版误期望 `def f():` 块重锚——重锚只比较**首非空行**基准缩进，`def f():` 顶格两侧相等故 no-op（参考保守语义），已改回/重设计为首行缩进漂移用例；`replace_all` 用例初版 old=`  foo\n` 是 4 空格行的精确子串致 exact 先命中，改用 tab/双 tab 文件 + 2 空格 old 强制 line_trimmed 逐区域重锚。
- 证据：[docs/evidence/v2-files-patch-reindent.json](../../evidence/v2-files-patch-reindent.json)。未全量；whole_core_parity_complete / whole_reference_feature_audit_complete / whole_v2_complete 保持 **false**。