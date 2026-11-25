"""Feature routers and bounded input for the authenticated V2 API."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar, cast

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, ValidationError

from ...application.ports import Record
from ...domain.contracts import BusinessError

if TYPE_CHECKING:
    from ..http import HttpRuntime

RuntimeLookup = Callable[[Request], "HttpRuntime"]
RouteFactory = Callable[[RuntimeLookup], APIRouter]
InputModel = TypeVar("InputModel", bound=BaseModel)
Service = TypeVar("Service")


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def registered_service(services: Mapping[str, object], feature_id: str, service_type: type[Service]) -> Service:
    service = services.get(feature_id)
    if not isinstance(service, service_type):
        raise BusinessError("feature_unavailable", f"Feature {feature_id} is unavailable", status=503)
    return service


@dataclass(frozen=True)
class FeatureRoutes:
    id: str
    factory: RouteFactory
    extra_paths: tuple[str, ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.id) is None:
            raise ValueError("Invalid feature ID")
        if any(not path.startswith("/agents/{agent_id}/") for path in self.extra_paths):
            raise ValueError("Additional feature paths must remain agent-scoped")


def mount_feature_routes(router: APIRouter, features: tuple[FeatureRoutes, ...], runtime: RuntimeLookup) -> None:
    if len({feature.id for feature in features}) != len(features):
        raise ValueError("Duplicate feature route IDs")
    used = {
        (method, route.path)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in (route.methods or ())
    }
    for feature in features:
        prefix = "/agents/{agent_id}/" + feature.id
        routes = feature.factory(runtime)
        if routes.prefix != prefix and not (feature.extra_paths and routes.prefix == ""):
            raise ValueError("Feature router must use its agent-scoped prefix")
        additional = set(feature.extra_paths)
        for route in routes.routes:
            if not isinstance(route, APIRoute) or (
                route.path != prefix
                and not route.path.startswith(prefix + "/")
                and route.path not in feature.extra_paths
            ):
                raise ValueError("Feature routes must use authenticated HTTP within their prefix")
            keys = {(method, router.prefix + route.path) for method in (route.methods or ())}
            if used & keys:
                raise ValueError("Duplicate feature HTTP route")
            used.update(keys)
            additional.discard(route.path)
        if additional:
            raise ValueError("Declared feature paths are missing from the router")
        router.include_router(routes)


async def bounded_configuration(request: Request, max_bytes: int = 262144, *, strict: bool = False) -> Record:
    parts: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise BusinessError(
                "request_too_large" if max_bytes != 262144 else "configuration_too_large",
                "JSON request exceeds the endpoint size limit",
                status=413,
            )
        parts.append(chunk)
    try:

        def pairs(items: list[tuple[str, Any]]) -> Record:
            result: Record = {}
            for key, item in items:
                if strict and key in result:
                    raise ValueError("Duplicate field")
                result[key] = item
            return result

        value = json.loads(b"".join(parts).decode("utf-8"), object_pairs_hook=pairs)
        if not isinstance(value, dict):
            raise ValueError
        return cast(Record, value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BusinessError("invalid_configuration_request", "A UTF-8 JSON object is required", status=422) from exc


def configuration_input(model: type[InputModel], document: object) -> InputModel:
    try:
        return model.model_validate(document)
    except ValidationError as exc:
        raise BusinessError(
            "invalid_configuration_request", "Configuration request fields are invalid", status=422
        ) from exc
