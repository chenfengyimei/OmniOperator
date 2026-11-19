# V2 工作区搜索邻接线 files_search context（K13 可离线缺口 搜索 context）

## 目的与参考

实现 [K13 工具/渠道/cron 审计](V2_TOOL_CHANNEL_AUDIT.md) 记录的**可离线缺口：工作区搜索 `context` 邻接线**（参考 hermes-agent `file_operations_search.py` `_parse_search_context_line` 与 rg `-C N` 上下文行）。V2 `files_search` content 模式此前只返回匹配行本身（path/line/text），模型无法看到命中行邻近代码。本批给 content 搜索补 `context`（0–3）：每条命中附 `before`/`after` 有界邻接行（`{line,text}`，文件边缘裁剪）。

**不含** offset/head_limit 分页、regex、modified 排序、file_glob 内容过滤、ripgrep 后端（仍列后续）。**无新表/迁移/路由/worker 协议帧/源文件/跨平台改动**——复用既有 `files_search` builtin worker 与 `workspace_path` 安全剪枝。

## 实现（V2 四层）

- **纯领域 `domain/files_search.py`**（零 I/O，既有模块加函数，**无新源文件**）：`SEARCH_CONTEXT_LINES=3`；`normalize_context`（None→0、须 `type is int` 且 `0<=v<=3`，拒 bool/float/str/越界）；`match_lines(text, query, context=0)` 增可选第三参——`context==0` 形状不变（`{line,text}`），`context>0` 每命中附 `before`（`[max(0,index-context), index)` 行）/`after`（`(index, min(n,index+1+context)]` 行）有界邻接 `{line,text}`（snippet 200、strip、文件边缘裁剪），每文件 5 命中上限不变。
- **worker `adapters/workers/main.py`**：`_files` files_search 分支增 `context_lines=normalize_context(args.get("context"))`（分派前校验，非法→ERROR/NONE；**局部名 `context_lines` 避免遮蔽 `_files` 的 `context` 工作区字典参量**）；content 模式 `match_lines(text, query, context_lines)`，`context_lines>0` 时结果行附 `before`/`after`（默认 0 形状不变）；`data` 增 `context`。files_with_matches/count 模式忽略 context（仅 content 附邻接线）。50 结果/2000 文件/5 秒/256KB/二进制硬链接跳过剪枝与 truncation 沿用。
- **`composition/capabilities.py`**：`files_search` spec description 述 context、properties 增 `context` integer minimum0 maximum3；required 仍 `["query"]`、writes=False、resource=workspace 不变。
- **`domain/routing.py` 不变**：`files_search` 已在 `RETRIEVAL_TOOLS`（agent 档只读）。

## 保持的合同

只读无写事务（Effect.NONE）；workspace 安全路径剪枝；有界（query 1-200、context 0-3、50 结果、2000 文件、5 秒、256KB、每文件 content 5 命中/snippet 200）；**响应帧界**：context≤3 时 50 命中×7 行×200 字符（含多字节）×2（text+data）远小于 `MAX_FRAME=1MiB`；非法 query/target/output_mode/context 分派前 ERROR/NONE。**无新表/迁移/路由/worker 协议帧/源文件/跨平台改动**。

## 验证

- `agent/tests/v2/core/test_v2_files_search.py` 增 **7 项**（normalize_context 默认/0-3/拒 bool/float/str/越界、match_lines context 邻接有界与边缘裁剪与默认 0 形状不变、worker content context 返回 before/after、worker 默认 context0 形状不变、worker count 模式忽略 context、worker 非法 context 5 参数化分派前拒、spec context integer 0-3 暴露），合既有共 **37 项**全过；files_search+worker_safety+files_patch 调用方 **93 过/0 失败**（8.80 秒）。
- 首轮曾 18 失败：局部 `context` 变量遮蔽 `_files` 的 `context` 工作区字典参量致 `context["workspace"]` TypeError（ broad except 掩盖为 invalid_capability_request），改名 `context_lines` 修复；另 content 分支须显式携带 before/after 入结果行（首轮遗漏致 1 失败），补后全过。
- V2 strict `pyrightconfig.strict.json` **410 文件零错误/警告**（无新源文件 include 不变）；ruff check/format 通过。
- `project_index` 运行（无新源文件，`declared_tools` 不变）；**聚合 `docs/index` 暂缓提交**（他会话未提交持久层编辑污染全局 `source_sha256`），再生推迟到工作树洁净。

## limits 与后续

`context` 仅 content 模式（files_with_matches/count 忽略）；邻接线有界 0-3、文件边缘裁剪、snippet 200。**不含 offset 分页/regex/modified 序/file_glob/ripgrep**（仍列后续独立批次）。K13 其余可离线缺口（补丁 escape/unicode/相似度/V4A、浏览器对话框、cron 技能绑定需 schema、代码沙箱、原始 CDP）仍后续。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false；不提前关闭任何对标项。