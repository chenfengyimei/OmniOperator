# V2 工作区补丁 unicode 匹配替换侧排版保留（K13.patch_unicode_preserve）

状态：已实现专项通过（2026-11-20，合同先于接线）。承接 [补丁缩进重锚](V2_FILES_PATCH_REINDENT.md)（K13.patch_reindent，明列「`_preserve_unicode_in_replacement`……独立缺口，V2 unicode 仍逐字拼接 new_string」）。学习参考 hermes-agent `tools/fuzzy_match.py` `_unicode_edit_plan`/`_preserve_unicode_in_replacement`/`_apply_replacements` 纯逻辑，独立实现于 V2 既有 `domain/files_patch.py`，复用既有 `_unicode_normalize`/`_build_orig_to_norm_map`/`_norm_end_to_orig`（与参考逐字一致），stdlib `bisect`+`difflib.SequenceMatcher` 无新依赖。

## 缺口与动机

`unicode_normalized` 策略把智能引号/em-dash/省略号等归一为 ASCII 后匹配，命中区域映射回原始坐标，但 V2 现状把 `new_string` **逐字**拼接 over 该区域。当 `new_string` 与 `old_string` 共享归一化文本（equal 部分）且文件区域在该处含**原排版字符**（如 `—` em-dash、`“”` 智能引号），逐字拼接会把文件的排版**flatten** 成 `new_string` 的 ASCII（`—`→`--`）。参考 `_preserve_unicode_in_replacement` 仅把 old→new 的**编辑**施加到区域**原文**，equal 部分保留区域原排版，使替换不破坏文件既有 typography。

## 范围（本批）

1. **domain/files_patch.py**（既有模块，无新源文件）：
   - `import bisect`（stdlib）。
   - 类型别名 `_Opcode = tuple[str, int, int, int, int]`；`_UnicodePlan = tuple[str, list[_Opcode]]`。
   - `_unicode_edit_plan(old_string, new_string) -> _UnicodePlan`：`norm_old=_unicode_normalize(old_string)`；返回 `(norm_old, SequenceMatcher(None, norm_old, new_string).get_opcodes())`。**区域无关**，replace_all 一次计算供每区域复用。
   - `_preserve_unicode_in_replacement(file_region, new_string, plan) -> str`：
     - `norm_old, opcodes = plan`；`norm_old != _unicode_normalize(file_region)` → 返回 `new_string`（**回退守卫**：策略本不应触发/trimmed 回退区域归一不等）。
     - `file_orig_to_norm = _build_orig_to_norm_map(file_region)`。
     - 逐 opcode `(tag, i1, i2, j1, j2)`：`equal` → `orig_start=bisect.bisect_right(file_orig_to_norm, i1)-1`（含多字符扩展内部的 norm 索引归属原字符）、`orig_end=_norm_end_to_orig(file_orig_to_norm, orig_start, i2)`、append `file_region[orig_start:orig_end]`（**保留原排版**）；`delete` → 跳过；`replace`/`insert` → append `new_string[j1:j2]`。`"".join`。
2. **patch_replace 拼接环**：`name == "unicode_normalized"` 时在既有 drift 守卫（拒）与 `_maybe_unescape_new_string`（解转义）**之后**一次计算 `plan=_unicode_edit_plan(old_string, effective_new)`；**逐区域**（`reversed(spans)`，`region=content[start:end]` 原始 content）先 `adjusted=_preserve_unicode_in_replacement(region, effective_new, plan)` **再** `_reindent_replacement`（参考顺序：排版保留→缩进重锚）。其余策略 plan=None 不保留（逐字/重锚不变）。
3. **worker/composition/routing/ports 不变**：写管线（指纹 CAS/staging/原子 publish/unknown 不重放/审批/agent 档）沿用；spec 描述补「unicode_normalized 匹配保留区域原排版（em-dash/智能引号），仅施加 old→new 编辑」；无新参数/schema 形状变化。

## 语义与零回归

- **仅 unicode_normalized**：其余策略（exact/line_trimmed/line_whitespace/escape/block_anchor/context_aware）不保留排版（行为不变）。
- **wholesale replace**（new 与 old 无共享归一文本，opcodes 全 replace）：结果 == new_string 逐字（既有 smart_quotes/em_dash/ellipsis/worker 用例**零回归**）。
- **equal 部分仅 ASCII**（如尾部 `\n`）：保留 == new_string 对应部分（既有 line_trimmed_fallback 用例**零回归**）。
- **trimmed 回退区域归一不等**（`_unicode_normalize(region) != norm_old`，如区域含尾随空格）：回退守卫返回 new_string 逐字（既有 line_trimmed_fallback **零回归**）。
- **equal 部分含原排版字符**：保留文件 `—`/`“”`/`…` 而非 new_string ASCII（**本批新增行为**）。
- 排版保留在缩进重锚**之前**（参考顺序）；drift 守卫在两者之前对原始 old/new 运行（保留/重锚不新增反斜杠/引号/字面 `\n`，不绕过守卫）。

## 有界与安全

`SequenceMatcher` 与 `bisect` 均 stdlib 无新依赖；opcodes 线性于 old/new 长度（既有 `PATCH_STRING_CHARS=64KiB` 上限）；`_build_orig_to_norm_map`/`_norm_end_to_orig` 线性；回退守卫保证区域归一不等时不强行映射；拼接环 reversed(spans) 用原始 content 偏移有效；写管线指纹 CAS/审批/原子 publish/unknown 不重放不变；近似命中仍禁 replace_all。无新表/迁移/路由/worker 协议帧/源文件/跨平台改动。

## 明确不做（列后续）

- V4A 多文件格式补丁；非 unicode 策略的排版保留；替换侧其他 shaping。不宣称参考全集对等。

## 验证计划

domain `_unicode_edit_plan`（norm_old/opcodes）、`_preserve_unicode_in_replacement`（equal 保留 em-dash/智能引号/省略号、replace 施加 new、delete 跳过、回退守卫归一不等返回 verbatim、多字符扩展内部 norm 索引 bisect 归属）；patch_replace 集成（unicode 匹配 new 共享排版前缀/后缀保留文件原字符、wholesale replace 零回归、trimmed 回退 verbatim、与重锚顺序组合、replace_all 逐区域各自排版）；调用方 files_patch+worker_safety+files_search 零回归。strict 本批零新错误（无新源文件，余 5 既有 repository.py 不追逐）；ruff。未全量；whole_*_complete 保持 false。

## 验证结果（2026-11-20）

- **专项**：`uv run --no-sync pytest -q -n 0 tests/v2/core/test_v2_files_patch.py`（agent/）→ **89 passed**（新增 10 节点：plan/preserve 单元 2 + em-dash/智能引号/省略号/delete/回退/replace_all 逐区域/同缩进集成 7 + worker 1；既有 79 全过**零回归**——既有 unicode 用例均 wholesale-replace 或仅共享 ASCII/trimmed 回退）。
- **调用方**：worker_safety + files_search + capabilities + business_inventory + integrations/routing → **180 passed / 0 failed**（50.41 秒）。
- **strict**（仓库根，`pyrightconfig.strict.json`，pyright 1.1.414）：filesAnalyzed **414**、errors **5**、warnings 0；5 个均为另一会话已提交 `adapters/persistence/repository.py`（L633/L659×2/L710×2，commit 6bcb0e98）**既有错误，本批未触碰**。本批初现 `files_patch.py:438` `reportReturnType`（`get_opcodes()` 返回 `list[tuple[Literal['replace'|'delete'|'insert'|'equal',...]]`，`list` 不变性致不可赋给 `list[_Opcode]`（tag=str）），按 pyright 建议改 `_UnicodePlan=tuple[str, Sequence[_Opcode]]`（`Sequence` 协变）修正，复跑本批源零新错误。
- **ruff**：check + format 3 文件全过（domain/files_patch.py、composition/capabilities.py、test_v2_files_patch.py）。
- 证据：[docs/evidence/v2-files-patch-unicode-preserve.json](../../evidence/v2-files-patch-unicode-preserve.json)。未全量；whole_core_parity_complete / whole_reference_feature_audit_complete / whole_v2_complete 保持 **false**。