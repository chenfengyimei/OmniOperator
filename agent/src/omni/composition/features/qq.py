"""Bind the QQ listener to the run dispatcher and the shared OneBot provider."""

from __future__ import annotations

from ...application.qq import QQService
from . import FeatureBinding, KernelFeatureContext


async def install_qq(context: KernelFeatureContext) -> FeatureBinding:
    service = QQService(context.database, context.channels.qq, context.notify_run)
    context.lifetime.own("qq", 0, service.close, service.stop_accepting)
    return FeatureBinding(service, start=service.start)
