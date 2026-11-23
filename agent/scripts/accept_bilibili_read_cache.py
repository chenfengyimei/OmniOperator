#!/usr/bin/env python3
"""Acceptance for the merged Bilibili read cache: real account (read-only) or offline self-test.

The real mode creates a brand-new instance home, loads an owner-provided cookie JSON through a
read-only SecretStore, then exercises only ``BilibiliService.read``. It never publishes, sends or
mutates the Bilibili account. Cookie values are registered with the gateway redactor and never
printed or written to the evidence file.

Checks: video/whois cache hits; 24h TTL boundary with a controlled clock; credential rotation
(authority generation change must miss and purge the old bucket); risk pause ordering (a paused
account must not be served from cache and must not reach the gateway); the never-cached endpoints
(history / dynamic_list / msgfeed) must call the gateway on every read and add no cache entries.

Usage:
  # offline harness self-test (no network, fake gateway)
  uv run --no-sync python agent/scripts/accept_bilibili_read_cache.py --self-test \
      --home <new-empty-dir> --output <new-empty-dir>

  # real isolated account (cookie JSON stays outside the repository)
  uv run --no-sync python agent/scripts/accept_bilibili_read_cache.py \
      --home <new-empty-dir> --output <new-empty-dir> \
      --cookie-file <path-to-cookie.json> --account <uid> [--video BV...]

Exit code is 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import update

from omni.adapters.persistence import schema as s
from omni.composition.instance import open_instance
from omni.domain.bilibili import ADAPTER, READ_CACHE_TTL_S
from omni.domain.contracts import BusinessError, ModelReply

COOKIE_MAX_BYTES = 16 * 1024
CHECK_OPS = ("history", "dynamic_list", "msgfeed")


class StubModel:
    """No model call is made; the instance only needs a ModelPort object."""

    async def complete(self, messages: Any, tools: Any, settings: Any) -> ModelReply:
        return ModelReply("acceptance stub", cost_micro=0)


class FileSecrets:
    """Read-only cookie store: one reference, one bounded JSON object, never written."""

    def __init__(self, cookies: dict[str, str]) -> None:
        self._raw = json.dumps(cookies, ensure_ascii=False, separators=(",", ":"))
        FileSecrets._validate(self._raw)

    @staticmethod
    def _validate(raw: str) -> None:
        if len(raw.encode()) > COOKIE_MAX_BYTES:
            raise SystemExit("cookie JSON exceeds the bounded acceptance size")
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise SystemExit("cookie file is not valid JSON") from exc
        if not isinstance(value, dict) or not value.get("SESSDATA"):
            raise SystemExit("cookie file must be a JSON object containing SESSDATA")

    def get(self, reference: str) -> str:
        return self._raw

    def set(self, reference: str, value: str) -> None:
        raise RuntimeError("acceptance secret store is read-only")

    def delete(self, reference: str) -> None:
        raise RuntimeError("acceptance secret store is read-only")


class FakeGateway:
    """Offline gateway for --self-test; counts reads and returns bounded fixture payloads."""

    identity = ADAPTER

    def __init__(self) -> None:
        self.reads = 0

    def account_key(self, configuration: Any) -> str:
        return str(configuration["account"])

    async def read(self, configuration: Any, operation: str, target: str, limit: int = 10) -> dict[str, object]:
        self.reads += 1
        if operation == "video":
            return {
                "bvid": target,
                "aid": 1,
                "cid": 2,
                "title": "fixture",
                "description": "fixture",
                "owner": "fixture",
                "duration_s": 1,
            }
        if operation == "whois":
            return {"mid": target, "name": "fixture", "sex": "", "sign": "", "level": None}
        if operation == "popular":
            return {"items": [{"bvid": "BV1xx411c7mD", "title": "fixture", "author": "fixture"}], "has_more": False}
        return {"items": [], "has_more": False}

    async def send(self, message: Any) -> Any:
        raise RuntimeError("acceptance never sends")

    async def close(self, timeout_s: float = 30) -> bool:
        return True


def _iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="seconds")


def _load_cookie(path: Path) -> dict[str, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"cannot read cookie file: {path}") from exc
    FileSecrets._validate(raw)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SystemExit("cookie file must be a JSON object")
    cookies = {str(k): str(v) for k, v in value.items() if isinstance(k, str) and isinstance(v, str) and v}
    if not cookies.get("SESSDATA"):
        raise SystemExit("cookie file must contain SESSDATA")
    return cookies


def _counted(gateway: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    original = gateway.read

    async def read(configuration: Any, operation: str, target: str, limit: int = 10) -> Any:
        counts[operation] = counts.get(operation, 0) + 1
        return await original(configuration, operation, target, limit)

    gateway.read = read
    return counts


async def _run(args: argparse.Namespace) -> int:
    home = Path(args.home).resolve()
    output = Path(args.output).resolve()
    if home.exists() and any(home.iterdir()):
        raise SystemExit(f"instance home must be new or empty: {home}")
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"output directory must be new or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    cookies = {} if args.self_test else _load_cookie(Path(args.cookie_file))
    account = "456" if args.self_test else str(args.account)
    if not account.isdigit() or not 1 <= len(account) <= 19:
        raise SystemExit("--account must be a positive decimal Bilibili UID")
    reference = f"bili-cache-acceptance/{uuid.uuid4()}"

    started = dt.datetime.now().astimezone()
    gateway: Any = FakeGateway() if args.self_test else None
    options: dict[str, Any] = {
        "model": StubModel(),
        "capabilities": (),
        "credential_backend": FileSecrets(cookies or {"SESSDATA": "self-test-fixture"}),
    }
    if gateway is not None:
        options["bilibili_backend"] = gateway
    checks: list[dict[str, Any]] = []
    live_errors: list[dict[str, str]] = []
    instance = await open_instance(home, **options)
    try:
        agent = str((await instance.application.query("agents", ""))[0]["id"])
        await instance._database.write(lambda u: u.credential_register(reference))
        config = await instance.configurations.create(
            agent,
            "channel",
            "bili-cache-acceptance",
            True,
            {"driver": "bilibili", "account": account, "endpoint": "", "credential_ref": reference},
        )
        service = instance.bilibili
        clock = [1000.0]
        service._clock = lambda: clock[0]
        counts = _counted(service._gateway)

        bv = args.video
        if not bv:
            popular = await service.read(agent, config["id"], "popular")
            items = popular.get("items")
            if not isinstance(items, list) or not items or not isinstance(items[0], dict):
                raise SystemExit("popular feed returned no candidate video; pass --video BV...")
            bv = str(items[0]["bvid"])

        def record(check: str, ok: bool, **detail: Any) -> None:
            checks.append({"check": check, "ok": bool(ok), "at": _iso(dt.datetime.now().astimezone()), **detail})

        async def read(operation: str, target: str = "") -> dict[str, object]:
            return await service.read(agent, config["id"], operation, target)

        # 1) video/whois: first read misses, second read is served from the cache.
        first_video = await read("video", bv)
        second_video = await read("video", bv)
        record(
            "video_cache_hit",
            counts.get("video") == 1 and first_video == second_video,
            calls=counts.get("video"),
        )
        first_whois = await read("whois", account)
        second_whois = await read("whois", account)
        record(
            "whois_cache_hit",
            counts.get("whois") == 1 and first_whois == second_whois,
            calls=counts.get("whois"),
        )

        # 2) credential rotation: a new authority generation must miss and purge the old bucket.
        before_rotation = service.health()["read_cache_entries"]
        await instance._database.write(
            lambda u: u.connection.execute(
                update(s.credential_references).values(revision=s.credential_references.c.revision + 1)
            )
        )
        await read("video", bv)
        await read("whois", account)
        after_rotation = service.health()["read_cache_entries"]
        record(
            "credential_rotation_misses",
            counts.get("video") == 2 and counts.get("whois") == 2 and after_rotation == 2,
            video_calls=counts.get("video"),
            whois_calls=counts.get("whois"),
            entries_before=before_rotation,
            entries_after=after_rotation,
        )

        # 3) risk pause gates before the cache and before the gateway.
        await instance._database.write(lambda u: u.bilibili_risk_pause(agent, config["id"], 12015))
        paused_code = ""
        try:
            await read("video", bv)
        except BusinessError as exc:
            paused_code = exc.code
        record(
            "risk_pause_precedes_cache",
            paused_code == "bilibili_risk_paused" and counts.get("video") == 2,
            error_code=paused_code,
            calls=counts.get("video"),
            synthetic_pause=True,
        )
        await service.resume(agent, config["id"])
        await read("video", bv)
        record("cache_resumes_after_owner_resume", counts.get("video") == 2, calls=counts.get("video"))

        # 4) never-cached endpoints call the gateway on every read and add no cache entries.
        entries_before_live = service.health()["read_cache_entries"]
        for operation in CHECK_OPS:
            before = counts.get(operation, 0)
            target = {"dynamic_list": account, "msgfeed": "reply"}.get(operation, "")
            try:
                await read(operation, target)
                await read(operation, target)
            except BusinessError as exc:
                live_errors.append({"operation": operation, "error_code": exc.code})
            record(
                f"{operation}_always_live",
                counts.get(operation, 0) - before == 2,
                calls=counts.get(operation, 0) - before,
            )
        record(
            "never_cached_ops_add_no_entries",
            service.health()["read_cache_entries"] == entries_before_live,
            entries=entries_before_live,
        )

        # 5) 24h TTL: hit at 23:59:59, miss at exactly 24:00:00 (controlled clock, not a real 24h wait).
        clock[0] += READ_CACHE_TTL_S["video"] - 1
        await read("video", bv)
        near_code = counts.get("video")
        clock[0] += 1
        await read("video", bv)
        record(
            "video_ttl_24h_boundary",
            near_code == 2 and counts.get("video") == 3,
            calls_at_23h59m59s=2,
            calls_after_24h=counts.get("video"),
            ttl_seconds=READ_CACHE_TTL_S["video"],
            controlled_clock=True,
        )
        cache_health = service.health()
    finally:
        closed = await instance.close()

    finished = dt.datetime.now().astimezone()
    passed = all(item["ok"] for item in checks) and bool(closed["complete"])
    result = {
        "schema": 1,
        "mode": "self_test" if args.self_test else "real_account",
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "elapsed_seconds": round((finished - started).total_seconds(), 2),
        "timezone": str(started.tzinfo),
        "account": "***" + account[-2:],
        "video": bv,
        "whois_mid": account,
        "cacheable_operations": sorted(READ_CACHE_TTL_S),
        "ttl_seconds": READ_CACHE_TTL_S,
        "checks": checks,
        "live_errors": live_errors,
        "cache_after": {
            "entries": cache_health.get("read_cache_entries"),
            "bytes": cache_health.get("read_cache_bytes"),
        },
        "instance_home": str(home),
        "closed_complete": bool(closed["complete"]),
        "result": "passed" if passed else "failed",
        "limits": [
            "read-only: no publication/send/write to the Bilibili account",
            "TTL boundary uses a controlled clock; a real 24h wall-clock wait is not observed",
            "risk pause is synthetic (repository pause) to verify gate ordering, not a real risk-control trigger",
            "never-cached endpoints are proven by gateway call counts and cache-entry counts; remote data mutation is not performed",
        ],
    }
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {k: result[k] for k in ("mode", "result", "started_at", "finished_at", "cache_after")}, ensure_ascii=False
        )
    )
    for item in checks:
        if not item["ok"]:
            print("FAILED", item["check"], json.dumps(item, ensure_ascii=False))
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", required=True, help="new isolated instance home directory")
    parser.add_argument("--output", required=True, help="new directory for result.json")
    parser.add_argument("--cookie-file", help="JSON cookie file (SESSDATA etc.); required in real mode")
    parser.add_argument("--account", help="isolated account UID; required in real mode")
    parser.add_argument("--video", help="public BV to read; default uses the popular feed")
    parser.add_argument("--self-test", action="store_true", help="offline fake gateway harness self-test")
    args = parser.parse_args(argv)
    if not args.self_test and (not args.cookie_file or not args.account):
        parser.error("real mode requires --cookie-file and --account")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
