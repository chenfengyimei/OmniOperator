"""Explicit MCP OAuth 2.0 authorization-code flow with PKCE, state and a vault token store.

Nothing here starts an authorization on its own: an owner action calls ``start``,
the browser returns one code, and only then are tokens stored. A 401 from a tool
call never reaches this module.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import secrets as random_secrets
import time
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from ...application.ports import AsyncSecretPort
from ...domain.contracts import BusinessError
from ...domain.mcp import OAUTH_TTL_S, ServerTarget, oauth_reference

log = logging.getLogger("omni.v2.mcp.oauth")
Packet = dict[str, Any]

MAX_PENDING = 4
MAX_METADATA_BYTES = 96 * 1024
FETCH_TIMEOUT_S = 5.0
TOKEN_TIMEOUT_S = 10.0
REFRESH_MARGIN_S = 60
MAX_TOKEN_BYTES = 8192
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
CLIENT_REFERENCE = "mcp.oauth-client.{server_id}"


def _now() -> float:
    return time.time()


def _iso(seconds_from_now: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds_from_now)).isoformat(
        timespec="microseconds"
    )


def _endpoint(value: object, *, allow_loopback: bool = True) -> str:
    if not isinstance(value, str) or not value or len(value) > 2000:
        raise ValueError("invalid endpoint")
    url = urlsplit(value)
    if (
        url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username
        or url.password
        or url.fragment
        or (url.scheme == "http" and (not allow_loopback or url.hostname not in LOOPBACK_HOSTS))
    ):
        raise ValueError("invalid endpoint")
    return value


def loopback_redirect(value: object) -> str:
    try:
        url = urlsplit(str(value))
    except ValueError:
        raise BusinessError("mcp_oauth_redirect_invalid", "A loopback callback URL is required", status=422) from None
    if (
        url.scheme != "http"
        or url.hostname not in LOOPBACK_HOSTS
        or url.path != "/oauth/callback"
        or url.query
        or url.fragment
    ):
        raise BusinessError(
            "mcp_oauth_redirect_invalid",
            "OAuth callbacks must return to http://127.0.0.1/oauth/callback",
            status=422,
        )
    return str(value)


class OAuthTokens:
    """Vault-backed access/refresh tokens; refresh is allowed, a new authorization is not."""

    def __init__(
        self,
        secrets: AsyncSecretPort,
        *,
        client_factory: Callable[..., httpx.AsyncClient] | None = None,
        redact: Callable[[str], None] | None = None,
    ) -> None:
        self._secrets = secrets
        self._client_factory = client_factory or httpx.AsyncClient
        self._redact = redact

    async def load(self, server_id: str) -> Packet:
        raw = await self._secrets.get(oauth_reference(server_id))
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return cast(Packet, parsed) if isinstance(parsed, dict) else {}

    async def save(self, server_id: str, value: Packet) -> None:
        token = str(value.get("access_token") or "")
        if token:
            if len(token.encode("utf-8")) > MAX_TOKEN_BYTES:
                raise BusinessError("mcp_oauth_token_invalid", "The OAuth access token is too large", status=502)
            if self._redact is not None:
                self._redact(token)
                if value.get("refresh_token"):
                    self._redact(str(value["refresh_token"]))
        await self._secrets.set(oauth_reference(server_id), json.dumps(value, ensure_ascii=False))

    async def clear(self, server_id: str) -> None:
        await self._secrets.delete(oauth_reference(server_id))

    async def client(self, server_id: str) -> Packet:
        raw = await self._secrets.get(CLIENT_REFERENCE.format(server_id=server_id))
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return cast(Packet, parsed) if isinstance(parsed, dict) else {}

    async def save_client(self, server_id: str, value: Packet) -> None:
        await self._secrets.set(CLIENT_REFERENCE.format(server_id=server_id), json.dumps(value, ensure_ascii=False))

    async def access_token(self, server_id: str) -> str:
        value = await self.load(server_id)
        token = str(value.get("access_token") or "")
        if not token:
            return ""
        expires_at = value.get("expires_at")
        if isinstance(expires_at, str) and expires_at:
            try:
                expiry = dt.datetime.fromisoformat(expires_at).timestamp()
            except ValueError:
                expiry = 0.0
            if expiry and expiry - REFRESH_MARGIN_S <= _now():
                refreshed = await self._refresh(server_id, value)
                return refreshed
        return token

    async def _refresh(self, server_id: str, value: Packet) -> str:
        refresh_token = str(value.get("refresh_token") or "")
        endpoint = str(value.get("token_endpoint") or "")
        client_id = str(value.get("client_id") or "")
        if not (refresh_token and endpoint and client_id):
            return ""
        try:
            endpoint = _endpoint(endpoint)
        except ValueError:
            return ""
        form = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
        if value.get("client_secret"):
            form["client_secret"] = str(value["client_secret"])
        try:
            payload = await self.post_form(endpoint, form)
        except BusinessError:
            log.warning("MCP OAuth refresh failed", extra={"event": "mcp.oauth_refresh_failed"})
            return ""
        token = str(payload.get("access_token") or "")
        if not token:
            return ""
        updated = {
            **value,
            "access_token": token,
            "refresh_token": str(payload.get("refresh_token") or refresh_token),
            "expires_at": _iso(int(payload.get("expires_in") or 3600)),
        }
        await self.save(server_id, updated)
        return token

    async def post_form(self, endpoint: str, form: dict[str, str]) -> Packet:
        client = self._client_factory(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(TOKEN_TIMEOUT_S, connect=FETCH_TIMEOUT_S),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=0),
        )
        async with (
            client,
            client.stream("POST", endpoint, data=form, headers={"Accept": "application/json"}) as response,
        ):
            if response.status_code != 200:
                raise BusinessError("mcp_oauth_token_rejected", "The token endpoint rejected the request", status=502)
            body = bytearray()
            async for part in response.aiter_bytes():
                body.extend(part)
                if len(body) > MAX_METADATA_BYTES:
                    raise BusinessError("mcp_oauth_token_invalid", "Token response exceeded its bound", status=502)
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeError):
            raise BusinessError("mcp_oauth_token_invalid", "Token endpoint returned invalid JSON", status=502) from None
        if not isinstance(parsed, dict):
            raise BusinessError("mcp_oauth_token_invalid", "Token endpoint returned an invalid object", status=502)
        return cast(Packet, parsed)


class OAuthFlow:
    """One bounded pending authorization at a time per server; state is single use."""

    def __init__(
        self,
        secrets: AsyncSecretPort,
        *,
        client_factory: Callable[..., httpx.AsyncClient] | None = None,
        redact: Callable[[str], None] | None = None,
    ) -> None:
        self.tokens = OAuthTokens(secrets, client_factory=client_factory, redact=redact)
        self._client_factory = client_factory or httpx.AsyncClient
        self._pending: dict[str, Packet] = {}
        self._lock = asyncio.Lock()

    async def _fetch(self, url: str) -> Packet:
        client = self._client_factory(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(FETCH_TIMEOUT_S, connect=FETCH_TIMEOUT_S),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=0),
        )
        try:
            async with client, client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                if response.status_code != 200:
                    raise BusinessError(
                        "mcp_oauth_discovery_failed", "OAuth discovery endpoint was unavailable", status=502
                    )
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > MAX_METADATA_BYTES:
                        raise BusinessError(
                            "mcp_oauth_discovery_failed", "OAuth metadata exceeded its bound", status=502
                        )
        except httpx.HTTPError as exc:
            raise BusinessError("mcp_oauth_discovery_failed", "OAuth discovery was unreachable", status=502) from exc
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeError):
            raise BusinessError("mcp_oauth_discovery_failed", "OAuth metadata was not JSON", status=502) from None
        if not isinstance(parsed, dict):
            raise BusinessError("mcp_oauth_discovery_failed", "OAuth metadata was not an object", status=502)
        return cast(Packet, parsed)

    async def _metadata(self, endpoint: str) -> tuple[str, Packet]:
        url = urlsplit(endpoint)
        origin = urlunsplit((url.scheme, url.netloc, "", "", ""))
        candidates = [origin + "/.well-known/oauth-protected-resource"]
        if url.path not in {"", "/"}:
            candidates.insert(0, origin + "/.well-known/oauth-protected-resource" + url.path.rstrip("/"))
        resource: Packet = {}
        for candidate in candidates:
            try:
                resource = await self._fetch(candidate)
                break
            except BusinessError:
                continue
        servers = cast(list[object], resource.get("authorization_servers"))
        if not isinstance(cast(object, servers), list) or not servers or not isinstance(servers[0], str):
            raise BusinessError(
                "mcp_oauth_discovery_failed", "The MCP server does not advertise an authorization server", status=502
            )
        try:
            issuer = _endpoint(servers[0])
        except ValueError:
            raise BusinessError(
                "mcp_oauth_discovery_failed", "The advertised issuer is not HTTPS", status=502
            ) from None
        issuer_url = urlsplit(issuer)
        issuer_origin = urlunsplit((issuer_url.scheme, issuer_url.netloc, "", "", ""))
        metadata: Packet = {}
        for candidate in (
            issuer_origin + "/.well-known/oauth-authorization-server",
            issuer_origin + "/.well-known/openid-configuration",
        ):
            try:
                metadata = await self._fetch(candidate)
                break
            except BusinessError:
                continue
        if not metadata:
            raise BusinessError(
                "mcp_oauth_discovery_failed", "Authorization server metadata is unavailable", status=502
            )
        return issuer, metadata

    async def _client(self, target: ServerTarget, metadata: Packet, redirect_uri: str) -> Packet:
        stored = await self.tokens.client(target.server_id)
        if stored.get("client_id"):
            return stored
        registration = metadata.get("registration_endpoint")
        if not isinstance(registration, str):
            raise BusinessError(
                "mcp_oauth_registration_unavailable",
                "The authorization server requires a pre-registered client",
                status=422,
            )
        try:
            endpoint = _endpoint(registration)
        except ValueError:
            raise BusinessError(
                "mcp_oauth_discovery_failed", "Registration endpoint is not HTTPS", status=502
            ) from None
        client = self._client_factory(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(TOKEN_TIMEOUT_S, connect=FETCH_TIMEOUT_S),
        )
        document = {
            "client_name": "OmniOperator",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        async with client, client.stream("POST", endpoint, json=document) as response:
            if response.status_code not in {200, 201}:
                raise BusinessError(
                    "mcp_oauth_registration_failed", "Dynamic client registration was rejected", status=502
                )
            body = bytearray()
            async for part in response.aiter_bytes():
                body.extend(part)
                if len(body) > MAX_METADATA_BYTES:
                    raise BusinessError("mcp_oauth_registration_failed", "Registration exceeded its bound", status=502)
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeError):
            raise BusinessError(
                "mcp_oauth_registration_failed", "Registration response was invalid", status=502
            ) from None
        client_id = cast(Packet, parsed).get("client_id") if isinstance(parsed, dict) else None
        if not isinstance(client_id, str) or not 1 <= len(client_id) <= 200:
            raise BusinessError("mcp_oauth_registration_failed", "Registration returned no client id", status=502)
        value = {"client_id": client_id}
        if isinstance(cast(Packet, parsed).get("client_secret"), str):
            value["client_secret"] = cast(str, cast(Packet, parsed)["client_secret"])[:MAX_TOKEN_BYTES]
        await self.tokens.save_client(target.server_id, value)
        return value

    async def start(self, target: ServerTarget, redirect_uri: str) -> dict[str, Any]:
        if target.auth_mode != "oauth" or target.transport != "streamable_http":
            raise BusinessError("mcp_oauth_not_configured", "This server does not use remote OAuth", status=422)
        try:
            endpoint = _endpoint(target.endpoint)
        except ValueError:
            raise BusinessError(
                "mcp_oauth_endpoint_invalid", "Remote OAuth requires a valid HTTPS endpoint", status=422
            )
        redirect_uri = loopback_redirect(redirect_uri)
        issuer, metadata = await self._metadata(endpoint)
        authorization = metadata.get("authorization_endpoint")
        token = metadata.get("token_endpoint")
        try:
            authorization = _endpoint(authorization)
            token = _endpoint(token)
        except ValueError:
            raise BusinessError(
                "mcp_oauth_discovery_failed", "Authorization endpoints are invalid", status=502
            ) from None
        client = await self._client(target, metadata, redirect_uri)
        state = random_secrets.token_urlsafe(32)
        verifier = random_secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        )
        scopes = metadata.get("scopes_supported")
        scope = (
            " ".join(item for item in cast(list[object], scopes)[:8] if isinstance(item, str) and item)
            if isinstance(scopes, list)
            else ""
        )
        query = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if scope:
            query["scope"] = scope
        split = urlsplit(authorization)
        separator = "&" if split.query else ""
        url = urlunsplit((split.scheme, split.netloc, split.path, split.query + separator + urlencode(query), ""))
        expires = _now() + OAUTH_TTL_S
        async with self._lock:
            stale = [key for key, value in self._pending.items() if value["expires"] <= _now()]
            for key in stale:
                self._pending.pop(key, None)
            if len(self._pending) >= MAX_PENDING:
                raise BusinessError("mcp_oauth_busy", "Too many pending OAuth authorizations", status=503)
            self._pending[target.server_id] = {
                "state": state,
                "verifier": verifier,
                "redirect_uri": redirect_uri,
                "token_endpoint": token,
                "client_id": client["client_id"],
                "client_secret": client.get("client_secret", ""),
                "agent_id": target.agent_id,
                "config_id": target.server_id,
                "expires": expires,
                "scope": scope,
                "issuer": issuer,
            }
        return {
            "authorization_url": url,
            "expires_at": dt.datetime.fromtimestamp(expires, dt.timezone.utc).isoformat(timespec="microseconds"),
            "redirect_uri": redirect_uri,
            "scope": scope,
        }

    async def callback(self, state: str, code: str) -> dict[str, Any]:
        if not isinstance(cast(object, state), str) or not 1 <= len(state) <= 200:
            raise BusinessError("mcp_oauth_state_invalid", "Unknown or expired OAuth state", status=409)
        async with self._lock:
            match = None
            for server_id, value in list(self._pending.items()):
                if hmac.compare_digest(value["state"], state):
                    match = (server_id, value)
                    break
            if match is None:
                raise BusinessError("mcp_oauth_state_invalid", "Unknown or expired OAuth state", status=409)
            server_id, value = match
            self._pending.pop(server_id, None)
            if value["expires"] <= _now():
                raise BusinessError(
                    "mcp_oauth_state_expired", "The authorization request expired; start again", status=409
                )
        if (
            not isinstance(cast(object, code), str)
            or not 1 <= len(code) <= 4096
            or any(ord(char) < 33 or ord(char) > 126 for char in code)
        ):
            raise BusinessError("mcp_oauth_code_invalid", "The authorization code is invalid", status=422)
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": value["redirect_uri"],
            "client_id": value["client_id"],
            "code_verifier": value["verifier"],
        }
        if value.get("client_secret"):
            form["client_secret"] = value["client_secret"]
        payload = await self.tokens.post_form(value["token_endpoint"], form)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise BusinessError("mcp_oauth_token_invalid", "The token endpoint returned no access token", status=502)
        stored = {
            "access_token": token,
            "refresh_token": str(payload.get("refresh_token") or ""),
            "expires_at": _iso(int(payload.get("expires_in") or 3600)),
            "token_endpoint": value["token_endpoint"],
            "client_id": value["client_id"],
            "client_secret": value.get("client_secret", ""),
            "scope": str(payload.get("scope") or value.get("scope") or "")[:200],
            "issuer": value.get("issuer", ""),
        }
        await self.tokens.save(server_id, stored)
        return {
            "server_id": server_id,
            "agent_id": value["agent_id"],
            "config_id": value["config_id"],
            "scope": stored["scope"],
            "expires_at": stored["expires_at"],
        }

    def health(self) -> dict[str, Any]:
        return {"pending": len(self._pending), "max_pending": MAX_PENDING, "ttl_s": OAUTH_TTL_S}


__all__ = ["OAuthFlow", "OAuthTokens", "loopback_redirect"]
