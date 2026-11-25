"""Bind desktop views, consent and owner actions to the platform ports."""

from __future__ import annotations

from ...application.capabilities import Capability
from ...application.desktop import DesktopService
from ...application.desktop_actions import DesktopActions
from ...application.desktop_control import DesktopControlService
from ...application.desktop_portal import DesktopPortalService
from ...application.desktop_services import DesktopServices
from ...domain.desktop_actions import SPEC
from . import FeatureBinding, FeatureContext


async def install_desktop(context: FeatureContext) -> FeatureBinding:
    portal: DesktopPortalService | None = None
    control: DesktopControlService | None = None

    async def detach_view(agent_id: str, view_id: str) -> None:
        failures: list[Exception] = []
        for service in (portal, control):
            if service is not None:
                try:
                    await service.detach(agent_id, view_id)
                except Exception as caught:  # noqa: BLE001 -- Request both releases before reporting a failure.
                    failures.append(caught)
        if failures:
            raise failures[0]

    observations = DesktopService(
        context.database,
        context.platform.desktop,
        context.clock,
        context.ids,
        context.boot_id,
        context.observer,
        context.objects,
        on_detach=detach_view,
    )
    context.lifetime.own("desktop_observations", 0, observations.close, observations.stop_accepting)
    portal = DesktopPortalService(
        context.database,
        context.platform.portal,
        context.ids,
        context.boot_id,
        context.observer,
        context.objects,
        authorize=observations.require_view,
    )
    context.lifetime.own("desktop_portal", 0, portal.close, portal.stop_accepting)
    control = DesktopControlService(
        context.database,
        context.platform.control,
        context.ids,
        context.boot_id,
        context.observer,
        authorize=observations.require_view,
    )
    # Take over the provider's rollback step so consent settles after native cleanup.
    context.lifetime.own("desktop_control", 0, control.close, control.stop_accepting)
    actions = DesktopActions(
        context.database,
        observations,
        context.platform.desktop,
        context.clock,
        context.ids,
        accepting=context.accepting,
    )
    context.lifetime.own("desktop_actions", 0, actions.close, actions.stop_accepting)
    return FeatureBinding(
        DesktopServices(observations, portal, control, actions),
        owner_tools=(Capability(SPEC, actions, actions.quiescent),),
    )
