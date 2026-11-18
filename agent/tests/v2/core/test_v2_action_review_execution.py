"""Real isolated plugin crash, retained source identity and owner reconciliation never replay the action."""

import asyncio

from omni_test_support.v2.v2_action_review_helpers import review_request
from omni_test_support.v2.v2_extension_helpers import (
    PluginModel,
    authorization,
    configured,
    installed,
    plugin_source,
    wait_run,
)

from omni.composition.instance import open_instance


def test_crashed_plugin_remains_unknown_after_revoke_and_owner_confirmation(tmp_path):
    async def scenario():
        marker = tmp_path / "written-once"
        source = plugin_source(tmp_path / "source", call_marker=marker, crash=True)
        instance = await open_instance(tmp_path / "home", model=PluginModel(), capabilities=())
        try:
            agent, extension = await installed(instance, source)
            enabled = await instance.extensions.authorize(agent, extension["id"], authorization(extension))
            await configured(instance, agent)
            turn = await instance.application.submit_turn(agent, None, "once", "Use this isolated plugin")
            await wait_run(instance, agent, turn["run_id"], {"waiting_approval"})
            approval = (await instance.application.approvals(agent))[0]
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            await wait_run(instance, agent, turn["run_id"], {"failed", "interrupted"})
            async with asyncio.timeout(5):
                while instance.application.health()["active_runs"]:
                    await asyncio.sleep(0.01)
            action = (await instance.application.query("actions", agent, run_id=turn["run_id"]))[0]
            await instance.extensions.disable(agent, extension["id"], enabled["revision"])
            inspected = await instance.action_reviews.get(agent, action["id"])
            assert inspected["eligible"] and inspected["proof"]["source"]["kind"] == "plugin"
            assert inspected["proof"]["source"]["package_sha256"] == enabled["package_sha256"]
            result = await instance.action_reviews.review(
                agent, action["id"], review_request(inspected, "applied", "Owner inspected the isolated marker file")
            )
            assert result["resolved"] and result["state"] == result["effect"] == "unknown"
            assert (await instance.application.query("actions", agent, run_id=turn["run_id"])) == [action]
            assert marker.read_text() == "approved-value"
            assert (await instance.application.get_run(agent, turn["run_id"]))["state"] != "succeeded"
            assert not await instance.application.approvals(agent)
            assert (await instance.extensions.get(agent, extension["id"]))["enabled"] is False
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
