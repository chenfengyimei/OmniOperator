"""Group the learning services used by run processing and the owner workbench."""

from __future__ import annotations

from dataclasses import dataclass

from .background_review import BackgroundReviewService
from .reflections import ReflectionService


@dataclass(frozen=True)
class LearningServices:
    reflections: ReflectionService
    review: BackgroundReviewService
