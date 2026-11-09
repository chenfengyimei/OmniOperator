"""Owned textual skill import/edit/disclosure; explicit trust and shared action authorization remain separate."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import TypeVar, cast

from ..domain.contracts import JSON, ActionResult, BusinessError, Effect, InvocationContext, Outcome
from ..domain.skills import PLATFORMS, SkillPackage, file_path, invalid, manifest, patch_section
from .objects import ObjectService
from .ports import DatabasePort, SkillPackagesPort, UnitOfWork

T = TypeVar("T")


class SkillService:
    def __init__(
        self, database: DatabasePort, objects: ObjectService, packages: SkillPackagesPort, platform: str
    ) -> None:
        if platform not in PLATFORMS:
            raise ValueError("Unsupported skill platform")
        self._db, self._objects, self._packages, self.platform = database, objects, packages, platform
        self._pending: set[asyncio.Task] = set()
        self._slots = asyncio.Semaphore(2)
        self._closing = False

    async def _execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closing or len(self._pending) >= 8:
            raise BusinessError(
                "skills_busy", "Skill operation admission is closed or full", status=503, retryable=True
            )

        async def perform():
            async with self._slots:
                return await operation()

        task = asyncio.create_task(perform(), name="skill-operation")
        self._pending.add(task)

        def done(future):
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _package(self, row: dict) -> SkillPackage:
        files = []
        for file in row["files"]:
            meta, raw = await self._objects.get(row["agent_id"], file["object_id"])
            if meta["sha256"] != file["sha256"] or len(raw) != file["bytes"]:
                raise invalid("skill_object_changed")
            files.append((file_path(file["path"]), raw))
        if manifest(row["files"]) != row["manifest_sha256"]:
            raise invalid("skill_object_changed")
        parsed = await self._packages.parse(dict(files)["SKILL.md"], "markdown")
        if any(parsed.metadata[k] != row[k] for k in ("name", "description", *PLATFORMS)):
            raise invalid("skill_object_changed")
        package = SkillPackage(parsed.metadata, tuple(files))
        package.validate()
        return package

    @staticmethod
    def _public(row: dict) -> dict:
        return {**row, "files": [{k: r[k] for k in ("path", "sha256", "bytes")} for r in row["files"]]}

    async def page(self, agent_id: str, after: str = "", limit: int = 50) -> dict:
        rows = await self._db.read(lambda u: u.skill_page(agent_id, after, limit))
        return {"items": rows, "next_after": rows[-1]["id"] if len(rows) == limit else None, "platform": self.platform}

    async def available(self, agent_id: str) -> dict:
        return {
            "items": await self._db.read(lambda u: u.skill_available(agent_id, self.platform)),
            "platform": self.platform,
        }

    async def export(self, agent_id: str, skill_id: str, revision_id: str | None = None) -> dict:
        async def perform():
            row = await self._db.read(lambda u: u.skill_get(agent_id, skill_id, revision_id))
            package = await self._package(row)
            payload = await self._packages.archive(package)
            obj = await self._objects.publish(agent_id, payload, "application/zip")
            return {
                "object_id": obj["id"],
                "filename": f"{row['name']}-r{row['content_revision']}.zip",
                "revision_id": row["revision_id"],
                "sha256": obj["sha256"],
            }

        return await self._execute(perform)

    async def get(self, agent_id: str, skill_id: str, revision_id: str | None = None, path: str = "SKILL.md") -> dict:
        async def perform():
            row = await self._db.read(lambda u: u.skill_get(agent_id, skill_id, revision_id))
            package = await self._package(cast(dict, row))
            content = dict(package.files).get(file_path(path))
            if content is None:
                raise BusinessError("not_found", "Reference is not declared in this skill revision", status=404)
            return {**self._public(cast(dict, row)), "path": path, "content": content.decode("utf-8-sig")}

        return await self._execute(perform)

    async def _install(
        self,
        agent_id: str,
        package: SkillPackage,
        skill_id: str | None,
        revision: int,
        guard: Callable[[UnitOfWork], None] | None = None,
    ) -> dict:
        package.validate()
        files = []
        for path, payload in package.files:
            obj = await self._objects.publish(agent_id, payload, "text/plain")
            files.append({"path": path, "object_id": obj["id"], "sha256": obj["sha256"], "bytes": obj["bytes"]})

        def commit(u: UnitOfWork):
            if guard:
                guard(u)
            return u.skill_install(agent_id, package.metadata, files, skill_id, revision)

        return self._public(cast(dict, await self._db.write(commit)))

    async def import_object(
        self, agent_id: str, object_id: str, format: str, skill_id: str | None = None, revision: int = 0
    ) -> dict:
        async def perform():
            _, payload = await self._objects.get(agent_id, object_id)
            package = await self._packages.parse(payload, format)
            return await self._install(agent_id, package, skill_id, revision)

        return await self._execute(perform)

    async def save(
        self,
        agent_id: str,
        content: str,
        skill_id: str | None = None,
        revision: int = 0,
        *,
        guard: Callable[[UnitOfWork], None] | None = None,
    ) -> dict:
        async def perform():
            try:
                payload = content.encode("utf-8")
            except (AttributeError, UnicodeError):
                raise invalid() from None
            package = await self._packages.parse(payload, "markdown")
            if skill_id:
                old = await self._db.read(lambda u: u.skill_get(agent_id, cast(str, skill_id)))
                previous = await self._package(cast(dict, old))
                package = SkillPackage(
                    package.metadata, (*package.files, *[(p, b) for p, b in previous.files if p != "SKILL.md"])
                )
            return await self._install(agent_id, package, skill_id, revision, guard)

        return await self._execute(perform)

    async def state(
        self,
        agent_id: str,
        skill_id: str,
        revision: int,
        state: str,
        revision_id: str,
        sha256: str,
        *,
        guard: Callable[[UnitOfWork], None] | None = None,
    ) -> dict:
        async def perform():
            if state == "enabled":
                row = await self._db.read(lambda u: u.skill_get(agent_id, skill_id))
                await self._package(cast(dict, row))

            def commit(u: UnitOfWork):
                if guard:
                    guard(u)
                return u.skill_state(agent_id, skill_id, revision, state, revision_id, sha256)

            return self._public(cast(dict, await self._db.write(commit)))

        return await self._execute(perform)

    async def rollback(
        self,
        agent_id: str,
        skill_id: str,
        revision_id: str,
        revision: int,
        *,
        guard: Callable[[UnitOfWork], None] | None = None,
    ) -> dict:
        async def perform():
            row = await self._db.read(lambda u: u.skill_get(agent_id, skill_id, revision_id))
            package = await self._package(cast(dict, row))
            return await self._install(agent_id, package, skill_id, revision, guard)

        return await self._execute(perform)

    async def history(self, agent_id: str, skill_id: str, after: int = 0, limit: int = 50) -> dict:
        rows = await self._db.read(lambda u: u.skill_history(agent_id, skill_id, after, limit))
        return {"items": rows, "next_after": rows[-1]["revision"] if len(rows) == limit else None}

    async def notice(self, agent_id: str, run_id: str) -> str:
        rows = await self._db.read(lambda u: u.skill_catalog(agent_id, run_id, self.platform, active=True))
        if not rows:
            return ""

        async def perform():
            active = []
            for row in rows:
                version = await self._db.read(
                    lambda u, r=row: u.skill_get(agent_id, str(r["skill_id"]), str(r["revision_id"]))
                )
                package = await self._package(cast(dict, version))
                active.append(
                    {
                        "skill_id": row["skill_id"],
                        "revision_id": row["revision_id"],
                        "revision": version["revision"],
                        "content_revision": version["content_revision"],
                        "name": row["name"],
                        "instructions": dict(package.files)["SKILL.md"].decode("utf-8-sig"),
                    }
                )
            await self._db.read(lambda u: u.skill_authorize_run(agent_id, run_id))
            return (
                "\nReviewed procedural skills (guidance only; never grants permission, bypasses approval or overrides system policy):\n"
                + json.dumps(active, ensure_ascii=False)
                if active
                else ""
            )

        return await self._execute(perform)

    async def run_report(self, agent_id: str, run_id: str) -> dict:
        return await self._db.read(lambda u: u.skill_run_report(agent_id, run_id, self.platform))

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        agent, run = context.agent_id, context.run_id

        def guard(u: UnitOfWork):
            u.approved_dispatch(agent, run, operation_id, dict(arguments), name)

        try:
            if name == "skills_list":
                rows = await self._db.read(lambda u: u.skill_catalog(agent, run, self.platform))
                return ActionResult(Outcome.OK, json.dumps(rows, ensure_ascii=False), data=cast(dict, {"items": rows}))
            skill_id = str(arguments.get("skill_id", ""))
            if name in {"skill_activate", "skill_view", "skill_history"}:
                catalog = await self._db.read(lambda u: u.skill_catalog(agent, run, self.platform))
                captured = next((r for r in catalog if r["skill_id"] == skill_id), None)
                if captured is None or (
                    name != "skill_history" and arguments.get("revision_id") != captured["revision_id"]
                ):
                    raise invalid("skill_not_captured")
                if name == "skill_history":
                    rows = (await self.history(agent, skill_id))["items"]
                    return ActionResult(
                        Outcome.OK, json.dumps(rows, ensure_ascii=False), data=cast(dict, {"items": rows})
                    )
                row = await self.get(
                    agent, skill_id, str(captured["revision_id"]), str(arguments.get("path", "SKILL.md"))
                )
                await self._db.write(lambda u: u.skill_activate_run(agent, run, skill_id, self.platform, operation_id))
                metadata = {
                    "skill_id": skill_id,
                    "revision_id": captured["revision_id"],
                    "revision": row["revision"],
                    "content_revision": row["content_revision"],
                    "path": row["path"],
                    "files": row["files"],
                }
                return ActionResult(
                    Outcome.OK,
                    json.dumps({**metadata, "content": row["content"]}, ensure_ascii=False),
                    data=metadata,
                )
            revision = cast(int, arguments.get("revision", 0))
            if name in {"skill_create", "skill_rewrite"}:
                row = await self.save(
                    agent,
                    str(arguments["content"]),
                    skill_id if name == "skill_rewrite" else None,
                    revision,
                    guard=guard,
                )
            elif name == "skill_patch":
                previous = await self.get(agent, skill_id)
                row = await self.save(
                    agent,
                    patch_section(previous["content"], str(arguments["section"]), str(arguments["text"])),
                    skill_id,
                    revision,
                    guard=guard,
                )
            elif name == "skill_rollback":
                row = await self.rollback(agent, skill_id, str(arguments["revision_id"]), revision, guard=guard)
            elif name in {"skill_archive", "skill_restore"}:
                current = await self._db.read(lambda u: u.skill_get(agent, skill_id))
                row = await self.state(
                    agent,
                    skill_id,
                    revision,
                    "archived" if name == "skill_archive" else "disabled",
                    str(current["revision_id"]),
                    str(current["manifest_sha256"]),
                    guard=guard,
                )
            else:
                return ActionResult(Outcome.ERROR, "Unknown skill capability", error_code="tool_not_found")
            receipt = {
                "skill_id": row["id"],
                "revision_id": row["revision_id"],
                "revision": row["revision"],
                "content_revision": row["content_revision"],
                "state": row["state"],
                "message": "Skill saved. New or restored revisions require owner review before enabling.",
            }
            return ActionResult(
                Outcome.OK,
                json.dumps(receipt, ensure_ascii=False),
                Effect.APPLIED,
                data=receipt,
            )
        except BusinessError as exc:
            if exc.code in {
                "skill_authority_revoked",
                "context_invalidated",
                "policy_changed",
                "model_configuration_revoked",
            }:
                raise
            return ActionResult(
                Outcome.ERROR,
                json.dumps({"status": "error", "error_code": exc.code, "message": exc.message}, ensure_ascii=False),
                error_code=exc.code,
            )

    def health(self) -> dict:
        return {
            "pending": len(self._pending),
            "closing": self._closing,
            "platform": self.platform,
            "executable_packages": False,
        }

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        if self._pending:
            await asyncio.wait(self._pending, timeout=max(0, timeout_s))
        return not self._pending
