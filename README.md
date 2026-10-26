# OmniOperator

像人一样生活在平台里的具身个人 agent：B站（看视频/发视频/私信）、QQ 收发、ADB 控安卓真机、长期记忆，Windows / macOS 通用。

当前阶段：**P2 地基 + 心跳已就绪** —— B站读四件套（信息/热评/字幕/**转写**）+ 记忆系统（FTS5 / 用户档案 / 回合后自进化 review）+ **Web 工作台** + **技能系统** + **QQ 通道**（待接 NapCat）+ **定时任务心跳**（agent 可自主排程）已完成。

## 快速开始

```bash
# 1. 安装依赖（Python 3.12+；uv 自动创建 .venv）
uv sync

# 2. 配密钥：复制 .env.example 为 .env，填至少一家 API key
#    （用本地 Ollama 则免 key：把 config/config.yaml 的 chat 改成 ollama/<模型名>）

# 3.（可选，推荐）B站 Cookie：浏览器登录B站 → F12 → Network 任一请求 →
#    复制 Cookie 里的 SESSDATA / bili_jct / buvid3，存成 data/bili_cookies.json：
#    {"SESSDATA": "...", "bili_jct": "...", "buvid3": "..."}
#    不配也能用（视频信息/评论匿名可读），但字幕与部分音频流需要登录态

# 4. 启动终端版（自动续接上一次会话）
uv run omni

# 5. 启动 Web 工作台（会话 / 聊天 / 记忆 / 档案 / Review / 技能可视化）
uv run omni-web            # 打开 http://127.0.0.1:8000

# 6.（可选）QQ 通道：按 docs/QQ_SETUP.md 启动 NapCat 后
uv run omni-qq             # 在 QQ 上跟它聊（白名单外的消息会被忽略）

# 7.（可选）心跳/定时任务常驻：对它说"每天早上八点给我发B站日报"即可排程
uv run omni-heartbeat      # 结果可投递到 QQ；无人值守上下文 fail-closed
```

## CLI 命令

| 命令 | 作用 |
|---|---|
| 直接输入 | 与 agent 对话 |
| `/new` | 新开会话 |
| `/list` | 列出最近会话 |
| `/use <id>` | 切换到指定会话 |
| `/switch <model>` | 切换模型（LiteLLM 串，如 `deepseek/deepseek-chat`） |
| `/model` | 查看当前模型 |
| `/help` | 帮助 |
| `/quit` | 退出 |

## 测试

```bash
uv run pytest
```

## 架构

六层分包 + 组装根，依赖单向（`tests/test_arch.py` 用 AST 强制，详见 `docs/ARCHITECTURE.md`）：

```
src/omni/
├── config.py          # .env + config.yaml 加载（叶子层）
├── bootstrap.py       # 组装根：唯一构造核心对象的地方，暴露 Runtime 门面
├── core/agent.py      # Agent 编排循环（纯内核：上下文 → LLM → 工具 → 回答）
├── core/review.py     # 回合后异步记忆整理（自进化，最小权限白名单）
├── llm/gateway.py     # LiteLLM 统一网关（Adapter：海外/国产/Ollama 一套格式）
├── memory/store.py    # SQLite 仓储（会话/消息/事实/用户档案 + FTS5 全文检索）
├── memory/skills.py   # 技能存储（程序性记忆，skills/<name>/SKILL.md）
├── tools/             # Registry + 工具集：builtin（core）、skill_tools、bilibili（读）
├── safety/gates.py    # 审批闸门（Risk 分级 + 上下文审批策略）
├── channels/cli.py    # 终端 REPL（只碰 Runtime，不构造任何对象）
└── channels/web.py    # Web 工作台（FastAPI + 静态前端，uv run omni-web）
```

## 路线图

P0 本地闭环 → P1 B站读 + 记忆成型 → P2 QQ（NapCat）→ P3 浏览器写操作 → P4 安卓（ADB）→ P5 像人化（心跳/夜间整理/Web 面板）。

参考项目在 `xuexi/`（仅学习用，不入库）：Hermes Agent、ghost-in-the-droid/android-agent、dsh-bili-whale、android-adb-control。研究笔记见 `xuexi/NOTES.md`。
