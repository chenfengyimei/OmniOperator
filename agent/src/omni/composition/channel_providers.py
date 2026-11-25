"""Bind channel drivers and own each transport shared by their services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..adapters.bilibili import BilibiliGateway
from ..adapters.diagnostics import Redactor
from ..adapters.onebot import OneBotTransport
from ..application.bilibili import BilibiliPort
from ..application.ports import AsyncSecretPort, DatabasePort, DeliveryPort, Record
from ..application.qq import QQTransportPort
from ..domain.delivery import OutboundMessage
from .lifecycle import Lifetime


@dataclass(frozen=True)
class ChannelProviders:
    qq: QQTransportPort
    bilibili: BilibiliPort
    delivery: dict[str, DeliveryPort]


def register_channel_providers(
    lifetime: Lifetime,
    database: DatabasePort,
    secrets: AsyncSecretPort,
    redactor: Redactor,
    *,
    qq_transport: QQTransportPort | None = None,
    bilibili_backend: BilibiliPort | None = None,
    delivery_ports: Mapping[str, DeliveryPort] | None = None,
) -> ChannelProviders:
    owned: set[int] = set()

    def own(name: str, port: DeliveryPort) -> None:
        if id(port) not in owned:
            lifetime.own(name, 1, port.close)
            owned.add(id(port))

    async def authorize_qq(message: OutboundMessage) -> str:
        return await database.read(lambda u: u.qq_outbound_authorized(message))

    async def authorize_bilibili(message: OutboundMessage) -> Record:
        return await database.read(lambda u: u.bilibili_outbound_authorized(message))

    qq = qq_transport if qq_transport is not None else OneBotTransport(secrets, authorize_qq)
    own("onebot", qq)
    bilibili = (
        bilibili_backend if bilibili_backend is not None else BilibiliGateway(secrets, redactor, authorize_bilibili)
    )
    own("bilibili_gateway", bilibili)
    overrides = dict(delivery_ports or {})
    for driver, port in overrides.items():
        own(f"delivery_transport:{driver}", port)
    return ChannelProviders(qq, bilibili, {"qq": qq, "bilibili": bilibili, **overrides})
