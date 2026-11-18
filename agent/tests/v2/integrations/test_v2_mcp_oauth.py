"""Explicit MCP OAuth: PKCE/state authorization, single-use callbacks, vault tokens and refresh."""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from omni_test_support.v2.v2_mcp_helpers import CallModel, FixtureHTTP, SeededSecrets

from omni.adapters.mcp.oauth import OAuthTokens
from omni.composition.http import http_app
from omni.domain.mcp import oauth_reference

REDIRECT = "http://127.0.0.1:8766/oauth/callback"


class AsyncSecrets:
    """AsyncSecretPort view over the in-memory SecretStore used by injected instances."""

    def __init__(self, store):
        self.store = store

    async def get(self, reference):
        return self.store.get(reference)

    async def set(self, reference, value):
        self.store.set(reference, value)

    async def delete(self, reference):
        self.store.delete(reference)

    def invalidate(self, reference):
        return None

    def stop_accepting(self):
        return None

    def health(self):
        return {"available": True}

    async def close(self, timeout_s=30):
        return True


def owner_client(tmp_path, model, secrets):
    client = TestClient(http_app(tmp_path, model=model, credential_backend=secrets))
    client.__enter__()
    client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text(encoding="utf-8")
    agent = client.get("/api/v1/agents").json()["items"][0]["id"]
    base = f"/api/v1/agents/{agent}"
    settings = client.get(base + "/settings").json()
    profile = client.get(base + "/configurations/" + settings["value"]["model_profile_id"]).json()
    client.put(
        base + "/configurations/" + profile["id"],
        json={
            "name": profile["name"],
            "enabled": True,
            "revision": profile["revision"],
            "value": {**profile["value"], "model": "fake"},
        },
    )
    return client, base


def configure(client, base, value, name="remote"):
    response = client.post(
        base + "/configurations", json={"kind": "mcp", "name": name, "enabled": True, "value": value}
    )
    assert response.status_code == 201, response.text
    return response.json()


def start(client, base, config):
    return client.post(base + f"/mcp/servers/{config['id']}/oauth/start", json={"redirect_uri": REDIRECT})


def test_oauth_start_uses_pkce_and_callback_revokes_old_authorization(tmp_path):
    secrets = SeededSecrets()
    with FixtureHTTP(oauth=True) as fixture:
        client, base = owner_client(tmp_path, CallModel("mcp__remote__search", {"q": "x"}), secrets)
        try:
            config = configure(client, base, fixture.configuration(auth_mode="oauth"))
            connected = client.post(
                base + f"/mcp/servers/{config['id']}/connect", json={"revision": config["revision"]}
            ).json()
            assert connected["connected"] is True
            catalog = client.get(base + f"/mcp/servers/{config['id']}/catalog").json()
            search = next(row for row in catalog["items"] if row["name"] == "search")
            authorized = client.put(
                base + f"/mcp/servers/{config['id']}/tools/{search['tool_id']}/authorization",
                json={
                    "catalog_fingerprint": catalog["catalog"]["fingerprint"],
                    "read_only": True,
                    "authorized": True,
                    "evidence": "owner reviewed",
                },
            ).json()
            assert authorized["authorized"] == ["search"]

            response = start(client, base, config)
            assert response.status_code == 200, response.text
            started = response.json()
            query = parse_qs(urlsplit(started["authorization_url"]).query)
            assert query["response_type"] == ["code"]
            assert query["client_id"] == ["fixture-client"]
            assert query["redirect_uri"] == [REDIRECT]
            assert query["code_challenge_method"] == ["S256"] and len(query["code_challenge"][0]) == 43
            assert query["scope"] == ["mcp.read"]
            assert fixture.authorizations[0]["redirect_uris"] == [REDIRECT]
            state = query["state"][0]

            wrong = client.get("/oauth/callback", params={"state": "not-the-state", "code": "fixture-code"})
            assert wrong.status_code == 409
            callback = client.get("/oauth/callback", params={"state": state, "code": "fixture-code"})
            assert callback.status_code == 200 and "授权已完成" in callback.text
            form = fixture.token_requests[-1]
            assert form["grant_type"] == "authorization_code" and form["code"] == "fixture-code"
            assert 40 <= len(form["code_verifier"]) <= 128 and form["redirect_uri"] == REDIRECT
            replay = client.get("/oauth/callback", params={"state": state, "code": "fixture-code"})
            assert replay.status_code == 409

            stored = json.loads(secrets.values[oauth_reference(config["id"])])
            assert stored["access_token"] == "fixture-access" and stored["refresh_token"] == "fixture-refresh"
            assert "fixture-access" not in client.get(base + "/audit").text
            after = client.get(base + f"/mcp/servers/{config['id']}").json()
            assert after["state"] == "idle" and after["authority_epoch"] == 2
            assert after["authorized_tools"] == 0

            reconnected = client.post(
                base + f"/mcp/servers/{config['id']}/connect", json={"revision": config["revision"]}
            ).json()
            assert reconnected["connected"] is True
            assert fixture.authorization_headers[-1] == "Bearer fixture-access"
        finally:
            client.__exit__(None, None, None)


def test_oauth_start_rejects_foreign_redirect_and_non_oauth_servers(tmp_path):
    secrets = SeededSecrets()
    with FixtureHTTP(oauth=True) as fixture:
        client, base = owner_client(tmp_path, CallModel("mcp__remote__search", {"q": "x"}), secrets)
        try:
            config = configure(client, base, fixture.configuration(auth_mode="oauth"))
            foreign = client.post(
                base + f"/mcp/servers/{config['id']}/oauth/start",
                json={"redirect_uri": "https://attacker.example/oauth/callback"},
            )
            assert foreign.status_code == 422 and foreign.json()["error"]["code"] == "mcp_oauth_redirect_invalid"
            plain = configure(client, base, fixture.configuration(auth_mode="none"), name="plain")
            not_oauth = start(client, base, plain)
            assert not_oauth.status_code == 422 and not_oauth.json()["error"]["code"] == "mcp_oauth_not_configured"
            unauthenticated = client.get(base + f"/mcp/servers/{config['id']}", headers={"Authorization": ""})
            assert unauthenticated.status_code == 401
        finally:
            client.__exit__(None, None, None)


def test_expired_access_token_is_refreshed_without_new_authorization(tmp_path):
    secrets = SeededSecrets()
    with FixtureHTTP(oauth=True) as fixture:
        config_id = "11111111-1111-1111-1111-111111111111"
        secrets.values[oauth_reference(config_id)] = json.dumps(
            {
                "access_token": "stale-access",
                "refresh_token": "fixture-refresh",
                "expires_at": "2000-01-01T00:00:00+00:00",
                "token_endpoint": fixture.endpoint.replace("/mcp", "/token"),
                "client_id": "fixture-client",
            }
        )
        tokens = OAuthTokens(AsyncSecrets(secrets))

        async def scenario():
            token = await tokens.access_token(config_id)
            assert token == "fixture-access-refreshed"
            assert fixture.token_requests[-1]["grant_type"] == "refresh_token"
            stored = json.loads(secrets.values[oauth_reference(config_id)])
            assert stored["access_token"] == "fixture-access-refreshed" and stored["refresh_token"] == "fixture-refresh"

        asyncio.run(scenario())


def test_a_second_start_supersedes_the_first_state(tmp_path):
    secrets = SeededSecrets()
    with FixtureHTTP(oauth=True) as fixture:
        client, base = owner_client(tmp_path, CallModel("mcp__remote__search", {"q": "x"}), secrets)
        try:
            config = configure(client, base, fixture.configuration(auth_mode="oauth"))
            first = parse_qs(urlsplit(start(client, base, config).json()["authorization_url"]).query)["state"][0]
            second = parse_qs(urlsplit(start(client, base, config).json()["authorization_url"]).query)["state"][0]
            assert first != second
            stale = client.get("/oauth/callback", params={"state": first, "code": "fixture-code"})
            assert stale.status_code == 409
            current = client.get("/oauth/callback", params={"state": second, "code": "fixture-code"})
            assert current.status_code == 200
        finally:
            client.__exit__(None, None, None)
