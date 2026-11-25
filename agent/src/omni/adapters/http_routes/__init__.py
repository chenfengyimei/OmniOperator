"""Feature routers and bounded input for the authenticated V2 API."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
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


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class FeatureRoutes:
    id: str
    factory: RouteFactory

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.id) is None:
            raise ValueError("Invalid feature ID")


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
        if routes.prefix != prefix:
            raise ValueError("Feature router must use its agent-scoped prefix")
        for route in routes.routes:
            if not isinstance(route, APIRoute) or (route.path != prefix and not route.path.startswith(prefix + "/")):
                raise ValueError("Feature routes must use authenticated HTTP within their prefix")
            keys = {(method, router.prefix + route.path) for method in (route.methods or ())}
            if used & keys:
                raise ValueError("Duplicate feature HTTP route")
            used.update(keys)
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
