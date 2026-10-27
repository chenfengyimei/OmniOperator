"""组装根（Composition Root）：全项目唯一负责装配的地方。

各入口（CLI / 冒烟脚本 / 未来的 QQ 通道、Web 面板）都从这里拿组装好的
Runtime 门面，禁止在通道代码里直接构造 Gateway / MemoryStore /
ToolRegistry / Agent——依赖关系只允许在本文件发生
（tests/test_arch.py 用 AST 强制这条规则）。
"""
from __future__ import annotations

import atexit
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import (
    CONFIG_PATH,
    DATA_DIR,
    AppConfig,
    QQ,
    RuntimePaths,
    load_config,
    load_qq_policy,
)
from .core.agent import Agent
from .core.session import ResourceLeases, SessionGate, SessionLeases
from .core.review import TurnEndReviewer
from .llm.gateway import Gateway
from .memory.state import ExecutionLog, JobStore, StateStore
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


def _no_port_probe(_port: int, _state: str) -> list[int]:
    """隔离实例不做端口探测：发现真机进程就等于把测试指向真实服务。"""
    return []


def _no_kill(_pid: int) -> None:
    # 用 RuntimeError 而不是 SystemExit：后者会穿透工具层直接退掉进程
    raise RuntimeError("隔离实例不允许终止本机进程")


def _no_spawn(*_args, **_kwargs) -> int:
    raise RuntimeError("隔离实例不允许启动本机服务")


@dataclass
class Runtime:
    """面向通道的门面（Facade）：通道只碰它，不碰内部对象的构造。"""

    config: AppConfig
    store: MemoryStore
    gateway: Gateway
    registry: ToolRegistry
    skills: SkillStore
    state: StateStore          # 共享状态库（任务/账本/额度/迁移）
    session_gate: SessionGate  # 进程内：同会话串行 + 排队有界
    session_leases: SessionLeases  # 跨进程：同会话同一时刻只有一个进程在跑
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
    data_dir: Path = DATA_DIR  # 运行数据根（工作台密钥/会话、截图、日志都从这里派生）
    paths: RuntimePaths = None  # 全部可写路径的唯一声明（见 config.RuntimePaths）

    def qq_policy(self) -> QQ:
        """当前 QQ 授权策略：每次从配置源重读，撤销白名单不必重启进程。"""
        return load_qq_policy(self.config_path)

    def close_mcp(self) -> None:
        """入口关闭时回收本 Runtime 的 MCP 子进程；不干扰其它服务。"""
        self.mcp.close()
        atexit.unregister(self.mcp.close)

    def close_state(self) -> None:
        """关闭共享状态库连接（测试与一次性命令用；常驻进程退出时由 OS 收尾）。"""
        self.state.close()

    def close_browser(self, *, wait_s: float | None = None) -> str:
        """关闭浏览器并在有界时间内回收线程与 profile 租约；返回可读结论。"""
        return self.browser.close(wait_s=wait_s)

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
    paths = RuntimePaths.create(data_root, config_path=config_path, skills_root=skills_root)
    memory_path = db_path if db_path is not None else data_root / "memory.db"
    store = MemoryStore(memory_path)
    gateway = Gateway(config.models)
    # 技能与常驻记忆共用一份修订存储：写入前先落历史，改坏了能回滚（D08）
    skills = SkillStore(
        paths.skills, history_root=paths.history, owner=f"pid:{os.getpid()}:skills"
    )
    registry = ToolRegistry()
    session_ref = SessionRef()  # Agent 与会话级工具（todo）共享的"当前会话"
    resident = ResidentMemory(
        paths.resident,
        char_limit=config.limits.resident_memory_chars,
        history_root=paths.history,
        owner=f"pid:{os.getpid()}:resident",
    )
    register_core_tools(registry, store)
    register_digest_tool(registry, store)
    register_todo_tools(registry, store, session_ref)
    register_resident_tools(registry, resident)
    agent_holder: dict[str, Any] = {}  # Agent 构造在后，delegate 工具运行期取用
    register_delegate_tools(registry, store, agent_holder)
    register_shell_tools(registry)
    register_skill_tools(registry, skills)
    mcp = MCPManager(config.mcp, log_dir=paths.mcp_logs)
    register_mcp_tools(registry, mcp)
    if mcp.servers():
        atexit.register(mcp.close)
    # Cookie 路径必须注入：否则临时实例会读到真机的 B 站登录态
    register_bilibili_tools(registry, BiliAPI(cookies_path=paths.cookies), config.asr)
    # 共享状态集中到 state.db（A09 跨进程丢写 / A10 损坏当空）：先迁移旧 JSON，
    # 缺失/合法空/损坏分别处理；损坏时只停用对应能力，绝不把已用额度归零。
    state = StateStore(paths.state)
    state.migrate_legacy(paths.data)
    session_gate = SessionGate(capacity=config.limits.session_queue_depth)
    session_leases = SessionLeases(
        state, ttl_s=config.limits.session_lease_ttl_s
    )
    cron_jobs = JobStore(state)
    cron_ledger = ExecutionLog(state)
    # 白名单以「读时为准」注入：任务新建与投递都取当前策略，不用启动时的快照
    def _allowed_qq_users() -> list[int]:
        return load_qq_policy(config_path).allowed_users

    register_cron_tools(registry, cron_jobs, _allowed_qq_users)
    # 浏览器：所有 Playwright 调用在一条专用线程里，profile 用 state.db 租约跨进程互斥
    browser = BrowserDriver(
        headless=config.browser.headless,
        slow_mo_ms=config.browser.slow_mo_ms,
        profile_dir=paths.browser_profile,
        shots_dir=paths.browser_shots,
        lease=ResourceLeases(state, owner=f"pid:{os.getpid()}"),
        lease_owner=f"pid:{os.getpid()}:browser",
    )
    write_budget = WriteBudget(state, max_per_day=config.browser.max_writes_per_day)
    register_browser_tools(registry, browser, write_budget)
    approvals = ApprovalBroker(timeout_s=config.browser.approval_timeout_s)
    # 隔离实例（注入了 data_dir）不探测也不终止真机进程：否则测试可能把真 NapCat 当残留杀掉
    isolated = data_dir is not None
    services = ServiceManager(
        napcat_dir=Path(config.system.napcat_dir) if config.system.napcat_dir else None,
        run_dir=paths.run, log_dir=paths.logs,
        port_prober=None if not isolated else _no_port_probe,
        killer=None if not isolated else _no_kill,
        spawner=None if not isolated else _no_spawn,
    )
    register_system_tools(registry, services)
    android_driver = AndroidDriver(
        cache_path=paths.android_cache / "tree.json", shots_dir=paths.android_shots
    )
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
        state=state,
        session_gate=session_gate,
        session_leases=session_leases,
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
        data_dir=data_root,
        paths=paths,
    )
