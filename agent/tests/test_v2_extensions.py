"""Real isolated Python host execution, immutable installation and approval/revocation certainty."""

import asyncio

from sqlalchemy import select
from v2_extension_helpers import (
    PUBLIC_TOOL,
    PluginModel,
    authorization,
    configured,
    installation,
    installed,
    plugin_source,
    wait_run,
)

from omni.adapters.persistence import schema as s
from omni.composition.instance import open_instance


def test_static_install_and_enable_do_not_execute_plugin_and_owner_approval_runs_isolated_code(tmp_path):
    async def scenario():
        marker, called = tmp_path / "imported", tmp_path / "called"
        source = plugin_source(tmp_path / "source", marker=marker, call_marker=called)
        model = PluginModel()
        instance = await open_instance(tmp_path / "home", model=model, capabilities=())
        try:
            agent, extension = await installed(instance, source)
            assert not extension["enabled"] and not marker.exists()
            assert (await instance.extensions.page(agent))["items"][0]["id"] == extension["id"]
            enabled = await instance.extensions.authorize(agent, extension["id"], authorization(extension))
            assert enabled["enabled"] and not marker.exists()
            await configured(instance, agent)
            receipt = await instance.application.submit_turn(agent, None, "turn", "Use the fixture plugin")
            await wait_run(instance, agent, receipt["run_id"], {"waiting_approval"})
            assert not marker.exists() and not called.exists()
            approvals = await instance.application.approvals(agent)
            assert len(approvals) == 1 and approvals[0]["tool"] == PUBLIC_TOOL
            await instance.application.approve(agent, approvals[0]["id"], True, approvals[0]["policy_revision"])
            final = await wait_run(instance, agent, receipt["run_id"], {"succeeded", "failed", "interrupted"})
            assert final["state"] == "succeeded", final
            assert marker.read_text() == "imported" and called.read_text() == "approved-value"
            actions = await instance.application.query("actions", agent, run_id=receipt["run_id"])
            assert len(actions) == 1 and actions[0]["state"] == "ok" and actions[0]["effect"] == "applied"
            assert PUBLIC_TOOL in model.catalogs[0]
            assert instance.extensions.health()["hosts"]["resident"] == 1
            workers = (await instance.extensions.get(agent, extension["id"]))["workers"]
            assert len(workers) == 1 and workers[0]["pid"] > 0
            assert workers[0]["authority_epoch"] == enabled["authority_epoch"]

            def protected_audit(unit):
                row = (
                    unit.connection.execute(
                        select(s.audit)
                        .join(s.grants, s.grants.c.review_audit_id == s.audit.c.id)
                        .where(s.grants.c.extension_id == extension["id"])
                    )
                    .mappings()
                    .one()
                )
                return unit._audit_retention_candidates(agent, [dict(row)], unit.clock.now())[0]

            proof = await instance._database.read(protected_audit)
            assert not proof["eligible"] and "audit_reference" in proof["protected_reasons"]
            assert (await instance._database.integrity())["foreign_keys"] == 0
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_revoke_pending_action_preserves_no_effect_and_regrant_never_resurrects_old_run_binding(tmp_path):
    async def scenario():
        marker = tmp_path / "must-not-import"
        source = plugin_source(tmp_path / "source", marker=marker)
        instance = await open_instance(tmp_path / "home", model=PluginModel(), capabilities=())
        try:
            agent, extension = await installed(instance, source)
            enabled = await instance.extensions.authorize(agent, extension["id"], authorization(extension))
            await configured(instance, agent)
            receipt = await instance.application.submit_turn(agent, None, "turn", "Use plugin")
            await wait_run(instance, agent, receipt["run_id"], {"waiting_approval"})
            disabled = await instance.extensions.disable(agent, extension["id"], enabled["revision"])
            assert not disabled["enabled"] and not marker.exists()
            final = await wait_run(instance, agent, receipt["run_id"], {"succeeded", "failed", "interrupted"})
            assert final["state"] == "succeeded", final
            action = (await instance.application.query("actions", agent, run_id=receipt["run_id"]))[0]
            assert action["state"] == "cancelled" and action["effect"] == "none"
            await instance.extensions.authorize(agent, extension["id"], authorization(disabled))
            assert not await instance.extensions.tools_for_run(agent, receipt["run_id"])
            assert not marker.exists()
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_plugin_crash_after_dispatch_keeps_unknown_effect_and_does_not_replay(tmp_path):
    async def scenario():
        marker = tmp_path / "actual-write"
        source = plugin_source(tmp_path / "source", call_marker=marker, crash=True)
        instance = await open_instance(tmp_path / "home", model=PluginModel(), capabilities=())
        try:
            agent, extension = await installed(instance, source)
            await instance.extensions.authorize(agent, extension["id"], authorization(extension))
            await configured(instance, agent)
            receipt = await instance.application.submit_turn(agent, None, "turn", "Use plugin once")
            await wait_run(instance, agent, receipt["run_id"], {"waiting_approval"})
            approval = (await instance.application.approvals(agent))[0]
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            final = await wait_run(instance, agent, receipt["run_id"], {"failed", "interrupted"})
            assert final["state"] != "succeeded" and marker.read_text() == "approved-value"
            actions = await instance.application.query("actions", agent, run_id=receipt["run_id"])
            assert len(actions) == 1 and actions[0]["state"] == actions[0]["effect"] == "unknown"
            assert not await instance.application.approvals(agent)
            replay = await instance.application.submit_turn(agent, None, "turn", "Use plugin once")
            assert replay["run_id"] == receipt["run_id"]
            assert len(await instance.application.query("actions", agent, run_id=receipt["run_id"])) == 1
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_agent_catalog_isolation_original_source_edits_and_install_replay_are_immutable(tmp_path):
    async def scenario():
        source = plugin_source(tmp_path / "source")
        model = PluginModel()
        instance = await open_instance(tmp_path / "home", model=model, capabilities=())
        try:
            agent, extension = await installed(instance, source)
            await instance.extensions.authorize(agent, extension["id"], authorization(extension))
            other = (await instance._database.write(lambda u: u.create_agent("Other")))["id"]
            await configured(instance, other)
            receipt = await instance.application.submit_turn(other, None, "other", "No plugin here")
            assert (await wait_run(instance, other, receipt["run_id"], {"succeeded", "failed"}))["state"] == "succeeded"
            assert all(not name.startswith("ext__") for name in model.catalogs[0])
            (source / "plugin.py").write_text("raise RuntimeError('original path must not run')", encoding="utf-8")
            repeated = await instance.extensions.install(agent, installation(source))
            assert repeated["extension_revision_id"] == extension["extension_revision_id"]
            assert repeated["package_sha256"] == extension["package_sha256"]
            assert await instance._database.read(
                lambda u: u.connection.execute(select(s.extension_revisions)).mappings().all()
            )
            await configured(instance, agent)
            captured = await instance.application.submit_turn(agent, None, "source-edited", "Use captured code")
            await wait_run(instance, agent, captured["run_id"], {"waiting_approval"})
            approval = (await instance.application.approvals(agent))[0]
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            assert (await wait_run(instance, agent, captured["run_id"], {"succeeded", "failed"}))[
                "state"
            ] == "succeeded"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_disable_during_real_plugin_write_keeps_unknown_and_reclaims_actual_child(tmp_path):
    async def scenario():
        called = tmp_path / "dispatched"
        source = plugin_source(tmp_path / "source", call_marker=called)
        entry = source / "plugin.py"
        body = entry.read_text(encoding="utf-8")
        entry.write_text(
            body.replace("    print(", "    await __import__('asyncio').sleep(600)\n    print("), encoding="utf-8"
        )
        instance = await open_instance(tmp_path / "home", model=PluginModel(), capabilities=())
        try:
            agent, extension = await installed(instance, source)
            enabled = await instance.extensions.authorize(agent, extension["id"], authorization(extension))
            await configured(instance, agent)
            receipt = await instance.application.submit_turn(agent, None, "write", "Run fixture")
            await wait_run(instance, agent, receipt["run_id"], {"waiting_approval"})
            approval = (await instance.application.approvals(agent))[0]
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            async with asyncio.timeout(15):
                while not called.exists():
                    await asyncio.sleep(0.01)
            disabled = await instance.extensions.disable(agent, extension["id"], enabled["revision"])
            assert not disabled["enabled"]
            final = await wait_run(
                instance, agent, receipt["run_id"], {"cancelled", "failed", "interrupted", "succeeded"}
            )
            assert final["state"] != "succeeded"
            actions = await instance.application.query("actions", agent, run_id=receipt["run_id"])
            assert len(actions) == 1 and actions[0]["state"] == actions[0]["effect"] == "unknown"
            assert called.read_text() == "approved-value"
        finally:
            assert (await instance.close(15))["complete"]
        assert instance.extensions.health()["hosts"]["resident"] == 0

    asyncio.run(scenario())
