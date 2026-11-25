"""Install visual analysis over the registered model, media and image services."""

from __future__ import annotations

from ...adapters.visual import ImageObjectFrames, MediaFrameSheets, ModelCallsVisualModelFactory
from ...application.capabilities import Capability
from ...application.media import MediaService
from ...application.vision_services import VisionServices
from ...application.visual_subagent import VISUAL_SUBAGENT, VisualSubagentCapabilities, VisualSubagentService
from . import FeatureBinding, WorkflowFeatureContext, require_service


async def install_vision(context: WorkflowFeatureContext) -> FeatureBinding:
    base = context.base
    media = require_service(base.services, "media", MediaService)
    service = VisualSubagentService(
        base.database,
        ImageObjectFrames(base.objects, base.images),
        ModelCallsVisualModelFactory(base.database, context.model_calls),
        media=MediaFrameSheets(media, base.objects, base.images),
    )
    base.lifetime.own("visual", 0, service.close, service.stop_accepting)
    tools = (
        (Capability(VISUAL_SUBAGENT, VisualSubagentCapabilities(service), lambda: service.health()["pending"] == 0),)
        if base.default_tools
        else ()
    )
    return FeatureBinding(VisionServices(base.images, service), tools)
