"""Agent 目录设置和当前公开文件区；只消费组装根注入的目录与路径策略。"""
from __future__ import annotations

import hashlib
import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ...system.agent_spaces import DirectoryConflict
from ...system.agent_spaces.inventory import storage_inventory
from ...system.filelock import FileLock
from ...tools.files.io import decode_bytes, is_binary
from ...tools.files.paths import PathRejected
from ...tools.pathpolicy import protected_reason

if TYPE_CHECKING:
    from ...bootstrap import Runtime

MAX_UPLOAD=8*1024*1024
MAX_PREVIEW=16384


class DirectoryUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision: str=Field(max_length=64)
    catalog: dict
    acknowledge: bool=False


class FolderCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scope: str
    path: str=Field(min_length=1,max_length=1024)


def create_storage_router(runtime: Runtime):
    router=APIRouter(tags=['storage'])

    def snapshot():
        catalog=runtime.directories.snapshot()
        running=runtime.agent_layout.describe()
        application=runtime.application_layout.describe()
        configured=next((p for p in catalog['resolved'] if p['id']==running['id']),None)
        return {**catalog,'running':running,'running_application':application,
                'restart_required':catalog['active']!=running['id'] or configured!=running or catalog['application_resolved']!=application,
                'catalog_path':str(runtime.directories.path),
                'public_roots':{name:str(workspace.root) for name,workspace in runtime.public_workspaces.items()},
                'runtime_paths':{name:str(getattr(runtime.paths,name)) for name in ('memory','resident','state','skills','workspace','files','documents','artifacts','cache','logs','command_logs','mcp_logs','backups','run','service_logs')},
                'inventory':storage_inventory(runtime.paths),
                'max_upload_bytes':MAX_UPLOAD,'max_preview_bytes':MAX_PREVIEW}

    @router.get('/api/storage')
    def overview():
        try:return snapshot()
        except (OSError,ValueError) as exc:raise HTTPException(409,str(exc)) from exc

    @router.post('/api/storage/preview')
    def preview(payload: DirectoryUpdate):
        try:return {'resolved':runtime.directories.preview(payload.catalog),
                    'application_resolved':runtime.directories.preview_application(payload.catalog)}
        except (OSError,ValueError) as exc:raise HTTPException(422,str(exc)) from exc

    @router.put('/api/storage')
    def update(payload: DirectoryUpdate):
        if getattr(runtime.agent,'closing',False):raise HTTPException(409,'运行态正在关闭')
        try:
            runtime.directories.save(payload.catalog,revision=payload.revision,acknowledge=payload.acknowledge)
            return snapshot()
        except DirectoryConflict as exc:raise HTTPException(409,str(exc)) from exc
        except (OSError,ValueError) as exc:raise HTTPException(422,str(exc)) from exc

    def target(scope,path,*,exists=False,write=False):
        if scope not in runtime.public_workspaces:raise HTTPException(404,'未知公开文件区')
        try:
            workspace=runtime.public_workspaces[scope]
            # 路径只允许本区内相对寻址；不能使用其它公开根或 Agent 的绝对目录。
            if Path(path).is_absolute():raise ValueError('文件接口只接受所选区域内的相对路径')
            resolved=workspace.resolve(path or '.',must_exist=exists,for_write=write)
            raw=workspace.root/path
            for part in (raw,*raw.parents):
                if part.is_symlink() or part.is_junction():raise ValueError('文件路径不能包含链接')
            return workspace,resolved
        except (OSError,ValueError) as exc:raise HTTPException(403,str(exc)) from exc

    @router.get('/api/storage/files')
    def files(scope: str='files',path: str='',offset: int=0):
        if offset<0 or offset>10000:raise HTTPException(422,'目录分页范围无效')
        workspace,resolved=target(scope,path)
        if not resolved.exists:return {'path':path,'entries':[],'missing':True,'next_offset':None}
        if not resolved.is_dir:raise HTTPException(422,'目标不是目录')
        entries=[];scanned=0;more=False
        try:
            # scandir 有界读取；不为排序而扫描巨型目录或递归树。
            with os.scandir(resolved.path) as iterator:
                for item in iterator:
                    scanned+=1
                    if scanned<=offset:continue
                    if scanned-offset>1000:more=True;break
                    if len(entries)>=200:more=True;break
                    if item.is_symlink() or Path(item.path).is_junction() or protected_reason((item.name,),item.name):continue
                    try:
                        candidate=workspace.resolve(str(Path(path)/item.name),must_exist=True)
                        info=item.stat(follow_symlinks=False)
                        entries.append({'name':item.name,'directory':candidate.is_dir,'size':info.st_size if not candidate.is_dir else None,'modified_at':info.st_mtime})
                    except (OSError,PathRejected):continue
            entries.sort(key=lambda row:(not row['directory'],row['name'].casefold()))
            return {'path':path,'entries':entries,'missing':False,'next_offset':scanned-1 if more else None}
        except OSError as exc:raise HTTPException(409,'无法读取文件区，请检查目录权限') from exc

    @router.get('/api/storage/preview-file')
    def preview_file(scope: str,path: str):
        workspace,resolved=target(scope,path,exists=True)
        if resolved.is_dir:raise HTTPException(422,'请选择文件')
        try:
            workspace.assert_unchanged(resolved)
            with resolved.path.open('rb') as stream:
                opened=os.fstat(stream.fileno());payload=stream.read(MAX_PREVIEW+1)
                current=resolved.path.stat()
                workspace.assert_unchanged(resolved)
                if (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino):raise ValueError('文件在读取时变化，请重新选择')
            binary=is_binary(payload) or payload.startswith((b'%PDF-',b'PK\x03\x04',b'\x89PNG',b'\xff\xd8\xff',b'GIF8',b'MZ'))
            return {'path':path,'size':opened.st_size,'binary':binary,'truncated':len(payload)>MAX_PREVIEW,
                    'text':None if binary else decode_bytes(payload[:MAX_PREVIEW]).text}
        except (OSError,ValueError) as exc:raise HTTPException(409,'文件无法预览或在读取时变化') from exc

    @router.post('/api/storage/folders')
    def create_folder(payload: FolderCreate):
        if getattr(runtime.agent,'closing',False):raise HTTPException(409,'运行态正在关闭')
        workspace,resolved=target(payload.scope,payload.path,write=True)
        try:
            workspace.assert_unchanged(resolved);resolved.path.mkdir(parents=True,exist_ok=False)
            return {'created':True,'path':payload.path}
        except FileExistsError as exc:raise HTTPException(409,'同名路径已存在') from exc
        except (OSError,ValueError) as exc:raise HTTPException(409,'无法创建目录，请检查权限与路径') from exc

    @router.post('/api/storage/upload')
    async def upload(request: Request,scope: str,path: str):
        if getattr(runtime.agent,'closing',False):raise HTTPException(409,'运行态正在关闭')
        workspace,resolved=target(scope,path,write=True)
        content=bytearray()
        async for chunk in request.stream():
            if len(content)+len(chunk)>MAX_UPLOAD:raise HTTPException(413,'单文件不能超过 8 MiB')
            content.extend(chunk)
        lock=runtime.paths.file_history/'upload-locks'/hashlib.sha256(str(resolved.path).encode()).hexdigest()
        # 先完整有界读取再创建；不接受原始 filename 决定目标，不覆盖已有文件。
        def persist():
            with FileLock(lock):
                workspace.assert_unchanged(resolved);resolved.path.parent.mkdir(parents=True,exist_ok=True)
                with resolved.path.open('xb') as output:
                    try:output.write(content);output.flush();os.fsync(output.fileno())
                    except OSError:
                        output.close();resolved.path.unlink(missing_ok=True);raise
            return {'created':True,'path':path,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}
        try:return await asyncio.to_thread(persist)
        except FileExistsError as exc:raise HTTPException(409,'同名文件已存在，未覆盖') from exc
        except (OSError,ValueError) as exc:raise HTTPException(409,'文件保存失败，请检查目录权限与空间') from exc

    return router
