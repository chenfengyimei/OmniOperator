"""One live approved-duplicate predicate for recall and new context capture; never edits memory truth."""

from typing import Any, cast

from sqlalchemy import Select, Table, and_, or_, select
from sqlalchemy.sql.base import ReadOnlyColumnCollection
from sqlalchemy.sql.elements import ColumnElement, KeyedColumnElement
from sqlalchemy.sql.selectable import Alias, Exists, NamedFromClause

from ...domain.memory_organization import POLICY
from . import schema as s


def live_organization_relations() -> Select[tuple[str, str, str, str]]:
    candidate = s.memory_organization_candidates.alias("organization_relation")
    batch = s.memory_organization_batches.alias("organization_batch")
    keep = s.memory_organization_sources.alias("organization_keep_source")
    duplicate = s.memory_organization_sources.alias("organization_duplicate_source")
    kept_memory = s.memory_entries.alias("organization_kept_memory")
    duplicate_memory = s.memory_entries.alias("organization_duplicate_memory")
    kept_revision = s.memory_revisions.alias("organization_kept_revision")
    duplicate_revision = s.memory_revisions.alias("organization_duplicate_revision")

    def live_source(
        source: NamedFromClause[ReadOnlyColumnCollection[str, KeyedColumnElement[Any]]],
        memory: NamedFromClause[ReadOnlyColumnCollection[str, KeyedColumnElement[Any]]],
        revision: NamedFromClause[ReadOnlyColumnCollection[str, KeyedColumnElement[Any]]],
    ) -> ColumnElement[bool]:
        person = s.people.alias("organization_person_" + source.name)
        return and_(
            memory.c.agent_id == source.c.agent_id,
            memory.c.id == source.c.memory_id,
            memory.c.kind == "fact",
            memory.c.deleted_at.is_(None),
            memory.c.revision == source.c.revision,
            memory.c.person_id.is_not_distinct_from(source.c.person_id),
            memory.c.session_id.is_not_distinct_from(source.c.session_id),
            revision.c.id == source.c.memory_revision_id,
            revision.c.memory_id == memory.c.id,
            revision.c.agent_id == memory.c.agent_id,
            revision.c.revision == source.c.revision,
            revision.c.content.collate("BINARY") == memory.c.content.collate("BINARY"),
            or_(
                memory.c.person_id.is_(None),
                select(person.c.id)
                .where(
                    person.c.id == memory.c.person_id,
                    person.c.agent_id == memory.c.agent_id,
                    person.c.deleted_at.is_(None),
                )
                .correlate(memory)
                .exists(),
            ),
        )

    statement = (
        select(
            candidate.c.id.label("candidate_id"),
            candidate.c.agent_id,
            kept_memory.c.id.label("keep_memory_id"),
            duplicate_memory.c.id.label("duplicate_memory_id"),
        )
        .select_from(
            candidate.join(batch, and_(batch.c.id == candidate.c.batch_id, batch.c.agent_id == candidate.c.agent_id))
            .join(
                keep,
                and_(
                    keep.c.id == candidate.c.keep_source_id,
                    keep.c.batch_id == batch.c.id,
                    keep.c.agent_id == batch.c.agent_id,
                ),
            )
            .join(
                duplicate,
                and_(
                    duplicate.c.id == candidate.c.duplicate_source_id,
                    duplicate.c.batch_id == batch.c.id,
                    duplicate.c.agent_id == batch.c.agent_id,
                ),
            )
            .join(kept_memory, and_(kept_memory.c.id == keep.c.memory_id, kept_memory.c.agent_id == keep.c.agent_id))
            .join(
                duplicate_memory,
                and_(
                    duplicate_memory.c.id == duplicate.c.memory_id, duplicate_memory.c.agent_id == duplicate.c.agent_id
                ),
            )
            .join(kept_revision, kept_revision.c.id == keep.c.memory_revision_id)
            .join(duplicate_revision, duplicate_revision.c.id == duplicate.c.memory_revision_id)
        )
        .where(
            candidate.c.state == "applied",
            batch.c.state.in_(["ready", "complete", "cancelled"]),
            batch.c.policy == POLICY,
            batch.c.history_epoch == select(s.history_identity.c.epoch).scalar_subquery(),
            live_source(keep, kept_memory, kept_revision),
            live_source(duplicate, duplicate_memory, duplicate_revision),
            keep.c.person_id.is_not_distinct_from(duplicate.c.person_id),
            keep.c.session_id.is_not_distinct_from(duplicate.c.session_id),
            keep.c.raw_sha256 == duplicate.c.raw_sha256,
            kept_memory.c.content.collate("BINARY") == duplicate_memory.c.content.collate("BINARY"),
        )
    )
    return cast(Select[tuple[str, str, str, str]], statement)


def memory_is_organized(memory: Table | Alias = s.memory_entries) -> Exists:
    relations = live_organization_relations().subquery("live_memory_organization")
    return (
        select(relations.c.candidate_id)
        .where(relations.c.agent_id == memory.c.agent_id, relations.c.duplicate_memory_id == memory.c.id)
        .correlate(memory)
        .exists()
    )
