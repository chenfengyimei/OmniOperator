"""Model tool-batch segment planning: independent read-only capabilities may run in parallel.

Interactive controls, writes and conflicting resource keys stay serial barriers. This module
only plans ordered segments; it never dispatches, approves, settles or replays actions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class BatchEntry:
    """One classified model tool call.

    ``parallel`` is true only for an offered read-only capability; writes need the exclusive
    approval state machine and controls mutate run state, so both remain barriers. ``resource``
    is the effective conflict key; an empty value falls back to the capability name.
    """

    name: str
    resource: str = ""
    parallel: bool = False


def plan_tool_batch(entries: Sequence[BatchEntry]) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """Split a batch into ordered ``("parallel" | "serial", indices)`` segments.

    Call order is preserved exactly: a later call never crosses an earlier barrier, so result
    order and side-effect boundaries match fully-sequential execution. A parallel run admits
    only pairwise-distinct resource keys; a conflict closes the run so the call starts a new
    one after the conflicting calls land. Runs shorter than two calls demote to serial and
    adjacent serial segments merge.
    """
    segments: list[tuple[str, tuple[int, ...]]] = []
    current: list[int] = []
    reserved: set[str] = set()

    def extend_serial(indices: Sequence[int]) -> None:
        if segments and segments[-1][0] == "serial":
            segments[-1] = ("serial", segments[-1][1] + tuple(indices))
        else:
            segments.append(("serial", tuple(indices)))

    def close_parallel() -> None:
        nonlocal current, reserved
        if len(current) >= 2:
            segments.append(("parallel", tuple(current)))
        elif current:
            extend_serial(current)
        current, reserved = [], set()

    for index, entry in enumerate(entries):
        if not entry.parallel:
            close_parallel()
            extend_serial((index,))
            continue
        key = entry.resource or entry.name
        if key in reserved:
            close_parallel()
        reserved.add(key)
        current.append(index)
    close_parallel()
    return tuple(segments)
