"""K09 model-side history recall: pure domain rules and the production session_search chain."""

from __future__ import annotations

import uuid

import pytest
from omni_test_support.v2.execution import (
    Invocation as Invocation,
)
from omni_test_support.v2.execution import (
    ModelReply as ModelReply,
)
from omni_test_support.v2.execution import (
    Outcome as Outcome,
)
from omni_test_support.v2.execution import (
    asyncio as asyncio,
)
from omni_test_support.v2.execution import (
    configure as configure,
)
from omni_test_support.v2.execution import (
    open_instance as open_instance,
)
from omni_test_support.v2.execution import (
    wait_state as wait_state,
)

from omni.application.session_search import SessionSearchCapabilities
from omni.domain.contracts import BusinessError, InvocationContext
from omni.domain.session_search import (
    MAX_EXCLUDE,
    MAX_RESULTS,
    MAX_WINDOW,
    browse_entry,
    group_discover,
    parse_request,
    query_tokens,
    render,
    shape_message,
    truncate_content,
)


def test_mode_inference_and_invalid_anchor() -> None:
    assert parse_request({"query": "deploy"}).mode == "discover"
    assert parse_request({"session_id": "s", "around_seq": 3}).mode == "scroll"
    assert parse_request({"session_id": "s"}).mode == "read"
    assert parse_request({}).mode == "browse"
    for bad in (
        {"session_id": "s", "around_seq": 0},
        {"session_id": "s", "around_seq": True},
        {"session_id": "s", "around_seq": "x"},
    ):
        with pytest.raises(BusinessError) as exc:
            parse_request(bad)
        assert exc.value.code == "invalid_session_query"


def test_clamps_bounds_and_rejects_unbounded() -> None:
    req = parse_request({"query": "q", "limit": 999, "window": 999, "head": 999, "tail": -5})
    assert req.limit == MAX_RESULTS and req.window == MAX_WINDOW and req.head == 50 and req.tail == 0
    with pytest.raises(BusinessError):
        parse_request({"query": "x" * 501})
    with pytest.raises(BusinessError):
        parse_request({"query": "bad\x00text"})
    with pytest.raises(BusinessError):
        parse_request({"query": "q", "role_filter": "user,bogus"})
    excluded = parse_request({"query": "q", "exclude_session_ids": [str(uuid.uuid4()) for _ in range(40)]})
    assert len(excluded.exclude) == MAX_EXCLUDE


def test_query_tokens_unicode_and_cap() -> None:
    assert query_tokens("deploy 部署 now") == ("deploy", "部署", "now")
    assert len(query_tokens(" ".join(f"t{i}" for i in range(50)))) == 20


def test_truncate_and_shape_message_anchor() -> None:
    text, truncated, original = truncate_content("abc", 2)
    assert text == "ab…" and truncated and original == 3
    row = {"id": "m", "seq": 4, "role": "user", "content": "x" * 3000, "created_at": "t"}
    shaped = shape_message(row, anchor_seq=4, max_content=2000)
    assert shaped["anchor"] is True and shaped["content_truncated"] is True
    assert shaped["original_content_chars"] == 3000 and len(str(shaped["content"])) == 2001
    assert "anchor" not in shape_message(row, anchor_seq=5, max_content=2000)


def test_group_discover_dedups_by_session_and_limits() -> None:
    rows = [
        {
            "session_id": "a",
            "id": "m1",
            "seq": 1,
            "role": "user",
            "content": "deploy",
            "title": "A",
            "session_created_at": "t2",
        },
        {
            "session_id": "a",
            "id": "m2",
            "seq": 2,
            "role": "assistant",
            "content": "deploy again",
            "title": "A",
            "session_created_at": "t2",
        },
        {
            "session_id": "b",
            "id": "m3",
            "seq": 1,
            "role": "user",
            "content": "deploy",
            "title": "B",
            "session_created_at": "t1",
        },
    ]
    results = group_discover(rows, 10)
    assert [r["session_id"] for r in results] == ["a", "b"]
    assert results[0]["match_message_id"] == "m1" and results[0]["match_seq"] == 1
    assert len(group_discover(rows, 1)) == 1


def test_browse_entry_and_render() -> None:
    entry = browse_entry({"session_id": "a", "title": "A", "created_at": "t", "message_count": 3, "preview": "p" * 300})
    assert entry["preview_truncated"] is True and entry["message_count"] == 3
    assert "No matching sessions" in render("discover", {"results": []})
    assert "Found 1" in render(
        "discover",
        {"results": [{"session_id": "a", "title": "A", "match_seq": 1, "matched_role": "user", "snippet": "s"}]},
    )
    assert "recent session" in render("browse", {"results": [entry]})
    assert "around seq=4" in render(
        "scroll",
        {"session_id": "a", "around_seq": 4, "window": 1, "messages_before": 0, "messages_after": 0, "messages": []},
    )


class FinishModel:
    async def complete(self, messages, tools, settings):
        return ModelReply("done", cost_micro=0)


class SearchModel:
    async def complete(self, messages, tools, settings):
        if any(m.get("role") == "tool" for m in messages):
            return ModelReply("done", cost_micro=0)
        return ModelReply(
            "", (Invocation("s1", "session_search", {"query": "deploy"}),), cost_micro=0, completion_reason="tool_calls"
        )


async def seed(tmp_path, model=None):
    instance = await open_instance(tmp_path, model=model or FinishModel())
    instance.maintenance.stop_accepting()
    app = instance.application
    agent = await configure(app, instance._database)
    return instance, app, agent


async def turn(app, agent, session_id, key, text):
    receipt = await app.submit_turn(agent, session_id, key, text)
    run = await wait_state(app, agent, receipt["run_id"], {"succeeded"})
    return receipt["run_id"], str(run["session_id"])


def capability(instance):
    return SessionSearchCapabilities(instance._database)


def context(agent, run_id, session_id):
    return InvocationContext(agent, run_id, session_id, 1)


def test_discover_finds_past_session_with_anchor_and_excludes_current(tmp_path):
    async def scenario():
        instance, app, agent = await seed(tmp_path)
        try:
            _run_a, session_a = await turn(app, agent, None, "k1", "We discussed the deploy pipeline")
            run_b, session_b = await turn(app, agent, None, "k2", "Gardening tomatoes")
            cap = capability(instance)
            result = await cap.invoke(
                "session_search", {"query": "deploy"}, operation_id="op", context=context(agent, run_b, session_b)
            )
            assert result.status == Outcome.OK
            results = result.data["results"]
            assert len(results) == 1 and results[0]["session_id"] == session_a
            assert results[0]["match_message_id"] and results[0]["match_seq"] == 1
            assert "deploy" in str(results[0]["snippet"])
            empty = await cap.invoke(
                "session_search",
                {"query": "nonexistentword"},
                operation_id="op",
                context=context(agent, run_b, session_b),
            )
            assert empty.data["results"] == []
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_agent_isolation_returns_not_found(tmp_path):
    async def scenario():
        instance, app, agent = await seed(tmp_path)
        try:
            run_a, session_a = await turn(app, agent, None, "k1", "deploy pipeline")
            cap = capability(instance)
            other = str(uuid.uuid4())
            result = await cap.invoke(
                "session_search", {"query": "deploy"}, operation_id="op", context=context(other, run_a, session_a)
            )
            assert result.status == Outcome.ERROR and result.error_code == "not_found"
            read = await cap.invoke(
                "session_search", {"session_id": session_a}, operation_id="op", context=context(other, run_a, session_a)
            )
            assert read.error_code == "not_found"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_scroll_window_and_boundary_counts(tmp_path):
    async def scenario():
        instance, app, agent = await seed(tmp_path)
        try:
            run_a, session_a = await turn(app, agent, None, "k1", "deploy one")
            for i in range(2, 5):
                run_a, _ = await turn(app, agent, session_a, f"k{i}", f"deploy {i}")
            cap = capability(instance)
            result = await cap.invoke(
                "session_search",
                {"session_id": session_a, "around_seq": 4, "window": 1},
                operation_id="op",
                context=context(agent, run_a, session_a),
            )
            assert result.status == Outcome.OK
            seqs = [m["seq"] for m in result.data["messages"]]
            assert seqs == [3, 4, 5]
            assert result.data["messages_before"] == 2 and result.data["messages_after"] == 3
            assert any(m.get("anchor") for m in result.data["messages"])
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_read_head_tail_truncation(tmp_path):
    async def scenario():
        instance, app, agent = await seed(tmp_path)
        try:
            run_a, session_a = await turn(app, agent, None, "k1", "deploy one")
            for i in range(2, 5):
                run_a, _ = await turn(app, agent, session_a, f"k{i}", f"deploy {i}")
            cap = capability(instance)
            result = await cap.invoke(
                "session_search",
                {"session_id": session_a, "head": 2, "tail": 2},
                operation_id="op",
                context=context(agent, run_a, session_a),
            )
            assert result.data["message_count"] == 8 and result.data["truncated"] is True
            seqs = [m["seq"] for m in result.data["messages"]]
            assert seqs == [1, 2, 7, 8]
            full = await cap.invoke(
                "session_search",
                {"session_id": session_a, "head": 50, "tail": 50},
                operation_id="op",
                context=context(agent, run_a, session_a),
            )
            assert full.data["truncated"] is False and full.data["message_count"] == 8
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_browse_recent_excludes_current(tmp_path):
    async def scenario():
        instance, app, agent = await seed(tmp_path)
        try:
            _run_a, session_a = await turn(app, agent, None, "k1", "deploy pipeline")
            run_b, session_b = await turn(app, agent, None, "k2", "gardening")
            cap = capability(instance)
            result = await cap.invoke("session_search", {}, operation_id="op", context=context(agent, run_b, session_b))
            ids = [r["session_id"] for r in result.data["results"]]
            assert session_a in ids and session_b not in ids
            assert all(r["message_count"] >= 1 for r in result.data["results"])
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_erasure_barrier_floors_session_history(tmp_path):
    async def scenario():
        instance, app, agent = await seed(tmp_path)
        try:
            run_a, session_a = await turn(app, agent, None, "k1", "deploy pipeline")
            await turn(app, agent, session_a, "k2", "deploy again")
            messages = await instance._database.read(lambda u: u.messages(agent, session_a))
            await app.erase_message(agent, str(messages[0]["id"]))
            cap = capability(instance)
            discover = await cap.invoke(
                "session_search", {"query": "deploy"}, operation_id="op", context=context(agent, run_a, session_a)
            )
            assert discover.data["results"] == []
            read = await cap.invoke(
                "session_search", {"session_id": session_a}, operation_id="op", context=context(agent, run_a, session_a)
            )
            assert read.data["message_count"] == 0
            scroll = await cap.invoke(
                "session_search",
                {"session_id": session_a, "around_seq": 1},
                operation_id="op",
                context=context(agent, run_a, session_a),
            )
            assert scroll.status == Outcome.ERROR and scroll.error_code == "context_invalidated"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_invalid_arguments_and_unknown_tool_map_to_error(tmp_path):
    async def scenario():
        instance, app, agent = await seed(tmp_path)
        try:
            run_a, session_a = await turn(app, agent, None, "k1", "deploy")
            cap = capability(instance)
            bad = await cap.invoke(
                "session_search",
                {"session_id": session_a, "around_seq": 0},
                operation_id="op",
                context=context(agent, run_a, session_a),
            )
            assert bad.status == Outcome.ERROR and bad.error_code == "invalid_session_query"
            unknown = await cap.invoke("other_tool", {}, operation_id="op", context=context(agent, run_a, session_a))
            assert unknown.error_code == "tool_not_found"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_engine_dispatches_production_session_search(tmp_path):
    async def scenario():
        instance, app, agent = await seed(tmp_path, model=SearchModel())
        try:
            _run_a, session_a = await turn(app, agent, None, "k1", "deploy pipeline discussion")
            run_b, session_b = await turn(app, agent, None, "k2", "recall the deploy topic")
            actions = await app.query("actions", agent, run_id=run_b)
            search = [a for a in actions if a["tool"] == "session_search"]
            assert len(search) == 1 and search[0]["state"] == "ok"
            results = search[0]["result"]["data"]["results"]
            assert any(r["session_id"] == session_a for r in results)
            assert session_b not in [r["session_id"] for r in results]
        finally:
            await instance.close()

    asyncio.run(scenario())
