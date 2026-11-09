"""Immutable terminal observations, scope/CAS/recovery and evidence erasure on isolated databases."""

import asyncio
import json
import uuid

import pytest
from sqlalchemy import delete, inspect, update
from test_v2_objects import Clock, opened
from v2_terminal_review_helpers import request, unknown

from omni.adapters.persistence import schema as s
from omni.domain.contracts import BusinessError
from omni.domain.terminal_review import parse_review, request_fingerprint


@pytest.mark.parametrize(
    "field,value",
    [
        ("acknowledge_limits", False),
        ("decision", "success"),
        ("evidence", ""),
        ("evidence", "x" * 2001),
        ("revision", True),
        ("fingerprint", "wrong"),
        ("policy", "action-review-1"),
        ("history_epoch", "wrong"),
    ],
)
def test_request_requires_current_exact_proof_and_explicit_limits(field, value):
    detail = {"revision": 0, "history_epoch": str(uuid.uuid4()), "fingerprint": "a" * 64, "policy": "terminal-review-1"}
    with pytest.raises(BusinessError) as invalid:
        parse_review(request(detail, **{field: value}))
    assert invalid.value.code == "terminal_review_invalid"


def test_observation_append_replay_evidence_erasure_and_source_receipts_are_independent(tmp_path):
    async def scenario():
        async with opened(tmp_path / "home", Clock()) as (_, db, _, agent):
            operation = await unknown(db, agent, tmp_path / "workspace")
            before = await db.read(lambda u: u.terminal_operation_lookup(agent, operation["id"]))
            detail = await db.read(lambda u: u.terminal_review_detail(agent, operation["id"]))
            assert detail["reviewable"] and detail["revision"] == 0 and not detail["authorizes_replay"]
            data = request(detail)
            review = await db.write(lambda u: u.terminal_review_commit(agent, operation["id"], data))
            assert review["revision"] == 1 and review["decision"] == "still_unknown"
            assert review["request_sha256"] == request_fingerprint(data)
            replay = await db.write(lambda u: u.terminal_review_commit(agent, operation["id"], data))
            assert replay["replayed"] and replay["id"] == review["id"]
            assert await db.read(lambda u: u.terminal_operation_lookup(agent, operation["id"])) == before
            assert "private-review-evidence" not in json.dumps(await db.read(lambda u: u.query("audit", agent)))
            erased = await db.write(lambda u: u.terminal_review_erase_evidence(agent, operation["id"], review["id"]))
            assert erased["evidence"] is None and erased["evidence_sha256"] == review["evidence_sha256"]
            assert (await db.write(lambda u: u.terminal_review_commit(agent, operation["id"], data)))[
                "evidence"
            ] is None
            assert (
                await db.write(lambda u: u.terminal_review_erase_evidence(agent, operation["id"], review["id"]))
                == erased
            )
            current = await db.read(lambda u: u.terminal_review_detail(agent, operation["id"]))
            assert current["latest_matches_source"] and current["latest_review"]["revision"] == 1
            assert (await db.read(lambda u: u.terminal_review_history(agent, operation["id"])))[0] == erased
            with pytest.raises(BusinessError):
                await db.write(
                    lambda u: u.terminal_review_commit(agent, operation["id"], {**data, "evidence": "changed"})
                )
            for change in ({"decision": "applied"}, {"revision": 99}, {"history_epoch": str(uuid.uuid4())}):
                with pytest.raises(BusinessError):
                    await db.write(
                        lambda u: u.terminal_review_commit(
                            agent, operation["id"], {**data, "review_id": str(uuid.uuid4()), **change}
                        )
                    )
            other = (await db.write(lambda u: u.create_agent("Other")))["id"]
            for fn in (
                lambda u: u.terminal_review_detail(other, operation["id"]),
                lambda u: u.terminal_review_commit(other, operation["id"], data),
                lambda u: u.terminal_review_history(other, operation["id"]),
                lambda u: u.terminal_review_erase_evidence(other, operation["id"], review["id"]),
            ):
                with pytest.raises(BusinessError) as missing:
                    await db.read(fn)
                assert missing.value.status == 404

    asyncio.run(scenario())


def test_review_and_audit_rollback_together_and_database_rejects_metadata_rewrite(tmp_path, monkeypatch):
    async def scenario():
        async with opened(tmp_path / "home", Clock()) as (_, db, _, agent):
            operation = await unknown(db, agent, tmp_path / "workspace")
            detail = await db.read(lambda u: u.terminal_review_detail(agent, operation["id"]))
            data = request(detail)

            def failed(u):
                u.terminal_review_commit(agent, operation["id"], data)
                raise RuntimeError("transaction interrupted")

            with pytest.raises(RuntimeError):
                await db.write(failed)
            assert not await db.read(lambda u: u.terminal_review_history(agent, operation["id"]))
            assert not any(
                r["kind"] == "terminal.review.recorded" for r in await db.read(lambda u: u.query("audit", agent))
            )
            review = await db.write(lambda u: u.terminal_review_commit(agent, operation["id"], data))
            for statement in (
                update(s.terminal_reviews).where(s.terminal_reviews.c.id == review["id"]).values(decision="applied"),
                delete(s.terminal_reviews).where(s.terminal_reviews.c.id == review["id"]),
            ):
                with pytest.raises(BusinessError) as rejected:
                    await db.write(lambda u: u.connection.execute(statement))
                assert rejected.value.code == "data_constraint"
            assert any(
                table is s.terminal_reviews and column.name == "audit_id" for table, column in s.AUDIT_REFERENCES
            )
            assert "terminal_reviews" in await db.read(lambda u: inspect(u.connection).get_table_names())

    asyncio.run(scenario())


def test_history_epoch_change_invalidates_old_proof_without_deleting_observations(tmp_path):
    async def scenario():
        async with opened(tmp_path / "home", Clock()) as (_, db, _, agent):
            operation = await unknown(db, agent, tmp_path / "workspace")
            detail = await db.read(lambda u: u.terminal_review_detail(agent, operation["id"]))
            first = await db.write(lambda u: u.terminal_review_commit(agent, operation["id"], request(detail)))
            stale = request(await db.read(lambda u: u.terminal_review_detail(agent, operation["id"])))
            await db.write(lambda u: u.connection.execute(update(s.history_identity).values(epoch=str(uuid.uuid4()))))
            with pytest.raises(BusinessError) as changed:
                await db.write(lambda u: u.terminal_review_commit(agent, operation["id"], stale))
            assert changed.value.code == "terminal_review_changed"
            current = await db.read(lambda u: u.terminal_review_detail(agent, operation["id"]))
            assert not current["latest_matches_source"] and current["revision"] == 1
            second = await db.write(lambda u: u.terminal_review_commit(agent, operation["id"], request(current)))
            assert second["revision"] == 2 and second["history_epoch"] != first["history_epoch"]
            assert (await db.read(lambda u: u.terminal_operation_lookup(agent, operation["id"])))["operation"][
                "state"
            ] == "unknown"

    asyncio.run(scenario())


def test_concurrent_owner_observations_share_one_revision_and_do_not_cross_session_scope(tmp_path):
    async def scenario():
        async with opened(tmp_path / "home", Clock()) as (_, db, _, agent):
            operation = await unknown(db, agent, tmp_path / "workspace")
            detail = await db.read(lambda u: u.terminal_review_detail(agent, operation["id"]))
            first, second = request(detail), request(detail, decision="applied")
            values = await asyncio.gather(
                db.write(lambda u: u.terminal_review_commit(agent, operation["id"], first)),
                db.write(lambda u: u.terminal_review_commit(agent, operation["id"], second)),
                return_exceptions=True,
            )
            assert sum(isinstance(row, BusinessError) for row in values) == 1
            winner = next(row for row in values if isinstance(row, dict))
            assert winner["revision"] == 1
            assert len(await db.read(lambda u: u.terminal_review_history(agent, operation["id"]))) == 1
            other = await unknown(db, agent, tmp_path / "other-workspace")

            def forged(u):
                row = {key: value for key, value in winner.items() if key != "replayed"}
                row.update(id=str(uuid.uuid4()), session_id=other["session_id"], revision=2)
                u.connection.execute(s.terminal_reviews.insert().values(**row))

            with pytest.raises(BusinessError) as constrained:
                await db.write(forged)
            assert constrained.value.code == "data_constraint"

            def unversioned(u):
                row = {key: value for key, value in winner.items() if key != "replayed"}
                row.update(id=str(uuid.uuid4()), revision=2, proof={})
                u.connection.execute(s.terminal_reviews.insert().values(**row))

            with pytest.raises(BusinessError) as constrained:
                await db.write(unversioned)
            assert constrained.value.code == "data_constraint"

    asyncio.run(scenario())
