"""Seed isolated ended unknown terminal receipts; no shell or raw input storage."""

import uuid

from omni_test_support.v2.terminals_storage import begin_terminal


async def unknown(database, agent, workspace):
    session, _request, _spec = await begin_terminal(database, agent, workspace)
    operation = str(uuid.uuid4())

    def finish(u):
        u.terminal_operation_begin(agent, session["id"], operation, "input", "a" * 64, 7)
        receipt = u.terminal_operation_settle(agent, session["id"], operation, "unknown", "terminal_partial_write")
        u.terminal_mark_closed(agent, session["id"], True, 0)
        return receipt

    return await database.write(finish)


def request(detail, **changes):
    return {
        "review_id": str(uuid.uuid4()),
        **{key: detail[key] for key in ("revision", "history_epoch", "fingerprint", "policy")},
        "decision": "still_unknown",
        "evidence": "private-review-evidence-独立核验",
        "acknowledge_limits": True,
        **changes,
    }
