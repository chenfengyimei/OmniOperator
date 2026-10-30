"""本机 Agent 布局的声明与校验，不创建目录或打开数据。"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ...config import PROJECT_ROOT, RuntimePaths

PUBLIC = ('workspace', 'files', 'documents')
DIRECTORIES = (*PUBLIC, 'memory', 'skills', 'artifacts', 'cache', 'logs', 'backups')
RESERVED = {'con','prn','aux','nul',*(f'com{i}' for i in range(1,10)),*(f'lpt{i}' for i in range(1,10))}


class DirectoryOverrides(BaseModel):
    model_config = ConfigDict(extra='forbid', str_max_length=1024)
    workspace: str = ''
    files: str = ''
    documents: str = ''
    memory: str = ''
    skills: str = ''
    artifacts: str = ''
    cache: str = ''
    logs: str = ''
    backups: str = ''


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,31}$')
    name: str = Field(min_length=1, max_length=60)
    home: str = Field(default='',max_length=1024)
    directories: DirectoryOverrides = Field(default_factory=DirectoryOverrides)


class ApplicationDirectories(BaseModel):
    """全项目目录设置；空值保持已有安装的布局。"""
    model_config = ConfigDict(extra='forbid', str_max_length=1024, str_strip_whitespace=True, frozen=True)
    data_root: str = ''
    workspace: str = ''
    agents_root: str = ''


@dataclass(frozen=True)
class ApplicationLayout:
    settings: ApplicationDirectories
    data_root: Path
    workspace: Path
    agents_root: Path
    paths: RuntimePaths
    catalog: Path
    isolation_root: Path | None = None

    def describe(self):
        return {name: str(path) for name, path in {
            'data_root': self.data_root, 'workspace': self.workspace,
            'agents_root': self.agents_root, 'source_root': PROJECT_ROOT,
            'catalog': self.catalog, 'run': self.paths.run,
            'service_logs': self.paths.service_logs or self.paths.logs,
        }.items()}


def local_path(value: str | Path, base: Path) -> Path:
    text=str(value).strip().replace('\\','/')
    if not text or text.startswith('//') or any(ord(c)<32 for c in text):
        raise ValueError('目录必须是有效的本机路径，不接受网络或设备路径')
    if ':' in text[2:] or (':' in text and not re.match(r'^[a-zA-Z]:/',text)):
        raise ValueError('目录不能包含备用数据流或驱动器相对路径')
    parts=text.split('/')
    for part in parts:
        if part in {'.','..'} or (part and part.endswith((' ','.'))) or part.split('.')[0].lower() in RESERVED:
            raise ValueError('目录不能包含上级跳转、保留名或尾随空格/点')
        if any(c in part for c in '<>"|?*'):
            raise ValueError('目录包含无效字符')
    path=Path(text).expanduser()
    if not path.is_absolute():path=base/path
    for item in (path,*path.parents):
        if item.is_symlink() or item.is_junction():raise ValueError('目录链不能包含链接或 junction')
        if item.exists() and not item.is_dir():raise ValueError('目录位置已被文件占用')
    path=path.resolve()
    if path==Path(path.anchor):raise ValueError('不能把磁盘根目录作为 Agent 目录')
    return path


def overlap(a: Path,b: Path) -> bool:
    return a==b or a.is_relative_to(b) or b.is_relative_to(a)


def resolve_application(settings: ApplicationDirectories, legacy: RuntimePaths, *, catalog: Path,
                        isolated=False) -> ApplicationLayout:
    """固定安装基点解析业务路径；进程身份、配对及配置控制区不随业务根移动。"""
    anchor = legacy.data.resolve()
    root = local_path(settings.data_root or anchor, anchor.parent)
    workspace = local_path(settings.workspace or legacy.workspace or PROJECT_ROOT, root)
    agents_root = local_path(settings.agents_root or root / 'agents', root)
    if isolated:
        for value, path in ((settings.data_root, root), (settings.workspace, workspace),
                            (settings.agents_root, agents_root)):
            if value and not path.is_relative_to(anchor):
                raise ValueError('隔离运行的目录必须位于注入的 data_dir 内')
    moved = root != anchor
    if moved:
        if root == PROJECT_ROOT or PROJECT_ROOT.is_relative_to(root):
            raise ValueError('业务存储根不能覆盖程序源码目录')
        if root.is_relative_to(PROJECT_ROOT) and not root.is_relative_to(anchor):
            raise ValueError('源码树内的新存储根必须位于原有数据区')
        for control in (catalog.parent, legacy.run, legacy.service_logs or legacy.logs,
                        legacy.config, legacy.env):
            if overlap(root, control.resolve()):
                raise ValueError('业务存储根不能覆盖固定控制目录或配置文件')
    paths = replace(legacy, workspace=workspace)
    if moved:
        memory, logs, runtime, private = root/'memory', root/'logs', root/'runtime', root/'private'
        paths = replace(paths, data=runtime, memory=memory/'memory.db', resident=memory/'MEMORY.md',
                        history=memory/'history', state=runtime/'state.db', file_history=runtime/'file_history',
                        skills=root/'skills', files=root/'files', documents=root/'documents',
                        cache=root/'cache', android_cache=root/'cache'/'android',
                        artifacts=root/'artifacts', android_shots=root/'artifacts'/'android',
                        browser_shots=root/'artifacts'/'browser', cookies=private/'bili_cookies.json',
                        browser_profile=private/'browser_profile', logs=logs/'system',
                        command_logs=logs/'commands', mcp_logs=logs/'mcp', backups=root/'backups')
    return ApplicationLayout(settings, root, workspace, agents_root, paths, catalog,
                             anchor if isolated else None)


@dataclass(frozen=True)
class AgentLayout:
    profile: AgentProfile
    home: Path
    directories: dict[str,Path]
    paths: RuntimePaths

    def describe(self):
        return {'id':self.profile.id,'name':self.profile.name,'home':str(self.home),
                'directories':{name:str(path) for name,path in self.directories.items()},
                'managed':self.profile.id!='default'}


def resolve_layout(profile: AgentProfile, legacy: RuntimePaths, *, catalog: Path, isolated=False,
                   application: ApplicationLayout | None = None) -> AgentLayout:
    """配置路径只在入口解析一次；所有消费者拿同一份冻结路径。"""
    managed=profile.id!='default'
    root = application.data_root if application else legacy.data
    agents_root = application.agents_root if application else root/'agents'
    home=local_path(profile.home or (agents_root/profile.id if managed else root),root)
    if not managed and home!=root.resolve():raise ValueError('默认兼容空间根固定；请修改业务目录或新建独立 Agent')
    defaults={name:home/name for name in DIRECTORIES}
    if not managed:
        defaults.update(workspace=legacy.workspace or PROJECT_ROOT, files=legacy.files or home/'files',
                        documents=legacy.documents or home/'documents', memory=legacy.memory.parent,
                        skills=legacy.skills,artifacts=legacy.artifacts,cache=legacy.cache,
                        logs=legacy.logs.parent if legacy.logs.name=='system' else legacy.logs,backups=legacy.backups)
    roots={name:local_path(profile.directories.model_dump()[name] or defaults[name],home) for name in DIRECTORIES}
    # 目录设置不能把持久数据写进程序包、配置或进程控制区；默认兼容布局保留。
    changes=profile.directories.model_dump()
    for name,path in roots.items():
        if managed or changes[name]:
            for control in (legacy.config,legacy.env,legacy.run,legacy.service_logs or legacy.logs,catalog.parent):
                if overlap(path,control):raise ValueError(f'{name} 目录不能覆盖固定控制目录')
            if name not in PUBLIC and path.is_relative_to(PROJECT_ROOT) and not path.is_relative_to(
                application.isolation_root if application and application.isolation_root else PROJECT_ROOT/'data'
            ):
                raise ValueError(f'{name} 目录不能写入程序源码树，请使用数据区或外部目录')
    if isolated:
        for path in ([home,*roots.values()] if managed else [home,*[roots[n] for n in DIRECTORIES if profile.directories.model_dump()[n]]]):
            isolation_root = application.isolation_root if application else legacy.data.resolve()
            if not path.is_relative_to(isolation_root):raise ValueError('隔离运行的目录必须位于注入的 data_dir 内')
    # 公开区、持久区互不覆盖。默认兼容目录保留原有 data/ 和 skills/ 保护。
    for i,name in enumerate(DIRECTORIES):
        for other in DIRECTORIES[i+1:]:
            if not managed and not profile.directories.workspace and 'workspace' in (name,other):
                private=roots[other if name=='workspace' else name]
                if private.is_relative_to(root.resolve()) and private.is_relative_to(roots['workspace']) and 'data' in private.relative_to(roots['workspace']).parts:
                    continue # 兼容空间原有 data/ 禁区可继续容纳自定义持久目录。
            if overlap(roots[name],roots[other]) and (managed or profile.directories.model_dump()[name] or profile.directories.model_dump()[other]):
                raise ValueError(f'{name} 与 {other} 目录相互覆盖，请分别设置')
    if managed:
        for name,path in roots.items():
            for private in (home/'runtime',home/'private',legacy.config,legacy.config.parent.parent/'.env',legacy.run,catalog.parent):
                if overlap(path,private):raise ValueError(f'{name} 目录不能覆盖内部数据或控制目录')
    m=roots['memory'];log=roots['logs'];art=roots['artifacts'];cache=roots['cache']
    mapped=dict(workspace=roots['workspace'],files=roots['files'],documents=roots['documents'],agent_id=profile.id,catalog=catalog)
    if managed:
        mapped.update(data=home/'runtime',state=home/'runtime'/'state.db',memory=m/'memory.db',resident=m/'MEMORY.md',history=m/'history',
                      file_history=home/'runtime'/'file_history',cookies=home/'private'/'bili_cookies.json',skills=roots['skills'],cache=cache,
                      android_shots=art/'android',android_cache=cache/'android',browser_profile=home/'private'/'browser_profile',
                      browser_shots=art/'browser',artifacts=art,logs=log/'system',command_logs=log/'commands',mcp_logs=log/'mcp',backups=roots['backups'])
    else:
        changes=profile.directories.model_dump()
        if changes['memory']:mapped.update(memory=m/'memory.db',resident=m/'MEMORY.md',history=m/'history')
        for name in ('skills','cache','artifacts','backups'):
            if changes[name]:mapped[name]=roots[name]
        if changes['logs']:mapped.update(logs=log/'system',command_logs=log/'commands',mcp_logs=log/'mcp')
        if changes['cache']:mapped['android_cache']=cache/'android'
        if changes['artifacts']:mapped.update(android_shots=art/'android',browser_shots=art/'browser')
    paths = replace(legacy,**mapped)
    # 未配置的旧项目工作区保留 data/ 名称保护；任何新增公开根均按实际路径判定，
    # 不依赖业务根恰好叫 data。私有区域不能通过 files/documents/workspace 暴露。
    private_paths = [paths.memory, paths.resident, paths.history, paths.state, paths.file_history, paths.skills,
                     paths.cookies, paths.browser_profile, paths.cache, paths.artifacts, paths.logs,
                     paths.command_logs, paths.mcp_logs, paths.backups, paths.data/'plugins',
                     legacy.run, legacy.service_logs or legacy.logs, catalog.parent, legacy.env]
    for name in PUBLIC:
        public = roots[name]
        for private in private_paths:
            if not overlap(public, private):continue
            inherited = name=='workspace' and not managed and not profile.directories.workspace and not (
                application and application.settings.workspace)
            if inherited and private.is_relative_to(public) and (
                'data' in private.relative_to(public).parts or private == legacy.env or private == catalog.parent
                or (private==paths.skills and private!=public and private.relative_to(public).parts[0]=='skills')):
                continue
            raise ValueError(f'{name} 目录不能覆盖内部数据或控制目录')
    return AgentLayout(profile,home,roots,paths)
