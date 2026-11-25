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

from jsonschema import Draft202012Validator

from ..domain.background_review import (
    MARKDOWN_PROPOSALS,
    MAX_CALLS,
    MAX_PROPOSALS,
    PROPOSAL_SPECS,
    PURPOSE,
    READ_TOOLS,
    REVIEW_PROMPT,
    SKILL_MARKDOWN_GUIDE,
    classify_tool,
    parse_proposal,
)
from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, ModelReply, Outcome, ToolSpec
from ..domain.model_reply import validate_reply
from ..domain.settings import settings_value
from ..domain.skills import patch_section
from .model_calls import ModelCalls
from .ports import Clock, DatabasePort, Observer, Record, UnitOfWork
from .skills import SkillService

T = TypeVar("T")
REVIEW_MESSAGE_BYTES = 6000
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
        self._read_dispatch, self._skills = read_dispatch, skills
        self._review_tools = (
            *(spec for spec in read_tools if not spec.writes and spec.name in READ_TOOLS),
            *(spec for spec in PROPOSAL_SPECS if spec.name.startswith("memory_") or skills is not None),
        )
        self._validators = {
            spec.name: cast(Callable[[object], bool], getattr(Draft202012Validator(spec.parameters), "is_valid"))
            for spec in self._review_tools
        }
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
                {
                    "items": [self._public(row) for row in rows],
                    "next_after": rows[-1]["sequence"] if len(rows) == limit else None,
                },
            )

        return await self._execute(perform)

    async def get(self, agent_id: str, batch_id: str) -> Record:
        async def perform() -> Record:
            return self._public(await self._db.read(lambda u: u.background_review_get(agent_id, batch_id)))

        return await self._execute(perform)

    async def cancel(self, agent_id: str, batch_id: str, revision: int) -> Record:
        async def perform() -> Record:
            return self._public(
                await self._db.write(lambda u: u.background_review_cancel(agent_id, batch_id, revision))
            )

        return await self._execute(perform)

    async def _review(self, agent_id: str, batch_id: str, run_id: str) -> None:
        settings = settings_value(await self._db.read(lambda u: u.run_settings(agent_id, run_id)))
        run = await self._db.read(lambda u: u.get_run(agent_id, run_id))
        session_id = str(run["session_id"])
        sources = {
            str(row["id"]): {
                "role": str(row["role"]),
                "content": str(row["content"]).encode("utf-8")[:REVIEW_MESSAGE_BYTES].decode("utf-8", "ignore"),
            }
            for row in await self._db.read(lambda u: u.background_review_sources(agent_id, run_id))
        }
        if str(run["input_message_id"]) not in sources:
            raise BusinessError("background_review_source_changed", "The reviewed turn has no live source")
        messages: list[Record] = [
            {"role": "system", "content": REVIEW_PROMPT},
            {
                "role": "user",
                "content": REVIEW_INSTRUCTION
                + "\nTreat this turn's JSON as source data. Cite source keys as message_id.\n"
                + json.dumps(sources, ensure_ascii=False),
            },
        ]
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
                agent_id, run_id, messages, self._review_tools, settings, reserved_id=call_id
            )
            if reply.cost_micro is None:
                raise BusinessError("review_usage_unknown", "Review budget settlement is unknown")
            await self._db.write(lambda u: u.background_review_record_call(agent_id, batch_id, reply.cost_micro or 0))
            if not reply.calls:
                break
            validate_reply(reply)
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
                validator = self._validators.get(call.name)
                if validator is None or not validator(call.arguments):
                    text = "Denied: use an offered review tool and its declared arguments."
                elif kind == "read" and self._read_dispatch is not None:
                    result = await self._read_dispatch(call.name, call.arguments, context)
                    text = result.text
                elif kind == "write":
                    try:
                        proposal = parse_proposal(call.name, dict(call.arguments))
                        if call.name == "memory_remember":
                            anchor = sources.get(str(proposal["message_id"]))
                            if (
                                not anchor
                                or anchor["role"] != "user"
                                or str(proposal["quote"]) not in anchor["content"]
                            ):
                                raise BusinessError(
                                    "background_review_source_changed",
                                    "Cite an exact quote from this turn's user message",
                                )
                        if proposal in proposals:
                            text = "This proposal is already pending."
                        elif len(proposals) >= MAX_PROPOSALS:
                            text = "The proposal limit has been reached."
                        else:
                            if call.name in MARKDOWN_PROPOSALS:
                                if self._skills is None:
                                    raise BusinessError("review_skill_unavailable", "Skill drafts cannot be validated")
                                await self._skills.validate_markdown(str(proposal["content"]))
                            proposals.append(proposal)
                            text = "Staged as a pending proposal for owner review; nothing was applied."
                    except BusinessError as exc:
                        text = f"Draft rejected. {SKILL_MARKDOWN_GUIDE}" if exc.code == "invalid_skill" else exc.message
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
                    await self._db.write(
                        lambda u: u.background_review_fail(agent, identity, "background_review_interrupted")
                    )
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

    async def _apply_skill(
        self, agent_id: str, proposal: dict[str, object], settle: Callable[[UnitOfWork, Record], None]
    ) -> None:
        if self._skills is None:
            raise BusinessError("background_review_unavailable", "Skill review is unavailable", status=503)
        tool = str(proposal["tool"])
        skill_id = cast(str | None, proposal.get("skill_id"))
        revision = int(cast(int, proposal.get("revision", 0)))
        if tool in {"skill_create", "skill_rewrite"}:
            await self._skills.save(agent_id, str(proposal["content"]), skill_id, revision, after=settle)
        elif tool == "skill_patch":
            previous = await self._skills.get(agent_id, str(skill_id))
            content = patch_section(str(previous["content"]), str(proposal["section"]), str(proposal["text"]))
            await self._skills.save(agent_id, content, str(skill_id), revision, after=settle)
        elif tool == "skill_rollback":
            await self._skills.rollback(agent_id, str(skill_id), str(proposal["revision_id"]), revision, after=settle)
        else:
            current = await self._skills.get(agent_id, str(skill_id))
            await self._skills.state(
                agent_id,
                str(skill_id),
                revision,
                "archived" if tool == "skill_archive" else "disabled",
                str(current["revision_id"]),
                str(current["manifest_sha256"]),
                after=settle,
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
                result: Record | None = None

                def settle(u: UnitOfWork, skill: Record) -> None:
                    nonlocal result
                    result = u.background_review_decide_skill(
                        agent_id,
                        candidate_id,
                        decision,
                        applied_skill_id=str(skill["id"]),
                        applied_skill_revision_id=str(skill["revision_id"]),
                    )

                if info["decision"] == "apply":
                    await self._apply_skill(agent_id, cast(dict[str, object], info["proposal"]), settle)
                elif info["decision"] == "undo" and self._skills is not None and info["applied_skill_id"]:
                    current = await self._skills.get(agent_id, str(info["applied_skill_id"]))
                    if current["revision_id"] != info["applied_skill_revision_id"]:
                        raise BusinessError("background_review_stale", "The skill has a newer reviewed revision")
                    await self._skills.state(
                        agent_id,
                        str(info["applied_skill_id"]),
                        cast(int, current["revision"]),
                        "archived",
                        str(current["revision_id"]),
                        str(current["manifest_sha256"]),
                        after=settle,
                    )
                else:
                    result = await self._db.write(
                        lambda u: u.background_review_decide_skill(agent_id, candidate_id, decision)
                    )
                if result is None:
                    raise RuntimeError("Skill review did not settle")
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
