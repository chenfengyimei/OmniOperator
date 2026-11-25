"""Bind Bilibili reads and publication drafts to the shared channel provider."""

from __future__ import annotations

from ...application.bilibili import PREPARE_SPEC, READ_SPEC, BilibiliService
from ...application.capabilities import Capability
from . import FeatureBinding, FeatureContext


async def install_bilibili(context: FeatureContext) -> FeatureBinding:
    service = BilibiliService(context.database, context.channels.bilibili)
    context.lifetime.own("bilibili", 0, service.close, service.stop_accepting)
    specs = (READ_SPEC, PREPARE_SPEC) if context.default_tools else ()
    return FeatureBinding(service, tuple(Capability(spec, service) for spec in specs))
