"""后台命令管理适配；不提供绕过 Registry 审批的启动接口。"""
from fastapi import APIRouter, HTTPException


def create_command_jobs_router(runtime):
    router=APIRouter(tags=['command-jobs'])

    @router.get('/api/command-jobs')
    def list_jobs(session_id: int | None = None):
        return {'jobs':runtime.command_jobs.list(session_id=session_id)}

    @router.get('/api/command-jobs/{identifier}')
    def read_job(identifier: str, after: int = 0):
        try:return runtime.command_jobs.read(identifier,after=after,limit=65536)
        except KeyError:raise HTTPException(404,'命令任务不存在') from None
        except (ValueError,OSError) as exc:raise HTTPException(409,str(exc)) from exc

    @router.post('/api/command-jobs/{identifier}/cancel')
    def cancel_job(identifier: str):
        try:return runtime.command_jobs.cancel(identifier)
        except KeyError:raise HTTPException(404,'命令任务不存在') from None
        except RuntimeError as exc:raise HTTPException(409,str(exc)) from exc
    return router
