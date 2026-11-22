"""Explicit Bilibili form profiles, targets and mention ranges; no browser, network or storage."""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
from collections import OrderedDict
from collections.abc import Mapping
from typing import cast
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import BusinessError

ADAPTER = "bilibili-ui/v2"
RISK_CODES = frozenset({12015, -105, -101, 401})


def is_risk_code(value: object) -> bool:
    """Risk-control API codes that pause an account until the owner resumes (reference traffic.mjs set)."""
    return type(value) is int and value in RISK_CODES


API = "https://api.bilibili.com"
MAX_CONTENT = 2000

# Public community specification (bilibili-API-collect): WBI request signing
# reorders the concatenated img/sub keys through this fixed table.
_MIXIN_KEY_ENC_TAB = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]
_STRIP_CHARS = "!'()*"
SUBTITLE_HOSTS = ("aisubtitle.hdslb.com", "api.bilibili.com", "www.bilibili.com")
STREAM_HOST_SUFFIXES = (".bilivideo.com", ".hdslb.com", ".akamaized.net", ".bilibili.com")


def wbi_keys(nav: Mapping[str, object]) -> tuple[str, str]:
    """Extract the (img_key, sub_key) pair from a verified nav payload."""
    wbi = nav.get("wbi_img")
    if not isinstance(wbi, dict):
        raise BusinessError("bilibili_read_rejected", "Bilibili nav response changed format", status=502)
    fields = cast(dict[str, object], wbi)
    try:
        img = str(fields.get("img_url", "")).rsplit("/", 1)[-1].split(".")[0]
        sub = str(fields.get("sub_url", "")).rsplit("/", 1)[-1].split(".")[0]
    except (AttributeError, IndexError) as exc:
        raise BusinessError("bilibili_read_rejected", "Bilibili nav response changed format", status=502) from exc
    if not re.fullmatch(r"[0-9a-f]{32}", img) or not re.fullmatch(r"[0-9a-f]{32}", sub):
        raise BusinessError("bilibili_read_rejected", "Bilibili nav response changed format", status=502)
    return img, sub


def sign_wbi(params: Mapping[str, object], img_key: str, sub_key: str, wts: int) -> dict[str, object]:
    """Signed parameter set (wts + w_rid); the timestamp is injected by the adapter."""
    signed: dict[str, object] = {k: v for k, v in sorted(params.items())}
    signed["wts"] = wts
    filtered = {k: str(v).translate({ord(c): None for c in _STRIP_CHARS}) for k, v in signed.items()}
    query = urlencode(filtered, quote_via=quote)
    raw = img_key + sub_key
    mixin = "".join(raw[i] for i in _MIXIN_KEY_ENC_TAB)[:32]
    signed["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return signed


def subtitle_url(value: object) -> str:
    """Only fixed Bilibili subtitle CDN hosts are fetched; never an arbitrary URL."""
    if not isinstance(value, str) or not 1 <= len(value) <= 1000:
        raise BusinessError("bilibili_read_rejected", "Subtitle URL is not a bounded Bilibili location", status=502)
    url = "https:" + value if value.startswith("//") else value
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise BusinessError("bilibili_read_rejected", "Subtitle URL is not a bounded Bilibili location", status=502)
    if parsed.hostname not in SUBTITLE_HOSTS:
        raise BusinessError("bilibili_read_rejected", "Subtitle URL is not a bounded Bilibili location", status=502)
    return url


def stream_url(value: object) -> str:
    """Only known Bilibili media CDN hosts are downloaded; never an arbitrary URL."""
    if not isinstance(value, str) or not 1 <= len(value) <= 2000:
        raise BusinessError("bilibili_read_rejected", "Stream URL is not a bounded Bilibili location", status=502)
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or not any(host.endswith(suffix) for suffix in STREAM_HOST_SUFFIXES)
    ):
        raise BusinessError("bilibili_read_rejected", "Stream URL is not a bounded Bilibili location", status=502)
    return value


def uid(value: object) -> str:
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", value) or int(value) >= 2**63:
        raise BusinessError("bilibili_identity_invalid", "A positive decimal Bilibili UID is required", status=422)
    return value


def connection(value: Mapping[str, object]) -> dict[str, object]:
    if value.get("driver") != "bilibili" or value.get("endpoint") not in {"", API} or not value.get("credential_ref"):
        raise BusinessError(
            "bilibili_configuration_invalid",
            "Bilibili uses its fixed HTTPS API and an explicit cookie credential reference",
            status=422,
        )
    return {**value, "account": uid(value.get("account"))}


def page_identity(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 1000:
        raise ValueError("Unbounded page identity")
    if re.fullmatch(r"[1-9][0-9]{0,18}|BV[0-9A-Za-z]{10}", value):
        return value
    parsed = urlsplit("https:" + value if value.startswith("//") else value)
    if parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in {None, 443}:
        if parsed.hostname == "space.bilibili.com" and re.fullmatch(r"/[1-9][0-9]{0,18}/?", parsed.path):
            return uid(parsed.path.strip("/"))
        match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", parsed.path)
        if parsed.hostname == "www.bilibili.com" and match:
            return match[1]
    raise ValueError("Unsupported page identity")


SEARCH_MAX_RESULTS = 50


def search_keyword(value: object) -> str:
    """A bounded public-search keyword; never a BV identity, blank or control bytes."""
    if not isinstance(value, str):
        raise BusinessError("bilibili_read_invalid", "Search requires a bounded keyword", status=422)
    keyword = value.strip()
    if not 1 <= len(keyword) <= 100 or any(ord(c) < 32 for c in keyword):
        raise BusinessError(
            "bilibili_read_invalid", "Search keyword must be 1–100 characters without control bytes", status=422
        )
    return keyword


def search_text(value: object, bound: int) -> str:
    """Strip Bilibili's HTML emphasis markup and bound one search text field."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BusinessError("bilibili_response_invalid", "Search text field changed format", status=502)
    text = re.sub(r"<[^>]+>", "", value)
    try:
        text.encode("utf-8")
    except UnicodeError as exc:
        raise BusinessError("bilibili_response_invalid", "Search text is not valid Unicode", status=502) from exc
    return text[:bound]


def search_rows(data: Mapping[str, object], limit: int) -> dict[str, object]:
    """Bounded, markup-stripped video search rows; an absent result list means no matches."""
    raw = data.get("result", data.get("results"))
    if raw is None:
        rows: list[object] = []
    elif isinstance(raw, list):
        rows = cast(list[object], raw)
    else:
        raise BusinessError("bilibili_response_invalid", "Search result list changed format", status=502)
    if len(rows) > SEARCH_MAX_RESULTS:
        raise BusinessError("bilibili_response_invalid", "Search result list changed format", status=502)
    items: list[dict[str, object]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            raise BusinessError("bilibili_response_invalid", "Search records changed format", status=502)
        entry = cast(dict[str, object], row)
        bvid = entry.get("bvid")
        if not isinstance(bvid, str) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
            raise BusinessError("bilibili_response_invalid", "Search records changed format", status=502)
        items.append(
            {
                "bvid": bvid,
                "title": search_text(entry.get("title"), 300),
                "author": search_text(entry.get("author"), 100),
                "play": entry.get("play") if type(entry.get("play")) is int else None,
                "danmaku": entry.get("video_review") if type(entry.get("video_review")) is int else None,
                "duration": search_text(entry.get("duration"), 40),
                "pubdate": entry.get("pubdate") if type(entry.get("pubdate")) is int else None,
                "description": search_text(entry.get("description"), 200),
            }
        )
    return {"items": items, "has_more": len(rows) >= limit}


def bounded_text(value: object, bound: int) -> str:
    """Validate and bound a plain-text field; unlike search_text it never strips markup."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BusinessError("bilibili_response_invalid", "Bilibili text field changed format", status=502)
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise BusinessError("bilibili_response_invalid", "Bilibili text is not valid Unicode", status=502) from exc
    return value[:bound]


def whois_profile(data: Mapping[str, object], mid: str) -> dict[str, object]:
    """Bounded public space profile from acc/info; identity must match the requested mid."""
    if uid(data.get("mid")) != mid:
        raise BusinessError("bilibili_source_changed", "Bilibili space identity changed")
    return {
        "mid": mid,
        "name": bounded_text(data.get("name"), 100),
        "sex": bounded_text(data.get("sex"), 20),
        "sign": bounded_text(data.get("sign"), 200),
        "level": data.get("level") if type(data.get("level")) is int else None,
    }


RELATION_FOLLOWED = 2
RELATION_MUTUAL = 6
RELATION_BLOCKED = 128


def relation_view(data: Mapping[str, object], mid: str) -> dict[str, object]:
    """Preserve unknown directions and codes instead of claiming a negative relationship."""
    attribute = data.get("attribute")
    if type(attribute) is not int:
        raise BusinessError("bilibili_response_invalid", "Bilibili relation changed format", status=502)
    reverse = data.get("be_relation")
    if reverse is not None and not isinstance(reverse, dict):
        raise BusinessError("bilibili_response_invalid", "Bilibili reverse relation changed format", status=502)
    be_attribute = cast(dict[str, object], reverse).get("attribute") if reverse is not None else None
    if be_attribute is not None and type(be_attribute) is not int:
        raise BusinessError("bilibili_response_invalid", "Bilibili reverse relation changed format", status=502)
    follow_states = {0: False, RELATION_FOLLOWED: True, RELATION_MUTUAL: True, RELATION_BLOCKED: False}
    follows = follow_states.get(attribute)
    reverse_follows = follow_states.get(be_attribute) if be_attribute is not None else None
    blocked: bool | None = None
    if attribute == RELATION_BLOCKED or be_attribute == RELATION_BLOCKED:
        blocked = True
    elif follows is not None and reverse_follows is not None:
        blocked = False
    return {
        "mid": mid,
        "attribute": attribute,
        "be_attribute": be_attribute,
        "follows": follows,
        "followed_by": True if attribute == RELATION_MUTUAL else reverse_follows,
        "mutual": attribute == RELATION_MUTUAL if follows is not None else None,
        "blocked": blocked,
    }


DANMAKU_HOST = "comment.bilibili.com"
DANMAKU_MAX_ROWS = 1000
DANMAKU_TEXT_CHARS = 100


def danmaku_url(value: object) -> str:
    """Fixed Bilibili danmaku XML host; the cid must be a bounded uid, never an arbitrary URL."""
    return f"https://{DANMAKU_HOST}/{uid(value)}.xml"


def parse_danmaku(xml: str) -> list[dict[str, object]]:
    """Bounded sample of danmaku rows (time seconds + unescaped text) from the official XML.

    The endpoint returns a SAMPLE set, not the full danmaku history; rows are capped and each
    text bounded. Non-finite/negative timestamps are skipped, never coerced.
    """
    items: list[dict[str, object]] = []
    for match in re.finditer(r'<d p="([^"]*)">([^<]*)</d>', xml):
        if len(items) >= DANMAKU_MAX_ROWS:
            break
        try:
            t = float(match.group(1).split(",", 1)[0])
        except ValueError:
            continue
        if not 0 <= t < 1e9:
            continue
        items.append({"t": round(t, 3), "text": html.unescape(match.group(2))[:DANMAKU_TEXT_CHARS]})
    return items


FEED_MAX_ROWS = 50
MSGFEED_LIKE_MAX_USERS = 20


def feed_rows(data: Mapping[str, object], limit: int) -> dict[str, object]:
    """Bounded popular/ranking video rows from data.list (nested stat/owner); absent list = empty."""
    raw = data.get("list")
    if raw is None:
        rows: list[object] = []
    elif isinstance(raw, list):
        rows = cast(list[object], raw)
    else:
        raise BusinessError("bilibili_response_invalid", "Feed list changed format", status=502)
    if len(rows) > FEED_MAX_ROWS:
        raise BusinessError("bilibili_response_invalid", "Feed list changed format", status=502)
    items: list[dict[str, object]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            raise BusinessError("bilibili_response_invalid", "Feed records changed format", status=502)
        entry = cast(dict[str, object], row)
        bvid = entry.get("bvid")
        if not isinstance(bvid, str) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
            raise BusinessError("bilibili_response_invalid", "Feed records changed format", status=502)
        raw_stat = entry.get("stat")
        stat: dict[str, object] = cast(dict[str, object], raw_stat) if isinstance(raw_stat, dict) else {}
        raw_owner = entry.get("owner")
        owner: dict[str, object] = cast(dict[str, object], raw_owner) if isinstance(raw_owner, dict) else {}
        items.append(
            {
                "bvid": bvid,
                "title": search_text(entry.get("title"), 300),
                "author": search_text(owner.get("name"), 100),
                "play": stat.get("view") if type(stat.get("view")) is int else None,
                "danmaku": stat.get("danmaku") if type(stat.get("danmaku")) is int else None,
                "duration_s": entry.get("duration") if type(entry.get("duration")) is int else None,
            }
        )
    return {"items": items, "has_more": len(rows) >= limit}


def _as_dict(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def duration_seconds(value: object) -> int | None:
    """Parse 'MM:SS' or 'HH:MM:SS' duration_text to whole seconds; unparseable -> None (no silent 0)."""
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]


def following_rows(data: Mapping[str, object], limit: int) -> dict[str, object]:
    """Bounded following video-dynamic rows from data.items (nested modules); non-video items skipped."""
    raw = data.get("items")
    if raw is None:
        rows: list[object] = []
    elif isinstance(raw, list):
        rows = cast(list[object], raw)
    else:
        raise BusinessError("bilibili_response_invalid", "Following feed items changed format", status=502)
    if len(rows) > FEED_MAX_ROWS:
        raise BusinessError("bilibili_response_invalid", "Following feed items changed format", status=502)
    items: list[dict[str, object]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            raise BusinessError("bilibili_response_invalid", "Following feed records changed format", status=502)
        entry = cast(dict[str, object], row)
        modules = _as_dict(entry.get("modules"))
        archive = _as_dict(_as_dict(_as_dict(modules.get("module_dynamic")).get("major")).get("archive"))
        bvid = archive.get("bvid")
        if bvid is None:
            continue
        if not isinstance(bvid, str) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
            raise BusinessError("bilibili_response_invalid", "Following feed records changed format", status=502)
        author = _as_dict(modules.get("module_author"))
        stat = _as_dict(archive.get("stat"))
        items.append(
            {
                "bvid": bvid,
                "title": search_text(archive.get("title"), 300),
                "author": search_text(author.get("name"), 100),
                "play": stat.get("play") if type(stat.get("play")) is int else None,
                "duration_s": duration_seconds(archive.get("duration_text")),
            }
        )
    return {"items": items, "has_more": len(rows) >= limit}


def fav_folder_rows(data: Mapping[str, object], limit: int) -> dict[str, object]:
    """Bounded owner-created favorite folders from data.list (list-all is complete); absent list = empty."""
    raw = data.get("list")
    if raw is None:
        rows: list[object] = []
    elif isinstance(raw, list):
        rows = cast(list[object], raw)
    else:
        raise BusinessError("bilibili_response_invalid", "Favorite folder list changed format", status=502)
    if len(rows) > FEED_MAX_ROWS:
        raise BusinessError("bilibili_response_invalid", "Favorite folder list changed format", status=502)
    items: list[dict[str, object]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            raise BusinessError("bilibili_response_invalid", "Favorite folder records changed format", status=502)
        entry = cast(dict[str, object], row)
        folder_id = entry.get("id")
        if type(folder_id) is not int:
            raise BusinessError("bilibili_response_invalid", "Favorite folder records changed format", status=502)
        items.append(
            {
                "id": folder_id,
                "title": search_text(entry.get("title"), 300),
                "media_count": entry.get("media_count") if type(entry.get("media_count")) is int else None,
            }
        )
    return {"items": items, "has_more": len(rows) > limit}


def tag_rows(data: object, limit: int) -> dict[str, object]:
    """Bounded public video tags from the data array (x/tag/archive/tags); absent/empty = normal."""
    if data is None:
        rows: list[object] = []
    elif isinstance(data, list):
        rows = cast(list[object], data)
    else:
        raise BusinessError("bilibili_response_invalid", "Tag list changed format", status=502)
    if len(rows) > FEED_MAX_ROWS:
        raise BusinessError("bilibili_response_invalid", "Tag list changed format", status=502)
    items: list[dict[str, object]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            raise BusinessError("bilibili_response_invalid", "Tag records changed format", status=502)
        entry = cast(dict[str, object], row)
        tag_id = entry.get("tag_id")
        if type(tag_id) is not int:
            raise BusinessError("bilibili_response_invalid", "Tag records changed format", status=502)
        items.append({"tag_id": tag_id, "tag_name": search_text(entry.get("tag_name"), 100)})
    return {"items": items, "has_more": len(rows) > limit}


def history_rows(data: Mapping[str, object], limit: int) -> dict[str, object]:
    """Bounded watch-history video rows from data.list (nested history oid/cid/business); absent list = empty."""
    raw = data.get("list")
    if raw is None:
        rows: list[object] = []
    elif isinstance(raw, list):
        rows = cast(list[object], raw)
    else:
        raise BusinessError("bilibili_response_invalid", "History list changed format", status=502)
    if len(rows) > FEED_MAX_ROWS:
        raise BusinessError("bilibili_response_invalid", "History list changed format", status=502)
    items: list[dict[str, object]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            raise BusinessError("bilibili_response_invalid", "History records changed format", status=502)
        entry = cast(dict[str, object], row)
        history = _as_dict(entry.get("history"))
        bvid = history.get("bvid")
        items.append(
            {
                "title": search_text(entry.get("title"), 300),
                "author_name": search_text(entry.get("author_name"), 100),
                "author_mid": str(entry.get("author_mid")) if type(entry.get("author_mid")) is int else "",
                "progress": entry.get("progress") if type(entry.get("progress")) is int else None,
                "business": history.get("business") if type(history.get("business")) is str else None,
                "aid": history.get("oid") if type(history.get("oid")) is int else None,
                "cid": history.get("cid") if type(history.get("cid")) is int else None,
                "bvid": bvid if isinstance(bvid, str) and re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid) else None,
            }
        )
    return {"items": items, "has_more": len(rows) >= limit}


def msgfeed_kind(value: object) -> str:
    """Inbox kind selector: reply ('回复我的'), at ('@我的') or like ('收到的赞'); anything else is invalid."""
    if value not in ("reply", "at", "like"):
        raise BusinessError("bilibili_read_invalid", "Msgfeed target must be reply, at or like", status=422)
    return cast(str, value)


def msgfeed_rows(data: Mapping[str, object], limit: int) -> dict[str, object]:
    """Bounded inbox notification rows from data.items; title/source_content are unreliable discovery hints.

    The official inbox only signals 'someone contacted us'; authoritative reply text must be
    read from the thread (replies operation), never inferred from these fields.
    """
    raw = data.get("items")
    if raw is None:
        rows: list[object] = []
    elif isinstance(raw, list):
        rows = cast(list[object], raw)
    else:
        raise BusinessError("bilibili_response_invalid", "Msgfeed items changed format", status=502)
    if len(rows) > FEED_MAX_ROWS:
        raise BusinessError("bilibili_response_invalid", "Msgfeed items changed format", status=502)
    items: list[dict[str, object]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            raise BusinessError("bilibili_response_invalid", "Msgfeed records changed format", status=502)
        entry = cast(dict[str, object], row)
        item_id = entry.get("id")
        if type(item_id) is not int:
            raise BusinessError("bilibili_response_invalid", "Msgfeed records changed format", status=502)
        user = _as_dict(entry.get("user"))
        item = _as_dict(entry.get("item"))
        reply_time = entry.get("reply_time")
        at_time = entry.get("at_time")
        mid = user.get("mid")
        business_id = item.get("business_id")
        items.append(
            {
                "id": item_id,
                "time": reply_time if type(reply_time) is int else (at_time if type(at_time) is int else None),
                "nickname": search_text(user.get("nickname"), 100),
                "mid": str(mid) if type(mid) is int else "",
                "business_id": business_id if type(business_id) is int else None,
                "subject_id": str(item.get("subject_id")) if type(item.get("subject_id")) is int else "",
                "root_id": str(item.get("root_id")) if type(item.get("root_id")) is int else "",
                "source_id": str(item.get("source_id")) if type(item.get("source_id")) is int else "",
                "uri": search_text(item.get("uri"), 300),
                "title": search_text(item.get("title"), 300),
                "source_content": search_text(item.get("source_content"), 300),
            }
        )
    return {"items": items, "has_more": len(rows) >= limit}


def msgfeed_like_rows(data: Mapping[str, object], limit: int) -> dict[str, object]:
    """Notification snippets are discovery hints, not authoritative content."""
    merged: list[dict[str, object]] = []
    seen: set[int] = set()
    for branch in ("latest", "total", "items"):
        nested = data.get(branch) if branch != "items" else None
        if nested is not None and not isinstance(nested, dict):
            raise BusinessError("bilibili_response_invalid", "Msgfeed like branch changed format", status=502)
        container = data if branch == "items" else cast(dict[str, object], nested) if nested is not None else {}
        raw = container.get("items")
        if raw is None:
            continue
        if not isinstance(raw, list):
            raise BusinessError("bilibili_response_invalid", "Msgfeed like items changed format", status=502)
        rows = cast(list[object], raw)
        if len(rows) > FEED_MAX_ROWS:
            raise BusinessError("bilibili_response_invalid", "Msgfeed like items changed format", status=502)
        for row in rows:
            if not isinstance(row, dict):
                raise BusinessError("bilibili_response_invalid", "Msgfeed like records changed format", status=502)
            entry = cast(dict[str, object], row)
            item_id = entry.get("id")
            if type(item_id) is not int:
                raise BusinessError("bilibili_response_invalid", "Msgfeed like records changed format", status=502)
            if item_id in seen:
                continue
            seen.add(item_id)
            merged.append(entry)
    if len(merged) > FEED_MAX_ROWS:
        raise BusinessError("bilibili_response_invalid", "Msgfeed like items changed format", status=502)
    items: list[dict[str, object]] = []
    for entry in merged[:limit]:
        users_raw = entry.get("users")
        if users_raw is None:
            users_list: list[object] = []
        elif isinstance(users_raw, list):
            users_list = cast(list[object], users_raw)
        else:
            raise BusinessError("bilibili_response_invalid", "Msgfeed like users changed format", status=502)
        users: list[dict[str, object]] = []
        for user in users_list[:MSGFEED_LIKE_MAX_USERS]:
            if not isinstance(user, dict):
                raise BusinessError("bilibili_response_invalid", "Msgfeed like users changed format", status=502)
            liker = cast(dict[str, object], user)
            mid = liker.get("mid")
            users.append(
                {"mid": str(mid) if type(mid) is int else "", "nickname": search_text(liker.get("nickname"), 100)}
            )
        like_time = entry.get("like_time")
        counts = entry.get("counts")
        item = _as_dict(entry.get("item"))
        business_id = item.get("business_id")
        items.append(
            {
                "id": cast(int, entry["id"]),
                "time": like_time if type(like_time) is int else None,
                "counts": counts if type(counts) is int else None,
                "users": users,
                "business_id": business_id if type(business_id) is int else None,
                "subject_id": str(item.get("subject_id")) if type(item.get("subject_id")) is int else "",
                "root_id": str(item.get("root_id")) if type(item.get("root_id")) is int else "",
                "source_id": str(item.get("source_id")) if type(item.get("source_id")) is int else "",
                "uri": search_text(item.get("uri"), 300),
                "title": search_text(item.get("title"), 300),
                "source_content": search_text(item.get("source_content"), 300),
            }
        )
    return {"items": items, "has_more": len(merged) >= limit}


# Bounded read cache for endpoints whose payload is stable enough to serve stale for a day
# (reference dsh-bili-whale: view/acc-info 24h, 400-entry LRU). Every other read is live on
# every call — account, comments, replies, search, relation, danmaku, feeds, favorites, tags,
# history, msgfeed — and nav is never cached so a rotated credential is observed next read.
READ_CACHE_TTL_S: Mapping[str, float] = {"video": 86_400.0, "whois": 86_400.0}
READ_CACHE_MAX_ENTRIES = 400
READ_CACHE_MAX_BYTES = 8 * 1024 * 1024
READ_CACHE_MAX_ENTRY_BYTES = 64 * 1024

ReadCacheKey = tuple[str, str, str, str, str, int]


def read_cache_key(
    agent_id: str, config_id: str, authority: str, operation: str, target: str, limit: int
) -> ReadCacheKey:
    """Key includes owner, configuration and credential generation, so a rotated credential misses."""
    if (
        not isinstance(cast(object, agent_id), str)
        or not agent_id
        or not isinstance(cast(object, config_id), str)
        or not config_id
        or not isinstance(cast(object, authority), str)
        or not authority
        or not isinstance(cast(object, operation), str)
        or not operation
        or not isinstance(cast(object, target), str)
        or type(limit) is not int
        or limit < 1
    ):
        raise ValueError("Read cache keys require owner, configuration, authority, operation and a positive limit")
    return (agent_id, config_id, authority, operation, target, limit)


class ReadCache:
    """TTL + LRU snapshots of JSON payloads; no I/O, no clock reads and no unbounded memory."""

    def __init__(
        self,
        max_entries: int = READ_CACHE_MAX_ENTRIES,
        max_bytes: int = READ_CACHE_MAX_BYTES,
    ) -> None:
        if type(max_entries) is not int or max_entries < 1 or type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("Read cache bounds must be positive integers")
        self._max_entries, self._max_bytes = max_entries, max_bytes
        self._entries: OrderedDict[ReadCacheKey, tuple[float, int, str]] = OrderedDict()
        self._bytes = 0

    @property
    def entries(self) -> int:
        return len(self._entries)

    @property
    def bytes(self) -> int:
        return self._bytes

    def get(self, key: ReadCacheKey, now: float) -> dict[str, object] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires, _, raw = entry
        if now >= expires:
            self._drop(key)
            return None
        self._entries.move_to_end(key)
        try:
            decoded: object = json.loads(raw)
        except (ValueError, RecursionError):
            self._drop(key)
            return None
        if not isinstance(decoded, dict):
            self._drop(key)
            return None
        return cast(dict[str, object], decoded)

    def put(self, key: ReadCacheKey, value: dict[str, object], now: float, ttl_s: float) -> bool:
        """Store an immutable snapshot; returns False when it cannot fit the byte bounds."""
        if type(ttl_s) not in (int, float) or not math.isfinite(ttl_s) or ttl_s <= 0:
            raise ValueError("Read cache TTL must be finite and positive")
        try:
            # Insertion order is preserved on the round trip so a cache hit serializes byte-identically
            # to the live read it replaces.
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            size = len(raw.encode())
        except (TypeError, ValueError, UnicodeError):
            return False
        if size > READ_CACHE_MAX_ENTRY_BYTES or size > self._max_bytes:
            return False
        self._drop(key)
        self._entries[key] = (now + float(ttl_s), size, raw)
        self._bytes += size
        while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
            self._drop(next(iter(self._entries)))
        return True

    def discard_scope(self, agent_id: str, config_id: str) -> int:
        """Drop every generation of one configuration after its credential authority changes."""
        keys = [key for key in self._entries if key[0] == agent_id and key[1] == config_id]
        for key in keys:
            self._drop(key)
        return len(keys)

    def discard_generations(self, agent_id: str, config_id: str, authority: str) -> int:
        """Drop snapshots of retired credential generations once the current generation is known."""
        keys = [key for key in self._entries if key[0] == agent_id and key[1] == config_id and key[2] != authority]
        for key in keys:
            self._drop(key)
        return len(keys)

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0

    def _drop(self, key: ReadCacheKey) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= entry[1]


class Form(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    account: str = Field(min_length=1, max_length=300)
    account_attr: str = "data-mid"
    target: str = Field(min_length=1, max_length=300)
    target_attr: str = "data-id"
    editor: str = Field(min_length=1, max_length=300)
    submit: str = Field(min_length=1, max_length=300)
    records: str = Field(min_length=1, max_length=300)
    record_id_attr: str = "data-id"
    record_author: str = Field(min_length=1, max_length=300)
    record_author_attr: str = "data-mid"
    record_text: str = Field(min_length=1, max_length=300)
    challenge: str = Field(min_length=1, max_length=300)
    logged_out: str = Field(min_length=1, max_length=300)
    mention_input: str = ""
    mention_options: str = ""
    mention_nodes: str = ""
    mention_uid_attr: str = "data-mid"
    record_mentions: str = ""


def forms_value(forms: object, enabled: bool) -> dict[str, object]:
    entries = cast(dict[str, object], forms) if isinstance(forms, dict) else None
    if (
        type(enabled) is not bool
        or entries is None
        or set(entries) - {"comment", "private_message"}
        or (enabled and not entries)
    ):
        raise BusinessError(
            "bilibili_profile_invalid",
            "Choose reviewed comment/private-message forms before enabling publication",
            status=422,
        )
    result: dict[str, object] = {}
    try:
        for kind, raw in entries.items():
            profile = Form.model_validate(raw).model_dump()
            for name, value in profile.items():
                value.encode("utf-8")
                if (
                    len(value) > 300
                    or "\0" in value
                    or (name.endswith("_attr") and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value))
                ):
                    raise ValueError("Invalid profile field")
            mention_fields = [profile[k] for k in ("mention_options", "mention_nodes", "record_mentions")]
            if any(mention_fields) and not all(mention_fields):
                raise ValueError(
                    "Mention candidates, editor chips and published record chips must be verified together"
                )
            result[kind] = profile
        if len(json.dumps(result).encode()) > 32768:
            raise ValueError("Profile payload exceeds its storage bound")
    except (ValidationError, TypeError, ValueError) as exc:
        raise BusinessError(
            "bilibili_profile_invalid", "Form fields must match the bounded selector and mention contract", status=422
        ) from exc
    return result


def target(kind: str, value: str) -> tuple[str, str]:
    if not isinstance(cast(object, value), str) or len(value) > 500:
        raise BusinessError("bilibili_target_invalid", "A bounded Bilibili target URL is required", status=422)
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ValueError("Invalid origin")
        if kind == "comment" and parsed.hostname == "www.bilibili.com" and not parsed.fragment:
            match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", parsed.path)
            query = parse_qsl(parsed.query, keep_blank_values=True)
            if match and (not query or (len(query) == 1 and query[0][0] == "reply_id" and uid(query[0][1]))):
                return "https://www.bilibili.com/video/" + match[1] + (
                    "?reply_id=" + query[0][1] if query else ""
                ), query[0][1] if query else match[1]
        if (
            kind == "private_message"
            and parsed.hostname == "message.bilibili.com"
            and parsed.path in {"", "/"}
            and not parsed.query
        ):
            match = re.fullmatch(r"/?whisper/mid([1-9][0-9]{0,18})/?", parsed.fragment)
            if match:
                return "https://message.bilibili.com/#/whisper/mid" + uid(match[1]), uid(match[1])
    except (ValueError, BusinessError) as exc:
        raise BusinessError(
            "bilibili_target_invalid", "Target is not a supported Bilibili comment or private-message URL", status=422
        ) from exc
    raise BusinessError(
        "bilibili_target_invalid", "Target is not a supported Bilibili comment or private-message URL", status=422
    )


def mentions_value(text: str, mentions: object) -> list[dict[str, object]]:
    if (
        not isinstance(cast(object, text), str)
        or not text.strip()
        or len(text) > MAX_CONTENT
        or any(ord(c) < 32 and c not in "\n\t" for c in text)
    ):
        raise BusinessError(
            "bilibili_content_invalid", "Publication requires 1–2000 characters without control bytes", status=422
        )
    if not isinstance(mentions, list):
        raise BusinessError(
            "bilibili_mentions_invalid", "At most ten explicit mention ranges are supported", status=422
        )
    items = cast(list[object], mentions)
    if len(items) > 10:
        raise BusinessError(
            "bilibili_mentions_invalid", "At most ten explicit mention ranges are supported", status=422
        )
    result: list[dict[str, object]] = []
    end = 0
    names: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            raise BusinessError(
                "bilibili_mentions_invalid",
                "Mentions must be ordered, nonoverlapping exact @name character ranges",
                status=422,
            )
        entry = cast(dict[str, object], item)
        start, stop = entry.get("start"), entry.get("end")
        name, ref = entry.get("name"), entry.get("uid")
        if (
            set(entry) != {"start", "end", "uid", "name"}
            or type(start) is not int
            or type(stop) is not int
            or not isinstance(name, str)
            or not 1 <= len(name) <= 40
            or any(ord(c) < 32 for c in name)
        ):
            raise BusinessError(
                "bilibili_mentions_invalid",
                "Mentions must be ordered, nonoverlapping exact @name character ranges",
                status=422,
            )
        if not end <= start < stop <= len(text) or text[start:stop] != "@" + name:
            raise BusinessError(
                "bilibili_mentions_invalid",
                "Mentions must be ordered, nonoverlapping exact @name character ranges",
                status=422,
            )
        identity = uid(ref)
        if name in names and names[name] != identity:
            raise BusinessError(
                "bilibili_mentions_invalid",
                "One mention display name cannot bind multiple UIDs in a publication",
                status=422,
            )
        names[name] = identity
        result.append({**entry, "uid": identity})
        end = stop
    return result


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
