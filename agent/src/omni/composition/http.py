"""HTTP composition and owner bootstrap; tokens never appear in diagnostic output."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import secrets
from pathlib import Path

from ..adapters.http import HttpRuntime, create_app
from ..adapters.platform.permissions import private_path
from ..application.ports import ModelPort
from .instance import open_instance


def web_assets() -> Path:
    package = Path(__file__).resolve().parents[1]
    for path in (package / "web_assets", Path(__file__).resolve().parents[4] / "web"):
        if (path / "v2/index.html").is_file():
            return path
    raise RuntimeError("V2 web assets are missing from this distribution")


def http_app(
    home: Path | None = None,
    *,
    model: ModelPort | None = None,
    master_key_file: Path | None = None,
    allowed_origins: tuple[str, ...] = (),
    stderr_only: bool = False,
):
    async def factory() -> HttpRuntime:
        instance = await open_instance(home, model=model, master_key_file=master_key_file, stderr_only=stderr_only)
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
                await instance._database.write(lambda u: u.issue_token(digest, expires))
                descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(token)
                    stream.flush()
                    os.fsync(stream.fileno())
                private_path(token_path)
            instance._logging.sink.redactor.register(token_path.read_text(encoding="utf-8"))

            async def authenticate(token: str) -> bool:
                if len(token) < 32 or len(token) > 200:
                    return False
                digest = hashlib.sha256(token.encode()).hexdigest()
                return await instance._database.read(lambda u: u.token_valid(digest))

            async def logs(limit: int) -> list[dict]:
                def read():
                    path = instance.paths.logs / "runtime.jsonl"
                    if not path.exists():
                        return []
                    if path.is_symlink():
                        raise OSError("Log path is a link")
                    with path.open("rb") as stream:
                        stream.seek(0, 2)
                        size = stream.tell()
                        stream.seek(max(0, size - 1_048_576))
                        if size > 1_048_576:
                            stream.readline()
                        lines = stream.read().decode("utf-8", errors="replace").splitlines()[-limit:]
                    return [json.loads(line) for line in lines if line.strip()]

                return await asyncio.to_thread(read)

            namespace = hashlib.sha256(str(instance.paths.home).encode()).hexdigest()[:20]
            from ..adapters.platform.secrets import FileVault, SystemSecrets

            vault = (
                FileVault(instance.paths.home / "private/vault", master_key_file)
                if master_key_file
                else SystemSecrets(namespace)
            )

            async def credential(reference: str, value: str | None) -> None:
                if value is None:
                    await asyncio.to_thread(vault.delete, reference)
                else:
                    instance._logging.sink.redactor.register(value)
                    await asyncio.to_thread(vault.set, reference, value)

            return HttpRuntime(
                instance.application, authenticate, instance.health, instance.close, logs, credential, web_assets()
            )
        except BaseException:
            await instance.close()
            raise

    return create_app(factory, allowed_origins=allowed_origins)
