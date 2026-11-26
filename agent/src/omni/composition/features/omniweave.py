"""Install the owner Web admission observer for this instance."""

from __future__ import annotations

from ...application.omniweave import OmniWeaveService
from . import FeatureBinding, KernelFeatureContext


async def install_omniweave(context: KernelFeatureContext) -> FeatureBinding:
    return FeatureBinding(OmniWeaveService(context.omniweave_mode))
