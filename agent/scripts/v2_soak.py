"""Observe an explicitly selected running V2 boot; never starts/stops it, opens its DB or invokes models/devices."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from omni.adapters.soak import LocalSampler, SoakFiles, collector_identity
from omni.domain.contracts import BusinessError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "report"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--url", help="Explicit loopback HTTP IP and port; no URL token")
    parser.add_argument("--limits", type=Path)
    parser.add_argument("--hours", type=float, default=72)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        files = SoakFiles(args.output)
        if args.command == "report":
            report = files.report()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["observational_soak_passed"] else 2
        if args.home is None or args.url is None:
            parser.error("run requires --home and --url")
        if not math.isfinite(args.hours) or not 0 < args.hours <= 168:
            parser.error("hours must be positive and no more than 168; shorter runs never pass 72h")
        sampler = LocalSampler(args.home, args.url)
        try:
            if args.resume:
                manifest = files.manifest()
                files.report()  # Validate the complete prior chain before continuing it.
                if manifest["origin"] != sampler.url or manifest["sampler_mode"] != sampler.mode:
                    raise BusinessError("v2_soak_resume_mismatch", "Resume the same V2 origin and sampler")
                sampler.identity = manifest["identity"]
                if manifest["collector_sha256"] != collector_identity():
                    raise BusinessError(
                        "v2_soak_collector_changed", "Use the original collector or a new evidence directory"
                    )
            else:
                if args.limits is None:
                    parser.error("a new run requires --limits; limits are never guessed")
                with args.limits.open("rb") as stream:
                    raw_policy = stream.read(4097)
                if len(raw_policy) > 4096:
                    raise BusinessError("v2_soak_limits_too_large", "Use one bounded limits document")
                manifest = files.create(args.home, sampler, json.loads(raw_policy), args.interval)
            deadline = time.monotonic() + args.hours * 3600
            while time.monotonic() < deadline:
                time.sleep(min(manifest["interval_s"], max(0, deadline - time.monotonic())))
                sample = sampler.sample()
                sequence = files.append(manifest, sample)
                print(json.dumps({"samples": sequence, "sample_ok": sample["ok"], "code": sample["code"]}), flush=True)
            report = files.report()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["observational_soak_passed"] else 2
        finally:
            sampler.close()
    except KeyboardInterrupt:
        print(json.dumps({"stopped": True, "evidence_retained": True, "observed_service_stopped": False}))
        return 130
    except (BusinessError, OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"error": "v2_soak_evidence_unavailable", "existing_evidence_retained": True}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
