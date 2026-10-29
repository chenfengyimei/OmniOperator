"""能力与诊断只读聚合：不连接 MCP、不访问手机、不启动终端/服务。"""
from collections import Counter
import shutil

from fastapi import APIRouter


def create_capabilities_router(runtime, terminal_manager):
    router=APIRouter(tags=['capabilities'])

    @router.get('/api/capabilities')
    def capabilities():
        tools=runtime.registry.describe()
        jobs=runtime.command_jobs.list()
        mcp=[{key:row.get(key) for key in ('name','enabled','state','waiting','max_waiting','generation','configuration_matches','log_error')}
             for row in runtime.mcp.status()]
        android={'adb_found':bool(shutil.which('adb')),**runtime.android.status_passive()}
        return {'read_only':True,'doctor':runtime.doctor(),
                'tools':{'total':len(tools),'by_toolset':dict(Counter(row['toolset'] for row in tools))},
                'terminal':terminal_manager.snapshot(),
                'command_jobs':{'recent_limit':100,'statuses':dict(Counter(row['status'] for row in jobs))},
                'mcp':mcp,'android':{**android,'previews':runtime.android_streams.status()},
                'closing':getattr(runtime.agent,'closing',False)}
    return router
