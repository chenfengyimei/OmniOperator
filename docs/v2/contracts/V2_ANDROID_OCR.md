# V2 Android 屏幕 OCR / 查找设计合同（K14 可离线缺口）

**状态：`design_contract_recorded`，未实现，无源码/测试/迁移/路由改动。** 本文件把 K14 审计组 2 的 `ocr_screen`/`ocr_region`/`find_on_screen` 缺口收敛为实现前边界；D0–D5 未决前不接模型、不新增工具/HTTP、不下载模型。

## 1. 范围

- 输入：**已由所有者授权路径取得**的 Android 截图或其中的有界区域，以及同次观察的窗口/应用身份。
- 处理：本地 OCR（可选 RapidOCR ONNX 或等价本地引擎）→ 有界文本行/框/置信度；`find_on_screen` 只做文本匹配与定位，不执行点击。
- 输出：只读文本/框/命中列表；可复用既有对象/图像引用，不自动附加模型、不自动写 memory/persona、不投递。
- 不做：云端 OCR、任意 URL/路径读取、自动点击/输入、OCR 结果当指令、密码字段识别、整库/整盘扫描、跨 Agent 共享、真实设备质量验收。

## 2. 参考语义（固定提交）

参考 `xuexi/android-agent` 固定提交 `7c09f6cd` 的 `ocr_screen`/`ocr_region`/`find_on_screen`：RapidOCR 本地识别，XML→OCR 回退查找；只返回文本/位置，不代替动作审批。V2 只继承「本地、只读、有界文本/框」语义，不复制其模型、依赖版本、提示或网络行为。

## 3. 待所有者/ A 决策

| ID | 决策 | 选项 | 未决时 |
|---|---|---|---|
| D0 | 是否引入 OCR | 不引入（保持无 OCR）／可选本地 RapidOCR ONNX／其他本地引擎 | 不实现，无依赖/模型下载 |
| D1 | 模型 provisioning | 独立可选安装包／运行时按需下载（需校验哈希）／系统自带 | 不下载、不捆绑 |
| D2 | 可见面 | 仅离线 adapter／所有者 HTTP 路由／模型工具（需 A 协调 capability schema） | 仅离线 adapter |
| D3 | 区域与输出上限 | 全屏/指定 region、最大像素、文本行/字符/框上限 | 全屏仅授权截图、保守上限 |
| D4 | 留存 | 仅本次调用／内存短缓存（不落盘）／所有者可读结果 | 仅本次调用，不写长期 |
| D5 | 失败语义 | 模型缺失/解码失败 → 显式 `android_ocr_unavailable`/`android_ocr_invalid` | 显式错误，不伪造空文本 |

**D0 未决不新增依赖/模型/路由；D1 未决不下载；D2 未决不新增模型工具/HTTP。** 共享 capability/HTTP 与安装包由 A 协调。

## 4. 合同边界

- 输入必须是既有 `CapturedFrame`/`session_captures` 对象或所有者显式上传的授权图像；不接受任意路径/URL、base64 之外的原始文件。
- 图像仍受 PNG、单边 ≤8192、总像素 ≤16,000,000、≤16MiB 上限；region 必须在图内且宽高为正，超出拒绝。
- OCR 文本是**不可信数据**：不执行、不解析命令/URL/模板，不据此自动动作；`find_on_screen` 只返回命中框与文本片段。
- 密码/敏感输入：沿用 Android 观察对 `password=true` 节点的排除口径；OCR 不承诺识别/还原密码，结果不得进入日志或长期记忆。
- 输出有界：文本行数、单行/总字符、框数量、置信度范围固定；超限截断并显式 `truncated`，不静默丢弃。
- 失败：模型缺失/加载失败/解码失败/超时显式错误；不重试真实模型，不 fallback 到云。
- 审计：只记录有界元数据（区域、行数、耗时、错误码），不记录识别正文。
- 不自动附加模型，不自动写 memory/persona/反思/投递；不新增表/迁移。

## 5. 测试计划（实现时）

1. 领域：region 校验、像素/文本/框上限、置信度 clamp、空结果/超限截断、非法 Unicode/控制字符拒绝。
2. 适配器：真实小图 OCR（注入本地引擎桩）；模型缺失/加载失败/超时显式错误；不读任意路径；不写日志正文。
3. 应用：授权截图/region、跨 Agent 拒绝、来源删除后失效、取消等待实际工作、不自动动作。
4. 调用方：Android capture/observe、image codec/objects、HTTP（若 D2）、架构/strict/ruff；模型 provisioning 另验。
5. 回退：摘 OCR/校验 → 新增用例红；恢复后绿。

## 6. 明确不做与未验证

- 不做云 OCR、屏幕分类、explore_app、自动点击、密码还原、真实设备质量。
- 不宣称 OCR 对等；`whole_core_parity_complete`、`whole_v2_complete` 保持 false。
- 本批无源码/测试/迁移/路由/依赖改动；真实模型/设备/72h 未验。
