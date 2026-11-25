"""Bind scheduling to run notifications, cancellation and late startup."""

from ...adapters.timezones import PinnedTimezones
from ...application.capabilities import Capability
from ...application.cronjob import CronjobCapabilities
from ...application.scheduling import SchedulingService
from ...domain.cronjob import CRONJOB_MANAGE
from . import FeatureBinding, KernelFeatureContext


async def install_schedules(context: KernelFeatureContext) -> FeatureBinding:
    base = context.base
    service = SchedulingService(
        base.database,
        base.clock,
        PinnedTimezones(),
        context.notify_run,
        context.cancel_run,
        base.skill_platform,
        redact=context.sink.redactor.text,
    )
    base.lifetime.own("scheduler", 0, service.close, service.stop_accepting)
    tools = (
        (Capability(CRONJOB_MANAGE, CronjobCapabilities(service, base.clock), lambda: True),)
        if base.default_tools
        else ()
    )
    return FeatureBinding(service, tools, start=service.start)
