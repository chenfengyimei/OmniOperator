"""V2 command host and instance bootstrap."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import cast

from ..adapters.platform.runtime.paths import InstancePaths
from ..domain.contracts import BusinessError
from ..domain.omniweave import OMNIWEAVE_MODES, OmniWeaveMode
from .commands import CommandPlugin, register_commands
from .commands.deployment import EXCHANGE, RELEASE, SERVICE
from .commands.installation import INSTALLATION
from .commands.installation import installation_report as installation_report
from .commands.installation import seal_installation as seal_installation
from .commands.maintenance import BACKUP, INDEXES, OBJECTS, RESTORE, VECTORS
from .commands.operations import DIAGNOSTICS, DOCTOR, local_origin
from .commands.operations import diagnostic_download as diagnostic_download
from .commands.retention import RETENTION_COMMANDS
from .instance import open_instance
from .serving import serve_instance


def serve_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", type=int, default=8766)
    command.add_argument("--external-url", help="Explicit trusted HTTPS origin when binding remotely")
    command.add_argument("--master-key-file", type=Path)
    command.add_argument("--stdout-logs", action="store_true")
    command.add_argument("--omniweave-mode", choices=OMNIWEAVE_MODES, default="off", help="Owner Web turn observation mode")
    command.add_argument(
        "--open-browser", action="store_true", help="Open the local workbench after this boot is ready"
    )


def auth_arguments(auth: argparse.ArgumentParser) -> None:
    auth.add_argument("--rotate", action="store_true", help="Rotate via the running instance and print the new token")
    auth.add_argument("--url", default="http://127.0.0.1:8766")


def request_arguments(query: argparse.ArgumentParser) -> None:
    query.add_argument("path")
    query.add_argument("--url", default="http://127.0.0.1:8766")


def run_init(_args: argparse.Namespace, paths: InstancePaths) -> int:
    async def initialize():
        instance = await open_instance(paths.home)
        try:
            return {"initialized": True, "home": str(paths.home), **instance.identity}
        finally:
            await instance.close()

    print(json.dumps(asyncio.run(initialize())))
    return 0


def run_serve(args: argparse.Namespace, paths: InstancePaths) -> int:
    from omni.adapters.platform.runtime.workbench import WorkbenchBrowser

    browser = WorkbenchBrowser(paths.home, args.host, args.port) if args.open_browser else nullcontext()
    with browser:
        serve_instance(
            paths.home,
            host=args.host,
            port=args.port,
            external_url=args.external_url,
            master_key_file=args.master_key_file,
            stdout_logs=args.stdout_logs,
            omniweave_mode=cast(OmniWeaveMode, args.omniweave_mode),
        )
    return 0


def run_auth(args: argparse.Namespace, paths: InstancePaths) -> int:
    token_path = paths.control / "owner.token"
    if not token_path.is_file():
        # 令牌由运行时首次启动时创建；未启动就取令牌要说清下一步，而不是抛 filesystem_error。
        raise BusinessError(
            "owner_token_missing",
            "The owner token is created by the first serve; run `serve` once, then retry `auth`",
            status=404,
        )
    if not args.rotate:
        print(token_path.read_text(encoding="utf-8"))
    else:
        import httpx

        origin = local_origin(args.url)
        token = token_path.read_text(encoding="utf-8").strip()
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
            response = client.post(origin + "/api/v1/owner-token/rotate", headers={"Authorization": "Bearer " + token})
            response.raise_for_status()
            print(response.json()["token"])
    return 0


def run_request(args: argparse.Namespace, paths: InstancePaths) -> int:
    import httpx

    origin = local_origin(args.url)
    if not args.path.startswith("/api/v1/"):
        raise BusinessError("invalid_local_endpoint", "CLI requests require a local API endpoint")
    token = (paths.control / "owner.token").read_text().strip()
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
        response = client.get(origin + args.path, headers={"Authorization": "Bearer " + token})
        response.raise_for_status()
        print(response.text)
    return 0


COMMANDS = register_commands(
    CommandPlugin("init", run_init, help="Create the empty V2 schema without calling a model"),
    DOCTOR,
    INSTALLATION,
    VECTORS,
    EXCHANGE,
    RELEASE,
    SERVICE,
    *RETENTION_COMMANDS,
    INDEXES,
    CommandPlugin(
        "auth", run_auth, help="Print or rotate the local owner token (keep it private)", configure=auth_arguments
    ),
    DIAGNOSTICS,
    CommandPlugin("serve", run_serve, configure=serve_arguments),
    BACKUP,
    RESTORE,
    CommandPlugin(
        "request",
        run_request,
        help="Authenticated request to an already running local instance",
        configure=request_arguments,
    ),
    OBJECTS,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="python -m omni.composition.cli", description="OmniOperator V2 development runtime"
    )
    result.add_argument("--home", type=Path, help="Fresh, independent V2 instance directory")
    commands = result.add_subparsers(dest="command", required=True)
    for plugin in COMMANDS.values():
        if plugin.help is None:
            command = commands.add_parser(plugin.name)
        else:
            command = commands.add_parser(plugin.name, help=plugin.help)
        if plugin.configure is not None:
            plugin.configure(command)
    return result


def main(argv: list[str] | None = None) -> int:
    return _run(parser().parse_args(argv))


def web_main(argv: list[str] | None = None) -> int:
    command = argparse.ArgumentParser(prog="omni-web-v2", description="OmniOperator V2 workbench")
    command.add_argument("--home", type=Path, help="Independent V2 instance directory")
    serve_arguments(command)
    command.set_defaults(command="serve")
    return _run(command.parse_args(argv))


def _run(args: argparse.Namespace) -> int:
    paths = InstancePaths.resolve(args.home)
    try:
        return COMMANDS[cast(str, args.command)].run(args, paths)
    except (BusinessError, OSError) as exc:
        print(
            json.dumps(
                {
                    "error": exc.code if isinstance(exc, BusinessError) else "filesystem_error",
                    "message": exc.message if isinstance(exc, BusinessError) else "Filesystem operation failed",
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
