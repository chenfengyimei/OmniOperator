# V2 B站直播设计合同（K18 缺口 #6：room/WS 弹幕/gift 只读/send/独立开关，决策 D7）

状态：**设计合同（design_contract_recorded，纯文档，无源码/测试/迁移/路由/worker 改动，2026-11-23）**。**未实现**。承接 [K18 审计](V2_BILI_WHALE_AUDIT.md) (f) 与 [剩余差异决策登记](V2_REMAINING_DECISIONS.md) D7；本册把参考 `dsh-bili-whale` 的直播房间/历史/实时弹幕/发送/gift 只读映射到 V2 四层、既有 `websockets` 依赖与写路径纪律，并明确哪些**不实现**。主动观看属 D5，分享属 D6，写广度属 D4。

## 参考事实（固定 `8b0009f8`，`tools/commands/danmaku.mjs`）

- **房间**：`GET https://api.live.bilibili.com/room/v1/Room/get_info?room_id=<id>`（失败回退 `short_id`）→ `data.room_id/short_id/uid/title/live_status(1=直播中)/online/attention/description`。
- **发送预检**：`GET https://api.live.bilibili.com/xlive/web-room/v1/dM/GetDMConfigByGroup?room_id=<real_id>` → 可用颜色/模式/字号（参考只打印）。
- **历史弹幕**：`GET https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory?roomid=<real_id>&room_type=0` → `data.room[]`（`timeline/uid/nickname/text`）。
- **发送**：`POST https://api.live.bilibili.com/msg/send`（form：`csrf/csrf_token= bili_jct`、`roomid`、`msg`、`rnd`、`fontsize=25`、`color=16777215`、`mode=1`、`bubble=0`、`room_type=0`、`jumpfrom=0`、`statistics={appId:100,platform:5}`；可选 `replay_dmid`/`reply_mid`/`reply_uname`）；错误码提示 `0/-101/-111/-400/1003212/10031`；**发送失败必须非零退出**（参考外部审计修复：exit 0 会被当成已发出）。发送前过限额闸（`room:<id>` 白名单/额度）。
- **实时弹幕**：`nav` 取 WBI keys → `GET https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?id=<real_id>&type=0&wts=…` **必须 WBI 签名**（无签名实测 -352）→ `token` + `host_list[]`；`wss://<host>:<wss_port>/sub`；16 字节包头 `total(u32BE)/headerLen(u16BE=16)/ver(u16BE)/op(u32BE)/seq(u32BE=1)`；auth `op=7` JSON `{uid, roomid, protover:3, platform:'web', type:2, key:token}`；心跳 `op=2` 30 秒；`ver=3` **brotli**、`ver=2` **zlib inflate** 递归解包；`op=5` JSON：`DANMU_MSG`（`info[1]` 文本、`info[2][0]` uid、`info[2][1]` 昵称、dmid）、`INTERACT_WORD`（进场）、`SEND_GIFT`（`data.uname/giftName/num`，**只读**）；监听时长由 `--seconds` 限定。
- **独立开关/范围**（审计原表）：直播能力独立开关，仅所有者或 @-only 范围；gift/charge 发送参考也未实现。

## V2 现状与映射

| 参考 | V2 原语 | 缺口 |
|---|---|---|
| 房间/历史读 | `bilibili_read`（固定 API、凭据 CAS、有界解析） | 无 `api.live.bilibili.com` 固定 host 与 live 读 operation |
| 实时弹幕 WS | `websockets>=17.3` 已是 `agent` 依赖（OneBot 用 `websockets.asyncio.client.connect`） | 无直播连接/协议解析/生命周期 |
| gift 只读 | 同上（SEND_GIFT 事件） | 无事件解析/有界缓冲 |
| 发送 | 写路径纪律：准备→所有者审批→outbox→**已审浏览器表单**（B 站写不得直接 API POST） | 无直播发送；参考的 API POST 形态**不可照搬** |
| 独立开关 | 能力装配/设置/所有者 HTTP | 无 live 开关/范围 |

## 设计选项（需所有者选定）

### L1 房间信息只读 operation（推荐先做）

- `bilibili_read` 新增 `live_room`，target 为房间号/短号（正十进制，422 前校验）；固定 host `api.live.bilibili.com`；`get_info`（room_id 失败回退 short_id）；有界字段 room_id(int)/short_id(int|None)/uid(str)/title(≤300)/live_status(int|None)/online(int|None)/attention(int|None)/description(≤300)；缺失/类型漂移 502；不缓存（实时性）或短 TTL（决策）。

### L2 历史弹幕只读 operation（推荐先做）

- `bilibili_read` 新增 `live_history`，target 房间号；`gethistory`；`data.room[]` ≤50 行，字段 `timeline(≤40)/uid(str)/nickname(≤100)/text(≤200)`；limit ≤20；缺失=正常空；非列表/坏行 502；不缓存（实时性）。

### L3 实时弹幕监听（WS，所有权与生命周期）

- **选项 A（推荐）**：所有者侧 `LiveDanmakuService`（组合根装配，仅生产路径），按 agent+room 单连接、owner 独立开关；`getDanmuInfo` 走既有 WBI；连接/鉴权/心跳/重组/有界缓冲（≤200 事件、单帧压缩 ≤1 MiB、解压 ≤4 MiB、总时长上限、退避重连 ≤3 次）；提供 owner HTTP 读「最近窗口摘要」（owner-only，模型不可见），摘要仅最新 N 条与计数。
- **选项 B**：纯领域解析器先落（无连接）：用合成帧（brotli/zlib/op5/gift/interact）测协议与边界；连接留到所有者批准后。
- **选项 C**：模型可见 live 摘要 operation（run 内读取最近窗口）——需结合 D5 主动观看，且把不可信弹幕文本注入模型面，建议仅摘要+条数、原文不进 system。
- 需决定：A/B/C；连接上限（每 agent 1、全局 ≤4）；是否落库（建议**内存 ring buffer，不落库、无迁移**）；断线/重连/退避；风险码与暂停联动（live 错误码是否复用 `RISK_CODES` 需实测，建议先显式映射 -101/-111/401 + 暂停）。

### L4 直播弹幕发送（写，默认不做）

- 参考是直接 API POST + csrf。V2 写纪律要求**已审浏览器表单/操作**，不得直接 API POST。选项：
  - **L4-a（推荐）**：本批**不实现发送**，只读 + gift 只读；审计明确「send 未实现」，不宣称对等。
  - **L4-b**：新增已审直播页表单/操作（编辑框 + 发送按钮 + 发送后回读 `gethistory`/WS 确认），走「准备→owner 审批→outbox→已审表单」+ 配额/去重/unknown/恢复，独立写批（需 D6/Q2 与直播 selector 审核、真实账号验收）。
  - **L4-c**：直接 API POST ——**拒绝**（违反 V2 写路径纪律）。
- 需决定：a/b；若 b，配额（每次/每房/每日）、@/reply 字段范围、失败码映射、验证方式。

### L5 独立开关与范围

- `live.enabled` 默认 OFF；按 agent/room 白名单或「仅所有者房间/@-only」；开启/关闭经 owner HTTP（服务启停走既有生命周期/关闭等待）；关闭时连接、任务、缓冲全部释放；被暂停账号（D 风控）不得连接或发送。
- 需决定：开关粒度（agent/room）、是否允许模型请求开启（建议**否**，仅所有者）。

## 安全与有界

- **固定 host**：`api.live.bilibili.com`（HTTP）；WS host 必须来自 `getDanmuInfo.host_list` 且**后缀 `.bilibili.com` + `wss` + 端口 1–65535**，否则拒连；不接受任意 URL。
- **凭据**：HTTP 复用既有 cookie 引用 + 权威 CAS；WS 鉴权只用一次性 `token`（**不发送 cookie**）；日志/事件不含 cookie/token。
- **解压炸弹**：单帧压缩 ≤1 MiB、解压 ≤4 MiB、`eof` 才接受（沿用 danmaku XML 防炸弹先例）；帧头长度校验；未知 `ver` 丢弃；`op` 白名单。
- **不可信文本**：弹幕/礼物/进场文本一律按不可信数据处理（截断、去控制字节；不得当指令）；摘要/事件不入 system 提示。
- **生命周期**：单 agent 单连接、全局上限、心跳 30s、监听时长上限、取消/关闭等待真实释放、重连退避；不新增第二内核。

## 持久化与共享接口（A 协调）

- 推荐首版**无新表、无迁移**（内存 ring buffer + owner HTTP 读）；若需落库（审计/摘要历史）→ 新表 + A 迁移号。
- 若新增模型可见 read operations（L1/L2），仅扩 `bilibili_read` 的 `READ_SPEC` enum/desc + adapter/http Literal（同既有读批），routing 不变；若新增 owner-side 服务，`composition/instance.py` 装配、`capabilities is None` 生产启用、隔离测试禁用。
- `websockets>=17.3` 已是依赖，无需改锁文件。

## 测试计划（实现批）

- 纯领域：房间/历史行解析（缺失/坏形状/上限/文本截断）；WS 包头（长度/头长/版本/op/seq 校验）、ver3 brotli/ver2 zlib 有界解压、递归嵌套、非法帧丢弃、op5 `DANMU_MSG/INTERACT_WORD/SEND_GIFT` 行、上限截断、坏 JSON/超长帧。
- 连接（本地假 WS 服务器 + 注入 connector）：auth op7 载荷、30s 心跳、token/host 校验（非 `.bilibili.com` 拒）、重连退避 ≤3、时长/取消/关闭释放、错误码映射与暂停。
- 应用/装配：开关默认 OFF、owner-only 启停、隔离测试不启连接、owner HTTP 摘要、routing/spec（若加读 operation）、架构/类型守卫。
- 真实 B 站直播/真实弹幕/真实账号另验；未实现前不宣称 (f) 对等。

## 明确不做

- 直播弹幕发送（L4-a 推荐）与 gift/charge 发送（参考也未实现）；直播录制/转码；主动观看（D5）；分享（D6）；写广度（D4）；真实账号验收；全量；72 小时。

## 关联

- K18 审计：[V2_BILI_WHALE_AUDIT](V2_BILI_WHALE_AUDIT.md) (f)。
- 决策登记：[V2_REMAINING_DECISIONS](V2_REMAINING_DECISIONS.md) D7。
- 相关合同：[V2_BILI_WRITE_BREADTH](V2_BILI_WRITE_BREADTH.md)（写纪律/配额）、[V2_BILI_DANMAKU](V2_BILI_DANMAKU.md)（录播弹幕 XML 与解压先例）、[V2_BILIBILI](V2_BILIBILI.md)。
- 证据：[v2-bili-live-design.json](../../evidence/v2-bili-live-design.json)。
