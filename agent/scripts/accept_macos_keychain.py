"""Explicit Mac Keychain roundtrip for a fresh test item; preserves cleanup identity, never secrets.

Run with --write-owned-item --output <new-private-directory>.
A timed-out worker is not retried. Use --cleanup <cleanup.json> explicitly to remove its test item.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import secrets
import signal
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from omni.adapters.platform.runtime.permissions import private_path
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.adapters.platform.runtime.secrets import SystemSecrets

MAX_FRAME = 65536


def identity(token: str) -> tuple[str, str]:
    if len(token) != 32 or uuid.UUID(hex=token).hex != token:
        raise ValueError("Invalid owned test identity")
    return "macos-acceptance-" + token, "probe/" + token


def worker() -> int:
    # Only a generated test namespace is accepted, never arbitrary production references.
    data = json.loads(sys.stdin.buffer.read(1025))
    if not isinstance(data, dict) or set(data) != {"token", "mode"} or data["mode"] not in {"roundtrip", "cleanup"}:
        return 2
    namespace, reference = identity(data["token"])
    store = SystemSecrets(namespace)
    report = {"status": "failed", "cleanup_verified": False, "cleanup_required": False}
    owns_item = data["mode"] == "cleanup"
    try:
        if data["mode"] == "roundtrip":
            if store.get(reference) is not None:
                raise RuntimeError("Fresh test namespace unexpectedly exists")
            owns_item = True
            value = secrets.token_urlsafe(32)
            store.set(reference, value)
            if not secrets.compare_digest(store.get(reference) or "", value):
                raise RuntimeError("Owned test readback did not match")
            report["readback_verified"] = True
        report["status"] = "passed"
    except Exception as error:  # noqa: BLE001 -- native credential errors must not export secret-bearing messages
        report["error_type"] = type(error).__name__
    finally:
        if owns_item:
            try:
                store.delete(reference)
                report["cleanup_verified"] = store.get(reference) is None
            except Exception as error:  # noqa: BLE001 -- preserve uncertainty without native exception text
                report["cleanup_error_type"] = type(error).__name__
        report["cleanup_required"] = owns_item and not report["cleanup_verified"]
        if report["cleanup_required"]:
            report["status"] = "unknown"
    print(json.dumps(report), flush=True)
    return 0


async def invoke(token: str, mode: str, directory: Path, *, worker_status: dict | None = None) -> dict:
    owner = await OwnedProcess.spawn(
        [sys.executable, "-I", str(Path(__file__).resolve()), "--worker"],
        cwd=str(directory),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": os.defpath, "HOME": str(Path.home()), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if worker_status is not None:
        worker_status.update(pid=owner.process.pid, created=owner.created, reclaimed=False)
    drains: list[asyncio.Task[bytes]] = []
    try:
        stdin, stdout, stderr = owner.process.stdin, owner.process.stdout, owner.process.stderr
        assert stdin is not None and stdout is not None and stderr is not None
        drains = [asyncio.create_task(stream.read(MAX_FRAME + 1)) for stream in (stdout, stderr)]
        async with asyncio.timeout(30):
            stdin.write(json.dumps({"token": token, "mode": mode}).encode())
            await stdin.drain()
            stdin.close()
            out, err = await asyncio.gather(*drains)
            code = await owner.process.wait()
            if code != 0 or len(out) > MAX_FRAME or len(err) > MAX_FRAME:
                raise RuntimeError("Owned Keychain worker failed")
            report = json.loads(out)
            allowed = {
                "status",
                "cleanup_verified",
                "cleanup_required",
                "readback_verified",
                "error_type",
                "cleanup_error_type",
            }
            if (
                not isinstance(report, dict)
                or set(report) - allowed
                or not {"status", "cleanup_verified", "cleanup_required"} <= set(report)
                or report.get("status") not in {"passed", "failed", "unknown"}
                or any(type(report.get(key)) is not bool for key in ("cleanup_verified", "cleanup_required"))
            ):
                raise RuntimeError("Invalid Keychain worker receipt")
            if report["status"] == "passed" and (
                not report["cleanup_verified"]
                or report["cleanup_required"]
                or (mode == "roundtrip" and report.get("readback_verified") is not True)
            ):
                raise RuntimeError("Unverified Keychain worker success")
            if report["cleanup_required"] and (report["cleanup_verified"] or report["status"] != "unknown"):
                raise RuntimeError("Inconsistent Keychain cleanup receipt")
            # Raw stderr is never exported, including native exception bodies.
            safe = {key: report[key] for key in ("status", "cleanup_verified", "cleanup_required")}
            safe["readback_verified"] = report.get("readback_verified") is True
            safe["stderr_bytes"] = len(err)
            if "error_type" in report:
                safe["error_code"] = "keychain_probe_failed"
            if "cleanup_error_type" in report:
                safe["cleanup_error_code"] = "keychain_cleanup_unverified"
            return safe
    finally:
        reclaimed = await owner.terminate(5)
        if worker_status is not None:
            worker_status["reclaimed"] = reclaimed
        for task in drains:
            if not task.done():
                task.cancel()
        await asyncio.gather(*drains, return_exceptions=True)
        if not reclaimed:
            raise RuntimeError("Owned Keychain worker cleanup remains pending")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-owned-item", action="store_true")
    parser.add_argument("--cleanup", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "darwin" or getattr(os, "getuid", lambda: 0)() == 0:
        parser.error("Requires an owner-approved Mac Keychain acceptance run")
    if args.worker:
        # POSIX default SIGALRM also bounds a native call after its parent crashes.
        # An interrupted external result remains unknown; the private cleanup receipt survives.
        signal.alarm(35)
        try:
            return worker()
        except Exception:  # noqa: BLE001 -- invalid worker input returns only a static failure
            return 2
    if bool(args.write_owned_item) == bool(args.cleanup) or args.output is None:
        parser.error("Choose --write-owned-item or --cleanup, and provide a new --output directory")
    output = args.output.absolute()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    private_path(output, directory=True)
    token = uuid.uuid4().hex
    if args.cleanup:
        previous = json.loads(args.cleanup.read_text(encoding="utf-8"))
        if previous.get("format") != "omni-owned-keychain-cleanup-1":
            parser.error("Not an owned Keychain cleanup receipt")
        token = previous["token"]
    identity(token)
    cleanup = {"format": "omni-owned-keychain-cleanup-1", "token": token, "needs_cleanup": True}
    cleanup_path = output / "cleanup.json"
    cleanup_path.write_text(json.dumps(cleanup, indent=2) + "\n", encoding="utf-8")
    private_path(cleanup_path)
    source = Path(__file__).resolve().parents[1] / "src/omni/adapters/platform/runtime/secrets.py"
    proof = {
        "format": "omni-macos-keychain-acceptance-1",
        "status": "unknown",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "scope": "Fresh owned test namespace only",
        "mode": "cleanup" if args.cleanup else "roundtrip",
        "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__), source)},
    }
    try:
        worker_status: dict = {}
        proof["worker"] = worker_status
        result = asyncio.run(invoke(token, proof["mode"], output, worker_status=worker_status))
        proof["result"] = result
        proof["status"] = result["status"]
        cleanup["needs_cleanup"] = result["cleanup_required"]
    except Exception as error:  # noqa: BLE001 -- retain recovery metadata when a native worker fails or times out
        proof["error_type"] = type(error).__name__
    finally:
        proof["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "acceptance.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        cleanup_path.write_text(json.dumps(cleanup, indent=2) + "\n", encoding="utf-8")
        for path in output.iterdir():
            private_path(path)
    print(json.dumps({"status": proof["status"], "cleanup_required": cleanup["needs_cleanup"], "output": str(output)}))
    return int(proof["status"] != "passed" or cleanup["needs_cleanup"])


if __name__ == "__main__":
    raise SystemExit(main())
