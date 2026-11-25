"""Group owner terminals and their durable operation reviews."""

from __future__ import annotations

from dataclasses import dataclass

from .terminal_review import TerminalReviewService
from .terminals import TerminalService


@dataclass(frozen=True)
class TerminalServices:
    terminals: TerminalService
    reviews: TerminalReviewService
