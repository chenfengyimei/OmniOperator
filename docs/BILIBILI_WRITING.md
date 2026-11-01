# B站评论与私信草稿（D23）

默认关闭。代码已通过模拟网页的真实 Chromium 闭环，**尚未使用真实账号发布**。
当前没有经过实站核验的通用选择器预设；用户必须先核验页面控件，再启用相应流程。
Cookie 读取配置和浏览器登录 profile 是两个入口，导入读 API 的 Cookie 不等于浏览器已登录。

## 数字具身视角的能力全景（D32）

设置中心的「数字具身 → 哔哩哔哩」分区与工具面是同一套能力：

| 能力 | 工具 | 级别 |
|---|---|---|
| 账号（UID/昵称/等级） | `bilibili_account_info` | SAFE 只读 |
| 视频信息 | `bilibili_video_info` | SAFE 只读 |
| 字幕 / 语音转写 | `bilibili_video_subtitle` / `bilibili_video_transcript` | SAFE 只读 |
| **抽帧拼图看画面** | `bilibili_video_frames` | SAFE 只读（产物） |
| 评论（热门/最新、可翻页） | `bilibili_video_comments` | SAFE 只读 |
| 楼中楼回复 | `bilibili_comment_replies` | SAFE 只读 |
| 找用户（拿 UID） | `bilibili_user_search` | SAFE 只读 |
| 存草稿（评论/私信/艾特） | `bilibili_prepare_write` | SAFE（只存不发） |
| 提交草稿 | `bilibili_submit_draft` | CONFIRM + 人工确认 |

### 抽帧拼图：一张图看完整个视频

`bilibili_video_frames` 会把视频按时间点等距抽帧（默认 6 帧、最多 12 帧），
**拼成一张**联络表存进产物库，返回 `[artifact:id]`。多模态模型一次就能看完
视频的画面脉络（谁在讲、屏幕上出现了什么），比逐帧发 N 张图又快又省 token。

实现：`tools/bilibili/frames.py`（PyAV 解码 + Pillow 拼接）；
画面文字仍建议配合字幕/转写工具一起看——抽帧看的是"画面"，不是"台词"。

### 回复评论与艾特

- **回复某条评论**：目标 URL 带 `?reply_id=<评论ID>`（如
  `https://www.bilibili.com/video/BVxxx?reply_id=88001`）。ID 来自
  `bilibili_video_comments` 的输出；其它查询参数一律拒绝。
- **艾特**：正文里写 `@{uid}` 占位，并在 `mentions` 里给出 `{"uid": "...", "name": "..."}`，
  存草稿时替换成 `@昵称`，并把艾特清单摆进人工确认的文案里。
  没给昵称的 UID 一律拒绝——宁可不发，也不发一个裸 UID 让读者自己猜是谁。
  **当前产出的是文本 @昵称，不是 B站网页版的真实 mention 组件**；真实艾特需要
  用户核验页面上的用户选择控件后再开（本文档未收录该开关）。


## 使用与配置

1. 用户在浏览器完成登录并打开指定视频或私信会话，处理验证码或安全挑战。
2. 在 `config.yaml` 的 `bilibili_write` 配置 `enabled: true` 及 `forms.comment` /
   `forms.private_message`；每个表单须提供下列控件描述。更改后重启相关 Omni 入口。
3. 聊天请求 `bilibili_prepare_write(kind,target,account,content)`，只观察页面和保存草稿。
   `account` 必须为当前登录者 UID；评论目标为 BV 视频 URL（回复某条评论时带
   `?reply_id=<评论ID>`），私信目标路由为
   `https://message.bilibili.com/#/whisper/mid<UID>`。实站若路由/控件不同，先更新适配并测试。
4. 工作台“发布草稿”可查看完整内容、修改与丢弃草稿。修改用 hash 做并发校验，旧审批失效。
5. `bilibili_submit_draft` 的人工确认必须包含草稿 ID、全文、hash、账号、目标和类型。
   smart / 无人值守不能批准。批准只覆盖本次草稿，默认观察有效期 600 秒。

| 字段 | 核验要求 |
| --- | --- |
| account / account_attr | 唯一可见的当前登录者控件；属性必须包含 UID 或可解析出 UID 的个人页 URL |
| target / target_attr | 唯一可见的当前视频 BV / 私信收件 UID，不能用显示昵称代替 |
| editor / submit | 唯一可见的可编辑内容框与发送按钮；不可选择密码框 |
| records / record_id_attr | 已发布记录集合与稳定记录 ID；不能用易变索引代替 |
| record_text / record_author / record_author_attr | 记录内唯一正文与作者控件及作者 UID 属性 |
| challenge / logged_out | 验证码/安全挑战与登录失效提示；匹配元素过多或可见即停 |

属性默认 `data-mid` / `data-id`，可显式用 `text` 取可见文本。选择器不是模型参数，
只有用户配置能指定。不要把会变化的昵称作为账号身份。控件必须在顶层 DOM 中；
跨域 iframe、需要可信物理点击的站点、无法读取稳定发布 ID 的页面暂不支持。
`max_content_chars` 默认 1000，上限 2000；填写和点击各占一份浏览器每日写额度。

## 结果与恢复

`state.db` v13 的 `publication_drafts` 保存账号、目标、全文 hash、观察、状态和回读证据。
观察检查 URL、页面时间原点、账号/目标原始属性、编辑框/按钮身份、输入与已有记录。
填写后复核，最终在同一个浏览器 JS 任务内检查保留的 DOM 元素后调用按钮 `click()`，
走页面 UI，不调用 B站私有写 API。发布成功须读到**新增稳定 ID + 同账号 + 同内容 hash**。
页面回读只能证明当时可见的发布记录，不保证后续审核通过或永久存在。

- `blocked`：未开始填写，拒绝执行；修正后须重新准备。
- `unknown`：填写后异常、可能自动保存、发送后回执缺失或排队超时。禁止自动重发。
- `sending`：正在执行或进程中断。300 秒内禁止人工改变结果，超时后也须先实站核查。
- `sent`：页面新增记录已核对；证据不包含 Cookie 或历史私信正文。

工作台只能登记人工核查的“已发送/未发送”，不会代替点击发布。
“未发送”释放重复保护，但旧草稿仍不能发送；必须重新观察、建草稿和审批。
同账号/目标/内容在 sending、unknown 或 sent 时有数据库唯一约束，跨进程不能重复领取。
超时后排队动作有撤销标记，晚到的浏览器任务不会继续发送。

## 验证边界

`tests/test_bilibili_publishing.py` 使用临时浏览器 profile，拦截并回填全部网络请求：
评论/私信一次提交、身份变化、最后瞬间账号漂移、审批后编辑、过期、取消、额度不足、
重复/跨进程领取、验证码、失效登录、回读丢失及超时迟到动作。
`tests/test_bilibili_publishing.py` 另覆盖艾特替换（缺昵称即拒）与 `reply_id` 目标。
`tests/test_bilibili_api.py` 走 httpx.MockTransport，覆盖账号、评论（新接口与旧接口
两条路）、楼中楼、用户搜索、视频流（合并流优先、DASH 兜底）。
`tests/test_bilibili_frames.py` 用 PyAV 现场造短片，验证抽帧数量、宽度缩放与拼图。
`tests/test_embodiments_api.py` 覆盖具身总览与账号接口（账号外呼用假实现替身）。
`tests/test_web.py` 检查配对、草稿 CAS 与人工结果登记。
没有自动打开真实账号或向真实视频/用户发送内容。实站验收仍需用户明确账号、目标、全文，
先观察验证控件，再对一次具体发送进行确认。
