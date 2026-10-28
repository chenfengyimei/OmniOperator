# QQ 场景与主动提醒

私聊仅响应 allowed_users，群聊仅响应 allowed_groups 内的精确 @。设置中心可开启
“回复近期机器人消息也可触发”：引用 ID 必须来自同一机器人、同一群的成功发送回执，
保留 7 天。私聊和每个群使用独立会话，群聊不进入个人 review。

群聊仅开放 get_time、无登录态 web_read、当前会话 todo；新工具默认拒绝，编造工具名
也不能绕过。带所有者 Cookie 的 B 站工具、浏览器、安卓、桌面、MCP、文件、shell、
个人记忆、委派均不开放。运行身份记录 channel=qq、发言人及 group:群号。

## 事件处理

- state.db v12 仅保存事件 ID/状态，按机器人与场景去重 30 天，最多 20000 条，满额拒绝。
  缺失 ID、重复、撤回墓碑不运行模型；进程中断的在途事件不自动重放。
- 默认 0.3 秒合并窗口，同场景同发送者最多 8 条、合计 16000 字；总在途/排队 64 条、
  最多 32 个场景、4 个并行回合。同场景串行，跨进程会话租约覆盖模型和回发并续约。
- 入站回调不等模型，回执读取持续运行；排队撤回不执行。模型运行中撤回/撤销白名单
  会协作取消并抑制回发和 review，每段发送前再检查。已发生的动作和已发分段无法撤销。
- 出站文本 auto_escape=true，模型生成 CQ 字符串不会被解释为 @全体等平台动作。

## 所有者主动提醒

升级后主动提醒默认关闭，已有定时任务仍执行并保存结果；QQ 投递须在设置中心显式开启：

```yaml
qq:
  enabled: true
  allowed_users: [你的QQ号]
  allowed_groups: []
  proactive_enabled: true
  owner_qq: 你的QQ号
  proactive_daily_limit: 5
  quiet_start: "22:00"
  quiet_end: "08:00"
  timezone: "Asia/Shanghai"
  group_reply_to_bot: false
```

只允许指定所有者。设置中心可编辑策略、查看状态、一键停止：热读开关阻止后续主动
发送，并丢弃未开始的提醒；已经进入网络发送的请求无法保证撤销。模型不能自行开启。
群发、加好友和陌生私信不在主动能力范围。

每日额度按提醒尝试累计，网络失败也保守计数；额度用尽或安静时段留在 outbox，
不消耗网络重试次数。相同起止时间表示不设安静时段。普通用户主动发来的对话回复不受
安静时段影响。outbox 保留任务名和原因，unknown 不自动重发；部分成功后策略变化
同样进入 unknown。人工重排核对活跃内容去重和容量。

离线覆盖四进程竞争、跨重启去重、精确 @、回复归属、合并/撤回、运行中撤销、跨进程忙、
队列满、未知新工具拒绝、每日额度/安静时段、分段期间停用、工作台停止。
真实 NapCat 登录、断网回执和小范围群验收仍待用户参与。

协议依据：[OneBot 通知事件](https://github.com/botuniverse/onebot-11/blob/master/event/notice.md)、
[发送接口](https://github.com/botuniverse/onebot-11/blob/master/api/public.md)。
