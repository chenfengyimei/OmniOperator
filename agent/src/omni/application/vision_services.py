"""Group image admission and visual analysis for the instance."""

from __future__ import annotations

from dataclasses import dataclass

from .images import ImageService
from .visual_subagent import VisualSubagentService


@dataclass(frozen=True)
class VisionServices:
    images: ImageService
    visual: VisualSubagentService
