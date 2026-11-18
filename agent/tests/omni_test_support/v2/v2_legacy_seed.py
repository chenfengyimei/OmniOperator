"""Explicit pre-v19 fixture seeding only; upgraded databases always use the real repositories."""

from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.repository import Repositories
from omni.domain.contracts import BusinessError


def preserved_rows(connection, tables, *, excluded_run_columns=("extension_capture_epoch", "execution_kind")):
    result = {}
    for name in tables:
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')]
        if name == "runs":
            columns = [column for column in columns if column not in {*excluded_run_columns, "skill_capture_epoch"}]
        projection = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
        result[name] = connection.execute(f'SELECT {projection} FROM "{name}" ORDER BY rowid').fetchall()
    return result


class PreSkillSeedRepositories(Repositories):
    """Only construct fixtures whose frozen schema predates V35."""

    # These schemas also predate V42: fixture creation has no delegated runs.
    # After upgrade the Database actor always constructs real Repositories.
    def delegation_link(self, agent_id, child_id):
        return None

    def delegation_children(self, agent_id, parent_id):
        return []

    def delegation_check_budget(self, agent_id, run_id, amount):
        return None

    def delegation_related_sessions(self, agent_id, sessions):
        return set(sessions)

    def delegation_recover(self):
        return None

    def skill_capture_run(self, *args, **kwargs):
        return None

    def skill_authorize_run(self, *args, **kwargs):
        return None

    def _one(self, table, agent_id, entity_id):
        if table is not s.runs:
            return super()._one(table, agent_id, entity_id)
        columns = [column for column in table.c if column.name != "skill_capture_epoch"]
        row = (
            self.connection.execute(select(*columns).where(table.c.id == entity_id, table.c.agent_id == agent_id))
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("not_found", "Resource does not exist in this Agent space", status=404)
        return dict(row)


class PreRoutingSeedRepositories(PreSkillSeedRepositories):
    """Seed a schema before v33 without invoking new routing persistence."""

    def route_resolve(self, *args, **kwargs):
        return None

    def route_reservation(self, agent_id, run_id, default):
        return default


class LegacySeedRepositories(PreRoutingSeedRepositories):
    def _record(self, table, values, agent_id=None):
        if table is s.runs:
            values = {k: v for k, v in values.items() if k != "execution_kind"}
        return super()._record(table, values, agent_id)

    def owner_command(self, agent_id, run_id):
        return None

    def _one(self, table, agent_id, entity_id):
        if table is not s.runs:
            return super()._one(table, agent_id, entity_id)
        columns = [
            column
            for column in table.c
            if column.name not in {"extension_capture_epoch", "execution_kind", "skill_capture_epoch"}
        ]
        row = (
            self.connection.execute(select(*columns).where(table.c.id == entity_id, table.c.agent_id == agent_id))
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("not_found", "Resource does not exist in this Agent space", status=404)
        return dict(row)

    def extension_capture_run(self, agent_id, run_id):
        return None

    def extension_assert_action(self, agent_id, action_id):
        return None

    def extension_bind_action(self, agent_id, run_id, action_id, spec):
        return None
