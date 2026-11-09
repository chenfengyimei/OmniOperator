"""Bounded OneBot text events and explicit conversation grants; no transport I/O."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .configuration import endpoint
from .contracts import BusinessError

ADAPTER = "onebot11-text/v2"
MAX_TEXT = 8000
SEND_COOLDOWN_SECONDS = 3


def qq_id(value: object) -> str:
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise BusinessError("invalid_qq_identity", "QQ identities must be positive decimal integers", status=422)
    if len(value) > 19 or value.startswith("0") or int(value) > 2**63 - 1:
        raise BusinessError("invalid_qq_identity", "QQ identity is outside the supported range", status=422)
    return value


def connection_value(value: dict) -> dict:
    try:
        endpoint(value.get("endpoint", ""), websocket=True)
    except (TypeError, ValueError) as exc:
        raise BusinessError(
            "invalid_qq_connection",
            "QQ requires a credential-free WebSocket URL with TLS except on loopback",
            status=422,
        ) from exc
    if value.get("driver") != "qq" or not value["endpoint"].startswith(("ws://", "wss://")):
        raise BusinessError("invalid_qq_connection", "QQ requires an explicit OneBot WebSocket endpoint", status=422)
    return {**value, "account": qq_id(value.get("account"))}


def binding_value(value: dict) -> dict:
    if set(value) != {"scene", "peer_id", "enabled", "persona", "cooldown_s"}:
        raise BusinessError("invalid_qq_binding", "Supply the complete conversation grant", status=422)
    if (
        not isinstance(value["scene"], str)
        or value["scene"] not in {"private", "group"}
        or type(value["enabled"]) is not bool
    ):
        raise BusinessError("invalid_qq_binding", "Invalid QQ conversation scope", status=422)
    if (
        not isinstance(value["persona"], str)
        or not value["persona"].strip()
        or len(value["persona"]) > 8000
        or "\0" in value["persona"]
        or type(value["cooldown_s"]) is not int
        or not 1 <= value["cooldown_s"] <= 3600
    ):
        raise BusinessError(
            "invalid_qq_binding", "A bounded persona and 1–3600 second cooldown are required", status=422
        )
    return {**value, "peer_id": qq_id(value["peer_id"])}


@dataclass(frozen=True)
class QQEvent:
    account: str
    scene: str
    peer_id: str
    sender_id: str
    message_id: int
    text: str

    @property
    def key(self) -> str:
        return hashlib.sha256(
            json.dumps([self.account, self.scene, self.peer_id, self.message_id], separators=(",", ":")).encode()
        ).hexdigest()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps([self.key, self.sender_id, self.text], ensure_ascii=False).encode()
        ).hexdigest()


def parse_event(event: dict, account: str) -> QQEvent | None:
    """Ignore untriggered/unsupported events; never turn CQ strings into executable segments."""
    if event.get("post_type") != "message":
        return None
    if qq_id(event.get("self_id")) != account:
        raise BusinessError("qq_account_mismatch", "OneBot event belongs to another account")
    scene = event.get("message_type")
    if not isinstance(scene, str) or not isinstance(event.get("sub_type"), str):
        return None
    if (scene, event.get("sub_type")) not in {("private", "friend"), ("group", "normal")}:
        return None
    sender = qq_id(event.get("user_id"))
    if sender == account:
        return None
    identity = event.get("message_id")
    if type(identity) is not int or not -(2**31) <= identity < 2**31:
        raise BusinessError("invalid_qq_event", "OneBot message id must be a signed int32", status=422)
    parts = event.get("message")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 128:
        return None
    text, mentioned = [], False
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("data"), dict):
            return None
        if part.get("type") == "at" and str(part["data"].get("qq")) == account:
            mentioned = True
        elif part.get("type") == "text" and isinstance(part["data"].get("text"), str):
            text.append(part["data"]["text"])
        else:
            # Attachments/replies/other mentions require their own source and budget contract.
            return None
    body = "".join(text).strip()
    if not body or (scene == "group" and not mentioned):
        return None
    if len(body) > MAX_TEXT or len(body.encode("utf-8")) > 32768 or "\0" in body:
        raise BusinessError("qq_message_too_large", "QQ text exceeds its admission limit", status=422)
    return QQEvent(account, scene, qq_id(event.get("group_id")) if scene == "group" else sender, sender, identity, body)
