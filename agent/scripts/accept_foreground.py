"""Explicit interactive acceptance: click the owned test edit when it appears, then leave input idle.

Run: uv run --no-sync python agent/scripts/accept_foreground.py
This is outside the default offline suite because it requires real foreground consent.
"""

import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path

from omni.adapters.platform.desktop.backend import DesktopBackend
from omni.domain.foreground import POLICY


def accept_foreground(tmp_path):
    if sys.platform != "win32":

        async def unavailable():
            backend = DesktopBackend()

            async def guard():
                pass

            result = await backend.foreground({}, {}, before_dispatch=guard)
            assert result["status"] == "denied" and result["effect"] == "none"
            assert await backend.close()

        asyncio.run(unavailable())
        return
    script = Path(__file__).with_name("foreground_fixture.py")
    child = subprocess.Popen(
        [sys.executable, "-P", str(script), str(tmp_path), "interactive"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready.json").exists():
            assert child.poll() is None, "Owned foreground fixture exited"
            assert time.monotonic() < deadline, "Owned foreground fixture did not start"
            time.sleep(0.01)
        target = json.loads((tmp_path / "ready.json").read_text(encoding="utf-8"))

        async def scenario():
            backend = DesktopBackend()
            receipts = []

            async def guard():
                pass

            async def state_until(predicate):
                async with asyncio.timeout(20):
                    while True:
                        if (tmp_path / "state.json").exists():
                            value = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
                            if predicate(value):
                                return value
                        await asyncio.sleep(0.01)

            async def execute(kind, text, role):
                report = await backend.observe(target)
                node = next(n for n in report["nodes"] if n["role"] == role and kind in n["actions"])
                action = {
                    "kind": kind,
                    "text": text,
                    "node": node["reference"],
                    "policy": POLICY,
                    "takeover": True,
                    **{key: str(uuid.uuid4()) for key in ("grant_id", "run_id", "operation_id")},
                }
                (tmp_path / "focus").write_text(str(uuid.uuid4()))
                await state_until(lambda value: value["focus"])
                result = await backend.foreground(target, action, before_dispatch=guard)
                receipts.append(result)
                assert result["status"] in {"ok", "unknown"} and result["effect"] in {"applied", "unknown"}, result
                assert backend.health()["resident"] == 0
                return result

            try:
                await execute("type_text", "ONLY-OWNED-TEXT", "ControlType.Edit")
                await state_until(lambda value: value["text"] == "ONLY-OWNED-TEXT")
                result = await execute("press_key", "Home", "ControlType.Edit")
                assert result["effect"] == "unknown"
                await execute("type_text", "PREFIX-", "ControlType.Edit")
                await state_until(lambda value: value["text"] == "PREFIX-ONLY-OWNED-TEXT")
                result = await execute("click", "", "ControlType.Button")
                assert result["effect"] == "unknown"
                final = await state_until(lambda value: value["clicks"] == 1)
                (tmp_path / "native-proof.json").write_text(
                    json.dumps({"receipts": receipts, "state": final}), encoding="utf-8"
                )
            finally:
                assert await backend.close()

        asyncio.run(scenario())
    finally:
        (tmp_path / "stop").write_text("stop owned fixture")
        try:
            stdout, stderr = child.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            stdout, stderr = child.communicate(timeout=5)
            raise AssertionError("Owned foreground fixture did not stop") from None
    assert child.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert stdout == stderr == b""


if __name__ == "__main__":
    from datetime import datetime, timezone

    if sys.platform != "win32":
        raise SystemExit("Interactive Windows acceptance requires a logged-in Windows test machine.")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    directory = options.output or root / "temp/build" / (
        "foreground-acceptance-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    directory = directory.absolute()
    directory.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__), Path(__file__).with_name("foreground_fixture.py")]
    sources.extend((root / "agent/src/omni/adapters/workers").glob("windows_*"))
    evidence = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "status": "in_progress",
        "sources": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "scope": "Owned temporary window only; no real application or account acceptance",
    }
    try:
        print("Click the edit inside the Omni owned foreground fixture, then leave input idle.", flush=True)
        accept_foreground(directory)
        evidence["status"] = "passed"
    except BaseException as error:
        evidence["status"] = "failed"
        evidence["error_type"] = type(error).__name__
        raise
    finally:
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        (directory / "acceptance.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print("Acceptance evidence: " + str(directory), flush=True)
