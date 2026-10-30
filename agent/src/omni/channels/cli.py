"""本地终端 REPL 入口：uv run omni。

职责纪律：本文件只做「界面展示 + 输入解析 + 调 Runtime 门面」；
所有对象装配都在 bootstrap.build_runtime 里完成（组装根规则见 docs/ARCHITECTURE.md）。
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..bootstrap import Runtime, build_runtime
from ..config import load_env
from ..llm.gateway import GatewayError
from ..safety.gates import ConsoleApprover

HELP = """\
/new              新开会话
/list             列出最近会话
/use <id>         切换到指定会话
/switch <model>   切换模型（LiteLLM 串，如 deepseek/deepseek-chat）
/model            查看当前模型与工具
/mode [档位]      查看或设置能力档位（auto/chat/retrieval/agent）
/doctor           只读诊断（不调用模型）
/help             本帮助
/quit             退出
其余输入都作为对话发给 agent。"""

# 能力档位（D28）：auto=按输入自动判定；chat=纯聊天；retrieval=只读检索；agent=完整能力。
MODE_CHOICES = ("auto", "chat", "retrieval", "agent")


def main() -> None:
    load_env()
    console = Console()
    runtime = build_runtime(approver=ConsoleApprover(console))
    try:
        _run_console(runtime, console)
    finally:
        result = runtime.close()
        if not result["complete"]:
            console.print(f"[yellow]关闭尚有残留：{result['residual']}[/]")


def _run_console(runtime: Runtime, console: Console) -> None:
    agent = runtime.agent
    # approval.mode=smart：CONFIRM 先经辅助模型预筛，拿不准仍回到终端问询
    if runtime.config.approval.mode == "smart":
        from ..safety.smart import SmartApprover

        review_model = runtime.config.models.review.strip() or agent.model

        def _ask(system: str, user: str) -> str:
            reply = runtime.gateway.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=review_model,
                tools=None,
            )
            return reply.text

        agent.approver = SmartApprover(base=ConsoleApprover(console), ask_model=_ask)

    console.print(
        Panel.fit(
            f"[bold]{runtime.config.persona.name}[/] · OmniOperator CLI\n"
            f"会话 #{agent.session_id} · 模型 [cyan]{agent.model}[/]",
            border_style="cyan",
        )
    )
    console.print("[dim]输入 /help 查看命令；直接说话开始聊天。Web 工作台：uv run omni-web[/]")

    # 能力档位（D28）：None=按配置默认（默认 auto 自动判定）
    selected_mode: str | None = None

    while True:
        try:
            text = console.input("[bold green]你 >[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见。[/]")
            break
        if not text:
            continue

        if text == "/doctor":
            console.print(runtime.doctor())
            continue
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            arg = arg.strip()
            if cmd in ("/quit", "/exit"):
                break
            if cmd == "/help":
                console.print(HELP)
            elif cmd == "/new":
                console.print(f"[cyan]已开新会话 #{runtime.new_session()}[/]")
            elif cmd == "/list":
                table = Table("ID", "标题", "创建时间")
                for sid, title, created in runtime.store.list_sessions():
                    table.add_row(str(sid), title, created)
                console.print(table)
            elif cmd == "/use":
                if arg.isdigit() and runtime.use_session(int(arg)):
                    console.print(f"[cyan]已切换到会话 #{arg}[/]")
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
                    f"当前模型: [cyan]{agent.model}[/] · 工具集: "
                    f"{', '.join(runtime.registry.names())}")
            elif cmd == "/mode":
                if not arg:
                    current = selected_mode or runtime.config.mode_routing.default_mode
                    console.print(
                        f"当前档位: [cyan]{current}[/]（auto=按输入自动判定；"
                        "chat=纯聊天；retrieval=只读检索；agent=完整能力）"
                    )
                elif arg in MODE_CHOICES:
                    selected_mode = arg
                    console.print(f"[cyan]档位已设为 {arg}[/]")
                else:
                    console.print(
                        f"[yellow]用法: /mode <{'/'.join(MODE_CHOICES)}>[/]"
                    )
            else:
                console.print(f"[yellow]未知命令 {cmd}，/help 查看可用命令。[/]")
            continue

        try:
            answer = agent.chat(text, mode=selected_mode)
        except GatewayError as exc:
            console.print(f"[red]调用失败：{exc}[/]")
            console.print(
                "[yellow]提示：检查 .env 里的 API key 与 config/config.yaml 的模型名；"
                "或用 /switch 换一个可用模型。[/]"
            )
            continue
        console.print(
            Panel(answer, title=f"[bold cyan]{runtime.config.persona.name}", border_style="cyan")
        )
        runtime.spawn_review()  # 回合后异步记忆整理，不阻塞对话

if __name__ == "__main__":
    main()
