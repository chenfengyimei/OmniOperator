"""Run WorkWeb and an explicitly selected V2 development instance on one origin."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from omni.composition.http import http_app


def mount_workweb(app: FastAPI) -> None:
    """Attach the new workbench to this development app."""
    app.mount(
        "/workweb",
        StaticFiles(directory=Path(__file__).with_name("ui"), html=True),
        name="workweb",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="WorkWeb 本地联调")
    parser.add_argument(
        "--home", type=Path, required=True, help="独立的 OmniOperator 实例目录"
    )
    parser.add_argument("--port", type=int, default=8774)
    args = parser.parse_args()
    app = http_app(args.home)
    mount_workweb(app)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        loop="omni.adapters.platform.runtime.event_loop:create_event_loop",
        access_log=False,
    )


if __name__ == "__main__":
    main()
