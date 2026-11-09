"""Terminal intent transactions and immutable metadata receipts; never opens a native process or stores terminal bodies."""

from __future__ import annotations

from sqlalchemy import insert, select, update

from ...domain.contracts import BusinessError
from ...domain.terminals import digest, geometry, identity, integer, page, parse_open, payload_fingerprint
from . import schema as s
from .configuration_repository import ConfigurationRepositories


class TerminalRepositories(ConfigurationRepositories):
    def _terminal_agent(self, agent_id: str) -> None:
        if self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first() is None:
            raise BusinessError("not_found", "Agent space does not exist", status=404)

    def terminal_get(self, agent_id: str, session_id: str) -> dict:
        return self._one(s.terminal_sessions, agent_id, session_id)

    def terminal_page(self, agent_id: str, after: int = 0, limit: int = 20) -> list[dict]:
        page(after, limit)
        self._terminal_agent(agent_id)
        return [
            dict(row)
            for row in self.connection.execute(
                select(s.terminal_sessions)
                .where(s.terminal_sessions.c.agent_id == agent_id, s.terminal_sessions.c.sequence > after)
                .order_by(s.terminal_sessions.c.sequence)
                .limit(limit)
            ).mappings()
        ]

    def terminal_operation_get(self, agent_id: str, session_id: str, operation_id: str) -> dict:
        self.terminal_get(agent_id, session_id)
        row = self._one(s.terminal_operations, agent_id, identity(operation_id))
        if row["session_id"] != session_id:
            raise BusinessError("not_found", "Operation does not belong to this terminal", status=404)
        return {**row, "operation_id": row["id"]}

    def terminal_operation_lookup(self, agent_id: str, operation_id: str) -> dict:
        self._terminal_agent(agent_id)
        row = self._one(s.terminal_operations, agent_id, identity(operation_id))
        return {
            "operation": {**row, "operation_id": row["id"]},
            "session": self.terminal_get(agent_id, row["session_id"]),
        }

    def terminal_open_replay(self, agent_id: str, request: dict) -> dict | None:
        checked = parse_open(request)
        self._terminal_agent(agent_id)
        prior = (
            self.connection.execute(
                select(s.terminal_operations).where(s.terminal_operations.c.id == checked["operation_id"])
            )
            .mappings()
            .first()
        )
        if prior is None:
            return None
        if prior["agent_id"] != agent_id:
            raise BusinessError("not_found", "Operation does not exist in this Agent space", status=404)
        if prior["kind"] != "open" or prior["payload_sha256"] != payload_fingerprint("open", checked):
            raise BusinessError("terminal_operation_conflict", "Operation identity already binds another intent")
        return {
            **self.terminal_get(agent_id, prior["session_id"]),
            "operation": {**dict(prior), "operation_id": prior["id"]},
            "replayed": True,
        }

    def terminal_open_begin(self, agent_id: str, request: dict, spec: dict, permission_epoch: int) -> dict:
        prior = self.terminal_open_replay(agent_id, request)
        if prior is not None:
            return prior
        checked = parse_open(request)
        settings = self.settings(agent_id)
        if settings["policy_revision"] != permission_epoch or settings["value"]["workspace"] != spec.get(
            "configured_workspace", spec["cwd"]
        ):
            raise BusinessError("terminal_authority_changed", "Reload the current configured workspace before opening")
        for key in ("executable", "cwd"):
            if not isinstance(spec.get(key), str) or not 1 <= len(spec[key]) <= 4000 or "\0" in spec[key]:
                raise BusinessError("terminal_invalid", "Native profile is invalid", status=422)
        digest(spec["workspace_sha256"])
        sid = self.ids.new()
        self.connection.execute(
            insert(s.terminal_sessions).values(
                id=sid,
                agent_id=agent_id,
                created_at=self.clock.now(),
                boot_id=self.boot_id,
                shell=checked["shell"],
                executable=spec["executable"],
                cwd=spec["cwd"],
                workspace_sha256=spec["workspace_sha256"],
                permission_epoch=permission_epoch,
                cols=checked["cols"],
                rows=checked["rows"],
                state="starting",
                revision=1,
                cleanup_pending=True,
                error_code="",
            )
        )
        receipt = self.terminal_operation_begin(
            agent_id, sid, checked["operation_id"], "open", payload_fingerprint("open", checked), 0
        )
        return {**self.terminal_get(agent_id, sid), "operation": receipt, "replayed": False}

    def _terminal_authority(self, agent_id: str, session_id: str, state: str) -> dict:
        row = self.terminal_get(agent_id, session_id)
        settings = self.settings(agent_id)
        if row["boot_id"] != self.boot_id or row["state"] != state:
            raise BusinessError(
                "terminal_unavailable", "Terminal does not belong to an active native process in this boot"
            )
        if row["permission_epoch"] != settings["policy_revision"]:
            raise BusinessError(
                "terminal_authority_changed", "Terminal authority changed; only cleanup remains allowed"
            )
        return row

    def terminal_assert_start(self, agent_id: str, session_id: str) -> dict:
        return self._terminal_authority(agent_id, session_id, "starting")

    def terminal_assert_control(self, agent_id: str, session_id: str) -> dict:
        row = self._terminal_authority(agent_id, session_id, "running")
        if row["cleanup_pending"]:
            raise BusinessError("terminal_cleanup_pending", "Native process cleanup is unresolved")
        return row

    def terminal_operation_begin(
        self,
        agent_id: str,
        session_id: str,
        operation_id: str,
        kind: str,
        payload_sha256: str,
        size: int,
        revision: int | None = None,
    ) -> dict:
        row = self.terminal_get(agent_id, session_id)
        operation_id, payload_sha256 = identity(operation_id), digest(payload_sha256)
        integer(size, 0, 16384)
        if (
            kind not in {"open", "input", "resize", "close"}
            or (kind != "input" and size != 0)
            or (kind == "input" and size == 0)
        ):
            raise BusinessError("terminal_invalid", "Invalid terminal operation", status=422)
        old = (
            self.connection.execute(select(s.terminal_operations).where(s.terminal_operations.c.id == operation_id))
            .mappings()
            .first()
        )
        if old is not None:
            if old["agent_id"] != agent_id or old["session_id"] != session_id:
                raise BusinessError("not_found", "Operation does not belong to this terminal", status=404)
            if old["kind"] != kind or old["payload_sha256"] != payload_sha256 or old["bytes"] != size:
                raise BusinessError("terminal_operation_conflict", "Operation identity already has another payload")
            return {**dict(old), "operation_id": operation_id, "replayed": True}
        if kind in {"input", "resize"}:
            self.terminal_assert_control(agent_id, session_id)
        elif kind == "open":
            self.terminal_assert_start(agent_id, session_id)
        elif revision is None or integer(revision, 1) != row["revision"]:
            raise BusinessError("revision_conflict", "Inspect this terminal before requesting cleanup")
        audit_id = self.audit(
            agent_id,
            "terminal." + kind + ".intent",
            session_id,
            {"operation_id": operation_id, "payload_sha256": payload_sha256, "bytes": size, "boot_id": self.boot_id},
        )
        op = self._record(
            s.terminal_operations,
            {
                "id": operation_id,
                "session_id": session_id,
                "boot_id": self.boot_id,
                "kind": kind,
                "payload_sha256": payload_sha256,
                "bytes": size,
                "state": "prepared",
                "intent_audit_id": audit_id,
                "error_code": "",
                "ended_at": None,
            },
            agent_id,
        )
        if kind == "close" and row["state"] not in {"exited", "closing"}:
            self.connection.execute(
                update(s.terminal_sessions)
                .where(s.terminal_sessions.c.id == session_id)
                .values(state="closing", cleanup_pending=True, revision=row["revision"] + 1)
            )
        return {**op, "operation_id": operation_id, "replayed": False}

    def terminal_operation_settle(
        self, agent_id: str, session_id: str, operation_id: str, state: str, error_code: str = ""
    ) -> dict:
        row = self.terminal_operation_get(agent_id, session_id, operation_id)
        if state not in {"accepted", "not_sent", "unknown"} or not isinstance(error_code, str) or len(error_code) > 80:
            raise BusinessError("terminal_invalid", "Invalid terminal result", status=422)
        if row["state"] != "prepared":
            if row["state"] != state or row["error_code"] != error_code:
                raise BusinessError("terminal_receipt_final", "A settled terminal observation cannot be rewritten")
            return row
        self.connection.execute(
            update(s.terminal_operations)
            .where(s.terminal_operations.c.id == operation_id)
            .values(state=state, error_code=error_code, ended_at=self.clock.now())
        )
        self.audit(
            agent_id,
            "terminal.operation." + state,
            session_id,
            {"operation_id": operation_id, "error_code": error_code},
        )
        return self.terminal_operation_get(agent_id, session_id, operation_id)

    def terminal_mark_running(self, agent_id: str, session_id: str, pid: int, created: float) -> dict:
        row = self.terminal_assert_start(agent_id, session_id)
        integer(pid, 1)
        if type(created) not in {int, float} or not 0 < created < 1e12:
            raise BusinessError("terminal_invalid", "Native process identity is missing")
        self.connection.execute(
            update(s.terminal_sessions)
            .where(s.terminal_sessions.c.id == session_id)
            .values(
                state="running", pid=pid, process_created=created, cleanup_pending=False, revision=row["revision"] + 1
            )
        )
        return self.terminal_get(agent_id, session_id)

    def terminal_mark_resized(self, agent_id: str, session_id: str, cols: int, rows: int) -> dict:
        row = self.terminal_assert_control(agent_id, session_id)
        cols, rows = geometry(cols, rows)
        if (cols, rows) != (row["cols"], row["rows"]):
            self.connection.execute(
                update(s.terminal_sessions)
                .where(s.terminal_sessions.c.id == session_id)
                .values(cols=cols, rows=rows, revision=row["revision"] + 1)
            )
        return self.terminal_get(agent_id, session_id)

    def terminal_mark_closed(
        self, agent_id: str, session_id: str, reclaimed: bool, exit_code: int | None, error_code: str = ""
    ) -> dict:
        row = self.terminal_get(agent_id, session_id)
        if type(reclaimed) is not bool or (exit_code is not None and type(exit_code) is not int):
            raise BusinessError("terminal_invalid", "Native cleanup observation is invalid")
        if row["boot_id"] != self.boot_id and reclaimed and row["state"] != "exited":
            raise BusinessError("terminal_old_boot", "Persisted process IDs cannot authorize cleanup")
        value = {
            "state": "exited" if reclaimed else "unknown",
            "cleanup_pending": not reclaimed,
            "exit_code": row["exit_code"] if row["exit_code"] is not None else exit_code,
            "error_code": error_code,
        }
        if row["state"] == "exited" and not reclaimed:
            return row
        if any(row[key] != item for key, item in value.items()):
            self.connection.execute(
                update(s.terminal_sessions)
                .where(s.terminal_sessions.c.id == session_id)
                .values(
                    **value,
                    ended_at=(row["ended_at"] or self.clock.now()) if reclaimed else None,
                    revision=row["revision"] + 1,
                )
            )
        return self.terminal_get(agent_id, session_id)

    def terminal_recover(self) -> int:
        rows = (
            self.connection.execute(
                select(s.terminal_sessions).where(s.terminal_sessions.c.state.in_(["starting", "running", "closing"]))
            )
            .mappings()
            .all()
        )
        for row in rows:
            self.connection.execute(
                update(s.terminal_sessions)
                .where(s.terminal_sessions.c.id == row["id"])
                .values(
                    state="unknown",
                    cleanup_pending=True,
                    error_code="terminal_previous_boot",
                    revision=row["revision"] + 1,
                )
            )
            self.audit(row["agent_id"], "terminal.recovered_unknown", row["id"], {"no_replay": True})
        self.connection.execute(
            update(s.terminal_operations)
            .where(s.terminal_operations.c.state == "prepared")
            .values(state="unknown", error_code="terminal_previous_boot", ended_at=self.clock.now())
        )
        return len(rows)
