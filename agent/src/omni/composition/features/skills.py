"""Bind the skills API, package storage and revision tools to one service."""

from __future__ import annotations

from ...adapters.skill_packages import SkillPackages
from ...application.capabilities import Capability
from ...application.skills import SkillService
from ...domain.skills import SKILL_SPECS
from . import FeatureBinding, FeatureContext


async def install_skills(context: FeatureContext) -> FeatureBinding:
    packages = SkillPackages()
    context.lifetime.own("skill_packages", 1, packages.close)
    service = SkillService(context.database, context.objects, packages, context.skill_platform)
    context.lifetime.own("skills", 0, service.close, service.stop_accepting)
    specs = SKILL_SPECS if context.default_tools else ()
    return FeatureBinding(
        service, tuple(Capability(spec, service, lambda: service.health()["pending"] == 0) for spec in specs)
    )
