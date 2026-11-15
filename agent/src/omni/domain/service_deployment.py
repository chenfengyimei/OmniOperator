"""Pure, versioned service plans. Validation performs no filesystem or OS calls."""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import ntpath
import posixpath
import re
import uuid
from typing import cast
from urllib.parse import urlsplit

from .contracts import BusinessError

REQUEST_FORMAT = "omni-service-request-1"
RUNTIME_FORMAT = "omni-service-runtime-2"
PLAN_FORMAT = "omni-service-plan-2"
STARTUP_FORMAT = "omni-service-startup-1"
TARGETS = frozenset({"windows-user", "macos-user", "macos-system", "linux-user", "linux-system"})
MAX_FILES, MAX_BYTES, MAX_DISTRIBUTIONS = 2048, 64 * 1024 * 1024, 512
REQUEST_KEYS = frozenset(
    {
        "format",
        "target",
        "instance_id",
        "home",
        "working_directory",
        "bundle_dir",
        "principal",
        "host",
        "port",
        "external_url",
        "master_key_file",
        "stdout_logs",
    }
)
RUNTIME_KEYS = frozenset(
    {
        "format",
        "platform",
        "python",
        "python_realpath",
        "python_prefix",
        "python_links",
        "venv_config",
        "python_sha256",
        "process_python",
        "process_python_sha256",
        "python_version",
        "architecture",
        "package_root",
        "build_id",
        "assets_sha256",
        "api_version",
        "schema_head",
        "editable",
        "principal",
        "files",
        "distributions",
    }
)


def invalid() -> BusinessError:
    return BusinessError(
        "invalid_service_plan", "Use an exact, bounded and explicitly scoped service deployment", status=422
    )


def _object(value: object, keys: set[str] | frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise invalid()
    fields = cast(dict[str, object], value)
    if set(fields) != keys:
        raise invalid()
    return fields


def _text(value: object, maximum: int = 4000) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    ):
        raise invalid()
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise invalid() from exc
    return value


def _uuid(value: object) -> str:
    value = _text(value, 36)
    try:
        if str(uuid.UUID(value)) != value:
            raise invalid()
    except ValueError as exc:
        raise invalid() from exc
    return value


def _sha(value: object) -> str:
    value = _text(value, 64)
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise invalid()
    return value


def _integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise invalid()
    return value


def _path(value: object, windows: bool) -> str:
    value = _text(value)
    if windows:
        drive, tail = ntpath.splitdrive(value)
        if re.fullmatch(r"[A-Za-z]:", drive) is None or not tail.startswith(("/", "\\")):
            raise invalid()
        parts = re.split(r"[\\/]", tail)
        for part in parts:
            if part in {".", ".."} or (part and part != part.rstrip(" .")):
                raise invalid()
            if any(character in '<>:"|?*' for character in part):
                raise invalid()
            if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".", 1)[0]):
                raise invalid()
        return drive.upper() + ntpath.normpath(tail)
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        raise invalid()
    if any(part in {".", ".."} for part in value.split("/")):
        raise invalid()
    return posixpath.normpath(value)


def deployment_fields(value: object, keys: set[str] | frozenset[str]) -> dict[str, object]:
    """Typed public bridge for sibling domain validators that must not import private helpers."""
    return _object(value, keys)


def deployment_created(value: object) -> str:
    return _created(value)


def deployment_plan(value: object) -> dict[str, object]:
    return validate_plan(value)


def deployment_make(request: object, runtime: object, identity: object, at: object) -> dict[str, object]:
    return make_plan(request, runtime, identity, at)


def deployment_integer(value: object, minimum: int, maximum: int) -> int:
    return _integer(value, minimum, maximum)


def deployment_path(value: object, windows: bool) -> str:
    return _path(value, windows)


def deployment_sha(value: object) -> str:
    return _sha(value)


def deployment_text(value: object, maximum: int = 4000) -> str:
    return _text(value, maximum)


def deployment_uuid(value: object) -> str:
    return _uuid(value)


def _principal(value: object, windows: bool, system: bool = False) -> dict[str, object]:
    value = _object(value, {"name", "id", "home"})
    name, identity = _text(value["name"], 200), _text(value["id"], 200)
    if windows:
        if re.fullmatch(r"S-1-(?:0|[1-9][0-9]*)(?:-(?:0|[1-9][0-9]*)){1,15}", identity) is None:
            raise invalid()
    else:
        if re.fullmatch(r"0|[1-9][0-9]{0,9}", identity) is None or int(identity) > 4294967294:
            raise invalid()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", name) is None:
            raise invalid()
        if system and (identity == "0" or name.lower() == "root"):
            raise invalid()
    return {"name": name, "id": identity, "home": _path(value["home"], windows)}


def _endpoint(host: object, port: object, external_url: object) -> tuple[str, int, str | None]:
    host = _text(host, 253)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host) is None:
            raise invalid() from None
    port = _integer(port, 1, 65535)
    origin = None
    if external_url is not None:
        try:
            url = urlsplit(_text(external_url, 2048))
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.path not in {"", "/"}
                or url.query
                or url.fragment
                or url.port == 0
            ):
                raise invalid()
            hostname = url.hostname
            try:
                ipaddress.ip_address(hostname)
            except ValueError:
                if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", hostname) is None:
                    raise invalid() from None
            authority = "[" + hostname + "]" if ":" in hostname else hostname
            origin = "https://" + authority + (":" + str(url.port) if url.port not in {None, 443} else "")
        except ValueError as exc:
            raise invalid() from exc
    if host not in {"127.0.0.1", "::1", "localhost"} and origin is None:
        raise invalid()
    return host, port, origin


def parse_request(data: object) -> dict[str, object]:
    value = _object(data, REQUEST_KEYS)
    target = value["target"]
    if (
        value["format"] != REQUEST_FORMAT
        or not isinstance(target, str)
        or target not in TARGETS
        or value["stdout_logs"] is not True
    ):
        raise invalid()
    windows = target == "windows-user"
    host, port, external = _endpoint(value["host"], value["port"], value["external_url"])
    return {
        "format": REQUEST_FORMAT,
        "target": target,
        "instance_id": _uuid(value["instance_id"]),
        "home": _path(value["home"], windows),
        "working_directory": _path(value["working_directory"], windows),
        "bundle_dir": _path(value["bundle_dir"], windows),
        "principal": _principal(value["principal"], windows, target.endswith("-system")),
        "host": host,
        "port": port,
        "external_url": external,
        "master_key_file": None if value["master_key_file"] is None else _path(value["master_key_file"], windows),
        "stdout_logs": True,
    }


def validate_runtime(data: object) -> dict[str, object]:
    value = _object(data, RUNTIME_KEYS)
    if (
        value["format"] != RUNTIME_FORMAT
        or not isinstance(value["platform"], str)
        or value["platform"] not in {"win32", "darwin", "linux"}
        or type(value["editable"]) is not bool
    ):
        raise invalid()
    windows = value["platform"] == "win32"
    python_version = value["python_version"]
    if isinstance(python_version, str):
        python_version = python_version.replace("\r\n", " ").replace("\n", " ")
    result: dict[str, object] = {
        **value,
        "python": _path(value["python"], windows),
        "python_realpath": _path(value["python_realpath"], windows),
        "python_prefix": _path(value["python_prefix"], windows),
        "package_root": _path(value["package_root"], windows),
        "python_sha256": _sha(value["python_sha256"]),
        "process_python": _path(value["process_python"], windows),
        "process_python_sha256": _sha(value["process_python_sha256"]),
        "python_version": _text(python_version, 1000),
        "architecture": _text(value["architecture"], 64),
        "build_id": _sha(value["build_id"]),
        "assets_sha256": _sha(value["assets_sha256"]),
        "principal": _principal(value["principal"], windows),
    }
    if (
        type(value["api_version"]) is not int
        or value["api_version"] != 1
        or re.fullmatch(r"v2_[0-9]{4}", _text(value["schema_head"], 20)) is None
    ):
        raise invalid()
    links_raw = value["python_links"]
    if not isinstance(links_raw, list):
        raise invalid()
    links = cast(list[object], links_raw)
    if len(links) > 8:
        raise invalid()
    python_links: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in links:
        entry = _object(item, {"path", "target"})
        path = _path(entry["path"], windows)
        if path in seen:
            raise invalid()
        seen.add(path)
        python_links.append({"path": path, "target": _text(entry["target"])})
    result["python_links"] = python_links
    config = value["venv_config"]
    if config is not None:
        options = _object(config, {"path", "sha256", "bytes"})
        config = {
            "path": _path(options["path"], windows),
            "sha256": _sha(options["sha256"]),
            "bytes": _integer(options["bytes"], 0, MAX_BYTES),
        }
    result["venv_config"] = config
    files_raw = value["files"]
    if not isinstance(files_raw, list):
        raise invalid()
    files = cast(list[object], files_raw)
    if not 1 <= len(files) <= MAX_FILES:
        raise invalid()
    entries: list[dict[str, object]] = []
    seen = set()
    total = 0
    for item in files:
        entry = _object(item, {"path", "sha256", "bytes"})
        path = _text(entry["path"])
        if (
            path.startswith("/")
            or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or ":" in path
        ):
            raise invalid()
        key = path.casefold() if windows else path
        if key in seen:
            raise invalid()
        seen.add(key)
        size = _integer(entry["bytes"], 0, MAX_BYTES)
        total += size
        if total > MAX_BYTES:
            raise invalid()
        entries.append({"path": path, "sha256": _sha(entry["sha256"]), "bytes": size})
    entries.sort(key=lambda row: cast(str, row["path"]))
    result["files"] = entries
    distributions_raw = value["distributions"]
    if not isinstance(distributions_raw, list):
        raise invalid()
    distributions = cast(list[object], distributions_raw)
    if len(distributions) > MAX_DISTRIBUTIONS:
        raise invalid()
    rows: list[dict[str, object]] = []
    seen = set()
    for item in distributions:
        entry = _object(item, {"name", "version"})
        name, version = _text(entry["name"], 200), _text(entry["version"], 200)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None:
            raise invalid()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            raise invalid()
        seen.add(key)
        rows.append({"name": name, "version": version})
    rows.sort(key=lambda row: cast(str, row["name"]))
    result["distributions"] = rows
    return result


def _created(value: object) -> str:
    value = _text(value, 40)
    try:
        timestamp = dt.datetime.fromisoformat(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() != dt.timedelta():
            raise invalid()
        return timestamp.isoformat(timespec="microseconds")
    except ValueError as exc:
        raise invalid() from exc


def _content(plan: object) -> dict[str, object]:
    if not isinstance(plan, dict):
        raise invalid()
    fields = cast(dict[str, object], plan)
    if (
        set(fields)
        not in (
            {"format", "id", "created_at", "request", "runtime", "isolated_registration"},
            {"format", "id", "created_at", "request", "runtime", "isolated_registration", "sha256"},
        )
        or fields["format"] != PLAN_FORMAT
    ):
        raise invalid()
    request, runtime = parse_request(fields["request"]), validate_runtime(fields["runtime"])
    if type(fields["isolated_registration"]) is not bool:
        raise invalid()
    platform = {"windows": "win32", "macos": "darwin", "linux": "linux"}[cast(str, request["target"]).split("-", 1)[0]]
    if platform != runtime["platform"] or request["principal"] != runtime["principal"]:
        raise invalid()
    return {
        "format": PLAN_FORMAT,
        "id": _uuid(fields["id"]),
        "created_at": _created(fields["created_at"]),
        "request": request,
        "runtime": runtime,
        "isolated_registration": fields["isolated_registration"],
    }


def plan_fingerprint(plan: object) -> str:
    content = json.dumps(_content(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def make_plan(
    request: object, runtime: object, id: object, created_at: object, *, isolated_registration: bool = False
) -> dict[str, object]:
    plan = _content(
        {
            "format": PLAN_FORMAT,
            "id": id,
            "created_at": created_at,
            "request": request,
            "runtime": runtime,
            "isolated_registration": isolated_registration,
        }
    )
    return {**plan, "sha256": plan_fingerprint(plan)}


def validate_plan(plan: object) -> dict[str, object]:
    value = _object(plan, {"format", "id", "created_at", "request", "runtime", "isolated_registration", "sha256"})
    result = _content(value)
    fingerprint = _sha(value["sha256"])
    if fingerprint != plan_fingerprint(result):
        raise BusinessError("service_plan_changed", "Service plan fingerprint does not match its contents", status=409)
    return {**result, "sha256": fingerprint}


def startup(plan: object) -> dict[str, object]:
    value = validate_plan(plan)
    request = cast(dict[str, object], value["request"])
    runtime = cast(dict[str, object], value["runtime"])
    return {
        "format": STARTUP_FORMAT,
        **{
            key: request[key]
            for key in (
                "instance_id",
                "home",
                "working_directory",
                "host",
                "port",
                "external_url",
                "master_key_file",
                "stdout_logs",
            )
        },
        "service_plan_sha256": value["sha256"],
        "build_id": runtime["build_id"],
    }


def parse_startup(data: object) -> dict[str, object]:
    value = _object(
        data,
        {
            "format",
            "instance_id",
            "home",
            "working_directory",
            "host",
            "port",
            "external_url",
            "master_key_file",
            "stdout_logs",
            "service_plan_sha256",
            "build_id",
        },
    )
    if value["format"] != STARTUP_FORMAT or value["stdout_logs"] is not True:
        raise invalid()
    home = _text(value["home"])
    windows = re.match(r"^[A-Za-z]:", home) is not None
    host, port, origin = _endpoint(value["host"], value["port"], value["external_url"])
    return {
        "format": STARTUP_FORMAT,
        "instance_id": _uuid(value["instance_id"]),
        "home": _path(home, windows),
        "working_directory": _path(value["working_directory"], windows),
        "host": host,
        "port": port,
        "external_url": origin,
        "master_key_file": None if value["master_key_file"] is None else _path(value["master_key_file"], windows),
        "stdout_logs": True,
        "service_plan_sha256": _sha(value["service_plan_sha256"]),
        "build_id": _sha(value["build_id"]),
    }


def service_argv(plan: object) -> list[str]:
    value = validate_plan(plan)
    runtime = cast(dict[str, object], value["runtime"])
    request = cast(dict[str, object], value["request"])
    return [
        cast(str, runtime["python"]),
        "-I",
        "-u",
        "-m",
        "omni.composition.service_runner",
        "--deployment",
        cast(str, request["bundle_dir"]),
    ]


def manager_identity(plan: object) -> dict[str, object]:
    value = validate_plan(plan)
    request = cast(dict[str, object], value["request"])
    principal = cast(dict[str, object], request["principal"])
    target = cast(str, request["target"])
    identity = cast(str, request["instance_id"])
    if value["isolated_registration"]:
        # Recovery never reuses a native key that an unresolved old operation could still mutate.
        identity += "-" + cast(str, value["id"])
    system = target.endswith("-system")
    if target == "windows-user":
        return {
            "manager": "task_scheduler",
            "name": "OmniOperator-V2-" + identity,
            "scope": "user",
            "domain": principal["id"],
            "artifact": "service.xml",
            "installed_path": None,
        }
    if target.startswith("macos-"):
        name = "com.omnioperator.v2." + identity
        directory = (
            "/Library/LaunchDaemons" if system else posixpath.join(cast(str, principal["home"]), "Library/LaunchAgents")
        )
        return {
            "manager": "launchd",
            "name": name,
            "scope": "system" if system else "user",
            "domain": "system" if system else "gui/" + cast(str, principal["id"]),
            "artifact": "daemon.plist" if system else "agent.plist",
            "installed_path": posixpath.join(directory, name + ".plist"),
        }
    name = "omnioperator-v2-" + identity + ".service"
    directory = (
        "/etc/systemd/system" if system else posixpath.join(cast(str, principal["home"]), ".config/systemd/user")
    )
    return {
        "manager": "systemd",
        "name": name,
        "scope": "system" if system else "user",
        "domain": "system" if system else "user/" + cast(str, principal["id"]),
        "artifact": "omni.service",
        "installed_path": posixpath.join(directory, name),
    }


created = _created

sha = _sha
