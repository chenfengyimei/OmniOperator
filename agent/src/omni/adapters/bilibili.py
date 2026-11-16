"""Bounded Bilibili reads and a single approved UI publication; credentials never enter diagnostics."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, cast

import httpx

from ..application.ports import AsyncSecretPort, Record
from ..domain.bilibili import ADAPTER, API, connection, uid
from ..domain.contracts import JSON, BusinessError, Effect, InvocationContext, Outcome
from ..domain.delivery import DeliveryResult, OutboundMessage, text_fingerprint
from .diagnostics import Redactor
from .workers.client import WorkerClient

Payload = dict[str, object]


async def _response_body(response: httpx.Response, limit: int, label: str) -> bytearray:
    """Bound retained decoded bytes before appending; the response context owns closing."""
    body = bytearray()
    async for part in response.aiter_bytes():
        if len(part) > limit - len(body):
            raise BusinessError("bilibili_response_too_large", f"{label} exceeded its bounded read limit", status=502)
        body.extend(part)
    return body


def _download_lengths(response: httpx.Response, offset: int) -> tuple[int | None, int | None]:
    """Validate one identity-encoded full response or contiguous single byte range."""
    invalid = BusinessError("media_download_failed", "Media response range or length is invalid", status=502)
    if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
        raise invalid
    raw_length = response.headers.get("content-length")
    length = None
    if raw_length is not None:
        if not re.fullmatch(r"[0-9]{1,20}", raw_length):
            raise invalid
        length = int(raw_length)
    if response.status_code == 200:
        return length, length
    if (
        response.status_code != 206
        or response.headers.get("content-type", "").split(";")[0].strip().lower() == "multipart/byteranges"
    ):
        raise invalid
    match = re.fullmatch(
        r"bytes ([0-9]{1,20})-([0-9]{1,20})/([0-9]{1,20})",
        response.headers.get("content-range", "").strip(),
        flags=re.IGNORECASE,
    )
    if match is None:
        raise invalid
    start, end, total = map(int, match.groups())
    if start != offset or end < start or end >= total or (length is not None and length != end - start + 1):
        raise invalid
    return end - start + 1, total


class BilibiliGateway:
    identity = ADAPTER

    def __init__(
        self,
        credentials: AsyncSecretPort,
        redactor: Redactor,
        authorize: Callable[[OutboundMessage], Awaitable[Record]],
        *,
        client_factory: Callable[..., Any] | None = None,
        worker_factory: Callable[[], WorkerClient] | None = None,
    ) -> None:
        self._credentials, self._redactor, self._authorize = credentials, redactor, authorize
        self._client_factory, self._worker_factory = client_factory, worker_factory
        self._workers: set[WorkerClient] = set()
        self._reads: set[asyncio.Task[Any]] = set()
        self._slots = asyncio.Semaphore(4)
        self._closing = False

    def account_key(self, configuration: Record) -> str:
        return cast(str, connection(cast(Payload, configuration))["account"])

    async def _cookies(self, value: Payload) -> dict[str, str]:
        raw = await self._credentials.get(cast(str, value["credential_ref"]))
        try:
            if not isinstance(raw, str) or len(raw.encode()) > 16384:
                raise ValueError("Missing bounded cookie object")
            cookies: dict[str, str] = json.loads(raw)
            if (
                not isinstance(cast(object, cookies), dict)
                or not 1 <= len(cookies) <= 32
                or not cookies.get("SESSDATA")
            ):
                raise ValueError("Missing session cookie")
            if any(
                not re.fullmatch(r"[A-Za-z0-9_]{1,64}", k)
                or not isinstance(cast(object, v), str)
                or not 1 <= len(v) <= 4096
                or any(ord(c) < 33 or ord(c) > 126 or c in ";," for c in v)
                for k, v in cookies.items()
            ):
                raise ValueError("Invalid cookie")
        except (ValueError, TypeError, UnicodeError) as exc:
            raise BusinessError(
                "bilibili_credential_invalid",
                "Credential must be a bounded JSON cookie object containing SESSDATA",
                status=422,
            ) from exc
        for secret in cookies.values():
            self._redactor.register(secret)
        if "DedeUserID" in cookies and uid(cookies["DedeUserID"]) != cast(str, value["account"]):
            raise BusinessError("bilibili_account_mismatch", "Cookie identity differs from the configured account")
        return cookies

    def _client(self, cookies: dict[str, str]) -> Any:
        factory = self._client_factory or httpx.AsyncClient
        return factory(
            base_url=API,
            cookies=cookies,
            follow_redirects=False,
            trust_env=False,
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0 OmniOperator/2", "Referer": "https://www.bilibili.com/"},
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=0),
        )

    @staticmethod
    async def _get(client: Any, path: str, params: Payload | None = None) -> Payload:
        async with client.stream("GET", path, params=params or {}) as response:
            if response.status_code != 200 or "json" not in response.headers.get("content-type", "").lower():
                raise BusinessError(
                    "bilibili_read_unavailable", "Bilibili read was denied, redirected or unavailable", status=503
                )
            body = await _response_body(response, 524288, "Bilibili response")
            try:
                value: dict[str, Any] = json.loads(body)
            except (ValueError, RecursionError) as exc:
                raise BusinessError(
                    "bilibili_response_invalid", "Bilibili response was not valid JSON", status=502
                ) from exc
            if (
                not isinstance(cast(object, value), dict)
                or type(value.get("code")) is not int
                or value["code"] != 0
                or not isinstance(value.get("data"), dict)
            ):
                raise BusinessError(
                    "bilibili_read_rejected", "Bilibili denied the request or changed its response format", status=502
                )
            return cast(Payload, value["data"])

    async def _login_nav(self, client: Any, account: str) -> Payload:
        nav = await self._get(client, "/x/web-interface/nav")
        if nav.get("isLogin") is not True or uid(nav.get("mid")) != account:
            raise BusinessError("bilibili_account_mismatch", "Bilibili login identity does not match configuration")
        return nav

    async def _login(self, client: Any, account: str) -> Payload:
        nav = await self._login_nav(client, account)
        return {"uid": account, "name": str(nav.get("uname", ""))[:100], "logged_in": True}

    async def _subtitle(self, client: Any, nav: Payload, video: Payload) -> Payload:
        """Official subtitle text via the WBI-signed player endpoint; empty means none exists."""
        from ..domain.bilibili import sign_wbi, subtitle_url, wbi_keys

        img, sub = wbi_keys(nav)
        player = await self._get(
            client,
            "/x/player/wbi/v2",
            sign_wbi({"bvid": video["bvid"], "cid": video["cid"]}, img, sub, int(time.time())),
        )
        subtitle = player.get("subtitle")
        if subtitle is not None and not isinstance(subtitle, dict):
            raise BusinessError("bilibili_response_invalid", "Subtitle list changed format", status=502)
        items = cast(Payload, subtitle if subtitle is not None else {}).get("subtitles")
        if items is not None and not isinstance(items, list):
            raise BusinessError("bilibili_response_invalid", "Subtitle list changed format", status=502)
        subtitles = cast(list[Payload], items if items is not None else [])
        if len(subtitles) > 32 or any(
            not isinstance(cast(object, item), dict) or not isinstance(item.get("lan", ""), str) for item in subtitles
        ):
            raise BusinessError("bilibili_response_invalid", "Subtitle list changed format", status=502)
        picked = next(
            (s for s in subtitles if cast(str, s.get("lan", "")).startswith("zh")),
            subtitles[0] if subtitles else None,
        )
        if picked is None:
            return {"language": "", "text": ""}
        if not isinstance(picked.get("subtitle_url"), str) or not picked["subtitle_url"]:
            raise BusinessError("bilibili_response_invalid", "Subtitle URL field is missing or invalid", status=502)
        url = subtitle_url(picked.get("subtitle_url"))
        async with client.stream("GET", url) as response:
            if response.status_code != 200 or "json" not in response.headers.get("content-type", "").lower():
                raise BusinessError(
                    "bilibili_read_unavailable", "Subtitle download was denied or unavailable", status=503
                )
            content = await _response_body(response, 2 * 1024 * 1024, "Subtitle")
        try:
            body = json.loads(content)
        except (ValueError, RecursionError) as exc:
            raise BusinessError(
                "bilibili_response_invalid", "Subtitle response was not valid JSON", status=502
            ) from exc
        lines = cast(list[Payload] | None, cast(Payload, body).get("body") if isinstance(body, dict) else None)
        if not isinstance(lines, list) or len(lines) > 20000:
            raise BusinessError("bilibili_response_invalid", "Subtitle body changed format", status=502)
        if any(not isinstance(cast(object, line), dict) or not isinstance(line.get("content"), str) for line in lines):
            raise BusinessError("bilibili_response_invalid", "Subtitle body changed format", status=502)
        text = "\n".join(cast(str, line["content"]) for line in lines).strip()
        if not text or len(text) > 60000:
            raise BusinessError("bilibili_response_invalid", "Subtitle body is empty or exceeds its bound", status=502)
        language = cast(str, picked.get("lan", ""))[:16]
        try:
            text.encode("utf-8")
            language.encode("utf-8")
        except UnicodeError as exc:
            raise BusinessError("bilibili_response_invalid", "Subtitle text is not valid Unicode", status=502) from exc
        return {"language": language, "text": text}

    async def _streams(self, client: Any, video: Payload) -> Payload:
        """Lowest-bitrate audio and decodable video direct URLs with bounded sizes."""
        from ..domain.bilibili import stream_url

        bvid, cid = video["bvid"], video["cid"]
        audio: Payload = {}
        play = await self._get(
            client, "/x/player/playurl", {"bvid": bvid, "cid": cid, "qn": 64, "fnval": 16, "fnver": 0, "fourk": 0}
        )
        tracks: list[Payload] = cast(list[Payload], cast(Payload, play.get("dash") or {}).get("audio") or [])
        if isinstance(cast(object, tracks), list) and tracks:
            best = min(
                (t for t in tracks if isinstance(cast(object, t), dict)),
                key=lambda t: int(cast(int, t.get("bandwidth") or 10**9)),
                default=None,
            )
            if best is not None:
                url = str(best.get("baseUrl") or best.get("base_url") or "")
                if not url:
                    backups: list[object] = cast(list[object], best.get("backupUrl") or best.get("backup_url") or [])
                    url = str(backups[0]) if isinstance(cast(object, backups), list) and backups else ""
                if url:
                    audio = {
                        "url": stream_url(url),
                        "bytes": int(cast(int, best.get("size") or 0)),
                    }
        if not audio:
            merged: list[Payload] = cast(list[Payload], play.get("durl") or [])
            if isinstance(cast(object, merged), list) and merged and isinstance(cast(object, merged[0]), dict):
                url = str(merged[0].get("url") or "")
                if url:
                    audio = {"url": stream_url(url), "bytes": int(cast(int, merged[0].get("size") or 0))}
        video_stream: Payload = {}
        merged_play = await self._get(
            client, "/x/player/playurl", {"bvid": bvid, "cid": cid, "qn": 32, "fnval": 0, "fnver": 0, "fourk": 0}
        )
        merged = cast(list[Payload], merged_play.get("durl") or [])
        if isinstance(cast(object, merged), list) and merged and isinstance(cast(object, merged[0]), dict):
            url = str(merged[0].get("url") or "")
            if url:
                video_stream = {"url": stream_url(url), "bytes": int(cast(int, merged[0].get("size") or 0))}
        if not video_stream:
            tracks = cast(list[Payload], cast(Payload, play.get("dash") or {}).get("video") or [])
            if isinstance(cast(object, tracks), list) and tracks:
                best = min(
                    (t for t in tracks if isinstance(cast(object, t), dict)),
                    key=lambda t: int(cast(int, t.get("bandwidth") or 10**9)),
                    default=None,
                )
                if best is not None:
                    url = str(best.get("baseUrl") or best.get("base_url") or "")
                    if not url:
                        backups = cast(list[object], best.get("backupUrl") or best.get("backup_url") or [])
                        url = str(backups[0]) if isinstance(cast(object, backups), list) and backups else ""
                    if url:
                        video_stream = {"url": stream_url(url), "bytes": int(cast(int, best.get("size") or 0))}
        if not audio and not video_stream:
            raise BusinessError(
                "bilibili_read_rejected", "This video exposes no decodable audio or video stream", status=502
            )
        return {"audio": audio or None, "video": video_stream or None}

    async def read(self, configuration: Mapping[str, object], operation: str, target: str, limit: int = 10) -> Payload:
        return await self._read(configuration, operation, target, limit)

    async def read_source(
        self, configuration: Mapping[str, object], operation: str, source: Mapping[str, object]
    ) -> Payload:
        if operation not in {"subtitle", "streams"}:
            raise BusinessError("bilibili_read_invalid", "Source reads require subtitle or streams", status=422)
        target = source.get("bvid")
        if not isinstance(target, str) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", target):
            raise BusinessError("bilibili_read_invalid", "Source reads require a registered BV identity", status=422)
        expected: Payload = {"aid": uid(source.get("aid")), "cid": uid(source.get("cid"))}
        return await self._read(configuration, operation, target, expected_source=expected)

    async def _read(
        self,
        configuration: Mapping[str, object],
        operation: str,
        target: str,
        limit: int = 10,
        *,
        expected_source: Payload | None = None,
    ) -> Payload:
        if self._closing or len(self._reads) >= 8:
            raise BusinessError("bilibili_busy", "Bilibili reader is draining or busy", status=503)
        owner = asyncio.current_task()
        assert owner is not None
        self._reads.add(owner)
        try:
            value = connection(cast(Payload, configuration))
            if (
                operation not in {"account", "video", "comments", "replies", "subtitle", "streams"}
                or type(limit) is not int
                or not 1 <= limit <= 20
            ):
                raise BusinessError(
                    "bilibili_read_invalid",
                    "Choose account/video/comments/replies/subtitle/streams and a limit of 1–20",
                    status=422,
                )
            root = ""
            if operation == "replies":
                target, separator, root = target.partition(":")
                if not separator:
                    raise BusinessError("bilibili_read_invalid", "Thread reads require BV:root-comment-id", status=422)
                root = uid(root)
            if operation != "account" and not re.fullmatch(r"BV[0-9A-Za-z]{10}", target):
                raise BusinessError("bilibili_read_invalid", "Read targets require an exact BV identifier", status=422)
            async with self._slots, asyncio.timeout(20):
                cookies = await self._cookies(value)
                async with self._client(cookies) as client:
                    nav = await self._login_nav(client, cast(str, value["account"]))
                    if operation == "account":
                        return {
                            "uid": cast(str, value["account"]),
                            "name": str(nav.get("uname", ""))[:100],
                            "logged_in": True,
                        }
                    video = await self._get(client, "/x/web-interface/view", {"bvid": target})
                    if video.get("bvid") != target:
                        raise BusinessError("bilibili_source_changed", "Video identity changed")
                    if expected_source is not None and any(
                        str(video.get(key, "")) != expected_source[key] for key in ("aid", "cid")
                    ):
                        raise BusinessError(
                            "bilibili_source_changed", "Registered video content identity changed", status=409
                        )
                    if operation == "video":
                        return {
                            "bvid": target,
                            "aid": uid(video.get("aid")),
                            "cid": uid(video.get("cid")),
                            "title": str(video.get("title", ""))[:300],
                            "description": str(video.get("desc", ""))[:8000],
                            "owner": str(cast(Payload, video.get("owner", {})).get("name", ""))[:100],
                            "duration_s": video.get("duration") if type(video.get("duration")) is int else None,
                        }
                    if operation == "subtitle":
                        return await self._subtitle(client, nav, {"bvid": target, "cid": uid(video.get("cid"))})
                    if operation == "streams":
                        return await self._streams(client, {"bvid": target, "cid": uid(video.get("cid"))})
                    comments = await self._get(
                        client,
                        "/x/v2/reply/reply" if root else "/x/v2/reply",
                        {
                            "type": 1,
                            "oid": uid(video.get("aid")),
                            "sort": 0,
                            "pn": 1,
                            "ps": limit,
                            **({"root": root} if root else {}),
                        },
                    )
                    if "replies" not in comments:
                        raise BusinessError("bilibili_response_invalid", "Comment list field is missing", status=502)
                    rows = cast(list[Payload] | None, comments.get("replies"))
                    if rows is None:
                        rows = []
                    if not isinstance(cast(object, rows), list) or len(rows) > 100:
                        raise BusinessError("bilibili_response_invalid", "Comment list changed format", status=502)
                    if any(
                        not isinstance(cast(object, r), dict)
                        or not isinstance(r.get("member"), dict)
                        or not isinstance(r.get("content"), dict)
                        or not isinstance(cast(Payload, r["content"]).get("message"), str)
                        for r in rows[:limit]
                    ):
                        raise BusinessError("bilibili_response_invalid", "Comment records changed format", status=502)
                    return {
                        "bvid": target,
                        "items": [
                            {
                                "id": uid(r.get("rpid")),
                                "author_uid": uid(r.get("mid")),
                                "name": str(cast(Payload, r.get("member", {})).get("uname", ""))[:100],
                                "text": str(cast(Payload, r.get("content", {})).get("message", ""))[:1000],
                            }
                            for r in rows[:limit]
                        ],
                        "has_more": len(rows) >= limit,
                    }
        except (httpx.HTTPError, TimeoutError) as exc:
            raise BusinessError(
                "bilibili_read_unavailable", "Bilibili read timed out or disconnected", status=503
            ) from exc
        finally:
            self._reads.discard(owner)

    async def download(
        self,
        configuration: Record,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
        budget_s: float,
        before_dispatch: Callable[[], Awaitable[None]] | None = None,
    ) -> Record:
        """Bounded resumable media download with the connection's cookies and Referer.

        CDN streams may drop mid-transfer (observed on a real machine): each
        retry resumes from the bytes already on disk, and a server that ignores
        Range (200 instead of 206) restarts from scratch. Credentials stay in
        this adapter and never enter diagnostics.
        """
        from ..domain.bilibili import stream_url

        if self._closing or len(self._reads) >= 8:
            raise BusinessError("bilibili_busy", "Bilibili reader is draining or busy", status=503)
        owner = asyncio.current_task()
        assert owner is not None
        self._reads.add(owner)
        last_error: Exception | None = None
        total_size: int | None = None
        validator: str | None = None
        try:
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            async with self._slots, asyncio.timeout(budget_s):
                checked = stream_url(url)
                value = connection(cast(Payload, configuration))
                cookies = await self._cookies(value)
                factory = self._client_factory or httpx.AsyncClient
                async with factory(
                    base_url=API,
                    cookies=cookies,
                    follow_redirects=False,
                    trust_env=False,
                    timeout=30,
                    headers={
                        "User-Agent": "Mozilla/5.0 OmniOperator/2",
                        "Referer": "https://www.bilibili.com/",
                        "Accept-Encoding": "identity",
                    },
                    limits=httpx.Limits(max_connections=2, max_keepalive_connections=0),
                ) as client:
                    for _attempt in range(3):
                        try:
                            have = destination.stat().st_size if destination.exists() else 0
                            if have > max_bytes:
                                raise BusinessError(
                                    "media_download_too_large", "Media partial exceeds its size bound", status=413
                                )
                            headers = {"Range": f"bytes={have}-"} if have else {}
                            if have and validator is not None:
                                headers["If-Range"] = validator
                            if before_dispatch is not None:
                                await before_dispatch()
                            async with client.stream("GET", checked, headers=headers) as response:
                                response.raise_for_status()
                                resumed = response.status_code == 206
                                expected, total = _download_lengths(response, have if resumed else 0)
                                if resumed and (
                                    (total_size is not None and total != total_size)
                                    or (validator is not None and response.headers.get("etag") != validator)
                                ):
                                    raise BusinessError(
                                        "media_download_failed",
                                        "Media representation changed during resume",
                                        status=502,
                                    )
                                if total is not None and total > max_bytes:
                                    raise BusinessError(
                                        "media_download_too_large",
                                        "Media stream exceeds its declared size bound",
                                        status=413,
                                    )
                                if not resumed:
                                    have = 0
                                    validator = None
                                if validator is None:
                                    tag = response.headers.get("etag", "").strip()
                                    validator = tag if re.fullmatch(r'"[\x21\x23-\x7e\x80-\xff]*"', tag) else None
                                total_size = total
                                received = 0
                                with destination.open("ab" if resumed else "wb") as handle:
                                    async for chunk in response.aiter_bytes(1 << 20):
                                        if have + len(chunk) > max_bytes:
                                            raise BusinessError(
                                                "media_download_too_large",
                                                "Media stream exceeds its size bound",
                                                status=413,
                                            )
                                        if expected is not None and received + len(chunk) > expected:
                                            raise BusinessError(
                                                "media_download_failed",
                                                "Media response exceeds its declared length",
                                                status=502,
                                            )
                                        handle.write(chunk)
                                        have += len(chunk)
                                        received += len(chunk)
                                if expected is not None and received != expected:
                                    raise httpx.RemoteProtocolError("Incomplete media response")
                            if total_size is not None and have < total_size:
                                continue
                            if not destination.exists() or destination.stat().st_size == 0:
                                raise BusinessError(
                                    "media_download_failed", "Media download produced an empty file", status=502
                                )
                            return {"bytes": destination.stat().st_size, "resumed": resumed}
                        except (httpx.HTTPError, TimeoutError) as exc:
                            last_error = exc
                raise BusinessError(
                    "media_download_failed", "Media download failed after its bounded retries", status=502
                ) from last_error
        except asyncio.CancelledError:
            raise
        except BusinessError:
            raise
        except Exception as exc:  # noqa: BLE001 - never emit cookie values or CDN URLs
            raise BusinessError("media_download_failed", "Media download failed", status=502) from exc
        finally:
            self._reads.discard(owner)

    async def send(self, message: OutboundMessage) -> DeliveryResult:
        worker = None
        dispatched = False
        try:
            if self._closing or len(self._workers) >= 4:
                return DeliveryResult("not_sent", message.fingerprint, error_code="bilibili_busy")
            value = connection(message.configuration)
            if cast(str, value["account"]) != message.account_key:
                raise BusinessError("bilibili_account_mismatch", "Approved Bilibili sender changed")
            spec = await self._authorize(message)
            async with asyncio.timeout(25):
                cookies = await self._cookies(value)
                async with self._client(cookies) as client:
                    await self._login(client, cast(str, value["account"]))
                    if spec["kind"] == "comment":
                        from urllib.parse import urlsplit

                        bvid = urlsplit(cast(str, spec["target"])).path.rstrip("/").split("/")[-1]
                        video = await self._get(client, "/x/web-interface/view", {"bvid": bvid})
                        if video.get("bvid") != bvid:
                            raise BusinessError("bilibili_source_changed", "Publication video identity changed")
                        spec["remote_target_id"] = uid(video.get("aid"))
                    else:
                        spec["remote_target_id"] = spec["target_key"]
                if (await self._authorize(message))["authority"] != spec["authority"]:
                    raise BusinessError("bilibili_credential_changed", "Cookie authority changed while connecting")
                worker = (
                    self._worker_factory()
                    if self._worker_factory
                    else WorkerClient(
                        "bilibili",
                        tool_writes={"bilibili_open": False, "bilibili_fill": True, "bilibili_commit": True},
                        argv=[sys.executable, "-m", "omni.adapters.workers.bilibili"],
                        forbid_artifacts=True,
                    )
                )
                self._workers.add(worker)
                context = InvocationContext(
                    cast(str, spec["agent_id"]),
                    cast(str, spec["run_id"]),
                    cast(str, spec["session_id"]),
                    int(cast(int, spec["policy_revision"])),
                    "",
                )

                async def guard() -> None:
                    current = await self._authorize(message)
                    if self._closing or current["authority"] != spec["authority"]:
                        raise BusinessError(
                            "bilibili_dispatch_revoked", "Publication was revoked before its next operation"
                        )

                opened = await worker.invoke(
                    "bilibili_open",
                    {
                        "kind": spec["kind"],
                        "headless": spec.get("headless", True),
                        "url": cast(str, spec["target"]),
                        "target_key": spec["target_key"],
                        "remote_target_id": spec["remote_target_id"],
                        "account": cast(str, value["account"]),
                        "form": spec["form"],
                        "cookies": cast(dict[str, JSON], cookies),
                    },
                    operation_id=message.token,
                    context=context,
                    before_dispatch=guard,
                )
                if opened.status != Outcome.OK:
                    return DeliveryResult("not_sent", message.fingerprint, error_code="bilibili_form_unavailable")
                filled = await worker.invoke(
                    "bilibili_fill",
                    {"observation": opened.data, "text": message.text, "mentions": spec["mentions"]},
                    operation_id=message.token,
                    context=context,
                    before_dispatch=guard,
                )
                if filled.status != Outcome.OK:
                    return DeliveryResult("not_sent", message.fingerprint, error_code="bilibili_form_rejected")
                dispatched = True
                result = await worker.invoke(
                    "bilibili_commit",
                    {"observation": filled.data},
                    operation_id=message.token,
                    context=context,
                    before_dispatch=guard,
                )
                proof = result.data
                receipt_id = proof.get("record_id")
                expected = {
                    "account": cast(str, value["account"]),
                    "target": cast(str, spec["target"]),
                    "body_hash": text_fingerprint(message.text),
                    "mentions": [m["uid"] for m in cast(list[Record], spec["mentions"])],
                }
                if (
                    result.status == Outcome.OK
                    and result.effect == Effect.APPLIED
                    and isinstance(cast(object, proof), dict)
                    and all(proof.get(k) == v for k, v in expected.items())
                    and isinstance(receipt_id, str)
                    and 1 <= len(receipt_id) <= 128
                ):
                    return DeliveryResult("sent", message.fingerprint, receipt_id=receipt_id)
                if result.effect == Effect.NONE:
                    return DeliveryResult("not_sent", message.fingerprint, error_code="bilibili_commit_rejected")
                return DeliveryResult("unknown", message.fingerprint, error_code="bilibili_publish_unknown")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- never emit cookie values, page bodies or provider exceptions
            return DeliveryResult(
                "unknown" if dispatched else "not_sent",
                message.fingerprint,
                error_code="bilibili_publish_unknown" if dispatched else "bilibili_not_dispatched",
            )
        finally:
            if worker is not None:
                # The actual send retains its worker and account lease until native cleanup is proven.
                while not await worker.close(5):
                    await asyncio.sleep(0.25)
                self._workers.discard(worker)

    async def close(self, timeout_s: float = 30) -> bool:
        self._closing = True
        if self._reads:
            await asyncio.wait(tuple(self._reads), timeout=max(0, timeout_s))
        return not self._reads and not self._workers
