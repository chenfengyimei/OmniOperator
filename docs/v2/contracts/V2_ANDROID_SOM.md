# V2 Android SoM 编号截图设计合同（K14 可离线缺口）

**状态：`offline_core_implemented_not_wired`（2026-11-22）。** 离线核心（节点准入/编号/PNG 渲染）已实现并专项通过；D0–D3 仍未决，**没有**模型工具、HTTP 路由、对象发布接线、迁移或真机验收。

## 1. 范围

- 输入：**已经由所有者授权路径取得**的 Android 截图（`CapturedFrame` PNG / 既有 `session_captures` 对象）＋同次 `android_observe` 的节点列表（`node_id`、bounds、class、actions、password/visible 标记）。
- 处理：纯本地图像叠加编号，不重新采集设备、不读新屏幕、不调用模型、不做 OCR。
- 输出：一张编号 PNG ＋ 有界映射（编号 → `node_id`/bounds），供既有图像/对象发布链使用；编号与 observe 的元素索引一致。
- 不做：自动附加到模型消息、自动写 memory/persona、自动点击/输入、设备健康矩阵、OCR/文字识别、跨 Agent 共享、任意 URL/路径输入。
- 与 K14 视觉子代理的关系：SoM 只是帧来源的一种（若将来 D3 选择 Android 帧），不改变视觉子代理「有界帧、无工具、单次、仅文本」合同。

## 2. 参考语义（固定提交）

参考 `xuexi/android-agent` 固定提交 `7c09f6cd` 的 `screenshot_annotated`：对 `get_elements` 的节点做 set-of-mark 编号叠加，编号与元素索引匹配；参考实现只做视觉标注，不代替动作审批。V2 只继承「同一观察的节点→稳定编号→叠加图」语义，不复制其绘制样式、字体、颜色或工具协议。

## 3. 待所有者/ A 决策

| ID | 决策 | 选项 | 未决时 |
|---|---|---|---|
| D0 | 可见面 | 仅离线核心 adapter／`android_observe` 可选 `mark=true`／独立所有者 HTTP 路由 | 仅离线核心，不接模型/HTTP |
| D1 | 输出发布 | 复用 `session_captures`/objects（推荐）／仅写入调用方临时目录 | 复用既有对象/会话引用，不新增表 |
| D2 | 编号策略 | observe 顺序 1-based（推荐，匹配索引）／空间排序（上→下、左→右） | observe 顺序 |
| D3 | 失败语义 | 截图为空/节点超限/绘制失败 → 显式错误、不返回半张图 | 显式 `android_som_*` 错误 |

**D0 未决不新增模型工具/HTTP；D1 未决不新增表/迁移。** 若 D0 选 `android_observe` 选项，需改共享 capability schema/路由并由 A 协调；若选独立路由，需所有者鉴权与 no-store。

## 4. 纯逻辑合同

### 4.1 节点准入

- 仅接受同一次观察的节点；`node_id` 规范 UUID 且不重复。
- 只标注 `visible != false` 且有合法 bounds 的节点；`password=true` 的节点**不标注**（不画框、不编号、不进入映射），避免把密码字段位置显式高亮。
- bounds 必须为有限整数且 `left<right`、`top<bottom`；裁剪到图像边界，完全越界的节点跳过并计数，不伪造坐标。
- 标记上限 ≤50；超过则按 observe 顺序取前 50，并返回 `truncated=true`（不静默丢弃）。
- 编号 1-based、唯一、严格按 observe 顺序；同一输入重复调用编号稳定。

### 4.2 图像边界

- 输入 PNG 必须经既有 `CapturedFrame`/图像 codec 校验：PNG、单边 ≤8192、总像素 ≤16,000,000、≤16MiB。
- 输出 PNG 同样受既有对象上限约束；绘制在 RGB/RGBA 副本上进行，不修改原图对象。
- 编号绘制不得改变图像尺寸；标记中心/文字尽量落在节点内，越界时贴边；颜色/描边要有足够对比度。
- 不把节点文本、`node_id`、object_id、路径或 sha 写进图像像素；图像上只出现数字。
- 绘制失败、编码失败或输出超限时显式失败，不返回未完成图。

### 4.3 输出与发布

- 返回 `{image: <object ref/bytes>, marks: [{number, node_id, bounds}], truncated, skipped}`；`node_id` 只在内部映射/所有者可见面出现，模型侧默认只见编号图与有界数量，不携带原始节点文本。
- 复用既有 `session_captures`/objects 发布时，仍绑定 grant/session/content_epoch；来源删除/擦除按既有屏障失效。
- 不自动附加模型，不写长期记忆/人格，不投递。

## 5. 测试计划（实现时）

1. 纯逻辑：0/1/50/51 节点、重复/非法 UUID、越界/反向 bounds、password 跳过、visible=false、编号顺序与 truncated。
2. 图像：真实 Pillow 小图绘制后解码校验尺寸/像素变化/编号数；RGB/RGBA/灰度；密码节点无标记；越界贴边；输出 ≤ 既有 caps。
3. 失败：坏 PNG、超大尺寸、编码失败、对象发布失败 → 显式错误、无半成品/临时文件残留。
4. 发布：真实 SQLite `session_captures`/objects 引用、跨 Agent 拒绝、来源删除后失效、取消等待保留实际工作。
5. 调用方：Android observe/capture、image codec、objects、HTTP（若 D0 选路由）、架构/strict/ruff。
6. 回退：摘绘制/准入 → 新增用例红；恢复后绿。

## 6. 明确不做与未验证

- 不做 OCR/文字识别、屏幕分类、explore_app 状态图、录制/流、overlay 真机叠加。
- 不宣称真实设备截图质量、厂商窗口/密码字段、SoM 视觉效果或真机可点性；这些需指定真机窗口。
- 不新增模型工具/HTTP/迁移/表；D0–D3 未决前只做离线核心。
- 真实设备截图质量、厂商窗口/密码字段、SoM 视觉效果未验；`whole_core_parity_complete`、`whole_v2_complete` 保持 false。

## 7. 离线核心实现记录（2026-11-22）

- `domain/som.py`：`SomNode`/`SomMark`/`SomSelection`；节点 1–128、规范 UUID 唯一、bounds 四整数且正宽高、`visible`/`password` 必须显式 bool；只标 visible 且非 password 节点，bounds 裁剪到帧内，完全越界计数跳过，≤50 标记按 observe 顺序 1-based，超限 `truncated=true`；帧尺寸复用 8192/16Mpx 上限；`mark_map` 只给内部/所有者映射。
- `adapters/som.py`：`render_som(png, selection)` 用 Pillow 同尺寸叠加红框 + 黑底黄边编号，只把数字写入像素；零标记返回原 PNG；坏 PNG/尺寸不符/编码异常显式 `android_som_invalid`，输入/输出超 16MiB `android_som_too_large`；不读文件、不发布对象、不调用模型。
- 测试：`agent/tests/v2/core/test_v2_android_som.py` **9 passed / 0.29s**（节点准入/密码跳过/裁剪/50+1 截断/重复与非法输入/渲染确定性/同尺寸/坏图）；架构/候选门/目录/类型范围守卫 **42 passed / 8.57s**；strict **432 文件 0 错 0 警**；ruff/格式通过。
- 未做：模型工具/HTTP/对象发布接线、真实设备帧、视觉质量、OCR/explore_app；D0–D3 未决。证据：[v2-android-som-core.json](../../evidence/v2-android-som-core.json)。
