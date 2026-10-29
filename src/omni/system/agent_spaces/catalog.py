"""配置注册表：有界读取、CAS、跨进程互斥、原子写；不搬运用户数据。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ...config import RuntimePaths
from ..atomic import atomic_write_text
from ..filelock import FileLock
from .layout import AgentProfile, DIRECTORIES, PUBLIC, overlap, resolve_layout


class DirectoryConflict(ValueError):pass


class Catalog(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version: int=1
    active: str='default'
    agents: list[AgentProfile]=Field(default_factory=lambda:[AgentProfile(id='default',name='默认 Agent')],min_length=1,max_length=32)


class AgentDirectoryStore:
    def __init__(self,path: Path,legacy: RuntimePaths,*,isolated=False):
        self.path,self.legacy,self.isolated=Path(path),legacy,isolated

    def _read(self):
        if self.path.is_symlink() or self.path.is_junction():raise ValueError('Agent 注册表不能是链接')
        for parent in self.path.parents:
            if parent.is_symlink() or parent.is_junction():raise ValueError('Agent 注册表路径不能包含链接')
        if not self.path.exists():return Catalog(),''
        if self.path.stat().st_size>131072:raise ValueError('Agent 注册表过大')
        raw=self.path.read_bytes();data=Catalog.model_validate_json(raw)
        self.validate(data)
        return data,hashlib.sha256(raw).hexdigest()

    def validate(self,data: Catalog):
        ids=[p.id for p in data.agents]
        if data.version!=1 or len(set(ids))!=len(ids) or 'default' not in ids or data.active not in ids:
            raise ValueError('Agent 注册表版本、唯一标识或默认选择无效')
        layouts=[self.layout(p) for p in data.agents]
        def owned(layout):
            p=layout.paths
            result=[p.memory,p.resident,p.history,p.skills,p.artifacts,p.cache,p.logs,p.command_logs,p.mcp_logs,p.backups,p.files,p.documents]
            if layout.profile.id!='default':result.extend([layout.home,p.workspace])
            elif layout.profile.directories.workspace:result.append(p.workspace)
            return [path for path in result if path is not None]
        for i,left in enumerate(layouts):
            for right in layouts[i+1:]:
                if any(overlap(a,b) for a in owned(left) for b in owned(right)):
                    raise ValueError('不同 Agent 的空间或持久目录不能相互覆盖')
        return layouts

    def layout(self,profile):
        return resolve_layout(profile,self.legacy,catalog=self.path,isolated=self.isolated)

    def snapshot(self):
        data,revision=self._read()
        return {'revision':revision,**data.model_dump(),'resolved':[self.layout(p).describe() for p in data.agents]}

    def selected(self,identifier=None):
        data,_=self._read();identifier=identifier or data.active
        profile=next((p for p in data.agents if p.id==identifier),None)
        if profile is None:raise ValueError('指定的 Agent 不存在')
        return self.layout(profile)

    def preview(self,data):
        parsed=Catalog.model_validate(data);return [layout.describe() for layout in self.validate(parsed)]

    def save(self,data,*,revision,acknowledge=False):
        parsed=Catalog.model_validate(data);self.validate(parsed)
        if not acknowledge:raise ValueError('请确认目录只在下次启动生效，旧数据不会自动移动')
        with FileLock(self.path.with_suffix('.lock')):
            _,current=self._read()
            if revision!=current:raise DirectoryConflict('目录配置已被其它页面修改，请重新加载后再保存')
            atomic_write_text(self.path,json.dumps(parsed.model_dump(),ensure_ascii=False,indent=2)+'\n')
        return self.snapshot()
