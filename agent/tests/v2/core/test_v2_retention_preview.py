"""Execution retention preview protects unresolved work and provenance; never prunes business data."""

from omni_test_support.v2.retention_preview import (
    ActionResult as ActionResult,
)
from omni_test_support.v2.retention_preview import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.retention_preview import (
    Clock as Clock,
)
from omni_test_support.v2.retention_preview import (
    DocumentService as DocumentService,
)
from omni_test_support.v2.retention_preview import (
    Effect as Effect,
)
from omni_test_support.v2.retention_preview import (
    FakePort as FakePort,
)
from omni_test_support.v2.retention_preview import (
    InstanceLock as InstanceLock,
)
from omni_test_support.v2.retention_preview import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.retention_preview import (
    InvocationContext as InvocationContext,
)
from omni_test_support.v2.retention_preview import (
    Outcome as Outcome,
)
from omni_test_support.v2.retention_preview import (
    RepositoryBase as RepositoryBase,
)
from omni_test_support.v2.retention_preview import (
    SystemClock as SystemClock,
)
from omni_test_support.v2.retention_preview import (
    TestClient as TestClient,
)
from omni_test_support.v2.retention_preview import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.retention_preview import (
    asyncio as asyncio,
)
from omni_test_support.v2.retention_preview import (
    dt as dt,
)
from omni_test_support.v2.retention_preview import (
    http_app as http_app,
)
from omni_test_support.v2.retention_preview import (
    json as json,
)
from omni_test_support.v2.retention_preview import (
    model_uow as model_uow,
)
from omni_test_support.v2.retention_preview import (
    offline_read as offline_read,
)
from omni_test_support.v2.retention_preview import (
    opened as opened,
)
from omni_test_support.v2.retention_preview import (
    pytest as pytest,
)
from omni_test_support.v2.retention_preview import (
    read_action as read_action,
)
from omni_test_support.v2.retention_preview import (
    retention_cutoff as retention_cutoff,
)
from omni_test_support.v2.retention_preview import (
    s as s,
)
from omni_test_support.v2.retention_preview import (
    seed as seed,
)
from omni_test_support.v2.retention_preview import (
    subprocess as subprocess,
)
from omni_test_support.v2.retention_preview import (
    sys as sys,
)
from omni_test_support.v2.retention_preview import (
    update as update,
)


@pytest.mark.parametrize(
    "protection",
    [
        "none",
        "pinned",
        "unknown_action",
        "unknown_cost",
        "reserved_cost",
        "pending_work",
        "held_lease",
        "memory_source",
        "pinned_artifact",
        "document_context",
        "outbound",
    ],
)
def test_preview_protects_unresolved_records_and_source_references(tmp_path, protection):
    async def scenario():
        clock = Clock()
        clock.current -= dt.timedelta(days=40)
        async with opened(tmp_path, clock) as (_, db, objects, agent):
            if protection == "document_context":
                await db.write(lambda u: model_uow(u, agent))
                docs = DocumentService(db, objects)
                try:
                    source = await docs.save(agent, "Source", "private-retention-result")
                    turn, action = await db.write(lambda u: read_action(u, agent))
                    result = await docs.invoke(
                        "document_read",
                        {"document_id": source["id"]},
                        operation_id=action["id"],
                        context=InvocationContext(agent, turn["run_id"], turn["session_id"], 1),
                    )
                    await db.write(lambda u: u.settle_action(agent, action["id"], result))
                    await db.write(lambda u: u.finish(agent, turn["run_id"], "permanent conversation"))
                finally:
                    assert await docs.close()
            else:
                turn, action = await db.write(
                    lambda u: seed(u, agent, outcome=Outcome.UNKNOWN if protection == "unknown_action" else Outcome.OK)
                )
            run = turn["run_id"]
            if protection == "pinned":
                await db.write(lambda u: u.retention_pin(agent, run, True, 1))
            elif protection in {"unknown_cost", "reserved_cost"}:
                call = await db.write(lambda u: u.reserve_call(agent, run, 100, 1000, "fixture"))
                if protection == "unknown_cost":
                    await db.write(lambda u: u.settle_call(agent, call, None))
            elif protection == "pending_work":
                await db.write(
                    lambda u: u.connection.execute(
                        update(s.work_items).where(s.work_items.c.run_id == run).values(state="pending")
                    )
                )
            elif protection == "held_lease":
                await db.write(lambda u: u.lease_acquire(agent, "fixture-retention-resource", run))
            elif protection == "memory_source":
                await db.write(
                    lambda u: u.remember(agent, "sourced fact", action_id=action["id"], quote="retention-result")
                )
            elif protection == "pinned_artifact":
                blob = await objects.publish(agent, b"private-retention-artifact", "text/plain", pinned=True)
                await db.write(lambda u: u.object_link(agent, blob["id"], "action", action["id"]))
            elif protection == "outbound":
                channel = await db.write(
                    lambda u: u.configuration_create(
                        agent, "channel", "Fixture", True, {"driver": "qq", "account": "fixture-account"}
                    )
                )
                messages = await db.read(lambda u: u.messages(agent, turn["session_id"]))
                await db.write(
                    lambda u: u.delivery_create(
                        agent,
                        messages[-1]["id"],
                        channel["id"],
                        "fixture-target",
                        "retention-check",
                        FakePort.identity,
                        "fixture-account",
                        channel["revision_id"],
                    )
                )
            clock.current += dt.timedelta(days=40)
            before = await db.read(lambda u: u.action(agent, action["id"]))
            preview = await db.read(lambda u: u.retention_preview())
            assert len(preview["items"]) == 1 and not preview["can_apply"]
            row = preview["items"][0]
            assert row["eligible"] == (protection == "none")
            if protection != "none":
                expected = {
                    "pinned": "run_pinned",
                    "unknown_action": "action_unresolved",
                    "unknown_cost": "model_cost_unresolved",
                    "reserved_cost": "model_cost_unresolved",
                    "pending_work": "work_unresolved",
                    "held_lease": "run_resource_held",
                    "memory_source": "memory_source",
                    "pinned_artifact": "artifact_pinned",
                    "document_context": "document_context_source",
                    "outbound": "outbound_unresolved",
                }
                assert row["protected_reasons"] == [expected[protection]]
            assert "private-retention-result" not in str(preview)
            assert "permanent conversation" not in str(preview)
            assert len(row["fingerprint"]) == 64 and row["fingerprinted_records"] > 1
            assert await db.read(lambda u: u.action(agent, action["id"])) == before
            assert (await db.read(lambda u: u.messages(agent, turn["session_id"])))[-1][
                "content"
            ] == "permanent conversation"

    asyncio.run(scenario())


def test_pin_cas_isolation_audit_rollback_and_fingerprint_changes(tmp_path, monkeypatch):
    async def scenario():
        clock = Clock()
        clock.current -= dt.timedelta(days=40)
        async with opened(tmp_path, clock) as (_, db, _, agent):
            turn, _ = await db.write(lambda u: seed(u, agent))
            run = turn["run_id"]
            clock.current += dt.timedelta(days=40)
            first = await db.read(lambda u: u.retention_inspect(agent, run))
            with monkeypatch.context() as patch:

                def fail(*args):
                    raise BusinessError("audit_unavailable", "Injected audit failure")

                patch.setattr(RepositoryBase, "audit", fail)
                with pytest.raises(BusinessError):
                    await db.write(lambda u: u.retention_pin(agent, run, True, 1))
            assert (await db.read(lambda u: u.retention_inspect(agent, run)))["fingerprint"] == first["fingerprint"]
            results = await asyncio.gather(
                *(db.write(lambda u: u.retention_pin(agent, run, True, 1)) for _ in range(2)), return_exceptions=True
            )
            assert sum(isinstance(row, BusinessError) for row in results) == 1
            pinned = await db.read(lambda u: u.retention_inspect(agent, run))
            assert pinned["fingerprint"] != first["fingerprint"] and pinned["protected_reasons"] == ["run_pinned"]
            other = (await db.write(lambda u: u.create_agent("other")))["id"]
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.retention_pin(other, run, False, 2))
            assert error.value.status == 404
            await db.write(lambda u: u.retention_pin(agent, run, False, 2))
            assert (await db.read(lambda u: u.retention_inspect(agent, run)))["eligible"]
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


def test_preview_is_bounded_read_only_and_offline_cli_refuses_live_instance(tmp_path):
    home = tmp_path / "home"

    async def setup():
        clock = Clock()
        clock.current -= dt.timedelta(days=40)
        async with opened(home, clock) as (_, db, _, agent):
            turn, _ = await db.write(lambda u: seed(u, agent))
            with pytest.raises(BusinessError):
                await db.read(lambda u: u.retention_preview(limit=51))
            with pytest.raises(BusinessError):
                await db.read(lambda u: u.retention_preview(after="invalid"))
            return turn

    turn = asyncio.run(setup())
    paths = InstancePaths.resolve(home)
    report = offline_read(paths.database, SystemClock(), UUIDs(), lambda u: u.retention_preview())
    assert report["items"][0]["run_id"] == turn["run_id"] and report["items"][0]["eligible"]
    assert report["verification"]["integrity_ok"]
    command = [sys.executable, "-m", "omni.composition.cli", "--home", str(home), "retention-preview", "--limit", "1"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["items"][0]["fingerprint"] == report["items"][0]["fingerprint"]
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 1 and json.loads(result.stderr)["error"] == "instance_busy"
    finally:
        lock.close()
    with pytest.raises(BusinessError) as failure:
        offline_read(
            paths.database,
            SystemClock(),
            UUIDs(),
            lambda u: u.connection.execute(update(s.runs).values(retention_pinned=True)),
        )
    assert failure.value.code == "diagnostic_database_unavailable"


def test_pin_http_and_recent_active_retention_status(tmp_path):
    with TestClient(http_app(tmp_path)) as client:
        assert client.get("/api/v1/agents").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text().strip()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        run = client.post(f"/api/v1/agents/{agent}/turns", json={"key": "pin", "message": "private-chat"}).json()[
            "run_id"
        ]
        path = f"/api/v1/agents/{agent}/runs/{run}"
        row = client.get(path + "/retention").json()
        assert not row["eligible"] and "within_body_retention" in row["protected_reasons"]
        assert client.put(path + "/retention-pin", json={"pinned": True, "revision": True}).status_code == 422
        assert (
            client.put(path + "/retention-pin", json={"pinned": True, "revision": 1}).json()["retention_revision"] == 2
        )
        assert client.put(path + "/retention-pin", json={"pinned": False, "revision": 1}).status_code == 409
        assert "run_pinned" in client.get(path + "/retention").json()["protected_reasons"]


def test_retention_time_requires_utc_aware_input():
    assert retention_cutoff("2026-11-04T08:00:00+08:00") == "2026-10-05T00:00:00.000000+00:00"
    with pytest.raises(BusinessError):
        retention_cutoff("2026-11-04T08:00:00")


def test_preview_cursor_is_stable_and_content_change_alters_fingerprint(tmp_path):
    async def scenario():
        clock = Clock()
        clock.current -= dt.timedelta(days=40)
        async with opened(tmp_path, clock) as (_, db, _, agent):

            def prepare(u):
                model_uow(u, agent)
                for index in range(3):
                    turn = u.accept_turn(agent, None, str(index), "permanent input", {})
                    u.transition(agent, turn["run_id"], "running")
                    u.prepare_context(agent, turn["run_id"])
                    u.finish(agent, turn["run_id"], "permanent reply")

            await db.write(prepare)
            clock.current += dt.timedelta(days=40)
            first = await db.read(lambda u: u.retention_preview(limit=2))
            second = await db.read(lambda u: u.retention_preview(first["next_after"], 2))
            assert len(first["items"]) == 2 and len(second["items"]) == 1 and second["next_after"] is None
            ids = [item["run_id"] for page in (first, second) for item in page["items"]]
            assert ids == sorted(set(ids))
            old = first["items"][0]
            await db.write(lambda u: u.event(agent, old["run_id"], "fixture.changed", {"private": "not exported"}))
            changed = await db.read(lambda u: u.retention_inspect(agent, old["run_id"]))
            assert changed["fingerprint"] != old["fingerprint"]
            assert "not exported" not in str(changed)

    asyncio.run(scenario())


def test_active_and_recent_runs_cannot_be_candidates(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path, clock) as (_, db, _, agent):
            turn = await db.write(lambda u: u.accept_turn(agent, None, "live", "preserve", {}))
            row = await db.read(lambda u: u.retention_inspect(agent, turn["run_id"]))
            assert not row["eligible"] and "run_not_terminal" in row["protected_reasons"]
            assert "within_body_retention" in row["protected_reasons"]
            assert (await db.read(lambda u: u.retention_preview()))["items"] == []

    asyncio.run(scenario())
