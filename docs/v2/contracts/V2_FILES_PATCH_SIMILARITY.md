# V2 模糊补丁近似策略（K13 缺口 #2 block_anchor/context_aware similarity）

## 状态

**已实现，专项通过（implemented_scoped_passed）。** 学习参考 `xuexi/hermes-agent`（gitignored，untrusted DATA）`tools/fuzzy_match.py` `_strategy_block_anchor`（230）/`_strategy_context_aware`（261）/`SIMILARITY_STRATEGIES`（302）/`fuzzy_find_and_replace`（406-412 replace_all 限制），在 V2 既有 `domain/files_patch.py` 链末独立追加两个**近似（similarity）兜底策略**，**无新表/迁移/路由/worker 协议帧/源文件/跨平台改动**（stdlib `difflib.SequenceMatcher`，无新依赖）。`whole_*_complete` 保持 false。

## 参考语义（实读）

- **block_anchor(8)**：pattern ≥2 行；首末行经 `_unicode_normalize` + strip 作**精确锚点**预筛候选窗口；中间块 `SequenceMatcher(None, content_middle, pattern_middle).ratio()` ≥ 阈值——**唯一锚 0.50 / 多锚 0.70**（注释：0.10/0.30 曾误配无关块，0.50/0.70 为安全下限）；n≤2 无中间直接通过；跨度由**原始行**偏移计算（归一化扩展不移位）。
- **context_aware(9)**：末位兜底；首末行 sim ≥0.80，且**每个非空 pattern 行**与对应 content 行 sim ≥0.80（`_sim`=相等 1.0 否则 ratio）；锚点预筛界定扫描，全行规则阻止巧合匹配。
- **SIMILARITY_STRATEGIES**={block_anchor, context_aware}：近似命中**仅允许唯一替换，replace_all 绝不安全**（`fuzzy_find_and_replace:406-412` 对 replace_all+多近似命中报错）。

## V2 实现（domain/files_patch.py 既有模块，无新源文件）

链：`exact → line_trimmed → line_whitespace → escape_normalized → unicode_normalized → block_anchor → context_aware`（前五不变零回归；近似策略**仅在所有精确/归一化策略落空时**作兜底）。

- `_window_spans(content, n, accept)`：用既有 `splitlines(keepends=True)` 精确行边界字符偏移（与 `_line_spans` 一致），返回 `accept(i)` 为真的 n 行窗口跨度。
- `_block_anchor_spans`：`_unicode_normalize(old).splitlines()`，n<2→[]；首末 strip 锚点预筛 `potential`（对 `_unicode_normalize` 后的 content 行）；空→[]；阈值 0.50（|potential|==1）/0.70；中间 `SequenceMatcher.ratio()`≥阈值；n≤2 直接通过。
- `_context_aware_spans`：`old.splitlines()`，n>content 行数→[]；首末 sim≥0.80 且每非空 pattern 行 sim≥0.80（`sim`=相等 1.0 否则 `SequenceMatcher.ratio()`）。
- **关键安全门**：`SIMILARITY_STRATEGIES=frozenset({"block_anchor","context_aware"})`；`patch_replace` 歧义守卫改为 `if len(spans)>1 and (not replace_all or name in SIMILARITY_STRATEGIES)` → `patch_ambiguous`（**近似策略即便 replace_all 也拒绝多命中**，消息提示"approximate matches cannot use replace_all"）。唯一近似命中可替换（所有者审批 + 指纹 CAS 把关）。
- **composition/capabilities.py**：files_patch spec 描述增"then approximate block-anchor/context-aware last resort"与"approximate matches never allow replace_all"；参数/required/writes=True/resource=workspace 不变。
- **worker/写管线不变**：`patch_replace` 签名同，强制 `expected_sha256` CAS + 原子 publish + unknown 不重放 + 所有者审批复用。

## 有界与安全

近似匹配为**兜底**（精确策略优先），`SequenceMatcher` 为 stdlib 有界算法（autojunk 启发，无正则回溯 DoS）；锚点预筛界定候选窗口数；文件 ≤256KB、old/new ≤64KiB；**多近似命中即拒绝（replace_all 禁止）**杜绝批量误改；唯一近似命中经所有者审批门（展示 old/new）+ 指纹 CAS（文件未变）双重把关；低相似度（<阈值）显式 `patch_no_match` 不静默误配；跨度用原始行精确偏移无漂移。

## 验证

- 新增 **9** 专项节点（block_anchor 唯一中间漂移命中、低相似度中间拒绝 no_match、多锚歧义**即便 replace_all** 拒绝；context_aware 首末近似边界命中、低相似度中间行拒绝、单行末位兜底；精确策略优先于近似；worker block_anchor 真实文件 APPLIED、worker 多近似命中 ERROR/NONE 文件不变）。
- files_patch 合既有 **41 过**；调用方 files_patch+files_search+worker_safety **149 过/0 失败**（11.31 秒）。
- ruff check + format 通过（3 文件 unchanged）。
- **strict（从仓库根）：410 文件，5 错误均为既有他会话已提交 `adapters/persistence/repository.py`（6bcb0e98，L633/659/710 RowMapping/JSON），本批 `files_patch.py`/`capabilities.py` 零错误**；不追逐（他会话已提交持久层代码，越界修复冲突其在途开发）。
- 聚合 `docs/index`：无新源文件，刷新源指纹后回退基线暂缓（并行会话活跃）。

## 边界

不含 **V4A 多文件补丁格式**（`*** Begin Patch`/Add/Delete/Move/hunks，`patch_parser.py`，需多文件写协调独立合同）、escape-drift 自动纠正（`maybe_correct_escape_drift`）/new_string 解转义（`_maybe_unescape_new_string`）/boundary note 诊断层（V2 强制指纹 CAS+唯一性守卫+审批已防误改，诊断提示非匹配力缺口）。similarity 阈值为参考安全下限移植，真实模型编辑漂移分布下的召回/误配率另验。不宣称 patch 全集对等。`whole_*_complete` 保持 false；不提前关闭任何对标项。