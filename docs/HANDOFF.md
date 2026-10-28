# OmniOperator 交接文档（给后续开发者 / AI）

> 生成于 2026-10-27，交接核对更新于 2026-10-28。本文是跨会话接手的唯一入口：先读本文，再读 `docs/ARCHITECTURE.md`（模块职责唯一权威）。
> 学习资料在 `xuexi/`（已 gitignore，不入库）：`NOTES.md` 是四个参考项目的深度提炼，本项目的对标路线全部出自它。

## 0 接手顺序（R0–R7 开发轮次）

1. 本文（能力清单 + 平台坑）
2. `docs/ARCHITECTURE.md`（分层与组装根唯一权威）
3. [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md)（**当前进度、A01–A20 阻断项证据、谁在哪个 commit 关了什么**）
4. [LOCAL_AGENT_PLAN.md](LOCAL_AGENT_PLAN.md)（完整评估、续接快照与路线）→ [DEEPSEEK_DEVELOPMENT_OUTLINE.md](DEEPSEEK_DEVELOPMENT_OUTLINE.md)（任务卡、验收与可复制续接指令）

任何"已完成"的宣称须核对台账证据、实际代码和测试：源码发现 ≠ 已复现 ≠ 已修复 ≠ 真实验收完成。
续接核对至 `57ed080`：D00–D15 已有实现提交；当前从 D16 推进至 D19，先读已有差异接续。若台账已更新，继续下一个未完成子项。任务行写“完成”不能代替其剩余限制和真实验收。

---

## 1. 一句话定位

OmniOperator（默认昵称"小鱼"）是跑在用户 Windows 电脑上的常驻"像人"具身个人 agent：
QQ / Web 工作台 / CLI / 心跳四入口，能看 B 站、操作浏览器、控制安卓手机、跑命令、自维护记忆与技能。
红线：**花钱 / 发布 / 群发 / 加好友必须人工确认；无人值守一律 fail-closed；小号试水防风控。**

- 仓库：https://gitee.com/chenfengloveyuri/OmniOperator.git（master）
- 本地：`D:\xm\OmniOperator`，Python 3.12 + uv
- 测试：`uv run pytest`；台账记录 **634 全绿**（2026-10-28 D18 审阅队列批次），工作区与当前环境应另行重跑；架构守卫见 `tests/test_arch.py`。续接时发现假 runner 测试依赖 ADB PATH（本机需 `D:\tools\platform-tools` 在 PATH 上，否则 `test_safe_allows_known_readonly_commands` 一例环境性失败），需在 D26 消除环境依赖，详见完整计划 §0。**另注**：`tests/test_state.py` 里 `test_v2_to_v3_migration_keeps_session_leases` 被重复定义两次（后者覆盖前者，前一份从未执行）——属既有问题，修复时保留两份内容中更完整的那份。
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
| core | skills_list / skill_view / skill_create / skill_patch / skill_rewrite / skill_history / skill_rollback | 技能（SKILL.md，渐进披露，patch 按节省 token，expected_hash CAS + 修订历史）；skill_view(name, path="references/guide.md") 按需读参考文本；工作台技能页可编辑（保存 CAS/差异/回滚，D18） |
| core | delegate_task | 子任务委派到 UUID 独立会话（同标签并发也不串历史，thread-local 防嵌套，会话引用自动还原） |
| shell | peek_command（SAFE，结构化 argv）/ run_command（CONFIRM） | 本机命令；SAFE 先解析成 argv 再 `shell=False` 执行（拒复合命令/重定向/写参数/密钥路径），CONFIRM 保留 shell 语义；两者都有超时后进程树回收与"副作用未知"话术；灾难级硬黑名单即便确认也不执行 |
| bilibili | video_info / video_comments / video_subtitle / video_transcript | 读四件套（WBI 签名 + ASR 兜底） |
| qq | qq_send_message | 主动私聊（仅白名单；空白名单 = 谁都不许，读时生效） |
| cron | cron_list / cron_add / cron_edit / cron_remove | 定时任务（支持 skills 注入 / deliver_to_qq / context_from 任务链；edit 只改传入字段、显式 null 清除） |
| browser | browser_* 读 SAFE / 写 CONFIRM | Playwright 持久登录态 + 每日写预算 + 审批流；截图存为产物 [artifact:id]（开 vision 后模型可直接看图） |
| android | status / screen_tree / screenshot + tap_element / tap_coords / swipe / key / type | 8 工具，读 SAFE 写 CONFIRM；截图同样入产物库；tap_element 观察绑定（D16/A12）：点击前复核界面、不一致不点、点后强制复检 |
| system | system_status（SAFE）/ system_service（CONFIRM） | NapCat/omni-qq/心跳 启停与状态 |
| mcp | mcp_list_tools / mcp_read_tool / mcp_call_tool | 本地 stdio 服务，渐进发现、用户只读白名单、人工确认；配置见 `docs/MCP.md` |

### 4.2 Agent 内核（`core/`）
- 工具循环 + 步数预算（8）+ grace call 强制收敛
- **产物 → 图像块（D15/A13）**：截图统一入产物库（sha256/归属/保留期），
  工具结果带 `[artifact:id]` 标记；开 `vision.send_to_cloud` 且有视觉模型时转成图像块
  （缩放 + JPEG 重编码 + 坐标口径说明），任一门没过就**如实降级**说"看不到图"
- **上下文压缩**（`core/compress.py`）：按消息区间持久摘要，FTS 增量维护；回合内工具输出临时压缩仍在内存，字符阈值和摘要质量待继续收口
- **消息不变量断言**：system 唯一首位 / 一批 tool_calls 后允许连续多项结果，每个调用 id 必须恰好匹配一项结果，不能漏回、重复或插入其它消息
- **回合作用域**：SessionRef.bind 在正常返回和异常时均恢复调用者会话；delegate 保持父审批链，无需放宽权限
- 系统提示词 = 人设 + 常驻记忆快照（用量头）+ 档案块，单轮内字节稳定
- review 已有有界队列、区间去重和共享模型账本；记忆额度、来源、作用域与遗忘已接入，候选变更/差异确认待 D18
- 运行账本、事件续读、协作式取消与 ToolResult 已接入；旧字符串工具仍有兼容适配，跨进程取消和在途操作限制见台账
- 模型能力、超时、有界重试、usage/费用预留结算已有实现；**预算默认未设上限**，无人值守付费推理需明确授权与限额
- 心跳调度：tick 30s，state.db 原子领取同一到期时刻并推进 next_run；任务可编辑（乐观 revision）；成败只看执行器是否抛异常（run_turn 结构化状态）；投递 outbox 已上线（掉线入队/恢复补投/unknown 不自动重发）；`context_from` 绑定具体 run 与并行上限留待后续

### 4.3 通道与界面
- **Web 工作台**（`channels/web.py` + `web_static/index.html` 单文件前端）：
  会话 / 聊天（**运行进度实时可视化**：等待期间显示每步模型调用与工具执行、可一键停止本回合，回复带费用标签）/
  右侧七页（记忆·档案·常驻·待办·Review·技能·产物）/ 工具列表 / 服务面板 / 扫码弹窗 /
  设置中心（人设·模型·记忆·B站与转写·浏览器·QQ·定时任务（含编辑表单+执行历史）·
  **用量**（今日花费/剩余额度/用途汇总/调用流水/模型能力）·
  **MCP**（服务状态/工具目录/只读与需确认授权标记；工具目录指纹漂移提醒与复查确认、失败服务重连，D19）·关于）/
  **内置终端**（xterm.js + ConPTY，header「⌨ 终端」）/ **安卓手机页**（MJPEG 看屏 + 点屏下发 + UI 树）
  / **配对身份**（`channels/web_auth.py`：控制台配对链接 → HttpOnly 会话 cookie；Host/Origin/CSRF 三级校验；
  终端 WS 握手鉴权；设置接口只回「密钥是否已配置」）/ **产物面板**（缩略图 + 大图 + 手动清理；
  聊天正文里的产物标记渲染成内联图，`/api/artifacts*` 带归属与指纹校验）
- CLI（rich REPL，`/new /list /use /switch /tools /memory /skills /help`）
- QQ（OneBot v11 正向 WS，白名单 + 拟人延迟 + 长消息分段）：
  空白名单不响应任何人；私聊 `dm:<qq>` 与群聊 `group:<群号>` 各自一条历史；
  群聊只在被 @ 时回复，且**不注入**所有者档案/常驻笔记、不提供个人记忆工具
- 心跳（无人值守 DenyApprover；支持 QQ 投递，空白名单不投递；**投递 outbox（D17）**：
  QQ 掉线时任务结果入队、恢复后每 60s 自动补投；发送结果未知的**不自动重发**，面板人工决定；
  白名单撤销对队列同样生效——补投前逐条重查；3 次失败成死信；工作台「定时任务 → 待投递」可见可操作）

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
已借鉴并实现多入口内核、grace call、todo、消息不变量、三层记忆、review、技能 patch、cron、审批、受限委派和工具注册；后续补了运行/取消、预算、持久摘要、文件/网页读取和产物/图像消息。
这不表示 Hermes 对标已完成。设备闭环、调度投递、技能治理、MCP 管理、运维评测和真实验收仍有缺口；完整比较与门槛见 [完整计划](LOCAL_AGENT_PLAN.md)。没有同模型同任务评测，不能断言实际效果相当。

MCP stdio 已支持，HTTP/OAuth 与生产联调待做（见 `docs/MCP.md`）。技能 references、历史/CAS 底座已有，工作台编辑/回滚与安装扫描待 D18。桌面、受控主动性和向量记忆属于后续扩展，不能替代安全和稳定性收口。

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
20. **Playwright 同步 API 只能用一条线程**：所有调用都进 `BrowserDriver._OwnerThread` 的队列；别再给驱动加 `RLock` 就当线程安全（那只防并发、不改线程归属）。超时返回 `BrowserUnknown`——是"可能仍在进行、状态未知"，不要自动重试写操作；`close()` 超时也要如实说"可能仍在运行"
21. **文件类记忆不要用 `threading.Lock`**：`skills/`、`data/MEMORY.md` 的写入者是 CLI/心跳/工作台三个进程，线程锁各管各的。整段读改写要拿 `system/filelock.FileLock`（进程退出自动释放，无僵尸锁），落盘走 `atomic_write_text`（固定 `.tmp` 名字会在并发时互相覆盖）。技能/常驻改动都会进 `data/history/`，回滚是「产生新修订」而不是把版本号改小
22. **指纹按字节算**：`view()` 用 `read_text` 得到的是 LF 版本，`read_bytes` 得到的是磁盘上的 CRLF，两者哈希不同 → 用户用记事本存过的技能会被误判「被改动过」。指纹统一取 `read_bytes()`
24. **取消是协作式的**：不杀线程、不发信号，检查点在「每次模型调用前 / 模型返回后 / 每次工具调用前」。取消时状态先 `cancelling`，真停了才写 `cancelled`——把仍在跑的线程显示成已停止是最容易被信以为真的谎
25. **run 的 owner 决定重启对账范围**：`owner = <kind>:<pid>:<boot>`，`interrupt_stale()` 只对同类进程（web/qq/heartbeat）的旧启动对账；否则网页重启会把心跳手里正在跑的任务宣布成失败
26. **审批器签名不能靠 try/except TypeError 探测**：`type(绑定方法)` 对所有方法都是 `method`，拿它做缓存键会把「WebApprover 支持 cancel」套到每一个审批器上。按 (所属类, 方法名) 缓存
27. **授权判定只写一处**：`private_target_allowed` 之外不要再手写 `if allowed_users and ...`——正是这种"空 = 不限"的写法让空白名单放行了所有人和 cron 投递。策略是**读时**从配置源取的，改内存里的 `runtime.config` 不会生效（测试也一样，要改就改 `runtime.config_path` 指向的文件）

## 6. 未完成事项（按优先级）

### 真实验收的外部前提
- QQ 北极星实测：工作台扫码登录 NapCat → 大号 10000000001 给小号 10000000002 发 B 站链接
- B 站小号 Cookie：工作台设置中心贴 SESSDATA → 解锁字幕/音频流 → 才能做 P3 发视频实操
- 安卓真机：开 USB 调试插上 + 装 ADBKeyboard.apk → P4 联调（工作台「📱 手机」页已可用）

### 当前开发顺序（以最新台账与工作区为准）

1. **D16 安卓闭环（离线部分已完成）**：观察绑定点击已交付——tap_element 必须带 screen_tree 的
   `obs=<id>`，点击前复核界面、不一致不点、点后强制复检；旧 idx 绝不点新元素（A12 关闭）。
   两设备交错/旋转/ADB 断连复检/中文输入恢复待**插真机**验收；真实视觉效果和 MJPEG 带宽同。
2. **D17 cron（已完成）**：两批交付——①任务编辑（工作台「编辑」按钮 + `cron_edit` 工具，
   字段级"没传=不改、null=清除"，expected_revision 乐观并发）+ 成败判定去前缀化；
   ②投递 outbox：QQ 掉线任务结果入队、心跳恢复后每 60s 补投（3 次上限成死信）、
   unknown 不自动重发（面板人工决定）、白名单撤销即丢、同内容活跃期去重；
   工作台「定时任务 → 待投递」面板。**剩余细项**：`context_from` 绑定具体成功 run、
   并行上限、显式时区。夜间整理→早报列为真实验收。
3. **D18 技能工作台（已完成）**：①技能页可编辑——保存带指纹 CAS（冲突拒绝）、
   历史/差异/回滚（回滚产生新修订）、修订里 author/source 可区分用户手改与 agent 修改；
   ②**候选变更审阅队列**——agent/review 自动改的修订进"待审阅"横幅（作者=user/external 不进），
   每条可看"这次改了什么"（相对上一版 diff）、一键回滚到变更前、标记已读（只前进）。
   **剩余**：外来包隔离安装（当前无导入通道，无导入攻击面）。
4. **D19 MCP 管理（已完成）**：工作台「MCP」页——服务状态（idle/握手/已连接/失败/停用，
   纯派生不启动服务）、工具目录与授权标记（只读白名单 🟢 / 需确认 🟡，只认用户配置）、
   **schema 漂移检测**（发现时登记指纹；服务升级换参数形态 → ⚠ 提醒复查只读授权，
   用户「复查后确认」才清除）、失败服务「重连」按钮（一次干净握手）。
   **剩余**：连接/健康细节（无自动退避重连）、Streamable HTTP/OAuth（可独立后置）、
   真实受信生产服务联调。
5. **D25/D26 运维与回归持续推进**：进程身份、备份恢复、CI/任务评测及内核剩余项；D27 完成必要真实验收与 72 小时常驻。
6. **R5/R6 扩展**：桌面逐应用验证后台/前台能力、QQ 受控主动性、B 站人工批准 UI 写、FTS+向量混合检索。不要承诺 SendInput 对所有软件后台不抢焦点，也不直接用向量替代 FTS。

完整 D00–D27 任务卡、依赖和验收见 [开发大纲](DEEPSEEK_DEVELOPMENT_OUTLINE.md)，其中 §12 可直接交给 DeepSeek 4.1f 续接。

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
  core/run.py            运行身份/取消信号/RunContext/ActiveRuns（协作式取消）
  llm/gateway.py         LiteLLM 统一网关
  memory/store.py        SQLite 仓储（会话/消息/事实/档案/review/绑定/待办/摘要）
  memory/resident.py     常驻小记忆（MEMORY.md 式，限额+防注入）
  memory/skills.py       SKILL.md 技能库（create/patch/rewrite）
  memory/artifacts.py    产物登记（截图/导出：sha256/归属/保留期/总量上限，D15）
  core/vision.py         产物字节 → 模型图像块（缩放/JPEG/坐标口径，D15）
  tools/shots.py         browser/android 截图 → 产物库 的共用桥（D15）
  memory/jobs.py         Job 模型 + 校验 + 调度时间（纯数据/纯函数）
  memory/state.py        data/state.db：任务/执行账本/写额度/迁移记录（事务、原子领取、revision）
  memory/revisions.py    内容修订快照（技能/常驻的回滚底座）
  memory/runs.py         运行记录 + 事件流（单调 seq、重启对账）
  memory/cron.py         兼容再导出（老导入路径不变）
  safety/gates.py        风险分级 + 各上下文 Approver
  safety/smart.py        smart 审批档（ask_model 注入）
  safety/broker.py       跨线程确认（Web 横幅）
  tools/registry.py      Registry + SessionRef（execute→ToolResult，dispatch→字符串）
  tools/result.py        ToolResult 契约（status/error_code/side_effect_state + 旧字符串适配）
  tools/runctx.py        RunRef（线程内的「当前 run」，给委派用）
  tools/builtin.py       core 工具（记忆/档案/待办/常驻/摘要）
  tools/shell/           SAFE 结构化命令（spec/process/danger/tools）+ 兼容薄层 shell_tools.py
  tools/delegate_tools.py 子任务委派
  tools/skill_tools.py   技能工具
  tools/cron_tools.py    定时任务工具
  tools/bilibili/        B站 API + ASR
  tools/browser/         Playwright 驱动器（单一所有者线程 + profile 租约）+ 写预算
  tools/android/         ADB 协议 + UI 树 + 语义驱动
  tools/qq/              OneBot v11 客户端与工具
  tools/system_tools.py  服务管理工具
  tools/mcp/             MCP stdio 客户端、生命周期与三项工具适配
  system/services.py     服务启停（分离进程、pid、日志）
  system/atomic.py       原子写（同目录唯一临时名 + fsync + replace）
  system/filelock.py     跨进程文件锁（msvcrt/flock，超时带持有者）
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
