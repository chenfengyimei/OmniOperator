"""组装根（Composition Root）：全项目唯一负责装配的地方。

各入口（CLI / 冒烟脚本 / 未来的 QQ 通道、Web 面板）都从这里拿组装好的
Runtime 门面，禁止在通道代码里直接构造 Gateway / MemoryStore /
ToolRegistry / Agent——依赖关系只允许在本文件发生
（tests/test_arch.py 用 AST 强制这条规则）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig, load_config
from .core.agent import Agent
from .llm.gateway import Gateway
from .memory.store import MemoryStore
from .safety.gates import Approver
from .tools.builtin import register_core_tools
from .tools.registry import ToolRegistry


@dataclass
class Runtime:
    """面向通道的门面（Facade）：通道只碰它，不碰内部对象的构造。"""

    config: AppConfig
    store: MemoryStore
    gateway: Gateway
    registry: ToolRegistry
    agent: Agent

    def new_session(self) -> int:
        """新开会话并把 agent 切过去。"""
        self.agent.session_id = self.store.new_session()
        return self.agent.session_id

    def use_session(self, session_id: int) -> bool:
        """切换到既有会话；不存在则返回 False。"""
        if self.store.session_exists(session_id):
            self.agent.session_id = session_id
            return True
        return False


def build_runtime(
    *,
    approver: Approver | None = None,
    db_path: Path | None = None,
    config_path: Path | None = None,
) -> Runtime:
    config = load_config(config_path)
    store = MemoryStore(db_path)
    gateway = Gateway(config.models)
    registry = ToolRegistry()
    register_core_tools(registry, store)
    session_id = store.latest_session_id() or store.new_session()
    agent = Agent(config, gateway, store, registry, session_id, approver=approver)
    return Runtime(config=config, store=store, gateway=gateway, registry=registry, agent=agent)
