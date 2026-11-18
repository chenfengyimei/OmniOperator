"""Capture installation identity and publish reviewed deployment bundles; never registers services or opens business storage."""

from __future__ import annotations

import getpass
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import stat
import sys
import uuid
from importlib.util import find_spec
from pathlib import Path
from typing import Any, NoReturn, cast

import psutil

from omni.adapters.build_identity import CODE_CACHE_DIRS, CODE_SUFFIXES, LAYERS
from omni.adapters.object_store import publish_file, sync_directory
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.platform.runtime.permissions import assert_private, private_path
from omni.adapters.platform.runtime.publication import publish_directory
from omni.domain.contracts import BusinessError

Record = dict[str, Any]
MAX_JSON = 2 * 1024 * 1024
MAX_FILES, MAX_BYTES = 2048, 64 * 1024 * 1024
MANIFEST = "bundle.manifest.json"
MANIFEST_FORMAT = "omni-service-bundle-1"
ASSET_SUFFIXES = frozenset(
    {".html", ".js", ".css", ".svg", ".woff", ".woff2", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp"}
)


def _error(code: str = "deployment_files_invalid", *, status: int = 422) -> BusinessError:
    return BusinessError(code, "Deployment paths, files or installation identity are invalid", status=status)


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path(value: str | Path) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or any(ord(char) < 32 for char in str(path))
        or (os.name == "nt" and (str(path).startswith("\\\\") or any(":" in part for part in path.parts[1:])))
    ):
        raise _error("deployment_path_unsafe")
    return path


def _no_links(path: Path, *, final: bool = True) -> None:
    for candidate in (path, *path.parents) if final else path.parents:
        if candidate.is_symlink() or candidate.is_junction():
            raise _error("deployment_path_unsafe")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    # Windows fstat and stat expose different ctime semantics; stable identity
    # uses inode/device, size, mode, links and last content modification instead.
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_size, info.st_mtime_ns, info.st_nlink


def _read(path: str | Path, limit: int, *, single: bool = False) -> bytes:
    path = _path(path)
    _no_links(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit or (single and before.st_nlink != 1):
            raise _error()
        content = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    _no_links(path)
    if (
        len(content) != before.st_size
        or _identity(before) != _identity(after)
        or _identity(before) != _identity(path.stat(follow_symlinks=False))
    ):
        raise _error("deployment_source_changed", status=409)
    return content


def _decode(data: bytes) -> Record:
    def pairs(items: list[tuple[str, Any]]) -> Record:
        result: Record = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate deployment field")
            result[key] = value
        return result

    def constant(_value: object) -> NoReturn:
        raise ValueError("non-finite deployment value")

    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(value, dict):
            raise ValueError
        # A finite exponent may overflow JSON's float parser too.
        _json(cast(Record, value))
        return cast(Record, value)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise _error() from exc


def _relative(value: str) -> str:
    if not isinstance(cast(object, value), str) or not value or len(value) > 1000 or "\\" in value:
        raise _error()
    parts = value.split("/")
    if len(parts) > 24 or any(
        part in {"", ".", ".."}
        or part.endswith((".", " "))
        or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
        or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part, re.I)
        for part in parts
    ):
        raise _error()
    value.encode("utf-8")
    return value


def _walk(root: Path, *, python_only: bool = False, source_assets: bool = False) -> list[tuple[str, Path]]:
    _no_links(_path(root))
    if not root.is_dir():
        raise _error("deployment_program_missing")
    result: list[tuple[str, Path]] = []
    pending = [root]
    entries = 0
    seen: set[str] = set()
    while pending:
        directory = pending.pop()
        _no_links(directory)
        with os.scandir(directory) as children:
            for child in children:
                entries += 1
                if entries > MAX_FILES * 16:
                    raise _error("deployment_capture_limit")
                if (child.name in CODE_CACHE_DIRS and python_only) or (child.name == "node_modules" and source_assets):
                    continue
                location = Path(child.path)
                name = _relative(location.relative_to(root).as_posix())
                _no_links(location)
                folded = name.casefold()
                if folded in seen:
                    raise _error()
                seen.add(folded)
                info = location.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(location)
                elif stat.S_ISREG(info.st_mode):
                    if (not python_only or location.suffix in CODE_SUFFIXES) and (
                        not source_assets or location.suffix.lower() in ASSET_SUFFIXES
                    ):
                        result.append((name, location))
                else:
                    raise _error()
                if len(result) > MAX_FILES:
                    raise _error("deployment_capture_limit")
    return sorted(result)


def _principal() -> Record:
    if os.name != "nt":
        identity = str(os.getuid())
    else:
        import ctypes
        from ctypes import wintypes as w

        security, kernel = (
            ctypes.WinDLL("advapi32", use_last_error=True),
            ctypes.WinDLL("kernel32", use_last_error=True),
        )
        token, needed, text = w.HANDLE(), w.DWORD(), w.LPWSTR()
        kernel.GetCurrentProcess.restype = w.HANDLE
        security.OpenProcessToken.argtypes, security.OpenProcessToken.restype = (
            [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)],
            w.BOOL,
        )
        security.GetTokenInformation.argtypes, security.GetTokenInformation.restype = (
            [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)],
            w.BOOL,
        )
        security.ConvertSidToStringSidW.argtypes, security.ConvertSidToStringSidW.restype = (
            [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)],
            w.BOOL,
        )
        kernel.CloseHandle.argtypes, kernel.LocalFree.argtypes = [w.HANDLE], [ctypes.c_void_p]
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)):
            raise _error("deployment_principal_unavailable")
        try:
            security.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            if not 1 <= needed.value <= 65536:
                raise _error("deployment_principal_unavailable")
            buffer = ctypes.create_string_buffer(needed.value)
            if not security.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
                raise _error("deployment_principal_unavailable")
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            if not security.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise _error("deployment_principal_unavailable")
            try:
                identity = str(text.value)
            finally:
                kernel.LocalFree(text)
        finally:
            kernel.CloseHandle(token)
    return {"name": getpass.getuser(), "id": identity, "home": str(Path.home().absolute())}


def _python() -> Record:
    return {
        **python_files(
            Path(sys.executable).absolute(), Path(sys.prefix).absolute(), Path(psutil.Process().exe()).resolve()
        ),
        "python_version": sys.version.replace("\r\n", " ").replace("\n", " "),
    }


def python_files(executable: Path, prefix: Path, process_executable: Path | None = None) -> Record:
    """Fingerprint an explicit interpreter's files without executing or importing that environment."""
    executable = _path(executable)
    links: list[dict[str, str]]
    location, links = executable, []
    _no_links(executable, final=False)
    while location.is_symlink():
        if len(links) >= 8:
            raise _error("deployment_python_link")
        target = os.readlink(location)
        if not target or len(target) > 4000 or any(ord(c) < 32 for c in target):
            raise _error("deployment_python_link")
        links.append({"path": str(location), "target": target})
        destination = Path(target) if Path(target).is_absolute() else location.parent / target
        location = _path(Path(os.path.normpath(destination)))
        _no_links(location, final=False)
    binary = _read(location, MAX_BYTES)
    process_executable = _path(process_executable or location)
    process_binary = _read(process_executable, MAX_BYTES)
    # Recheck the exact link chain after reading the binary.
    for link in links:
        if not Path(link["path"]).is_symlink() or os.readlink(link["path"]) != link["target"]:
            raise _error("deployment_source_changed", status=409)
    prefix = _path(prefix)
    _no_links(prefix)
    config = prefix / "pyvenv.cfg"
    venv = None
    if config.exists() or config.is_symlink():
        content = _read(config, 16384)
        venv = {"path": str(config), "sha256": _sha(content), "bytes": len(content)}
    return {
        "python": str(executable),
        "python_realpath": str(location),
        "python_prefix": str(prefix),
        "python_links": links,
        "python_sha256": _sha(binary),
        "process_python": str(process_executable),
        "process_python_sha256": _sha(process_binary),
        "venv_config": venv,
    }


def _installation() -> tuple[Path, Path, bool]:
    # 不用绝对 `import omni`：包本身没有 py.typed，strict 会报 reportMissingTypeStubs；
    # find_spec 同样能定位包根目录，且不需要执行包 __init__。
    spec = find_spec("omni")
    origin = spec.origin if spec is not None else None
    if not origin:
        raise _error("deployment_program_missing")
    package = _path(Path(origin).absolute().parent)
    _no_links(package)
    assets, editable = package / "web_assets", True
    try:
        distribution = importlib.metadata.distribution("omnioperator")
        direct = distribution.read_text("direct_url.json")
        metadata_root = Path(str(distribution.locate_file(""))).absolute()
        editable = metadata_root != package.parent or bool(
            direct and _decode(direct.encode()).get("dir_info", {}).get("editable")
        )
    except importlib.metadata.PackageNotFoundError:
        pass
    if not (assets / "ui/v2/index.html").is_file():
        # The source checkout fallback is always preview-only and never grants execution eligibility.
        assets = package.parents[2] / "web"
        editable = True
    if not (assets / "ui/v2/index.html").is_file():
        raise _error("deployment_assets_missing")
    return package, assets, editable


def _proof(name: str, content: bytes) -> Record:
    return {"path": name, "sha256": _sha(content), "bytes": len(content)}


def _startup_locations() -> list[Path]:
    home = Path.home()
    locations = [
        home / "Library/LaunchAgents",
        Path("/Library/LaunchAgents"),
        Path("/Library/LaunchDaemons"),
        Path("/System/Library/LaunchAgents"),
        Path("/System/Library/LaunchDaemons"),
        home / ".config/systemd",
        home / ".config/autostart",
        Path("/etc/systemd"),
        Path("/usr/lib/systemd"),
        Path("/lib/systemd"),
        Path("/run/systemd"),
        Path("/etc/xdg/autostart"),
        Path("/etc/init.d"),
    ]
    for env, suffix in (
        ("APPDATA", "Microsoft/Windows/Start Menu/Programs/Startup"),
        ("PROGRAMDATA", "Microsoft/Windows/Start Menu/Programs/Startup"),
        ("WINDIR", "System32/Tasks"),
        ("XDG_CONFIG_HOME", "systemd"),
        ("XDG_CONFIG_HOME", "autostart"),
        ("XDG_RUNTIME_DIR", "systemd"),
    ):
        if os.environ.get(env) and Path(os.environ[env]).is_absolute():
            locations.append(Path(os.environ[env]) / suffix)
    return [path.absolute() for path in locations]


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _destination(path: str | Path, plan: Record) -> Path:
    output = _path(path)
    _no_links(output)
    request, runtime = plan["request"], plan["runtime"]
    protected = [Path(request["home"]), Path(runtime["package_root"]), Path(runtime["python"]).parent]
    protected.extend(_startup_locations())
    if any(_overlap(output, root) for root in protected):
        raise _error("deployment_destination_unsafe")
    return output


class CurrentDeploymentFiles:
    def capture(self) -> Record:
        package, assets, editable = _installation()
        files: list[Record]
        files, total = [], 0
        build, assets_digest = hashlib.sha256(), hashlib.sha256()
        candidates = [("__init__.py", package / "__init__.py")]
        candidates += [
            (layer + "/" + name, path) for layer in LAYERS for name, path in _walk(package / layer, python_only=True)
        ]
        candidates += [
            ("web_assets/" + name, path) for name, path in _walk(assets, source_assets=assets != package / "web_assets")
        ]
        if len(candidates) > MAX_FILES or len({name.casefold() for name, _ in candidates}) != len(candidates):
            raise _error("deployment_capture_limit")
        for name, path in sorted(candidates):
            if any(
                part.casefold() in {".env", "owner.token", "omni.db", "credentials.json", "vault", "private"}
                or part.casefold().startswith(".env.")
                for part in name.split("/")
            ):
                raise _error("deployment_private_file")
            content = _read(path, MAX_BYTES)
            total += len(content)
            if total > MAX_BYTES:
                raise _error("deployment_capture_limit")
            files.append(_proof(name, content))
            first = name.split("/", 1)[0]
            if first in LAYERS and path.suffix in CODE_SUFFIXES:
                build.update(name.encode())
                build.update(content)
            elif first == "web_assets":
                assets_digest.update(name.removeprefix("web_assets/").encode())
                assets_digest.update(content)
        distributions: dict[str, str] = {}
        for distribution in importlib.metadata.distributions():
            name = re.sub(r"[-_.]+", "-", distribution.metadata.get("Name", "")).lower()
            version = distribution.version
            if (
                not name
                or len(name) > 200
                or not version
                or len(version) > 200
                or (name in distributions and distributions[name] != version)
            ):
                raise _error("deployment_distribution_invalid")
            distributions[name] = version
            if len(distributions) > 512:
                raise _error("deployment_capture_limit")
        result: Record = {
            "format": "omni-service-runtime-2",
            "platform": sys.platform,
            **_python(),
            "architecture": platform.machine(),
            "package_root": str(package),
            "build_id": build.hexdigest(),
            "assets_sha256": assets_digest.hexdigest(),
            "api_version": 1,
            "schema_head": migration_head(),
            "editable": editable,
            "principal": _principal(),
            "files": files,
            "distributions": [{"name": name, "version": version} for name, version in sorted(distributions.items())],
        }
        from omni.domain.service_deployment import validate_runtime

        return validate_runtime(result)

    def read(self, path: str | Path) -> Record:
        return _decode(_read(path, MAX_JSON, single=True))

    def write_capture(self, runtime: Record, path: str | Path) -> Record:
        from omni.domain.service_deployment import validate_runtime

        checked = validate_runtime(runtime)
        target = _path(path)
        _no_links(target)
        if any(
            _overlap(target, root)
            for root in (
                Path(cast(str, checked["package_root"])),
                Path(cast(str, checked["python_prefix"])),
                *_startup_locations(),
            )
        ):
            raise _error("deployment_destination_unsafe")
        data = _json(checked)
        self._publish_json(target, data)
        return {"path": str(target), "sha256": _sha(data), "captured": True}

    def write_plan(self, plan: Record, path: str | Path) -> Record:
        from omni.domain.service_deployment import validate_plan

        checked = validate_plan(plan)
        target = _destination(path, checked)
        data = _json(checked)
        self._publish_json(target, data)
        return {"path": str(target), "plan_id": checked["id"], "sha256": _sha(data), "installed": False}

    def _publish_json(self, target: Path, data: bytes) -> None:
        if target.exists():
            raise _error("destination_exists", status=409)
        if not target.parent.is_dir():
            raise _error("deployment_parent_missing")
        if len(data) > MAX_JSON:
            raise _error("deployment_capture_limit")
        temporary = target.with_name("." + str(uuid.uuid4()) + ".plan.partial")
        try:
            self._write(temporary, data)
            _no_links(target)
            if os.name == "nt":
                publish_file(temporary, target)
            else:
                os.link(temporary, target)
                temporary.unlink()
                sync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            private_path(path)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def write_file(path: Path, data: bytes) -> None:
        CurrentDeploymentFiles._write(path, data)

    def publish_json(self, target: Path, data: bytes) -> None:
        self._publish_json(target, data)

    def materialize(self, plan: Record, destination: str | Path) -> Record:
        from omni.adapters.platform.services.service_templates import render
        from omni.domain.service_deployment import plan_fingerprint, validate_plan

        checked = validate_plan(plan)
        target = _destination(destination, checked)
        if cast(Record, checked["runtime"])["platform"] == sys.platform and target != _path(
            cast(Record, checked["request"])["bundle_dir"]
        ):
            raise _error("deployment_location_mismatch")
        if target.exists():
            raise _error("destination_exists", status=409)
        if not target.parent.is_dir():
            raise _error("deployment_parent_missing")
        artifacts = render(checked)
        if not isinstance(cast(object, artifacts), dict) or not 1 <= len(artifacts) <= 16 or MANIFEST in artifacts:
            raise _error()
        entries: list[Record] = []
        for name, content in sorted(artifacts.items()):
            if (
                _relative(name) != name
                or "/" in name
                or not isinstance(cast(object, content), bytes)
                or len(content) > MAX_JSON
            ):
                raise _error()
            entries.append(_proof(name, content))
        manifest: Record = {
            "format": MANIFEST_FORMAT,
            "plan_id": checked["id"],
            "plan_sha256": plan_fingerprint(checked),
            "files": entries,
        }
        manifest["sha256"] = _sha(_json(manifest))
        temporary = target.with_name("." + str(uuid.uuid4()) + ".deployment.partial")
        temporary.mkdir(mode=0o700)
        private_path(temporary, directory=True)
        # Partial bundles are intentionally retained on failure. They are never an installed service or a valid published bundle.
        for name, content in artifacts.items():
            self._write(temporary / name, content)
        self._write(temporary / MANIFEST, _json(manifest))
        sync_directory(temporary)
        _no_links(target)
        publish_directory(temporary, target)
        return {
            "bundle": str(target),
            "plan_id": checked["id"],
            "plan_sha256": plan_fingerprint(checked),
            "files": entries,
            "installed": False,
        }

    def verify(self, bundle: str | Path) -> Record:
        from omni.adapters.platform.services.service_templates import render

        issues: list[str] = []
        plan: Record = {}
        try:
            root = _path(bundle)
            plan = self.artifact_plan(root)
            artifacts = render(plan)
            current = self.capture()
            if current != plan["runtime"]:
                issues.append("deployment_program_changed")
            valid = not issues
            if plan["runtime"]["editable"]:
                issues.append("editable_installation")
            expected_platform = {
                "windows-user": "win32",
                "macos-user": "darwin",
                "macos-system": "darwin",
                "linux-user": "linux",
                "linux-system": "linux",
            }.get(plan["request"]["target"])
            if expected_platform != sys.platform:
                issues.append("deployment_platform_mismatch")
            if plan["runtime"]["platform"] == sys.platform and root != _path(plan["request"]["bundle_dir"]):
                issues.append("deployment_location_mismatch")
            if plan["runtime"]["principal"] != current["principal"]:
                issues.append("deployment_principal_mismatch")
            try:
                assert_private(root)
                for name in (*artifacts, MANIFEST):
                    assert_private(root / name)
            except OSError:
                issues.append("deployment_permissions_unsafe")
            return {
                "valid": valid,
                "eligible": valid and not issues,
                "issues": issues,
                "plan": plan,
                "bundle": str(root),
            }
        except (BusinessError, OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            code = exc.code if isinstance(exc, BusinessError) else "deployment_files_invalid"
            return {"valid": False, "eligible": False, "issues": [code], "plan": plan, "bundle": str(bundle)}

    def artifact_plan(self, bundle: str | Path) -> Record:
        """Verify artifact bytes only; never grants foreign installation execution eligibility."""
        from omni.adapters.platform.services.service_templates import render
        from omni.domain.service_deployment import plan_fingerprint, validate_plan

        root = _path(bundle)
        _no_links(root)
        if not root.is_dir():
            raise _error()
        manifest = self.read(root / MANIFEST)
        if (
            set(manifest) != {"format", "plan_id", "plan_sha256", "files", "sha256"}
            or manifest["format"] != MANIFEST_FORMAT
            or manifest["sha256"] != _sha(_json({key: value for key, value in manifest.items() if key != "sha256"}))
        ):
            raise _error()
        plan = validate_plan(self.read(root / "deployment.json"))
        _destination(root, plan)
        artifacts = render(plan)
        if (
            manifest["plan_id"] != plan["id"]
            or manifest["plan_sha256"] != plan_fingerprint(plan)
            or manifest["files"] != [_proof(name, content) for name, content in sorted(artifacts.items())]
        ):
            raise _error()
        if {child.name for child in root.iterdir()} != {*artifacts, MANIFEST}:
            raise _error("deployment_extra_files")
        for name, content in artifacts.items():
            if _relative(name) != name or "/" in name or _read(root / name, MAX_JSON, single=True) != content:
                raise _error("deployment_artifact_changed", status=409)
        return plan


decode = _decode
proof = _proof
read_file = _read
json_bytes = _json
no_links = _no_links
path_of = _path
walk = _walk


def write_file(path: Path, data: bytes) -> None:
    CurrentDeploymentFiles.write_file(path, data)


destination = _destination


def publish_json(files: CurrentDeploymentFiles, target: Path, data: bytes) -> None:
    files.publish_json(target, data)
