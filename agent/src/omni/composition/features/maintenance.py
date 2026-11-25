"""Install supervised maintenance after its document, memory and learning dependencies."""

from __future__ import annotations

from ...application.documents import DocumentService
from ...application.learning_services import LearningServices
from ...application.maintenance import MaintenanceService
from ...application.memory_services import MemoryServices
from ...domain.memory_decay import DECAY_DAILY_HOUR
from . import FeatureBinding, WorkflowFeatureContext, require_service


async def install_maintenance(context: WorkflowFeatureContext) -> FeatureBinding:
    base = context.base
    documents = require_service(base.services, "documents", DocumentService)
    memory = require_service(base.services, "memory", MemoryServices)
    learning = require_service(base.services, "learning", LearningServices)
    service = MaintenanceService(
        base.database,
        documents,
        base.clock,
        base.observer,
        memory.organization,
        learning.review,
        decay_daily_hour=DECAY_DAILY_HOUR if base.default_tools else None,
    )
    memory.organization.on_queued(service.notify)
    learning.review.on_queued(service.notify)
    base.lifetime.own("maintenance", 0, service.close, service.stop_accepting)
    return FeatureBinding(service, start=service.start)
