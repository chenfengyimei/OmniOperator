"""Standalone offline V2 installer: Python plus a pinned app wheel and dependency bundle are sufficient."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--dependency-bundle", type=Path, required=True)
    parser.add_argument("--dependency-sha256", required=True)
    parser.add_argument("--manager", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    try:
        if re.fullmatch(r"[a-f0-9]{64}", args.sha256) is None or args.wheel.suffix != ".whl":
            raise ValueError("invalid wheel pin")
        # Capture and pin before importing any application code, even from an editable checkout.
        with tempfile.TemporaryDirectory(prefix="omni-v2-bootstrap-") as temporary:
            captured = Path(temporary) / args.wheel.name
            digest, size = hashlib.sha256(), 0
            with args.wheel.open("rb") as source, captured.open("xb") as target:
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > 512 * 1024 * 1024:
                        raise ValueError("wheel too large")
                    digest.update(block)
                    target.write(block)
            if digest.hexdigest() != args.sha256 or "omni" in sys.modules:
                raise ValueError("wheel pin mismatch or application already imported")
            sys.path.insert(0, str(captured))
            from omni.adapters.platform.candidate_installer import install_candidate
            from omni.domain.contracts import BusinessError

            try:
                result = install_candidate(
                    captured,
                    args.sha256,
                    args.dependency_bundle,
                    args.dependency_sha256,
                    args.manager,
                    args.home,
                )
            except BusinessError as exc:
                print(json.dumps({"error": exc.code}), file=sys.stderr)
                return 1
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, ImportError):
        print(json.dumps({"error": "candidate_install_failed"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
