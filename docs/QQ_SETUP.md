# QQ 通道安装指南（NapCat）

> 目标：让小欧通过 QQ 收发消息。需要准备：**一个 QQ 小号**（机器人身份，强烈建议小号）+ NapCat。

## 1. 安装 NapCat（Windows）

NapCat 是无头 QQ 客户端（OneBot v11 协议），开源项目：<https://github.com/NapNeko/NapCatQQ>

1. 到 [Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载 Windows 版压缩包，解压到任意目录（如 `D:\tools\napcat`）
2. 启动 NapCat（首次运行会引导配置）
3. 用手机 QQ **扫码登录小号**
4. 登录成功后记下 WebUI 地址和 token（终端会打印，默认 `http://127.0.0.1:6099`）

## 2. 配置正向 WebSocket

NapCat WebUI → **网络配置** → 新建连接：

- 类型：**WebSocket（正向）**
- 监听端口：`3001`
- token：可留空（仅本机使用）

保存后 NapCat 会在 `ws://127.0.0.1:3001` 监听，等 agent 来连。

## 3. 配置 OmniOperator

编辑 `config/config.yaml`：

```yaml
qq:
  enabled: true
  ws_url: "ws://127.0.0.1:3001"
  access_token: ""            # 与 NapCat 网络配置里一致
  allowed_users: [10000]      # ← 改成你的常用 QQ 号（允许谁跟它说话）
  allowed_groups: []          # 需要群聊再加群号（群里还得 @机器人）
  reply_delay_s: [1.0, 3.0]
```

注意：`allowed_users` 填的是**发指令的人**（你的常用号），不是机器人小号。白名单外的私聊会被静默忽略。

## 4. 启动

```bash
uv run omni-qq
```

看到这两行即成功：

```
[QQ] 已连接 ws://127.0.0.1:3001
[QQ] 机器人 QQ 号：xxxxx
```

然后用你的常用号给小号发条消息试试——丢个B站链接，看看它的看后感。
这些对话会以 `qq:<QQ号>` 为标题存进同一个记忆库，**在 Web 工作台里也能看到**。

## 风控提醒（项目红线）

- **先小号试水**，跑稳几周再考虑其它安排
- 新设备登录 QQ 可能触发安全验证，手机确认即可；频繁异地登录会加大风控概率
- 拟人延迟默认 1-3 秒随机（`reply_delay_s`），别改太短
- 机器人账号的一切异常（冻结/验证码）以 NapCat 日志为准
