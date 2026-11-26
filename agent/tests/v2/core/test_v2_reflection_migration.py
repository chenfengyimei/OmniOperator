"""K17 v2_0052 upgrade keeps v2_0051 drafts and turns on revision-bound owner review."""

from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from alembic import command
from omni_test_support.v2.v2_legacy_seed import PreKernelSeedRepositories
from sqlalchemy import inspect, select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.migration_config import configuration, head
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import ActionResult, Outcome, ToolSpec
from omni.domain.documents import DocumentText
from omni.domain.objects import Blob

BODY = {
    "encounter": "what I saw",
    "facts": "📖",
    "author_view": "",
    "inference": "",
    "previous_judgment": "",
    "revision": "📖",
    "missing": "unknown ending",
    "next_response": "",
}


def test_v0052_upgrade_preserves_v0051_draft_and_enables_owner_review(tmp_path):
    path = tmp_path / "omni.db"
    clock, ids, boot = SystemClock(), UUIDs(), UUIDs().new()
    engine = database_engine(path)
    try:
        with engine.connect() as connection:
            cfg = configuration()
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "v2_0051")
            u = PreKernelSeedRepositories(connection, clock, ids, boot)
            u.initialize()
            agent = connection.execute(select(s.agents.c.id)).scalar_one()

            text = DocumentText.from_text("ab📖")
            expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
            obj = u.object_register(Blob(agent, text.sha256, text.size), "text/plain", expires)
            document = u.document_save(agent, "source", str(obj["id"]), text)
            turn = u.accept_turn(agent, None, "read", "read source", {})
            run = str(turn["run_id"])
            u.transition(agent, run, "running")
            u.prepare_context(agent, run)
            action = u.prepare_action(agent, run, ToolSpec("document_read", "source", {}, False), {}, 1)
            u.authorize_action(agent, str(action["id"]))
            u.document_capture(
                agent,
                run,
                str(action["id"]),
                [{"document_revision_id": str(document["revision_id"]), "start": 2, "end": 3}],
            )
            u.settle_action(agent, str(action["id"]), ActionResult(Outcome.OK, "ab📖"))
            capture = connection.execute(
                select(s.context_documents.c.id).where(s.context_documents.c.action_id == action["id"])
            ).scalar_one()
            journal = u.reflection_journal_save(agent, run, BODY, [{"capture_id": str(capture), "field": "facts"}])
            assert journal["state"] == "pending" and journal["revision"] == 1
            journals_before = [dict(row) for row in connection.execute(select(s.reflection_journals)).mappings()]
            links_before = [dict(row) for row in connection.execute(select(s.reflection_document_links)).mappings()]
            assert len(journals_before) == 1 and len(links_before) == 1
            assert not {
                "reflection_media_links",
                "reflection_reads",
                "reflection_candidates",
                "reflection_slots",
            } & set(inspect(connection).get_table_names())
            connection.commit()

            command.upgrade(cfg, "head")
            command.upgrade(cfg, "head")
            assert head(cfg) == "v2_0058"
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "v2_0058"
            tables = set(inspect(connection).get_table_names())
            assert {
                "reflection_media_links",
                "reflection_reads",
                "reflection_candidates",
                "reflection_slots",
            } <= tables
            for table in ("reflection_media_links", "reflection_reads", "reflection_candidates", "reflection_slots"):
                assert connection.execute(select(s.metadata.tables[table])).first() is None
            assert [
                dict(row) for row in connection.execute(select(s.reflection_journals)).mappings()
            ] == journals_before
            assert [
                dict(row) for row in connection.execute(select(s.reflection_document_links)).mappings()
            ] == links_before
            triggers = {
                row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
            }
            assert {
                "reflection_document_request",
                "reflection_document_delete",
                "reflection_erase_links",
                "reflection_media_delete",
                "reflection_erase_review",
            } <= triggers
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []

            review = u
            read = review.reflection_model_review(agent, run, {"operation": "read", "journal_id": journal["id"]})
            assert read["body"] == BODY and read["revision"] == 1
            selected = review.reflection_model_review(
                agent,
                run,
                {
                    "operation": "select",
                    "journal_id": journal["id"],
                    "revision": 1,
                    "field": "revision",
                    "start": 0,
                    "end": 1,
                    "sentence": "📖",
                },
            )
            assert selected["state"] == "proposed"
            digest = hashlib.sha256("📖".encode()).hexdigest()
            approved = review.reflection_decide(agent, str(selected["candidate_id"]), 1, digest, "approve")
            assert approved["state"] == "approved" and approved["revision"] == 2
            page = review.reflection_page(agent, "", 20, True)
            assert page["items"][0]["sentence"] == "📖"
            assert review.reflection_page(agent, "", 20, False)["items"] == []
            assert (
                review.reflection_decide(agent, str(selected["candidate_id"]), 2, digest, "retract")["state"]
                == "retracted"
            )
            assert review.reflection_page(agent, "", 20, True)["items"] == []
            assert review.reflection_page(agent, "", 20, False)["items"] == []

            with pytest.raises(RuntimeError, match="downgrad"):
                command.downgrade(cfg, "v2_0051")
    finally:
        engine.dispose()
