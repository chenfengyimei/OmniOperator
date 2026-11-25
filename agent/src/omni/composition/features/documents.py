"""Bind source documents to local indexes and persona invalidation."""

from __future__ import annotations

from ...application.documents import DocumentService
from ...application.people import PeopleServices
from . import FeatureBinding, FeatureContext, require_service


async def install_documents(context: FeatureContext) -> FeatureBinding:
    people = require_service(context.services, "people", PeopleServices)
    service = DocumentService(context.database, context.objects, context.vectors, persona=people.persona)
    context.lifetime.own("source_documents", 1, service.close)
    return FeatureBinding(service)
