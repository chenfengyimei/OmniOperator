"""组装根（Composition Root）：全项目唯一负责装配的地方。

各入口（CLI / 冒烟脚本 / 未来的 QQ 通道、Web 面板）都从这里拿组装好的
Runtime 门面，禁止在通道代码里直接构造 Gateway / MemoryStore /
ToolRegistry / Agent——依赖关系只允许在本文件发生
（tests/test_arch.py 用 AST 强制这条规则）。
"""
from __future__ import annotations

import atexit
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .config import (
    CONFIG_PATH,
    DATA_DIR,
    PROJECT_ROOT,
    QQ,
    AppConfig,
    RuntimePaths,
    load_config,
    load_qq_policy,
)
from .core.agent import Agent
from .core.journal import JournalConsolidator
from .core.retrieval import HybridMemory
from .core.review import TurnEndReviewer
from .core.router import ModeRouter
from .core.run import ActiveRuns
from .core.session import ResourceLeases, SessionGate, SessionLeases
from .llm.embedding import EmbeddingProvider
from .llm.gateway import Gateway
from .memory.artifacts import ArtifactStore
from .memory.command_jobs import CommandJobStore
from .memory.dialogue import DialogueLog
from .memory.guard import MemoryGuard
from .memory.journal import JournalStore
from .memory.owner import OwnerMemory
from .memory.owner_migrate import migrate_owner_memory
from .memory.people import PersonStore
from .memory.publications import PublicationStore
from .memory.qq import QQMessageStore
from .memory.runs import RunStore
from .memory.session_log import SessionLog
from .memory.skill_inbox import SkillInbox
from .memory.skills import SkillStore
from .memory.state import (
    ExecutionLog,
    JobStore,
    MCPSchemaStore,
    OutboxStore,
    SkillAckStore,
    StateStore,
)
from .memory.store import MemoryStore
from .memory.system import MemorySystem
from .memory.usage import UsageLedger
from .memory.vectors import VectorIndex
from .plugins import PluginHost
from .plugins.community import CommunityClient, register_community_tools
from .plugins.tools import register_plugin_tools
from .safety.broker import ApprovalBroker
from .safety.gates import Approver
from .system.agent_spaces import AgentDirectoryStore, ApplicationLayout
from .system.agent_spaces.inventory import private_paths
from .system.backup import backup_runtime
from .system.doctor import diagnose
from .system.observability import BoundedLog, EventLog
from .system.services import ServiceManager
from .tools.android.driver import AndroidDriver
from .tools.android.stream import AndroidFrameHub
from .tools.android.tools import register_android_tools
from .tools.bilibili.api import BiliAPI
from .tools.bilibili.publishing import BilibiliPublisher
from .tools.bilibili.tools import register_bilibili_tools
from .tools.bilibili.write_tools import register_bilibili_write_tools
from .tools.browser.budget import WriteBudget
from .tools.browser.driver import BrowserDriver
from .tools.browser.forms import BrowserForms
from .tools.browser.tools import register_browser_tools
from .tools.delegation import register_delegate_tools
from .tools.desktop.driver import DesktopDriver
from .tools.desktop.native import WindowsDesktopBackend
from .tools.desktop.tools import register_desktop_tools
from .tools.files import FileHistory, Workspace, register_file_tools
from .tools.mcp.client import MCPManager
from .tools.mcp.tools import register_mcp_tools
from .tools.personal import (
    register_core_tools,
    register_digest_tool,
    register_memory_tools,
    register_profile_tools,
    register_refine_tool,
    register_resident_tools,
    register_todo_tools,
)
from .tools.personal.journal import register_journal_tools
from .tools.personal.people import register_people_tools
from .tools.personal.transcript import register_transcript_tools
from .tools.qq.client import OneBotClient
from .tools.qq.tools import register_qq_tools
from .tools.registry import SessionRef, ToolRegistry
from .tools.runctx import RunRef
from .tools.scheduling import register_cron_tools
from .tools.shell import register_shell_tools
from .tools.shell.job_tools import register_command_job_tools
from .tools.shell.jobs import CommandJobManager
from .tools.skills import register_skill_inbox_tools, register_skill_tools
from .tools.system import register_system_tools
from .tools.web import FetchPolicy, register_web_tools


def _no_port_probe(_port: int, _state: str) -> list[int]:
    """隔离实例不做端口探测：发现真机进程就等于把测试指向真实服务。"""
    return []


def _no_kill(_pid: int) -> None:
    # 用 RuntimeError 而不是 SystemExit：后者会穿透工具层直接退掉进程
    raise RuntimeError("隔离实例不允许终止本机进程")


def _no_process_probe(_pid: int):
    return None


def _no_tcp_probe(_port: int) -> bool:
    return False


def _no_spawn(*_args, **_kwargs) -> int:
    raise RuntimeError("隔离实例不允许启动本机服务")


def _resolve_root(value: str | Path) -> Path:
    """配置里的根：相对路径按项目根解析，不按当前工作目录（服务可能从别处启动）。"""
    path = Path(value).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path)


@dataclass
class Runtime:
    """面向通道的门面（Facade）：通道只碰它，不碰内部对象的构造。"""

    config: AppConfig
    store: MemoryStore
    gateway: Gateway
    registry: ToolRegistry
    skills: SkillStore
    state: StateStore          # 共享状态库（任务/账本/额度/迁移/运行）
    usage: UsageLedger         # 模型调用流水与当日费用账（D10）
    runs: RunStore             # 运行记录与事件流
    active_runs: ActiveRuns    # 本进程内可取消的运行
    session_gate: SessionGate  # 进程内：同会话串行 + 排队有界
    session_leases: SessionLeases  # 跨进程：同会话同一时刻只有一个进程在跑
    cron_jobs: JobStore
    cron_ledger: ExecutionLog
    resident: Any  # OwnerMemory 门面（数据层统一后=owner 人物记忆；block/usage/entries 同形）
    browser: BrowserDriver
    write_budget: WriteBudget
    approvals: ApprovalBroker
    services: ServiceManager
    agent: Agent
    android: AndroidDriver
    mcp: MCPManager
    reviewer: TurnEndReviewer | None = None
    journal_consolidator: JournalConsolidator | None = None  # 记忆系统整理 worker（M3–M5）
    memory_guard: MemoryGuard | None = None  # 记忆写入额度与输入检查（D12）
    owner_memory: OwnerMemory | None = None  # 所有者记忆门面（web 端点用；数据层统一）
    workspace: Workspace | None = None        # 文件工具的允许根与敏感项边界（D13）
    file_history: FileHistory | None = None   # 工作区文件的写入备份（D13）
    artifacts: ArtifactStore | None = None    # 产物存储（D15）
    outbox: OutboxStore | None = None         # 投递 outbox：QQ 掉线不丢心跳结果（D17）
    mcp_schemas: MCPSchemaStore | None = None # MCP 工具目录指纹：漂移提醒（D19）
    skill_acks: SkillAckStore | None = None   # 技能变更已读标记：待审阅队列（D18）
    skill_inbox: SkillInbox | None = None     # 外来技能包收件箱/隔离区（技能中心复用）
    qq: OneBotClient | None = None
    retrieval: HybridMemory | None = None
    memory_system: MemorySystem | None = None  # 记忆系统门面（M1–M6 聚合）
    desktop: DesktopDriver | None = None
    qq_messages: QQMessageStore | None = None
    publications: PublicationStore | None = None
    event_log: EventLog | None = None
    android_streams: AndroidFrameHub | None = None
    command_jobs: CommandJobManager | None = None
    plugins: PluginHost | None = None
    community: CommunityClient | None = None  # 社区源客户端（插件索引 + 人设卡，工作台市场用）
    config_path: Path | None = None  # 策略热读用的配置源（None=默认 config.yaml）
    data_dir: Path = DATA_DIR  # 运行数据根（工作台密钥/会话、截图、日志都从这里派生）
    paths: RuntimePaths = None  # 全部可写路径的唯一声明（见 config.RuntimePaths）
    directories: AgentDirectoryStore | None = None
    public_workspaces: dict[str, Workspace] = field(default_factory=dict)
    agent_layout: Any = None
    application_layout: ApplicationLayout | None = None
    _close_threads: dict = field(default_factory=dict, repr=False)
    _close_results: dict = field(default_factory=dict, repr=False)
    _close_lock: object = field(default_factory=threading.Lock, repr=False)
    _fully_closed: bool = field(default=False, repr=False)

    def qq_policy(self) -> QQ:
        """当前 QQ 授权策略：每次从配置源重读，撤销白名单不必重启进程。"""
        return load_qq_policy(self.config_path)

    def close_mcp(self, wait_s: float = 10) -> bool:
        """入口关闭时回收本 Runtime 的 MCP 子进程；不干扰其它服务。"""
        done = self.mcp.close(wait_s)
        if done:
            atexit.unregister(self.mcp.close)
        return done

    def doctor(self) -> dict:
        report = diagnose(self.paths, inspector=self.services._inspect)
        report["runtime"] = {"active_runs": len(self.active_runs.contexts()),
                             "approval_waiting": len(self.approvals.list_pending()),
                             "review_queue": self.reviewer.queue_depth if self.reviewer else 0,
                             "closing": getattr(self.agent, "closing", False)}
        try:
            report['metrics'] = self.runs.metrics()
            report['metrics']['log_dropped'] = self.event_log.log.dropped if self.event_log else 0
        except Exception as exc:  # noqa: BLE001, S110  （有意容错：边界转换/生命周期清理不阻断）
            report['metrics'] = {'unavailable': type(exc).__name__}
        return report

    def backup(self) -> dict:
        """显式用户动作生成新目录备份；默认脱敏配置凭据，不切换运行数据。"""
        name = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        return backup_runtime(self.paths, self.paths.backups / name).to_dict()

    def close(self, wait_s: float = 10) -> dict:
        """有界统一关闭；残留 worker 未结束时保留数据库，允许稍后再次核验。"""
        deadline = time.monotonic() + max(0, wait_s)
        if not self._close_lock.acquire(timeout=max(0, wait_s)):
            return {"complete": False, "residual": ["close_in_progress"]}
        try:
            if self._fully_closed:
                return {"complete": True, "residual": []}
            self.agent.closing = True
            self.active_runs.cancel_all("运行态关闭")
            self.approvals.close()
            remaining = lambda: max(0, deadline - time.monotonic())
            def browser_done():
                result = self.close_browser(wait_s=remaining())
                return "已关闭" in result or "未启动" in result
            actions = {"review": lambda: self.close_reviewer(remaining()),
                       "retrieval": lambda: self.close_retrieval(remaining()),
                       "mcp": lambda: self.close_mcp(remaining()), "browser": browser_done,
                       "command_jobs": lambda: self.command_jobs.close(remaining())["complete"] if self.command_jobs else True,
                       "android_streams": lambda: self.android_streams.close(remaining())["complete"] if self.android_streams else True}
            def perform(name, action):
                try:
                    self._close_results[name] = bool(action())
                except Exception:  # noqa: BLE001, S110  （有意容错：边界转换/生命周期清理不阻断）
                    self._close_results[name] = False
            for name, action in actions.items():
                thread = self._close_threads.get(name)
                if self._close_results.get(name) or thread and thread.is_alive():
                    continue
                thread = threading.Thread(target=perform, args=(name, action), name=f"close-{name}", daemon=True)
                self._close_threads[name] = thread
                thread.start()
            for thread in self._close_threads.values():
                thread.join(remaining())
            while self.active_runs.contexts() and remaining() > 0:
                time.sleep(min(0.02, remaining()))
            residual = [name for name in actions if not self._close_results.get(name) or self._close_threads[name].is_alive()]
            if self.active_runs.contexts():
                residual.append("active_runs")
            if not residual:
                self.store.close()
                if self.memory_system is not None:
                    self.memory_system.close()
                self.state.close()
                self._fully_closed = True
            return {"complete": not residual, "residual": residual}
        finally:
            self._close_lock.release()

    def close_reviewer(self, wait_s: float = 5.0) -> bool:
        """停掉 review worker（有界队列，最多等 wait_s）；返回是否在时限内结束。"""
        if self.reviewer is None:
            return True
        return self.reviewer.close(wait_s=wait_s)

    def close_state(self) -> None:
        """关闭共享状态库连接（测试与一次性命令用；常驻进程退出时由 OS 收尾）。"""
        self.state.close()

    def close_retrieval(self, wait_s: float = 5) -> bool:
        if self.retrieval is None:
            return True
        done = self.retrieval.close(wait_s)
        if done:
            atexit.unregister(self.retrieval.close)
            self.retrieval = None
        return done

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
    owner_kind: str = "cli",
    start_background_workers: bool = True,
    agent_id: str | None = None,
    directory_catalog: Path | None = None,
) -> Runtime:
    # 记住实际读取的配置源：策略热读必须命中同一个文件，否则会读到真机配置
    config_path = Path(config_path or (os.environ.get('OMNI_CONFIG_PATH') if data_dir is None else '') or CONFIG_PATH)
    config_path=config_path.resolve()
    config = load_config(config_path)
    data_root = Path(data_dir or os.environ.get('OMNI_STORAGE_BASE') or DATA_DIR)
    data_root=data_root.resolve()
    paths = RuntimePaths.create(data_root, config_path=config_path, skills_root=skills_root, memory_path=db_path)
    paths=replace(paths,workspace=_resolve_root(config.files.root) if config.files.root else PROJECT_ROOT)
    catalog_path=Path(directory_catalog or (data_root/'control'/'agents.json' if data_dir is not None else os.environ.get('OMNI_AGENT_CATALOG') or config_path.with_name('agents.json')))
    directories=AgentDirectoryStore(catalog_path,paths,isolated=data_dir is not None)
    application_layout,layout=directories.select_runtime(agent_id or (os.environ.get('OMNI_AGENT_ID') if data_dir is None else None))
    paths=layout.paths
    if db_path is not None:paths=replace(paths,memory=Path(db_path))
    if skills_root is not None:paths=replace(paths,skills=Path(skills_root))
    if not config.terminal.roots:config.terminal.roots=[str(paths.workspace)]
    if layout.profile.id!='default':
        config.persona.name=layout.profile.name
        config.files.root=str(paths.workspace)
        config.files.extra_roots=[str(paths.files),str(paths.documents)]
        config.terminal.roots=[str(paths.workspace)]
        for directory in (paths.workspace,paths.files,paths.documents):directory.mkdir(parents=True,exist_ok=True)
    elif layout.profile.directories.workspace or application_layout.settings.workspace:
        config.files.root=str(paths.workspace);config.terminal.roots=[str(paths.workspace)]
        paths.workspace.mkdir(parents=True,exist_ok=True)
    memory_path = paths.memory
    store = MemoryStore(memory_path)
    # 记忆系统（M1–M6）：对话全量/会话全量/日周月笔记/核心人物四个记忆单元
    # 共用 memory.db（各开独立连接，内部加锁）；MemorySystem 只做聚合门面
    dialogue_log = DialogueLog(
        memory_path, max_entry_chars=config.transcript.max_entry_chars
    )
    session_log = SessionLog(
        memory_path,
        max_args_chars=config.transcript.max_tool_args_chars,
        max_result_chars=config.transcript.max_tool_result_chars,
    )
    journal_store = JournalStore(memory_path)
    people_store = PersonStore(
        memory_path,
        archive_max_chars=config.people.archive_max_chars,
        resident_max_chars=config.people.resident_max_chars,
        event_max_chars=config.people.event_max_chars,
        card_chars=config.people.person_card_chars,
        # 所有者沿用旧协议的总量限额：档案 2000（PROFILE_MAX_CHARS），常驻=原 MEMORY.md 限额配置
        owner_resident_total_chars=config.limits.resident_memory_chars,
    )
    memory_system = MemorySystem(
        dialogue=dialogue_log, session_log=session_log,
        journal=journal_store, people=people_store,
    )
    # 所有者记忆门面（数据层统一）：档案行与常驻笔记=owner 人物的 archive/resident
    # 条目；启动时一次性迁移旧 profile 表与 MEMORY.md（幂等+先备份+按内容去重续迁，
    # 失败不阻断启动，下次启动自动补齐；旧表与旧文件只冻结不删除）
    owner_memory = OwnerMemory(people_store)
    try:
        migrate_owner_memory(
            store, people_store, paths.resident,
            backup_root=paths.backups, memory_db_path=paths.memory,
        )
        if config.qq.owner_qq:
            try:
                people_store.link_account(
                    owner_memory.owner_id(), "qq", str(config.qq.owner_qq), label="所有者"
                )
            except Exception:  # noqa: BLE001, S110  —— 已绑定给别的人物等情况不阻断启动
                pass
    except Exception as exc:  # noqa: BLE001 —— 迁移失败只打日志：已导入部分生效，下次启动续迁
        print(f"[所有者记忆迁移] 失败（下次启动自动续迁）：{exc}", file=sys.stderr, flush=True)
    # 技能与常驻记忆共用一份修订存储：写入前先落历史，改坏了能回滚（D08）
    skills = SkillStore(
        paths.skills,
        history_root=paths.history,
        owner=f"pid:{os.getpid()}:skills",
        history_keep=config.skills.history_keep,
    )
    # 共享状态集中到 state.db（A09 跨进程丢写 / A10 损坏当空）：先迁移旧 JSON，
    # 缺失/合法空/损坏分别处理；损坏时只停用对应能力，绝不把已用额度归零。
    state = StateStore(paths.state)
    state.migrate_legacy(paths.data)
    # 模型费用账本（D10）：预留/结算走 state.db，跨进程共享同一份当日额度
    usage = UsageLedger(state)
    # 产物存储（D15）：截图/导出统一登记，元数据在 state.db（v6）
    artifacts = ArtifactStore(
        state, paths.artifacts,
        retention_days=config.artifacts.retention_days,
        max_total_bytes=config.artifacts.max_total_mb * 1024 * 1024,
    )
    # 投递 outbox（D17）：QQ 掉线时任务结果入队，恢复后由心跳进程补投
    outbox = OutboxStore(state)
    qq_messages = QQMessageStore(state)
    publications = PublicationStore(state)
    # MCP 工具目录指纹（D19）：服务升级换 schema 后提醒复查只读授权
    mcp_schemas = MCPSchemaStore(state)
    # 技能变更已读标记（D18）：agent/review 自动改技能进待审阅队列
    skill_acks = SkillAckStore(state)
    gateway = Gateway(config.models, llm=config.llm, ledger=usage, reasoning=config.reasoning)
    try:
        pruned = usage.prune(keep_days=180)
        if pruned and not (data_dir is not None):
            print(f"[额度] 清理了 {pruned} 条过老的调用流水", flush=True)
    except Exception:  # noqa: BLE001, S110  —— 清理失败不影响启动
        pass
    session_gate = SessionGate(capacity=config.limits.session_queue_depth)
    session_leases = SessionLeases(
        state, ttl_s=config.limits.session_lease_ttl_s
    )
    cron_jobs = JobStore(state)
    cron_ledger = ExecutionLog(state)
    # 运行记录：owner 记"本类进程的哪一次启动"，重启时据此对账（见 RunStore.interrupt_stale）
    event_log = EventLog(BoundedLog(paths.logs / 'events.jsonl'))
    runs = RunStore(state, event_sink=event_log.emit)
    active_runs = ActiveRuns()
    run_ref = RunRef()
    owner = f"{owner_kind}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    interrupted = runs.interrupt_stale(owner_kind=owner_kind, current_owner=owner)
    registry = ToolRegistry()
    command_job_store = CommandJobStore(state)
    command_jobs = CommandJobManager(command_job_store, paths.command_logs,
                                    root=paths.workspace, config=config.command_jobs)
    session_ref = SessionRef()  # Agent 与会话级工具（todo）共享的"当前会话"
    register_command_job_tools(registry, command_jobs, session_ref=session_ref, run_ref=run_ref)
    retrieval = None
    if config.embedding.enabled:
        embedding = EmbeddingProvider(config.embedding, usage, daily_limit_usd=config.llm.budget.daily_usd)
        vector_index = VectorIndex(memory_path, model=embedding.fingerprint,
                                   dimensions=config.embedding.dimensions, scopes=config.embedding.scopes)
        retrieval = HybridMemory(store, vector_index, embedding, ResourceLeases(state), run_ref=run_ref)
        if start_background_workers:
            retrieval.start()
        atexit.register(retrieval.close)
    # 记忆写入闸门（D12）：事实/档案/技能共用一份当日额度，review 走同一个闸门
    memory_guard = MemoryGuard(
        state,
        max_writes_per_day=config.memory.max_writes_per_day,
        max_fact_chars=config.memory.max_fact_chars,
        max_skill_chars=config.skills.max_write_chars,
    )
    register_core_tools(
        registry,
        store,
        owner=owner_memory,
        guard=memory_guard,
        run_ref=run_ref,
        session_ref=session_ref,
        recall_prefix=config.memory.recall_prefix,
        retriever=retrieval.search if retrieval is not None else None,
    )
    register_digest_tool(registry, store, profile_usage=owner_memory.profile_usage_chars)
    register_todo_tools(registry, store, session_ref)
    register_resident_tools(registry, owner_memory)
    # 记忆系统工具面（M1–M6）：全量检索/精确读取/笔记/人物；全部不进群聊许可名单
    register_transcript_tools(registry, dialogue_log, session_log)
    register_journal_tools(registry, journal_store)
    register_people_tools(registry, people_store, guard=memory_guard)
    agent_holder: dict[str, Any] = {}  # Agent 构造在后，delegate 工具运行期取用
    register_delegate_tools(registry, store, agent_holder, run_ref)
    register_shell_tools(registry,cwd=_resolve_root(config.files.root) if config.files.root else PROJECT_ROOT)
    # 工作区文件工具（D13）：允许的根只来自配置；写需人工确认 + 指纹 CAS + 写前备份。
    # 模型侧没有任何"再加一个根"的参数——路径参数只做相对定位。
    workspace = Workspace(
        root=_resolve_root(config.files.root) if config.files.root else PROJECT_ROOT,
        extra_roots=[_resolve_root(item) for item in config.files.extra_roots]+[paths.files,paths.documents],
        deny_globs=tuple(config.files.deny_globs),
        write_deny_globs=tuple(config.files.write_deny_globs),
        read_only_roots=[paths.documents],
        private_paths=private_paths(paths),
    )
    public_workspaces={name:Workspace(root=path,private_paths=private_paths(paths)) for name,path in [('workspace',workspace.root),('files',paths.files),('documents',paths.documents)] if path is not None}
    file_history = FileHistory(paths.file_history)
    register_file_tools(
        registry,
        workspace,
        file_history,
        lock_dir=paths.file_history / "locks",
        max_read_lines=config.files.max_read_lines,
        max_list_entries=config.files.max_list_entries,
        max_search_files=config.files.max_search_files,
        max_search_results=config.files.max_search_results,
        max_search_seconds=config.files.max_search_seconds,
    )
    # 网页读取（D14）：公网 http/https，内网目标必须由配置逐条授权；
    # 不携带任何 Cookie/Authorization，重定向每一跳都重新校验。
    register_web_tools(
        registry,
        FetchPolicy(
            timeout_s=config.web.timeout_s,
            total_deadline_s=config.web.total_deadline_s,
            max_bytes=config.web.max_bytes,
            max_redirects=config.web.max_redirects,
            allow_private_hosts=tuple(config.web.allow_private_hosts),
        ),
        default_max_chars=config.web.max_chars,
        default_max_links=config.web.max_links,
    )
    register_skill_tools(registry, skills, guard=memory_guard)
    # 外来技能包：导入只进隔离区（SAFE），激活是 CONFIRM——审批流里的人工确认就是"用户激活"
    skill_inbox = SkillInbox(skills)
    register_skill_inbox_tools(registry, skill_inbox)
    # 插件系统：收包校验零执行；激活（CONFIRM+人工确认）即同意执行插件代码。
    # 宿主在这里恢复已激活插件的工具注册；装载失败只报 stderr，不阻断启动。
    def _resolve_plugin_dir(rel: str) -> Path | None:
        from .tools.files.paths import PathRejected

        try:
            resolved = workspace.resolve(rel, must_exist=True)
        except PathRejected:
            return None
        return resolved.path if resolved.is_dir else None

    plugin_host = PluginHost(
        paths.data / "plugins", registry, resolve_dir=_resolve_plugin_dir
    )
    for _failure in plugin_host.load_installed():
        print(f"[插件装载失败] {_failure}", file=sys.stderr, flush=True)
    register_plugin_tools(registry, plugin_host)
    # 插件社区客户端：查询/安装 SAFE，发布 CONFIRM+人工确认（发布红线）；
    # 配置存 data/plugins/community.json（server_url/token），刻意不进 config.yaml
    community_client = CommunityClient(
        paths.data / "plugins" / "community.json",
        plugin_host,
        resolve_dir=_resolve_plugin_dir,
    )
    register_community_tools(registry, community_client)
    mcp = MCPManager(config.mcp, log_dir=paths.mcp_logs, schema_store=mcp_schemas,
                     config_provider=lambda: load_config(paths.config).mcp)
    register_mcp_tools(registry, mcp, run_ref=run_ref)
    if mcp.servers():
        atexit.register(mcp.close)
    # Cookie 路径必须注入：否则临时实例会读到真机的 B 站登录态
    register_bilibili_tools(registry, BiliAPI(cookies_path=paths.cookies), config.asr, cache_root=paths.cache/'asr')
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
    publication_forms = BrowserForms(browser, config.bilibili_write.forms)
    publisher = BilibiliPublisher(config.bilibili_write, publications, publication_forms, write_budget)
    register_bilibili_write_tools(registry, publisher, session_ref, run_ref)
    # can_view = 截图文本能否声称"模型看得到"：隐私开关 + 至少一个视觉模型可用
    _vision_route = ""
    try:
        _vision_route = gateway.resolve("vision")
    except Exception:  # noqa: BLE001 —— 未配置 vision 路由
        _vision_route = ""
    can_view = bool(
        config.vision.send_to_cloud
        and (
            config.llm.capability(_vision_route).vision
            or config.llm.capability(config.models.chat).vision
        )
    )
    register_browser_tools(
        registry, browser, write_budget,
        artifacts=artifacts, session_ref=session_ref, run_ref=run_ref, can_view=can_view,
    )
    desktop = DesktopDriver(config.desktop,
                            WindowsDesktopBackend(timeout_s=config.desktop.timeout_s, max_nodes=config.desktop.max_nodes),
                            ResourceLeases(state),
                            WriteBudget(state, max_per_day=config.desktop.max_writes_per_day, scope="desktop_write"))
    register_desktop_tools(registry, desktop, artifacts=artifacts, session_ref=session_ref, run_ref=run_ref, can_view=can_view)
    approvals = ApprovalBroker(timeout_s=config.browser.approval_timeout_s)
    # 隔离实例（注入了 data_dir）不探测也不终止真机进程：否则测试可能把真 NapCat 当残留杀掉
    isolated = data_dir is not None
    if interrupted and not isolated:
        print(
            f"[运行] 上次有 {len(interrupted)} 个运行未正常结束，已标记为 interrupted（不自动重试）："
            + "、".join(interrupted[:5]),
            flush=True,
        )
    services = ServiceManager(
        napcat_dir=Path(config.system.napcat_dir) if config.system.napcat_dir else None,
        run_dir=paths.run, log_dir=paths.service_logs or paths.logs,
        environment={'OMNI_AGENT_ID':paths.agent_id,'OMNI_AGENT_CATALOG':str(catalog_path.resolve()),
                     'OMNI_CONFIG_PATH':str(config_path.resolve()),'OMNI_STORAGE_BASE':str(data_root.resolve())},
        port_prober=None if not isolated else _no_port_probe,
        killer=None if not isolated else _no_kill,
        spawner=None if not isolated else _no_spawn,
        inspector=None if not isolated else _no_process_probe,
        tcp_probe=None if not isolated else _no_tcp_probe,
    )
    register_system_tools(registry, services)
    android_driver = AndroidDriver(
        cache_path=paths.android_cache / "tree.json", shots_dir=paths.android_shots,
        allowed_apps=config.android.allowed_apps, observation_ttl_s=config.android.observation_ttl_s,
        operation_timeout_s=config.android.operation_timeout_s, max_devices=config.android.max_devices
    )
    android_streams = AndroidFrameHub(lambda serial: android_driver.screenshot_bytes(serial=serial, preview=True),
                                     fps=config.android.preview_fps, max_clients=config.android.preview_clients,
                                     idle_s=config.android.preview_idle_s, max_devices=config.android.max_devices)
    register_android_tools(
        registry, android_driver,
        artifacts=artifacts, session_ref=session_ref, run_ref=run_ref, can_view=can_view,
    )
    qq_client: OneBotClient | None = None
    if config.qq.enabled:
        qq_client = OneBotClient(config.qq.ws_url, config.qq.access_token)
        register_qq_tools(registry, qq_client, _allowed_qq_users)
    session_id = store.latest_session_id() or store.new_session()
    # 能力档位路由（D28）：先判档位再注入工具规格，轻量回合少带一批工具规格。
    # 路由只决定"发哪些工具规格"，不改变任何审批与作用域边界。
    mode_router = ModeRouter(config)
    agent = Agent(
        config, gateway, store, registry, session_id,
        approver=approver, session_ref=session_ref, resident=owner_memory,
        runs=runs, active_runs=active_runs, run_ref=run_ref, owner=owner,
        artifacts=artifacts,
        dialogue_log=dialogue_log, session_log=session_log,
        person_card=people_store.card_for_account,
        profile_block=owner_memory.profile_block,
        mode_router=mode_router,
    )
    agent_holder["agent"] = agent

    # 回合后 review：白名单（最小权限：记忆/档案/常驻笔记/技能）在这里定义，
    # 属策略接线；store/registry 工厂保证每次 review 用独立连接与注册表
    # （技能是文件存储、常驻记忆自带锁，实例线程安全，可直接共享）。
    def _review_registry(review_store: MemoryStore) -> ToolRegistry:
        review_registry = ToolRegistry()
        # review 的写入与用户手动写入抢同一份额度（同一 MemoryGuard 实例）。
        # count_hits=False：后台取材的召回不给 hits 记账，防信号被整理偏好污染。
        register_memory_tools(
            review_registry, review_store, guard=memory_guard,
            recall_prefix=config.memory.recall_prefix, source="review",
            count_hits=False,
        )
        register_profile_tools(review_registry, owner_memory, store=review_store, guard=memory_guard, source="review")
        register_resident_tools(review_registry, owner_memory)
        register_skill_tools(review_registry, skills, guard=memory_guard, lifecycle=False)
        return review_registry

    reviewer = (
        TurnEndReviewer(
            config,
            gateway,
            lambda: MemoryStore(memory_path),
            _review_registry,
        )
        if config.review.enabled and start_background_workers
        else None
    )
    if reviewer is not None:
        # 手动触发整理（session_refine，SAFE，作用域=当前会话）：与自动 review
        # 共用同一个有界队列与消息区间去重；registry 每轮动态取 specs，晚注册对 Agent 可见
        register_refine_tool(registry, reviewer, session_ref)

    # 记忆系统整理 worker（M3–M5）：白名单=记忆沉淀工具（来源记 journal，额度与
    # 用户手动写入共享同一份 MemoryGuard）；note_save 工具由 worker 按期注入
    def _journal_registry(journal_store: MemoryStore) -> ToolRegistry:
        journal_registry = ToolRegistry()
        # count_hits=False：同 review——夜间整理的召回是取材，不给 hits 记账。
        register_memory_tools(
            journal_registry, journal_store, guard=memory_guard,
            recall_prefix=config.memory.recall_prefix, source="journal",
            count_hits=False,
        )
        register_profile_tools(journal_registry, owner_memory, store=journal_store, guard=memory_guard, source="journal")
        register_resident_tools(journal_registry, owner_memory)
        register_people_tools(journal_registry, people_store, guard=memory_guard, source="journal")
        return journal_registry

    journal_consolidator = (
        JournalConsolidator(
            config, gateway,
            journal=journal_store, dialogue=dialogue_log,
            make_store=lambda: MemoryStore(memory_path),
            make_registry=_journal_registry,
            session_log=session_log, people=people_store,
        )
        if config.journal.enabled
        else None
    )
    return Runtime(
        config=config,
        store=store,
        gateway=gateway,
        registry=registry,
        skills=skills,
        state=state,
        usage=usage,
        artifacts=artifacts,
        outbox=outbox,
        qq_messages=qq_messages,
        publications=publications,
        event_log=event_log,
        plugins=plugin_host,
        community=community_client,
        command_jobs=command_jobs,
        mcp_schemas=mcp_schemas,
        skill_acks=skill_acks,
        skill_inbox=skill_inbox,
        runs=runs,
        active_runs=active_runs,
        session_gate=session_gate,
        session_leases=session_leases,
        cron_jobs=cron_jobs,
        cron_ledger=cron_ledger,
        resident=owner_memory,
        owner_memory=owner_memory,
        browser=browser,
        write_budget=write_budget,
        approvals=approvals,
        services=services,
        agent=agent,
        android=android_driver,
        android_streams=android_streams,
        mcp=mcp,
        reviewer=reviewer,
        journal_consolidator=journal_consolidator,
        memory_guard=memory_guard,
        workspace=workspace,
        file_history=file_history,
        qq=qq_client,
        retrieval=retrieval,
        memory_system=memory_system,
        desktop=desktop,
        config_path=config_path,
        data_dir=data_root,
        paths=paths,
        directories=directories,
        public_workspaces=public_workspaces,
        agent_layout=layout,
        application_layout=application_layout,
    )
