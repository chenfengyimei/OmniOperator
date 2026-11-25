"""Model and routing configuration, revisions, probes and Agent settings."""

from __future__ import annotations

import uuid
from typing import Literal, cast

from fastapi import APIRouter, Query, Request
from pydantic import Field

from ...application.ports import Record
from ...application.routing import RoutingService
from ...domain.contracts import BusinessError
from . import RuntimeLookup, StrictInput, bounded_configuration, configuration_input

Payload = dict[str, object]


class RoutingInput(StrictInput):
    revision: int = Field(ge=0, strict=True)
    value: Payload


class SettingsInput(StrictInput):
    revision: int = Field(ge=1, strict=True)
    value: Payload


class ConfigurationInput(StrictInput):
    kind: Literal["model", "channel", "mcp", "preset"]
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = Field(default=False, strict=True)
    value: Payload
    image_policy: Payload | None = None


class ConfigurationEdit(StrictInput):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = Field(strict=True)
    revision: int = Field(ge=1, strict=True)
    value: Payload
    image_policy: Payload | None = None


def configurations_router(runtime: RuntimeLookup) -> APIRouter:
    router = APIRouter()

    @router.get("/agents/{agent_id}/configurations")
    async def configurations(
        agent_id: uuid.UUID,
        request: Request,
        kind: Literal["model", "channel", "mcp", "preset"] = "model",
        after: uuid.UUID | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        return await runtime(request).configurations.page(str(agent_id), kind, str(after) if after else "", limit)

    def routing_service(request: Request) -> RoutingService:
        service = runtime(request).routing
        if service is None:
            raise BusinessError("routing_unavailable", "Routing service is unavailable", status=503)
        return service

    @router.get("/agents/{agent_id}/routing")
    async def route_policy(agent_id: uuid.UUID, request: Request):
        return await routing_service(request).get(str(agent_id))

    @router.put("/agents/{agent_id}/routing")
    async def save_route_policy(agent_id: uuid.UUID, data: RoutingInput, request: Request):
        return await routing_service(request).save(str(agent_id), cast(Record, data.value), data.revision)

    @router.get("/agents/{agent_id}/runs/{run_id}/routing")
    async def route_report(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await routing_service(request).report(str(agent_id), str(run_id))

    @router.post("/agents/{agent_id}/configurations", status_code=201)
    async def create_configuration(agent_id: uuid.UUID, request: Request):
        data = configuration_input(ConfigurationInput, await bounded_configuration(request))
        return await runtime(request).configurations.create(
            str(agent_id),
            data.kind,
            data.name,
            data.enabled,
            cast(Record, data.value),
            cast(Record | None, data.image_policy),
        )

    @router.get("/agents/{agent_id}/configurations/template")
    async def configuration_template(
        agent_id: uuid.UUID, request: Request, kind: Literal["model", "channel", "mcp", "preset"] = "model"
    ):
        await runtime(request).application.settings(str(agent_id))
        return {"schema": 1, **runtime(request).configurations.template(kind)}

    @router.post("/agents/{agent_id}/configurations/preview")
    async def preview_configuration(agent_id: uuid.UUID, request: Request):
        await runtime(request).application.settings(str(agent_id))
        return runtime(request).configurations.preview(await bounded_configuration(request))

    @router.get("/agents/{agent_id}/configurations/{config_id}")
    async def get_configuration(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await runtime(request).configurations.get(str(agent_id), str(config_id))

    @router.put("/agents/{agent_id}/configurations/{config_id}")
    async def save_configuration(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        data = configuration_input(ConfigurationEdit, await bounded_configuration(request))
        return await runtime(request).configurations.save(
            str(agent_id),
            str(config_id),
            data.name,
            data.enabled,
            cast(Record, data.value),
            data.revision,
            cast(Record | None, data.image_policy),
        )

    @router.delete("/agents/{agent_id}/configurations/{config_id}")
    async def archive_configuration(
        agent_id: uuid.UUID, config_id: uuid.UUID, request: Request, revision: int = Query(ge=1)
    ):
        await runtime(request).configurations.archive(str(agent_id), str(config_id), revision)
        return {"archived": True, "history_retained": True}

    @router.get("/agents/{agent_id}/configurations/{config_id}/revisions")
    async def configuration_history(
        agent_id: uuid.UUID,
        config_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).configurations.history(str(agent_id), str(config_id), after, limit)

    @router.get("/agents/{agent_id}/configurations/{config_id}/diagnosis")
    async def configuration_diagnosis(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await runtime(request).configurations.diagnose(str(agent_id), str(config_id))

    @router.get("/agents/{agent_id}/configurations/{config_id}/preset")
    async def apply_preset(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        return await runtime(request).configurations.preset(str(agent_id), str(config_id))

    @router.post("/agents/{agent_id}/configurations/{config_id}/probe")
    async def configuration_probe(agent_id: uuid.UUID, config_id: uuid.UUID, request: Request):
        """连接测试：用所存凭据向端点发起一次最小真实调用，验证 Key/端点/模型可用。

        不创建运行、不落库调用台账；离线实例的适配器会明确拒绝（offline_model_denied）。
        先经 diagnose 校验凭据授权与配置启用，避免未授权凭据被尝试使用。
        """
        rt = runtime(request)
        row = await rt.configurations.get(str(agent_id), str(config_id))
        if row["kind"] != "model":
            raise BusinessError("probe_not_model", "Only model configurations can be probed", status=422)
        diagnosis = await rt.configurations.diagnose(str(agent_id), str(config_id))
        if not diagnosis.get("configuration_enabled"):
            raise BusinessError("probe_disabled", "Enable the model configuration before probing", status=409)
        if not diagnosis.get("available"):
            raise BusinessError(
                "probe_unavailable",
                f"Model is not ready: {diagnosis.get('state', 'unknown')}",
                status=409,
            )
        port = rt.model_port
        if port is None:
            raise BusinessError("probe_unavailable", "No model adapter is wired in this instance", status=503)
        fields = dict(cast(Payload, row["value"]))
        # 最小调用：输出上限压到 16 token，避免测试产生明显费用。
        fields["output_tokens"] = min(int(cast(int, fields.get("output_tokens", 16))), 16)
        settings: Payload = {
            "persona": "",
            "workspace": "",
            "daily_budget_micro": 0,
            "call_reservation_micro": 1,
            "max_steps": 1,
            "disabled_tools": [],
            "model_profile_id": str(config_id),
            "model_revision_id": str(row.get("revision_id", "")),
            "model_permission_epoch": 1,
            **fields,
        }
        from ...domain.settings import settings_value

        resolved = settings_value(settings)
        complete = getattr(port, "complete", None)
        if complete is None:
            raise BusinessError("probe_unavailable", "Model adapter does not support probing", status=503)
        reply = await complete([{"role": "user", "content": "Reply with the single word: ok"}], [], resolved)
        return {
            "ok": True,
            "model": fields.get("model", ""),
            "content": str(getattr(reply, "content", "") or "")[:120],
            "completion_reason": getattr(reply, "completion_reason", "stop"),
        }

    @router.get("/agents/{agent_id}/settings/revisions")
    async def settings_history(
        agent_id: uuid.UUID,
        request: Request,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await runtime(request).configurations.settings_history(str(agent_id), after, limit)

    @router.get("/agents/{agent_id}/runs/{run_id}/configuration")
    async def run_configuration(agent_id: uuid.UUID, run_id: uuid.UUID, request: Request):
        return await runtime(request).application.run_configuration(str(agent_id), str(run_id))

    @router.get("/agents/{agent_id}/settings")
    async def settings(agent_id: uuid.UUID, request: Request):
        return await runtime(request).application.settings(str(agent_id))

    @router.put("/agents/{agent_id}/settings")
    async def save_settings(agent_id: uuid.UUID, request: Request):
        data = configuration_input(SettingsInput, await bounded_configuration(request))
        return await runtime(request).application.save_settings(str(agent_id), cast(Record, data.value), data.revision)

    return router
