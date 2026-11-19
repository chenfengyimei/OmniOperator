# V2 工作区搜索正则模式（K13.search_regex）

状态：已实现并专项通过（2026-11-20）。学习参考 hermes-agent `tools/file_operations_search.py` 的 regex 搜索语义，独立实现于 V2 既有四层链，不复用 V1 ToolRegistry。

## 范围

`files_search` 内容搜索新增 `use_regex` 布尔轴（默认 `false`，子串合同字节等价零回归）：

- `use_regex=true` 时 `query` 作为**大小写敏感**正则（rg 默认语义；子串模式保持大小写不敏感）。
- 与 `output_mode`（content/files_with_matches/count）、`context`（0-3 邻接线）、`offset`（0-1000 分页）、`order`（discovery/modified）、`file_glob` 正交组合；命中形状/每文件 5 命中上限/50 结果页/2000 文件/5 秒/256KB/二进制与硬链接跳过剪枝全部沿用。
- `target=files` 忽略 `use_regex`（query 即 basename glob，同 `file_glob` 先例）。
- 响应 `data` 增 `use_regex` 布尔；只读 `Effect.NONE`，路由不变（`files_search` 已在 RETRIEVAL_TOOLS agent 档）。

## 有界正则安全设计（保守静态门，非 ripgrep 全集）

Python `re` 无匹配超时且存在灾难性回溯；V2 不引入第三方正则引擎，采用**编译前静态拒绝超线性构造**：

1. 经 `re._parser.parse` 取语法树（无 typeshed stub，经 `importlib` + 类型化 Protocol 承接，strict 全检查）；语法非法 → `ValueError` → 分派前 `invalid_capability_request`（ERROR/NONE，零文件读取）。
2. 拒绝构造（`_reject_unbounded_regex` 递归 walk，guarded=处于量词或环视内部）：
   - 反向引用/条件引用（GROUPREF/GROUPREF_EXISTS）——指数回溯；
   - 嵌套量词（`(a+)+`、`(?:a+)+`、`(a{2}){3}`）；
   - 量词下的交替/环视（`(a|ab)*`、`(?=a+)b` 外层再量词）；
   - 环视内部的量词/交替（`(?=a+)`、`(?!x+)`）——逐位置超线性；
   - 有限重复上界 > `SEARCH_REGEX_MAX_REPEAT=1024`（`a{1025}` 拒；无界 `*`/`+` 单层允许）。
3. 接受构造：字面量/字符类/单层量词（贪婪/惰性/占有）/分组/顶层交替/锚点/无量词环视。解析器把单字符交替优化为字符类（`(a|b)+`→`[ab]+`）属线性，正确接受。
4. 剩余模式在 256KB 单行文件上近线性；叠加既有 200 字符 query 上限、每文件 5 命中、50 页、2000 文件、5 秒扫描界。**保守门有意拒绝部分合法但无法证明线性的模式**（显式报错而非静默降级），不宣称 ripgrep 语法/性能全集对等。

## 实现位置

- `domain/files_search.py`（既有模块，无新源文件）：`normalize_use_regex`（None→False、严格 bool）、`compile_search_regex`（parse→安全门→`re.compile`）、`_reject_unbounded_regex`、`regex_match_lines`/`regex_count_matches`；`match_lines` 主体抽为共享 `_line_hits`（子串路径行为不变）。
- `adapters/workers/main.py` `_files` files_search 分支：`use_regex` 分派前校验，regex 仅 content 目标编译（非法零扫描即拒），content/files_with_matches/count 三分支按 regex 选择匹配器，data 增 use_regex。
- `composition/capabilities.py`：spec 增 `use_regex` boolean 与描述；`domain/routing.py` 不变。

无新表/迁移/路由/worker 协议帧/源文件/跨平台改动。

## 验证

- 新增 33 项回归（normalize/安全门接受与拒绝×11/语法非法×4/大小写敏感与截断与每文件上限/context 形状/count 全量/worker 内容/default 零回归/三输出模式+context/file_glob+order+offset 组合/每文件 5 上限/分派前拒×5/非 bool 拒×4/files 目标忽略/spec），合既有 77 共 110 过。
- 调用方：files_search 110 + worker_safety 31 + files_patch 41 + routing 16 = **198 过 0 失败**（48.50 秒）。
- strict（pyrightconfig.strict.json，仓库根）：本批 4 文件零错误；余 5 错误均为既有他会话已提交 `adapters/persistence/repository.py`（6bcb0e98，L633/659/710），沿用前批纪律不追逐。
- ruff check/format 通过（format 仅折叠本批新增代码）。

## 明确不做

ripgrep 二进制/语法全集（ lookahead 内量词、嵌套量词等被保守拒绝）、大小写不敏感 regex 旗标、多行模式、`--replace`、glob 否定 `!`、高级 gitignore 语义；真实大仓性能基准另验。