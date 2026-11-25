"""Group memory search and organization services."""

from __future__ import annotations

from dataclasses import dataclass

from .memory_organization import MemoryOrganizationService
from .memory_semantics import MemorySemanticService


@dataclass(frozen=True)
class MemoryServices:
    semantics: MemorySemanticService
    organization: MemoryOrganizationService
