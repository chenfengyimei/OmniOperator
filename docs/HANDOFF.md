# OmniOperator 交接文档（给后续开发者 / AI）

> 生成于 2026-10-27。本文是跨会话接手的唯一入口：先读本文，再读 `docs/ARCHITECTURE.md`（模块职责唯一权威）。
> 学习资料在 `xuexi/`（已 gitignore，不入库）：`NOTES.md` 是四个参考项目的深度提炼，本项目的对标路线全部出自它。

## 0 接手顺序（R0–R7 开发轮次）

1. 本文（能力清单 + 平台坑）
2. `docs/ARCHITECTURE.md`（分层与组装根唯一权威）
3. `docs/DEVELOPMENT_STATUS.md`（**当前进度、A01–A20 阻断项证据、谁在哪个 commit 关了什么**）
4. `docs/LOCAL_AGENT_PLAN.md`（完整评估与路线）→ `docs/DEEPSEEK_DEVELOPMENT_OUTLINE.md`（任务卡与验收）

任何"已完成"的宣称只认 `DEVELOPMENT_STATUS.md` 里的证据列：源码发现 ≠ 已复现 ≠ 已修复 ≠ 真实验收完成。

---

## 1. 一句话定位

OmniOperator（默认昵称"小鱼"）是跑在用户 Windows 电脑上的常驻"像人"具身个人 agent：
QQ / Web 工作台 / CLI / 心跳四入口，能看 B 站、操作浏览器、控制安卓手机、跑命令、自维护记忆与技能。
红线：**花钱 / 发布 / 群发 / 加好友必须人工确认；无人值守一律 fail-closed；小号试水防风控。**

- 仓库：https://gitee.com/chenfengloveyuri/OmniOperator.git（master）
- 本地：`D:\xm\OmniOperator`，Python 3.12 + uv
- 测试：`uv run pytest`（当前 **394 全绿**）；架构守卫 `tests/test_arch.py` 会拦分层违规。
  **注意**：常驻服务占着 `.venv/Scripts/omni-*.exe` 时 `uv run` 重建会失败，这时用 `uv run --no-sync pytest` 或先停服务

## 2. 五分钟上手

```powershell
cd D:\xm\OmniOperator
uv run pytest                      # 全量测试（必须全绿再提交）
uv run omni-web                    # 工作台 http://127.0.0.1:8000（控制台会打印配对链接，点开才可操作）
uv run omni-cli                    # 终端 REPL
uv run omni-qq                     # QQ 通道（需 NapCat 运行）
uv run omni-heartbeat              # 心跳调度（cron）
uv run python -m omni.system.backup --to data/backups/2026-10-27   # 离线备份（先备份再升级）
uv run python -m omni.system.backup --restore <备份目录> --into <新目录>  # 恢复到新目录再校验
```

服务管理（工作台"服务"面板或 `omni/system/services.py`）：
- NapCat：`D:\tools\napcat\napcat.bat`，WS 3001，机器人小号 10000000002（**重启后需工作台扫码登录**）
- 所有服务以分离进程运行（无窗口）：pid 在 `data/run/*.pid`，日志在 `data/system_logs/`
- adb（Platform-Tools 37.0.1）在 `D:\tools\platform-tools`，已加入用户 PATH

## 3. 架构纪律（改代码前必读）

`docs/ARCHITECTURE.md` 是唯一权威，`tests/test_arch.py` 用 AST 强制：

1. **分层不倒挂**：`channels`/`bootstrap` → `core` → `tools`/`llm`/`memory`/`safety` → `config`。
   - `safety` 是叶子层：**不能 import llm**（smart 审批用注入的 `ask_model`，不要破坏）
   - `tools` 不能 import `core`/`llm`
2. **组装根**：`Gateway / MemoryStore / SkillStore / JobStore / ToolRegistry / Agent / TurnEndReviewer / BrowserDriver / WriteBudget / ApprovalBroker / ServiceManager / MCPManager` 只准在 `bootstrap.py` 构造
3. **工具接入**：`tools/<域>/` 建包 → `register_xxx_tools(registry, deps)` → 在 `bootstrap.build_runtime` 接线 → 写单测（见 ARCHITECTURE §5 checklist）
4. 工具风险分级：读=SAFE / 写=CONFIRM / 高危=DANGEROUS（当前一律拒）
5. 新数据存储要支持**测试隔离**：`build_runtime(data_dir=tmp_path)`（jobs/账本/常驻记忆/安卓缓存都走它）；`ServiceManager(run_dir=..., log_dir=...)` 同理

## 4. 当前能力清单（已交付）

### 4.1 工具面（`uv run omni-cli` 里 `/tools` 可看全部）

| 域 | 工具 | 说明 |
|---|---|---|
| core | get_time / remember / recall / search_history | 时间 + 事实记忆 + FTS 检索 |
| core | profile_manage | 用户档案（2000 字符限额+报错逼合并） |
| core | resident_memory | 常驻工作笔记（2200 限额，每轮注入系统提示词，带防注入扫描） |
| core | todo | 会话级多步清单（SessionRef thread-local 注入当前会话） |
| core | daily_digest | 过去 N 小时活动摘要（夜间整理素材） |
| core | skills_list / skill_view / skill_create / skill_patch / skill_rewrite | 技能（SKILL.md，渐进披露，patch 按节省 token）；skill_view(name, path="references/guide.md") 按需读参考文本 |
| core | delegate_task | 子任务委派到 UUID 独立会话（同标签并发也不串历史，thread-local 防嵌套，会话引用自动还原） |
| shell | peek_command（SAFE，结构化 argv）/ run_command（CONFIRM） | 本机命令；SAFE 先解析成 argv 再 `shell=False` 执行（拒复合命令/重定向/写参数/密钥路径），CONFIRM 保留 shell 语义；两者都有超时后进程树回收与"副作用未知"话术；灾难级硬黑名单即便确认也不执行 |
| bilibili | video_info / video_comments / video_subtitle / video_transcript | 读四件套（WBI 签名 + ASR 兜底） |
| qq | qq_send_message | 主动私聊（仅白名单；空白名单 = 谁都不许，读时生效） |
| cron | cron_list / cron_add / cron_remove | 定时任务（支持 skills 注入 / deliver_to_qq / context_from 任务链） |
| browser | browser_* 读 SAFE / 写 CONFIRM | Playwright 持久登录态 + 每日写预算 + 审批流 |
| android | status / screen_tree / screenshot + tap_element / tap_coords / swipe / key / type | 8 工具，读 SAFE 写 CONFIRM |
| system | system_status（SAFE）/ system_service（CONFIRM） | NapCat/omni-qq/心跳 启停与状态 |
| mcp | mcp_list_tools / mcp_read_tool / mcp_call_tool | 本地 stdio 服务，渐进发现、用户只读白名单、人工确认；配置见 `docs/MCP.md` |

### 4.2 Agent 内核（`core/`）
- 工具循环 + 步数预算（8）+ grace call 强制收敛
- **上下文压缩**（`core/compress.py`）：超 24k 字符把旧块压摘要（工具调用对不拆开），失败降级
- **消息不变量断言**：system 唯一首位 / 一批 tool_calls 后允许连续多项结果，每个调用 id 必须恰好匹配一项结果，不能漏回、重复或插入其它消息
- **回合作用域**：SessionRef.bind 在正常返回和异常时均恢复调用者会话；delegate 保持父审批链，无需放宽权限
- 系统提示词 = 人设 + 常驻记忆快照（用量头）+ 档案块，单轮内字节稳定
- 回合后异步 review（`core/review.py`，白名单：记忆/档案/常驻/技能）
- 心跳调度（`core/cron.py`）：tick 30s，先推进 next_run 再派发（至多一次），错过半周期跳过，执行账本

### 4.3 通道与界面
- **Web 工作台**（`channels/web.py` + `web_static/index.html` 单文件前端）：
  会话 / 聊天 / 右侧六页（记忆·档案·常驻·待办·Review·技能）/ 工具列表 / 服务面板 / 扫码弹窗 /
  设置中心（人设·模型·记忆·B站与转写·浏览器·QQ·定时任务（含新建表单+执行历史）·关于）/
  **内置终端**（xterm.js + ConPTY，header「⌨ 终端」）/ **安卓手机页**（MJPEG 看屏 + 点屏下发 + UI 树）
  / **配对身份**（`channels/web_auth.py`：控制台配对链接 → HttpOnly 会话 cookie；Host/Origin/CSRF 三级校验；
  终端 WS 握手鉴权；设置接口只回「密钥是否已配置」）
- CLI（rich REPL，`/new /list /use /switch /tools /memory /skills /help`）
- QQ（OneBot v11 正向 WS，白名单 + 拟人延迟 + 长消息分段）：
  空白名单不响应任何人；私聊 `dm:<qq>` 与群聊 `group:<群号>` 各自一条历史；
  群聊只在被 @ 时回复，且**不注入**所有者档案/常驻笔记、不提供个人记忆工具
- 心跳（无人值守 DenyApprover；支持 QQ 投递，空白名单不投递）

### 4.4 安全层
- 审批三档：SAFE 直通 / CONFIRM 问人 / DANGEROUS 拒
- 上下文策略：CLI 控制台问询、Web 横幅确认（阻塞+超时拒）、cron/心跳 DenyApprover
- **smart 审批档**（`config approval.mode=smart`）：辅助模型一词裁决 ALLOW/DENY/ESCALATE，
  参数视为不可信输入包 `<tool-call>` 定界符，ESCALATE/失败回退人工
- 审批加固：仅接受完整的一词裁决；DANGEROUS 硬拒绝、DenyApprover 不被 smart 绕过；
  浏览器/安卓写入与任意 shell 命令带 `requires_human`，smart 直接转人工；缺省审批器拒绝非 SAFE；
  Web 确认框展示完整参数；QQ 空白名单拒绝全部主动投递
- 硬黑名单（shell）/ 常驻记忆防注入扫描 / QQ 白名单

### 4.5 Hermes 对标进度
已抄完（NOTES §1）：多入口单内核、grace call、todo、压缩+不变量、记忆三层（档案/事实/常驻快照）、
回合后 review、技能 patch、cron 一等任务（skills/context_from/账本）、审批矩阵（含 smart）、
delegate_task、工具自注册+toolset 分组。
**未做**：cron script/预采集脚本、provider 覆盖、suggestions（主动建议）、
记忆向量检索（sqlite-vec）、computer-use 桌面控制（Hermes 经 MCP 接 cua-driver）、
MCP HTTP/OAuth 等扩展、技能安装扫描。MCP stdio 客户端已支持（见 `docs/MCP.md`）。技能 references 已支持：主文列路径、三级按需读；限制 UTF-8 文本 64 KiB，禁越界/符号链接/junction/Windows ADS；目录最多列 60 项。

## 5. 平台坑清单（血泪，别再踩）

1. **中文 Windows GBK**：`netstat`/`tasklist` 等必须 bytes 捕获 + `errors="replace"`（或 utf-8→gbk 逐级）；`text=True` 会崩
2. **uv add 装包**：`.venv\Scripts\*.exe` 被运行中的服务占用会失败——先停服务再 uv add
3. **测试隔离**：任何落 `data/` 的存储都要能注入路径（`data_dir` / `run_dir` / `log_dir`），否则测试会污染真机运行态（曾把真实 omni-qq 当测试残留树杀、把测试任务写进真 jobs.json）
4. **FastAPI**：Pydantic 响应模型不要嵌套在 `create_app` 内（annotations 解析失败→422）
5. **Playwright/浏览器**：拟人慢放写在工具层；写操作必过预算+审批+写后回读
6. **av19 与 faster-whisper 冲突**：ASR 用自研 PyAV 解码绕开（`tools/bilibili/asr.py`）
7. **FTS5**：中文 trigram 需 ≥3 字，2 字走 2-gram/LIKE 兜底链
8. **naive/aware datetime**：比较前统一 `astimezone()`
9. **ConPTY 终端**：读取是阻塞的，必须在线程池跑；输出是带 VT 序列的碎块，判断内容别用短前缀
10. **NapCat 快速登录 `-q`** 会触发风控要求扫码；扫码文件在 `napcat/napcat/cache/qrcode.png`
11. **MJPEG 帧**：screencap 每次 0.5-2s，别提高帧率；已用 Pillow 转半分辨率 JPEG q40；点屏继续携带实际帧尺寸，只在后端换算一次
12. **pytest.raises match 中文** 在 GBK 控制台显示乱码但断言本身正常，别被吓到
13. **MCP Windows 进程收尾**：使用 AnyIO CancelScope，不能用原生 task.cancel 打断 SDK 的 shield 清理；Python 服务默认 PYTHONUTF8=1；超时后结果未知，不自动重放
14. **data_dir 完整隔离**：所有可写路径都在 `config.RuntimePaths` 里声明一次，由 bootstrap 注入（jobs/常驻/记忆/浏览器/安卓截图与缓存/服务 pid 与日志/MCP 日志/工作台密钥/B站 Cookie/配置读写）；skills 仍由 skills_root 单独控制。注入 `data_dir` 的实例**不探测也不启停真机进程**——`stop()` 的端口兜底曾把用户真服务当过残留
15. **SAFE 命令不要用 shell**：`shell=True` 下前缀白名单等于没有白名单（`git status & 任意命令`）。改成 argv + `shell=False` 后，参数形态也要逐条校验——`git branch <name>`、`git show HEAD:.env` 都能借只读子命令读写
16. **`git status` 会写 index**：观察类 git 必须带 `GIT_OPTIONAL_LOCKS=0`，否则 SAFE 观察也会改仓库状态；超时用 `taskkill /F /T` 收进程树，不能只 kill 直接子进程
17. **跨进程状态不要用 JSON + threading.Lock**：`JobStore`/`ExecutionLog`/`WriteBudget` 已迁进 `data/state.db`。JSON 时代三个进程互相覆盖、固定 `.tmp` 互踩、读失败当空数据（额度被清零）。额度只能用 `UPDATE ... WHERE used < limit` 的 rowcount 判定，任务领取只能用同一个写事务推进 `next_run_at`——不要退回"先读后写"
18. **备份 SQLite 不要直接 copy 文件**：WAL 里还有已提交事务，复制出来像"丢了一段历史"且不报错。用 `omni.system.backup.snapshot_sqlite`（`Connection.backup()` + `integrity_check` + sha256）
19. **会话 id 由请求固定**：`/api/chat` 不再改写 `agent.session_id`，回答、落库、review、工具作用域全用本回合的 sid。想切到某个会话是客户端动作（`POST /api/sessions/{id}/select`），不是发消息的副作用。并发保护两层：进程内 `SessionGate`（同会话串行、队列满 429）+ `state.db` 的 `session_leases`（跨进程互斥）
20. **授权判定只写一处**：`private_target_allowed` 之外不要再手写 `if allowed_users and ...`——正是这种"空 = 不限"的写法让空白名单放行了所有人和 cron 投递。策略是**读时**从配置源取的，改内存里的 `runtime.config` 不会生效（测试也一样，要改就改 `runtime.config_path` 指向的文件）

## 6. 未完成事项（按优先级）

### 等用户动手（代码已就绪）
- QQ 北极星实测：工作台扫码登录 NapCat → 大号 10000000001 给小号 10000000002 发 B 站链接
- B 站小号 Cookie：工作台设置中心贴 SESSDATA → 解锁字幕/音频流 → 才能做 P3 发视频实操
- 安卓真机：开 USB 调试插上 + 装 ADBKeyboard.apk → P4 联调（工作台「📱 手机」页已可用）

### 建议的下一步开发（按价值排序）
1. **MJPEG 优化（已完成）**：Pillow，半分辨率 + JPEG q40，坏帧/设备离线断流；已验证 RGB/RGBA/灰度与坐标换算，实际带宽与清晰度待安卓真机验收
2. **技能工作台编辑**：目前只能看；加代码编辑器（右侧面板或弹窗）直改 SKILL.md + 版本回滚
3. **cron 任务编辑**：目前只能删了重建；补 PUT 编辑 prompt/schedule
4. **记忆向量检索**（sqlite-vec）：事实层语义搜索，替代纯 FTS
5. **computer-use 桌面**：Windows UIAutomation + SendInput（后台不抢焦点），对标 Hermes cua-driver
6. **MCP 客户端（stdio 已完成）**：懒连接、持久会话、发现分页、只读白名单与人工确认、超时及进程回收；通过真实离线子进程验证，外部生产服务联调与 HTTP/OAuth 待做（见 `docs/MCP.md`）
7. **QQ 群聊**：@ 触发 + 群白名单已就绪，缺群场景的拟人节奏与话题管理
8. **B 站写操作**（P3）：评论/私信走浏览器 UI，复用现有预算+审批+回读
9. **日报模板 + context_from 实测**：夜间整理 → 早报链路端到端跑一次

## 7. 代码约定

- 全量类型标注 + `from __future__ import annotations`；docstring 写「职责 + 明确不做」
- 注释写"为什么"不写"是什么"
- 每个模块必须有单测；提交前 `uv run pytest` 全绿
- 提交信息：中文，`领域：要点——细节（N 单测全绿）`，一条 commit 一个完整交付
- 前端是单文件 `web_static/index.html`（无构建），xterm 资源在 `vendor/` 本地化，不依赖 CDN

## 8. 关键文件地图

```
src/omni/
  bootstrap.py           组装根（唯一构造核心对象处）
  config.py              .env + config.yaml（AppConfig）+ RuntimePaths（全部可写路径的唯一声明）
  core/agent.py          工具循环 + 系统提示词组装
  core/compress.py       上下文压缩 + 消息不变量
  core/review.py         回合后异步记忆整理
  core/cron.py           心跳调度器 + 任务执行/投递 + 账本记账
  core/session.py        会话闸门（同会话串行/队列有界）+ 跨进程会话租约
  llm/gateway.py         LiteLLM 统一网关
  memory/store.py        SQLite 仓储（会话/消息/事实/档案/review/绑定/待办/摘要）
  memory/resident.py     常驻小记忆（MEMORY.md 式，限额+防注入）
  memory/skills.py       SKILL.md 技能库（create/patch/rewrite）
  memory/jobs.py         Job 模型 + 校验 + 调度时间（纯数据/纯函数）
  memory/state.py        data/state.db：任务/执行账本/写额度/迁移记录（事务、原子领取、revision）
  memory/cron.py         兼容再导出（老导入路径不变）
  safety/gates.py        风险分级 + 各上下文 Approver
  safety/smart.py        smart 审批档（ask_model 注入）
  safety/broker.py       跨线程确认（Web 横幅）
  tools/registry.py      Registry + SessionRef
  tools/builtin.py       core 工具（记忆/档案/待办/常驻/摘要）
  tools/shell/           SAFE 结构化命令（spec/process/danger/tools）+ 兼容薄层 shell_tools.py
  tools/delegate_tools.py 子任务委派
  tools/skill_tools.py   技能工具
  tools/cron_tools.py    定时任务工具
  tools/bilibili/        B站 API + ASR
  tools/browser/         Playwright 驱动 + 预算
  tools/android/         ADB 协议 + UI 树 + 语义驱动
  tools/qq/              OneBot v11 客户端与工具
  tools/system_tools.py  服务管理工具
  tools/mcp/             MCP stdio 客户端、生命周期与三项工具适配
  system/services.py     服务启停（分离进程、pid、日志）
  system/backup.py       离线备份/恢复（SQLite 一致快照 + integrity_check + sha256）
  system/config_io.py    config.yaml 白名单键定向修改（保留注释）
  channels/web.py        工作台 REST + WS（终端）+ MJPEG
  channels/terminal.py   ConPTY 终端会话池
  channels/cli.py / qq.py / heartbeat.py
  channels/web_static/index.html  单文件前端
data/                    SQLite、jobs、账本、MEMORY.md、截图、pid、日志（gitignore）
skills/                  种子技能（bilibili-watch / self-check / nightly-tidy）
```

## 9. 给接手者的第一条命令

```powershell
cd D:\xm\OmniOperator; uv run pytest -q; git log --oneline -n 10
```

测试全绿 + 看过最近提交，就可以开工。改任何文件前先读 `docs/ARCHITECTURE.md`；
新增能力的路线图在第 6 节。
