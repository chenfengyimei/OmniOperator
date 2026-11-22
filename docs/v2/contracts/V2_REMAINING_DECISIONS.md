# V2 K01/K13/K18 剩余差异决策登记（2026-11-22）

状态：**决策登记（纯文档，无源码/测试/迁移改动）**。把已交付批次之外、必须先由所有者（安全/语义）或 A（共享接口/迁移/公共 CI）决定的剩余项逐项登记：现状、缺口、需要决定什么、安全与授权面、验证要求、依赖与建议顺序。**本册所有条目均为未实现**，不宣称对等。

## 已交付（不在本册决策范围）

- **K01(b)** 提示缓存路由键：`372ad56d`（实现，`pc_<sha256(agent|session|epoch)[:24]>` 仅 slot-keyed/OpenAI `extra_body`）+ `4cc292d3`（证据/合同同步）。D3(a)/(c) 与 D8 仍待决，见 D1。
- **K13 cron 四批**：`1d52cca1`（技能绑定+输出依赖持久化 v0049）、`a3436361`（触发链技能激活/上游输出注入/脱敏）、`4acc46c9`（`cronjob_manage` 六动作）、`42cf2e98`（生产端到端，四批完结）。
- **K13 浏览器对话框**：`7827b664`（`browser_dialog` 有界持有队列，非本册）。
- **K18 读 API 广度**：`4802dbca`(search) → `f673f969`(whois) → `f80ab73d`(danmaku) → `b14d06ff`(feed) → `091e20d1`(following) → `4c280835`(favorites) → `a81a0353`(tags) → `21417ec6`(history) → `624413eb`(msgfeed) → `2608f592`(msgfeed like) → `840e5f5f`(relation)。
- **K18 风控暂停**：`8923054c`（风险码类型化 → 每账号暂停 → 所有者恢复，v0046）。
- **K18 读缓存**：本批 `K18.read_cache`（见 [V2_BILI_READ_CACHE](V2_BILI_READ_CACHE.md)），不重复决策。

以下 D1–D7 逐项登记待决。

---

## D1（K01）系统前缀重构 D3(a)/(c) 与 D8 真实缓存效果

- **现状**：`domain/metering.py` 的 `memory_system`/`history_request` 冻结前缀 = `persona` + owner memory + 通知（上下文省略/档位路由/已审技能），memories 与档位/技能通知跨 run 漂移；K01 已交付仅「单 run 前缀冻结」+ (b) 路由键。内容地址 provider（Anthropic/DeepSeek/Gemini）靠前缀字节 parity，跨 run 不命中。
- **缺口**：D3(a) 参考对齐（memory 与 notice 移稳定尾部块，**语义变更**：memory 从 system 高 salience 降为尾部）；D3(c) 混合（persona + 稳定 memory 快照，增量走尾部）；D8 真实 provider 命中率验收。
- **需决定**：所有者选 (a)/(c)/维持现状；是否接受 memory salience 与遗忘/优先级变化；批准修改冻结前缀合同（`memory_system`/`history_request`）；D8 的隔离凭据/环境与计费边界。
- **安全/授权面**：系统前缀承载 owner memory（数据非授权），移尾不改授权语义、不新增出网字段；委派/重置换桶语义（D4/D5）保持。
- **验证要求**：前缀字节稳定回归（跨 run 同字节、变更新桶）、memory/notice 语义对照、既有 metering/context/压缩/摘要调用方零回归、D8 真实命中率单独验收（不入默认离线套件）。
- **依赖**：所有者批准 + 隔离 provider 环境。**建议最后做，与 D2–D7 无耦合。**

## D2（K13）工具调用代码沙箱（execute_code / code_kernel + RPC 桥）

- **现状**：无。`shell_execute` 是一次性已审 argv，非工具调用代码沙箱；无持久 kernel、无密钥 scrub 子环境、无允许清单 RPC。
- **缺口**：本地 Python 沙箱 + 允许清单工具调用桥（参考 ≤50 调用/次、5 分钟超时、stdout spill）、持久 kernel（owner/mode/interpreter/cwd/tool-set/reset）、`code_execution_env`（密钥 scrub、隔离 temp CWD）、`code_execution_rpc`（UDS/TCP + token auth + 调用预算）。
- **需决定**：沙箱边界（子进程 + RPC vs 进程内）、持久 kernel 的归属与生命周期（agent/run/session，重启恢复？）、可调工具允许清单与写动作是否复用既有 `action_review` 审批、跨平台实现（builtin worker 还是本地 Python）、unknown/取消/崩溃结算、资源上限（CPU/内存/磁盘/时长）。
- **安全/授权面**：**新增任意代码执行面**，可绕开既有工具 schema/审批；须独立安全合同（禁止继承运行中凭据/环境、显式允许清单、默认 OFF、逐次审批或预算）。属最高风险项。
- **验证要求**：离线沙箱逃逸负例（读宿主环境/凭据/文件系统）、允许清单越权、审批门 approved/denied/revoked/expired、调用/时长预算、unknown 不重放、取消与孤儿进程回收、与 K05 并行/资源锁交互、strict/架构守卫。
- **依赖**：所有者安全批准；可能需要新 worker 协议 → A 协调。**独立批次，先合同后实现。**

## D3（K13）原始 CDP / 脚本化浏览器执行（browser_cdp / browser_exec）

- **现状**：仅 `browser_navigate/read/click/fill` + URL 审批；无 CDP method/params/target/frame 通道，无预导入 helper 的脚本执行。
- **缺口**：原始 CDP（含 OOPIF 跨域 iframe 帧路由）、脚本化执行（命名隔离会话）、CDP endpoint 暴露策略。
- **需决定**：是否开放原始 CDP（可绕 URL 审批/读任意页面）；专用 agent 浏览器是否暴露 debug URL、如何注入；帧路由与 target 白名单；脚本能力边界（禁止未审批导航/下载/凭据访问）、审计与回放、超时与输出上限。
- **安全/授权面**：原始 CDP 等于浏览器全权，需合同定义「仅已审 URL + 只读方法白名单」或整体默认为所有者显式启用；与 SSRF/IMDS floor 关系。
- **验证要求**：负例（未审批 URL、越权 CDP method、跨 OOPIF 越界）、审计事件、与既有 browser 审批/worker 协议零回归、真实 Chromium 另验。
- **依赖**：所有者安全批准 + 真实 Chromium 环境；可能触 worker 协议 → A 协调。

## D4（K18）写扩面（fav / dynamic / comment-del；danmaku-send 归 D7 直播）

- **现状**：写只支持 `comment`/`private_message`：准备 → 所有者审批 → `delivery` outbox → **已审浏览器表单**（非直接 API POST）+ mention 精确范围 + 幂等指纹 + unknown 不重发。参考另实现 fav（`x/v3/fav/resource/deal`）、dynamic post/rm/edit/image、comment-del；`danmaku-send` 是直播 API（`api.live.bilibili.com/msg/send`）归 D7；like/coin/follow/gift/charge 参考也未实现。
- **缺口**：以上三种动作的已审表单/操作 + 配额 + 幂等 + 失败重排队；**新发现**：现有 comment/dm 路径**没有任何配额/冷却**（V2 仅 `MAX_ATTEMPTS=3` 退避 + QQ 专属 `cooldown_s`），K18 审计「共享限额/冷却」措辞需更正。
- **需决定**：逐动作是否开放（各自独立表单审核 + 所有者批准）；配额机制选 Q1（仅审批+幂等，零迁移）/Q2（通用配额账本，迁移由 A 编号）/Q3（内存限速，不采用）；是否移植参考具体窗口（fav 3 日/BV、comment 30 日/视频、dm 1/日）；unknown/失败重排队策略。
- **安全/授权面**：写扩面即扩大真实账号可执行动作；须保持「准备≠发送、审批≠成功」与 owner-only 批准，禁止模型直接 API POST。
- **验证要求**：表单选择器审核、审批四态、幂等/unknown 不重发、配额与冷却、真实账号送达另验；fav/dynamic 需先补只读验证面（`fav_resources`/`dynamic_list`）。
- **依赖**：所有者逐动作批准 + 配额 Q1/Q2 决策；若 Q2 → A 分配迁移号。设计合同见 [V2_BILI_WRITE_BREADTH](V2_BILI_WRITE_BREADTH.md)。**未实现。**

## D5（K18）主动观看（生物钟 / 双日记 / 沉默衰减 / 候选打分 / 偏好学习）

- **现状**：`SchedulingService`（interval/cron，所有者侧）、`delivery` outbox、K08 回合后学习、K15 动机审计已记录；无自主观看策略。
- **缺口**：night-plan 随机唤醒（skipChance 0.3/minGap 10min/maxWakes 12/maxVideos 5）；双日记（致所有者信 + 自我偏好校准）；沉默衰减（≥3 日线性降 share 上限、第 7 日=0、日记仍写 normal/quiet/faint 带）；反馈检测去重（like_time/reply-hash）；候选打分（log 弹幕密度×3 + 偏好命中×4 + 学习 token≤7.5，排除播放量，junk play<300&dm<3 过滤）；feed 偏好学习。
- **需决定**：策略参数与默认值（默认 OFF、所有者总闸）；日记文件模型与所有者只读面/导出；衰减与配额落库（新表？→ A 分配迁移）；反馈来源与去重键；与 K15 动机引擎的边界（需求→目标→驱动 vs 观看候选）；`SchedulingService` 触发与 K08 评审的交互（K13 已记录 `schedule_blocker` 与在途评审的既有竞态）。
- **安全/授权面**：自主外联/分享必须经所有者审批与既有 outbox 限额；不得 idle-wake（无待审/无唤醒窗即不动作）；不自动扩写全局指令。
- **验证要求**：纯策略离线（注入时钟/配额/衰减曲线/打分序/反馈去重）、无 idle-wake、不自动外联、跨 agent 隔离、真实账号/模型质量另验。
- **依赖**：D4/D6 的写与分享门先定；与 K15 协同需 A/所有者对齐。

## D6（K18）受控分享策略（share ≤3/日、同 BV 7 日去重、score/reason/rank 门、notify 多后端）

- **现状**：准备→审批→outbox→送达核查原语已有；参考特定阈值与 notify 后端未实现；AI-share 守卫参考默认 OFF 且**从未在线验证**（文档 60 vs 代码 30 漂移）。
- **缺口**：share 每日上限、同 BV 7 日去重、score≥13/20 + reason≥8 + rank 1 门；notify 后端（none/desktop/webhook/command/dm/stdout/file，去 shell 注入）；AI-share 守卫（默认 OFF、独立合同、真实误报率）。
- **需决定**：是否采纳参考阈值（或放共享 `delivery` 配置而非硬编码）；通知后端范围与去 shell 注入；AI-share 守卫是否移植（**不照抄漂移阈值**，须独立合同 + 真实验证）；沉默衰减（D5）与 share 上限的耦合口径。
- **安全/授权面**：分享是外联动作，必须保持所有者审批、mention 精确绑定、unknown 不重发；notify command 后端需明确禁 shell 插值。
- **验证要求**：配额/去重/门控离线回归、审批与 outbox 调用方零回归、真实送达与误报率另验。
- **依赖**：所有者决定阈值；D5 提供沉默衰减输入。

## D7（K18）直播（room info / history / WebSocket 弹幕 / send / gift 只读 / 独立开关）

- **现状**：无直播房间观看、无实时弹幕 WebSocket、无 gift 只读事件。
- **缺口**：room info/history、WebSocket 实时弹幕（brotli/deflate 解析、重连、有界缓冲）、send、gift 只读（SEND_GIFT）、独立开关与范围（所有者/@-only）。
- **需决定**：是否接直播 WebSocket（新常驻连接/worker 归属）、send 是否允许（参考有，V2 默认应只读）、gift 只读事件保留期、风险码与既有暂停联动、连接上限/心跳/退避、真实网络验收方案。
- **安全/授权面**：实时连接与消息解析扩大出网面；send 属写动作，须审批；弹幕内容是不可信数据。
- **验证要求**：协议解析离线样本帧、有界/断线/重连/背压、开关隔离、风险码暂停联动、真实网络/账号另验。
- **依赖**：所有者网络/账号验收安排；新 worker/连接协议 → A 协调。

## D8（K13）调度阻塞与后台评审辅助调用

- **现状（已核，2026-11-22 基线 `0793f254`）**：K08 评审以 `purpose="summary"`、被评审 run 名下 `reserve_call` 记账（`background_review.py:147-156`）；`schedule_blocker` 对该 run 名下**所有** reserved/unknown 模型调用计为未解决工作、无 purpose 过滤（`lease_repository.py:120-155`），触发链因此把在途评审当作外部结果未复核：`assert_run_lease` 抛 `previous_outcome_unknown`（`:99-105`），`schedule_materialize` 不物化下一次 occurrence（`schedule_repository.py:477-483`）；同因亦覆盖 K06 `compact_run` 摘要调用（`context.py:151/289`）。现有 e2e 以 `wait_review_settled` 等待评审终态绕过（`test_v2_cronjob_e2e.py:61-78`），`42cf2e98` 已记录修复前真实失败计数。
- **需决定**：是否采用合同 A（blocker 只统计 `purpose="turn"`，summary 不阻塞；turn/actions/outbox 与 `schedule_reviews` 语义不变），或 B（自动确认）、C（调度等待评审）、D（维持现状）。
- **安全/授权面**：守卫本身安全敏感（复核未派发外部结果）；A 只移除内部辅助调用的假阻塞，不放过任何外部动作/投递复核；unknown summary 成本仍在 `model_calls`/usage/审计可见。
- **验证要求**：离线冻结时钟 + 真实隔离 SQLite（summary 不阻塞、turn 仍阻塞、actions/outbox 仍阻塞、崩溃遗留 unknown summary 不阻塞）、cron/评审/维护调用方零回归、回退验证。
- **依赖**：所有者批准；预计无迁移/接口改动（A 协调仅备案）。合同见 [V2_SCHEDULE_REVIEW_BLOCKER](V2_SCHEDULE_REVIEW_BLOCKER.md)。**未实现。**

## D9（K13）浏览器 URL 显式 SSRF/IMDS floor

- **现状（已核，2026-11-22 基线 `0793f254`）**：builtin browser worker 的 `browser_navigate` 只校验 scheme ∈ {http,https} 且无 userinfo（`adapters/workers/main.py:447-453`），**无主机/IP floor**：loopback、RFC1918、link-local、云元数据地址均会被接受；`page.goto` 默认跟随 HTTP 重定向且无每请求拦截，落地地址/子资源不受初始 URL 审批约束。`browser_navigate`/`click`/`fill`/`dialog` 均 `writes=True` 走所有者审批（`composition/capabilities.py:113-155`），但审批只看到模型给出的初始 URL。实测：本地 302 → `http://127.0.0.1:<port>/latest/meta-data/` 被 Playwright 跟随并读取 body（`docs/evidence/v2-delivered-verification.json`）。
- **需决定**：是否补 floor 及范围——（i）仅拒云元数据/link-local（169.254.0.0/16、fd00:ec2::254、metadata.google.internal、100.100.100.200），保留 loopback/RFC1918（V2 UI/本地开发需要）；（ii）默认拒 loopback+RFC1918+link-local，按 agent 配置显式允许本地浏览；（iii）只校验初始 URL（弱，重定向可绕过）；（iv）worker 级 `page.route` 全请求拦截 + 重定向后复检（与 i/ii 组合，最强，需评估性能与误伤）。
- **安全/授权面**：SSRF 可读云元数据/内网服务，绕过 owner 审批看到的 URL；属安全策略变更，需所有者定策略（本地浏览是否允许、allowlist 形式）。无迁移/共享接口改动（builtin worker 内实现）。
- **验证要求**：离线真实 Chromium + 本地 HTTP 夹具（loopback/私网/元数据名/重定向/子资源/iframe）、正常公网导航零回归、`browser_dialog` 等既有套件、审批面说明。
- **依赖**：所有者批准；worker 协议不变（builtin）。**未实现。**

---

## 其他剩余差异（非决策项，登记）

- **K13 组 5（cron）表内其余残余**：cron 四批只交付 `skills`/`context_from`/`continuity` + `cronjob_manage` 六动作 + 生产端到端；审计表里 `monitor`（URL/脚本变化检测门控 run + diff 注入）、`no_agent`+`script`（无模型看门狗）、`enabled_toolsets` 允许清单、`workdir`、`deliver`/`failure_deliver`/`attach_to_session` 细分、`cronjob_prompt_scan`（prompt 威胁扫描）仍为**部分对等/可离线缺口**，未实现、未列本册决策（不需新授权面，但会触 `SchedulingService`/`delivery`/技能接口 → A 协调排期）。
- **K13 组 1（终端/进程）模型侧面**：模型侧只有一次性 `shell_execute`；持久终端会话与 `process_manage`（list/poll/log/wait/kill/write/submit/close/handoff）、`read_terminal`/`close_terminal`/`read_window_below` 仍缺（所有者侧 `TerminalService` 已有）。属新 worker 持久会话协议 + 审批 + 所有者面，需设计与实机验收，未列本册决策。
- **K13 组 2（浏览器）URL floor**：**已核（2026-11-22）**——worker 仅 scheme 校验、无主机/IP floor 且跟随重定向（见 D9，实测 loopback 元数据路径可读）；待所有者定策略，未实现。
- **读侧速率预算**：参考 读 600/h·3000/d、写 12/h·30/d、gap 1500ms、写冷却 120s、dm 60s；V2 共享 delivery 限额/冷却覆盖投递侧，**读侧预算未实现**（需跨进程/重启持久的共享预算账本设计）。
- **nav 10min 缓存**：有意不做（比参考更严，见 [V2_BILI_READ_CACHE](V2_BILI_READ_CACHE.md)）。
- **真实账号/网络/模型质量/72 小时验收**：全部读端点送达、风控触发与恢复、QR 登录、真实写发送、直播连接、模型生成质量；参考亦未在线验证的 AI-share 守卫误报率。

## 顺序建议

D1（前缀，触冻结合同）与 D2/D3（新执行面）风险最高、需所有者批准；D4 写扩面在 D6 分享策略前；D5 主动观看依赖 D4/D6 与 K15 动机对齐；D7 直播独立；D8（cron 阻塞与评审交互，预计无迁移）与 D9（浏览器 SSRF floor，仅 builtin worker）可在所有者选定策略后小批先行，是当前决策登记中实现成本最低的两项。共享接口/迁移/公共 CI 由 A 协调：D1 触 `domain/metering.py`，D5/D6 若新增账本/日记表需迁移编号，D2/D3/D7 若扩 worker 协议需两端约定提交方。`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。
