"""Own one bounded post-turn reviewer per finished run; proposals stay pending until the owner decides.

The reviewer replays a finished turn through the auxiliary model channel under an independent call
budget, may only READ through a whitelist, and STAGES every memory/skill write intent as a pending
proposal. Applying a proposal still goes through the evidence-anchored memory chain or the
review-before-enable skill chain, so a cited source is never itself an authorization.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import TypeVar, cast

from ..domain.background_review import (
    MAX_CALLS,
    PURPOSE,
    REVIEW_PROMPT,
    classify_tool,
    parse_proposal,
)
from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, ModelReply, Outcome, ToolSpec
from ..domain.settings import settings_value
from ..domain.skills import patch_section
from .model_calls import ModelCalls
from .ports import Clock, DatabasePort, Observer, Record
from .skills import SkillService

T = TypeVar("T")
REVIEW_TAIL_MESSAGES = 24
REVIEW_INSTRUCTION = (
    "Review the finished turn above. Inspect existing memory and skills with the read-only tools if "
    "useful, then propose durable memory facts or skill edits by calling the matching memory/skill "
    "tool (each call is staged for owner review, never applied). If nothing is worth saving, reply "
    "exactly 'Nothing to save.' and stop."
)
ReadDispatch = Callable[[str, Mapping[str, JSON], InvocationContext], Awaitable[ActionResult]]


class BackgroundReviewService:
    def __init__(
        self,
        database: DatabasePort,
        calls: ModelCalls,
        clock: Clock,
        observer: Observer,
        *,
        read_tools: tuple[ToolSpec, ...] = (),
        read_dispatch: ReadDispatch | None = None,
        skills: SkillService | None = None,
    ) -> None:
        self._db, self._calls, self._clock, self._observer = database, calls, clock, observer
        self._read_tools, self._read_dispatch, self._skills = read_tools, read_dispatch, skills
        self._pending: set[asyncio.Task[object]] = set()
        self._slots = asyncio.Semaphore(2)
        self._closing = False
        self._notify: Callable[[], None] = lambda: None

    def on_queued(self, notify: Callable[[], None]) -> None:
        self._notify = notify

    async def _execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closing:
            raise BusinessError("background_review_closing", "Background review is draining", status=503)
        if len(self._pending) >= 8:
            raise BusinessError(
                "background_review_busy", "Background review requests are bounded", status=503, retryable=True
            )

        async def perform() -> T:
            async with self._slots:
                return await operation()

        task = asyncio.create_task(perform(), name="background-review-operation")
        self._pending.add(task)

        def complete(future: asyncio.Task[T]) -> None:
            self._pending.discard(cast(asyncio.Task[object], future))
            if not future.cancelled():
                future.exception()

        task.add_done_callback(complete)
        return await asyncio.shield(task)

    @staticmethod
    def _public(row: Record) -> Record:
        return {key: value for key, value in row.items() if key not in {"request_key", "request_sha256"}}

    async def trigger(self, agent_id: str, run_id: str) -> None:
        """Idempotent post-turn hook: one review per finished run; a repeat never re-pays."""
        if self._closing:
            return
        try:
            await self._db.write(lambda u: u.background_review_enqueue(agent_id, {"run_id": run_id}))
        except BusinessError:
            return
        self._notify()

    async def submit(self, agent_id: str, request: Record) -> Record:
        async def perform() -> Record:
            result = await self._db.write(lambda u: u.background_review_enqueue(agent_id, request))
            self._notify()
            return self._public(result)

        return await self._execute(perform)

    async def page(self, agent_id: str, after: int = 0, limit: int = 20) -> Record:
        async def perform() -> Record:
            rows = await self._db.read(lambda u: u.background_review_page(agent_id, after, limit))
            return cast(
                Record,
                {"items": [self._public(row) for row in rows], "next_after": rows[-1]["sequence"] if len(rows) == limit else None},
            )

        return await self._execute(perform)

    async def get(self, agent_id: str, batch_id: str) -> Record:
        async def perform() -> Record:
            return self._public(await self._db.read(lambda u: u.background_review_get(agent_id, batch_id)))

        return await self._execute(perform)

    async def cancel(self, agent_id: str, batch_id: str, revision: int) -> Record:
        async def perform() -> Record:
            return self._public(await self._db.write(lambda u: u.background_review_cancel(agent_id, batch_id, revision)))

        return await self._execute(perform)

    async def _review(self, agent_id: str, batch_id: str, run_id: str) -> None:
        settings = settings_value(await self._db.read(lambda u: u.run_settings(agent_id, run_id)))
        run = await self._db.read(lambda u: u.get_run(agent_id, run_id))
        session_id = str(run["session_id"])
        history = await self._db.read(lambda u: u.messages(agent_id, session_id, REVIEW_TAIL_MESSAGES))
        messages: list[Record] = [{"role": "system", "content": REVIEW_PROMPT}]
        for row in history:
            if row["role"] in {"user", "assistant"} and isinstance(row.get("content"), str):
                messages.append({"role": str(row["role"]), "content": str(row["content"])})
        messages.append({"role": "user", "content": REVIEW_INSTRUCTION})
        context = InvocationContext(agent_id, run_id, session_id, cast(int, run["policy_epoch"]))
        proposals: list[dict[str, object]] = []
        for _ in range(MAX_CALLS):
            current = await self._db.read(lambda u: u.background_review_get(agent_id, batch_id))
            if current["state"] != "running":
                return
            try:
                call_id = await self._db.write(
                    lambda u: u.reserve_call(
                        agent_id,
                        run_id,
                        settings["call_reservation_micro"],
                        settings["daily_budget_micro"],
                        settings["model"],
                        purpose="summary",
                    )
                )
            except BusinessError as exc:
                if exc.code in {"budget_exceeded", "database_busy"}:
                    break
                raise
            reply: ModelReply = await self._calls.invoke(
                agent_id, run_id, messages, self._read_tools, settings, reserved_id=call_id
            )
            await self._db.write(lambda u: u.background_review_record_call(agent_id, batch_id, reply.cost_micro or 0))
            if not reply.calls:
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.text,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                        }
                        for call in reply.calls
                    ],
                }
            )
            for call in reply.calls:
                kind = classify_tool(call.name)
                if kind == "read" and self._read_dispatch is not None:
                    result = await self._read_dispatch(call.name, call.arguments, context)
                    text = result.text
                elif kind == "write":
                    try:
                        proposals.append(parse_proposal(call.name, dict(call.arguments)))
                        text = "Staged as a pending proposal for owner review; nothing was applied."
                    except BusinessError as exc:
                        text = exc.message
                else:
                    text = "Denied: background review may only read and propose memory or skill edits."
                messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
        await self._db.write(lambda u: u.background_review_stage(agent_id, batch_id, proposals))

    async def process_one(self) -> bool:
        """One bounded reviewer run across reserve/invoke/settle transactions; cancel and failure settle."""

        async def perform() -> bool:
            target = await self._db.read(lambda u: u.background_review_next())
            if not target:
                return False
            agent, identity, run_id = str(target["agent_id"]), str(target["id"]), str(target["run_id"])
            with (
                self._observer.context(agent_id=agent, operation_id=identity),
                self._observer.span("background.review") as measurement,
            ):
                claimed = await self._db.write(lambda u: u.background_review_claim(agent, identity))
                if claimed is None:
                    return True
                if claimed["state"] != "running":
                    if measurement is not None:
                        measurement.result(Outcome.CANCELLED)
                    return True
                try:
                    await self._review(agent, identity, run_id)
                except asyncio.CancelledError:
                    await self._db.write(lambda u: u.background_review_fail(agent, identity, "background_review_interrupted"))
                    if measurement is not None:
                        measurement.result(Outcome.CANCELLED)
                    raise
                except BusinessError as exc:
                    code = exc.code
                    await self._db.write(lambda u: u.background_review_fail(agent, identity, code))
                    if measurement is not None:
                        measurement.result(Outcome.ERROR)
            return True

        return await self._execute(perform)

    async def _apply_skill(self, agent_id: str, proposal: dict[str, object]) -> Record:
        if self._skills is None:
            raise BusinessError("background_review_unavailable", "Skill review is unavailable", status=503)
        tool = str(proposal["tool"])
        skill_id = cast(str | None, proposal.get("skill_id"))
        revision = int(cast(int, proposal.get("revision", 0)))
        if tool in {"skill_create", "skill_rewrite"}:
            return await self._skills.save(agent_id, str(proposal["content"]), skill_id, revision)
        if tool == "skill_patch":
            previous = await self._skills.get(agent_id, str(skill_id))
            content = patch_section(str(previous["content"]), str(proposal["section"]), str(proposal["text"]))
            return await self._skills.save(agent_id, content, str(skill_id), revision)
        if tool == "skill_rollback":
            return await self._skills.rollback(agent_id, str(skill_id), str(proposal["revision_id"]), revision)
        current = await self._skills.get(agent_id, str(skill_id))
        state = "archived" if tool == "skill_archive" else "disabled"
        return await self._skills.state(
            agent_id,
            str(skill_id),
            revision or cast(int, current["revision"]),
            state,
            str(current["revision_id"]),
            str(current["manifest_sha256"]),
        )

    async def decide(self, agent_id: str, candidate_id: str, decision: Record) -> Record:
        async def perform() -> Record:
            with self._observer.context(agent_id=agent_id, operation_id=candidate_id):
                info = await self._db.read(lambda u: u.background_review_candidate(agent_id, candidate_id, decision))
                if info["kind"] == "memory":
                    result = await self._db.write(
                        lambda u: u.background_review_decide_memory(agent_id, candidate_id, decision)
                    )
                    if result.get("valid") is False:
                        raise BusinessError("background_review_stale", "Captured turn changed; review a new proposal")
                    return self._public(result)
                applied_id: str | None = None
                applied_revision: str | None = None
                if info["decision"] == "apply":
                    row = await self._apply_skill(agent_id, cast(dict[str, object], info["proposal"]))
                    applied_id, applied_revision = str(row["id"]), str(row["revision_id"])
                elif info["decision"] == "undo" and self._skills is not None and info["applied_skill_id"]:
                    current = await self._skills.get(agent_id, str(info["applied_skill_id"]))
                    await self._skills.state(
                        agent_id,
                        str(info["applied_skill_id"]),
                        cast(int, current["revision"]),
                        "archived",
                        str(current["revision_id"]),
                        str(current["manifest_sha256"]),
                    )
                result = await self._db.write(
                    lambda u: u.background_review_decide_skill(
                        agent_id, candidate_id, decision,
                        applied_skill_id=applied_id, applied_skill_revision_id=applied_revision,
                    )
                )
                if result.get("valid") is False:
                    raise BusinessError("background_review_stale", "Captured turn changed; review a new proposal")
                return self._public(result)

        return await self._execute(perform)

    def health(self) -> Record:
        return {
            "closing": self._closing,
            "pending": len(self._pending),
            "model_calls_enabled": True,
            "purpose": PURPOSE,
            "max_calls": MAX_CALLS,
        }

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        if self._pending:
            _, pending = await asyncio.wait(self._pending, timeout=max(0, timeout_s))
            return not pending
        return True