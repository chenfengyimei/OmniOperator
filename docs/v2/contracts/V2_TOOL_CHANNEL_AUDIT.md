# V2 参考工具组/渠道/cron 变体全集审计（K13）

## 结论与范围

K13 是**逐项审计**（非新增工具批次）：把参考 `xuexi/hermes-agent`（固定提交 `9976f4857b72439d79016354d7b6e1a3ee952e2f`，`tools/` 286 个模块，经 `tools/registry.py` 注册、`tools/toolsets.py` 门控）的模型侧工具面，与 V2 现有能力逐项对照，分三类记录：**已对等**、**需实机/账号/网络验收**、**可离线补齐缺口**。已有 files/shell/browser/MCP **不重复实现**；本审计**不宣称全集对等**，可离线缺口列为后续实现批次。

V2 模型侧工具面（生产 `capabilities is None`）：`system_info`、`files_list/read/write`、`shell_execute`、`browser_navigate/read/click/fill`、`document_search/read`、`memory_search/remember/forget`、`session_search`（K09）、`skills_list`/`skill_activate/view/history/create`、`bilibili_read/prepare_publication`、`media_list/transcript`、`android_request_access/observe/act/input/restore_ime`、`desktop_semantic`（所有者），控制 `request_mode`/`todo_list`（K03）/`delegate_task`（K04）/`clarify`（K10），以及 extensions/MCP 动态工具。日程/投递/终端为**所有者侧 API**（SchedulingService/DeliveryService/TerminalService），非模型侧工具。

## 组 1 — 文件 / shell / 终端

| 参考工具 | V2 对应 | 分类 |
| --- | --- | --- |
| `read_file` / `write_file`（file_tools） | `files_read` / `files_write`（指纹覆盖守卫） | **已对等**（V2 额外要求 expected_sha256 防盲写） |
| 文件列举 | `files_list`（≤200 条） | **已对等** |
| `terminal`（持久 shell 会话，cwd/env 持久） | `shell_execute`（一次性已审 argv）；持久终端为所有者侧 TerminalService | **部分对等**：模型侧一次性命令对等；持久会话模型工具缺 |
| `process_manage`（后台进程 list/poll/log/wait/kill/write/submit/close/handoff） | 所有者侧 terminals/terminal_reviews | **需实机验收**：后台进程模型控制工具缺 |
| `patch`（模糊匹配编辑） | 无（files_write 整文件覆盖） | **可离线缺口**：模糊补丁编辑工具 |
| `search_files`（内容/文件名搜索，grep/glob 变体） | 无（document_search 仅源文档，非工作区） | **可离线缺口**：工作区内容/文件名搜索工具 |
| 终端后端 local/docker/ssh/modal/daytona/vercel_sandbox | V2 本地 worker | **需账号/网络**：远程沙箱后端 |
| `read_terminal`/`close_terminal`/`read_window_below`（desktop_ui toolset） | 无（GUI 会话专用） | **需实机验收**：Electron 桌面 pane |

## 组 2 — 高级浏览器

| 参考工具 | V2 对应 | 分类 |
| --- | --- | --- |
| 基础导航/读/点击/填表 | `browser_navigate/read/click/fill`（专用 agent 浏览器，URL 审批） | **已对等**（基础交互） |
| `browser_exec`（对预导入 harness helper 执行 Python over CDP，命名隔离会话） | 无 | **可离线缺口**（需本地 Chromium/CDP）：脚本化浏览器执行 |
| `browser_cdp`（原始 CDP method/params/target_id/frame_id，OOPIF 跨域 iframe） | 无 | **可离线缺口**（需 CDP URL override）：原始 CDP 与帧路由 |
| `browser_dialog`（accept/dismiss 原生 JS alert/confirm/prompt/beforeunload） | 无 | **可离线缺口**：Playwright 对话框处理 |
| `browser_vault`（list/fill/save_login/enter_code/unlock，1Password/Bitwarden/TOTP，origin 绑定，密钥不入对话） | 无（V2 凭据走 FileVault/NativeCredentials，非浏览器填表） | **需账号/网络**：凭据管理器集成 |
| 云会话 Browserbase/Browser Use cloud/Firecrawl | 无 | **需账号/网络** |
| sandbox（Bot Desktop Chromium in Docker/SSH）、real_profile（真实 Chromium profile + OS keychain）、Lightpanda 引擎 | 无 | **需实机/账号**：沙箱/真实 profile/替代引擎 |
| `browser_url_policy`（SSRF floors，IMDS 拒、私网拒除非本地后端） | V2 browser worker URL 审批 | **部分对等**：V2 有 URL 审批；显式 SSRF/IMDS floor 待核 |

## 组 3 — 代码沙箱

| 参考工具 | V2 对应 | 分类 |
| --- | --- | --- |
| `execute_code`（Python 沙箱，经 `hermes_tools` RPC 允许清单调用工具，≤50 调用/次、5 分钟超时、stdout spill） | 无（shell_execute 跑 argv，非工具调用代码沙箱） | **可离线缺口**（本地 Python）：工具调用代码沙箱 + RPC 桥 |
| `code_kernel`（持久会话 kernel，按 owner/mode/interpreter/cwd/tool-set，reset） | 无 | **可离线缺口**：持久 kernel |
| `code_kernel_remote`（docker/ssh/modal 远程持久） | 无 | **需账号/网络** |
| `code_execution_env`（密钥 scrub 子环境、strict 隔离 temp CWD）/`code_execution_rpc`（UDS/TCP + 远程文件轮询，token auth，调用预算） | 无 | **可离线缺口**（本地）：scrub 环境与 RPC 传输 |

## 组 4 — 渠道 / 供应商 / 连接器

| 参考工具/渠道 | V2 对应 | 分类 |
| --- | --- | --- |
| QQ（qqbot 平台插件） | V2 QQ（OneBotTransport）+ 投递 | **已对等**（QQ 链路，需实机账号验收） |
| B 站读写/登录/发布 | `bilibili_read/prepare_publication` + 写审批/送达核查 | **已对等**（需实机账号验收） |
| `manage_connections`（hosted connector 账号 status/connect/reconnect/rename，本地 MCP install/enable/authorize） | V2 extensions/MCP（MCPService）+ 凭据 | **部分对等**：MCP 对等；hosted connector 账号管理缺 |
| `manage_catalog`（catalog 插件 + hub 技能安装，用户审批） | V2 skill_create/审核 + extension 包 | **部分对等**：技能/扩展审核对等；catalog hub 缺 |
| `discord`/`discord_admin`（动态 schema Discord REST） | 无 | **需账号/网络** |
| `feishu_doc_read`/`feishu_drive_*`（飞书文档/drive 评论） | 无 | **需账号/网络** |
| `yb_*`（腾讯元宝群信息/成员/DM/表情） | 无 | **需账号/网络** |
| `x_search`（xAI/X 搜索） | 无 | **需账号/网络** |
| 27 个 gateway 平台插件（a2a/bluebubbles/buzz/dingtalk/email/google_chat/irc/line/matrix/mattermost/ntfy/photon/raft/signal/simplex/slack/sms/teams/telegram/wecom/weixin/whatsapp/whatsapp_cloud/yuanbao 等） | V2 DeliveryService outbox 框架 + QQ/bilibili 端口 | **需账号/网络**：V2 有投递框架与多端口注入，但平台全集未对等 |
| 参考无模型侧 `send_message`（出站走 gateway） | V2 出站走 DeliveryService（所有者授权） | **设计一致**（出站非模型自由工具） |

## 组 5 — cron / 定时任务变体

| 参考能力（`cronjob_manage`） | V2 对应 | 分类 |
| --- | --- | --- |
| create/list/update/pause/resume/remove/run；interval/one-shot/自然语言/cron/ISO；repeat | V2 SchedulingService（所有者 API，interval/cron） | **部分对等**：调度对等（所有者侧）；**模型侧 cronjob_manage 工具缺** |
| `skills`（cron prompt 前按序加载技能绑定） | 无（V2 技能按 run 捕获激活） | **可离线缺口**：cron 技能绑定 |
| `context_from`（注入其他 job 最新输出，链式）/`continuity`（自身上次输出，去重/增量） | 无 | **可离线缺口**：任务输出依赖 |
| `monitor`（URL/脚本变化检测门控 LLM run，diff 注入）/`no_agent`+`script`（无 LLM 看门狗） | 无 | **可离线缺口**：变化检测/无模型看门狗 |
| `enabled_toolsets` 允许清单/`workdir`/model `pinned` | V2 run 档位/工具白名单/固定模型 | **部分对等**：V2 有档位与固定模型；cron 级 toolsets/workdir 缺 |
| `deliver`/`failure_deliver`（local/all/bot-chat/platform:chat:thread）/`attach_to_session` | V2 DeliveryService 投递路由 | **部分对等**：投递框架对等；cron 级失败投递/会话附着待核 |
| `cronjob_prompt_scan`（用户 prompt 严格威胁扫描，技能体仅 prose-proof） | V2 技能审核/来源删除屏障 | **部分对等**：V2 有审核；cron prompt 威胁扫描待核 |

## 组 6 — computer_use / 桌面

| 参考工具 | V2 对应 | 分类 |
| --- | --- | --- |
| `computer_use`（合并动作判别：capture som/vision/ax、click/double/right/middle、drag、scroll、type、key、set_value、wait、list_apps/windows、focus_app；元素索引优先；非 capture 需审批） | V2 `desktop_semantic`（所有者）+ android_act/input/observe | **部分对等**：V2 有桌面语义/Android 动作；合并 som/ax 捕获与元素索引 targeting 待核 |
| cua_backend（MCP-over-stdio 到 cua-driver，macOS SkyLight SPI）/doctor/permissions（DISPLAY/AT-SPI/TCC） | V2 DesktopBackend/AndroidBackend | **需实机验收**：真实桌面设备 + 驱动安装 |

## 可离线补齐缺口清单（后续实现批次，非 K13 实现）

按价值与风险排序（均不重复实现已有 files/shell/browser/MCP，不擅换账号/模型）：

1. **工作区搜索工具**（参考 `search_files`）：内容/文件名有界搜索，补 files_list/read/write 之外的检索。需 worker 协议新增 op（跨平台 Python/Swift/C），独立批次。
2. **模糊补丁编辑工具**（参考 `patch`）：基于指纹的模糊匹配编辑，补 files_write 整文件覆盖。需 worker 协议 op，独立批次。
3. **浏览器对话框工具**（参考 `browser_dialog`）：Playwright 原生 JS 对话框 accept/dismiss，补 browser 基础交互。需 browser worker op，独立批次。
4. **cron 技能绑定 + 任务输出依赖**（参考 `skills`/`context_from`/`continuity`）：SchedulingService 层（application/domain，无 worker 改动），模型侧 cron 工具 + 技能绑定 + 输出链。独立批次。
5. **工具调用代码沙箱**（参考 `execute_code`/`code_kernel`）：本地 Python 沙箱 + 允许清单 RPC 桥 + 持久 kernel。复杂，独立批次。
6. **原始 CDP / 脚本化浏览器执行**（参考 `browser_cdp`/`browser_exec`）：需 CDP 接入与帧路由，独立批次。

## 实现进度（2026-11-22 更新；审计原文保留为当时快照）

缺口清单 1–6 状态：

1. **工作区搜索：已交付**——`files_search` 内容/文件名发现 + output_mode/context/offset/order/file_glob（含否定）/use_regex 有界安全门，见 `V2_FILES_SEARCH*.md` 系列合同与证据。
2. **模糊补丁：已交付**——`files_patch` 指纹 CAS 模糊编辑 + escape/unicode 归一化 + block_anchor/context_aware 近似兜底（近似命中永不许 `replace_all`），见 `V2_FILES_PATCH*.md` 系列与证据。
3. **浏览器对话框：已交付**——`7827b664`（有界持有队列，accept/dismiss + `prompt_text` + `expected_url` CAS）。
4. **cron 技能绑定 + 输出依赖：已交付**——`1d52cca1`（v0049 两表一列）、`a3436361`（触发链技能激活/上游输出注入/Redactor 脱敏）、`4acc46c9`（`cronjob_manage` 六动作）、`42cf2e98`（生产端到端四批完结）。
5. **工具调用代码沙箱：未实现（决策待定）**——见决策登记 D2 与设计合同 [V2_CODE_SANDBOX](V2_CODE_SANDBOX.md)（S1 不移植推荐先明确边界 / S2 受限计算沙箱 / S3 完整 PTC 需 A 协议 / S4 远程容器另立；非安全边界声明；写/terminal 不桥接）。
6. **原始 CDP / 脚本化浏览器执行：未实现（决策待定）**——见决策登记 D3 与设计合同 [V2_BROWSER_CDP](V2_BROWSER_CDP.md)（C1 不移植推荐 / C2 声明式多步计划 / C3 只读 CDP 白名单需 D9+A / C4 完整 browser_exec 拒绝；D9 floor 为前置）。

2026-11-22 交付核对与两项残留（本批只读核对，无源码改动）：

- **已交付项逐条核对通过**：K01(b) 路由键（`adapters/models.py:23-40` 派生键 + provider 分类、`_prepare` 注入）、cron 四批（`domain/cronjob.py`/`application/cronjob.py`/`composition/instance.py:743` attach/`v0049`）、browser_dialog（`adapters/workers/main.py:432-516` 持有队列）、B 站读广度与风控暂停（详见 [v2-delivered-verification.json](../../evidence/v2-delivered-verification.json)）。核对套件 **112 过 / 204.17 秒**（prompt cache、cronjob tool/e2e、schedule context/bindings、browser_dialog）。
- **新增残留 D8（cron 阻塞与后台评审交互）**：`schedule_blocker` 无 purpose 过滤，把 K08/K06 的 `summary` 辅助调用当未复核外部结果；已核机制与影响，合同 [V2_SCHEDULE_REVIEW_BLOCKER](V2_SCHEDULE_REVIEW_BLOCKER.md)，待所有者选 A–D。
- **组 2 URL floor 已核（原「待核」）**：worker 只校验 scheme 且跟随重定向，无主机/IP floor；实测 loopback 元数据路径可读。策略待决 → 决策登记 D9（含 4 个选项与验证要求）。

## 需实机/账号/网络验收清单（不可离线宣称对等）

- 渠道全集（discord/feishu/yuanbao/x_search/27 gateway 平台）、hosted connector 账号管理、catalog hub。
- 浏览器云会话/真实 profile/凭据 vault/沙箱/Lightpanda。
- 远程终端与代码 kernel 后端（docker/ssh/modal/daytona/vercel）。
- computer_use 真实桌面设备 + cua-driver；Android 真机质量。
- QQ/B 站/投递的真实账号送达质量。

## 完成条件与边界

K13 交付本逐项审计记录（六组分类 + 可离线缺口清单 + 实机验收清单），证据为参考固定提交工具名清单与 V2 工具面对照。**审计完成不等于全集功能对等**：可离线缺口与实机验收项均保留为后续任务，`whole_core_parity_complete` 与 `whole_reference_feature_audit_complete` 仍为 false（K14… 领域参考审计未核完）。本批不新增工具/迁移/路由，不改运行中服务与数据库。