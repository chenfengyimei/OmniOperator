"""Trusted-plugin identity, scoped grants and final dispatch are tested through the concrete isolated UoW."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from dataclasses import replace

import pytest
from sqlalchemy import select
from test_v2_extensions_contract import chinese_manifest, manifest
from test_v2_objects import Clock, opened
from v2_configuration_helpers import model_uow

from omni.adapters.extensions.packages import capture_directory
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.maintenance import backup, restore
from omni.adapters.platform.paths import InstancePaths
from omni.domain.contracts import ActionResult, BusinessError, Effect, Outcome
from omni.domain.extensions import request_fingerprint, tool_spec


def package_source(directory, *, version="1.0", count=2, value=None):
    directory.mkdir(parents=True, exist_ok=True)
    value = value or manifest(count)
    value["version"] = version
    (directory / "extension.json").write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    (directory / "plugin.py").write_text(
        "raise RuntimeError('repository fixtures must never import plugin code')\n", encoding="utf-8"
    )
    return directory


def metadata(captured):
    return {
        "manifest": captured.manifest,
        "files": list(captured.files),
        "package_sha256": captured.package_sha256,
        "catalog_sha256": captured.catalog_sha256,
    }


def install_request(source, key="install", previous=None):
    return {
        "key": key,
        "source": str(source),
        "extension_id": previous["id"] if previous else None,
        "revision": previous["revision"] if previous else 0,
    }


async def install(database, objects, agent, source, key="install", previous=None):
    captured = capture_directory(source)
    package = await objects.publish(agent, captured.content, "application/zip")
    request = install_request(source, key, previous)
    row = await database.write(lambda unit: unit.extension_install(agent, request, metadata(captured), package["id"]))
    return row, captured, package, request


def authorization(row, names=None):
    return {
        "revision": row["revision"],
        "package_sha256": row["package_sha256"],
        "catalog_sha256": row["catalog_sha256"],
        "history_epoch": row["history_epoch"],
        "tools": names or [tool["name"] for tool in row["tools"]],
        "trust": True,
    }


async def grant(database, agent, row, names=None):
    return await database.write(lambda unit: unit.extension_authorize(agent, row["id"], authorization(row, names)))


def new_turn(unit, agent, key="turn"):
    return unit.accept_turn(agent, None, key, "isolated plugin action", {})


def prepare_action(unit, agent, turn, arguments=None, *, start=True):
    if start:
        unit.transition(agent, turn["run_id"], "running")
    binding = unit.extension_run_tools(agent, turn["run_id"])[0]
    spec = tool_spec(binding)
    policy = unit.settings(agent)["policy_revision"]
    action = unit.prepare_action(agent, turn["run_id"], spec, arguments or {}, policy)
    return action, binding


def approve_action(unit, agent, action):
    pending = next(row for row in unit.pending_approvals(agent) if row["action_id"] == action["id"])
    unit.resolve_approval(agent, pending["id"], True, action["policy_revision"])


def table_rows(unit, table, agent):
    return [
        dict(row)
        for row in unit.connection.execute(
            select(table).where(table.c.agent_id == agent).order_by(table.c.id)
        ).mappings()
    ]


def test_install_replay_retains_original_version_after_update_and_never_reopens_source(tmp_path):
    source = package_source(tmp_path / "source")

    async def scenario():
        async with opened(tmp_path / "home", Clock()) as (_, db, objects, agent):
            first, captured, package, request = await install(db, objects, agent, source)
            assert first["state"] == "disabled" and first["retired_authority_epoch"] == 0
            assert first["package_sha256"] == package["sha256"] == hashlib.sha256(captured.content).hexdigest()
            assert all(not tool["granted"] for tool in first["tools"])
            enabled = await grant(db, agent, first)
            package_source(source, version="2.0")
            second, _, _, update = await install(db, objects, agent, source, "update", enabled)
            assert second["id"] == first["id"] and second["extension_revision_id"] != first["extension_revision_id"]
            assert second["state"] == "disabled" and second["retired_authority_epoch"] == enabled["authority_epoch"]
            (source / "extension.json").unlink()
            replay = await db.write(lambda unit: unit.extension_install(agent, request, {}, "not-an-object"))
            assert replay["replayed"] and not replay["is_current_version"] and replay["version"] == "1.0"
            assert (
                replay["extension_revision_id"] == first["extension_revision_id"]
                and replay["package_sha256"] == first["package_sha256"]
            )
            assert (
                replay["current_revision_id"] == second["extension_revision_id"]
                and replay["retired_authority_epoch"] == 0
            )
            latest = await db.write(lambda unit: unit.extension_install(agent, update, {}, "not-an-object"))
            assert latest["extension_revision_id"] == second["extension_revision_id"] and latest["is_current_version"]
            with pytest.raises(BusinessError) as error:
                await db.write(lambda unit: unit.extension_install_replay(agent, request["key"], "0" * 64))
            assert error.value.code == "idempotency_conflict"
            stranger = (await db.write(lambda unit: unit.create_agent("Other Agent")))["id"]
            for operation in (
                lambda unit: unit.extension_get(stranger, first["id"]),
                lambda unit: unit.extension_authorize(stranger, first["id"], authorization(second)),
                lambda unit: unit.extension_install(stranger, update, metadata(captured), package["id"]),
            ):
                with pytest.raises(BusinessError) as error:
                    await db.write(operation)
                assert error.value.status == 404
            assert (
                await db.read(
                    lambda unit: unit.extension_install_replay(stranger, request["key"], request_fingerprint(request))
                )
                is None
            )
            assert await db.read(lambda unit: unit.extension_page(stranger)) == []
            versions = await db.read(lambda unit: table_rows(unit, s.extension_revisions, agent))
            assert len(versions) == 2 and sorted(row["ordinal"] for row in versions) == [1, 2]

    asyncio.run(scenario())


def test_empty_roster_is_sealed_and_new_authorization_only_appears_in_new_runs(tmp_path):
    source = package_source(tmp_path / "source")

    async def scenario():
        async with opened(tmp_path / "home", Clock()) as (_, db, objects, agent):
            empty = await db.write(lambda unit: new_turn(unit, agent, "before-install"))
            installed, _, _, _ = await install(db, objects, agent, source)
            disabled = await db.write(lambda unit: new_turn(unit, agent, "before-grant"))
            granted = await grant(db, agent, installed)
            for turn in (empty, disabled):
                await db.write(lambda unit, turn=turn: unit.extension_capture_run(agent, turn["run_id"]))
                assert await db.read(lambda unit, turn=turn: unit.extension_run_tools(agent, turn["run_id"])) == []
                row = await db.read(lambda unit, turn=turn: unit.get_run(agent, turn["run_id"]))
                assert row["extension_capture_epoch"] == granted["history_epoch"]
            fresh = await db.write(lambda unit: new_turn(unit, agent, "after-grant"))
            bindings = await db.read(lambda unit: unit.extension_run_tools(agent, fresh["run_id"]))
            assert len(bindings) == len(granted["tools"]) == 2
            assert all(row["extension_revision_id"] == granted["extension_revision_id"] for row in bindings)
            assert all(
                row["file_count"] == granted["file_count"] and row["total_bytes"] == granted["total_bytes"]
                for row in bindings
            )
            original = [row["binding_id"] for row in bindings]
            await db.write(lambda unit: unit.extension_capture_run(agent, fresh["run_id"]))
            assert [
                row["binding_id"]
                for row in await db.read(lambda unit: unit.extension_run_tools(agent, fresh["run_id"]))
            ] == original

    asyncio.run(scenario())


def test_pending_revocation_resumes_run_and_regrant_does_not_revive_old_approval_or_binding(tmp_path):
    source = package_source(tmp_path / "source")

    async def scenario():
        async with opened(tmp_path / "home", Clock()) as (_, db, objects, agent):
            await db.write(lambda unit: model_uow(unit, agent))
            installed, _, _, _ = await install(db, objects, agent, source)
            enabled = await grant(db, agent, installed)
            turn = await db.write(lambda unit: new_turn(unit, agent))
            action, binding = await db.write(lambda unit: prepare_action(unit, agent, turn))
            assert (await db.read(lambda unit: unit.get_run(agent, turn["run_id"])))["state"] == "waiting_approval"
            pending = (await db.read(lambda unit: unit.pending_approvals(agent)))[0]
            disabled = await db.write(lambda unit: unit.extension_disable(agent, installed["id"], enabled["revision"]))
            assert (
                disabled["cancel_operations"] == []
                and disabled["retired_authority_epoch"] == enabled["authority_epoch"]
            )
            current = await db.read(lambda unit: unit.action(agent, action["id"]))
            assert current["state"] == current["approval_state"] == "cancelled" and current["effect"] == "none"
            assert (await db.read(lambda unit: unit.get_run(agent, turn["run_id"])))["state"] == "running"
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda unit: unit.resolve_approval(agent, pending["id"], True, action["policy_revision"])
                )
            assert error.value.code == "approval_expired"
            regranted = await grant(db, agent, disabled)
            assert regranted["authority_epoch"] > enabled["authority_epoch"]
            assert await db.read(lambda unit: unit.extension_run_tools(agent, turn["run_id"])) == []
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda unit: unit.prepare_action(
                        agent, turn["run_id"], tool_spec(binding), {}, action["policy_revision"]
                    )
                )
            assert error.value.code == "extension_not_authorized"
            assert len(await db.read(lambda unit: unit.query("actions", agent))) == 1
            fresh = await db.write(lambda unit: new_turn(unit, agent, "fresh-authority"))
            fresh_bindings = await db.read(lambda unit: unit.extension_run_tools(agent, fresh["run_id"]))
            assert all(
                row["authority_epoch"] == regranted["authority_epoch"] and row["grant_id"] != binding["grant_id"]
                for row in fresh_bindings
            )

    asyncio.run(scenario())


def test_public_dispatch_returns_persisted_argument_proof_and_preserves_executing_unknown(tmp_path):
    value = manifest(1)
    value["tools"][0]["parameters"] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    source = package_source(tmp_path / "source", value=value)
    arguments = {"text": "参数原文只存在业务记录"}

    async def scenario():
        async with opened(tmp_path / "home", Clock()) as (_, db, objects, agent):
            await db.write(lambda unit: model_uow(unit, agent))
            installed, _, _, _ = await install(db, objects, agent, source)
            enabled = await grant(db, agent, installed)
            turn = await db.write(lambda unit: new_turn(unit, agent))
            action, binding = await db.write(lambda unit: prepare_action(unit, agent, turn, arguments))
            with pytest.raises(BusinessError) as error:
                await db.read(
                    lambda unit: unit.extension_dispatch(agent, turn["run_id"], action["id"], binding["name"])
                )
            assert error.value.code == "approval_expired"
            await db.write(lambda unit: approve_action(unit, agent, action))
            with pytest.raises(BusinessError) as error:
                await db.read(
                    lambda unit: unit.extension_dispatch(agent, turn["run_id"], action["id"], binding["name"])
                )
            assert error.value.code == "extension_not_authorized"
            await db.write(lambda unit: unit.authorize_action(agent, action["id"]))
            proof = await db.read(
                lambda unit: unit.extension_dispatch(agent, turn["run_id"], action["id"], binding["name"])
            )
            expected = hashlib.sha256(
                json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            assert proof["arguments_sha256"] == action["arguments_sha256"] == expected
            assert proof["file_count"] == installed["file_count"] and proof["total_bytes"] == installed["total_bytes"]
            with pytest.raises(BusinessError) as error:
                await db.read(
                    lambda unit: unit.extension_dispatch(agent, turn["run_id"], action["id"], "ext__calculator__wrong")
                )
            assert error.value.code == "extension_not_authorized"
            disabled = await db.write(lambda unit: unit.extension_disable(agent, installed["id"], enabled["revision"]))
            assert disabled["cancel_operations"] == [action["id"]]
            assert (await db.read(lambda unit: unit.action(agent, action["id"])))["state"] == "executing"
            with pytest.raises(BusinessError) as error:
                await db.read(
                    lambda unit: unit.extension_dispatch(agent, turn["run_id"], action["id"], binding["name"])
                )
            assert error.value.code == "extension_not_authorized"
            await db.write(
                lambda unit: unit.settle_action(
                    agent, action["id"], ActionResult(Outcome.UNKNOWN, "isolated uncertain write", Effect.UNKNOWN)
                )
            )
            with pytest.raises(BusinessError) as error:
                await db.write(lambda unit: unit.authorize_action(agent, action["id"]))
            assert error.value.code == "action_not_authorized"
            await db.write(lambda unit: unit.recover())
            assert (await db.read(lambda unit: unit.action(agent, action["id"])))["state"] == "unknown"
            audits = await db.read(lambda unit: table_rows(unit, s.audit, agent))
            assert arguments["text"] not in json.dumps(audits, ensure_ascii=False)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["settings", "model", "approval"])
def test_final_dispatch_rechecks_all_current_authority_after_action_was_authorized(tmp_path, change):
    source = package_source(tmp_path / "source", count=1)
    clock = Clock()

    async def scenario():
        async with opened(tmp_path / "home", clock) as (_, db, objects, agent):
            await db.write(lambda unit: model_uow(unit, agent))
            installed, _, _, _ = await install(db, objects, agent, source)
            await grant(db, agent, installed)
            turn = await db.write(lambda unit: new_turn(unit, agent))
            action, binding = await db.write(lambda unit: prepare_action(unit, agent, turn))
            await db.write(lambda unit: approve_action(unit, agent, action))
            await db.write(lambda unit: unit.authorize_action(agent, action["id"]))
            if change == "settings":

                def revoke(unit):
                    current = unit.settings(agent)
                    unit.settings_put(
                        agent, {**current["value"], "disabled_tools": [binding["name"]]}, current["revision"]
                    )

                await db.write(revoke)
                expected = "policy_changed"
            elif change == "model":

                def revoke(unit):
                    current = unit.settings(agent)
                    model = unit.configuration_get(agent, current["value"]["model_profile_id"])
                    unit.configuration_put(agent, model["id"], model["name"], False, model["value"], model["revision"])

                await db.write(revoke)
                expected = "model_configuration_revoked"
            else:
                # Keep the original owned lease healthy while the real 180-second approval expires.
                for seconds in (59, 59, 59, 4):
                    clock.current += dt.timedelta(seconds=seconds)
                    await db.write(lambda unit: unit.renew_run_leases())
                expected = "approval_expired"
            with pytest.raises(BusinessError) as error:
                await db.read(
                    lambda unit: unit.extension_dispatch(agent, turn["run_id"], action["id"], binding["name"])
                )
            assert error.value.code == expected
            assert (await db.read(lambda unit: unit.action(agent, action["id"])))["state"] == "executing"

    asyncio.run(scenario())


def test_descriptor_changes_cannot_bypass_write_approval_or_rebind_a_builtin(tmp_path):
    source = package_source(tmp_path / "source", count=1)

    async def scenario():
        async with opened(tmp_path / "home", Clock()) as (_, db, objects, agent):
            installed, captured, package, _ = await install(db, objects, agent, source)
            await grant(db, agent, installed)
            turn = await db.write(lambda unit: new_turn(unit, agent))
            await db.write(lambda unit: unit.transition(agent, turn["run_id"], "running"))
            binding = (await db.read(lambda unit: unit.extension_run_tools(agent, turn["run_id"])))[0]
            spec = tool_spec(binding)
            altered = (
                replace(spec, writes=False),
                replace(spec, description="different"),
                replace(spec, timeout_s=spec.timeout_s + 1),
                replace(spec, resource="builtin"),
                replace(spec, name="system_info"),
                replace(spec, binding_id=""),
                replace(
                    spec,
                    parameters={
                        "type": "object",
                        "properties": {"extra": {"type": "string"}},
                        "additionalProperties": False,
                    },
                ),
            )
            for forged in altered:
                with pytest.raises(BusinessError) as error:
                    await db.write(
                        lambda unit, forged=forged: unit.prepare_action(
                            agent, turn["run_id"], forged, {}, unit.settings(agent)["policy_revision"]
                        )
                    )
                assert error.value.code == "extension_not_authorized"
            assert await db.read(lambda unit: unit.query("actions", agent)) == []
            declared = metadata(captured)
            declared["manifest"]["tools"][0]["writes"] = False
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda unit: unit.extension_install(
                        agent, install_request(source, "read-only", installed), declared, package["id"]
                    )
                )
            assert error.value.code == "extension_invalid"
            assert len(await db.read(lambda unit: table_rows(unit, s.extension_revisions, agent))) == 1

    asyncio.run(scenario())


def test_concurrent_authorizations_have_one_cas_winner_and_never_revive_revoked_grants(tmp_path):
    source = package_source(tmp_path / "source", count=1)

    async def scenario():
        async with opened(tmp_path / "home", Clock()) as (_, db, objects, agent):
            installed, _, _, _ = await install(db, objects, agent, source)
            body = authorization(installed)
            results = await asyncio.gather(
                *[db.write(lambda unit: unit.extension_authorize(agent, installed["id"], body)) for _ in range(2)],
                return_exceptions=True,
            )
            successful = [row for row in results if isinstance(row, dict)]
            failures = [row for row in results if isinstance(row, BusinessError)]
            assert len(successful) == len(failures) == 1 and failures[0].code == "revision_conflict"
            grants = await db.read(lambda unit: table_rows(unit, s.grants, agent))
            assert len(grants) == 1 and grants[0]["revoked_at"] is None
            audits = await db.read(lambda unit: table_rows(unit, s.audit, agent))
            assert sum(row["kind"] == "extension.authorized" for row in audits) == 1
            again = await grant(db, agent, successful[0])
            after = await db.read(lambda unit: table_rows(unit, s.grants, agent))
            assert len(after) == 2 and sum(row["revoked_at"] is None for row in after) == 1
            old = next(row for row in after if row["id"] == grants[0]["id"])
            assert (
                old["revoked_at"]
                and next(row for row in after if not row["revoked_at"])["authority_epoch"] == again["authority_epoch"]
            )

    asyncio.run(scenario())


def test_package_object_is_referenced_in_gc_and_backup_and_restore_revokes_old_authority(tmp_path):
    source, clock = package_source(tmp_path / "source", count=1), Clock()
    home, snapshot, restored = tmp_path / "home", tmp_path / "snapshot", tmp_path / "restored"

    async def prepare():
        async with opened(home, clock) as (paths, db, objects, agent):
            await db.write(lambda unit: model_uow(unit, agent))
            installed, captured, package, _ = await install(db, objects, agent, source)
            enabled = await grant(db, agent, installed)
            turn = await db.write(lambda unit: new_turn(unit, agent))
            action, binding = await db.write(lambda unit: prepare_action(unit, agent, turn))
            clock.current += dt.timedelta(days=40)
            obj = await db.read(lambda unit: unit.object_get(agent, package["id"]))
            assert obj["references"] >= 1 and obj["expires_at"] < clock.now()
            with pytest.raises(BusinessError) as error:
                await db.write(lambda unit: unit.object_remove(agent, package["id"], package["revision"]))
            assert error.value.code == "object_protected"
            return paths, agent, enabled, captured, package, turn, action, binding

    paths, agent, enabled, captured, package, turn, action, binding = asyncio.run(prepare())
    manifest = backup(paths, snapshot)
    archive = next(
        row for row in manifest["files"] if row["path"].startswith("objects/") and row["sha256"] == package["sha256"]
    )
    assert (snapshot / archive["path"]).read_bytes() == captured.content
    report = restore(snapshot, restored)
    assert report["extensions_quarantined"] is True

    async def inspect():
        async with opened(restored, clock) as (_, db, objects, restored_agent):
            assert restored_agent == agent
            current = await db.read(lambda unit: unit.extension_get(agent, enabled["id"]))
            assert current["enabled"] is False and current["authority_epoch"] > enabled["authority_epoch"]
            assert current["history_epoch"] != enabled["history_epoch"]
            assert all(not row["granted"] for row in current["tools"])
            assert await db.read(lambda unit: unit.extension_run_tools(agent, turn["run_id"])) == []
            assert (await db.read(lambda unit: unit.action(agent, action["id"])))["state"] == "cancelled"
            assert all(row["revoked_at"] for row in await db.read(lambda unit: table_rows(unit, s.grants, agent)))
            with pytest.raises(BusinessError):
                await db.write(lambda unit: unit.extension_authorize(agent, enabled["id"], authorization(enabled)))
            registered, payload = await objects.get(agent, package["id"])
            assert payload == captured.content and registered["sha256"] == package["sha256"]
            regranted = await grant(db, agent, current)
            assert regranted["enabled"]
            assert await db.read(lambda unit: unit.extension_run_tools(agent, turn["run_id"])) == []
            fresh = await db.write(lambda unit: new_turn(unit, agent, "restored-fresh"))
            assert (await db.read(lambda unit: unit.extension_run_tools(agent, fresh["run_id"])))[0][
                "grant_id"
            ] != binding["grant_id"]
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(inspect())
    assert InstancePaths.resolve(restored).database.is_file()


def test_exact_chinese_manifest_budget_survives_database_round_trip(tmp_path):
    value = chinese_manifest()
    source = package_source(tmp_path / "source", value=value)

    async def scenario():
        async with opened(tmp_path / "home", Clock()) as (_, db, objects, agent):
            installed, captured, _, _ = await install(db, objects, agent, source)
            assert installed["manifest"] == captured.manifest == value
            assert len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) == 65_536
            assert installed["catalog_sha256"] == captured.catalog_sha256
            assert (await db.read(lambda unit: unit.extension_get(agent, installed["id"])))["manifest"] == value
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())
