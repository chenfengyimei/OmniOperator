"""Pure rules for bounded model empty-reply recovery: deterministic-empty detection and cost-aware budget.

On an empty completion (no answer text and no tool calls) the turn may retry the SAME authorized
model a bounded number of times; each retry is a fresh reserved call that re-bills the full input
and stays under the daily budget. Two guards shorten the streak: a deterministic empty (two
consecutive usage-backed zero-output empties with the same model/provider/finish signature) skips
the remaining retries, and a single expensive attempt drops the budget from 3 to 1.

V2 hard limits the reference does not have: a call whose cost is unknown settled ``unknown`` and is
NEVER replayed, and recovery never swaps the account or model (no fallback chain). This module only
decides — it never reserves, dispatches or touches storage.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ModelReply

DEFAULT_EMPTY_RETRY_BUDGET = 3
REDUCED_EMPTY_RETRY_BUDGET = 1
DEFAULT_COST_THRESHOLD_MICRO = 250_000


@dataclass(frozen=True)
class EmptyAttempt:
    model: str
    provider: str
    finish_reason: str
    usage_present: bool
    zero_output: bool
    cost_micro: int | None

    @property
    def signature(self) -> tuple[str, str, str]:
        return (self.model, self.provider, self.finish_reason)


def is_empty(reply: ModelReply) -> bool:
    return not reply.calls and not reply.text.strip()


def empty_attempt(reply: ModelReply, *, model: str, provider: str) -> EmptyAttempt:
    usage_present = reply.output_tokens is not None and reply.input_tokens is not None and reply.input_tokens > 0
    return EmptyAttempt(
        model=model,
        provider=provider,
        finish_reason=reply.completion_reason,
        usage_present=usage_present,
        zero_output=reply.output_tokens == 0,
        cost_micro=reply.cost_micro,
    )


def deterministic_empty(attempts: list[EmptyAttempt]) -> bool:
    if len(attempts) < 2:
        return False
    signature = attempts[0].signature
    return all(a.signature == signature and a.usage_present and a.zero_output for a in attempts)


def should_retry_empty(attempts: list[EmptyAttempt], threshold_micro: int = DEFAULT_COST_THRESHOLD_MICRO) -> bool:
    """Whether to send another reserved attempt after the latest empty completion.

    ``attempts`` includes the latest empty. An unknown-cost attempt settled ``unknown`` and is never
    replayed; a deterministic streak or an exhausted (cost-aware) budget stops recovery.
    """
    if not attempts:
        return False
    latest = attempts[-1]
    if latest.cost_micro is None:
        return False
    if deterministic_empty(attempts):
        return False
    budget = REDUCED_EMPTY_RETRY_BUDGET if latest.cost_micro >= threshold_micro else DEFAULT_EMPTY_RETRY_BUDGET
    return len(attempts) < budget
