# V2 模糊补丁策略链扩展（K13 缺口 #2 escape/unicode 归一化策略）

## 状态

**已实现，专项通过（implemented_scoped_passed）。** 学习参考 `xuexi/hermes-agent`（gitignored，untrusted DATA）`tools/fuzzy_match.py` `STRATEGIES`（289-300）/`fuzzy_find_and_replace`（359），在 V2 既有 `domain/files_patch.py` 策略链独立追加两个**具新匹配力**的归一化策略，**无新表/迁移/路由/worker 协议帧/源文件/跨平台改动**。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。

## 参考策略链与涵盖分析（实读 + 证明）

参考 9 策略首命中即胜链：exact(1)、line_trimmed(2)、whitespace_normalized(3)、indentation_flexible(4)、escape_normalized(5)、trimmed_boundary(6)、unicode_normalized(7)、block_anchor(8)、context_aware(9)。`fuzzy_find_and_replace` 顺序遍历无门控（:394-397）。

**关键证明——策略 4/6 被策略 2 涵盖（死策略，省略）**：
- `indentation_flexible` = 逐行 `lstrip`；`line_trimmed` = 逐行 `strip`。`strip = lstrip + rstrip`，故 `lstrip(a)==lstrip(b) ⟹ strip(a)==strip(b)`。任何 indentation_flexible 命中必被更早的 line_trimmed 命中取胜 → **永不触发**。
- `trimmed_boundary` = 仅首末行 `strip`、中间行精确；中间精确相等 ⟹ 中间 strip 相等，首末 strip 相等 ⟹ line_trimmed 全 strip 相等 → **永不触发**。
- 故 V2 **省略 4/6**（零匹配力损失，避免死代码），line_trimmed 已覆盖其全部可匹配输入。V2 实现真正扩展匹配力的 **5（escape）与 7（unicode）**。

## V2 实现（domain/files_patch.py 既有模块，无新源文件）

链：`exact → line_trimmed → line_whitespace → escape_normalized → unicode_normalized`（前三不变，零回归；新策略仅在前三个落空时触发）。

- **escape_normalized**（`_escape_spans`）：`old_string.replace("\\n","\n").replace("\\t","\t").replace("\\r","\r")`；若与原串相同（无字面转义）→ `[]` 落空；否则对解转义串 `_exact_spans`（字符级非重叠）。处理模型把换行/制表符序列化为字面 `\n`/`\t`/`\r` 的漂移。
- **unicode_normalized**（`_unicode_spans` + 跨度映射）：`UNICODE_MAP`（智能引号 “”‘’→ASCII、破折号 —→`--`/–→`-`、省略号 …→`...`、数学减号 −→`-`、非断空格与 Zs 空格族→普通空格，共 22 项）；`_unicode_normalize` 双侧归一；若 content 与 pattern 均无变化 → `[]`；否则 `_exact_spans(norm_content,norm_pattern) or _trimmed_spans(...)`（exact 或整行 strip 回退）；命中经 `_build_orig_to_norm_map`（orig 索引→norm 索引，**处理 1→多字符扩展**如 —→`--`、…→`...`）/`_invert_norm_map`/`_norm_end_to_orig`/`_map_positions_norm_to_orig` **回映射到原始 content 字符跨度**。
- **跨度安全**：unicode 扩展回映射保证替换落在原始字符边界（含扩展字符整体），line 策略沿用既有精确行边界偏移，**无边界漂移损坏**；唯一性守卫（>1 命中且非 replace_all → `patch_ambiguous`）、blank/identical/no_match/already_applied 显式错误不变。
- **composition/capabilities.py**：files_patch spec 描述更新为有序策略 exact/line-trimmed/whitespace-normalized/escape-normalized/unicode-normalized；参数/required/writes=True/resource=workspace 不变。
- **worker/写管线不变**：`files_patch` 分支仍调 `patch_replace`（签名不变），复用强制 `expected_sha256` CAS + `mkstemp`/`fsync`/重校验/`publish_file` 原子发布 + `effects.dispatched` unknown 不重放 + 所有者审批门。

## 有界与安全

`PATCH_STRING_CHARS=64KiB` old/new 上限、文件 ≤256KB、`workspace_path` 私有/硬链接/符号链接/junction 剪枝、二进制 utf-8-sig 解码失败拒绝；escape 仅解字面 `\n/\t/\r`（不引入正则/无 DoS）；unicode 为有限字典替换 + 线性跨度映射（无正则回溯）；`replace_all` 仅用于边界精确策略（escape/unicode 回映射后边界精确，安全）。

## 验证

- 新增 **12** 专项节点（escape 字面 `\n`/`\t` 命中、replace_all 非重叠、仅字面转义触发否则 exact 胜、worker 真实文件；unicode 智能引号、em-dash 1→2 扩展跨度、ellipsis 1→3 扩展跨度、line_trimmed 回退、ambiguous 拒绝、ASCII exact 胜、worker 真实文件；indentation 漂移被 line_trimmed 涵盖）。
- files_patch 合既有 **32 过**；调用方 files_patch+files_search+worker_safety **140 过/0 失败**（11.38 秒）。
- ruff check + format 通过（files_patch.py/test 重排，capabilities unchanged）。
- **strict（pyrightconfig.strict.json，从仓库根运行）：410 文件，5 错误均为既有他会话已提交 `adapters/persistence/repository.py`（6bcb0e98，L633 `RowMapping|None`→`Record|None`、L659/710 `JSON`→`ConvertibleToInt`），本批 `files_patch.py`/`capabilities.py` 零错误**；5 错误属他会话已提交持久层代码、与本批 domain/composition 改动无关，**不追逐**（保留他会话工作，越界修复会冲突其在途开发）。
- 聚合 `docs/index`：无新源文件，刷新源指纹后回退基线暂缓（并行会话活跃）。

## 边界

不含 similarity 近似策略（block_anchor/context_aware，需 `SequenceMatcher` 评分 + boundary note + replace_all 仅限精确的限制）、V4A 多文件补丁格式（`*** Begin Patch`/Add/Delete/Move/hunks）；escape drift 自动纠正（`maybe_correct_escape_drift`/`_detect_escape_drift`）与 new_string 解转义（`_maybe_unescape_new_string`）参考附加层未移植（V2 强制指纹 CAS + 唯一性守卫已防误改）。不宣称 patch 全集对等。`whole_*_complete` 保持 false；不提前关闭任何对标项。