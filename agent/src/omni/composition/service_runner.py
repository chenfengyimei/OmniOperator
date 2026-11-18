"""Start only a verified immutable deployment as its declared account; never registers a native service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import cast

from omni.adapters.platform.services.deployment_files import CurrentDeploymentFiles
from ..domain.contracts import BusinessError
from ..domain.service_deployment import parse_startup, startup
from .serving import serve_instance


def run(bundle: Path) -> None:
    files = CurrentDeploymentFiles()
    report = files.verify(bundle)
    if report.get("valid") is not True or report.get("eligible") is not True:
        raise BusinessError("service_deployment_ineligible", "Deployment cannot start; run service verify for details")
    plan = report["plan"]
    configuration = parse_startup(files.read(bundle / "startup.json"))
    if configuration != startup(plan):
        raise BusinessError("service_startup_changed", "Service startup differs from its pinned plan")
    working = Path(cast(str, configuration["working_directory"]))
    for path in (working, *working.parents):
        if path.is_symlink() or path.is_junction():
            raise BusinessError("service_working_directory_unsafe", "Working directory cannot use links")
    if not working.is_dir():
        raise BusinessError("service_working_directory_missing", "Create the explicit service working directory first")
    os.chdir(working)
    # parse_startup 返回 dict[str, object]；这里按持久的启动契约逐项窄化，不做隐式转换。
    home = cast(str, configuration["home"])
    host = cast(str, configuration["host"])
    port = cast(int, configuration["port"])
    external_url = cast("str | None", configuration["external_url"])
    master_key = configuration["master_key_file"]
    serve_instance(
        Path(home),
        host=host,
        port=port,
        external_url=external_url,
        master_key_file=Path(cast(str, master_key)) if master_key else None,
        stdout_logs=cast(bool, configuration["stdout_logs"]),
        deployment=configuration,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Pinned OmniOperator V2 service entry")
    parser.add_argument("--deployment", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.deployment)
        return 0
    except (BusinessError, OSError) as exc:
        print(
            json.dumps({"error": exc.code if isinstance(exc, BusinessError) else "service_filesystem_error"}),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
