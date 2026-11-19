"""K08 v43 migration: frozen review stores, immutable evidence and idempotent per-run enqueue."""

from __future__ import annotations

import asyncio

from omni_test_support.v2.execution import (
    Capability as Capability,
)
from omni_test_support.v2.execution import (
    ModelReply as ModelReply,
)
from omni_test_support.v2.execution import (
    ToolSpec as ToolSpec,
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
from sqlalchemy import inspect, update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.migration_config import head as migration_head
from omni.domain.contracts import BusinessError

NEW_TABLES = {"background_review_batches", "background_review_candidates"}
NEW_TRIGGERS = {
    "background_review_batch_identity",
    "background_review_candidate_identity",
    "background_review_batch_keep",
    "background_review_candidate_keep",
}


class DoneModel:
    async def complete(self, messages, tools, settings):
        return ModelReply("done", cost_micro=0)


def test_v43_head_declares_review_stores_with_matching_columns_and_triggers(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, model=DoneModel(), capabilities=())
        try:
            database = instance._database
            assert database.schema_version == migration_head()

            def compare(u):
                reflection = inspect(u.connection)
                assert NEW_TABLES <= set(reflection.get_table_names())
                for name in NEW_TABLES:
                    table = s.metadata.tables[name]
                    assert {c["name"] for c in reflection.get_columns(name)} == set(table.c.keys())
                    declared = {tuple(c.column_keys) for c in table.foreign_key_constraints}
                    actual = {tuple(c["constrained_columns"]) for c in reflection.get_foreign_keys(name)}
                    assert declared == actual, name
                triggers = {
                    row[0]
                    for row in u.connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
                }
                assert NEW_TRIGGERS <= triggers

            await database.read(compare)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_review_enqueue_is_idempotent_per_run_and_evidence_is_immutable(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, model=DoneModel(), capabilities=())
        try:
            app = instance.application
            database = instance._database
            agent = await configure(app, database)
            receipt = await app.submit_turn(agent, None, "k", "I prefer concise answers")
            run_id = receipt["run_id"]
            await wait_state(app, agent, run_id, {"succeeded"})
            first = await database.write(lambda u: u.background_review_enqueue(agent, {"run_id": run_id}))
            second = await database.write(lambda u: u.background_review_enqueue(agent, {"run_id": run_id}))
            assert first["id"] == second["id"] and first["state"] == "queued"

            async def denied(statement):
                try:
                    await database.write(lambda u: u.connection.execute(statement))
                except BusinessError as exc:
                    assert exc.code == "data_constraint"
                    return
                raise AssertionError("immutable review evidence accepted a forbidden write")

            await denied(
                update(s.background_review_batches)
                .where(s.background_review_batches.c.id == first["id"])
                .values(run_id="00000000-0000-0000-0000-000000000000")
            )
            await denied(
                s.background_review_batches.delete().where(s.background_review_batches.c.id == first["id"])
            )
            report = await database.integrity()
            assert report["integrity"] == "ok" and report["foreign_keys"] == 0
        finally:
            await instance.close()

    asyncio.run(scenario())