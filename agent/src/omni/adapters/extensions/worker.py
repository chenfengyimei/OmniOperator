"""Static extension handshake and trusted Python invocation in an owned child; never opens the main database."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import inspect
import io
import json
import os
import re
import sys
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import BinaryIO

from jsonschema import Draft202012Validator

from ...domain.contracts import ActionResult, BusinessError, Effect, Outcome
from ...domain.extensions import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_TOTAL_BYTES,
    MAX_ARTIFACTS,
    PROTOCOL,
    CapturedPackage,
    artifact_types,
    catalog,
)
from ..workers.protocol import MAX_FRAME, VERSION, action, decode, encode_response, request
from .packages import relative_path, verify_snapshot

HELLO_FIELDS = frozenset({"version", "id", "method", "protocol", "package_sha256", "catalog_sha256"})
ARTIFACTS_ENV = "OMNI_EXTENSION_ARTIFACTS"


def _artifacts(root: Path | None, value: object, allowed: tuple[str, ...]) -> list[dict]:
    """Compute size/hash inside the child; the host revalidates both before publishing."""
    if value is None:
        return []
    if not isinstance(value, list) or not value or len(value) > MAX_ARTIFACTS:
        raise ValueError
    permitted = set(artifact_types(list(allowed)))
    result = []
    total = 0
    seen = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "media_type"}:
            raise ValueError
        name, media_type = item["name"], item["media_type"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", name):
            raise ValueError
        if name in seen or media_type not in permitted or root is None:
            raise ValueError
        seen.add(name)
        path = (root / name).resolve()
        if path.parent != root.resolve() or path.is_symlink() or not path.is_file():
            raise ValueError
        size = path.stat().st_size
        total += size
        if not 0 < size <= MAX_ARTIFACT_BYTES or total > MAX_ARTIFACT_TOTAL_BYTES:
            raise ValueError
        result.append(
            {
                "name": name,
                "media_type": media_type,
                "bytes": size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return result


def _hello(identity: str, method: str, package_sha256: str, catalog_sha256: str) -> bytes:
    return (
        json.dumps(
            {
                "version": VERSION,
                "id": identity,
                "method": method,
                "protocol": PROTOCOL,
                "package_sha256": package_sha256,
                "catalog_sha256": catalog_sha256,
            },
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


async def handshake(process: asyncio.subprocess.Process, package_sha256: str, catalog_sha256: str) -> None:
    """A static challenge only: child code is not imported until a separately authorized invoke arrives."""
    try:
        if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in (package_sha256, catalog_sha256)):
            raise ValueError
        if process.stdin is None or process.stdout is None:
            raise ValueError
        identity = str(uuid.uuid4())
        process.stdin.write(_hello(identity, "initialize", package_sha256, catalog_sha256))
        await process.stdin.drain()
        response = decode(await process.stdout.readline())
        if (
            set(response) != HELLO_FIELDS
            or response["id"] != identity
            or response["method"] != "ready"
            or response["protocol"] != PROTOCOL
            or response["package_sha256"] != package_sha256
            or response["catalog_sha256"] != catalog_sha256
        ):
            raise ValueError
    except (ValueError, TypeError, OSError) as exc:
        raise BusinessError(
            "extension_handshake_failed", "Static extension handshake did not match the captured revision", status=503
        ) from exc


def _load_entry(root: Path, package: CapturedPackage) -> ModuleType:
    """Execute the already fingerprinted entry bytes rather than reopening a mutable entry path."""
    entry = relative_path(package.manifest["entry"])
    path = root / entry
    namespace = "_omni_extension_" + package.package_sha256
    container = ModuleType(namespace)
    container.__path__ = [str(root)]
    sys.modules[namespace] = container
    parts = entry[:-3].split("/")
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    name = namespace + ("." + ".".join(parts) if parts else "")
    parent = name.rpartition(".")[0]
    if parent and parent not in sys.modules:
        importlib.import_module(parent)
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(path.parent)] if is_package else None
    )
    if spec is None:
        raise ValueError
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        payload = archive.read(entry)
    exec(compile(payload, str(path), "exec"), module.__dict__)
    if not callable(getattr(module, "invoke", None)):
        raise ValueError
    return module


async def _read() -> bytes:
    line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME + 1)
    if not line or len(line) > MAX_FRAME or not line.endswith(b"\n"):
        raise ValueError
    return line


def _write(stream: BinaryIO, frame: bytes) -> None:
    stream.write(frame)
    stream.flush()


async def serve(location: str, package_sha256: str, catalog_sha256: str) -> None:
    root = Path(location)
    raw_artifacts = os.environ.get(ARTIFACTS_ENV, "")
    artifact_root: Path | None = None
    if raw_artifacts:
        candidate = Path(raw_artifacts)
        if not candidate.is_absolute() or candidate.is_symlink():
            raise ValueError
        artifact_root = candidate.resolve()
    initial = decode(await _read())
    if (
        set(initial) != HELLO_FIELDS
        or initial["method"] != "initialize"
        or initial["protocol"] != PROTOCOL
        or initial["package_sha256"] != package_sha256
        or initial["catalog_sha256"] != catalog_sha256
    ):
        raise ValueError
    captured = verify_snapshot(root, package_sha256, catalog_sha256)
    tools = {tool["name"]: tool for tool in catalog(captured.manifest)}
    output = sys.stdout.buffer
    _write(output, _hello(initial["id"], "ready", package_sha256, catalog_sha256))
    # Only this child redirects stdout; protocol writes retain the original descriptor.
    # Late/background plugin prints stay on stderr instead of corrupting future frames.
    sys.stdout = sys.stderr
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    module: ModuleType | None = None
    while True:
        try:
            invocation = request(await _read())
        except ValueError:
            return
        tool = tools.get(invocation["method"])
        if tool is None or invocation["writes"] is not True:
            _write(
                output,
                encode_response(
                    invocation["id"],
                    ActionResult(
                        Outcome.ERROR,
                        "The captured catalog does not authorize this capability",
                        error_code="unsupported_capability",
                    ),
                ),
            )
            continue
        if not Draft202012Validator(tool["parameters"]).is_valid(invocation["arguments"]):
            _write(
                output,
                encode_response(
                    invocation["id"],
                    ActionResult(
                        Outcome.ERROR,
                        "Arguments do not match the captured tool schema",
                        error_code="invalid_tool_arguments",
                    ),
                ),
            )
            continue
        try:
            captured = verify_snapshot(root, package_sha256, catalog_sha256)
            if module is None:
                module = _load_entry(root, captured)
            invoke = getattr(module, "invoke")
            raw = invoke(tool["local_name"], invocation["arguments"], invocation["context"])
            if inspect.isawaitable(raw):
                raw = await raw
            result = action(raw, writes=True)
            if result.artifacts:
                raise ValueError
            descriptors = _artifacts(artifact_root, result.data.get("artifacts"), tuple(tool.get("artifacts", [])))
            if descriptors:
                result = replace(
                    result,
                    data={key: value for key, value in result.data.items() if key != "artifacts"}
                    | {"artifacts": descriptors},
                )
        except BaseException:  # noqa: BLE001 -- plugin SystemExit and import failures cannot erase dispatched effects
            # Import or call may already have changed the outside world. Never
            # turn an entry exception, malformed receipt or artifact into retryable failure.
            _write(
                output,
                encode_response(
                    invocation["id"],
                    ActionResult(
                        Outcome.UNKNOWN,
                        "Extension outcome requires owner verification",
                        Effect.UNKNOWN,
                        error_code="extension_outcome_unknown",
                    ),
                ),
            )
            return
        _write(output, encode_response(invocation["id"], result))


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4:
            raise ValueError
        asyncio.run(serve(*sys.argv[1:]))
    except BaseException:  # noqa: BLE001 -- the child must not print untrusted traceback or exception payloads
        # Native and plugin exception bodies do not become child stderr/diagnostic payloads.
        raise SystemExit(1) from None
