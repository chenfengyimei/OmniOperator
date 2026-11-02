"""显式配置的远程MCP传输与人工OAuth；不扩大工具授权，不自动打开浏览器。"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ...system.filelock import FileLock
from ...system.protected_json import read_protected, write_protected


def origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("远程地址不能包含凭据")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError("远程MCP只接受HTTPS；HTTP仅供回环测试")
    return parsed.scheme + "://" + parsed.netloc.lower()


class TokenStorage:
    def __init__(self, path: Path):
        self.path = path

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        data = read_protected(self.path).get("tokens")
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens):
        with FileLock(self.path.with_suffix(".transaction.lock")):
            data = read_protected(self.path)
            data["tokens"] = tokens.model_dump(mode="json")
            write_protected(self.path, data)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        data = read_protected(self.path).get("client")
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def set_client_info(self, client_info):
        with FileLock(self.path.with_suffix(".transaction.lock")):
            data = read_protected(self.path)
            data["client"] = client_info.model_dump(mode="json")
            write_protected(self.path, data)


class RemoteAuthorization:
    def __init__(self, config, directory: Path):
        self.config = config
        key = hashlib.sha256((config.url + "\0" + config.oauth_redirect_uri).encode()).hexdigest()
        self.storage = TokenStorage(directory / (key + ".credential"))
        self._lock = threading.RLock()
        self._interactive = False
        self._url = ""
        self._state = ""
        self._result = None
        self._deadline = 0.0
        self.error = ""

    def begin(self):
        with self._lock:
            self._interactive = True
            self._url = self._state = self.error = ""
            self._result = None
            self._deadline = time.monotonic() + 120

    def status(self) -> dict:
        with self._lock:
            waiting = self._interactive and time.monotonic() < self._deadline
            return {
                "state": "waiting" if waiting else "idle",
                "authorization_url": self._url if waiting else "",
                "error": self.error,
            }

    def matches(self, state: str) -> bool:
        with self._lock:
            return bool(self._state and secrets.compare_digest(self._state, state))

    def can_authenticate(self) -> bool:
        with self._lock:
            if self._interactive and time.monotonic() < self._deadline:
                return True
        saved = read_protected(self.storage.path)
        return bool(saved.get("tokens") and saved.get("client"))

    def complete(self, code: str, state: str, issuer: str | None = None):
        from mcp.shared.auth import AuthorizationCodeResult

        with self._lock:
            if (
                not self._interactive
                or time.monotonic() >= self._deadline
                or not self._state
                or not secrets.compare_digest(state, self._state)
            ):
                raise ValueError("授权回调无效或已过期")
            if not code or len(code) > 8192 or self._result is not None:
                raise ValueError("授权码无效或已被消费")
            self._result = AuthorizationCodeResult(code=code, state=state, iss=issuer)

    def provider(self):
        from mcp.client.auth.oauth2 import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata

        allowed = {origin(self.config.url), *(origin(value) for value in self.config.oauth_allowed_origins)}
        owner = self

        class RestrictedProvider(OAuthClientProvider):
            async def async_auth_flow(self, request):
                flow = super().async_auth_flow(request)
                try:
                    outgoing = await anext(flow)
                    while True:
                        if origin(str(outgoing.url)) not in allowed:
                            raise ValueError("OAuth目标来源尚未被用户授权")
                        response = yield outgoing
                        outgoing = await flow.asend(response)
                except StopAsyncIteration:
                    return
                finally:
                    await flow.aclose()

        async def redirect(url):
            with owner._lock:
                if not owner._interactive or time.monotonic() >= owner._deadline:
                    raise ValueError("需要用户先点击授权")
                if origin(url) not in allowed:
                    raise ValueError("授权页面来源未被允许")
                values = parse_qs(urlsplit(url).query)
                owner._state = values.get("state", [""])[0]
                if not owner._state:
                    raise ValueError("授权页面缺少state")
                owner._url = url

        async def callback():
            while time.monotonic() < owner._deadline:
                with owner._lock:
                    if owner._result is not None:
                        owner._interactive = False
                        return owner._result
                await asyncio.sleep(0.05)
            raise TimeoutError("人工授权超时")

        metadata = OAuthClientMetadata(
            client_name="OmniOperator",
            redirect_uris=[self.config.oauth_redirect_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        )
        return RestrictedProvider(
            self.config.url, metadata, self.storage, redirect_handler=redirect, callback_handler=callback
        )


@asynccontextmanager
async def remote_transport(config, authorization):
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    auth = authorization.provider() if config.auth == "oauth" and authorization.can_authenticate() else None
    async with (
        httpx2.AsyncClient(auth=auth, timeout=config.timeout_s, trust_env=False, follow_redirects=False) as client,
        streamable_http_client(config.url, http_client=client, max_sse_event_size=1048576) as streams,
    ):
        yield streams
