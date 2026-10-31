"""Agent 目录注册与纯路径解析；执行对象仍由 bootstrap 构造。"""
from .catalog import AgentDirectoryStore, DirectoryConflict
from .layout import (
           AgentProfile,
           ApplicationDirectories,
           ApplicationLayout,
           DirectoryOverrides,
           resolve_application,
           resolve_layout,
)

__all__ = ['AgentDirectoryStore', 'DirectoryConflict', 'AgentProfile', 'ApplicationDirectories',
           'ApplicationLayout', 'DirectoryOverrides', 'resolve_application', 'resolve_layout']
