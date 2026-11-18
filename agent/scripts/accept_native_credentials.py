"""Explicit roundtrip of a fresh OS credential test item through production owned helpers.

Unknown native operations are never repeated. Retain cleanup.json and invoke
--cleanup explicitly after resolving any process or unlock problem.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from omni.adapters.diagnostics import Redactor
from omni.adapters.native_credentials import NativeCredentials
from omni.adapters.object_store import sync_directory
from omni.adapters.platform.runtime.permissions import private_path
from omni.application.ports import AsyncSecretPort
from omni.domain.contracts import BusinessError

FORMAT = "omni-native-credential-cleanup-1"


def identity(token: str) -> tuple[str, str]:
    if not isinstance(token, str) or len(token) != 32 or uuid.UUID(hex=token).hex != token:
        raise ValueError("Invalid owned test identity")
    return hashlib.sha256((FORMAT + token).encode()).hexdigest()[:20], "acceptance/" + token


async def accept(service: AsyncSecretPort, token: str, *, cleanup: bool = False) -> dict:
    _, reference = identity(token)
    result = {
        "status": "failed",
        "error_code": None,
        "cleanup_required": cleanup,
        "native_write_confirmed": False,
        "readback_verified": False,
        "item_absence_verified": False,
        "platform_acceptance_complete": False,
        "secret_values_included": False,
    }
    owns = cleanup
    try:
        if not cleanup:
            if await service.get(reference) is not None:
                raise BusinessError("credential_sample_item_exists", "Refusing to replace an existing test item")
            owns = True  # preserve this identity even when the set reply is lost
            result["native_write_confirmed"] = None
            value = secrets.token_urlsafe(32)
            await service.set(reference, value)
            result["native_write_confirmed"] = True
            actual = await service.get(reference)
            if actual is None or not secrets.compare_digest(actual, value):
                raise BusinessError("credential_sample_readback_mismatch", "Owned test readback did not match")
            result["readback_verified"] = True
        await service.delete(reference)
        if await service.get(reference) is not None:
            raise BusinessError("credential_sample_cleanup_unconfirmed", "Owned test item is still present")
        result["item_absence_verified"] = True
        owns = False
        result["status"] = "cleanup_passed" if cleanup else "sample_passed"
    except BusinessError as error:
        result["error_code"] = error.code
        result["status"] = "unknown" if owns else "failed"
    except Exception:  # noqa: BLE001 -- never export OS/provider exception text or secret-bearing results
        result["error_code"] = "credential_sample_failed"
        result["status"] = "unknown" if owns else "failed"
    finally:
        # No delete is attempted here after an unknown set/read/delete. Only an
        # explicit cleanup invocation can operate on the retained test identity.
        result["cleanup_required"] = owns
        try:
            reclaimed = await service.close(5)
        except Exception:  # noqa: BLE001 -- preserve actual cleanup failure without native error text
            reclaimed = False
        result["processes_reclaimed"] = reclaimed
        if not reclaimed:
            result.update(status="cleanup_pending", error_code="credential_cleanup_pending")
    return result


def cleanup_identity(path: Path) -> str:
    info = path.lstat()
    if path.is_symlink() or path.is_junction() or not path.is_file() or info.st_nlink != 1 or info.st_size > 4096:
        raise ValueError("Cleanup identity must be a bounded single-link regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if set(value) != {"format", "token", "platform"} or value["format"] != FORMAT or value["platform"] != sys.platform:
        raise ValueError("Cleanup identity does not belong to this native platform")
    identity(value["token"])
    return value["token"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument(
        "--write-owned-item", action="store_true", help="Explicitly create, read and remove a fresh native test item"
    )
    operation.add_argument("--cleanup", type=Path, help="Explicitly remove only the test item recorded in cleanup.json")
    parser.add_argument("--output", type=Path, required=True, help="New private evidence directory")
    args = parser.parse_args()
    if sys.platform not in {"win32", "darwin", "linux"}:
        parser.error("Native sample requires Windows, macOS or Linux")
    token = cleanup_identity(args.cleanup) if args.cleanup else uuid.uuid4().hex
    namespace, _ = identity(token)
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    private_path(args.output, directory=True)
    cleanup_path = args.output / "cleanup.json"
    with cleanup_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({"format": FORMAT, "token": token, "platform": sys.platform}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    private_path(cleanup_path)
    sync_directory(args.output)
    sync_directory(args.output.parent)
    result = asyncio.run(accept(NativeCredentials(namespace, Redactor()), token, cleanup=args.cleanup is not None))
    result.update(
        timestamp=datetime.now(timezone.utc).isoformat(),
        system=platform.system(),
        machine=platform.machine(),
        python=platform.python_version(),
    )
    result["scope"] = (
        "One explicitly authorized owned native credential sample; not complete platform or account acceptance"
    )
    output = args.output / "evidence.json"
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    private_path(output)
    print(json.dumps({key: result[key] for key in ("status", "error_code", "cleanup_required", "processes_reclaimed")}))
    return 0 if result["status"] in {"sample_passed", "cleanup_passed"} and result["processes_reclaimed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
