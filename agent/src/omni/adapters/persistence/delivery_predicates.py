"""Shared outbound pending/review predicates for dispatch, scheduling and maintenance queries."""

from sqlalchemy import or_, select

from ...domain.delivery import CANCELLABLE_DELIVERIES
from . import schema as s


def held_outbound():
    return (
        select(s.dispatch_resources.c.resource_key)
        .where(
            s.dispatch_resources.c.attempt_id == s.outbox.c.latest_attempt_id,
            s.dispatch_resources.c.state == "held",
        )
        .correlate(s.outbox)
        .exists()
    )


def unresolved_outbound():
    return (s.outbox.c.state == "unknown") & or_(
        s.outbox.c.reviewed_epoch != s.outbox.c.review_epoch, s.outbox.c.resolution.is_(None)
    )


def pending_outbound():
    return or_(s.outbox.c.state.in_(CANCELLABLE_DELIVERIES), held_outbound())


def pending_for_run(run_id):
    return select(s.outbox.c.id).where(s.outbox.c.run_id == run_id, pending_outbound()).exists()
