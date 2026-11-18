"""Explicit Linux window-share sample; pixels require --capture-window, and input is never authorized."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from omni.adapters.platform.runtime.permissions import private_path
from omni.adapters.platform.desktop.portal import PortalBackend
from omni.domain.contracts import BusinessError
from omni.domain.desktop_portal import CHOICE_SECONDS, TERMINAL


async def accept(backend: PortalBackend, hold_seconds: float, *, capture_window: bool = False) -> dict:
    scope = {key: str(uuid.uuid4()) for key in ("agent_id", "view_id", "boot_id", "grant_id")}
    result = {
        "schema": 1,
        "scope": "Explicit native window frame sample"
        if capture_window
        else "Explicit native window-share consent sample only",
        "status": "failed",
        "error_code": None,
        "platform_acceptance_complete": False,
        "business_audit_tested": False,
        "pixels_read": False,
        "pipewire_fd_opened": False,
        "input_authorized": False,
        "phases": [],
    }

    async def guard():
        return None

    try:
        async with asyncio.timeout(CHOICE_SECONDS + hold_seconds + 8):
            value = await backend.open(scope, before_dispatch=guard)
            while True:
                result["phases"].append(value["phase"])
                if value["phase"] == "active":
                    await asyncio.sleep(hold_seconds)
                    value = await backend.status(scope)
                    if value["phase"] == "active" and not value["cleanup_pending"]:
                        if capture_window:

                            async def capture_guard():
                                current = await backend.status(scope)
                                if (
                                    current["phase"] != "active"
                                    or current["cleanup_pending"]
                                    or not current["capture_available"]
                                ):
                                    raise BusinessError(
                                        current["capture_error_code"] or "portal_revoked",
                                        "Frame not available",
                                        status=503,
                                    )

                            await capture_guard()
                            result["pixels_read"] = result["pipewire_fd_opened"] = None
                            frame = await backend.capture(scope, before_dispatch=capture_guard)
                            await capture_guard()
                            result["pixels_read"] = result["pipewire_fd_opened"] = True
                            result["frame"] = {
                                "width": frame.width,
                                "height": frame.height,
                                "bytes": len(frame.content),
                                "sha256": frame.sha256,
                                "method": frame.method,
                            }
                        result["status"] = "capture_sample_passed" if capture_window else "consent_sample_passed"
                    else:
                        result["error_code"] = value["error_code"] or "portal_consent_revoked"
                        result["phases"].append(value["phase"])
                    break
                if value["phase"] in TERMINAL:
                    result["error_code"] = value["error_code"] or "portal_consent_not_granted"
                    break
                value = await backend.wait(scope, value["revision"])
    except BusinessError as error:
        result["error_code"] = error.code
    except TimeoutError:
        result["error_code"] = "portal_timeout"
    except Exception:  # noqa: BLE001 -- private bus/session details never enter the report
        result["error_code"] = "portal_sample_failed"
    finally:
        try:
            clean = await backend.close(6)
        except Exception:  # noqa: BLE001 -- reclaim failure is explicit without native error text
            clean = False
        result["cleanup_verified"] = clean
        if not clean:
            result.update(status="cleanup_pending", error_code="portal_cleanup_pending")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--consent-window-share",
        action="store_true",
        help="Explicitly present the OS window selector on this logged-in test account",
    )
    parser.add_argument("--hold-seconds", type=float, default=1)
    parser.add_argument(
        "--capture-window", action="store_true", help="Explicitly read one selected window frame; keep only its receipt"
    )
    parser.add_argument("--output", type=Path, required=True, help="New private evidence directory")
    args = parser.parse_args()
    if sys.platform != "linux" or not args.consent_window_share:
        parser.error("Requires Linux and explicit --consent-window-share")
    if not 0 <= args.hold_seconds <= 10:
        parser.error("Use a hold time between 0 and 10 seconds")
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    private_path(args.output, directory=True)
    result = asyncio.run(
        accept(
            PortalBackend(capture_root=args.output / "staging"), args.hold_seconds, capture_window=args.capture_window
        )
    )
    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    path = args.output / "evidence.json"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    private_path(path)
    print(json.dumps({key: result[key] for key in ("status", "error_code", "cleanup_verified")}))
    return (
        0
        if result["status"] in {"consent_sample_passed", "capture_sample_passed"} and result["cleanup_verified"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
