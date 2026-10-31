"""上下文设置与只读统计；不调用模型、不构造服务、不修改运行中的配置。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ...memory.state import StateUnavailable
from ...system.config_io import (
    ConfigConflict,
    commit_config_updates,
    read_config_snapshot,
)
from ...system.filelock import LockTimeout

if TYPE_CHECKING:
    from ...bootstrap import Runtime
    from ...config import AppConfig

SECTIONS={
    'limits':('max_history_messages','max_agent_steps','resident_memory_chars'),
    'compression':('enabled','trigger_chars','keep_recent','min_messages'),
    'review':('enabled','max_messages','steps','queue_depth','min_messages'),
    'memory':('max_writes_per_day','max_fact_chars','recall_prefix'),
}


class ContextUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision: str=Field(min_length=64,max_length=64)
    updates: dict[str,dict[str,Any]]


def settings(config: AppConfig) -> dict[str,dict[str,Any]]:
    return {section:{name:getattr(getattr(config,section),name) for name in names}
            for section,names in SECTIONS.items()}


def create_context_router(runtime: Runtime) -> APIRouter:
    router=APIRouter(tags=['context'])

    def configuration() -> dict[str,Any]:
        _text,saved,revision=read_config_snapshot(runtime.paths.config)
        running=settings(runtime.config);pending=settings(saved)
        return {'revision':revision,'saved':pending,'running':running,
                'restart_required':pending!=running,
                'changed_fields':[f'{section}.{key}' for section,values in pending.items()
                                  for key,value in values.items() if value!=running[section][key]],
                'hint':'保存到配置文件后，重启相应入口生效；当前回合继续使用运行中的配置。'}

    @router.get('/api/context')
    def overview(session_id: int | None=Query(default=None,ge=1)) -> dict[str,Any]:
        sid=session_id if session_id is not None else runtime.agent.session_id
        if not runtime.store.session_exists(sid):raise HTTPException(404,'会话不存在')
        try:
            return {'session_id':sid,'agent_id':runtime.paths.agent_id,'model':runtime.agent.model,
                    'context':runtime.agent.context_overview(sid),
                    'cache':runtime.runs.context_cache_usage(sid)}
        except (ValueError,StateUnavailable) as exc:
            raise HTTPException(409,'上下文统计暂不可用，请检查记忆与运行状态库') from exc

    @router.get('/api/context/settings')
    def get_settings() -> dict[str,Any]:
        try:return configuration()
        except (OSError,ValueError) as exc:raise HTTPException(409,'上下文配置无法读取，请检查配置文件') from exc

    @router.put('/api/context/settings')
    def update(payload: ContextUpdate) -> dict[str,Any]:
        if getattr(runtime.agent,'closing',False):raise HTTPException(409,'运行态正在关闭')
        if not payload.updates or any(section not in SECTIONS or not values
                or any(key not in SECTIONS[section] for key in values)
                for section,values in payload.updates.items()):
            raise HTTPException(422,'只允许修改上下文、记忆和回合整理的声明配置')
        try:
            commit_config_updates(runtime.paths.config,payload.updates,revision=payload.revision)
            return configuration()
        except ConfigConflict as exc:raise HTTPException(409,str(exc)) from exc
        except LockTimeout as exc:raise HTTPException(409,'配置正在由其它入口修改，请稍后重试') from exc
        except (OSError,ValueError) as exc:raise HTTPException(422,'配置未保存，请检查各项数值和配置文件格式') from exc

    return router
