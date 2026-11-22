# V2 原始 CDP 与脚本化浏览器执行设计合同（K13 缺口 #6：browser_cdp / browser_exec，决策 D3）

状态：**设计合同（design_contract_recorded，纯文档，无源码/测试/迁移/路由/worker 改动，2026-11-23）**。**未实现**。承接 [K13 审计](V2_TOOL_CHANNEL_AUDIT.md) 组 2 与 [剩余差异决策登记](V2_REMAINING_DECISIONS.md) D3；本册把参考 hermes-agent 的 `browser_exec`（任意 Python over CDP）与 `browser_cdp`（原始 CDP 方法/帧）映射到 V2 的浏览器 worker、URL 审批与 SSRF floor（D9），并明确拒绝「任意宿主代码 + 全权 CDP」的组合。写扩面（D4）、主动观看（D5）等不并入。

## 参考事实（固定 `9976f485`，仅作设计数据）

- **browser_exec**（`tools/browser_use_cli.py`）：模型提交 Python 代码，经 browser-harness **在宿主上**通过 CDP 驱动浏览器（预导入 helper），只有输出回模型；`browser_exec` 是唯一核心浏览器，且因「任意宿主 Python」在定义期被 `terminal` surface 门控（`_rewrite_browser_exec`）；代码内 URL 字面量预扫描（`_code_url_verdict`），URL 里带秘密直接拒绝；CDP endpoint 解析优先级：操作者 `BU_CDP_*` > `/browser connect`/`BROWSER_CDP_URL`/配置 > hybrid local sidecar > 云 provider > 真实 profile（需同意）> Lightpanda > 打包 Chromium；命名隔离会话（session/cache key）。
- **browser_cdp**（`tools/browser_cdp_tool.py`）：原始 CDP `method/params/target_id/frame_id`（OOPIF 经 `Target.attachToTarget(flatten=True)` 多路复用）；**必须已有 supervisor**（browser_exec 挂上）且 `check_fn` 要求 browser_exec 可用 + CDP override；**私有网络守卫**：云元数据/IMDS 无条件拒绝、私有地址仅在后端本地/hybrid/`browser.allow_private_urls` 时允许；页面读取类方法在页面 URL 私有或**无法验证**时拒绝；`_METHOD_PARAM_GUARDS` 对带 URL 参数的方法做预检；文档明言「原始 CDP 与 browser_exec 共享同一私有网络边界，**不得成为其绕过**」。
- **URL floor**（`tools/browser_url_policy.py`）：有序 floor — 云元数据/IMDS 无条件拒；私有地址除非本地后端/hybrid/显式允许；然后网站 allow/deny；DNS 解析参与判定；JS 表达式中的 `http(s)://` 字面量预扫（fetch/XHR 不改变 `location.href`）。
- **共同前提**：浏览器能力与 `terminal`/代码执行强耦合；参考也承认这类执行面需要门控与私有网络 floor。

## V2 现状

- `browser_navigate/read/click/fill/browser_dialog`：builtin worker + Playwright；navigate 只校验 scheme/无 userinfo（**无主机/IP floor**，见 D9 与证据 `v2-delivered-verification.json`）；click/fill/dialog 带 `expected_url` CAS；四者均 `writes=True` 逐动作所有者审批；**无 CDP endpoint、无任意浏览器代码、无脚本化多步计划**。
- 审批语义：一次审批=一个动作；多步脚本无法表达为单次审批。
- 依赖：Playwright 已有；`websockets` 已有（若将来接 CDP）。

## 设计选项（需所有者选定）

### C1 不移植（推荐，与 S1 一致）

不新增原始 CDP 与任意浏览器 Python；审计登记「参考特有、V2 不移植」，不宣称对等。浏览器能力继续按**逐动作审批**推进；先做 D9 的 URL/IMDS floor。优点：无「绕过 URL 审批/读任意页面」的新面；缺点：复杂多步交互效率低。

### C2 声明式多步浏览器计划（推荐先做，若需要多步）

新增模型能力 `browser_plan`（**单次审批执行有界动作清单**，非代码执行）：

- 输入：`steps[]`（≤20），每步 `{action: navigate|click|fill|wait|read|dialog, url/selector/text/ms, expected_url, dialog:{action,prompt_text}}`；
- 校验：navigate URL 必须 http(s) 且**过 D9 floor**（元数据/link-local 默认拒、私网按策略）；click/fill/dialog 必须带 `expected_url` 并逐步 CAS；selector/text 沿用既有上限；`wait ms ≤ 5000`；
- 执行：builtin worker 顺序执行（复用现有 navigate/click/fill/dialog/read 实现），每步前校验 URL/页面未漂移，**任何一步失败即停**（已执行步骤如实回执，unknown 不重放）；总时长 ≤60s、总输出 ≤80 KB；
- 审批：整个 plan `writes=True` 一次审批（主模型/所有者在批准前看到全部 URL 与选择器）；计划内容与审批绑定 fingerprint；执行中不接受新 URL；
- 验证：每步返回 `verification=page.url`；最终 read 文本有界；partial 失败列出已完成步骤。
- **无 CDP、无代码、无任意 JS 求值**；不引入新 host/端口；无新表/迁移；worker 协议不加帧（计划在现有 action 参数内传递，若超帧需评估 → 由 A 决定）。

### C3 只读 CDP 白名单（需 D9 先落地 + A 协调协议）

- 暴露 CDP endpoint（worker 或受管 Chromium 的 loopback debug URL）给 V2 适配器（**新端口/协议面**）；
- 仅允许**只读方法**（如 `Target.getTargets`、`DOM.getDocument`、`Page.getLayoutMetrics`、`Page.captureScreenshot`）；**禁止** `Runtime.evaluate`/`Runtime.callFunctionOn`（等于代码执行）、`Page.navigate`/`Input.*`（变更）、`Network.getCookies`/凭据类；
- 每个方法带参数守卫（URL/expression 参数预检）；执行前验证目标页面 URL 公共/非私有（含 iframe target），否则拒；**D9 floor 为前置**；日志/输出脱敏；单连接 ≤1。
- 风险/成本：CDP 是浏览器全权面（方法白名单需长期维护）；新 loopback 端口扩大本地面；跨平台 worker 改动需 A 协调；**不推荐首批**。

### C4 完整 browser_exec（拒绝）

任意宿主 Python + 全权 CDP：可绕 URL 审批、读任意页面/凭据、执行任意代码；与 V2「逐动作审批 + URL floor」纪律直接冲突，**明确拒绝**，与 D2 的 S1/S2 边界一致。

## 决策问题

1. 选 C1（不移植）、C2（声明式多步计划）还是 C3（只读 CDP 白名单）？C4 拒绝。
2. 若 C2：plan 审批粒度（整单一次）、步数/时长/输出上限、失败语义（停止+已完成回执）、是否允许 `dialog`/`wait`、是否允许后续步骤引用上一步结果（建议不，保持声明式）。
3. 若 C3：只读方法清单、目标页面验证（OOPIF/target）、CDP endpoint 暴露方式与 worker 协议、跨平台实现（Windows/Linux）归属、D9 floor 是否已落地。
4. 共同前提：D9（浏览器 SSRF/IMDS floor）必须先实现；C2/C3 均不得读取/回传 cookie 或凭据。

## 实现要点（获批准后独立批）

- C2：领域 `browser_plan` 校验器（纯、bounded）；worker `browser_plan` op（复用现有 action 实现 + 步进/超时/输出上限）；capability 注册（writes=True、agent 档、resource=browser、独立审批）；无新表/迁移；回退摘注册即全红。
- C3：adapter/worker CDP 客户端（`websockets`）、方法白名单与参数守卫、目标页面私有检查、D9 floor 复用；**共享 worker 协议 → A 协调**；只读、无持久化。
- 测试：C2 计划校验正负例、真实 Chromium 本地夹具多步（navigate→click→fill→read）、expected_url 漂移中止、审批四态、超时/输出截断、unapproved URL/私网/metadata 拒绝；C3 方法白名单、私有页面拒绝、无法验证即拒、方法参数守卫、无凭据读取。
- 真实公网网站/账号另验；未实现前不宣称组 2 对等。

## 明确不做

- C4 完整 `browser_exec`/任意浏览器 Python；凭据 vault；云浏览器/真实 profile/Lightpanda；沙箱容器/远程后端；写扩面（D4）；主动观看（D5）；真实网站质量；全量；72 小时。

## 关联

- K13 审计：[V2_TOOL_CHANNEL_AUDIT](V2_TOOL_CHANNEL_AUDIT.md) 组 2。
- 决策登记：[V2_REMAINING_DECISIONS](V2_REMAINING_DECISIONS.md) D3（与 D9 关联）。
- 相关合同：[V2_CODE_SANDBOX](V2_CODE_SANDBOX.md)（D2）、[V2_BROWSER_DIALOG](V2_BROWSER_DIALOG.md)、D9 见决策登记。
- 证据：[v2-browser-cdp-design.json](../../evidence/v2-browser-cdp-design.json)。
