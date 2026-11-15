"""Explicit Bilibili form profiles, targets and mention ranges; no browser, network or storage."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import cast
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import BusinessError

ADAPTER = "bilibili-ui/v2"
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
