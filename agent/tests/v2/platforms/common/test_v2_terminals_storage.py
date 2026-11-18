"""Owner terminal metadata, durable receipts and truthful cleanup; no native shell is launched."""

from __future__ import annotations

from omni_test_support.v2.terminals_storage import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.terminals_storage import (
    Clock as Clock,
)
from omni_test_support.v2.terminals_storage import (
    FixtureTerminalBackend as FixtureTerminalBackend,
)
from omni_test_support.v2.terminals_storage import (
    Repositories as Repositories,
)
from omni_test_support.v2.terminals_storage import (
    RepositoryBase as RepositoryBase,
)
from omni_test_support.v2.terminals_storage import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.terminals_storage import (
    asyncio as asyncio,
)
from omni_test_support.v2.terminals_storage import (
    begin_terminal as begin_terminal,
)
from omni_test_support.v2.terminals_storage import (
    configure_workspace as configure_workspace,
)
from omni_test_support.v2.terminals_storage import (
    delete as delete,
)
from omni_test_support.v2.terminals_storage import (
    func as func,
)
from omni_test_support.v2.terminals_storage import (
    insert as insert,
)
from omni_test_support.v2.terminals_storage import (
    json as json,
)
from omni_test_support.v2.terminals_storage import (
    open_request as open_request,
)
from omni_test_support.v2.terminals_storage import (
    opened as opened,
)
from omni_test_support.v2.terminals_storage import (
    payload_fingerprint as payload_fingerprint,
)
from omni_test_support.v2.terminals_storage import (
    pytest as pytest,
)
from omni_test_support.v2.terminals_storage import (
    s as s,
)
from omni_test_support.v2.terminals_storage import (
    select as select,
)
from omni_test_support.v2.terminals_storage import (
    update as update,
)


def test_owner_input_receipts_and_audit_store_hashes_without_bodies_or_fake_runs(tmp_path):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (_, database, _, agent):
            session, request, _ = await begin_terminal(database, agent, tmp_path / "workspace")
            text = "terminal-private-body-canary\x03\n秘密"
            data = {"operation_id": UUIDs().new(), "connection_id": UUIDs().new(), "data": text}
            sha = payload_fingerprint("input", data)
            size = len(text.encode("utf-8"))
            receipt = await database.write(
                lambda u: u.terminal_operation_begin(agent, session["id"], data["operation_id"], "input", sha, size)
            )
            assert receipt["state"] == "prepared" and not receipt["replayed"]
            accepted = await database.write(
                lambda u: u.terminal_operation_settle(agent, session["id"], data["operation_id"], "accepted")
            )
            assert accepted["state"] == "accepted" and accepted["ended_at"]
            assert accepted["payload_sha256"] == sha and accepted["bytes"] == size
            lookup = await database.read(lambda u: u.terminal_operation_lookup(agent, data["operation_id"]))
            assert lookup["operation"] == accepted and lookup["session"]["state"] == "running"
            assert lookup["session"]["exit_code"] is None

            def records(unit):
                return {
                    "sessions": [dict(r) for r in unit.connection.execute(select(s.terminal_sessions)).mappings()],
                    "operations": [dict(r) for r in unit.connection.execute(select(s.terminal_operations)).mappings()],
                    "audit": [dict(r) for r in unit.connection.execute(select(s.audit)).mappings()],
                    "runs": unit.connection.execute(select(func.count()).select_from(s.runs)).scalar_one(),
                    "actions": unit.connection.execute(select(func.count()).select_from(s.actions)).scalar_one(),
                }

            stored = await database.read(records)
            assert stored["runs"] == stored["actions"] == 0
            assert "terminal-private-body-canary" not in json.dumps(stored, ensure_ascii=False)
            assert "秘密" not in json.dumps(stored, ensure_ascii=False)
            assert not {"data", "input", "output", "body", "transcript"} & set(s.terminal_operations.c.keys())
            assert stored["operations"][0]["id"] == request["operation_id"]
            intent = next(r for r in stored["audit"] if r["kind"] == "terminal.input.intent")
            assert intent["data"]["payload_sha256"] == sha and intent["data"]["bytes"] == size

    asyncio.run(scenario())


def test_receipt_replays_are_permanent_and_conflicting_intents_or_close_revisions_fail(tmp_path):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (_, database, _, agent):
            session, request, spec = await begin_terminal(database, agent, tmp_path / "workspace")
            replay = await database.write(
                lambda u: u.terminal_open_begin(agent, request, spec, session["permission_epoch"])
            )
            assert replay["replayed"] and replay["id"] == session["id"]
            assert replay["operation"]["state"] == "accepted"
            with pytest.raises(BusinessError) as conflict:
                await database.read(lambda u: u.terminal_open_replay(agent, {**request, "cols": 120}))
            assert conflict.value.code == "terminal_operation_conflict"
            opid = UUIDs().new()
            receipt = await database.write(
                lambda u: u.terminal_operation_begin(agent, session["id"], opid, "input", "a" * 64, 5)
            )
            unknown = await database.write(
                lambda u: u.terminal_operation_settle(
                    agent, session["id"], opid, "unknown", "terminal_dispatch_unknown"
                )
            )
            again = await database.write(
                lambda u: u.terminal_operation_begin(agent, session["id"], opid, "input", "a" * 64, 5)
            )
            assert again["replayed"] and again["state"] == "unknown" and again["ended_at"] == unknown["ended_at"]
            assert receipt["intent_audit_id"] == again["intent_audit_id"]
            for kind, sha, size in (("input", "b" * 64, 5), ("resize", "a" * 64, 0), ("input", "a" * 64, 6)):
                with pytest.raises(BusinessError) as conflict:
                    await database.write(
                        lambda u: u.terminal_operation_begin(agent, session["id"], opid, kind, sha, size)
                    )
                assert conflict.value.code == "terminal_operation_conflict"
            with pytest.raises(BusinessError) as stale:
                await database.write(
                    lambda u: u.terminal_operation_begin(
                        agent, session["id"], UUIDs().new(), "close", "c" * 64, 0, session["revision"] - 1
                    )
                )
            assert stale.value.code == "revision_conflict"
            other = (await database.write(lambda u: u.create_agent("Other")))["id"]
            for operation in (
                lambda u: u.terminal_get(other, session["id"]),
                lambda u: u.terminal_operation_lookup(other, opid),
                lambda u: u.terminal_operation_get(other, session["id"], opid),
            ):
                with pytest.raises(BusinessError) as missing:
                    await database.read(operation)
                assert missing.value.status == 404

    asyncio.run(scenario())


def test_workspace_revocation_and_foreign_boot_block_start_input_and_false_cleanup(tmp_path):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (_, database, _, agent):
            pending, request, _ = await begin_terminal(database, agent, tmp_path / "workspace", running=False)
            running, _, _ = await begin_terminal(database, agent, tmp_path / "workspace")
            replacement = tmp_path / "replacement"
            replacement.mkdir()
            await database.write(lambda u: configure_workspace(u, agent, replacement))
            for operation in (
                lambda u: u.terminal_assert_start(agent, pending["id"]),
                lambda u: u.terminal_mark_running(agent, pending["id"], 888, 123456.0),
                lambda u: u.terminal_assert_control(agent, running["id"]),
                lambda u: u.terminal_operation_begin(agent, running["id"], UUIDs().new(), "input", "a" * 64, 1),
            ):
                with pytest.raises(BusinessError) as revoked:
                    await database.write(operation)
                assert revoked.value.code == "terminal_authority_changed"
            assert (await database.read(lambda u: u.terminal_get(agent, pending["id"])))["pid"] is None
            assert (await database.read(lambda u: u.terminal_open_replay(agent, request)))["replayed"]
            foreign_boot = UUIDs().new()
            for method, args, expected in (
                ("terminal_assert_control", (agent, running["id"]), "terminal_unavailable"),
                ("terminal_mark_closed", (agent, running["id"], True, 0), "terminal_old_boot"),
            ):
                with pytest.raises(BusinessError) as fenced:
                    await database.write(
                        lambda u: getattr(Repositories(u.connection, u.clock, u.ids, foreign_boot), method)(*args)
                    )
                assert fenced.value.code == expected
            unchanged = await database.read(lambda u: u.terminal_get(agent, running["id"]))
            assert unchanged["state"] == "running" and unchanged["boot_id"] == database.boot_id

    asyncio.run(scenario())


def test_unreclaimed_close_stays_unknown_and_confirmed_exit_is_monotonic_and_idempotent(tmp_path):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (_, database, _, agent):
            session, _, _ = await begin_terminal(database, agent, tmp_path / "workspace")
            opid = UUIDs().new()
            await database.write(
                lambda u: u.terminal_operation_begin(
                    agent, session["id"], opid, "close", "a" * 64, 0, session["revision"]
                )
            )
            unknown = await database.write(
                lambda u: u.terminal_mark_closed(agent, session["id"], False, None, "terminal_cleanup_pending")
            )
            await database.write(
                lambda u: u.terminal_operation_settle(agent, session["id"], opid, "unknown", "terminal_cleanup_pending")
            )
            assert unknown["state"] == "unknown" and unknown["cleanup_pending"] and unknown["ended_at"] is None
            duplicate = await database.write(
                lambda u: u.terminal_mark_closed(agent, session["id"], False, None, "terminal_cleanup_pending")
            )
            assert duplicate == unknown
            exited = await database.write(lambda u: u.terminal_mark_closed(agent, session["id"], True, 7))
            assert exited["state"] == "exited" and not exited["cleanup_pending"] and exited["ended_at"]
            for code in (None, 7, 99):
                assert (
                    await database.write(lambda u: u.terminal_mark_closed(agent, session["id"], True, code)) == exited
                )
            assert await database.write(lambda u: u.terminal_mark_closed(agent, session["id"], False, None)) == exited
            assert (await database.read(lambda u: u.terminal_operation_get(agent, session["id"], opid)))[
                "state"
            ] == "unknown"
            for values in (
                {"state": "running", "cleanup_pending": False, "ended_at": None},
                {"exit_code": 0},
            ):
                with pytest.raises(BusinessError) as guarded:
                    await database.write(
                        lambda u: u.connection.execute(
                            update(s.terminal_sessions)
                            .where(s.terminal_sessions.c.id == session["id"])
                            .values(**values, revision=exited["revision"] + 1)
                        )
                    )
                assert guarded.value.code == "data_constraint"

    asyncio.run(scenario())


def test_recovery_keeps_pid_evidence_and_unknown_receipts_without_reopening_or_reclaiming(tmp_path):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (_, database, _, agent):
            running, _, _ = await begin_terminal(database, agent, tmp_path / "workspace")
            pending, request, _ = await begin_terminal(database, agent, tmp_path / "workspace", running=False)
            finished, _, _ = await begin_terminal(database, agent, tmp_path / "workspace")
            exited = await database.write(lambda u: u.terminal_mark_closed(agent, finished["id"], True, 0))
            boot = UUIDs().new()

            def recover(unit):
                return Repositories(unit.connection, unit.clock, unit.ids, boot).terminal_recover()

            assert await database.write(recover) == 2
            assert await database.write(recover) == 0
            for original in (running, pending):
                row = await database.read(lambda u: u.terminal_get(agent, original["id"]))
                assert row["state"] == "unknown" and row["cleanup_pending"] and row["ended_at"] is None
                assert row["revision"] == original["revision"] + 1
                assert (row["pid"], row["process_created"], row["boot_id"]) == (
                    original["pid"],
                    original["process_created"],
                    original["boot_id"],
                )
                with pytest.raises(BusinessError) as unavailable:
                    await database.write(
                        lambda u: Repositories(u.connection, u.clock, u.ids, boot).terminal_assert_control(
                            agent, original["id"]
                        )
                    )
                assert unavailable.value.code == "terminal_unavailable"
            assert (
                await database.read(lambda u: u.terminal_operation_get(agent, pending["id"], request["operation_id"]))
            )["state"] == "unknown"
            assert await database.read(lambda u: u.terminal_get(agent, finished["id"])) == exited

    asyncio.run(scenario())


def test_intent_audit_failure_rolls_back_session_and_receipt_before_native_dispatch(tmp_path, monkeypatch):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (_, database, _, agent):
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            settings = await database.write(lambda u: configure_workspace(u, agent, workspace))
            request = open_request()
            spec = await FixtureTerminalBackend().prepare(request["shell"], str(workspace))
            original = RepositoryBase.audit

            def failed(unit, agent_id, kind, operation_id, data):
                if kind == "terminal.open.intent":
                    original(unit, agent_id, kind, operation_id, data)
                    raise OSError("isolated audit persistence failure")
                return original(unit, agent_id, kind, operation_id, data)

            with monkeypatch.context() as patch:
                patch.setattr(RepositoryBase, "audit", failed)
                with pytest.raises(OSError, match="isolated audit"):
                    await database.write(
                        lambda u: u.terminal_open_begin(agent, request, spec, settings["policy_revision"])
                    )
            assert await database.read(lambda u: u.terminal_open_replay(agent, request)) is None
            assert await database.read(lambda u: u.terminal_page(agent)) == []
            assert (
                await database.read(
                    lambda u: u.connection.execute(
                        select(func.count()).select_from(s.audit).where(s.audit.c.kind == "terminal.open.intent")
                    ).scalar_one()
                )
                == 0
            )
            retried = await database.write(
                lambda u: u.terminal_open_begin(agent, request, spec, settings["policy_revision"])
            )
            assert retried["operation"]["state"] == "prepared" and not retried["replayed"]

    asyncio.run(scenario())


def test_database_fences_receipt_identity_scope_audit_and_runtime_evidence_deletion(tmp_path):
    async def scenario():
        async with opened(tmp_path / "instance", Clock()) as (_, database, _, agent):
            session, request, _ = await begin_terminal(database, agent, tmp_path / "workspace")
            operation = session["operation"]
            other = (await database.write(lambda u: u.create_agent("Other")))["id"]
            statements = (
                update(s.terminal_operations)
                .where(s.terminal_operations.c.id == request["operation_id"])
                .values(state="unknown"),
                update(s.terminal_sessions)
                .where(s.terminal_sessions.c.id == session["id"])
                .values(pid=999, revision=session["revision"] + 1),
                update(s.terminal_sessions)
                .where(s.terminal_sessions.c.id == session["id"])
                .values(cols=19, revision=session["revision"] + 1),
                delete(s.terminal_sessions).where(s.terminal_sessions.c.id == session["id"]),
                delete(s.terminal_operations).where(s.terminal_operations.c.id == request["operation_id"]),
                delete(s.audit).where(s.audit.c.id == operation["intent_audit_id"]),
                insert(s.terminal_operations).values(
                    id=UUIDs().new(),
                    agent_id=other,
                    created_at=Clock().now(),
                    session_id=session["id"],
                    boot_id=database.boot_id,
                    kind="input",
                    payload_sha256="a" * 64,
                    bytes=1,
                    state="prepared",
                    intent_audit_id=operation["intent_audit_id"],
                    error_code="",
                ),
            )
            for statement in statements:
                with pytest.raises(BusinessError) as guarded:
                    await database.write(lambda u: u.connection.execute(statement))
                assert guarded.value.code == "data_constraint"
            assert await database.read(lambda u: u.terminal_get(agent, session["id"])) == {
                k: v for k, v in session.items() if k != "operation"
            }
            assert (await database.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())
