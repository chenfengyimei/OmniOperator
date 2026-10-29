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


def resolve_layout(profile: AgentProfile, legacy: RuntimePaths, *, catalog: Path, isolated=False) -> AgentLayout:
    """配置路径只在入口解析一次；所有消费者拿同一份冻结路径。"""
    managed=profile.id!='default'
    home=local_path(profile.home or (legacy.data/'agents'/profile.id if managed else legacy.data),legacy.data)
    if not managed and home!=legacy.data.resolve():raise ValueError('默认兼容空间根固定；请修改业务目录或新建独立 Agent')
    defaults={name:home/name for name in DIRECTORIES}
    if not managed:
        defaults.update(workspace=legacy.workspace or PROJECT_ROOT,memory=legacy.memory.parent,
                        skills=legacy.skills,artifacts=legacy.artifacts,cache=legacy.cache,
                        logs=legacy.logs,backups=legacy.backups)
    roots={name:local_path(profile.directories.model_dump()[name] or defaults[name],home) for name in DIRECTORIES}
    if isolated:
        for path in ([home,*roots.values()] if managed else [home,*[roots[n] for n in DIRECTORIES if profile.directories.model_dump()[n]]]):
            if not path.is_relative_to(legacy.data.resolve()):raise ValueError('隔离运行的目录必须位于注入的 data_dir 内')
    # 公开区、持久区互不覆盖。默认兼容目录保留原有 data/ 和 skills/ 保护。
    for i,name in enumerate(DIRECTORIES):
        for other in DIRECTORIES[i+1:]:
            if not managed and not profile.directories.workspace and 'workspace' in (name,other):
                private=roots[other if name=='workspace' else name]
                if private.is_relative_to(legacy.data.resolve()) and private.is_relative_to(roots['workspace']) and 'data' in private.relative_to(roots['workspace']).parts:
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
    return AgentLayout(profile,home,roots,replace(legacy,**mapped))
