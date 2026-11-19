# V2 工作区搜索输出模式 files_search output_mode（K13 可离线缺口 搜索 output_mode）

## 目的与参考

实现 [K13 工具/渠道/cron 审计](V2_TOOL_CHANNEL_AUDIT.md) 记录的**可离线缺口：工作区搜索 `output_mode`**（参考 hermes-agent `file_operations_search.py` `_OUTPUT_MODE_FLAGS={"files_only":"-l","count":"-c"}`、`_parse_search_output` content/files_only/count 三态）。V2 `files_search` 此前 content 目标只返回匹配行（path/line/text），files 目标按 basename glob 发现文件名；**无「仅列出命中文件」与「每文件命中计数」两种 rg 常用输出态**。本批给 content 搜索补 `output_mode` 轴：`content`（默认，匹配行）/`files_with_matches`（去重命中路径）/`count`（每文件命中行总数）。

**不含** context 邻接线（-A/-B/-C）、offset/head_limit 分页、regex、modified 排序、file_glob 内容过滤、ripgrep 后端（仍列后续）。**无新表/迁移/路由/worker 协议帧/跨平台改动**——复用既有 `files_search` builtin worker 能力与 `workspace_path` 安全剪枝。

## 实现（V2 四层）

- **纯领域 `domain/files_search.py`**（零 I/O，既有模块加函数，**无新源文件**）：`SEARCH_OUTPUT_MODES=("content","files_with_matches","count")`；`normalize_output_mode`（None→content、须 str 且属三态否则 ValueError）；`count_matches`（大小写不敏感子串**全量**命中行计数，不受 `match_lines` 每文件 5 命中上限约束，文件已由调用方 256KB 尺寸界定）。`match_lines`/`match_name`/`normalize_query` 不变。
- **worker `adapters/workers/main.py`**：`_files` files_search 分支增 `output_mode=normalize_output_mode(args.get("output_mode"))`（分派前校验，非法→ERROR/NONE）；content 目标按 mode 产出——content 沿用 `match_lines` 命中行；files_with_matches 有命中即追加单条 `{path}`（每文件一条）；count 经 `count_matches` 全量计数 >0 追加 `{path,count}`。50 结果/2000 文件/5 秒/256KB/二进制硬链接跳过剪枝与 truncation 沿用；`data` 增 `output_mode`。files 目标（basename glob）行为不变。
- **`composition/capabilities.py`**：`files_search` spec description 述三态、properties 增 `output_mode` enum；required 仍 `["query"]`、writes=False、resource=workspace 不变。
- **`domain/routing.py` 不变**：`files_search` 已在 `RETRIEVAL_TOOLS`（agent 档只读），tier 过滤不变。

## 保持的合同

只读无写事务（Effect.NONE）；workspace 安全路径剪枝（私有/硬链接/符号链接/junction 跳过、相对路径）；有界（query 1-200 无控制字节、50 结果、2000 文件、5 秒、256KB、每文件 content 5 命中/snippet 200）；非法 query/target/output_mode 分派前 ERROR/NONE；count 全量计数仍受文件尺寸界约束。**无新表/迁移/路由/worker 协议帧/源文件/跨平台改动**。

## 验证

- `agent/tests/v2/core/test_v2_files_search.py` 增 **9 项**（normalize_output_mode 默认/三态/非法拒、count_matches 超 5 上限全量计数/无命中 0/多行、worker files_with_matches 去重路径仅 {path}、worker count 每文件全量 12、worker content 默认形状不变、worker count 50 文件截断、worker 非法 output_mode 4 参数化分派前拒、spec output_mode enum 暴露），合既有共 **30 项**首轮全过；files_search+worker_safety+files_patch 调用方 **83 过/0 失败**（9.88 秒），routing 调用方 **16 过**（42.75 秒）。
- V2 strict `pyrightconfig.strict.json` **410 文件零错误/警告**（无新源文件 include 不变）；ruff check/format 通过（4 文件无重格式化）。
- `project_index` 运行（无新源文件，`declared_tools` 不变）；**聚合 `docs/index` 暂缓提交**（他会话未提交持久层编辑污染全局 `source_sha256`），再生推迟到工作树洁净。

## limits 与后续

`output_mode` 仅 content 搜索轴（files 目标 basename glob 不变）；count 为每文件命中行总数（非全文出现次数），files_with_matches 为去重路径。**不含 context 邻接线/offset 分页/regex/modified 排序/file_glob/ripgrep**（仍列后续独立批次）。K13 其余可离线缺口（补丁 escape/unicode/相似度/V4A、浏览器对话框、cron 技能绑定需 schema、代码沙箱、原始 CDP）仍后续。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false；不提前关闭任何对标项。