"""Explicit pre-v19 fixture seeding only; upgraded databases always use the real repositories."""

from sqlalchemy import MetaData, Table, select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.repository import Repositories
from omni.domain.contracts import BusinessError
from omni.domain.routing import KERNEL_DEFAULT, POLICY_DEFAULT

_MEMORY_DECAY_COLUMNS = ("importance", "clarity", "recall_count", "decay_steps", "pinned", "last_seen_at")


def preserved_rows(
    connection,
    tables,
    *,
    excluded_run_columns=("extension_capture_epoch", "execution_kind"),
    excluded_action_columns=(),
    excluded_memory_columns=_MEMORY_DECAY_COLUMNS,
):
    result = {}
    for name in tables:
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')]
        if name == "runs":
            columns = [column for column in columns if column not in {*excluded_run_columns, "skill_capture_epoch"}]
        if name == "actions":
            columns = [column for column in columns if column not in excluded_action_columns]
        if name == "memory_entries":
            columns = [column for column in columns if column not in excluded_memory_columns]
        projection = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
        result[name] = connection.execute(f'SELECT {projection} FROM "{name}" ORDER BY rowid').fetchall()
    return result


class PreKernelSeedRepositories(Repositories):
    """Create original routing rows on pinned schemas prior to v53."""

    def omniweave_private_scope(self, agent_id: str, field: str, identity: str) -> bool:
        # Historical fixture schemas predate Worker sessions. Live databases reach the current head before admission.
        return False

    def omniweave_router(self, agent_id, run_id):
        return None

    def omniweave_assert_action(self, agent_id, run_id, spec):
        return None

    def _record(self, table, values, agent_id=None):
        if table is s.run_routes:
            table = Table("run_routes", MetaData(), autoload_with=self.connection)
            values = {k: v for k, v in values.items() if k in table.c}
        return super()._record(table, values, agent_id)

    def route_policy(self, agent_id):
        return {
            "id": None,
            "revision": 0,
            "agent_id": agent_id,
            **POLICY_DEFAULT,
            "kernel": KERNEL_DEFAULT,
        }


class PreSkillSeedRepositories(PreKernelSeedRepositories):
    """Only construct fixtures whose frozen schema predates V35."""

    # These schemas also predate V42: fixture creation has no delegated runs.
    # After upgrade the Database actor always constructs real Repositories.
    # v0047 decay columns are absent at pinned pre-v47 schemas; seed original columns.
    def _record(self, table, values, agent_id=None):
        if table is s.memory_entries:
            values = {k: v for k, v in values.items() if k not in _MEMORY_DECAY_COLUMNS}
        return super()._record(table, values, agent_id)

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

    def claim_runs(self, limit):
        # v42 added run_delegations, but these fixtures are created on a pinned pre-v42 schema;
        # use the original claim query without the delegation NOT IN subquery.
        result = []
        rows = self.connection.execute(
            select(s.work_items.c.agent_id, s.work_items.c.run_id)
            .join(s.agents, s.agents.c.id == s.work_items.c.agent_id)
            .where(
                s.work_items.c.kind == "turn",
                s.work_items.c.state == "pending",
                s.agents.c.enabled.is_(True),
            )
            .order_by(s.work_items.c.queue_sequence)
            .limit(64)
        ).all()
        for agent_id, run_id in rows:
            if len(result) >= max(0, min(4, limit)):
                break
            claim = self._claim_run(agent_id, run_id)
            if claim:
                result.append(claim)
        return result

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
