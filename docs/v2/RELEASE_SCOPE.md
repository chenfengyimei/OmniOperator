# V2 本版交付边界（2026-11-24 基线 `76f57804`）

> 用途：把「本版承诺什么、不承诺什么」写成一份可直接用于验收排期的清单，避免把已记录为延期的离线核心当成应当开箱可用的功能。
> 真值优先级：`docs/tasks/*.json`、各合同与验收证据 > 本文件。本文件只汇总边界，不新增实现承诺。
> 当前结论：**整个 V2 仍在开发，不能宣布开发收尾，也不能把本文件当作整体验收通过。** 本文件的作用是让外部验收按承诺范围执行，并把「如果要纳入本版」所需的工作列清。

## 1. 本版承诺（已接生产或有生产入口）

| 范围 | 承诺内容 | 证据/入口 |
|---|---|---|
| Agent 内核 | K02–K12：回包协议、任务清单、单层委派、批次并行、run 内压缩、迭代守卫、后台评审、历史检索、澄清、停止验核、故障恢复 | [内核对标](contracts/V2_AGENT_CORE_PARITY.md) |
| 视觉子代理（截图理解） | `visual_subagent` 已接生产：Android 截图/已注册图像对象 → 一次有界视觉调用 → 纯文本摘要；**单次最多 4 张图像对象或一张媒体帧 sheet**，来源互斥由 schema 强制；超过 4 张在参数校验即拒绝 | [视觉合同](contracts/V2_VISUAL_SUBAGENT.md)、[证据](../evidence/v2-visual-tool-contract-2026-11-24.json) |
| Android 设备操作 | 观察/动作/输入/恢复 IME 等既有模型工具，新鲜观察绑定、逐次审批、unknown 不重试 | [Android 审计](contracts/V2_ANDROID_AUDIT.md) |
| 媒体 | 上传/B 站来源、字幕、离线 ASR、等距/场景抽帧、格号索引、`media_zoom`、`media_locate`；依赖缺省时显式 `media_component_missing`，不隐式安装 | [媒体合同](contracts/V2_MEDIA.md) |
| 记忆/人格 | 记忆衰减/评分/首遇门、K08 待审学习、反思信笺与人格成长派生视图（所有者审核） | [人格成长](contracts/V2_PERSONA_GROWTH.md) |
| B 站读面 | 搜索/空间/弹幕/feed/关注/收藏/标签/历史/收件箱/风控暂停/读缓存 | [B 站审计](contracts/V2_BILI_WHALE_AUDIT.md) |
| Windows | 桌面读取/语义写/前台输入、终端、安装/备份恢复/卸载、真实 ASR/抽帧业务链 | [Windows 验收](platforms/windows/WINDOWS_V2_ACCEPTANCE_2026-11-18.md) |
| macOS | arm64 helper、用户域服务 install→ready→uninstall、桌面读/语义/前台输入（自用机已验） | [macOS 交接](platforms/macos/V2_MACOS_HANDOFF.md) |
| Linux | X11 会话的窗口身份/AT-SPI、受限前台按键与受限文本追加（按键仅限明确提供原生窗口 XID 的节点，如当前 Avalonia 属性；鼠标、通用 GTK/Qt 关联与 Wayland 输入仍延期）；Wayland 只读观察与独立的系统授权记录 | [Linux 交接](platforms/linux/V2_LINUX_HANDOFF.md)、[前台控制](platforms/linux/V2_LINUX_FOREGROUND.md) |
| 打包 | 默认 wheel/依赖包由 CI `uv export --no-dev` 生成；默认不含 `av`/`faster-whisper`，媒体/ASR 需 `omnioperator[asr]` extra | [CI 工作流](../../.github/workflows/v2.yml) |

## 2. 本版明确不承诺（延期，已写清理由与再纳入所需工作）

### 2.1 Android 自动探索（`explore_app`）与控件编号标注（SoM）

- **现状**：`ExploreGraph`（`domain/android_explore.py`）与 `render_som`（`adapters/som.py`）是可离线核心，只有单元测试调用；正式源码中没有注册工具、worker 或应用入口，也没有对象发布/审批接线。
- **本版裁定**：不承诺“自动探索 App”，也不承诺“按截图编号选控件”。本版 Android 视觉只到「截图 → `visual_subagent` 文本理解」。
- **如要纳入本版**：需新增模型工具与 `ToolSpec`、Android 截图 → SoM 编号图/探索图的产物注册与删除屏障、逐次审批、`unknown` 语义、真机帧与视觉质量验收；按 [SoM 合同](contracts/V2_ANDROID_SOM.md) 与 [explore 合同](contracts/V2_ANDROID_EXPLORE.md) 的 D0–D3 决策执行。

### 2.2 Linux Wayland 前台键鼠

- **现状**：RemoteDesktop 系统授权可以记录，但 `application/desktop_control.py` 与 worker 的所有回执固定 `input_authorized=false`；实际桌面 worker 只在 `inspector.session.kind == "x11"` 时建立输入守卫（`adapters/workers/linux_desktop.py`）。EIS/`SeatInput`/Wayland 窗口代码只在离线专项中使用，生产 worker 未调用 `prepare_eis`/`prepare_input_monitor`。
- **本版裁定**：不承诺 Wayland 下可点击、按键；Wayland 仅只读观察与独立授权记录，X11 的受限前台输入不受影响。
- **如要纳入本版**：需把独立授权 → EI 设备/键图 → 逐次业务许可 → 派发 → 应用后验接进 worker，并补目标关联、输入暂停/撤回、真实 Wayland 合成器验收；见 [Wayland 控制文档](platforms/linux/V2_WAYLAND_CONTROL.md)。

### 2.3 视频分镜/ASR 是否开箱可用（取决于发行包）

- **现状**：默认 `uv export --no-dev` 的锁定依赖不含 `av`；`av` 经 `faster-whisper` 由 `[asr]` extra 带入。默认安装调用抽帧/转写会得到 `media_component_missing`，不是崩溃也不是静默降级；错误文案现在明确提示安装 `omnioperator[asr]` extra（2026-11-24）。
- **本版裁定**：默认安装**不承诺**视频抽帧/ASR 开箱可用；需要这些能力时安装 `omnioperator[asr]`。带 `[asr]` 的验收包必须单独构建、单独用装好的程序实际抽一帧并记录证据，不能沿用默认包的验收结论。2026-11-24 已在隔离 venv 用安装后的 wheel 实测：默认包返回 `media_component_missing` 且无部分产物；`[asr]` 包通过真实 worker 子进程抽出 3 帧 640×520 PNG（`av 19.0.2`，worker 干净关闭）。其他平台、正式安装链与 ASR 转写仍未覆盖，见 [安装边界证据](../evidence/v2-media-asr-install-2026-11-24.json)。
- **待所有者确认**：若产品要求「默认安装即支持视频分镜」，则需把 `av`（及 ASR 所需依赖）纳入默认发行包或发布专用 extra 包，并重新跑安装链/抽帧验收；这会改变包体与 CI 依赖导出参数。

### 2.4 macOS 公开分发（签名之外的公证）

- **现状**：`scripts/build_macos_helper.py` 支持指定签名身份，默认 ad hoc；仓库构建/发布流程中没有 `notarytool` 提交、公证等待或 `stapler` 装订步骤。
- **本版裁定**：自用 Mac mini 可以继续按现有方式验收；**不承诺**面向普通用户的公开分发产物。
- **如要公开分发**：需确定交付形式（dmg/pkg/zip）、Developer ID 证书与 Apple 公证账号、CI/发布脚本中的公证与装订、Gatekeeper 干净机验证；未完成前不得把 ad hoc 产物称为可分发包。

### 2.5 已在决策册登记的其他延期项

K15 分层人格/主动动机、K18 写广度/受控分享/直播、K16 帧内 OCR、K13 代码沙箱/CDP/cron 残余、读侧速率预算等，均按 [剩余差异决策登记](contracts/V2_REMAINING_DECISIONS.md) 保持延期；本文件不重复其实现细节。

## 3. 外部验收口径

1. 按 §1 的承诺范围安排干净机、真实账号、真机与 72h 长跑；**不要把 §2 的延期项作为本版阻塞项**，除非所有者明确把该项纳入本版。
2. 视觉模型验收前先核对：当前图像协议只接受 `domain/images.py` 列出的 OpenAI low-detail 视觉模型名（如 `gpt-4o`/`gpt-4o-mini` 及指定版本）；其它本地视觉模型即使能看图，也会在调用前被拒绝。
3. 视频分镜/ASR 验收必须使用带 `[asr]` extra 的安装包，并在装好的程序里实际抽帧/转写；默认包只验 `media_component_missing` 的显式失败文案与不崩溃。
4. macOS 验收区分「自用签名构建」与「公开分发产物」；后者在公证/装订完成前不进入验收。
5. 全量测试仍留到开发冻结窗口；开发期只跑受影响模块与调用方，不能把局部专项通过写成整体验收通过。

### 3.1 72 小时长跑与最终候选的关系

- 当前长跑 **2026-11-24T02:26 启动**，运行标识 `05b44447`、wheel SHA `7afc5b7d…`，覆盖的是 2026-11-23 的候选源码。
- 2026-11-24 之后 `v2` 又合入三项**程序改动**：`fdc513ad`（视觉工具 4 图上限/来源互斥）、`4ec16920`（媒体缺组件文案）、`fa359a09`（媒体工作台缺组件提示）。因此这轮长跑 **不能直接作为当前 HEAD 的最终候选证据**；它仍可作为内核稳定性观察。
- 最终发布必须二选一：**(a)** 在当前 HEAD（含视觉修复）重建候选并重新跑满 72 小时——推荐，如果视觉修复必须随版；**(b)** 所有者明确接受这两项差异，以 `05b44447` 为发布源——则该版本不含视觉修复。未选定前，不得把 `05b44447` 的 72h 结果写成当前源码已通过。
- 当前 HEAD 的受影响组回归（视觉生产 + Android 探索 + Linux RemoteDesktop）为 **63 passed / 54.18s**（2026-11-24），只证明局部模块，不替代 72 小时。

## 4. 变更方式

任何一项从 §2 移入 §1，必须同时满足：所有者明确纳入本版、对应合同决策门关闭、生产入口/审批/删除屏障/失败语义齐备、受影响专项与真实环境验收有原始证据。只改本文件不算实现。
