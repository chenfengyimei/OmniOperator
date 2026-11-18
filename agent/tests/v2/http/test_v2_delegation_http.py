"""Owner API and a real isolated MCP worker validate inherited grants on delegated production runs."""

import uuid

from omni_test_support.v2.mcp_flow import authorize_search, configure, connect, owner_client, stdio_server, wait_run

from omni.domain.contracts import Invocation, ModelReply


def test_owner_delegation_view_and_child_inherit_mcp_without_expanding_grants(tmp_path):
    class Model:
        async def complete(self, messages, tools, settings):
            child = any(m["role"] == "user" and str(m["content"]).startswith("Delegated task.") for m in messages)
            names = {tool.name for tool in tools}
            assert "mcp__notes__search" in names and "mcp__notes__write" not in names
            if not any(m["role"] == "tool" for m in messages):
                if child:
                    assert "delegate_task" not in names
                    return ModelReply(
                        "search",
                        (Invocation("search", "mcp__notes__search", {"q": "child-only-canary"}),),
                        cost_micro=7,
                    )
                return ModelReply(
                    "delegate", (Invocation("delegate", "delegate_task", {"goal": "search notes"}),), cost_micro=7
                )
            if child:
                assert "child-only-canary" in str(messages)
                return ModelReply("MCP child summary", cost_micro=7)
            assert "MCP child summary" in str(messages) and "child-only-canary" not in str(messages)
            return ModelReply("parent completed", cost_micro=7)

    definition, _ = stdio_server(tmp_path / "server", "ok", tmp_path / "calls.log")
    client, _agent, base = owner_client(tmp_path / "home", Model())
    try:
        config = configure(client, base, "notes", definition)
        assert connect(client, base, config)["connected"]
        catalog = client.get(base + f"/mcp/servers/{config['id']}/catalog").json()
        authorize_search(client, base, config, next(t for t in catalog["items"] if t["name"] == "search"))
        receipt = client.post(
            base + "/turns", json={"key": "delegate", "message": "search through a child", "mode": "agent"}
        ).json()
        assert wait_run(client, base, receipt["run_id"])["state"] == "succeeded"
        target = base + "/runs/" + receipt["run_id"] + "/delegations"
        response = client.get(target)
        assert response.status_code == 200
        child = response.json()["items"][0]
        assert (
            child["summary"] == "MCP child summary"
            and child["known_cost_micro"] == 14
            and child["unknown_model_calls"] == 0
        )
        calls = client.get(base + f"/mcp/servers/{config['id']}/calls").json()["items"]
        assert len(calls) == 1 and calls[0]["run_id"] == child["child_run_id"]
        assert (tmp_path / "calls.log").read_text(encoding="utf-8") == "call:search\n"
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/runs/{receipt['run_id']}/delegations").status_code == 404
        assert client.post(target, json={"goal": "bypass"}).status_code == 405
        client.headers.pop("Authorization")
        assert client.get(target).status_code == 401
    finally:
        client.__exit__(None, None, None)
