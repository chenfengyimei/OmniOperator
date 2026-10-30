"""配置注册表：有界读取、CAS、跨进程互斥、原子写；不搬运用户数据。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ...config import RuntimePaths
from ..atomic import atomic_write_text
from ..filelock import FileLock
from .layout import AgentProfile, ApplicationDirectories, ApplicationLayout, overlap, resolve_application, resolve_layout


class DirectoryConflict(ValueError):pass


class Catalog(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version: int=1
    active: str='default'
    application: ApplicationDirectories=Field(default_factory=ApplicationDirectories)
    agents: list[AgentProfile]=Field(default_factory=lambda:[AgentProfile(id='default',name='默认 Agent')],min_length=1,max_length=32)


def read_catalog(path: Path) -> tuple[Catalog, str]:
    """纯读取供启动和离线诊断共用；缺失不创建文件，损坏不回退为空配置。"""
    path = Path(path)
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError('Agent 注册表路径不能包含链接')
    if not path.exists():return Catalog(), ''
    with path.open('rb') as stream:raw = stream.read(131073)
    if len(raw)>131072:raise ValueError('Agent 注册表过大')
    return Catalog.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def validate_catalog(data: Catalog, legacy: RuntimePaths, *, catalog: Path, isolated=False):
    """布局校验单点；CLI 只读检查无需创建运行对象或存储仓储。"""
    ids = [profile.id for profile in data.agents]
    if data.version!=1 or len(set(ids))!=len(ids) or 'default' not in ids or data.active not in ids:
        raise ValueError('Agent 注册表版本、唯一标识或默认选择无效')
    application = resolve_application(data.application, legacy, catalog=catalog, isolated=isolated)
    layouts = [resolve_layout(profile, application.paths, catalog=catalog, isolated=isolated,
                              application=application) for profile in data.agents]

    def owned(layout):
        p = layout.paths
        result = [p.memory,p.resident,p.history,p.state,p.file_history,p.cookies,p.browser_profile,
                  p.data/'plugins',p.skills,p.artifacts,p.cache,p.logs,p.command_logs,p.mcp_logs,
                  p.backups,p.files,p.documents]
        if layout.profile.directories.memory:result.append(p.memory.parent)
        if layout.profile.id!='default':result.extend([layout.home,p.workspace])
        elif layout.profile.directories.workspace or data.application.workspace:result.append(p.workspace)
        return [path for path in result if path is not None]

    for i,left in enumerate(layouts):
        for right in layouts[i+1:]:
            if any(overlap(a,b) for a in owned(left) for b in owned(right)):
                raise ValueError('不同 Agent 的空间或持久目录不能相互覆盖')
    default = next(layout for layout in layouts if layout.profile.id=='default')
    for path in owned(default):
        if overlap(application.agents_root,path):
            raise ValueError('Agent 空间池不能覆盖默认空间的工作区或持久目录')
    for control in (catalog.parent,legacy.run,legacy.service_logs or legacy.logs,legacy.config,legacy.env):
        if overlap(application.agents_root,control):
            raise ValueError('Agent 空间池不能覆盖固定控制目录')
    return application, layouts


class AgentDirectoryStore:
    def __init__(self,path: Path,legacy: RuntimePaths,*,isolated=False):
        self.path,self.legacy,self.isolated=Path(path),legacy,isolated

    def _read(self):
        data,revision=read_catalog(self.path)
        self.validate(data)
        return data,revision

    def validate(self,data: Catalog):
        return validate_catalog(data,self.legacy,catalog=self.path,isolated=self.isolated)[1]

    def application(self,data: Catalog) -> ApplicationLayout:
        return resolve_application(data.application,self.legacy,catalog=self.path,isolated=self.isolated)

    def layout(self,profile,*,application=None):
        application = application or self.application(Catalog())
        return resolve_layout(profile,application.paths,catalog=self.path,isolated=self.isolated,application=application)

    def snapshot(self):
        data,revision=self._read()
        return {'revision':revision,**data.model_dump(), 'application_resolved':self.application(data).describe(),
                'resolved':[layout.describe() for layout in self.validate(data)]}

    def selected(self,identifier=None):
        return self.select_runtime(identifier)[1]

    def select_runtime(self,identifier=None):
        """启动使用同一配置快照，避免并发保存混合两代路径。"""
        data,_=self._read();identifier=identifier or data.active
        profile=next((p for p in data.agents if p.id==identifier),None)
        if profile is None:raise ValueError('指定的 Agent 不存在')
        application,layouts=validate_catalog(data,self.legacy,catalog=self.path,isolated=self.isolated)
        return application,next(layout for layout in layouts if layout.profile.id==identifier)

    def _parse_update(self,data,current=None):
        if 'application' not in data:
            current = current or self._read()[0]
            if any(current.application.model_dump().values()):
                raise ValueError('旧客户端未包含全局目录设置，请重新加载')
        return Catalog.model_validate(data)

    def preview(self,data):
        parsed=self._parse_update(data);return [layout.describe() for layout in self.validate(parsed)]

    def preview_application(self,data):
        parsed=self._parse_update(data);self.validate(parsed)
        return self.application(parsed).describe()

    def save(self,data,*,revision,acknowledge=False):
        parsed=self._parse_update(data);self.validate(parsed)
        if not acknowledge:raise ValueError('请确认目录只在下次启动生效，旧数据不会自动移动')
        with FileLock(self.path.with_suffix('.lock')):
            existing,current=self._read()
            if revision!=current:raise DirectoryConflict('目录配置已被其它页面修改，请重新加载后再保存')
            self._parse_update(data,current=existing)
            atomic_write_text(self.path,json.dumps(parsed.model_dump(),ensure_ascii=False,indent=2)+'\n')
        return self.snapshot()
