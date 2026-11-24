"""Replay the documented owner workflow via HTTP, the real file worker and Python plugin host."""

import hashlib
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from omni_test_support.paths import REPOSITORY_ROOT
from omni_test_support.v2.extensions_http import owner
from omni_test_support.v2.v2_extension_helpers import authorization, configured, installation, wait_run

from omni.composition.http import http_app
from omni.domain.background_review import REVIEW_PROMPT
from omni.domain.contracts import Invocation, ModelReply

EXAMPLE = REPOSITORY_ROOT / "agent/examples/extensions/text-summary"
TOOL = "ext__text_summary__summarize"


class ExampleModel:
    """Issue the requested invocation even when unoffered, to exercise actual dispatch rejection."""

    async def complete(self, messages, tools, settings):
        if messages[0]["content"] == REVIEW_PROMPT:
            return ModelReply("Nothing to save.", cost_micro=1)
        if messages[-1]["role"] == "tool":
            self.reply = messages[-1]["content"]
            return ModelReply("done", cost_micro=1)
        self.offered = {spec.name for spec in tools}
        return ModelReply("", (self.call,), cost_micro=1)


def test_owner_can_build_review_use_upgrade_and_disable_summary(tmp_path):
    home = tmp_path / "home"
    market = tmp_path / "workspace"
    draft = market / "text-summary"
    draft.mkdir(parents=True)
    model = ExampleModel()
    with TestClient(http_app(home, model=model)) as client:
        agent, extensions = owner(client, home)
        base = f"/api/v1/agents/{agent}"
        runtime = client.app.state.runtime
        client.portal.call(configured, runtime, agent)
        settings = client.get(base + "/settings").json()
        response = client.put(
            base + "/settings",
            json={"value": {**settings["value"], "workspace": str(market)}, "revision": settings["revision"]},
        )
        assert response.status_code == 200, response.text

        def run_tool(name, args, *, offered):
            model.call = Invocation(str(uuid4()), name, args)
            model.reply = None
            receipt = client.post(base + "/turns", json={"key": str(uuid4()), "message": name, "mode": "agent"})
            assert receipt.status_code < 300, receipt.text
            run_id = receipt.json()["run_id"]

            def wait(states):
                return client.portal.call(wait_run, runtime, agent, run_id, states | {"failed", "interrupted"})

            if offered:
                waiting = wait({"waiting_approval", "succeeded"})
                assert waiting["state"] == "waiting_approval", waiting
                [approval] = client.get(base + "/approvals").json()["items"]
                assert approval["tool"] == name
                decision = client.post(
                    base + "/approvals/" + approval["id"],
                    json={"allowed": True, "policy_revision": approval["policy_revision"]},
                )
                assert decision.status_code == 200, decision.text
            final = wait({"succeeded", "waiting_approval"} if not offered else {"succeeded"})
            assert final["state"] == "succeeded", final
            assert (name in model.offered) == offered
            actions = client.portal.call(lambda: runtime.application.query("actions", agent, run_id=run_id))
            if offered:
                [action] = actions
                assert action["state"] == "ok", action
                return action["result"]["data"]
            assert not actions and "Unknown capability" in str(model.reply)

        for filename in ("extension.json", "plugin.py"):
            content = (EXAMPLE / filename).read_text(encoding="utf-8")
            run_tool("files_write", {"path": "text-summary/" + filename, "content": content}, offered=True)
            assert (draft / filename).read_text(encoding="utf-8") == content

        def scan_and_install(key):
            scan = client.post(extensions + "/market", json={"source": str(market)})
            assert scan.status_code == 200, scan.text
            [item] = scan.json()["items"]
            assert item["source"] == str(draft) and item["name"] == "text_summary"
            result = client.post(
                extensions, json=installation(item["source"], key, item["extension_id"], item["revision"])
            )
            assert result.status_code == 201, result.text
            record = result.json()
            assert not record["enabled"]
            return record

        first = scan_and_install("install-one")
        target = extensions + "/" + first["id"]
        args = {"text": "  第一行\n\n第二行"}
        expected = {"headline": "第一行", "nonempty_lines": 2, "characters": len(args["text"])}
        run_tool(TOOL, args, offered=False)
        grant = authorization(first)
        assert client.put(target + "/authorization", json=grant).json()["enabled"]
        assert run_tool(TOOL, args, offered=True) == expected
        assert json.loads(model.reply) == expected

        path = draft / "extension.json"
        manifest = json.loads(path.read_bytes())
        manifest["version"] = "2.0"
        run_tool(
            "files_write",
            {
                "path": "text-summary/extension.json",
                "content": json.dumps(manifest, ensure_ascii=False),
                "expected_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            },
            offered=True,
        )
        second = scan_and_install("install-two")
        assert second["id"] == first["id"] and second["version"] == "2.0"
        assert second["package_sha256"] != first["package_sha256"]
        assert client.put(target + "/authorization", json=grant).status_code == 409
        stale_package = {**authorization(second), "package_sha256": first["package_sha256"]}
        assert client.put(target + "/authorization", json=stale_package).status_code == 409
        run_tool(TOOL, args, offered=False)
        enabled = client.put(target + "/authorization", json=authorization(second)).json()
        assert enabled["enabled"] and run_tool(TOOL, args, offered=True) == expected
        assert not client.post(target + "/disable", json={"revision": enabled["revision"]}).json()["enabled"]
        run_tool(TOOL, args, offered=False)
