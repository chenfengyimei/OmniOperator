"""Isolated unknown intents for owner-reconciliation tests; never executes external code."""

import uuid

from v2_configuration_helpers import model_uow

from omni.domain.contracts import ActionResult, Effect, Outcome, ToolSpec


def seed_unknown(unit, agent, *, tool="files_write", terminal=True, key=None):
    model_uow(unit, agent)
    turn = unit.accept_turn(agent, None, key or str(uuid.uuid4()), "private source canary", {})
    unit.transition(agent, turn["run_id"], "running")
    policy = unit.settings(agent)["policy_revision"]
    action = unit.prepare_action(
        agent,
        turn["run_id"],
        ToolSpec(tool, "Isolated test intent", {}, writes=True),
        {"private": "argument canary"},
        policy,
    )
    approval = next(row for row in unit.pending_approvals(agent) if row["action_id"] == action["id"])
    unit.resolve_approval(agent, approval["id"], True, policy)
    unit.authorize_action(agent, action["id"])
    unit.settle_action(agent, action["id"], ActionResult(Outcome.UNKNOWN, "private result canary", Effect.UNKNOWN))
    if terminal:
        unit.transition(agent, turn["run_id"], "failed", "action_outcome_unknown")
    return {
        **turn,
        "action_id": action["id"],
        "message_id": unit.get_run(agent, turn["run_id"])["input_message_id"],
    }


def review_request(detail, decision="not_applied", evidence="Owner inspected the isolated target"):
    return {
        "review_id": str(uuid.uuid4()),
        "revision": detail["revision"],
        "history_epoch": detail["history_epoch"],
        "fingerprint": detail["fingerprint"],
        "policy": detail["policy"],
        "decision": decision,
        "evidence": evidence,
    }
