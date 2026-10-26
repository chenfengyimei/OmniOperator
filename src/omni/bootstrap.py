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
from .core.review import TurnEndReviewer
from .llm.gateway import Gateway
from .memory.skills import SkillStore
from .memory.store import MemoryStore
from .safety.gates import Approver
from .tools.bilibili.api import BiliAPI
from .tools.bilibili.tools import register_bilibili_tools
from .tools.builtin import (
    register_core_tools,
    register_memory_tools,
    register_profile_tools,
)
from .tools.registry import ToolRegistry
from .tools.skill_tools import register_skill_tools


@dataclass
class Runtime:
    """面向通道的门面（Facade）：通道只碰它，不碰内部对象的构造。"""

    config: AppConfig
    store: MemoryStore
    gateway: Gateway
    registry: ToolRegistry
    skills: SkillStore
    agent: Agent
    reviewer: TurnEndReviewer | None = None

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

    def spawn_review(self) -> None:
        """回合后异步记忆整理（未启用则为空操作）。通道在回答后调用即可，不阻塞。"""
        if self.reviewer is not None:
            self.reviewer.review_in_background(self.agent.session_id)


def build_runtime(
    *,
    approver: Approver | None = None,
    db_path: Path | None = None,
    config_path: Path | None = None,
    skills_root: Path | None = None,
) -> Runtime:
    config = load_config(config_path)
    store = MemoryStore(db_path)
    gateway = Gateway(config.models)
    skills = SkillStore(skills_root)
    registry = ToolRegistry()
    register_core_tools(registry, store)
    register_skill_tools(registry, skills)
    register_bilibili_tools(registry, BiliAPI())
    session_id = store.latest_session_id() or store.new_session()
    agent = Agent(config, gateway, store, registry, session_id, approver=approver)

    # 回合后 review：白名单（最小权限：记忆/档案/技能）在这里定义，
    # 属策略接线；store/registry 工厂保证每次 review 用独立连接与注册表
    # （技能是文件存储，SkillStore 实例线程安全，可直接共享）。
    def _review_registry(review_store: MemoryStore) -> ToolRegistry:
        review_registry = ToolRegistry()
        register_memory_tools(review_registry, review_store)
        register_profile_tools(review_registry, review_store)
        register_skill_tools(review_registry, skills)
        return review_registry

    reviewer = (
        TurnEndReviewer(
            config,
            gateway,
            lambda: MemoryStore(db_path),
            _review_registry,
        )
        if config.review.enabled
        else None
    )
    return Runtime(
        config=config,
        store=store,
        gateway=gateway,
        registry=registry,
        skills=skills,
        agent=agent,
        reviewer=reviewer,
    )
