"""Explicit macOS acceptance against the owned Swift fixture, never the user's applications."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from omni.adapters.platform.desktop.backend import DesktopBackend
from omni.adapters.platform.desktop.macos_helper import BUNDLE_NAME, resolve
from omni.adapters.platform.runtime.permissions import private_path
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.domain.contracts import JSON
from omni.domain.desktop import validate_observation
from omni.domain.desktop_actions import POLICY
from omni.domain.foreground import POLICY as FOREGROUND_POLICY


async def state_until(output: Path, key: str, expected: object) -> None:
    async with asyncio.timeout(5):
        while True:
            path = output / "state.json"
            if path.exists() and json.loads(path.read_text(encoding="utf-8")).get(key) == expected:
                return
            await asyncio.sleep(0.05)


async def accept(build: Path, output: Path, mutate: bool, foreground_input: bool = False) -> dict:
    fixture = build / "OmniDesktopFixture.app/Contents/MacOS/OmniDesktopFixture"
    if not fixture.is_file() or resolve() is None:
        raise RuntimeError("Build the fixture and install the fixed helper before acceptance")
    owner = await OwnedProcess.spawn(
        [str(fixture), str(output)],
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    drains: list[asyncio.Task[bytes]] = []
    backend = DesktopBackend(capture_root=output / "staging", macos_transport="launchservices")
    try:
        stdout, stderr = owner.process.stdout, owner.process.stderr
        assert stdout is not None and stderr is not None
        drains = [asyncio.create_task(stream.read(65537)) for stream in (stdout, stderr)]
        async with asyncio.timeout(20):
            while not (output / "ready.json").exists():
                if owner.process.returncode is not None:
                    raise RuntimeError("Owned fixture exited")
                await asyncio.sleep(0.05)
        target = json.loads((output / "ready.json").read_text())
        verification = await backend.verify(target)
        proof: dict[str, JSON] = {"probe": await backend.probe(), "verify": verification}
        assert verification["matches"] is True, "Owned target identity did not match"
        report = validate_observation(await backend.observe(target), target)
        assert report["nodes"] and any(node["password"] for node in report["nodes"])
        assert all(not n["name"] and not n["text"] for n in report["nodes"] if n["password"])
        assert "OMNI-PRIVATE-PASSWORD-CANARY" not in json.dumps(report)
        proof["observation"] = {
            "nodes": len(report["nodes"]),
            "truncated": report["truncated"],
            "password_redacted": True,
        }
        captured = await backend.capture(target)
        (output / "owned-window.png").write_bytes(captured.content)
        proof["capture"] = {
            "sha256": captured.sha256,
            "width": captured.width,
            "height": captured.height,
            "method": captured.method,
        }
        proof["semantic_mutations"] = "not_requested"
        if mutate:

            async def guard():
                pass

            receipts = []
            for kind, text in (("set_text", "owned-v2-acceptance"), ("invoke", "")):
                report = validate_observation(await backend.observe(target), target)
                node = next(n for n in report["nodes"] if kind in n["actions"])
                result = await backend.act(
                    target,
                    {"kind": kind, "text": text, "node": node["reference"], "policy": POLICY},
                    before_dispatch=guard,
                )
                assert result["status"] in {"ok", "unknown"} and result["effect"] in {"applied", "unknown"}, result
                await state_until(output, "text" if kind == "set_text" else "clicks", text if kind == "set_text" else 1)
                receipts.append(result)
            proof["semantic_mutations"] = {"receipts": receipts, "independent_state_verified": True, "replayed": False}
        proof["foreground_input"] = "not_requested"
        if foreground_input:
            receipts = []

            async def guard():
                pass

            initial = "omni-owned-fixture"
            for kind, text, role, key, expected in [
                ("type_text", "-FG", "AXTextField", "text", initial + "-FG"),
                ("press_key", "Left", "AXTextField", "selection_start", len(initial) + 2),
                ("type_text", "_", "AXTextField", "text", initial + "-F_G"),
                ("click", "", "AXButton", "clicks", 1),
            ]:
                observed = validate_observation(await backend.observe(target), target)
                node = next(n for n in observed["nodes"] if n["role"] == role and kind in n["actions"])
                result = await backend.foreground(
                    target,
                    {
                        "kind": kind,
                        "text": text,
                        "node": node["reference"],
                        "policy": FOREGROUND_POLICY,
                        "takeover": True,
                        **{k: str(uuid.uuid4()) for k in ("grant_id", "run_id", "operation_id")},
                    },
                    before_dispatch=guard,
                )
                assert result["status"] in {"ok", "unknown"} and result["effect"] in {"applied", "unknown"}, result
                if kind in {"click", "press_key"}:
                    assert result["status"] == "unknown", "Posting cannot prove an application postcondition"
                # Independent fixture state is evidence for this test only. It never
                # rewrites the production receipt or authorizes a retry.
                await state_until(output, key, expected)
                receipts.append(result)
            proof["foreground_input"] = {"receipts": receipts, "independent_state_verified": True, "replayed": False}
        assert backend.health()["resident"] == 0
        return proof
    finally:
        (output / "stop").write_text("stop owned fixture")
        backend_clean = await backend.close()
        try:
            await asyncio.wait_for(owner.process.wait(), 5)
        finally:
            assert await owner.terminate(5), "Fixture process group cleanup incomplete"
            streams = await asyncio.gather(*drains)
            assert all(len(data) <= 65536 for data in streams), "Fixture output exceeded bound"
        assert backend_clean, "Native helper cleanup incomplete"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mutate-owned-fixture", action="store_true")
    parser.add_argument("--foreground-owned-fixture", action="store_true")
    args = parser.parse_args()
    if args.mutate_owned_fixture and args.foreground_owned_fixture:
        parser.error("Use independent fixture runs for semantic and foreground acceptance")
    if sys.platform != "darwin":
        parser.error("Run on the logged-in Mac mini; Windows/container results cannot prove macOS support")
    output = args.output.absolute()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    private_path(output, directory=True)
    manifest = args.build / (BUNDLE_NAME + ".build.json")
    evidence = {
        "format": "omni-macos-owned-acceptance-1",
        "status": "in_progress",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "build_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "scope": "Owned fixture AX/capture/explicit optional mutations; not full platform acceptance",
        "foreground_input_requested": args.foreground_owned_fixture,
    }
    try:
        evidence["results"] = asyncio.run(
            accept(args.build.absolute(), output, args.mutate_owned_fixture, args.foreground_owned_fixture)
        )
        evidence["status"] = "passed"
    except BaseException as error:
        evidence["status"] = "failed"
        evidence["error_type"] = type(error).__name__
        raise
    finally:
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "acceptance.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        for path in output.rglob("*"):
            private_path(path, directory=path.is_dir())
        print("Acceptance evidence: " + str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
