"""Proof-bound read-only V2 observation and append-only evidence; no V1, service control, models or database opens."""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import os
import secrets
import stat
import time
from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
import psutil

from ..domain import soak as soak_policy
from ..domain.contracts import BusinessError
from ..domain.soak import COUNTERS, MAX_SAMPLES, RESOURCES, assess, invalid, limits, number
from omni.adapters.platform.releases.dependency_bundle import no_links
from omni.adapters.platform.runtime.paths import PrivateFileLock
from omni.adapters.platform.runtime.permissions import assert_private, private_path
from omni.adapters.platform.runtime.runtime_identity import READINESS_FIELDS, read_private
from omni.adapters.platform.services.service_readiness import verify_proof

MAX_HTTP_BYTES = 256 * 1024
MAX_LINE_BYTES = 8192
MAX_CHILDREN = 64
FORMAT = "omni-v2-soak-1"
Payload = dict[str, Any]


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def collector_identity() -> str:
    digestor = hashlib.sha256()
    for path in (Path(__file__), Path(soak_policy.__file__)):
        digestor.update(path.read_bytes())
    return digestor.hexdigest()


def fixed_path(value: Path) -> Path:
    path = value.expanduser().absolute()
    if ".." in path.parts:
        raise invalid()
    no_links(path)
    return path


def origin(value: str) -> str:
    try:
        address = urlsplit(value)
        if (
            address.scheme != "http"
            or address.username is not None
            or address.password is not None
            or address.path not in {"", "/"}
            or address.query
            or address.fragment
            or address.hostname is None
            or "%" in address.hostname
            or not ipaddress.ip_address(address.hostname).is_loopback
            or address.port is None
            or not 1 <= address.port <= 65535
        ):
            raise ValueError
        host = ipaddress.ip_address(address.hostname)
        return f"http://{'[' + str(host) + ']' if host.version == 6 else host}:{address.port}"
    except ValueError:
        raise BusinessError("v2_soak_origin_invalid", "Use an explicit HTTP loopback IP and port", status=422) from None


def process_resources(identity: Mapping[str, object]) -> dict[str, int]:
    process = psutil.Process(int(cast(int, identity["pid"])))
    if process.create_time() != identity["process_created"]:
        raise invalid()
    children = process.children(recursive=True)
    if len(children) > MAX_CHILDREN:
        raise invalid()
    descendants = 0
    for child in children:
        created = child.create_time()
        descendants += child.memory_info().rss
        if child.create_time() != created or not child.is_running():
            raise invalid()
    descriptors = process.num_handles() if os.name == "nt" else process.num_fds()
    values = {
        "main_rss_bytes": process.memory_info().rss,
        "descendant_rss_sum_bytes": descendants,
        "threads": process.num_threads(),
        "descriptors": descriptors,
        "children": len(children),
    }
    if process.create_time() != identity["process_created"] or not process.is_running():
        raise invalid()
    return values


class LocalSampler:
    def __init__(
        self,
        home: Path,
        url: str,
        *,
        client: httpx.Client | None = None,
        resources: Callable[[Mapping[str, object]], dict[str, int]] = process_resources,
    ) -> None:
        self.home, self.url = fixed_path(home), origin(url)
        self.resources = resources
        self.mode = "native_v2_hmac_http" if client is None and resources is process_resources else "injected"
        self.client = client or httpx.Client(trust_env=False, follow_redirects=False, timeout=5)
        self._owned_client = client is None
        self.identity: Payload | None = None

    def close(self) -> None:
        if self._owned_client:
            self.client.close()

    def _get(self, path: str, *, token: str | None = None, query: dict[str, str] | None = None) -> Payload:
        deadline = time.monotonic() + 10
        headers = {"Accept-Encoding": "identity", **({"Authorization": "Bearer " + token} if token else {})}
        with self.client.stream(
            "GET", self.url + path, headers=headers, params=query, follow_redirects=False
        ) as response:
            if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                raise invalid()
            content = bytearray()
            for chunk in response.iter_raw():
                if len(content) + len(chunk) > MAX_HTTP_BYTES or time.monotonic() >= deadline:
                    raise invalid()
                content.extend(chunk)
        value = json.loads(content)
        if not isinstance(value, dict):
            raise invalid()
        return cast(Payload, value)

    def sample(self) -> Payload:
        try:
            control = self.home / "run"
            raw = read_private(control / "runtime.json")
            identity = json.loads(raw)
            if not isinstance(identity, dict):
                raise invalid()
            identity = cast(Payload, identity)
            if identity.get("format") != "omni-runtime-identity-1" or identity.get("state") != "running":
                raise invalid()
            pinned = {field: identity[field] for field in READINESS_FIELDS}
            if self.identity is not None and pinned != self.identity:
                raise invalid()
            key = read_private(control / "readiness.key", 32)
            challenge = secrets.token_hex(32)
            proof = self._get("/health/identity", query={"challenge": challenge})
            verified = verify_proof(proof, identity, challenge, key)
            if verified["ready"] is not True:
                raise invalid()
            token = read_private(control / "owner.token", 512).decode("ascii").strip()
            if not 32 <= len(token) <= 512 or any(
                character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for character in token
            ):
                raise invalid()
            metrics = self._get("/api/v1/system/metrics", token=token)
            host, runtime, persisted = metrics["host"], metrics["runtime"], metrics["persisted"]
            if (
                host["boot_id"] != pinned["boot_id"]
                or host["build_id"] != pinned["build_id"]
                or persisted["database"]["schema_version"] != pinned["schema_version"]
                or runtime.get("ready") is not True
                or runtime.get("closing") is not False
            ):
                raise invalid()
            resources = self.resources(pinned)
            resources["cleanup_pending"] = sum(
                int(number(runtime[key], integer=True))
                for key in (
                    "extension_cleanup_pending",
                    "mcp_cleanup_pending",
                    "desktop_portal_cleanup_pending",
                    "desktop_control_cleanup_pending",
                    "desktop_cleanup_pending",
                    "terminal_cleanup_pending",
                    "shutdown_residual_count",
                )
            )
            for field in RESOURCES:
                number(resources[field], integer=True)
            counters = {
                "failed_runs": int(number(persisted["runs"].get("failed", 0), integer=True)),
                "unknown_model_calls": int(number(persisted["model_calls"].get("unknown", 0), integer=True)),
            }
            if read_private(control / "runtime.json") != raw or read_private(control / "readiness.key", 32) != key:
                raise invalid()
            self.identity = pinned
            return {"ok": True, "resources": resources, "counters": counters, "code": None}
        except Exception:  # noqa: BLE001 -- monitor errors are fixed metadata, never native/HTTP exception text
            # Native/HTTP errors can contain paths, tokens or response bodies.
            # The observation artifact only stores this fixed metadata code.
            return {"ok": False, "resources": None, "counters": None, "code": "v2_soak_sample_failed"}


class SoakFiles:
    def __init__(
        self,
        folder: Path,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.folder = fixed_path(folder)
        self._clock, self._wall = clock, wall
        self.clock_mode = "native_monotonic_wall" if clock is time.monotonic and wall is time.time else "injected"
        self._tail: tuple[tuple[int, int, int, int], str, int, str] | None = None

    @contextmanager
    def _locked(self) -> Generator[None, None, None]:
        if not self.folder.is_dir() or not (self.folder / "manifest.json").is_file():
            raise invalid()
        lock = PrivateFileLock(self.folder / "writer.lock")
        lock.acquire()
        try:
            yield
        finally:
            lock.close()

    def _fingerprint(self) -> tuple[int, int, int, int]:
        path = self.folder / "samples.jsonl"
        no_links(path)
        assert_private(path)
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_SAMPLES * MAX_LINE_BYTES:
            raise invalid()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    def _read_json(self, name: str) -> Payload:
        value = json.loads(read_private(self.folder / name, 32768))
        if not isinstance(value, dict):
            raise invalid()
        return cast(Payload, value)

    def create(self, home: Path, sampler: LocalSampler, policy: object, interval_s: int) -> Payload:
        no_links(self.folder)
        selected_home = fixed_path(home)
        if selected_home != sampler.home:
            raise invalid()
        if self.folder.is_relative_to(selected_home) or selected_home.is_relative_to(self.folder):
            raise invalid()
        checked = limits(policy)
        if type(interval_s) is not int or not 10 <= interval_s <= 3600:
            raise invalid()
        first = sampler.sample()
        if first["ok"] is not True or sampler.identity is None:
            raise BusinessError("v2_soak_not_ready", "The selected local V2 boot is not ready", status=503)
        self.folder.mkdir(parents=True, mode=0o700)
        private_path(self.folder, directory=True)
        manifest = {
            "format": FORMAT,
            "identity": sampler.identity,
            "sampler_mode": sampler.mode,
            "clock_mode": self.clock_mode,
            "origin": sampler.url,
            "limits": checked,
            "interval_s": interval_s,
            "anchor_monotonic": self._clock(),
            "anchor_wall": self._wall(),
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "collector_sha256": collector_identity(),
        }
        for name, value in (("manifest.json", canonical(manifest)), ("samples.jsonl", b"")):
            with (self.folder / name).open("xb") as stream:
                private_path(self.folder / name)
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
        self.append(manifest, first)
        return manifest

    def manifest(self) -> Payload:
        value = self._read_json("manifest.json")
        if value.get("format") != FORMAT or value.get("collector_sha256") != collector_identity():
            raise invalid()
        limits(value["limits"])
        origin(value["origin"])
        return value

    def rows(self, manifest: Payload) -> Iterator[Payload]:
        path = self.folder / "samples.jsonl"
        no_links(path)
        previous = digest(manifest)
        before = self._fingerprint()
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino) != before[:2]:
                raise invalid()
            for sequence in range(1, MAX_SAMPLES + 2):
                line = stream.readline(MAX_LINE_BYTES + 1)
                if not line:
                    if self._fingerprint() != before:
                        raise invalid()
                    return
                if sequence > MAX_SAMPLES or len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
                    raise invalid()
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise invalid()
                row = cast(Payload, row)
                stored = row.pop("sha256", None)
                if set(row) != {
                    "sequence",
                    "previous",
                    "elapsed_s",
                    "wall_elapsed_s",
                    "ok",
                    "resources",
                    "counters",
                    "code",
                }:
                    raise invalid()
                if row.get("previous") != previous or row.get("sequence") != sequence or digest(row) != stored:
                    raise invalid()
                previous = stored
                if line != canonical({**row, "sha256": stored}) + b"\n":
                    raise invalid()
                yield row

    def append(self, manifest: Payload, sample: Payload) -> int:
        if set(sample) != {"ok", "resources", "counters", "code"} or type(sample["ok"]) is not bool:
            raise invalid()
        if sample["ok"]:
            if (
                set(sample["resources"]) != set(RESOURCES)
                or set(sample["counters"]) != set(COUNTERS)
                or sample["code"] is not None
            ):
                raise invalid()
            for value in (*sample["resources"].values(), *sample["counters"].values()):
                number(value, integer=True)
        elif sample != {"ok": False, "resources": None, "counters": None, "code": "v2_soak_sample_failed"}:
            raise invalid()
        with self._locked():
            if self.manifest() != manifest or manifest.get("clock_mode") != self.clock_mode:
                raise invalid()
            fingerprint = self._fingerprint()
            manifest_digest = digest(manifest)
            if self._tail is not None and self._tail[:2] == (fingerprint, manifest_digest):
                _, _, previous_sequence, previous = self._tail
                sequence = previous_sequence + 1
            else:
                last = None
                for last in self.rows(manifest):
                    pass
                sequence = 1 if last is None else last["sequence"] + 1
                previous = digest(manifest) if last is None else digest(last)
            if sequence > MAX_SAMPLES:
                raise invalid()
            row = {
                "sequence": sequence,
                "previous": previous,
                "elapsed_s": self._clock() - manifest["anchor_monotonic"],
                "wall_elapsed_s": self._wall() - manifest["anchor_wall"],
                **sample,
            }
            encoded = canonical({**row, "sha256": digest(row)}) + b"\n"
            if len(encoded) > MAX_LINE_BYTES:
                raise invalid()
            path = self.folder / "samples.jsonl"
            descriptor = os.open(
                path,
                os.O_WRONLY
                | os.O_APPEND
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            with os.fdopen(descriptor, "ab") as stream:
                info = os.fstat(stream.fileno())
                if (info.st_dev, info.st_ino) != fingerprint[:2] or info.st_size != fingerprint[2]:
                    raise invalid()
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._tail = self._fingerprint(), manifest_digest, sequence, digest(row)
            return sequence

    def report(self) -> dict[str, object]:
        with self._locked():
            manifest = self.manifest()
            return assess(manifest, self.rows(manifest))
