"""MCP transport identity, fingerprints, stdio/HTTP sessions and bounded uncertainty."""

import asyncio
import hashlib
import json
import sys
import uuid

import pytest
from v2_mcp_helpers import TOOLS, FixtureHTTP, stdio_server

from omni.adapters.mcp.hosts import MCPHosts
from omni.adapters.mcp.http import HttpSession
from omni.adapters.mcp.stdio import StdioSession
from omni.domain.mcp import (
    MAX_TEXT_CHARS,
    MCPDispatchError,
    MCPUncertain,
    ServerTarget,
    catalog_fingerprint,
    catalog_rows,
    exposed_name,
    result_text,
    tool_fingerprint,
)

SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}, "additionalProperties": False}


def target(**overrides):
    values = {
        "agent_id": str(uuid.uuid4()),
        "server_id": str(uuid.uuid4()),
        "name": "notes",
        "transport": "stdio",
        "command": sys.executable,
        "arguments": (),
        "endpoint": "",
        "auth_mode": "none",
        "credential_ref": "",
        "authority_epoch": 1,
        "config_revision_id": str(uuid.uuid4()),
    }
    values.update(overrides)
    return ServerTarget(**values)


def test_tool_fingerprint_ignores_description_and_tracks_schema():
    changes = [
        {"name": "search", "inputSchema": SCHEMA},
        {
            "name": "search",
            "inputSchema": {**SCHEMA, "properties": {"q": {"type": "string"}, "z": {"type": "integer"}}},
        },
        {"name": "other", "inputSchema": SCHEMA},
    ]
    fingerprints = [tool_fingerprint(item["name"], item["inputSchema"]) for item in changes]
    assert len(set(fingerprints)) == 3
    rows = catalog_rows([{**TOOLS[0], "description": "first"}], "notes")
    same = catalog_rows([{**TOOLS[0], "description": "second"}], "notes")
    assert rows[0]["tool_fingerprint"] == same[0]["tool_fingerprint"]
    assert exposed_name("notes", "search") == "mcp__notes__search"
    assert catalog_fingerprint(rows) == catalog_fingerprint(same)


def test_catalog_rows_are_bounded_and_reject_bad_tools():
    with pytest.raises(Exception):
        catalog_rows([{"name": "bad name", "inputSchema": SCHEMA}], "notes")
    with pytest.raises(Exception):
        catalog_rows([{"name": "search", "inputSchema": {"type": "string"}}], "notes")
    deep = {"type": "object", "properties": {}}
    node = deep
    for _ in range(30):
        child = {"type": "object", "properties": {}}
        node["properties"]["x"] = child
        node = child
    with pytest.raises(Exception):
        catalog_rows([{"name": "deep", "inputSchema": deep}], "notes")


def test_result_text_is_attributed_and_bounded():
    text, data = result_text("notes", "search", {"content": [{"type": "text", "text": "x" * (MAX_TEXT_CHARS * 2)}]})
    assert len(text) <= MAX_TEXT_CHARS and data["truncated"] is True
    assert text.startswith("mcp server=notes tool=search")
    omitted, _ = result_text("notes", "image", {"content": [{"type": "image", "data": "AAAA"}]})
    assert "omitted non-text" in omitted and "AAAA" not in omitted


def test_stdio_session_paginates_calls_and_keeps_unknown_outcomes(tmp_path):
    async def scenario():
        definition, _ = stdio_server(tmp_path / "server", "ok", tmp_path / "calls.log")
        session = StdioSession(target(arguments=tuple(definition["arguments"])), cwd=str(tmp_path))
        try:
            handshake = await session.start()
            assert handshake["server_name"] == "fixture"
            raw = await session.catalog()
            assert [item["name"] for item in raw] == ["search", "write", "echo"]
            payload = await session.call("search", {"q": "日报"}, 5)
            assert payload["content"][0]["text"].endswith('{"q": "日报"}')
            assert (tmp_path / "calls.log").read_text(encoding="utf-8") == "call:search\n"
            error = await session.call("search", {"q": "x"}, 5)
            assert error["isError"] is False
            assert session.health()["running"] is True
        finally:
            assert await session.close()

    asyncio.run(scenario())


def test_stdio_is_error_is_a_result_and_crash_is_unknown(tmp_path):
    async def scenario():
        definition, _ = stdio_server(tmp_path / "server", "error")
        session = StdioSession(target(arguments=tuple(definition["arguments"])), cwd=str(tmp_path))
        try:
            await session.start()
            payload = await session.call("search", {"q": "x"}, 5)
            assert payload["isError"] is True
            assert session.health()["running"] is True
        finally:
            assert await session.close()
        crashed, _ = stdio_server(tmp_path / "crash", "crash")
        broken = StdioSession(target(arguments=tuple(crashed["arguments"])), cwd=str(tmp_path))
        try:
            await broken.start()
            with pytest.raises(MCPUncertain) as caught:
                await broken.call("write", {"text": "x"}, 5)
            assert caught.value.sent is True and caught.value.code == "mcp_connection_lost"
            assert broken.health()["running"] is False
        finally:
            assert await broken.close()

    asyncio.run(scenario())


def test_stdio_timeout_after_send_is_unknown_and_cancellation_is_bounded(tmp_path):
    async def scenario():
        definition, _ = stdio_server(tmp_path / "slow", "slow")
        session = StdioSession(target(arguments=tuple(definition["arguments"])), cwd=str(tmp_path))
        try:
            await session.start()
            with pytest.raises(MCPUncertain):
                await session.call("search", {"q": "x"}, 0.3)
            assert session.health()["cleanup_pending"] is False
        finally:
            assert await session.close()
        cancelled, _ = stdio_server(tmp_path / "cancel", "slow")
        second = StdioSession(target(arguments=tuple(cancelled["arguments"])), cwd=str(tmp_path))
        try:
            await second.start()
            task = asyncio.create_task(second.call("search", {"q": "x"}, 30))
            await asyncio.sleep(0.1)
            task.cancel()
            with pytest.raises((asyncio.CancelledError, MCPUncertain)):
                await task
            assert second.health()["running"] is False
        finally:
            assert await second.close()

    asyncio.run(scenario())


def test_stdio_broken_start_is_not_uncertain(tmp_path):
    async def scenario():
        definition, _ = stdio_server(tmp_path / "server", "crash_at_start")
        session = StdioSession(target(arguments=tuple(definition["arguments"])), cwd=str(tmp_path))
        try:
            with pytest.raises(MCPDispatchError) as caught:
                await session.start()
            assert caught.value.sent is False
        finally:
            assert await session.close()

    asyncio.run(scenario())


def test_http_session_json_sse_bearer_and_sse_unknown(tmp_path):
    async def scenario():
        with FixtureHTTP(require_token="remote-secret") as fixture:
            session = HttpSession(
                target(transport="streamable_http", endpoint=fixture.endpoint), lambda: "remote-secret"
            )
            try:
                handshake = await session.start()
                assert handshake["server_name"] == "http-fixture"
                raw = await session.catalog()
                assert len(raw) == 3
                payload = await session.call("search", {"q": "远程"}, 5)
                assert "远程结果" in payload["content"][0]["text"]
                assert fixture.session_ids[-1] == "fixture-session"
            finally:
                await session.close()
        with FixtureHTTP(require_token="remote-secret") as fixture:
            anonymous = HttpSession(target(transport="streamable_http", endpoint=fixture.endpoint), lambda: "")
            try:
                with pytest.raises(MCPDispatchError) as caught:
                    await anonymous.start()
                assert caught.value.code == "mcp_unauthorized" and caught.value.sent is False
            finally:
                await anonymous.close()
        with FixtureHTTP(sse=True) as fixture:
            streaming = HttpSession(target(transport="streamable_http", endpoint=fixture.endpoint), lambda: "")
            try:
                await streaming.start()
                payload = await streaming.call("search", {"q": "sse"}, 5)
                assert "sse" in payload["content"][0]["text"]
            finally:
                await streaming.close()
        with FixtureHTTP(mode="status500") as fixture:
            failing = HttpSession(target(transport="streamable_http", endpoint=fixture.endpoint), lambda: "")
            try:
                await failing.start()
                with pytest.raises(MCPUncertain) as caught:
                    await failing.call("search", {"q": "x"}, 5)
                assert caught.value.sent is True
            finally:
                await failing.close()

    asyncio.run(scenario())


def test_hosts_own_one_session_per_generation_and_reclaim_stale(tmp_path):
    async def scenario():
        definition, _ = stdio_server(tmp_path / "server", "ok")
        first = target(arguments=tuple(definition["arguments"]), authority_epoch=1)
        hosts = MCPHosts(lambda _target: asyncio.sleep(0), cwd=str(tmp_path))
        try:
            handshake = await hosts.connect(first)
            assert handshake["transport"] == "stdio" and handshake["pid"] > 0
            again = await hosts.connect(first)
            assert again["pid"] == handshake["pid"]
            generation = target(
                agent_id=first.agent_id,
                server_id=first.server_id,
                name=first.name,
                transport=first.transport,
                command=first.command,
                arguments=first.arguments,
                authority_epoch=2,
                config_revision_id=first.config_revision_id,
            )
            second = await hosts.connect(generation)
            assert second["pid"] != handshake["pid"]
            assert hosts.health()["resident"] == 1
            assert await hosts.stop(first.agent_id, first.server_id)
            assert hosts.health()["resident"] == 0
        finally:
            assert await hosts.close()

    asyncio.run(scenario())


def test_argument_digest_is_stable_and_bounded():
    from omni.domain.mcp import argument_digest

    assert (
        argument_digest({"q": "日报", "n": 1})
        == hashlib.sha256(
            json.dumps({"n": 1, "q": "日报"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    with pytest.raises(Exception):
        argument_digest({"q": "x" * 300000})
