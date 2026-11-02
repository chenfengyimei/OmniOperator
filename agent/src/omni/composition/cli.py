"""V2 development entrypoint. Online commands use the authenticated HTTP facade."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from ..adapters.persistence.maintenance import backup, inspect_database, restore
from ..adapters.platform.paths import InstanceLock, InstancePaths
from ..domain.contracts import BusinessError
from .http import http_app
from .instance import open_instance


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="python -m omni.composition.cli", description="OmniOperator V2 development runtime"
    )
    result.add_argument("--home", type=Path, help="Fresh, independent V2 instance directory")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create the empty V2 schema without calling a model")
    commands.add_parser("doctor", help="Read-only, exclusive database diagnostics")
    commands.add_parser("auth", help="Print the local owner token (keep it private)")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--external-url", help="Explicit trusted HTTPS origin when binding remotely")
    serve.add_argument("--master-key-file", type=Path)
    serve.add_argument("--stdout-logs", action="store_true")
    snapshot = commands.add_parser("backup")
    snapshot.add_argument("destination", type=Path)
    recover = commands.add_parser("restore")
    recover.add_argument("source", type=Path)
    recover.add_argument("destination", type=Path)
    query = commands.add_parser("request", help="Authenticated request to an already running local instance")
    query.add_argument("path")
    query.add_argument("--url", default="http://127.0.0.1:8766")
    return result


def main() -> int:
    args = parser().parse_args()
    paths = InstancePaths.resolve(args.home)
    try:
        if args.command == "serve":
            import uvicorn

            origins = ()
            if args.host not in {"127.0.0.1", "::1", "localhost"}:
                url = urlsplit(args.external_url or "")
                if (
                    url.scheme != "https"
                    or not url.hostname
                    or url.path not in {"", "/"}
                    or url.query
                    or url.fragment
                    or url.username
                ):
                    raise BusinessError(
                        "https_origin_required", "Remote binding requires an explicit trusted HTTPS origin"
                    )
                origins = (args.external_url.rstrip("/"),)
            uvicorn.run(
                http_app(
                    paths.home,
                    master_key_file=args.master_key_file,
                    allowed_origins=origins,
                    stderr_only=args.stdout_logs,
                ),
                host=args.host,
                port=args.port,
                workers=1,
                access_log=False,
            )
        elif args.command == "init":

            async def initialize():
                instance = await open_instance(paths.home)
                try:
                    return {"initialized": True, "home": str(paths.home), "api_version": 1}
                finally:
                    await instance.close()

            print(json.dumps(asyncio.run(initialize())))
        elif args.command == "doctor":
            lock = InstanceLock(paths)
            lock.acquire()
            try:
                print(json.dumps(inspect_database(paths.database)))
            finally:
                lock.close()
        elif args.command == "backup":
            print(json.dumps(backup(paths, args.destination)))
        elif args.command == "restore":
            print(json.dumps(restore(args.source, args.destination)))
        elif args.command == "auth":
            print((paths.control / "owner.token").read_text(encoding="utf-8"))
        elif args.command == "request":
            import httpx

            target = urlsplit(args.url)
            if (
                target.scheme != "http"
                or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                or target.username
                or target.query
                or target.fragment
                or not args.path.startswith("/api/v1/")
            ):
                raise BusinessError("invalid_local_endpoint", "CLI requests require a local API endpoint")
            token = (paths.control / "owner.token").read_text().strip()
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
                response = client.get(args.url.rstrip("/") + args.path, headers={"Authorization": "Bearer " + token})
                response.raise_for_status()
                print(response.text)
        return 0
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
