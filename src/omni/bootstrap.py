"""组装根（Composition Root）：全项目唯一负责装配的地方。

各入口（CLI / 冒烟脚本 / 未来的 QQ 通道、Web 面板）都从这里拿组装好的
Runtime 门面，禁止在通道代码里直接构造 Gateway / MemoryStore /
ToolRegistry / Agent——依赖关系只允许在本文件发生
（tests/test_arch.py 用 AST 强制这条规则）。
"""
from __future__ import annotations

import atexit
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CONFIG_PATH, DATA_DIR, AppConfig, QQ, load_config, load_qq_policy
from .core.agent import Agent
from .core.review import TurnEndReviewer
from .llm.gateway import Gateway
from .memory.cron import ExecutionLog, JobStore
from .memory.resident import ResidentMemory
from .memory.skills import SkillStore
from .memory.store import MemoryStore
from .safety.broker import ApprovalBroker
from .safety.gates import Approver
from .system.services import ServiceManager
from .tools.bilibili.api import BiliAPI
from .tools.bilibili.tools import register_bilibili_tools
from .tools.browser.budget import WriteBudget
from .tools.browser.driver import BrowserDriver
from .tools.browser.tools import register_browser_tools
from .tools.builtin import (
    register_core_tools,
    register_digest_tool,
    register_memory_tools,
    register_profile_tools,
    register_resident_tools,
    register_todo_tools,
)
from .tools.cron_tools import register_cron_tools
from .tools.delegate_tools import register_delegate_tools
from .tools.registry import SessionRef, ToolRegistry
from .tools.android.driver import AndroidDriver
from .tools.android.tools import register_android_tools
from .tools.qq.client import OneBotClient
from .tools.qq.tools import register_qq_tools
from .tools.shell_tools import register_shell_tools
from .tools.skill_tools import register_skill_tools
from .tools.system_tools import register_system_tools
from .tools.mcp.client import MCPManager
from .tools.mcp.tools import register_mcp_tools


@dataclass
class Runtime:
    """面向通道的门面（Facade）：通道只碰它，不碰内部对象的构造。"""

    config: AppConfig
    store: MemoryStore
    gateway: Gateway
    registry: ToolRegistry
    skills: SkillStore
    cron_jobs: JobStore
    cron_ledger: ExecutionLog
    resident: ResidentMemory
    browser: BrowserDriver
    write_budget: WriteBudget
    approvals: ApprovalBroker
    services: ServiceManager
    agent: Agent
    android: AndroidDriver
    mcp: MCPManager
    reviewer: TurnEndReviewer | None = None
    qq: OneBotClient | None = None
    config_path: Path | None = None  # 策略热读用的配置源（None=默认 config.yaml）

    def qq_policy(self) -> QQ:
        """当前 QQ 授权策略：每次从配置源重读，撤销白名单不必重启进程。"""
        return load_qq_policy(self.config_path)

    def close_mcp(self) -> None:
        """入口关闭时回收本 Runtime 的 MCP 子进程；不干扰其它服务。"""
        self.mcp.close()
        atexit.unregister(self.mcp.close)

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

    def spawn_review(self, session_id: int | None = None) -> None:
        """回合后异步记忆整理（未启用则为空操作）；通道可传绑定会话。"""
        if self.reviewer is not None:
            self.reviewer.review_in_background(
                session_id if session_id is not None else self.agent.session_id
            )


def build_runtime(
    *,
    approver: Approver | None = None,
    db_path: Path | None = None,
    config_path: Path | None = None,
    skills_root: Path | None = None,
    data_dir: Path | None = None,
) -> Runtime:
    # 记住实际读取的配置源：策略热读必须命中同一个文件，否则会读到真机配置
    config_path = Path(config_path) if config_path else CONFIG_PATH
    config = load_config(config_path)
    data_root = Path(data_dir) if data_dir else DATA_DIR
    memory_path = db_path if db_path is not None else data_root / "memory.db"
    store = MemoryStore(memory_path)
    gateway = Gateway(config.models)
    skills = SkillStore(skills_root)
    registry = ToolRegistry()
    session_ref = SessionRef()  # Agent 与会话级工具（todo）共享的"当前会话"
    resident = ResidentMemory(
        data_root / "MEMORY.md", char_limit=config.limits.resident_memory_chars
    )
    register_core_tools(registry, store)
    register_digest_tool(registry, store)
    register_todo_tools(registry, store, session_ref)
    register_resident_tools(registry, resident)
    agent_holder: dict[str, Any] = {}  # Agent 构造在后，delegate 工具运行期取用
    register_delegate_tools(registry, store, agent_holder)
    register_shell_tools(registry)
    register_skill_tools(registry, skills)
    mcp = MCPManager(config.mcp, log_dir=data_root / "mcp_logs")
    register_mcp_tools(registry, mcp)
    if mcp.servers():
        atexit.register(mcp.close)
    register_bilibili_tools(registry, BiliAPI(), config.asr)
    cron_jobs = JobStore(data_root / "cron_jobs.json")
    cron_ledger = ExecutionLog(data_root / "cron_executions.json")
    # 白名单以「读时为准」注入：任务新建与投递都取当前策略，不用启动时的快照
    def _allowed_qq_users() -> list[int]:
        return load_qq_policy(config_path).allowed_users

    register_cron_tools(registry, cron_jobs, _allowed_qq_users)
    browser = BrowserDriver(
        headless=config.browser.headless, slow_mo_ms=config.browser.slow_mo_ms,
        profile_dir=data_root / "browser_profile", shots_dir=data_root / "browser_shots",
    )
    write_budget = WriteBudget(data_root / "browser_budget.json", max_per_day=config.browser.max_writes_per_day)
    register_browser_tools(registry, browser, write_budget)
    approvals = ApprovalBroker(timeout_s=config.browser.approval_timeout_s)
    services = ServiceManager(
        napcat_dir=Path(config.system.napcat_dir) if config.system.napcat_dir else None,
        run_dir=data_root / "run", log_dir=data_root / "system_logs",
    )
    register_system_tools(registry, services)
    android_driver = AndroidDriver(cache_path=data_root / "android_cache" / "tree.json")
    register_android_tools(registry, android_driver)
    qq_client: OneBotClient | None = None
    if config.qq.enabled:
        qq_client = OneBotClient(config.qq.ws_url, config.qq.access_token)
        register_qq_tools(registry, qq_client, _allowed_qq_users)
    session_id = store.latest_session_id() or store.new_session()
    agent = Agent(
        config, gateway, store, registry, session_id,
        approver=approver, session_ref=session_ref, resident=resident,
    )
    agent_holder["agent"] = agent

    # 回合后 review：白名单（最小权限：记忆/档案/常驻笔记/技能）在这里定义，
    # 属策略接线；store/registry 工厂保证每次 review 用独立连接与注册表
    # （技能是文件存储、常驻记忆自带锁，实例线程安全，可直接共享）。
    def _review_registry(review_store: MemoryStore) -> ToolRegistry:
        review_registry = ToolRegistry()
        register_memory_tools(review_registry, review_store)
        register_profile_tools(review_registry, review_store)
        register_resident_tools(review_registry, resident)
        register_skill_tools(review_registry, skills)
        return review_registry

    reviewer = (
        TurnEndReviewer(
            config,
            gateway,
            lambda: MemoryStore(memory_path),
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
        cron_jobs=cron_jobs,
        cron_ledger=cron_ledger,
        resident=resident,
        browser=browser,
        write_budget=write_budget,
        approvals=approvals,
        services=services,
        agent=agent,
        android=android_driver,
        mcp=mcp,
        reviewer=reviewer,
        qq=qq_client,
        config_path=config_path,
    )
