"""HTTP composition and owner bootstrap; tokens never appear in diagnostic output."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import os
import secrets
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..adapters.http import HttpRuntime, create_app
from ..adapters.log_query import LogQuery
from omni.adapters.platform.runtime.permissions import private_path
from ..application.android import AndroidPort
from ..application.bilibili import BilibiliPort
from ..application.mcp import MCPTransportPort
from ..application.media import MediaProcessingPort
from ..application.ports import (
    DeliveryPort,
    DesktopBackendPort,
    DesktopControlPort,
    DesktopPortalPort,
    LocalVectorizer,
    ModelPort,
    ModelStreamingPort,
    Record,
    SecretStore,
    TerminalBackendPort,
    TokenCounter,
)
from ..application.qq import QQTransportPort
from .instance import open_instance
from .lifecycle import close_until_quiescent


def web_assets() -> Path:
    package = Path(__file__).resolve().parents[1]
    for path in (package / "web_assets", Path(__file__).resolve().parents[4] / "web"):
        if (path / "ui/v2/index.html").is_file():
            return path
    raise RuntimeError("V2 web assets are missing from this distribution")


def http_app(
    home: Path | None = None,
    *,
    model: ModelPort | None = None,
    model_streamer: ModelStreamingPort | None = None,
    token_counter: TokenCounter | None = None,
    vectorizer: LocalVectorizer | None = None,
    delivery_ports: dict[str, DeliveryPort] | None = None,
    qq_transport: QQTransportPort | None = None,
    bilibili_backend: BilibiliPort | None = None,
    media_processor: MediaProcessingPort | None = None,
    mcp_transport: MCPTransportPort | None = None,
    android_backend: AndroidPort | None = None,
    master_key_file: Path | None = None,
    allowed_origins: tuple[str, ...] = (),
    stderr_only: bool = False,
    process_diagnostics: bool = False,
    credential_backend: SecretStore | None = None,
    terminal_backend: TerminalBackendPort | None = None,
    desktop_backend: DesktopBackendPort | None = None,
    portal_backend: DesktopPortalPort | None = None,
    control_backend: DesktopControlPort | None = None,
    deployment: Record | None = None,
    shutdown: Callable[[], None] | None = None,
):
    async def factory() -> HttpRuntime:
        drain_deadline: float | None = None
        instance = await open_instance(
            home,
            model=model,
            model_streamer=model_streamer,
            token_counter=token_counter,
            vectorizer=vectorizer,
            delivery_ports=delivery_ports,
            qq_transport=qq_transport,
            bilibili_backend=bilibili_backend,
            media_processor=media_processor,
            mcp_transport=mcp_transport,
            android_backend=android_backend,
            master_key_file=master_key_file,
            stderr_only=stderr_only,
            process_diagnostics=process_diagnostics,
            credential_backend=credential_backend,
            terminal_backend=terminal_backend,
            desktop_backend=desktop_backend,
            portal_backend=portal_backend,
            control_backend=control_backend,
            deployment=deployment,
        )
        try:
            token_path = instance.paths.control / "owner.token"
            if token_path.is_symlink():
                raise OSError("Owner token cannot be a link")
            if not token_path.exists():
                token = secrets.token_urlsafe(32)
                digest = hashlib.sha256(token.encode()).hexdigest()
                expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat(timespec="microseconds")
                # Register before publishing. A crash may leave an unreachable token hash,
                # but cannot publish a credential that was not durably authorized.
                await instance.database.write(lambda u: u.issue_token(digest, expires))
                descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(token)
                    stream.flush()
                    os.fsync(stream.fileno())
                private_path(token_path)
            instance.logging.sink.redactor.register(token_path.read_text(encoding="utf-8"))

            async def authenticate(token: str) -> bool:
                if len(token) < 32 or len(token) > 200:
                    return False
                digest = hashlib.sha256(token.encode()).hexdigest()
                return await instance.database.read(lambda u: u.token_valid(digest))

            rotate_lock = asyncio.Lock()

            async def token_sessions() -> list[Record]:
                rows = await instance.database.read(lambda u: u.token_sessions(50))
                current = ""
                if token_path.exists() and not token_path.is_symlink():
                    current = hashlib.sha256(token_path.read_text(encoding="utf-8").strip().encode()).hexdigest()
                return [
                    {
                        "created_at": str(row["created_at"]),
                        "expires_at": str(row["expires_at"]),
                        "revoked_at": row["revoked_at"],
                        "current": row["token_hash"] == current,
                    }
                    for row in rows
                ]

            async def rotate_token() -> Record:
                async with rotate_lock:
                    new_token = secrets.token_urlsafe(32)
                    digest = hashlib.sha256(new_token.encode()).hexdigest()
                    expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat(
                        timespec="microseconds"
                    )
                    # Issue first so a crash can only leave an extra valid session,
                    # never a published token the database does not recognize.
                    await instance.database.write(lambda u: u.issue_token(digest, expires))

                    def publish() -> None:
                        if token_path.is_symlink():
                            raise OSError("Owner token cannot be a link")
                        staged = token_path.with_name(token_path.name + ".next")
                        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        try:
                            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                                stream.write(new_token)
                                stream.flush()
                                os.fsync(stream.fileno())
                            os.replace(staged, token_path)
                            private_path(token_path)
                        except BaseException:
                            try:
                                staged.unlink()
                            except OSError:
                                pass
                            raise

                    await asyncio.to_thread(publish)
                    await instance.database.write(lambda u: u.revoke_other_tokens(digest))
                    instance.logging.sink.redactor.register(new_token)
                    # Returned exactly once; never logged or persisted outside the control file.
                    return {"token": new_token, "expires_at": expires}

            async def logs(query: LogQuery) -> Record:
                return await instance.operations.logs(asdict(query))

            async def drain(value: Record) -> Record:
                # RuntimeIdentityFiles 尚未纳入 strict；request_drain 返回排空回执，用局部 Any 桥接其未类型化签名。
                identity_files: Any = instance.identity_files
                return await identity_files.request_drain(
                    value, dt.datetime.now(dt.timezone.utc).isoformat(), request_shutdown
                )

            def request_shutdown() -> None:
                nonlocal drain_deadline
                if drain_deadline is None:
                    drain_deadline = asyncio.get_running_loop().time() + 30
                instance.lifetime.stop_accepting()
                if shutdown is not None:
                    shutdown()

            async def close() -> Record:
                remaining = 30 if drain_deadline is None else max(0, drain_deadline - asyncio.get_running_loop().time())
                return await close_until_quiescent(instance.close, remaining)

            return HttpRuntime(
                qq=instance.qq,
                bilibili=instance.bilibili,
                media=instance.media,
                mcp=instance.mcp,
                android=instance.android,
                application=instance.application,
                authenticate=authenticate,
                describe=instance.health,
                close=close,
                logs=logs,
                assets=web_assets(),
                objects=instance.objects,
                images=instance.images,
                people=instance.people,
                configurations=instance.configurations,
                routing=instance.routing,
                memory_semantics=instance.memory_semantics,
                skills=instance.skills,
                documents=instance.documents,
                schedules=instance.schedules,
                deliveries=instance.deliveries,
                operations=instance.operations,
                credentials=instance.credentials,
                maintenance=instance.maintenance,
                memory_organization=instance.memory_organization,
                extensions=instance.extensions,
                action_reviews=instance.action_reviews,
                terminals=instance.terminals,
                terminal_reviews=instance.terminal_reviews,
                desktop=instance.desktop,
                desktop_portal=instance.desktop_portal,
                desktop_control=instance.desktop_control,
                desktop_actions=instance.desktop_actions,
                identity_proof=instance.identity_proof,
                drain=drain if shutdown is not None else None,
                token_sessions=token_sessions,
                rotate_token=rotate_token,
                local_owner_token=lambda: token_path.read_text(encoding="utf-8").strip(),
                model_port=instance.model_port,
            )
        except BaseException:
            await instance.close()
            raise

    return create_app(factory, allowed_origins=allowed_origins)
