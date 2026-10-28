"""只读项目目录、接口与实时能力；由 web.py 的统一鉴权中间件保护。"""
from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from ...config import PROJECT_ROOT
from ...system.catalog import build_index
from ...system.catalog.schema import MODULES

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_workbench_router(runtime: Runtime, *, source_root: Path = PROJECT_ROOT) -> APIRouter:
    router = APIRouter(tags=['project-workbench'])
    cache: dict = {}
    lock = threading.Lock()

    def index() -> dict:
        with lock:
            if time.monotonic() >= cache.get('until', 0):
                try:
                    result = build_index(source_root)
                except (OSError, ValueError, SyntaxError):
                    raise HTTPException(503, '项目源码暂不可索引，请检查源码完整性') from None
                cache.update(data=result, until=time.monotonic() + 5)
            return cache['data']

    @router.get('/api/workbench/project')
    def project() -> dict:
        return index()

    @router.get('/api/workbench/docs/{name}')
    def document(name: str) -> dict:
        allowed = {'PROJECT_INDEX.md', 'ARCHITECTURE.md', *(doc for module in MODULES for doc in module.docs)}
        path = source_root / 'docs' / name
        if name not in allowed or not path.is_file():
            raise HTTPException(404, '没有这份索引文档')
        if (path.is_symlink() or path.parent.is_symlink() or path.parent.is_junction()
                or not path.resolve().is_relative_to((source_root / 'docs').resolve()) or path.stat().st_size > 500_000):
            raise HTTPException(404, '这份文档不可读取')
        return {'name': name, 'text': path.read_text(encoding='utf-8')}

    @router.get('/api/workbench/tools')
    def tools() -> dict:
        sources = {tool['name']: {'path': tool['source'], 'line': tool['line'], 'module': tool['module']}
                   for tool in index()['declared_tools']}
        return {'items': [{**item, 'source': sources.get(item['name'])} for item in runtime.registry.describe()],
                'scope': 'current-process', 'note': '当前进程实际注册的工具；MCP 动态目录需在原 MCP 面板显式发现。'}

    @router.get('/api/workbench/extensions')
    def extensions() -> dict:
        host = runtime.plugins
        def select(rows, fields):
            return [{key: row.get(key) for key in fields} for row in rows[:100]]
        return {'plugins': select(host.installed(), ('id', 'name', 'version', 'description', 'enabled', 'tools')) if host else [],
                'pending': select(host.pending(), ('id', 'name', 'version', 'description')) if host else [],
                'contracts': [
                    {'kind': 'tool', 'title': '内置工具', 'entry': 'tools/<域>/ → register_xxx_tools → bootstrap', 'action': '通过对话按现有审批使用'},
                    {'kind': 'plugin', 'title': 'Python 插件', 'entry': 'manifest.json + plugin.py → 隔离 → 人工激活', 'action': 'plugin_install / plugin_activate / plugin_list'},
                    {'kind': 'skill', 'title': '技能与参考数据', 'entry': 'SKILL.md + references/ → 校验 → 激活', 'action': 'skill_import / skill_activate / skill_pending'},
                    {'kind': 'mcp', 'title': '进程外 MCP', 'entry': '显式配置 → 工具目录 → 权限复核', 'action': '设置中心 MCP；读取目录会连接服务'},
                    {'kind': 'community', 'title': '插件社区', 'entry': '搜索/详情 → 校验下载 → 本机隔离激活', 'action': 'community_search / community_install；发布继续人工批准'},
                ]}

    return router
