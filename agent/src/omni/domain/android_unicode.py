"""Pinned helper profiles and authenticated, bounded Unicode input messages; no device I/O."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from typing import cast

from .configuration import reference
from .contracts import BusinessError

PACKAGE = "org.omnioperator.inputhelper"
COMPONENT = PACKAGE + "/.HelperIme"
SOCKET = PACKAGE + ".v1.u"
VERSION = 1
MAX_PACKET = 16384


def invalid(code: str = "android_unicode_invalid") -> BusinessError:
    return BusinessError(code, "Unicode helper configuration or proof is invalid", status=422)


def text_value(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 256
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    ):
        raise invalid()
    try:
        if len(value.encode("utf-8")) > 2048:
            raise invalid()
    except UnicodeError:
        raise invalid() from None
    return value


def profile(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise invalid()
    record = cast(dict[str, object], value)
    if (
        set(record) != {"enabled", "credential_ref", "apk_sha256", "certificate_sha256"}
        or type(record["enabled"]) is not bool
    ):
        raise invalid()
    for field in ("apk_sha256", "certificate_sha256"):
        digest = record[field]
        if not isinstance(digest, str) or (digest and not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise invalid()
    credential = record["credential_ref"]
    if not isinstance(credential, str) or len(credential) > 200:
        raise invalid()
    try:
        reference(credential)
    except (TypeError, ValueError):
        raise invalid() from None
    if record["enabled"] and not all(record[k] for k in ("credential_ref", "apk_sha256", "certificate_sha256")):
        raise invalid()
    return dict(record)


def key_value(value: object) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise invalid("android_pairing_key_invalid")
    raw = base64.urlsafe_b64decode(value + "=")
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value:
        raise invalid("android_pairing_key_invalid")
    return raw


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def packet(value: dict[str, object], key: bytes, *, response_to: str = "") -> bytes:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if not 1 <= len(body) <= MAX_PACKET:
        raise invalid()
    prefix = (
        b"omni-input-response-1\n" + response_to.encode("ascii") + b"\n" if response_to else b"omni-input-request-1\n"
    )
    return len(body).to_bytes(4, "big") + body + hmac.digest(key, prefix + body, "sha256")


def unpack(body: bytes, signature: bytes, key: bytes, nonce: str) -> dict[str, object]:
    if not 1 <= len(body) <= MAX_PACKET or not hmac.compare_digest(
        signature, hmac.digest(key, b"omni-input-response-1\n" + nonce.encode("ascii") + b"\n" + body, "sha256")
    ):
        raise invalid("android_helper_auth_failed")

    def pairs(entries: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for name, item in entries:
            if name in value:
                raise invalid()
            value[name] = item
        return value

    def constant(_: str) -> object:
        raise invalid()

    result = json.loads(body, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(result, dict):
        raise invalid()
    record = cast(dict[str, object], result)
    version = record.get("version")
    if type(version) is not int or version != VERSION or record.get("nonce") != nonce:
        raise invalid()
    return record


def ime_component(value: object) -> str:
    if not isinstance(value, str) or len(value) > 400 or not re.fullmatch(r"[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+", value):
        raise invalid("android_ime_identity_invalid")
    return value
