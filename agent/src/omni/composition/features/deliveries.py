"""Bind durable delivery to registered transports and the instance lifecycle."""

from __future__ import annotations

from ...application.delivery import DeliveryService
from . import FeatureBinding, FeatureContext


async def install_deliveries(context: FeatureContext) -> FeatureBinding:
    service = DeliveryService(context.database, context.clock, context.observer, context.channels.delivery)
    context.lifetime.own("deliveries", 0, service.drain, service.stop_accepting)
    context.lifetime.own("delivery_leases", 2, service.release_boot_leases)
    return FeatureBinding(service, start=service.start)
