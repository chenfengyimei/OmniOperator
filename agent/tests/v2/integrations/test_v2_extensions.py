"""Real isolated Python host execution, immutable installation and approval/revocation certainty."""

from omni_test_support.v2.extensions import (
    MANIFEST as MANIFEST,
)
from omni_test_support.v2.extensions import (
    PUBLIC_TOOL as PUBLIC_TOOL,
)
from omni_test_support.v2.extensions import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.extensions import (
    Path as Path,
)
from omni_test_support.v2.extensions import (
    PluginModel as PluginModel,
)
from omni_test_support.v2.extensions import (
    artifact_descriptors as artifact_descriptors,
)
from omni_test_support.v2.extensions import (
    asyncio as asyncio,
)
from omni_test_support.v2.extensions import (
    authorization as authorization,
)
from omni_test_support.v2.extensions import (
    catalog as catalog,
)
from omni_test_support.v2.extensions import (
    catalog_fingerprint as catalog_fingerprint,
)
from omni_test_support.v2.extensions import (
    configured as configured,
)
from omni_test_support.v2.extensions import (
    installation as installation,
)
from omni_test_support.v2.extensions import (
    installed as installed,
)
from omni_test_support.v2.extensions import (
    json as json,
)
from omni_test_support.v2.extensions import (
    open_instance as open_instance,
)
from omni_test_support.v2.extensions import (
    parse_manifest as parse_manifest,
)
from omni_test_support.v2.extensions import (
    plugin_source as plugin_source,
)
from omni_test_support.v2.extensions import (
    pytest as pytest,
)
from omni_test_support.v2.extensions import (
    s as s,
)
from omni_test_support.v2.extensions import (
    select as select,
)
from omni_test_support.v2.extensions import (
    tool_artifacts as tool_artifacts,
)
from omni_test_support.v2.extensions import (
    wait_run as wait_run,
)


def test_manifest_artifact_capability_changes_the_fingerprint_and_is_bounded():
    plain = parse_manifest(json.loads(json.dumps(MANIFEST)))
    assert catalog(plain)[0]["artifacts"] == [] and tool_artifacts(plain, "echo") == ()
    declared = json.loads(json.dumps(MANIFEST))
    declared["tools"][0]["artifacts"] = ["image/png", "application/pdf"]
    parsed = parse_manifest(declared)
    assert tool_artifacts(parsed, "echo") == ("image/png", "application/pdf")
    assert catalog_fingerprint(parsed) != catalog_fingerprint(plain)
    for invalid in (["image/png", "image/png"], ["IMAGE/PNG"], ["text"], [""], ["text/plain"] * 5):
        broken = json.loads(json.dumps(MANIFEST))
        broken["tools"][0]["artifacts"] = invalid
        with pytest.raises(Exception):
            parse_manifest(broken)


def test_artifact_descriptors_are_bounded_and_must_match_declared_types():
    allowed = ("image/png",)
    good = artifact_descriptors([{"name": "a.png", "media_type": "image/png", "bytes": 3, "sha256": "a" * 64}], allowed)
    assert good[0]["name"] == "a.png"
    for invalid in (
        [{"name": "../a.png", "media_type": "image/png", "bytes": 3, "sha256": "a" * 64}],
        [{"name": "a.png", "media_type": "text/plain", "bytes": 3, "sha256": "a" * 64}],
        [{"name": "a.png", "media_type": "image/png", "bytes": 0, "sha256": "a" * 64}],
        [{"name": "a.png", "media_type": "image/png", "bytes": 3, "sha256": "zz"}],
        [{"name": "a.png", "media_type": "image/png", "bytes": 3, "sha256": "a" * 64}] * 5,
    ):
        with pytest.raises(Exception):
            artifact_descriptors(invalid, allowed)


def test_artifact_plugin_publishes_verified_objects_and_bounded_model_text(tmp_path):
    async def scenario():
        body = b"\x89PNG fixture artifact"
        source = plugin_source(tmp_path / "source", artifact_type="image/png", artifact_body=body)
        instance = await open_instance(tmp_path / "home", model=PluginModel(), capabilities=())
        try:
            agent, extension = await installed(instance, source)
            await instance.extensions.authorize(agent, extension["id"], authorization(extension))
            await configured(instance, agent)
            receipt = await instance.application.submit_turn(agent, None, "artifact", "Produce the fixture artifact")
            await wait_run(instance, agent, receipt["run_id"], {"waiting_approval"})
            approval = (await instance.application.approvals(agent))[0]
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            final = await wait_run(instance, agent, receipt["run_id"], {"succeeded", "failed", "interrupted"})
            assert final["state"] == "succeeded", final
            action = (await instance.application.query("actions", agent, run_id=receipt["run_id"]))[0]
            assert action["state"] == "ok" and action["effect"] == "applied"
            result = action["result"]
            assert len(result["artifacts"]) == 1
            metadata, payload = await instance.objects.get(agent, result["artifacts"][0])
            assert payload == body and metadata["media_type"] == "image/png"
            assert result["data"]["artifacts"] == [
                {"id": result["artifacts"][0], "media_type": "image/png", "bytes": len(body)}
            ]
            assert "artifacts: image/png" in result["text"]
            assert "fixture artifact" not in result["text"]
            listed = await instance.extensions.actions(agent, extension["id"])
            assert len(listed["items"]) == 1 and listed["next_after"] is None
            evidence = listed["items"][0]
            assert set(evidence) == {"id", "tool", "state", "effect", "ended_at", "artifacts"}
            assert evidence["tool"] == PUBLIC_TOOL and evidence["state"] == "ok" and evidence["effect"] == "applied"
            assert evidence["artifacts"] == [
                {"id": result["artifacts"][0], "media_type": "image/png", "bytes": len(body)}
            ]
            assert "fixture artifact" not in str(evidence)
            assert await instance._database.read(
                lambda u: u.connection.execute(
                    select(s.action_objects).where(
                        s.action_objects.c.agent_id == agent,
                        s.action_objects.c.action_id == action["id"],
                        s.action_objects.c.object_id == result["artifacts"][0],
                    )
                ).first()
            )
            staging = Path(instance.extensions._hosts.artifact_path(agent, extension["id"]))
            assert not staging.exists()
            assert instance.extensions.health()["hosts"]["cleanup_pending"] == 0
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["escape", "undeclared", "missing", "oversize"])
def test_unverifiable_artifacts_end_unknown_without_publishing_objects(tmp_path, mode):
    async def scenario():
        source = plugin_source(
            tmp_path / "source",
            artifact_type="image/png",
            artifact_name="report.png",
            artifact_mode=mode,
            artifact_body=b"bounded",
        )
        instance = await open_instance(tmp_path / "home", model=PluginModel(), capabilities=())
        try:
            agent, extension = await installed(instance, source)
            await instance.extensions.authorize(agent, extension["id"], authorization(extension))
            await configured(instance, agent)
            receipt = await instance.application.submit_turn(agent, None, "invalid", "Produce a bad artifact")
            await wait_run(instance, agent, receipt["run_id"], {"waiting_approval"})
            approval = (await instance.application.approvals(agent))[0]
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            final = await wait_run(instance, agent, receipt["run_id"], {"succeeded", "failed", "interrupted"})
            assert final["state"] == "failed", final
            action = (await instance.application.query("actions", agent, run_id=receipt["run_id"]))[0]
            assert action["state"] == action["effect"] == "unknown"
            assert action["result"]["artifacts"] == []
            assert action["result"]["error_code"] in {
                "extension_outcome_unknown",
                "extension_artifact_invalid",
            }
            assert (
                await instance._database.read(
                    lambda u: u.connection.execute(
                        select(s.action_objects).where(
                            s.action_objects.c.agent_id == agent, s.action_objects.c.action_id == action["id"]
                        )
                    ).first()
                )
                is None
            )
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


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


def test_market_scan_is_read_only_reports_updates_and_rejects_unsafe_sources(tmp_path):
    async def scenario():
        market = tmp_path / "market"
        market.mkdir()
        plugin_source(market / "v1", version="1")
        plugin_source(market / "v2", version="2")
        broken = market / "broken"
        broken.mkdir()
        (broken / "extension.json").write_text("{", encoding="utf-8")
        source = plugin_source(tmp_path / "source", version="1")
        instance = await open_instance(tmp_path / "home", model=PluginModel(), capabilities=())
        try:
            agent, extension = await installed(instance, source)
            result = await instance.extensions.market(agent, str(market))
            assert result["scanned"] == 2 and result["marketplace_source"] == str(market)
            rows = {row["version"]: row for row in result["items"]}
            assert rows["1"]["installed"] and rows["1"]["is_current_version"] and not rows["1"]["update_available"]
            assert rows["2"]["update_available"] and rows["2"]["extension_id"] == extension["id"]
            assert rows["2"]["revision"] == extension["revision"] and rows["2"]["installed_version"] == "1"
            assert (await instance.extensions.get(agent, extension["id"]))["version"] == "1"
            for bad in ("relative/market", str(tmp_path / "missing")):
                with pytest.raises(BusinessError):
                    await instance.extensions.market(agent, bad)
            link = tmp_path / "market-link"
            try:
                link.symlink_to(market, target_is_directory=True)
            except OSError:
                link = None
            if link is not None:
                with pytest.raises(BusinessError):
                    await instance.extensions.market(agent, str(link))
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_extension_can_request_allowlisted_host_capabilities(tmp_path):
    async def scenario():
        source = plugin_source(tmp_path / "source", host_mode="happy")
        instance = await open_instance(tmp_path / "home", model=PluginModel(), capabilities=())
        try:
            agent, extension = await installed(instance, source)
            await instance.extensions.authorize(agent, extension["id"], authorization(extension))
            await configured(instance, agent)
            receipt = await instance.application.submit_turn(agent, None, "host", "Use a host callback")
            await wait_run(instance, agent, receipt["run_id"], {"waiting_approval"})
            approval = (await instance.application.approvals(agent))[0]
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            final = await wait_run(instance, agent, receipt["run_id"], {"succeeded", "failed", "interrupted"})
            assert final["state"] == "succeeded", final
            action = (await instance.application.query("actions", agent, run_id=receipt["run_id"]))[0]
            assert action["state"] == "ok" and action["effect"] == "applied"
            result = action["result"]
            assert result["data"]["host_callbacks"] == {
                "calls": 3,
                "logs": ["fixture-callback-log"],
            }
            assert result["data"]["host"] == {
                "tool": PUBLIC_TOOL,
                "extension_id": extension["id"],
            }
            assert f"ctx={PUBLIC_TOOL} denied=True" in result["text"]
            assert "fixture-callback-log" not in result["text"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("host_mode", ["over_budget", "oversize_log"])
def test_host_callback_budget_violations_end_unknown_without_replay(tmp_path, host_mode):
    async def scenario():
        source = plugin_source(tmp_path / "source", host_mode=host_mode)
        instance = await open_instance(tmp_path / "home", model=PluginModel(), capabilities=())
        try:
            agent, extension = await installed(instance, source)
            await instance.extensions.authorize(agent, extension["id"], authorization(extension))
            await configured(instance, agent)
            receipt = await instance.application.submit_turn(agent, None, "budget", "Exceed the callback budget")
            await wait_run(instance, agent, receipt["run_id"], {"waiting_approval"})
            approval = (await instance.application.approvals(agent))[0]
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            final = await wait_run(instance, agent, receipt["run_id"], {"succeeded", "failed", "interrupted"})
            assert final["state"] == "failed", final
            action = (await instance.application.query("actions", agent, run_id=receipt["run_id"]))[0]
            assert action["state"] == action["effect"] == "unknown"
            assert action["result"]["error_code"] in {
                "extension_outcome_unknown",
                "worker_disconnected",
            }
            assert action["result"]["text"] != "unreachable"
        finally:
            assert (await instance.close(15))["complete"]

    asyncio.run(scenario())
