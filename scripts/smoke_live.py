"""Phase 0 真实闭环冒烟：走真实 LLM + 工具 + SQLite 持久化。

用法：
    uv run python scripts/smoke_live.py "请记住：我喜欢喝拿铁"
    uv run python scripts/smoke_live.py "我喜欢喝什么？" --fresh

第二步带 --fresh 开新会话（无对话历史），agent 必须靠 recall 工具
从事实库里捞到第一步记住的内容——以此验证跨会话长期记忆。
"""
from __future__ import annotations

import argparse

from omni.bootstrap import build_runtime
from omni.config import load_env
from omni.llm.gateway import GatewayError


def main() -> None:
    parser = argparse.ArgumentParser(description="OmniOperator Phase 0 闭环冒烟")
    parser.add_argument("message", help="发给 agent 的一句话")
    parser.add_argument("--fresh", action="store_true", help="新开会话，不继承对话历史")
    args = parser.parse_args()

    load_env()
    runtime = build_runtime()  # 无 approver：CLI 外的无人值守上下文，safe 工具直通
    if args.fresh:
        runtime.new_session()

    print(f"[会话 #{runtime.agent.session_id}] [你] {args.message}")
    try:
        answer = runtime.agent.chat(args.message)
    except GatewayError as exc:
        raise SystemExit(f"调用失败：{exc}\n提示：检查 .env 里的 API key 与 config/config.yaml 的模型名。")
    print(f"[{runtime.config.persona.name}] {answer}")
    runtime.store.close()


if __name__ == "__main__":
    main()
