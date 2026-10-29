"""工作台领域路由聚合；应用生命周期与统一鉴权仍由 web.py 持有。"""
from __future__ import annotations
from .command_jobs import create_command_jobs_router
from .android import create_android_router

from typing import TYPE_CHECKING

from fastapi import APIRouter

from .approvals import create_approvals_router
from .artifacts import create_artifacts_router
from .automation import create_automation_router
from .conversation import create_conversation_router
from .devices import create_devices_router
from .mcp import create_mcp_router
from .memory import create_memory_router
from .operations import create_operations_router
from .publications import create_publications_router
from .runs import create_runs_router
from .sessions import create_sessions_router
from .skills import create_skills_router

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_api_routers(runtime: Runtime) -> tuple[APIRouter, ...]:
    return (create_android_router(runtime), create_command_jobs_router(runtime),
        create_sessions_router(runtime),
        create_conversation_router(runtime),
        create_runs_router(runtime),
        create_memory_router(runtime),
        create_skills_router(runtime),
        create_artifacts_router(runtime),
        create_approvals_router(runtime),
        create_mcp_router(runtime),
        create_operations_router(runtime),
        create_automation_router(runtime),
        create_publications_router(runtime),
        create_devices_router(runtime),
    )

__all__ = ["create_api_routers"]
