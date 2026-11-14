"""MCP runtime generations, catalog snapshots, per-tool grants, run captures and call records.

The repository only reads stored rows and writes bounded state. It never opens a
socket, spawns a process or resolves a credential; the application service owns
those steps and passes already-validated catalog rows in.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from sqlalchemy import Select, and_, func, select, update

from ...application.ports import Record
from ...domain.contracts import JSON, BusinessError, ToolSpec
from ...domain.mcp import (
    MAX_TEXT_CHARS,
    MAX_TOOLS,
    TERMINAL_CALL_STATES,
    catalog_fingerprint,
    parse_tool_authorization,
    tool_spec,
)
from . import schema as s
from .context_repository import ContextRepositories


class MCPRepositories(ContextRepositories):
    # ---------- configuration and runtime views ----------

    def mcp_configuration(self, agent_id: str, config_id: str, revision_id: str = "") -> Record:
        config = self.configuration_get(agent_id, config_id)
        if config["kind"] != "mcp":
            raise BusinessError("mcp_configuration_invalid", "This configuration is not an MCP server", status=422)
        if not config["enabled"] or (revision_id and config["revision_id"] != revision_id):
            raise BusinessError("mcp_configuration_revoked", "MCP configuration changed or was disabled")
        if not self.connection.execute(
            select(s.agents.c.id).where(s.agents.c.id == agent_id, s.agents.c.enabled.is_(True))
        ).first():
            raise BusinessError("agent_disabled", "Agent is disabled")
        return config

    def _mcp_server(self, agent_id: str, config_id: str) -> Record | None:
        row = (
            self.connection.execute(
                select(s.mcp_servers).where(
                    s.mcp_servers.c.agent_id == agent_id, s.mcp_servers.c.config_id == config_id
                )
            )
            .mappings()
            .first()
        )
        return None if row is None else cast(Record, dict(row))

    def _mcp_require_server(self, agent_id: str, config_id: str) -> Record:
        server = self._mcp_server(agent_id, config_id)
        if server is None:
            raise BusinessError("mcp_not_connected", "This MCP server has no runtime state yet", status=404)
        return server

    def _mcp_counts(self, agent_id: str, server_id: str) -> Record:
        total = self.connection.execute(
            select(func.count())
            .select_from(s.mcp_grants)
            .where(
                s.mcp_grants.c.agent_id == agent_id,
                s.mcp_grants.c.server_id == server_id,
                s.mcp_grants.c.revoked_at.is_(None),
            )
        ).scalar_one()
        read_only = self.connection.execute(
            select(func.count())
            .select_from(s.mcp_grants)
            .where(
                s.mcp_grants.c.agent_id == agent_id,
                s.mcp_grants.c.server_id == server_id,
                s.mcp_grants.c.revoked_at.is_(None),
                s.mcp_grants.c.read_only.is_(True),
            )
        ).scalar_one()
        return {"authorized_tools": total, "read_only_tools": read_only}

    def _mcp_view(self, agent_id: str, config: Record, server: Record | None) -> Record:
        value = cast(Record, config["value"])
        view: Record = {
            "config_id": config["id"],
            "name": config["name"],
            "enabled": config["enabled"],
            "revision": config["revision"],
            "revision_id": config["revision_id"],
            "transport": value["transport"],
            "auth_mode": value["auth_mode"],
            "endpoint": value["endpoint"],
            "command": value["command"],
            "arguments": cast(list[JSON], value["arguments"]),
            "state": "idle",
            "authority_epoch": 0,
            "fingerprint": "",
            "tool_count": 0,
            "error_code": "",
            "connected_at": None,
            "authorized_tools": 0,
            "read_only_tools": 0,
            "needs_confirmation": False,
        }
        if server is None:
            return view
        counts = self._mcp_counts(agent_id, str(server["id"]))
        return {
            **view,
            **counts,
            "state": server["state"],
            "authority_epoch": server["authority_epoch"],
            "fingerprint": server["fingerprint"],
            "tool_count": server["tool_count"],
            "error_code": server["error_code"],
            "connected_at": server["connected_at"],
            "needs_confirmation": bool(
                server["state"] == "ready"
                and cast(int, server["tool_count"]) > 0
                and counts["authorized_tools"] == 0
                and server["fingerprint"]
            ),
        }

    def mcp_server_overview(self, agent_id: str) -> list[Record]:
        return [
            self._mcp_view(
                agent_id,
                self.configuration_get(agent_id, cast(str, entity["id"])),
                self._mcp_server(agent_id, cast(str, entity["id"])),
            )
            for entity in self.configuration_page(agent_id, "mcp", "", 200)
        ]

    def mcp_server_get(self, agent_id: str, config_id: str) -> Record:
        config = self.configuration_get(agent_id, config_id)
        if config["kind"] != "mcp":
            raise BusinessError("mcp_configuration_invalid", "This configuration is not an MCP server", status=422)
        return self._mcp_view(agent_id, config, self._mcp_server(agent_id, config_id))

    # ---------- connection lifecycle ----------

    def _mcp_runtime(self, agent_id: str, config_id: str, **values: object) -> Record:
        self.connection.execute(
            update(s.mcp_servers)
            .where(s.mcp_servers.c.agent_id == agent_id, s.mcp_servers.c.config_id == config_id)
            .values(**values)
        )
        return self._mcp_require_server(agent_id, config_id)

    def _mcp_revoke(self, agent_id: str, server: Record, reason: str) -> int:
        revoked = self.connection.execute(
            update(s.mcp_grants)
            .where(
                s.mcp_grants.c.agent_id == agent_id,
                s.mcp_grants.c.server_id == server["id"],
                s.mcp_grants.c.revoked_at.is_(None),
            )
            .values(revoked_at=self.clock.now())
        ).rowcount
        if revoked:
            self.audit(
                agent_id,
                "mcp.authorization_revoked",
                str(server["config_id"]),
                {"reason": reason, "tools": int(revoked), "authority_epoch": server["authority_epoch"]},
            )
        return revoked

    def mcp_server_begin(self, agent_id: str, config_id: str, revision: int) -> Record:
        config = self.mcp_configuration(agent_id, config_id)
        if type(revision) is not int or revision != config["revision"]:
            raise BusinessError("mcp_revision_conflict", "MCP configuration changed; reload the current revision")
        server = self._mcp_server(agent_id, config_id)
        if server is None:
            server = self._record(
                s.mcp_servers,
                {
                    "config_id": config_id,
                    "config_revision_id": config["revision_id"],
                    "state": "idle",
                    "authority_epoch": 1,
                    "fingerprint": "",
                    "tool_count": 0,
                    "error_code": "",
                    "connected_at": None,
                },
                agent_id,
            )
            self.audit(
                agent_id,
                "mcp.server_registered",
                config_id,
                {"config_revision_id": config["revision_id"], "authority_epoch": 1},
            )
        if server["state"] == "starting":
            raise BusinessError("mcp_server_busy", "This MCP server is already starting", retryable=True, status=503)
        if server["state"] == "ready" and server["config_revision_id"] == config["revision_id"]:
            return {**config, "server": server, "already_ready": True, "revoked": 0}
        revoked = 0
        epoch = cast(int, server["authority_epoch"])
        if server["config_revision_id"] != config["revision_id"]:
            revoked = self._mcp_revoke(agent_id, server, "mcp_configuration_revoked")
            epoch += 1
            # A ready runtime leaves through idle first; every transition stays in the frozen whitelist.
            server = self._mcp_runtime(
                agent_id,
                config_id,
                state="idle" if server["state"] == "ready" else server["state"],
                config_revision_id=config["revision_id"],
                authority_epoch=epoch,
                fingerprint="",
                tool_count=0,
                error_code="",
                connected_at=None,
            )
        server = self._mcp_runtime(agent_id, config_id, state="starting", error_code="")
        self.audit(
            agent_id,
            "mcp.connect_started",
            config_id,
            {"config_revision_id": config["revision_id"], "authority_epoch": epoch, "revoked": revoked},
        )
        return {**config, "server": server, "already_ready": False, "revoked": revoked}

    def _mcp_catalog(self, agent_id: str, server_id: str, fingerprint: str) -> Record | None:
        row = (
            self.connection.execute(
                select(s.mcp_catalogs).where(
                    s.mcp_catalogs.c.agent_id == agent_id,
                    s.mcp_catalogs.c.server_id == server_id,
                    s.mcp_catalogs.c.fingerprint == fingerprint,
                )
            )
            .mappings()
            .first()
        )
        return None if row is None else cast(Record, dict(row))

    def _mcp_tools(self, agent_id: str, catalog_id: str) -> list[Record]:
        return [
            cast(Record, dict(row))
            for row in self.connection.execute(
                select(s.mcp_tools)
                .where(s.mcp_tools.c.agent_id == agent_id, s.mcp_tools.c.catalog_id == catalog_id)
                .order_by(s.mcp_tools.c.position, s.mcp_tools.c.name)
            ).mappings()
        ]

    def _mcp_active_grants(self, agent_id: str, server_id: str) -> dict[str, Record]:
        return {
            str(row["tool_id"]): cast(Record, dict(row))
            for row in self.connection.execute(
                select(s.mcp_grants).where(
                    s.mcp_grants.c.agent_id == agent_id,
                    s.mcp_grants.c.server_id == server_id,
                    s.mcp_grants.c.revoked_at.is_(None),
                )
            ).mappings()
        }

    def mcp_catalog_commit(self, agent_id: str, config_id: str, revision_id: str, tools: Sequence[Record]) -> Record:
        config = self.mcp_configuration(agent_id, config_id, revision_id)
        server = self._mcp_require_server(agent_id, config_id)
        if server["state"] not in {"starting", "ready"}:
            raise BusinessError("mcp_server_state_invalid", "Connect this MCP server before committing its catalog")
        if len(tools) > MAX_TOOLS:
            raise BusinessError("mcp_catalog_too_large", "The MCP catalog exceeds its bound", status=502)
        fingerprint = catalog_fingerprint([dict(tool) for tool in tools])
        catalog = self._mcp_catalog(agent_id, str(server["id"]), fingerprint)
        if catalog is None:
            catalog = self._record(
                s.mcp_catalogs,
                {
                    "server_id": server["id"],
                    "config_revision_id": revision_id,
                    "fingerprint": fingerprint,
                    "tool_count": len(tools),
                },
                agent_id,
            )
            for tool in tools:
                self._record(
                    s.mcp_tools,
                    {
                        "server_id": server["id"],
                        "catalog_id": catalog["id"],
                        "name": tool["name"],
                        "exposed_name": tool["exposed_name"],
                        "description": tool["description"],
                        "input_schema": tool["input_schema"],
                        "tool_fingerprint": tool["tool_fingerprint"],
                        "position": tool["position"],
                    },
                    agent_id,
                )
        drift = bool(server["fingerprint"]) and server["fingerprint"] != fingerprint
        revoked = self._mcp_revoke(agent_id, server, "mcp_catalog_drift") if drift else 0
        epoch = cast(int, server["authority_epoch"]) + (1 if drift else 0)
        server = self._mcp_runtime(
            agent_id,
            config_id,
            state="ready",
            authority_epoch=epoch,
            fingerprint=fingerprint,
            tool_count=len(tools),
            error_code="",
            connected_at=self.clock.now(),
        )
        self.audit(
            agent_id,
            "mcp.connected",
            config_id,
            {
                "catalog_id": catalog["id"],
                "fingerprint": fingerprint,
                "tools": len(tools),
                "drift": drift,
                "authority_epoch": epoch,
            },
        )
        return cast(
            Record,
            {
                **config,
                "server": server,
                "catalog": catalog,
                "tools": list(tools),
                "drift": drift,
                "revoked": revoked,
            },
        )

    def mcp_server_failed(self, agent_id: str, config_id: str, error_code: str) -> Record:
        server = self._mcp_require_server(agent_id, config_id)
        values: Record = {"error_code": error_code[:80]}
        if server["state"] in {"starting", "ready"}:
            values["state"] = "failed"
        self._mcp_runtime(agent_id, config_id, **values)
        self.audit(agent_id, "mcp.connect_failed", config_id, {"error_code": error_code[:80]})
        return self.mcp_server_get(agent_id, config_id)

    def mcp_server_stopped(self, agent_id: str, config_id: str, error_code: str = "") -> Record:
        server = self._mcp_require_server(agent_id, config_id)
        if server["state"] != "idle":
            self._mcp_runtime(agent_id, config_id, state="idle", connected_at=None, error_code=error_code[:80])
        elif error_code:
            self._mcp_runtime(agent_id, config_id, error_code=error_code[:80])
        self.audit(agent_id, "mcp.disconnected", config_id, {"error_code": error_code[:80]})
        return self.mcp_server_get(agent_id, config_id)

    def mcp_server_reset(self, agent_id: str, config_id: str) -> Record:
        server = self._mcp_require_server(agent_id, config_id)
        if server["state"] != "idle":
            self._mcp_runtime(agent_id, config_id, state="idle", connected_at=None, error_code="")
        else:
            self._mcp_runtime(agent_id, config_id, error_code="")
        self.audit(agent_id, "mcp.reset", config_id, {"authority_epoch": server["authority_epoch"]})
        return self.mcp_server_get(agent_id, config_id)

    # ---------- catalogs and owner authorizations ----------

    def mcp_catalog_page(self, agent_id: str, config_id: str, after: int = -1, limit: int = 20) -> Record:
        if type(after) is not int or type(limit) is not int or not 1 <= limit <= 100:
            raise BusinessError("mcp_page_invalid", "Invalid MCP catalog cursor or limit", status=422)
        server = self._mcp_require_server(agent_id, config_id)
        view = self._mcp_view(agent_id, self.configuration_get(agent_id, config_id), server)
        if not server["fingerprint"]:
            return {"server": view, "catalog": None, "items": [], "next_after": None, "authorized": [], "read_only": []}
        catalog = self._mcp_catalog(agent_id, str(server["id"]), cast(str, server["fingerprint"]))
        if catalog is None:
            raise BusinessError("mcp_catalog_missing", "The current catalog snapshot is unavailable")
        all_tools = self._mcp_tools(agent_id, str(catalog["id"]))
        grants = self._mcp_active_grants(agent_id, str(server["id"]))
        rows = [row for row in all_tools if cast(int, row["position"]) > after][:limit]
        items: list[Record] = []
        for row in rows:
            grant = grants.get(str(row["id"]))
            items.append(
                {
                    "tool_id": row["id"],
                    "name": row["name"],
                    "exposed_name": row["exposed_name"],
                    "description": row["description"],
                    "input_schema": row["input_schema"],
                    "tool_fingerprint": row["tool_fingerprint"],
                    "position": row["position"],
                    "authorized": grant is not None,
                    "read_only": bool(grant["read_only"]) if grant else False,
                }
            )
        names = {str(row["id"]): str(row["name"]) for row in all_tools}
        return cast(
            Record,
            {
                "server": view,
                "catalog": {
                    "id": catalog["id"],
                    "fingerprint": catalog["fingerprint"],
                    "tool_count": catalog["tool_count"],
                    "config_revision_id": catalog["config_revision_id"],
                },
                "items": items,
                "next_after": items[-1]["position"] if len(items) == limit else None,
                "authorized": sorted(names[tool_id] for tool_id in grants),
                "read_only": sorted(names[tool_id] for tool_id, grant in grants.items() if grant["read_only"]),
            },
        )

    def mcp_authorize(self, agent_id: str, config_id: str, request: Record) -> Record:
        self.mcp_configuration(agent_id, config_id)
        server = self._mcp_require_server(agent_id, config_id)
        if server["state"] != "ready" or not server["fingerprint"]:
            raise BusinessError("mcp_not_connected", "Connect and discover this MCP server before authorizing tools")
        catalog = self._mcp_catalog(agent_id, str(server["id"]), cast(str, server["fingerprint"]))
        if catalog is None:
            raise BusinessError("mcp_catalog_missing", "The current catalog snapshot is unavailable")
        rows = self._mcp_tools(agent_id, str(catalog["id"]))
        by_id = {str(row["id"]): row for row in rows}
        checked = parse_tool_authorization(request, by_id, cast(str, server["fingerprint"]))
        checked_tools = cast(dict[str, Record], checked["tools"])
        names = {tool_id: str(by_id[tool_id]["name"]) for tool_id in checked_tools}
        audit_id = self.audit(
            agent_id,
            "mcp.authorization_reviewed",
            config_id,
            cast(
                Record,
                {
                    "catalog_fingerprint": server["fingerprint"],
                    "tools": sorted(names.values()),
                    "read_only": sorted(name for tool_id, name in names.items() if checked_tools[tool_id]["read_only"]),
                    "evidence": cast(str, checked["evidence"]),
                },
            ),
        )
        active = self._mcp_active_grants(agent_id, str(server["id"]))
        now = self.clock.now()
        revoked = 0
        for tool_id, grant in list(active.items()):
            wanted = checked_tools.get(tool_id)
            if wanted is None or wanted["read_only"] != bool(grant["read_only"]):
                self.connection.execute(
                    update(s.mcp_grants).where(s.mcp_grants.c.id == grant["id"]).values(revoked_at=now)
                )
                revoked += 1
                active.pop(tool_id)
        created = 0
        for tool_id, wanted in checked_tools.items():
            if tool_id in active:
                continue
            tool = by_id[tool_id]
            self._record(
                s.mcp_grants,
                {
                    "server_id": server["id"],
                    "catalog_id": catalog["id"],
                    "tool_id": tool_id,
                    "catalog_fingerprint": server["fingerprint"],
                    "tool_fingerprint": tool["tool_fingerprint"],
                    "authority_epoch": server["authority_epoch"],
                    "read_only": wanted["read_only"],
                    "revoked_at": None,
                    "review_audit_id": audit_id,
                },
                agent_id,
            )
            created += 1
        self.audit(
            agent_id,
            "mcp.authorized",
            config_id,
            {
                "catalog_fingerprint": server["fingerprint"],
                "authorized": len(checked_tools),
                "read_only": sum(1 for item in checked_tools.values() if item["read_only"]),
                "created": created,
                "revoked": revoked,
                "authority_epoch": server["authority_epoch"],
            },
        )
        return {**self.mcp_catalog_page(agent_id, config_id, -1, 100), "created": created, "revoked": revoked}

    def mcp_tool_decision(
        self,
        agent_id: str,
        config_id: str,
        tool_id: str,
        *,
        authorized: bool,
        read_only: bool,
        catalog_fingerprint: str,
        evidence: str,
    ) -> Record:
        self.mcp_configuration(agent_id, config_id)
        server = self._mcp_require_server(agent_id, config_id)
        if server["state"] != "ready" or not server["fingerprint"]:
            raise BusinessError("mcp_not_connected", "Connect and discover this MCP server before authorizing tools")
        if catalog_fingerprint != server["fingerprint"]:
            raise BusinessError(
                "mcp_catalog_changed", "The catalog fingerprint changed; inspect the current tools again", status=409
            )
        catalog = self._mcp_catalog(agent_id, str(server["id"]), cast(str, server["fingerprint"]))
        if catalog is None:
            raise BusinessError("mcp_catalog_missing", "The current catalog snapshot is unavailable")
        tool = self._one(s.mcp_tools, agent_id, tool_id)
        if tool["catalog_id"] != catalog["id"]:
            raise BusinessError("mcp_authorization_invalid", "This tool is not in the current catalog", status=422)
        if type(read_only) is not bool or not isinstance(cast(object, evidence), str) or not 1 <= len(evidence) <= 200:
            raise BusinessError("mcp_authorization_invalid", "A decision needs a read-only flag and bounded evidence")
        audit_id = self.audit(
            agent_id,
            "mcp.authorization_reviewed",
            config_id,
            {
                "catalog_fingerprint": server["fingerprint"],
                "tools": [tool["name"]],
                "read_only": [tool["name"]] if authorized and read_only else [],
                "evidence": evidence,
            },
        )
        active = self._mcp_active_grants(agent_id, str(server["id"]))
        grant = active.get(tool_id)
        created = revoked = 0
        if grant is not None and (not authorized or bool(grant["read_only"]) != read_only):
            self.connection.execute(
                update(s.mcp_grants).where(s.mcp_grants.c.id == grant["id"]).values(revoked_at=self.clock.now())
            )
            revoked = 1
            grant = None
        if authorized and grant is None:
            self._record(
                s.mcp_grants,
                {
                    "server_id": server["id"],
                    "catalog_id": catalog["id"],
                    "tool_id": tool_id,
                    "catalog_fingerprint": server["fingerprint"],
                    "tool_fingerprint": tool["tool_fingerprint"],
                    "authority_epoch": server["authority_epoch"],
                    "read_only": read_only,
                    "revoked_at": None,
                    "review_audit_id": audit_id,
                },
                agent_id,
            )
            created = 1
        self.audit(
            agent_id,
            "mcp.authorized" if authorized else "mcp.authorization_revoked",
            config_id,
            {
                "catalog_fingerprint": server["fingerprint"],
                "tool": tool["name"],
                "read_only": read_only,
                "created": created,
                "revoked": revoked,
                "authority_epoch": server["authority_epoch"],
            },
        )
        return {
            **self.mcp_catalog_page(agent_id, config_id, -1, 100),
            "created": created,
            "revoked": revoked,
        }

    def mcp_revoke_grants(self, agent_id: str, config_id: str, reason: str) -> Record:
        server = self._mcp_require_server(agent_id, config_id)
        revoked = self._mcp_revoke(agent_id, server, reason)
        return {"revoked": revoked, "authority_epoch": server["authority_epoch"]}

    def mcp_oauth_authorized(self, agent_id: str, config_id: str) -> Record:
        """A completed remote authorization invalidates old grants and requires a reconnect."""
        config = self.mcp_configuration(agent_id, config_id)
        server = self._mcp_server(agent_id, config_id)
        if server is None:
            self._record(
                s.mcp_servers,
                {
                    "config_id": config_id,
                    "config_revision_id": config["revision_id"],
                    "state": "idle",
                    "authority_epoch": 1,
                    "fingerprint": "",
                    "tool_count": 0,
                    "error_code": "",
                    "connected_at": None,
                },
                agent_id,
            )
            self.audit(agent_id, "mcp.oauth_authorized", config_id, {"authority_epoch": 1})
            return self.mcp_server_get(agent_id, config_id)
        revoked = self._mcp_revoke(agent_id, server, "mcp_oauth_authorized")
        revision_changed = server["config_revision_id"] != config["revision_id"]
        values: Record = {
            "state": "idle",
            "connected_at": None,
            "error_code": "",
            "authority_epoch": cast(int, server["authority_epoch"]) + 1,
        }
        if revision_changed:
            values.update(config_revision_id=config["revision_id"], fingerprint="", tool_count=0)
        if server["state"] == "idle":
            values.pop("state")
        self._mcp_runtime(agent_id, config_id, **values)
        self.audit(
            agent_id,
            "mcp.oauth_authorized",
            config_id,
            {"revoked": revoked, "authority_epoch": values["authority_epoch"], "revision_changed": revision_changed},
        )
        return self.mcp_server_get(agent_id, config_id)

    # ---------- run capture and dispatch ----------

    def _mcp_live_grants(self, agent_id: str) -> Select[tuple[object, ...]]:
        statement = (
            select(
                s.mcp_grants.c.id.label("grant_id"),
                s.mcp_grants.c.server_id,
                s.mcp_grants.c.tool_id,
                s.mcp_grants.c.read_only,
                s.mcp_grants.c.authority_epoch,
                s.mcp_grants.c.catalog_fingerprint,
                s.mcp_grants.c.tool_fingerprint,
                s.mcp_tools.c.name,
                s.mcp_tools.c.exposed_name,
                s.mcp_tools.c.description,
                s.mcp_tools.c.input_schema,
                s.mcp_servers.c.config_id,
                s.mcp_servers.c.config_revision_id,
                s.mcp_servers.c.fingerprint.label("server_fingerprint"),
            )
            .join(
                s.mcp_servers,
                and_(
                    s.mcp_servers.c.id == s.mcp_grants.c.server_id, s.mcp_servers.c.agent_id == s.mcp_grants.c.agent_id
                ),
            )
            .join(
                s.mcp_tools,
                and_(s.mcp_tools.c.id == s.mcp_grants.c.tool_id, s.mcp_tools.c.agent_id == s.mcp_grants.c.agent_id),
            )
            .where(
                s.mcp_grants.c.agent_id == agent_id,
                s.mcp_grants.c.revoked_at.is_(None),
                s.mcp_servers.c.state == "ready",
                s.mcp_servers.c.authority_epoch == s.mcp_grants.c.authority_epoch,
                s.mcp_servers.c.fingerprint == s.mcp_grants.c.catalog_fingerprint,
                s.mcp_tools.c.tool_fingerprint == s.mcp_grants.c.tool_fingerprint,
            )
        )
        return cast(Select[tuple[object, ...]], statement)

    def _mcp_capture_row(self, agent_id: str, run_id: str, binding_id: str) -> Record:
        binding = self._one(s.mcp_run_tools, agent_id, binding_id)
        if str(binding["run_id"]) != run_id:
            raise BusinessError("mcp_not_authorized", "MCP run capture belongs to another run")
        return {
            **self._mcp_live_binding(agent_id, binding),
            "binding_id": binding_id,
            "run_id": run_id,
            "writes": bool(binding["writes"]),
            "name": str(binding["name"]),
        }

    def _mcp_live_binding(self, agent_id: str, binding: Record) -> Record:
        row = (
            self.connection.execute(
                select(
                    s.mcp_grants.c.id.label("grant_id"),
                    s.mcp_grants.c.server_id,
                    s.mcp_grants.c.tool_id,
                    s.mcp_grants.c.authority_epoch,
                    s.mcp_grants.c.catalog_fingerprint,
                    s.mcp_grants.c.tool_fingerprint,
                    s.mcp_tools.c.tool_fingerprint.label("tool_current_fingerprint"),
                    s.mcp_grants.c.read_only,
                    s.mcp_tools.c.name.label("tool_name"),
                    s.mcp_tools.c.exposed_name,
                    s.mcp_tools.c.description,
                    s.mcp_tools.c.input_schema,
                    s.mcp_servers.c.config_id,
                    s.mcp_servers.c.config_revision_id,
                    s.mcp_servers.c.fingerprint.label("server_fingerprint"),
                    s.mcp_servers.c.state.label("server_state"),
                )
                .join(
                    s.mcp_servers,
                    and_(
                        s.mcp_servers.c.id == s.mcp_grants.c.server_id,
                        s.mcp_servers.c.agent_id == s.mcp_grants.c.agent_id,
                    ),
                )
                .join(
                    s.mcp_tools,
                    and_(s.mcp_tools.c.id == s.mcp_grants.c.tool_id, s.mcp_tools.c.agent_id == s.mcp_grants.c.agent_id),
                )
                .where(
                    s.mcp_grants.c.agent_id == agent_id,
                    s.mcp_grants.c.id == binding["grant_id"],
                    s.mcp_grants.c.tool_id == binding["tool_id"],
                    s.mcp_grants.c.server_id == binding["server_id"],
                    s.mcp_grants.c.revoked_at.is_(None),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("mcp_not_authorized", "MCP tool authorization changed; inspect the current catalog")
        current = cast(Record, dict(row))
        if (
            str(current["server_state"]) != "ready"
            or cast(int, current["authority_epoch"]) != cast(int, binding["authority_epoch"])
            or str(current["server_fingerprint"]) != str(current["catalog_fingerprint"])
            or str(current["tool_current_fingerprint"]) != str(current["tool_fingerprint"])
            or str(current["exposed_name"]) != str(binding["name"])
            or bool(current["read_only"]) != (not bool(binding["writes"]))
        ):
            raise BusinessError("mcp_not_authorized", "MCP run capture no longer matches its authorization")
        return {
            **current,
            "server_id": str(current["server_id"]),
            "read_only": bool(current["read_only"]),
            "input_schema": current["input_schema"],
        }

    def mcp_run_capture(self, agent_id: str, run_id: str) -> None:
        self._one(s.runs, agent_id, run_id)
        captured = self.connection.execute(
            select(func.count())
            .select_from(s.mcp_run_tools)
            .where(s.mcp_run_tools.c.agent_id == agent_id, s.mcp_run_tools.c.run_id == run_id)
        ).scalar_one()
        if captured:
            return
        rows = [
            cast(Record, dict(row))
            for row in self.connection.execute(
                self._mcp_live_grants(agent_id).order_by(s.mcp_tools.c.exposed_name).limit(MAX_TOOLS + 1)
            ).mappings()
        ]
        if len(rows) > MAX_TOOLS:
            raise BusinessError("mcp_capacity", "Stored MCP authorizations exceed the per-Agent tool bound")
        names: set[str] = set()
        for row in rows:
            name = str(row["exposed_name"])
            if name in names:
                raise BusinessError(
                    "mcp_tool_collision", "Two MCP servers expose the same capability name; rename one server"
                )
            names.add(name)
            self._record(
                s.mcp_run_tools,
                {
                    "run_id": run_id,
                    "server_id": row["server_id"],
                    "tool_id": row["tool_id"],
                    "grant_id": row["grant_id"],
                    "authority_epoch": row["authority_epoch"],
                    "name": name,
                    "writes": not bool(row["read_only"]),
                },
                agent_id,
            )

    def mcp_run_tools(self, agent_id: str, run_id: str) -> list[Record]:
        self._one(s.runs, agent_id, run_id)
        identities = cast(
            list[str],
            list(
                self.connection.execute(
                    select(s.mcp_run_tools.c.id)
                    .where(s.mcp_run_tools.c.agent_id == agent_id, s.mcp_run_tools.c.run_id == run_id)
                    .order_by(s.mcp_run_tools.c.name)
                    .limit(MAX_TOOLS + 1)
                ).scalars()
            ),
        )
        if len(identities) > MAX_TOOLS:
            raise BusinessError("mcp_capacity", "A run exceeds its captured MCP tool bound")
        result: list[Record] = []
        for identity in identities:
            try:
                result.append(self._mcp_capture_row(agent_id, run_id, str(identity)))
            except BusinessError as exc:
                if exc.code != "mcp_not_authorized":
                    raise
        return result

    def mcp_bind_action(self, agent_id: str, run_id: str, action_id: str, spec: ToolSpec) -> None:
        if not spec.name.startswith("mcp__"):
            return
        if not spec.binding_id:
            raise BusinessError("mcp_not_authorized", "MCP capabilities require a run capture binding")
        binding = self._mcp_capture_row(agent_id, run_id, spec.binding_id)
        action = self._one(s.actions, agent_id, action_id)
        if (
            action["run_id"] != run_id
            or action["tool"] != spec.name
            or binding["name"] != spec.name
            or tool_spec(binding) != spec
        ):
            raise BusinessError("mcp_not_authorized", "MCP action does not match its run capture")
        self._record(s.mcp_action_bindings, {"action_id": action_id, "run_tool_id": spec.binding_id}, agent_id)

    def mcp_assert_action(self, agent_id: str, action_id: str) -> Record | None:
        action = self._one(s.actions, agent_id, action_id)
        binding = (
            self.connection.execute(
                select(s.mcp_action_bindings).where(
                    s.mcp_action_bindings.c.agent_id == agent_id,
                    s.mcp_action_bindings.c.action_id == action_id,
                )
            )
            .mappings()
            .first()
        )
        if binding is None:
            if str(action["tool"]).startswith("mcp__"):
                raise BusinessError("mcp_not_authorized", "MCP action has no captured authorization")
            return None
        captured = self._mcp_capture_row(agent_id, str(action["run_id"]), str(binding["run_tool_id"]))
        if captured["name"] != str(action["tool"]):
            raise BusinessError("mcp_not_authorized", "MCP action capture changed")
        return captured

    def mcp_dispatch(self, agent_id: str, run_id: str, action_id: str, name: str) -> Record:
        self.assert_context(agent_id, run_id)
        self.assert_model_authorized(agent_id, run_id)
        action, current = self._one(s.actions, agent_id, action_id), self.settings(agent_id)
        if action["policy_revision"] != current["policy_revision"] or name in cast(
            list[object], cast(Record, current["value"])["disabled_tools"]
        ):
            raise BusinessError("policy_changed", "Action policy changed before MCP dispatch")
        run = self._one(s.runs, agent_id, cast(str, action["run_id"]))
        if action["run_id"] != run_id or run["state"] != "running" or action["tool"] != name:
            raise BusinessError("mcp_not_authorized", "MCP action is not dispatchable")
        if action["writes"]:
            approval = (
                self.connection.execute(
                    select(s.approvals).where(s.approvals.c.agent_id == agent_id, s.approvals.c.action_id == action_id)
                )
                .mappings()
                .first()
            )
            if (
                approval is None
                or approval["state"] != "approved"
                or cast(str, approval["expires_at"]) <= self.clock.now()
            ):
                raise BusinessError("approval_expired", "MCP action approval expired before dispatch")
        captured = self.mcp_assert_action(agent_id, action_id)
        if captured is None:
            raise BusinessError("mcp_not_authorized", "MCP action has no captured authorization")
        return {**captured, "arguments_sha256": action["arguments_sha256"]}

    # ---------- call records ----------

    def mcp_call_begin(self, agent_id: str, run_id: str, action_id: str, binding: Record) -> Record:
        existing = (
            self.connection.execute(
                select(s.mcp_calls).where(s.mcp_calls.c.agent_id == agent_id, s.mcp_calls.c.action_id == action_id)
            )
            .mappings()
            .first()
        )
        if existing is not None:
            return cast(Record, dict(existing))
        return self._record(
            s.mcp_calls,
            {
                "server_id": binding["server_id"],
                "tool_id": binding["tool_id"],
                "run_id": run_id,
                "action_id": action_id,
                "name": binding["name"],
                "writes": binding["writes"],
                "state": "prepared",
                "error_code": "",
                "text_chars": 0,
                "truncated": False,
                "duration_ms": 0,
                "finished_at": None,
            },
            agent_id,
        )

    def mcp_call_get(self, agent_id: str, call_id: str) -> Record:
        return self._one(s.mcp_calls, agent_id, call_id)

    def mcp_call_dispatched(self, agent_id: str, call_id: str) -> Record:
        self.connection.execute(
            update(s.mcp_calls)
            .where(s.mcp_calls.c.agent_id == agent_id, s.mcp_calls.c.id == call_id, s.mcp_calls.c.state == "prepared")
            .values(state="dispatched")
        )
        return self._one(s.mcp_calls, agent_id, call_id)

    def mcp_call_finish(
        self,
        agent_id: str,
        call_id: str,
        state: str,
        error_code: str = "",
        text_chars: int = 0,
        truncated: bool = False,
        duration_ms: int = 0,
    ) -> Record:
        if state not in TERMINAL_CALL_STATES:
            raise BusinessError("mcp_call_state_invalid", "Unknown MCP call state", status=422)
        current = self._one(s.mcp_calls, agent_id, call_id)
        if current["state"] in TERMINAL_CALL_STATES:
            return current
        self.connection.execute(
            update(s.mcp_calls)
            .where(
                s.mcp_calls.c.agent_id == agent_id,
                s.mcp_calls.c.id == call_id,
                s.mcp_calls.c.state.in_(("prepared", "dispatched")),
            )
            .values(
                state=state,
                error_code=error_code[:80],
                text_chars=min(MAX_TEXT_CHARS, max(0, int(text_chars))),
                truncated=bool(truncated),
                duration_ms=min(86_400_000, max(0, int(duration_ms))),
                finished_at=self.clock.now(),
            )
        )
        finished = self._one(s.mcp_calls, agent_id, call_id)
        effect = "applied" if finished["state"] == "done" else "unknown" if finished["state"] == "unknown" else "none"
        self.audit(
            agent_id,
            "mcp.call_" + cast(str, finished["state"]),
            str(finished["action_id"]),
            {
                "server_id": finished["server_id"],
                "tool": finished["name"],
                "error_code": finished["error_code"],
                "chars": finished["text_chars"],
                "effect": effect,
            },
        )
        return finished

    def mcp_call_page(self, agent_id: str, config_id: str, after: str = "", limit: int = 50) -> Record:
        if not 1 <= limit <= 100:
            raise BusinessError("mcp_page_invalid", "MCP call page limit must be between 1 and 100", status=422)
        server = self._mcp_require_server(agent_id, config_id)
        rows = [
            cast(Record, dict(row))
            for row in self.connection.execute(
                select(
                    s.mcp_calls.c.id,
                    s.mcp_calls.c.created_at,
                    s.mcp_calls.c.name,
                    s.mcp_calls.c.writes,
                    s.mcp_calls.c.state,
                    s.mcp_calls.c.error_code,
                    s.mcp_calls.c.text_chars,
                    s.mcp_calls.c.truncated,
                    s.mcp_calls.c.duration_ms,
                    s.mcp_calls.c.finished_at,
                    s.mcp_calls.c.run_id,
                    s.mcp_calls.c.action_id,
                )
                .where(
                    s.mcp_calls.c.agent_id == agent_id,
                    s.mcp_calls.c.server_id == server["id"],
                    s.mcp_calls.c.id > after,
                )
                .order_by(s.mcp_calls.c.id)
                .limit(limit)
            ).mappings()
        ]
        return cast(
            Record,
            {"items": rows, "next_after": rows[-1]["id"] if len(rows) == limit else None},
        )

    def mcp_calls_recover(self) -> Record:
        """Startup reconciliation: a prepared call never left; a dispatched call may have run."""
        now = self.clock.now()
        prepared = self.connection.execute(
            update(s.mcp_calls)
            .where(s.mcp_calls.c.state == "prepared")
            .values(state="failed", error_code="mcp_restart_interrupted", finished_at=now)
        ).rowcount
        dispatched = self.connection.execute(
            update(s.mcp_calls)
            .where(s.mcp_calls.c.state == "dispatched")
            .values(state="unknown", error_code="mcp_restart_unknown", finished_at=now)
        ).rowcount
        servers = self.connection.execute(
            update(s.mcp_servers)
            .where(s.mcp_servers.c.state.in_(("starting", "ready")))
            .values(state="idle", connected_at=None, error_code="mcp_restart_disconnected")
        ).rowcount
        return {
            "calls_failed": prepared,
            "calls_unknown": dispatched,
            "servers_idle": servers,
        }


__all__ = ["MCPRepositories"]
