# OmniOperator 交接文档（给后续开发者 / AI）

> 生成于 2026-10-27。本文是跨会话接手的唯一入口：先读本文，再读 `docs/ARCHITECTURE.md`（模块职责唯一权威）。
> 学习资料在 `xuexi/`（已 gitignore，不入库）：`NOTES.md` 是四个参考项目的深度提炼，本项目的对标路线全部出自它。

---

## 1. 一句话定位

OmniOperator（默认昵称"小鱼"）是跑在用户 Windows 电脑上的常驻"像人"具身个人 agent：
QQ / Web 工作台 / CLI / 心跳四入口，能看 B 站、操作浏览器、控制安卓手机、跑命令、自维护记忆与技能。
红线：**花钱 / 发布 / 群发 / 加好友必须人工确认；无人值守一律 fail-closed；小号试水防风控。**

- 仓库：https://gitee.com/chenfengloveyuri/OmniOperator.git（master）
- 本地：`D:\xm\OmniOperator`，Python 3.12 + uv
- 测试：`uv run pytest`（当前 **190 全绿**）；架构守卫 `tests/test_arch.py` 会拦分层违规

## 2. 五分钟上手

```powershell
cd D:\xm\OmniOperator
uv run pytest                      # 全量测试（必须全绿再提交）
uv run omni-web                    # 工作台 http://127.0.0.1:8000
uv run omni-cli                    # 终端 REPL
uv run omni-qq                     # QQ 通道（需 NapCat 运行）
uv run omni-heartbeat              # 心跳调度（cron）
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
2. **组装根**：`Gateway / MemoryStore / SkillStore / JobStore / ToolRegistry / Agent / TurnEndReviewer / BrowserDriver / WriteBudget / ApprovalBroker` 只准在 `bootstrap.py` 构造
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
| core | skills_list / skill_view / skill_create / skill_patch / skill_rewrite | 技能（SKILL.md，渐进披露，patch 按节省 token） |
| core | delegate_task | 子任务委派到干净会话（thread-local 防嵌套，会话引用自动还原） |
| shell | peek_command（SAFE，只读白名单）/ run_command（CONFIRM） | 本机命令；含硬黑名单（灾难级即使确认也不执行） |
| bilibili | video_info / video_comments / video_subtitle / video_transcript | 读四件套（WBI 签名 + ASR 兜底） |
| qq | qq_send_message | 主动私聊（限白名单） |
| cron | cron_list / cron_add / cron_remove | 定时任务（支持 skills 注入 / deliver_to_qq / context_from 任务链） |
| browser | browser_* 读 SAFE / 写 CONFIRM | Playwright 持久登录态 + 每日写预算 + 审批流 |
| android | status / screen_tree / screenshot + tap_element / tap_coords / swipe / key / type | 8 工具，读 SAFE 写 CONFIRM |
| system | system_status（SAFE）/ system_service（CONFIRM） | NapCat/omni-qq/心跳 启停与状态 |

### 4.2 Agent 内核（`core/`）
- 工具循环 + 步数预算（8）+ grace call 强制收敛
- **上下文压缩**（`core/compress.py`）：超 24k 字符把旧块压摘要（工具调用对不拆开），失败降级
- **消息不变量断言**：system 唯一首位 / tool 必跟 tool_calls
- 系统提示词 = 人设 + 常驻记忆快照（用量头）+ 档案块，单轮内字节稳定
- 回合后异步 review（`core/review.py`，白名单：记忆/档案/常驻/技能）
- 心跳调度（`core/cron.py`）：tick 30s，先推进 next_run 再派发（至多一次），错过半周期跳过，执行账本

### 4.3 通道与界面
- **Web 工作台**（`channels/web.py` + `web_static/index.html` 单文件前端）：
  会话 / 聊天 / 右侧六页（记忆·档案·常驻·待办·Review·技能）/ 工具列表 / 服务面板 / 扫码弹窗 /
  设置中心（人设·模型·记忆·B站与转写·浏览器·QQ·定时任务（含新建表单+执行历史）·关于）/
  **内置终端**（xterm.js + ConPTY，header「⌨ 终端」）/ **安卓手机页**（MJPEG 看屏 + 点屏下发 + UI 树）
- CLI（rich REPL，`/new /list /use /switch /tools /memory /skills /help`）
- QQ（OneBot v11 正向 WS，白名单 + 拟人延迟 + 长消息分段）
- 心跳（无人值守 DenyApprover；支持 QQ 投递）

### 4.4 安全层
- 审批三档：SAFE 直通 / CONFIRM 问人 / DANGEROUS 拒
- 上下文策略：CLI 控制台问询、Web 横幅确认（阻塞+超时拒）、cron/心跳 DenyApprover
- **smart 审批档**（`config approval.mode=smart`）：辅助模型一词裁决 ALLOW/DENY/ESCALATE，
  参数视为不可信输入包 `<tool-call>` 定界符，ESCALATE/失败回退人工
- 硬黑名单（shell）/ 常驻记忆防注入扫描 / QQ 白名单

### 4.5 Hermes 对标进度
已抄完（NOTES §1）：多入口单内核、grace call、todo、压缩+不变量、记忆三层（档案/事实/常驻快照）、
回合后 review、技能 patch、cron 一等任务（skills/context_from/账本）、审批矩阵（含 smart）、
delegate_task、工具自注册+toolset 分组。
**未做**：cron script/预采集脚本、provider 覆盖、suggestions（主动建议）、
记忆向量检索（sqlite-vec）、computer-use 桌面控制（Hermes 经 MCP 接 cua-driver）、
MCP 客户端支持、技能 references 目录与安装扫描。

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
11. **MJPEG 帧**：screencap 每次 0.5-2s，别提高帧率；PNG 帧浏览器可播，JPEG 压缩需 Pillow（未装）
12. **pytest.raises match 中文** 在 GBK 控制台显示乱码但断言本身正常，别被吓到

## 6. 未完成事项（按优先级）

### 等用户动手（代码已就绪）
- QQ 北极星实测：工作台扫码登录 NapCat → 大号 10000000001 给小号 10000000002 发 B 站链接
- B 站小号 Cookie：工作台设置中心贴 SESSDATA → 解锁字幕/音频流 → 才能做 P3 发视频实操
- 安卓真机：开 USB 调试插上 + 装 ADBKeyboard.apk → P4 联调（工作台「📱 手机」页已可用）

### 建议的下一步开发（按价值排序）
1. **MJPEG 优化**：装 Pillow，半分辨率 + JPEG q40（NOTES §2.2 建议），带宽降 5-10 倍
2. **技能工作台编辑**：目前只能看；加代码编辑器（右侧面板或弹窗）直改 SKILL.md + 版本回滚
3. **cron 任务编辑**：目前只能删了重建；补 PUT 编辑 prompt/schedule
4. **记忆向量检索**（sqlite-vec）：事实层语义搜索，替代纯 FTS
5. **computer-use 桌面**：Windows UIAutomation + SendInput（后台不抢焦点），对标 Hermes cua-driver
6. **MCP 客户端**：接外部 MCP 服务器（Hermes 的生态入口）
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
  config.py              .env + config.yaml（AppConfig）
  core/agent.py          工具循环 + 系统提示词组装
  core/compress.py       上下文压缩 + 消息不变量
  core/review.py         回合后异步记忆整理
  core/cron.py           心跳调度器 + 任务执行/投递 + 账本记账
  llm/gateway.py         LiteLLM 统一网关
  memory/store.py        SQLite 仓储（会话/消息/事实/档案/review/绑定/待办/摘要）
  memory/resident.py     常驻小记忆（MEMORY.md 式，限额+防注入）
  memory/skills.py       SKILL.md 技能库（create/patch/rewrite）
  memory/cron.py         Job 模型 + JobStore + ExecutionLog
  safety/gates.py        风险分级 + 各上下文 Approver
  safety/smart.py        smart 审批档（ask_model 注入）
  safety/broker.py       跨线程确认（Web 横幅）
  tools/registry.py      Registry + SessionRef
  tools/builtin.py       core 工具（记忆/档案/待办/常驻/摘要）
  tools/shell_tools.py   命令执行 + 硬黑名单
  tools/delegate_tools.py 子任务委派
  tools/skill_tools.py   技能工具
  tools/cron_tools.py    定时任务工具
  tools/bilibili/        B站 API + ASR
  tools/browser/         Playwright 驱动 + 预算
  tools/android/         ADB 协议 + UI 树 + 语义驱动
  tools/qq/              OneBot v11 客户端与工具
  tools/system_tools.py  服务管理工具
  system/services.py     服务启停（分离进程、pid、日志）
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
