"""Bind person records and derived persona text to instance-owned storage."""

from __future__ import annotations

from ...adapters.persona_files import PersonaGrowthFiles
from ...application.people import PeopleService, PeopleServices
from ...application.persona import PersonaGrowthService
from . import FeatureBinding, FeatureContext


async def install_people(context: FeatureContext) -> FeatureBinding:
    people = PeopleService(context.database, context.objects)
    persona = PersonaGrowthService(context.database, PersonaGrowthFiles(context.paths.persona))
    context.lifetime.own("persona_growth", 1, persona.close)
    return FeatureBinding(PeopleServices(people, persona))
