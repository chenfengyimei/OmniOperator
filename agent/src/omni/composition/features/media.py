"""Mount media processing, including recovery of jobs from the previous process."""

from __future__ import annotations

import logging
from typing import cast

from ...adapters.media import MediaProcessor
from ...application.capabilities import Capability
from ...application.media import (
    CELLS_SPEC,
    LIST_SPEC,
    LOCATE_SPEC,
    TRANSCRIPT_SPEC,
    ZOOM_SPEC,
    BilibiliMediaPort,
    MediaProcessingPort,
    MediaService,
)
from . import FeatureBinding, FeatureContext


async def install_media(context: FeatureContext) -> FeatureBinding:
    processor = context.media_processor
    if processor is None:
        processor = cast(MediaProcessingPort, MediaProcessor())
    # Until the service is constructed, the startup rollback owns the processor directly.
    context.lifetime.own("media", 0, processor.close, processor.stop_accepting)
    # One channel transport supplies both Bilibili reads and bounded media downloads.
    service = MediaService(
        context.database,
        context.objects,
        context.images,
        cast(BilibiliMediaPort, context.channels.bilibili),
        processor,
        context.clock,
        context.control / "media-staging",
        persona=context.persona,
    )

    def stop_accepting() -> None:
        service.stop_accepting()
        processor.stop_accepting()

    context.lifetime.own("media", 0, service.close, stop_accepting)
    recovered = await service.recover()
    if recovered["cancelled"] or recovered["unknown"]:
        logging.getLogger("omni.v2.startup").warning(
            "Media jobs from the previous process were reconciled",
            extra={"event": "media.restart_reconciled", **recovered},
        )
    specs = (LIST_SPEC, TRANSCRIPT_SPEC, LOCATE_SPEC, CELLS_SPEC, ZOOM_SPEC) if context.default_tools else ()
    return FeatureBinding(service, tuple(Capability(spec, service) for spec in specs))
