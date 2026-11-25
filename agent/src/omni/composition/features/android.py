"""Construct the Android feature and advertise its approved tool surface."""

from __future__ import annotations

from ...adapters.android import AndroidBackend
from ...application.android import AndroidService
from ...application.capabilities import Capability
from ...domain.android import ACCESS_SPEC, MODEL_SPEC, OBSERVE_SPEC, RESTORE_SPEC, SPEC
from . import FeatureBinding, FeatureContext


async def install_android(context: FeatureContext) -> FeatureBinding:
    backend = context.android_backend
    if backend is None:
        backend = AndroidBackend(context.control / "android-captures", secrets=context.secrets)
    context.lifetime.own("android_io", 1, backend.close, backend.stop_accepting)
    service = AndroidService(
        context.database,
        backend,
        context.objects,
        context.clock,
        context.ids,
        accepting=context.accepting,
    )
    context.lifetime.own("android", 0, service.close, service.stop_accepting)
    agent_specs = (ACCESS_SPEC, OBSERVE_SPEC, MODEL_SPEC) if context.default_tools else ()
    return FeatureBinding(
        service,
        tuple(Capability(spec, service, service.quiescent) for spec in agent_specs),
        tuple(Capability(spec, service, service.quiescent) for spec in (SPEC, RESTORE_SPEC)),
    )
