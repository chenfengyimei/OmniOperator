"""Install memory indexing and organization with scoped instance dependencies."""

from __future__ import annotations

from ...application.memory_organization import MemoryOrganizationService
from ...application.memory_semantics import MemorySemanticService
from ...application.memory_services import MemoryServices
from . import FeatureBinding, FeatureContext


async def install_memory(context: FeatureContext) -> FeatureBinding:
    semantics = MemorySemanticService(context.database, context.vectors)
    context.lifetime.own("memory_semantics", 0, semantics.close, semantics.stop_accepting)
    organization = MemoryOrganizationService(context.database, context.observer)
    context.lifetime.own("memory_organization", 0, organization.close, organization.stop_accepting)
    return FeatureBinding(MemoryServices(semantics, organization))
