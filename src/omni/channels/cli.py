"""本地终端 REPL 入口：uv run omni。"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..config import load_config, load_env
from ..core.agent import Agent
from ..core.tools import build_default_tools
from ..llm.gateway import Gateway, GatewayError
from ..memory.store import MemoryStore

HELP = """\
/new              新开会话
/list             列出最近会话
/use <id>         切换到指定会话
/switch <model>   切换模型（LiteLLM 串，如 deepseek/deepseek-chat）
/model            查看当前模型
/help             本帮助
/quit             退出
其余输入都作为对话发给 agent。"""


def main() -> None:
    load_env()
    config = load_config()
    store = MemoryStore()
    gateway = Gateway(config.models)
    registry = build_default_tools(store)

    # 自动续接最近一次会话（跨会话记得昨天聊的）
    session_id = store.latest_session_id() or store.new_session()
    agent = Agent(config, gateway, store, registry, session_id)

    console = Console()
    console.print(
        Panel.fit(
            f"[bold]{config.persona.name}[/] · OmniOperator Phase 0\n"
            f"会话 #{session_id} · 模型 [cyan]{agent.model}[/]",
            border_style="cyan",
        )
    )
    console.print("[dim]输入 /help 查看命令；直接说话开始聊天。[/]")

    while True:
        try:
            text = console.input("[bold green]你 >[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见。[/]")
            break
        if not text:
            continue

        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            arg = arg.strip()
            if cmd in ("/quit", "/exit"):
                break
            if cmd == "/help":
                console.print(HELP)
            elif cmd == "/new":
                session_id = store.new_session()
                agent.session_id = session_id
                console.print(f"[cyan]已开新会话 #{session_id}[/]")
            elif cmd == "/list":
                table = Table("ID", "标题", "创建时间")
                for sid, title, created in store.list_sessions():
                    table.add_row(str(sid), title, created)
                console.print(table)
            elif cmd == "/use":
                if arg.isdigit() and store.session_exists(int(arg)):
                    session_id = int(arg)
                    agent.session_id = session_id
                    console.print(f"[cyan]已切换到会话 #{session_id}[/]")
                else:
                    console.print("[yellow]用法: /use <会话ID>（先用 /list 查看）[/]")
            elif cmd == "/switch":
                if arg:
                    agent.model = arg
                    console.print(f"[cyan]模型已切换为 {arg}[/]")
                else:
                    console.print("[yellow]用法: /switch <LiteLLM模型串>[/]")
            elif cmd == "/model":
                console.print(
                    f"当前模型: [cyan]{agent.model}[/] · 工具: {', '.join(registry.names())}"
                )
            else:
                console.print(f"[yellow]未知命令 {cmd}，/help 查看可用命令。[/]")
            continue

        try:
            answer = agent.chat(text)
        except GatewayError as exc:
            console.print(f"[red]调用失败：{exc}[/]")
            console.print(
                "[yellow]提示：检查 .env 里的 API key 与 config/config.yaml 的模型名；"
                "或用 /switch 换一个可用模型。[/]"
            )
            continue
        console.print(
            Panel(answer, title=f"[bold cyan]{config.persona.name}", border_style="cyan")
        )

    store.close()


if __name__ == "__main__":
    main()
