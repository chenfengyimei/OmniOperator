"""Compose the single-worker HTTP process for manual and pinned service startup."""

from __future__ import annotations

import signal
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import urlsplit

from ..domain.contracts import BusinessError
from .http import http_app


def serve_instance(
    home: Path,
    *,
    host: str,
    port: int,
    external_url: str | None,
    master_key_file: Path | None,
    stdout_logs: bool,
    deployment: dict[str, Any] | None = None,
) -> None:
    import uvicorn

    origins = ()
    if host not in {"127.0.0.1", "::1", "localhost"}:
        try:
            url = urlsplit(external_url or "")
            invalid = (
                url.scheme != "https"
                or not url.hostname
                or url.path not in {"", "/"}
                or url.query
                or url.fragment
                or url.username is not None
                or url.password is not None
            )
            url.port
        except ValueError as exc:
            raise BusinessError("https_origin_required", "Remote binding requires a trusted HTTPS origin") from exc
        if invalid:
            raise BusinessError("https_origin_required", "Remote binding requires a trusted HTTPS origin")
        origins = ((external_url or "").rstrip("/"),)

    def request_shutdown() -> None:
        server.should_exit = True

    configuration = uvicorn.Config(
        http_app(
            home,
            master_key_file=master_key_file,
            allowed_origins=origins,
            stderr_only=stdout_logs,
            process_diagnostics=True,
            deployment=deployment,
            shutdown=request_shutdown,
        ),
        loop="omni.adapters.platform.runtime.event_loop:create_event_loop",
        host=host,
        port=port,
        workers=1,
        log_config=None,
        access_log=False,
        limit_concurrency=64,
        timeout_graceful_shutdown=30,
    )
    server = uvicorn.Server(configuration)

    def handle_termination(_signum: int, _frame: FrameType | None) -> None:
        server.should_exit = True

    # uvicorn restores the previous handler and re-raises the captured signal
    # after a graceful shutdown, which would exit 143/130. Keep our own handler
    # installed so a stopped service exits 0 and the container check can prove
    # a complete shutdown.
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signum, handle_termination)
        except (ValueError, OSError):
            pass
    server.run()
