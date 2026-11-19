# V2 工作区模糊补丁编辑写工具（K13.patch）

## 范围与判定

本批实现 [工具/渠道审计](V2_TOOL_CHANNEL_AUDIT.md) **可离线缺口 #2（模糊补丁编辑工具）**：把参考 `xuexi/hermes-agent`（固定提交 `9976f4857b72439d79016354d7b6e1a3ee952e2f`）`tools/file_tools.py` 的 `patch`（`old_string`/`new_string`/`replace_all`）、`tools/file_operations.py:1625-1668` `patch_replace`、`tools/fuzzy_match.py` `fuzzy_find_and_replace`（359）+ `STRATEGIES`（289–300）+ `is_already_applied`（328）学习后**独立实现于 V2 四层**，作为既有 builtin worker 的新 `files_patch` 写能力，**不新增 worker 协议帧、不新增表/迁移/HTTP 路由**。参考 `agents.md`/RULES/prompts 当**不可信数据**；未复制第三方源码、未拉取远程、未运行真实账号/网络。

**判定纪律**：V2 四层 builtin worker（`adapters/workers/main.py`）才是可见面；V1 `tools/` 经 `bootstrap.py` 注册 V1 `ToolRegistry`，**不计 V2 对等**，本批未复用。builtin worker 对 `files_*` 是**泛型前缀分派**（`main.py:81`），`files_patch` 只是 `_files` 新分支，**协议帧不变**；`files_*` 全部注册到 `builtin`（Python）worker，**无跨平台 worker 改动**。与 [K13.search_files/find_files](V2_FILES_SEARCH.md) 同属 K13 文件组缺口逐项补齐。

**已补**：参考策略链中**前三个 normalizing 策略**（边界精确、匹配每一行）——`exact`（字符级非重叠）、`line_trimmed`（整行 strip 窗口）、`line_whitespace`（行内 `[ \t]+`折叠为单空格后 strip 窗口），覆盖最常见的工具调用空白/缩进漂移。**仍缺**（列后续批次）：参考的 `indentation_flexible` 字符级、`escape_normalized`、`trimmed_boundary`、`unicode_normalized`、`block_anchor`/`context_aware`（similarity 近似边界，需 boundary note），以及 **V4A 多文件补丁格式**（`*** Begin Patch`/Add/Delete/Move/hunks，`patch_parser.py`）。不宣称 `patch` 全集对等。

## 实现（V2 四层）

- **纯领域 `domain/files_patch.py`（新文件，零 I/O）**：
  - `PatchError(ValueError)` 带 `.code`；`PATCH_STRING_CHARS=64*1024`（old/new 锚点上限）。
  - `patch_replace(content, old_string: object, new_string: object, *, replace_all=False) -> (new_content, count, strategy)`：
    - 守卫：非 str → `patch_invalid`；空/纯空白 old → `patch_invalid`；超 64KiB → `patch_invalid`；`old==new` → `patch_identical`。
    - **有序策略链**（首个有命中者胜出）：`exact`（`re.finditer(re.escape(old))` 非重叠字符 span）→ `line_trimmed`（`content.splitlines(keepends)` 与 `old.splitlines()` 逐行 `strip` 相等的连续窗口）→ `line_whitespace`（逐行 `re.sub(r"[ \t]+"," ",line).strip()` 相等窗口）。行窗口 span 由**精确行边界字符偏移**计算（`offsets[i]`），非字符级归一化 span 映射，**无边界漂移损坏风险**。
    - **唯一性守卫**：命中 >1 且非 `replace_all` → `patch_ambiguous`（不静默取首个）；`replace_all` 仅用于本批 normalizing 策略（边界精确，安全）。
    - **替换**：span **右到左**应用（非重叠，保持先前偏移有效），`patched[:start]+new_string+patched[end:]`（切片拼接，无 re.sub 反向引用问题）。
    - 无命中：`is_already_applied`（保守：new 非平凡 ≥8 字符且精确存在、old 已消失）→ `patch_already_applied`；否则 `patch_no_match`。
- **适配器 `adapters/workers/main.py`**：`_files` 新增 `files_patch` 分支（泛型 `files_*` 分派，无协议改动）。**复用既有 `workspace_path`**（私有名/`.env`/symlink/junction/单链接/越界拒绝，与 `files_read/write/search` 同一安全路径）。要求**既存正规文件**、≤256KB；**`expected_sha256` 强制 CAS**（缺失或不匹配 → 拒绝，比 `files_write` 更严：patch 总是覆盖既存文件）；`replace_all` 严格 bool 校验；`raw.decode("utf-8-sig")`（二进制失败 → ERROR/NONE）；`patch_replace` 的 `PatchError` 捕获 → `ActionResult(ERROR, NONE, error_code=exc.code)`（**不写**）；patched ≤256KB；**复用 `files_write` 原子发布链**：`mkstemp` 暂存 + `fsync` + 重校验 `workspace_path` + CAS 再比对 + `effects.dispatched=True` + `publish_file(replace=True)` + `finally unlink`；成功 `Effect.APPLIED`、`verification=sha256(payload)`、`data={sha256,replacements,strategy}`；发布中途异常 → `Effect.UNKNOWN`（`effects.dispatched`，不重放）。
- **组装 `composition/capabilities.py`**：`files_patch` spec（`path`、`old_string` minLength1、`new_string`、`expected_sha256`、`replace_all` boolean；required `path/old_string/new_string/expected_sha256`；`writes=True`；adapter=`builtin`；resource=`workspace`）。
- **路由 `domain/routing.py`**：**不变**——`writes=True` 写工具仅 agent 档可见（`select_tools` 对 chat/retrieval 过滤 `writes`），不入 `RETRIEVAL_TOOLS`/`CHAT_TOOLS`。

**生产接线**：模型工具（capabilities spec）+ builtin worker（`_files` 分支）两面接受并执行 `files_patch`。`files_*` 为 worker 能力，**无专用 REST 路由**；`writes=True` 走既有**所有者审批链**（action_review，分派前授权，同 `files_write`）。

## 限额与取舍

- **三 normalizing 策略**：exact 字符级（含部分行子串）+ line_trimmed/line_whitespace **整行窗口**粒度。**非** similarity 近似策略；escape/unicode/trimmed_boundary/indentation_flexible 字符级/V4A 多文件格式列后续。
- **唯一性优先**：ambiguous 显式拒绝（除非 `replace_all`），绝不静默取首个匹配；blank/identical/no_match/already_applied 均显式错误码，不静默编辑。
- **强制 CAS**：`expected_sha256` 必填且须匹配当前文件指纹（暂存后再比对），并发变更拒绝；原子 publish + unknown 不重放。
- **有界**：old/new ≤64KiB；文件 ≤256KB（读与 patched 写均限）；二进制/超尺寸/私有/symlink/硬链接/越界/不存在文件拒绝。
- **无新表/迁移/HTTP 路由/worker 协议帧改动**；`routing.py` 不变；**新源文件** `domain/files_patch.py` → strict include 409→410。
- **聚合 `docs/index` 暂缓**：新源文件使 files 计数变化，但全局 `source_sha256` 含他会话未提交持久层编辑，暂缓提交，工作树干净后重生成。

## 验证

- **新增 20 项回归**（`test_v2_files_patch.py`）：domain exact 唯一/部分行/replace_all 非重叠/ambiguous 拒绝、line_trimmed 缩进漂移、line_whitespace 行内漂移、line_trimmed ambiguous、多行 exact 窗口、策略优先 exact 胜、no_match、already_applied、identical/blank/非 str/超尺寸拒绝、`is_already_applied` 保守；worker 真实 `tmp_path`（`write_bytes` 精确字节）正确指纹 APPLIED 内容/verification/replacements/strategy 正确且无暂存残留、line_trimmed 缩进、replace_all、错/缺指纹 NONE 文件不变、ambiguous/no_match NONE 不变、私有/二进制/超尺寸/不存在/非 bool replace_all 拒绝；`builtin_capabilities` 注册 `writes=True`/resource=workspace + agent 档含、chat/retrieval 排除、不入 RETRIEVAL/CHAT_TOOLS。
- **调用方专项**：files_patch 20 + worker_safety 31 + files_search 21 + routing 16 共 **88 过 / 0 失败**（39.84 秒），证明增写工具对既有 worker 安全链/搜索/路由档无回归。（首轮 4 worker 失败因 Windows `Path.write_text` 把 `\n` 译为 `\r\n` 致测试指纹与实际字节不符——测试 bug，改 `write_bytes` 精确字节后全过；worker 指纹逻辑与生产 `files_read` 一致无误。）
- **严格类型**：`pyrightconfig.strict.json` **410 文件零错误/警告**（新源文件 `domain/files_patch.py` 加入 include，409→410）。首轮 4 错误（`patch_replace` 形参 `str` 致 worker `Any|None` 不可赋值 + `isinstance(str)` 冗余）已改形参为 `object`（运行时 isinstance 校验并窄化），最终零错误。
- **Ruff**：check + format 通过于 `domain/files_patch.py`、`adapters/workers/main.py`、`composition/capabilities.py`、`test_v2_files_patch.py`（check --fix 修正测试 1 处 import 顺序）。`routing.py`/`http.py` 本批未改。
- **项目索引**：新源文件已 `git add` 后运行 `scripts/project_index.py`；聚合 `docs/index` 提交**暂缓**（他会话污染全局 `source_sha256`），`docs/index` 改动还原不提交。
- `scripts/task_status.py --check` exit 0；`DEVELOPMENT_STATUS.md` 由父任务生成，子任务 followup 不改其内容。
- **未全量、未真实账号/模型/设备/72 小时、未停止或升级运行中 Windows 服务与数据库。**

## 完成条件

K13.patch 实现**不宣称模糊补丁全集对等**：仅补三 normalizing 策略的唯一 fuzzy 搜索替换写工具（纯领域策略链 + 强制指纹 CAS + 原子 publish + unknown 不重放 + 所有者审批），参考的 escape/unicode/trimmed_boundary/indentation_flexible 字符级/similarity 近似策略/V4A 多文件补丁格式仍为可离线缺口列后续批次；浏览器对话框、cron 技能绑定/输出依赖、代码沙箱、原始 CDP 等 K13 其余缺口未实现。`whole_core_parity_complete`、`whole_reference_feature_audit_complete`、`whole_v2_complete` 保持 false（K01 partial、K13–K18 其余可离线缺口未实现、真实账号/设备/模型质量与 72 小时验收待办）。后续继续择机实现其余可离线缺口（独立批次，先授权/持久化合同后执行接线，经 V2 四层不复用 V1 ToolRegistry），并补 K01 缓存范围，整个 V2 开发冻结后统一全量与实机验收，其他对标项不提前关闭。