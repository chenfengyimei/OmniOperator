# OmniOperator

像人一样生活在平台里的具身个人 agent：B站（看视频/发视频/私信）、QQ 收发、ADB 控安卓真机、长期记忆，Windows / macOS 通用。

当前阶段：**Phase 0 —— 本地 CLI 简单闭环**（多供应商对话 + 模型切换 + 跨会话记忆 + remember/recall 工具）。

## 快速开始

```bash
# 1. 安装依赖（Python 3.12+；uv 自动创建 .venv）
uv sync

# 2. 配密钥：复制 .env.example 为 .env，填至少一家 API key
#    （用本地 Ollama 则免 key：把 config/config.yaml 的 chat 改成 ollama/<模型名>）

# 3. 启动（自动续接上一次会话）
uv run omni
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

## 架构（Phase 0）

```
src/omni/
├── config.py          # .env + config.yaml 加载
├── llm/gateway.py     # LiteLLM 统一网关（海外/国产/Ollama 一套 OpenAI 格式）
├── memory/store.py    # SQLite：会话 / 消息 / 事实记忆
├── core/agent.py      # Agent 循环（上下文 → LLM → 工具 → 回答）
├── core/tools.py      # 工具注册表（function calling）
└── channels/cli.py    # 终端 REPL 入口
```

## 路线图

P0 本地闭环 → P1 B站读 + 记忆成型 → P2 QQ（NapCat）→ P3 浏览器写操作 → P4 安卓（ADB）→ P5 像人化（心跳/夜间整理/Web 面板）。

参考项目在 `xuexi/`（仅学习用，不入库）：Hermes Agent、ghost-in-the-droid/android-agent、dsh-bili-whale、android-adb-control。研究笔记见 `xuexi/NOTES.md`。
