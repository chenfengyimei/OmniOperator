"""Bind review decisions to executor activity and the schedule notifier."""

from __future__ import annotations

from ...application.action_review import ActionReviewService
from ...application.scheduling import SchedulingService
from . import FeatureBinding, KernelFeatureContext, require_service


async def install_action_reviews(context: KernelFeatureContext) -> FeatureBinding:
    schedules = require_service(context.services, "schedules", SchedulingService)
    service = ActionReviewService(context.database, context.review_blockers, schedules.notify)
    context.lifetime.own("action_reviews", 0, service.close, service.stop_accepting)
    return FeatureBinding(service)
