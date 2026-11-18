"""Build a platform-pinned V2 dependency bundle from a local wheel directory; never downloads or installs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from omni.adapters.platform.releases.dependency_bundle import build_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_bundle(args.wheels, args.output), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
