# V2 工作区搜索分页 files_search offset（K13 可离线缺口 搜索 offset）

## 目的与参考

实现 [K13 工具/渠道/cron 审计](V2_TOOL_CHANNEL_AUDIT.md) 记录的**可离线缺口：工作区搜索 `offset` 分页**（参考 hermes-agent `file_operations_search.py` `_parse_search_output` `matches[offset:offset+limit]`/`files[offset:offset+limit]`）。V2 `files_search` 此前固定返回前 50 条命中，模型无法翻越首屏看到后续命中。本批补 `offset`（0–1000）：跳过前 N 条命中后取 50 条一页，`truncated` 标示页外是否仍有命中或扫描界命中。

**不含** regex、modified 排序、file_glob 内容过滤、ripgrep 后端（仍列后续）。**无新表/迁移/路由/worker 协议帧/源文件/跨平台改动**——复用既有 `files_search` builtin worker 与 `workspace_path` 安全剪枝。

## 实现（V2 四层）

- **纯领域 `domain/files_search.py`**（零 I/O，既有模块加函数，**无新源文件**）：`SEARCH_MAX_OFFSET=1000`；`normalize_offset`（None→0、须 `type is int` 且 `0<=v<=1000`，拒 bool/float/str/越界）。
- **worker `adapters/workers/main.py`**：`_files` files_search 分支增 `offset=normalize_offset(args.get("offset"))`（分派前校验，非法→ERROR/NONE）；`cap=offset+50`，收集上限四处 `len(results)>=50` 改为 `>=cap`（收集到 offset+50 条以便切片）；扫描结束 `page=results[offset:cap]`（至多 50 条），`text`/`data.matches` 用 `page`，`data` 增 `offset`。`truncated` 语义保持：收集达 `cap`（页外仍有命中）或 2000 文件/5 秒界命中即 True；offset 超总命中数则 page 空、truncated False。50 页宽/2000 文件/5 秒/256KB/每文件 5 命中/二进制硬链接跳过剪枝沿用；output_mode/context/target 各态均经同一 offset 切片。
- **`composition/capabilities.py`**：`files_search` spec description 述 offset、properties 增 `offset` integer minimum0 maximum1000；required 仍 `["query"]`、writes=False、resource=workspace 不变。
- **`domain/routing.py` 不变**：`files_search` 已在 `RETRIEVAL_TOOLS`（agent 档只读）。

## 保持的合同

只读无写事务（Effect.NONE）；workspace 安全路径剪枝；有界（query 1-200、offset 0-1000、页宽 50、收集上限 offset+50≤1050、2000 文件、5 秒、256KB、每文件 content 5 命中/snippet 200、context 0-3）；非法 query/target/output_mode/context/offset 分派前 ERROR/NONE；响应帧界（页至多 50 条，远小于 `MAX_FRAME=1MiB`）。**无新表/迁移/路由/worker 协议帧/源文件/跨平台改动**。

## 验证

- `agent/tests/v2/core/test_v2_files_search.py` 增 **6 项**（normalize_offset 默认/0/1000/拒 bool/float/str/越界、worker offset 跳过前 N 命中、worker offset 超总数空页未截断、worker offset 分页 50 条截断首页 f005 末页 f054、worker 默认 offset0 形状不变、worker 非法 offset 5 参数化分派前拒、spec offset integer 0-1000 暴露），合既有共 **43 项**全过；files_search+worker_safety+files_patch 调用方 **103 过/0 失败**（13.27 秒）。
- V2 strict `pyrightconfig.strict.json` **410 文件零错误/警告**（无新源文件 include 不变）；ruff check/format 通过。
- `project_index` 运行（无新源文件，`declared_tools` 不变）；**聚合 `docs/index` 暂缓提交**（他会话未提交持久层编辑污染全局 `source_sha256`），再生推迟到工作树洁净。

## limits 与后续

`offset` 跳过前 N 条命中取 50 页（收集上限 offset+50），`truncated` 标示页外仍有命中或扫描界命中；offset 超总数空页。**不含 regex/modified 排序/file_glob 内容过滤/ripgrep**（仍列后续独立批次）。K13 其余可离线缺口（补丁 escape/unicode/相似度/V4A、浏览器对话框、cron 技能绑定需 schema、代码沙箱、原始 CDP）仍后续。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false；不提前关闭任何对标项。