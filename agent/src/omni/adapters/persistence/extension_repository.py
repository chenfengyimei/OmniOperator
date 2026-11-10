"""Immutable trusted-plugin versions, scoped grants and execution bindings; no plugin code or file I/O."""

from __future__ import annotations

import re

from sqlalchemy import and_, func, select, update

from ...domain.contracts import BusinessError, ToolSpec
from ...domain.extensions import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_PACKAGE_BYTES,
    MAX_TOOLS,
    PROTOCOL,
    catalog,
    catalog_fingerprint,
    page,
    parse_authorization,
    parse_install,
    parse_manifest,
    positive_revision,
    request_fingerprint,
    tool_spec,
)
from . import schema as s
from .repository_base import RepositoryBase

MAX_SLOTS = 16
MAX_VERSIONS = 128
MAX_ENABLED_TOOLS = 64
MAX_ARCHIVE_BYTES = 9 * 1024 * 1024


class ExtensionRepositories(RepositoryBase):
    def _extension_agent(self, agent_id: str) -> None:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent space does not exist", status=404)

    def _extension_epoch(self) -> str:
        return self.connection.execute(select(s.history_identity.c.epoch)).scalar_one()

    @staticmethod
    def _extension_denied() -> BusinessError:
        return BusinessError("extension_not_authorized", "Plugin authorization changed; use a newly authorized run")

    def _extension_version(self, slot: dict, revision_id: str | None = None) -> dict:
        version = self._one(s.extension_revisions, slot["agent_id"], revision_id or slot["current_revision_id"])
        if version["extension_id"] != slot["id"]:
            raise self._extension_denied()
        return version

    def _extension_detail(self, slot: dict, revision_id: str | None = None) -> dict:
        version, epoch = self._extension_version(slot, revision_id), self._extension_epoch()
        tools = [
            dict(row)
            for row in self.connection.execute(
                select(s.extension_tools)
                .where(
                    s.extension_tools.c.agent_id == slot["agent_id"],
                    s.extension_tools.c.extension_id == slot["id"],
                    s.extension_tools.c.extension_revision_id == version["id"],
                )
                .order_by(s.extension_tools.c.name)
                .limit(MAX_TOOLS + 1)
            ).mappings()
        ]
        grants = {
            row["tool_id"]: dict(row)
            for row in self.connection.execute(
                select(s.grants)
                .where(
                    s.grants.c.agent_id == slot["agent_id"],
                    s.grants.c.extension_id == slot["id"],
                    s.grants.c.extension_revision_id == version["id"],
                    s.grants.c.authority_epoch == slot["authority_epoch"],
                    s.grants.c.history_epoch == epoch,
                    s.grants.c.revoked_at.is_(None),
                )
                .limit(MAX_TOOLS + 1)
            ).mappings()
        }
        current = slot["current_revision_id"] == version["id"]
        enabled = current and slot["enabled"] and slot["state"] == "enabled"
        return {
            **slot,
            "extension_id": slot["id"],
            "extension_revision_id": version["id"],
            "version": version["version"],
            "plugin_key": version["manifest"]["id"],
            "protocol": version["protocol"],
            "package_sha256": version["package_sha256"],
            "catalog_sha256": version["catalog_sha256"],
            "object_id": version["object_id"],
            "manifest": version["manifest"],
            "files": version["files"],
            "entry": version["entry"],
            "file_count": version["file_count"],
            "total_bytes": version["total_bytes"],
            "history_epoch": epoch,
            "is_current_version": current,
            "tools": [
                {
                    **tool,
                    "granted": bool(enabled and tool["id"] in grants),
                    "grant_id": grants[tool["id"]]["id"] if enabled and tool["id"] in grants else None,
                }
                for tool in tools
            ],
        }

    def extension_get(self, agent_id: str, extension_id: str) -> dict:
        return self._extension_detail(self._one(s.extensions, agent_id, extension_id))

    def extension_page(self, agent_id: str, after: int = 0, limit: int = 20) -> list[dict]:
        page(after, limit)
        self._extension_agent(agent_id)
        return [
            self._extension_detail(dict(row))
            for row in self.connection.execute(
                select(s.extensions)
                .where(
                    s.extensions.c.agent_id == agent_id,
                    s.extensions.c.sequence > after,
                )
                .order_by(s.extensions.c.sequence)
                .limit(limit)
            ).mappings()
        ]

    def extension_install_replay(self, agent_id: str, key: str, request_sha: str) -> dict | None:
        self._extension_agent(agent_id)
        previous = (
            self.connection.execute(
                select(s.extension_install_requests).where(
                    s.extension_install_requests.c.agent_id == agent_id,
                    s.extension_install_requests.c.request_key == key,
                )
            )
            .mappings()
            .first()
        )
        if previous is None:
            return None
        if previous["request_sha256"] != request_sha:
            raise BusinessError("idempotency_conflict", "This key already identifies another plugin installation")
        slot = self._one(s.extensions, agent_id, previous["extension_id"])
        return {
            **self._extension_detail(slot, previous["extension_revision_id"]),
            "replayed": True,
            "cancel_operations": [],
            "retired_authority_epoch": 0,
        }

    def _extension_change(self, slot: dict, **values) -> dict:
        changed = self.connection.execute(
            update(s.extensions)
            .where(
                s.extensions.c.agent_id == slot["agent_id"],
                s.extensions.c.id == slot["id"],
                s.extensions.c.revision == slot["revision"],
            )
            .values(
                revision=slot["revision"] + 1,
                authority_epoch=slot["authority_epoch"] + 1,
                updated_at=self.clock.now(),
                **values,
            )
        ).rowcount
        if changed != 1:
            raise BusinessError("revision_conflict", "Plugin changed; reload before editing")
        return self._one(s.extensions, slot["agent_id"], slot["id"])

    def _extension_revoke(self, slot: dict) -> list[str]:
        now, agent = self.clock.now(), slot["agent_id"]
        self.connection.execute(
            update(s.grants)
            .where(s.grants.c.agent_id == agent, s.grants.c.extension_id == slot["id"], s.grants.c.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        actions = (
            select(s.action_extension_bindings.c.action_id)
            .join(
                s.run_extension_tools,
                and_(
                    s.run_extension_tools.c.id == s.action_extension_bindings.c.run_tool_id,
                    s.run_extension_tools.c.agent_id == s.action_extension_bindings.c.agent_id,
                ),
            )
            .where(s.action_extension_bindings.c.agent_id == agent, s.run_extension_tools.c.extension_id == slot["id"])
        )
        pending = [
            dict(row)
            for row in self.connection.execute(
                select(s.actions.c.id, s.actions.c.run_id).where(
                    s.actions.c.agent_id == agent,
                    s.actions.c.id.in_(actions),
                    s.actions.c.state.in_(["pending", "approved"]),
                )
            ).mappings()
        ]
        executing = list(
            self.connection.execute(
                select(s.actions.c.id).where(
                    s.actions.c.agent_id == agent, s.actions.c.id.in_(actions), s.actions.c.state == "executing"
                )
            ).scalars()
        )
        if pending:
            ids = [row["id"] for row in pending]
            self.connection.execute(
                update(s.approvals)
                .where(
                    s.approvals.c.agent_id == agent,
                    s.approvals.c.action_id.in_(ids),
                    s.approvals.c.state.in_(["pending", "approved"]),
                )
                .values(state="cancelled", resolved_at=now)
            )
            self.connection.execute(
                update(s.actions)
                .where(
                    s.actions.c.agent_id == agent,
                    s.actions.c.id.in_(ids),
                    s.actions.c.state.in_(["pending", "approved"]),
                )
                .values(state="cancelled", effect="none", ended_at=now)
            )
        return executing

    def extension_install(self, agent_id: str, request: dict, captured: dict, object_id: str) -> dict:
        normalized = parse_install(request)
        sha = request_fingerprint(normalized)
        replay = self.extension_install_replay(agent_id, normalized["key"], sha)
        if replay is not None:
            return replay
        manifest = parse_manifest(captured["manifest"])
        tools = catalog(manifest)
        # Captured metadata is produced by the injected file adapter, not accepted from the public install body.
        files = captured["files"]
        try:
            if not isinstance(files, (list, tuple)) or not 1 <= len(files) <= MAX_FILES:
                raise ValueError
            paths = []
            for item in files:
                if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}:
                    raise ValueError
                path = item["path"]
                if (
                    not isinstance(path, str)
                    or not 1 <= len(path) <= 240
                    or "\\" in path
                    or ":" in path
                    or any(part in {"", ".", ".."} for part in path.split("/"))
                    or type(item["bytes"]) is not int
                    or not 0 <= item["bytes"] <= MAX_FILE_BYTES
                    or not isinstance(item["sha256"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
                ):
                    raise ValueError
                paths.append(path)
            if paths != sorted(set(paths)) or "extension.json" not in paths or manifest["entry"] not in paths:
                raise ValueError
        except (ValueError, TypeError, KeyError) as exc:
            raise BusinessError(
                "extension_invalid", "Plugin file manifest exceeds its bounded contract", status=422
            ) from exc
        total = sum(item["bytes"] for item in files)
        if total > MAX_PACKAGE_BYTES or total < 1 or catalog_fingerprint(manifest) != captured["catalog_sha256"]:
            raise BusinessError("extension_integrity_failed", "Captured plugin metadata does not match its identity")
        package = self._one(s.objects, agent_id, object_id)
        if package["sha256"] != captured["package_sha256"] or not 0 < package["bytes"] <= MAX_ARCHIVE_BYTES:
            raise BusinessError(
                "extension_integrity_failed", "Plugin archive object does not match the captured package"
            )
        if normalized["extension_id"]:
            slot = self._one(s.extensions, agent_id, normalized["extension_id"])
            if slot["revision"] != normalized["revision"]:
                raise BusinessError("revision_conflict", "Plugin changed before installation")
            if slot["name"] != manifest["id"]:
                raise BusinessError("extension_identity_conflict", "A plugin update cannot change its package identity")
        else:
            if self.connection.execute(
                select(s.extensions.c.id).where(
                    s.extensions.c.agent_id == agent_id, s.extensions.c.name == manifest["id"]
                )
            ).first():
                raise BusinessError("extension_name_conflict", "This plugin identity is already installed")
            if (
                self.connection.execute(
                    select(func.count()).select_from(s.extensions).where(s.extensions.c.agent_id == agent_id)
                ).scalar_one()
                >= MAX_SLOTS
            ):
                raise BusinessError(
                    "extension_capacity", "An Agent can have at most sixteen installed plugins", status=422
                )
            slot = None
        if (
            self.connection.execute(
                select(func.count())
                .select_from(s.extension_revisions)
                .where(s.extension_revisions.c.agent_id == agent_id)
            ).scalar_one()
            >= MAX_VERSIONS
        ):
            raise BusinessError(
                "extension_capacity", "An Agent can retain at most 128 immutable plugin versions", status=422
            )
        version_id = self.ids.new()
        if slot is None:
            slot = self._record(
                s.extensions,
                {
                    "name": manifest["id"],
                    "revision": 1,
                    "authority_epoch": 1,
                    "enabled": False,
                    "state": "disabled",
                    "current_revision_id": version_id,
                    "updated_at": self.clock.now(),
                    "error_code": "",
                },
                agent_id,
            )
            cancelled, ordinal, retired = [], 1, 0
        else:
            retired = slot["authority_epoch"]
            cancelled = self._extension_revoke(slot)
            ordinal = (
                self.connection.execute(
                    select(func.coalesce(func.max(s.extension_revisions.c.ordinal), 0)).where(
                        s.extension_revisions.c.agent_id == agent_id, s.extension_revisions.c.extension_id == slot["id"]
                    )
                ).scalar_one()
                + 1
            )
            slot = self._extension_change(
                slot, enabled=False, state="disabled", current_revision_id=version_id, error_code=""
            )
        self._record(
            s.extension_revisions,
            {
                "id": version_id,
                "extension_id": slot["id"],
                "ordinal": ordinal,
                "version": manifest["version"],
                "protocol": PROTOCOL,
                "object_id": object_id,
                "package_sha256": captured["package_sha256"],
                "catalog_sha256": captured["catalog_sha256"],
                "manifest": manifest,
                "files": list(files),
                "entry": manifest["entry"],
                "file_count": len(files),
                "total_bytes": total,
            },
            agent_id,
        )
        for tool in tools:
            self._record(
                s.extension_tools, {"extension_id": slot["id"], "extension_revision_id": version_id, **tool}, agent_id
            )
        self._record(
            s.extension_install_requests,
            {
                "request_key": normalized["key"],
                "request_sha256": sha,
                "extension_id": slot["id"],
                "extension_revision_id": version_id,
            },
            agent_id,
        )
        self.audit(
            agent_id,
            "extension.installed",
            slot["id"],
            {
                "extension_revision_id": version_id,
                "package_sha256": captured["package_sha256"],
                "catalog_sha256": captured["catalog_sha256"],
                "tool_count": len(tools),
            },
        )
        return {
            **self.extension_get(agent_id, slot["id"]),
            "replayed": False,
            "cancel_operations": cancelled,
            "retired_authority_epoch": retired,
        }

    def extension_authorize(self, agent_id: str, extension_id: str, request: dict) -> dict:
        authorization = parse_authorization(request)
        slot = self._one(s.extensions, agent_id, extension_id)
        if slot["revision"] != authorization["revision"]:
            raise BusinessError("revision_conflict", "Plugin changed; review the installed package again")
        version = self._extension_version(slot)
        epoch = self._extension_epoch()
        if (
            authorization["package_sha256"] != version["package_sha256"]
            or authorization["catalog_sha256"] != version["catalog_sha256"]
            or authorization["history_epoch"] != epoch
        ):
            raise self._extension_denied()
        if slot["state"] == "drifted":
            raise BusinessError("extension_drifted", "Reinstall and verify the package before granting it again")
        tools = [
            dict(row)
            for row in self.connection.execute(
                select(s.extension_tools)
                .where(
                    s.extension_tools.c.agent_id == agent_id,
                    s.extension_tools.c.extension_revision_id == version["id"],
                    s.extension_tools.c.extension_id == extension_id,
                )
                .limit(MAX_TOOLS + 1)
            ).mappings()
        ]
        selected = {tool["name"]: tool for tool in tools if tool["name"] in authorization["tools"]}
        if len(selected) != len(authorization["tools"]):
            raise BusinessError(
                "extension_invalid", "Authorization must select tools from the exact current catalog", status=422
            )
        enabled = self.connection.execute(
            select(func.count())
            .select_from(
                s.grants.join(
                    s.extensions,
                    and_(s.extensions.c.id == s.grants.c.extension_id, s.extensions.c.agent_id == s.grants.c.agent_id),
                )
            )
            .where(
                s.grants.c.agent_id == agent_id,
                s.grants.c.extension_id != extension_id,
                s.grants.c.revoked_at.is_(None),
                s.grants.c.authority_epoch == s.extensions.c.authority_epoch,
                s.grants.c.history_epoch == epoch,
                s.grants.c.extension_revision_id == s.extensions.c.current_revision_id,
                s.extensions.c.enabled.is_(True),
                s.extensions.c.state == "enabled",
            )
        ).scalar_one()
        if enabled + len(selected) > MAX_ENABLED_TOOLS:
            raise BusinessError("extension_capacity", "An Agent can enable at most 64 plugin tools", status=422)
        retired = slot["authority_epoch"]
        cancelled = self._extension_revoke(slot)
        slot = self._extension_change(slot, enabled=True, state="enabled", error_code="")
        audit_id = self.audit(
            agent_id,
            "extension.authorized",
            extension_id,
            {
                "extension_revision_id": version["id"],
                "authority_epoch": slot["authority_epoch"],
                "history_epoch": epoch,
                "tool_count": len(selected),
                "package_sha256": version["package_sha256"],
                "catalog_sha256": version["catalog_sha256"],
            },
        )
        for tool in selected.values():
            self._record(
                s.grants,
                {
                    "extension_id": extension_id,
                    "extension_revision_id": version["id"],
                    "tool_id": tool["id"],
                    "authority_epoch": slot["authority_epoch"],
                    "history_epoch": epoch,
                    "revoked_at": None,
                    "review_audit_id": audit_id,
                },
                agent_id,
            )
        return {
            **self.extension_get(agent_id, extension_id),
            "cancel_operations": cancelled,
            "retired_authority_epoch": retired,
        }

    def extension_disable(self, agent_id: str, extension_id: str, revision: int) -> dict:
        positive_revision(revision)
        slot = self._one(s.extensions, agent_id, extension_id)
        if slot["revision"] != revision:
            raise BusinessError("revision_conflict", "Plugin changed; reload before disabling")
        if not slot["enabled"] and slot["state"] == "disabled":
            return {**self.extension_get(agent_id, extension_id), "cancel_operations": [], "retired_authority_epoch": 0}
        retired = slot["authority_epoch"]
        cancelled = self._extension_revoke(slot)
        slot = self._extension_change(slot, enabled=False, state="disabled", error_code="")
        self.audit(agent_id, "extension.disabled", extension_id, {"authority_epoch": slot["authority_epoch"]})
        return {
            **self.extension_get(agent_id, extension_id),
            "cancel_operations": cancelled,
            "retired_authority_epoch": retired,
        }

    def extension_mark_drift(self, agent_id: str, extension_id: str, revision_id: str, code: str) -> dict:
        slot = self._one(s.extensions, agent_id, extension_id)
        if slot["current_revision_id"] != revision_id or slot["state"] == "drifted":
            return {**self.extension_get(agent_id, extension_id), "cancel_operations": [], "retired_authority_epoch": 0}
        retired = slot["authority_epoch"]
        cancelled = self._extension_revoke(slot)
        error = (
            code
            if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code)
            else "extension_integrity_failed"
        )
        slot = self._extension_change(slot, enabled=False, state="drifted", error_code=error)
        self.audit(
            agent_id,
            "extension.drifted",
            extension_id,
            {"extension_revision_id": revision_id, "authority_epoch": slot["authority_epoch"], "error_code": error},
        )
        return {
            **self.extension_get(agent_id, extension_id),
            "cancel_operations": cancelled,
            "retired_authority_epoch": retired,
        }

    def _extension_live_grants(self, agent_id: str):
        return (
            select(s.grants)
            .join(
                s.extensions,
                and_(s.extensions.c.id == s.grants.c.extension_id, s.extensions.c.agent_id == s.grants.c.agent_id),
            )
            .where(
                s.grants.c.agent_id == agent_id,
                s.grants.c.revoked_at.is_(None),
                s.grants.c.history_epoch == self._extension_epoch(),
                s.grants.c.authority_epoch == s.extensions.c.authority_epoch,
                s.grants.c.extension_revision_id == s.extensions.c.current_revision_id,
                s.extensions.c.enabled.is_(True),
                s.extensions.c.state == "enabled",
            )
        )

    def extension_capture_run(self, agent_id: str, run_id: str) -> None:
        run = self._one(s.runs, agent_id, run_id)
        if run["extension_capture_epoch"] is not None:
            return
        epoch = self._extension_epoch()
        if run["state"] != "queued":
            raise self._extension_denied()
        grants = [
            dict(row)
            for row in self.connection.execute(
                self._extension_live_grants(agent_id).order_by(s.grants.c.id).limit(MAX_ENABLED_TOOLS + 1)
            ).mappings()
        ]
        if len(grants) > MAX_ENABLED_TOOLS:
            raise BusinessError("extension_capacity", "Stored plugin grants exceed the per-Agent tool bound")
        for grant in grants:
            tool = self._one(s.extension_tools, agent_id, grant["tool_id"])
            self._record(
                s.run_extension_tools,
                {
                    "run_id": run_id,
                    "extension_id": grant["extension_id"],
                    "extension_revision_id": grant["extension_revision_id"],
                    "tool_id": grant["tool_id"],
                    "grant_id": grant["id"],
                    "authority_epoch": grant["authority_epoch"],
                    "history_epoch": epoch,
                    "name": tool["name"],
                },
                agent_id,
            )
        self.connection.execute(
            update(s.runs)
            .where(s.runs.c.agent_id == agent_id, s.runs.c.id == run_id, s.runs.c.extension_capture_epoch.is_(None))
            .values(extension_capture_epoch=epoch)
        )

    def _extension_binding(self, agent_id: str, binding_id: str) -> dict:
        binding = self._one(s.run_extension_tools, agent_id, binding_id)
        slot = self._one(s.extensions, agent_id, binding["extension_id"])
        version = self._extension_version(slot, binding["extension_revision_id"])
        tool = self._one(s.extension_tools, agent_id, binding["tool_id"])
        grant = self._one(s.grants, agent_id, binding["grant_id"])
        run = self._one(s.runs, agent_id, binding["run_id"])
        epoch = self._extension_epoch()
        if (
            not slot["enabled"]
            or slot["state"] != "enabled"
            or slot["current_revision_id"] != version["id"]
            or slot["authority_epoch"] != binding["authority_epoch"]
            or binding["history_epoch"] != epoch
            or run["extension_capture_epoch"] != epoch
            or grant["revoked_at"] is not None
            or grant["authority_epoch"] != binding["authority_epoch"]
            or grant["history_epoch"] != epoch
            or grant["tool_id"] != tool["id"]
            or grant["extension_id"] != slot["id"]
            or grant["extension_revision_id"] != version["id"]
            or tool["extension_id"] != slot["id"]
            or tool["extension_revision_id"] != version["id"]
            or binding["name"] != tool["name"]
            or tool["writes"] is not True
        ):
            raise self._extension_denied()
        return {
            **tool,
            "binding_id": binding["id"],
            "run_id": binding["run_id"],
            "extension_id": slot["id"],
            "extension_revision_id": version["id"],
            "grant_id": grant["id"],
            "authority_epoch": binding["authority_epoch"],
            "history_epoch": epoch,
            "version": version["version"],
            "protocol": version["protocol"],
            "package_sha256": version["package_sha256"],
            "catalog_sha256": version["catalog_sha256"],
            "object_id": version["object_id"],
            "manifest": version["manifest"],
            "files": version["files"],
            "entry": version["entry"],
            "file_count": version["file_count"],
            "total_bytes": version["total_bytes"],
            "plugin_key": version["manifest"]["id"],
        }

    def extension_run_tools(self, agent_id: str, run_id: str) -> list[dict]:
        self._one(s.runs, agent_id, run_id)
        bindings = list(
            self.connection.execute(
                select(s.run_extension_tools.c.id)
                .where(
                    s.run_extension_tools.c.agent_id == agent_id,
                    s.run_extension_tools.c.run_id == run_id,
                )
                .order_by(s.run_extension_tools.c.name)
                .limit(MAX_ENABLED_TOOLS + 1)
            ).scalars()
        )
        if len(bindings) > MAX_ENABLED_TOOLS:
            raise BusinessError("extension_capacity", "A run exceeds its captured plugin-tool bound")
        result = []
        for identity in bindings:
            try:
                result.append(self._extension_binding(agent_id, identity))
            except BusinessError as exc:
                if exc.code != "extension_not_authorized":
                    raise
        return result

    def extension_bind_action(self, agent_id: str, run_id: str, action_id: str, spec: ToolSpec) -> None:
        if spec.name.startswith("mcp__"):
            # MCP run captures are bound by their own repository path.
            return
        if not spec.name.startswith("ext__"):
            if spec.binding_id:
                raise self._extension_denied()
            return
        if not spec.binding_id:
            raise self._extension_denied()
        binding = self._extension_binding(agent_id, spec.binding_id)
        action = self._one(s.actions, agent_id, action_id)
        if (
            binding["run_id"] != run_id
            or action["run_id"] != run_id
            or action["tool"] != spec.name
            or action["writes"] is not True
            or tool_spec(binding) != spec
        ):
            raise self._extension_denied()
        self._record(s.action_extension_bindings, {"action_id": action_id, "run_tool_id": spec.binding_id}, agent_id)

    def extension_assert_action(self, agent_id: str, action_id: str) -> dict | None:
        action = self._one(s.actions, agent_id, action_id)
        binding = (
            self.connection.execute(
                select(s.action_extension_bindings).where(
                    s.action_extension_bindings.c.agent_id == agent_id,
                    s.action_extension_bindings.c.action_id == action_id,
                )
            )
            .mappings()
            .first()
        )
        if binding is None:
            if action["tool"].startswith("ext__"):
                raise self._extension_denied()
            return None
        captured = self._extension_binding(agent_id, binding["run_tool_id"])
        if captured["run_id"] != action["run_id"] or captured["name"] != action["tool"] or action["writes"] is not True:
            raise self._extension_denied()
        return captured

    def _extension_dispatch_binding(self, agent_id: str, run_id: str, action_id: str, name: str) -> dict:
        action = self._one(s.actions, agent_id, action_id)
        run = self._one(s.runs, agent_id, run_id)
        if (
            action["run_id"] != run_id
            or action["state"] != "executing"
            or run["state"] != "running"
            or action["tool"] != name
        ):
            raise self._extension_denied()
        captured = self.extension_assert_action(agent_id, action_id)
        if captured is None:
            raise self._extension_denied()
        return captured
