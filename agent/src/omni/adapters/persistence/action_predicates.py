"""One current owner-resolution predicate; never rewrites raw action observations or acknowledges model costs."""

from typing import Any

from sqlalchemy import and_, func, or_, select

from ...domain.action_review import POLICY, TERMINAL_RUNS
from . import schema as s

ACTIVE_ACTION_STATES = frozenset({"pending", "approved", "executing"})


def resolved_action(action: Any = s.actions):
    head = s.action_review_heads.alias("current_action_review")
    review = s.action_reviews.alias("current_action_review_evidence")
    run = s.runs.alias("reviewed_action_run")
    session = s.sessions.alias("reviewed_action_session")
    return (
        select(head.c.id)
        .select_from(
            head.join(
                review,
                and_(
                    review.c.id == head.c.last_review_id,
                    review.c.head_id == head.c.id,
                    review.c.agent_id == head.c.agent_id,
                ),
            )
            .join(run, and_(run.c.id == head.c.run_id, run.c.agent_id == head.c.agent_id))
            .join(session, and_(session.c.id == run.c.session_id, session.c.agent_id == run.c.agent_id))
        )
        .where(
            head.c.agent_id == action.c.agent_id,
            head.c.action_id == action.c.id,
            head.c.run_id == action.c.run_id,
            head.c.policy == POLICY,
            head.c.state == "reviewed",
            head.c.resolution.in_(["applied", "not_applied"]),
            head.c.history_epoch == select(s.history_identity.c.epoch).scalar_subquery(),
            head.c.session_epoch == session.c.content_epoch,
            head.c.arguments_sha256 == action.c.arguments_sha256,
            review.c.action_id == action.c.id,
            review.c.run_id == action.c.run_id,
            review.c.history_epoch == head.c.history_epoch,
            review.c.fingerprint == head.c.fingerprint,
            review.c.policy == POLICY,
            review.c.decision == head.c.resolution,
            func.json_extract(review.c.proof, "$.run_state") == run.c.state,
            func.json_extract(review.c.proof, "$.session_id") == run.c.session_id,
            action.c.writes.is_(True),
            action.c.state.not_in(ACTIVE_ACTION_STATES),
            or_(action.c.state == "unknown", action.c.effect == "unknown"),
            run.c.state.in_(TERMINAL_RUNS),
            or_(action.c.body_pruned_at.is_(None), action.c.result_sha256 == head.c.result_sha256),
        )
        .correlate(action)
        .exists()
    )


def unresolved_action(action: Any = s.actions):
    return or_(
        action.c.state.in_(ACTIVE_ACTION_STATES),
        and_(or_(action.c.state == "unknown", action.c.effect == "unknown"), ~resolved_action(action)),
    )
