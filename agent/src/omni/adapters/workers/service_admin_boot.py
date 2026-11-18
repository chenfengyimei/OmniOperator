"""Standalone administrator entry. Invoke a reviewed root-owned bundle with Python -I -S."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, cast


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def strict_json(data: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[Any, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    decoded: Any = json.loads(
        data, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
    )
    if not isinstance(decoded, dict):
        raise ValueError("object required")
    result = cast(dict[str, Any], decoded)
    canonical(result)
    return result


def trusted(path: Path) -> None:
    for current in (path, *path.parents):
        info = current.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError("untrusted root resource")


def read_owner_file(path: Path, uid: int, maximum: int = 2 * 1024 * 1024) -> bytes:
    if sys.platform != "linux" and sys.platform != "darwin":
        raise ValueError("Native Unix descriptor walk required")
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("absolute path required")
    # Descend through held directory descriptors. A pathname race cannot cause
    # root to follow an owner-supplied directory link into unrelated storage.
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = current
        file = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=descriptor)
        with os.fdopen(file, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != uid
                or before.st_mode & 0o077
                or before.st_size > maximum
            ):
                raise ValueError("private owner JSON required")
            result = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
        fields = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        current = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        if len(result) != before.st_size or fields(before) != fields(after) or fields(after) != fields(current):
            raise ValueError("source changed")
        return result
    finally:
        os.close(descriptor)


def validate_bundle(root: Path) -> dict[str, Any]:
    trusted(root)
    manifest_path = root / "manifest.json"
    trusted(manifest_path)
    manifest = strict_json(manifest_path.read_bytes())
    if set(manifest) != {"format", "files", "sha256"} or manifest["format"] != "omni-service-admin-bundle-4":
        raise ValueError("bundle format")
    if (
        manifest["sha256"]
        != hashlib.sha256(canonical({key: value for key, value in manifest.items() if key != "sha256"})).hexdigest()
    ):
        raise ValueError("bundle hash")
    if not isinstance(manifest["files"], list):
        raise ValueError("bounded bundle required")
    entries = cast(list[Any], manifest["files"])
    if not 8 <= len(entries) <= 32:
        raise ValueError("bounded bundle required")
    names: set[str] = set()
    for record in entries:
        if not isinstance(record, dict):
            raise ValueError("record format")
        fields = cast(dict[str, Any], record)
        if set(fields) != {"path", "source", "bytes", "sha256"}:
            raise ValueError("record format")
        name = fields["path"]
        if (
            not isinstance(name, str)
            or name in names
            or name.startswith("/")
            or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise ValueError("bundle path")
        names.add(name)
        path = root / name
        trusted(path)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
            raise ValueError("bundle file")
        data = path.read_bytes()
        if (
            type(fields["bytes"]) is not int
            or len(data) != fields["bytes"]
            or hashlib.sha256(data).hexdigest() != fields["sha256"]
        ):
            raise ValueError("bundle changed")
    actual: set[str] = set()
    for path in root.rglob("*"):
        trusted(path)
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
        if len(actual) > 33:
            raise ValueError("bundle size")
    if actual != names | {"manifest.json"} or "service-admin.py" not in names:
        raise ValueError("extra bundle files")
    return manifest


def validate_sources(manifest: dict[str, Any], operation: dict[str, Any]) -> None:
    if operation.get("format") == "omni-service-admin-phase-1":
        # A phase executes only its own installation's administrator code.
        # The other installation is reviewed metadata, never imported as root.
        deployments = [operation["exchange"]["release"][operation["role"]]]
    else:
        deployments = (
            [operation["deployment"]]
            if "deployment" in operation
            else [operation["release"][side] for side in ("source", "target")]
        )
    for deployment in deployments:
        captured = {item["path"]: (item["sha256"], item["bytes"]) for item in deployment["runtime"]["files"]}
        for record in manifest["files"]:
            if record["source"] is not None and captured.get(record["source"]) != (record["sha256"], record["bytes"]):
                raise ValueError("owner and administrator sources differ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", type=Path, required=True)
    parser.add_argument("--approve", required=True)
    parser.add_argument("--owner-uid", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if (
            (sys.platform != "linux" and sys.platform != "darwin")
            or sys.version_info < (3, 12)
            or not sys.flags.isolated
            or not sys.flags.no_site
            or os.getuid() != 0
            or os.geteuid() != 0
            or not 1 <= args.owner_uid <= 4_294_967_294
        ):
            raise ValueError("isolated root native Unix entry required")
        # -S prevents site/.pth execution. The administrator must start this
        # root-owned artifact with a trusted interpreter and a clean environment.
        trusted(Path(sys.executable).resolve())
        trusted(Path(sys.base_prefix).resolve())
        root = Path(__file__).resolve().parent
        manifest = validate_bundle(root)
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(root / "lib"))
        from omni.adapters.platform.services.service_admin import serve
        from omni.adapters.platform.services.service_admin_policy import RootServicePolicy
        from omni.domain.service_admin import admin_scope, scoped_operations

        operation = admin_scope(strict_json(read_owner_file(args.operation, args.owner_uid)))
        if operation["sha256"] != args.approve or any(
            item["deployment"]["request"]["principal"]["id"] != str(args.owner_uid)
            for item in scoped_operations(operation).values()
        ):
            raise ValueError("review mismatch")
        validate_sources(manifest, operation)
        if sys.platform == "darwin":
            from omni.adapters.platform.services.darwin_admin_commands import DarwinCommands
            from omni.adapters.platform.services.darwin_admin_files import protected
            from omni.adapters.platform.services.darwin_admin_policy import DarwinPolicy

            # The administrator has already reviewed and protected the entire
            # bootstrap artifact. Check extended ACLs again before native work.
            for path in (root, *root.rglob("*")):
                protected(path)
            return serve(DarwinPolicy(operation, manifest["sha256"], DarwinCommands(lambda: True), root))
        return serve(RootServicePolicy(operation, manifest["sha256"]))
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- privilege boundary must never print exception data
        # Root/user paths, JSON bodies, native errors and traceback locals must
        # not be printed from this privilege boundary.
        print(json.dumps({"error_code": "service_admin_failed", "operation_replayed": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
