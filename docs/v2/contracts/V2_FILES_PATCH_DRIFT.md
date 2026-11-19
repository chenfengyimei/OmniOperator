# V2 工作区补丁 escape-drift 自动纠正与近似边界注记（K13.patch_drift）

状态：已实现并专项通过（2026-11-20）。学习参考 hermes-agent `tools/escape_drift_autocorrect.py` 与 `tools/fuzzy_match.py` `boundary_note`，独立实现于 V2 既有 `domain/files_patch.py`，不复用 V1 ToolRegistry。

## 范围

1. **匹配前 escape-drift 验证自动纠正**（`correct_escape_drift`，在策略链之前运行）：
   - **引号漂移**：old/new 同时含 `\'` 或 `\"`（JSON 双重转义线艺特征）、old 不字面在文件中、且该引号种类在**整个调用所有出现处**都恰好被一个反斜杠转义（裸引号或更长反斜杠串意味着有意转义，不可区分则拒绝纠正）→ 剥离后的 old 必须**字面存在于文件**才纠正双侧；new 单独转义了 old 未转义的引号种类 → 显式 `patch_escape_drift` 拒绝（新代码意图无法证明，绝不猜测）。
   - **反斜杠倍增**：old 含反斜杠、不字面在文件中、所有反斜杠串长度为偶数、减半后的 old 字面存在于文件 → 仅纠正 old；new 含任何反斜杠 → 显式 `patch_escape_drift` 拒绝（减半可能删除有意反斜杠）。
   - 纠正成功返回注记（quote/backslash 各一文案），未触发返回原参数与 None；已字面精确的锚点绝不纠正。
2. **近似边界注记**（`boundary_note`）：仅 `block_anchor`/`context_aware` 命中时返回警告——编辑跨度是近似定位而非 old_string 字面边界，需复核变更落点（偏移边界可能在编辑区外重复/丢行）；归一化策略逐行精确匹配，不注记（避免教模型忽略常规编辑）。
3. **`patch_replace` 返回四元组** `(new_content, match_count, strategy, note)`：note 合并纠正注记与边界注记（`; ` 连接）或 None。纠正后再做 identical 检查（纠正后相同 → `patch_identical`）。歧义/无匹配/已应用错误合同不变。

## 实现位置

- `domain/files_patch.py`（既有模块，无新源文件）：`correct_escape_drift`/`boundary_note` 及私有 `_halve_backslash_runs`/`_all_backslash_runs_even`/`_strip_spurious_quote_escapes`/`_quote_escapes_all_spurious`；`patch_replace` 签名扩展。
- `adapters/workers/main.py`：解包四元组；成功文本 `"File patched"` 后附 note（如有），`data` 增 `note`（JSON null 或字符串）。**写管线不变**：强制指纹 CAS、staging 后复核、原子 publish、unknown 不重放、所有者审批复用；drift 歧义在**任何写之前**以 `PatchError` → ERROR/NONE 拒绝。
- `composition/capabilities.py`：files_patch spec 描述更新；参数 schema 不变；routing 不变（写工具 agent 档）。

无新表/迁移/路由/worker 协议帧/源文件/跨平台改动。

## 验证

- 新增 15 项回归（引号纠正验证/精确锚点跳过/裸引号混合跳过/长反斜杠串跳过/new 未证明种类拒、反斜杠减半验证/new 含反斜杠拒/奇数串不纠正/减半锚点缺失不纠正、纠正后 identical 拒、boundary_note 仅近似、worker 引号纠正 APPLIED+note/歧义 drift 写前拒文件不变/近似 note 浮现/精确 note None），既有 41 项适配四元组后全过，共 **56 过**。
- 调用方：files_patch 56 + worker_safety 31 + files_search 110 + routing 16 = **213 过 0 失败**（46.68 秒；format 后 files_patch 56 复跑绿）。
- strict（pyrightconfig.strict.json，仓库根）：本批 4 文件零错误；余 5 错误均既有他会话已提交 `adapters/persistence/repository.py`（6bcb0e98 L633/659/710），沿用前批纪律不追逐。
- ruff check/format 通过（仅本批文件）。

## 明确不做

参考 `_detect_escape_drift` 匹配后字面 `\n` 漂移拒绝与 `_maybe_unescape_new_string` 替换侧解转义（仍后续）；V4A 多文件补丁格式；drift 纠正不绕过指纹 CAS/审批，不改变 unknown 不重放合同。