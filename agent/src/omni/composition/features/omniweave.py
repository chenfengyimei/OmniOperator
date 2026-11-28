"""Install the shared owner Web and Router capability service."""

from __future__ import annotations

from ...application.capabilities import RunCapabilityProvider
from ...application.omniweave import OmniWeaveService
from . import FeatureBinding, KernelFeatureContext


async def install_omniweave(context: KernelFeatureContext) -> FeatureBinding:
    service = OmniWeaveService(
        context.omniweave_mode,
        context.base.database,
        context.notify_run,
        context.base.objects,
        context.cancel_run,
        context.worker_tool_specs,
    )
    return FeatureBinding(service, run_provider=RunCapabilityProvider("omniweave", service, lambda _: True))
