"""Bind scheduling to run notifications, cancellation and late startup."""

from ...adapters.timezones import PinnedTimezones
from ...application.capabilities import Capability
from ...application.cronjob import CronjobCapabilities
from ...application.scheduling import SchedulingService
from ...domain.cronjob import CRONJOB_MANAGE
from . import FeatureBinding, KernelFeatureContext


async def install_schedules(context: KernelFeatureContext) -> FeatureBinding:
    service = SchedulingService(
        context.database,
        context.clock,
        PinnedTimezones(),
        context.notify_run,
        context.cancel_run,
        context.skill_platform,
        redact=context.redact,
    )
    context.lifetime.own("scheduler", 0, service.close, service.stop_accepting)
    tools = (
        (Capability(CRONJOB_MANAGE, CronjobCapabilities(service, context.clock), lambda: True),)
        if context.default_tools
        else ()
    )
    return FeatureBinding(service, tools, start=service.start)
