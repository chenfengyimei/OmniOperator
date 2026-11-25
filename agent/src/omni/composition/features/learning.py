"""Install post-turn learning with the captured read tools and the skills service."""

from __future__ import annotations

from ...application.background_review import BackgroundReviewService
from ...application.learning_services import LearningServices
from ...application.reflections import ReflectionService
from ...application.skills import SkillService
from . import FeatureBinding, WorkflowFeatureContext, require_service


async def install_learning(context: WorkflowFeatureContext) -> FeatureBinding:
    base = context.base
    skills = require_service(base.services, "skills", SkillService)
    reflections = ReflectionService(base.database)
    base.lifetime.own("reflections", 0, reflections.close)
    review = BackgroundReviewService(
        base.database,
        context.model_calls,
        base.clock,
        base.observer,
        read_tools=context.read_tools,
        read_dispatch=context.read_dispatch,
        skills=skills,
    )
    base.lifetime.own("background_review", 0, review.close, review.stop_accepting)
    return FeatureBinding(LearningServices(reflections, review))
