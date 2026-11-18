"""Root bundle's fixed read-only system probe. Never imported from an owner environment."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    try:
        if (
            sys.platform != "darwin"
            or not sys.flags.isolated
            or not sys.flags.no_site
            or os.getuid() != 0
            or os.geteuid() != 0
            or len(argv) != 2
            or re.fullmatch(r"com\.omnioperator\.v2\.[0-9a-f-]{36}", argv[0]) is None
            or re.fullmatch(r"[0-9a-f]{32}", argv[1]) is None
        ):
            raise ValueError("fixed root probe required")
        root = Path(__file__).resolve().parent
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(root / "lib"))
        from omni.adapters.platform.services.darwin_admin_files import protected
        from omni.adapters.platform.services.darwin_admin_probe import NativeJobs

        protected(Path(__file__).resolve())
        result = NativeJobs().query(argv[0])
        result.update(format="omni-macos-root-query-1", label=argv[0], nonce=argv[1])
        data = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        if len(data) > 65536:
            raise ValueError("native result limit")
        sys.stdout.buffer.write(data + b"\n")
        return 0
    except Exception:  # noqa: BLE001 -- no native description, exception text or stderr leaves the root probe
        print('{"error_code":"service_admin_native_unverified"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
