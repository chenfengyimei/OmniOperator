"""Explicit read-only Linux native sample; output contains no window identity or UI text."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from omni.adapters.platform.desktop import DesktopBackend
from omni.adapters.platform.runtime.permissions import private_path
from omni.domain.contracts import BusinessError
from omni.domain.desktop import target_fingerprint, validate_capabilities, validate_observation, validate_targets


async def accept(
    backend: DesktopBackend, fixture_pid: int | None, *, backend_kind: str = "atspi", capture_window: bool = False
) -> dict:
    """The caller explicitly selects a temporary sample PID; no automatic interaction or writes."""
    evidence = {
        "schema": 1,
        "scope": "Linux read-only owner-selected window sample, not complete platform acceptance",
        "platform_acceptance_complete": False,
        "status": "failed",
        "error_code": None,
        "native_input": False,
        "capture": False,
        "ui_content_included": False,
        "sample_requested": fixture_pid is not None,
    }
    try:
        async with asyncio.timeout(25):
            report = validate_capabilities(await backend.probe())
            if report["platform"] != "linux":
                raise BusinessError("linux_sample_platform_invalid", "Linux helper required")
            evidence["session_kind"] = report["session"]["kind"]
            evidence["capabilities"] = [
                {key: item[key] for key in ("id", "supported", "installed", "permission", "available", "error_code")}
                for item in report["capabilities"]
            ]
            if fixture_pid is not None:
                if type(fixture_pid) is not int or not 1 <= fixture_pid <= 4294967295:
                    raise BusinessError("linux_sample_pid_invalid", "A bounded sample PID is required")
                required = (0, 2) if capture_window else (0, 1)
                if backend_kind not in {"atspi", "x11"} or capture_window != (backend_kind == "x11"):
                    raise BusinessError(
                        "linux_sample_backend_invalid", "Use AT-SPI observation or explicit X11 capture"
                    )
                if not all(report["capabilities"][index]["available"] for index in required):
                    raise BusinessError("linux_sample_read_unavailable", "Read-only native capability unavailable")
                listing = validate_targets(await backend.targets(50), 50)
                selected = [
                    item for item in listing["items"] if item["pid"] == fixture_pid and item["backend"] == backend_kind
                ]
                if len(selected) != 1:
                    raise BusinessError("linux_sample_target_ambiguous", "Select one visible top-level sample window")
                target = selected[0]
                if not (await backend.verify(target))["matches"]:
                    raise BusinessError("desktop_target_changed", "Sample identity changed")
                if capture_window:
                    frame = await backend.capture(target)
                    frame.validate()
                    if frame.method != "x11_composite":
                        raise BusinessError("linux_sample_backend_invalid", "Expected X11 capture provenance")
                    evidence["capture"] = {
                        "sha256": frame.sha256,
                        "bytes": len(frame.content),
                        "width": frame.width,
                        "height": frame.height,
                        "method": frame.method,
                    }
                    observation = None
                else:
                    observation = validate_observation(await backend.observe(target), target)
                if not (await backend.verify(target))["matches"]:
                    raise BusinessError("desktop_target_changed", "Sample identity changed")
                evidence["sample"] = {
                    "target_sha256": target_fingerprint(target),
                    "before_after_identity_verified": True,
                }
                if observation is not None:
                    evidence["sample"].update(
                        nodes=len(observation["nodes"]),
                        password_nodes=sum(node["password"] for node in observation["nodes"]),
                        truncated=observation["truncated"],
                        coordinate_space=observation["coordinate_space"],
                    )
            evidence["status"] = "sample_passed" if fixture_pid is not None else "probe_completed"
    except BusinessError as error:
        evidence["error_code"] = error.code
    except TimeoutError:
        evidence["error_code"] = "desktop_timeout"
    except Exception:  # noqa: BLE001 -- native exception bodies may contain UI or local identity
        evidence["error_code"] = "linux_sample_failed"
    finally:
        try:
            cleanup = await backend.close(5)
        except Exception:  # noqa: BLE001 -- report cleanup failure without exporting local provider details
            cleanup = False
        evidence["cleanup_verified"] = cleanup
        if not cleanup:
            evidence.update(status="cleanup_pending", error_code="desktop_cleanup_pending")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-pid", type=int, help="PID of an owner-opened temporary single-window sample")
    parser.add_argument("--consent-read-ui", action="store_true", help="Explicitly read that sample's bounded UI")
    parser.add_argument("--native-backend", choices=("atspi", "x11"), default="atspi")
    parser.add_argument(
        "--capture-window",
        action="store_true",
        help="Explicitly capture selected X11 sample; evidence does not include pixels",
    )
    parser.add_argument("--output", type=Path, required=True, help="New private evidence directory")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run this native sample on Linux")
    if (args.fixture_pid is not None) != args.consent_read_ui:
        parser.error("--fixture-pid and --consent-read-ui must be provided together")
    if args.capture_window != (args.native_backend == "x11") or args.capture_window and args.fixture_pid is None:
        parser.error("X11 requires --capture-window and an explicitly selected fixture PID")
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    private_path(args.output, directory=True)
    result = asyncio.run(
        accept(
            DesktopBackend(capture_root=args.output / "staging"),
            args.fixture_pid,
            backend_kind=args.native_backend,
            capture_window=args.capture_window,
        )
    )
    result.update(
        timestamp=datetime.now(timezone.utc).isoformat(), system=platform.system(), machine=platform.machine()
    )
    path = args.output / "evidence.json"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    private_path(path, directory=False)
    print(
        json.dumps(
            {
                "status": result["status"],
                "error_code": result["error_code"],
                "cleanup_verified": result["cleanup_verified"],
            }
        )
    )
    return 0 if result["status"] in {"probe_completed", "sample_passed"} and result["cleanup_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
