"""配置加载：.env 密钥 + config.yaml 主配置 + 运行期路径表。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
ENV_PATH = PROJECT_ROOT / ".env"
DATA_DIR = PROJECT_ROOT / "data"
SKILLS_DIR = PROJECT_ROOT / "skills"  # 程序性记忆：skills/<name>/SKILL.md
BILI_COOKIES_PATH = DATA_DIR / "bili_cookies.json"  # B站登录态（浏览器 Cookie，gitignore）
STATE_SCHEMA_VERSION = 14  # 存储兼容上限；迁移实现仍归 memory/state.py。


@dataclass(frozen=True)
class RuntimePaths:
    """运行期所有可写路径的唯一声明处（A08）。

    为什么要有：以前各模块自己拼 `DATA_DIR / ...`，于是 `build_runtime(data_dir=tmp)`
    只隔离了一部分——安卓截图、B站 Cookie、配置写入仍然打到真机目录。
    现在路径只在这里定义一次，由 bootstrap 装配后传给每个使用者；测试换一个 `data`
    就等于换掉全部写入位置。

    明确不做：不创建目录（缺目录由使用者按需 mkdir），不读取配置内容。
    """

    data: Path
    config: Path          # 配置源（读 + 工作台写回同一个文件）
    env: Path             # 密钥文件（.env）：与 config 同一棵树，迁移时一起搬
    state: Path           # 共享运行状态（任务/执行账本/额度/迁移记录，SQLite）
    memory: Path          # 可独立注入的记忆库；诊断与备份必须读同一路径。
    resident: Path        # 常驻工作笔记（MEMORY.md）
    history: Path         # 技能与常驻内容的修订快照（可回滚）
    file_history: Path    # 工作区文件的写入备份与回滚（D13）
    cookies: Path         # B站登录态
    skills: Path          # 技能库（与 data 独立，可单独注入）
    cache: Path           # 可再生的缓存（ASR 等）
    android_shots: Path
    android_cache: Path
    browser_profile: Path
    browser_shots: Path
    artifacts: Path      # 产物文件（截图/导出，D15）
    run: Path             # 服务 pid
    logs: Path
    command_logs: Path
    mcp_logs: Path
    backups: Path
    workspace: Path | None = None
    files: Path | None = None
    documents: Path | None = None
    service_logs: Path | None = None
    agent_id: str = "default"
    catalog: Path | None = None

    @classmethod
    def create(
        cls,
        data_dir: Path,
        *,
        config_path: Path | None = None,
        skills_root: Path | None = None,
        memory_path: Path | None = None,
    ) -> RuntimePaths:
        data = Path(data_dir)
        config_file = Path(config_path) if config_path is not None else CONFIG_PATH
        return cls(
            data=data,
            config=config_file,
            env=(
                config_file.parent.parent / ".env"
                if config_path is not None
                else ENV_PATH
            ),
            state=data / "state.db",
            memory=Path(memory_path) if memory_path is not None else data / "memory.db",
            resident=data / "MEMORY.md",
            history=data / "history",
            file_history=data / "file_history",
            cookies=data / "bili_cookies.json",
            skills=Path(skills_root) if skills_root is not None else SKILLS_DIR,
            cache=data / "cache",
            android_shots=data / "android_shots",
            android_cache=data / "android_cache",
            browser_profile=data / "browser_profile",
            browser_shots=data / "browser_shots",
            artifacts=data / "artifacts",
            run=data / "run",
            logs=data / "system_logs",
            command_logs=data / "command_logs",
            mcp_logs=data / "mcp_logs",
            backups=data / "backups",
            workspace=PROJECT_ROOT,
            files=data / "files",
            documents=data / "documents",
            service_logs=data / "system_logs",
            catalog=data / "control" / "agents.json",
        )


def load_env(env_path: Path | None = None) -> None:
    """把 .env 里的 KEY=VALUE 注入环境变量，供 LiteLLM 读取。

    优先级约定：已存在的**非空**环境变量优先（尊重外部预配置）；
    缺失或为空的变量用 .env 填充——机器里可能残留空值变量（本次踩坑：
    空的 DEEPSEEK_API_KEY 曾导致 Authorization: "Bearer " 非法）。
    """
    selected_config = os.environ.get('OMNI_CONFIG_PATH')
    path = env_path or (Path(selected_config).resolve().parent.parent / '.env' if selected_config else ENV_PATH)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


class Persona(BaseModel):
    name: str = "小鱼"
    system_prompt: str = (
        "你是 OmniOperator，一个运行在用户电脑上的个人智能体。"
    )


class Models(BaseModel):
    chat: str = "deepseek/deepseek-chat"
    vision: str = "anthropic/claude-sonnet-4-5"
    local: str = "ollama/qwen2.5:7b"
    review: str = ""  # 回合后 review 用的模型；留空则跟随 chat


class ModelCapability(BaseModel):
    """某个模型**声明**的能力与单价（D10）。

    为什么必须显式声明：模型串长得像并不等于能力相同。"支持工具"和"支持图片"
    都是要花钱调一次才知道的事，而一旦供应商默默忽略 tools 参数，agent 会以为
    自己在用工具、实际在瞎猜。所以默认 `tools=True`（本项目所有在用模型都支持），
    而 `vision=False`——没声明就是没声明，调用前拒绝，并给出可执行的提示。
    """

    model_config = ConfigDict(extra="forbid")
    tools: bool = True
    vision: bool = False
    stream: bool = True
    context_tokens: int = Field(default=0, ge=0)  # 0 = 未声明
    input_usd_per_m: float | None = Field(default=None, ge=0)   # 每百万输入 token
    output_usd_per_m: float | None = Field(default=None, ge=0)  # 每百万输出 token


class Retry(BaseModel):
    """有界重试：次数 + 总时限 + 指数退避，三者同时约束。"""

    max_attempts: int = Field(default=3, ge=1, le=5)
    total_deadline_s: float = Field(default=120.0, gt=0, le=900)
    backoff_s: float = Field(default=0.5, ge=0, le=30)


class LLMBudget(BaseModel):
    """费用上限（美元）。0 = 未设上限：只记账、不拦截。

    为什么不给一个"默认上限"：拍一个数字会在用户不知情时中断正在做的事。
    未设上限是现状的延续，但界面与事件里会明确显示"未设上限"，
    并且**一旦配置就是硬拦截**（调用前预留，超额直接不发请求）。
    """

    daily_usd: float = Field(default=0.0, ge=0)
    per_run_usd: float = Field(default=0.0, ge=0)  # 单次运行（含子运行累计）上限


class Vision(BaseModel):
    """截图等图像发给云端模型看（D15/A13 的隐私闸门）。

    `send_to_cloud` 默认关：截图可能含聊天记录、邮箱、文件名等敏感内容，
    **纯本地存图不等于已获云端上传许可**——要看图必须显式打开这个开关。
    """

    model_config = ConfigDict(extra="forbid")
    send_to_cloud: bool = False
    max_image_bytes: int = Field(default=4_000_000, gt=0)   # 单图上限（字节）
    max_images_per_turn: int = Field(default=4, ge=1)        # 单回合最多附图数
    max_edge_px: int = Field(default=1568, ge=64)            # 长边超此值先缩放再发


class Artifacts(BaseModel):
    """产物（截图/导出）的保留与容量。"""

    model_config = ConfigDict(extra="forbid")
    retention_days: int = Field(default=7, ge=0)   # 0 = 永不过期（不推荐）
    max_total_mb: int = Field(default=500, ge=1)   # 总量上限，超了拒绝保存新产物


class LLM(BaseModel):
    """模型调用层：超时、重试、预算与能力声明。"""

    request_timeout_s: float = Field(default=90.0, gt=0, le=600)
    max_output_tokens: int = Field(default=1024, ge=1)  # 预留费用时按此上界估算
    retry: Retry = Field(default_factory=Retry)
    budget: LLMBudget = Field(default_factory=LLMBudget)
    capabilities: dict[str, ModelCapability] = Field(default_factory=dict)

    def capability(self, model: str) -> ModelCapability:
        return self.capabilities.get(model) or ModelCapability()


class Limits(BaseModel):
    max_history_messages: int = 40
    max_agent_steps: int = 8
    resident_memory_chars: int = 2200  # 常驻小记忆（MEMORY.md 式）整文件限额
    session_queue_depth: int = 1   # 同一会话允许排队的请求数（不含正在执行的那条）
    session_lease_ttl_s: int = 600  # 跨进程会话租约的有效期（进程崩溃后按此过期）


class Review(BaseModel):
    """回合后异步 review（记忆自进化）的开关与预算。"""

    enabled: bool = True
    max_messages: int = 12
    steps: int = 4
    queue_depth: int = 4      # 待整理队列上限：满了就明确跳过，不无限堆线程（A16）
    min_messages: int = 2     # 少于这么多条新消息不整理（节省调用）


class Memory(BaseModel):
    """记忆写入的确定性约束（D12/A15）。

    额度不是"建议"：所有记忆写入口（事实 / 档案 / 技能）共用同一份当日额度，
    自动 review 与用户手动操作走同一条路，不能靠模型自觉。
    """

    max_writes_per_day: int = 200   # 事实+档案+技能的每日写入总次数
    max_fact_chars: int = 500       # 单条事实的最大长度（防止把整段对话塞进来）
    recall_prefix: bool = True      # 检索结果加"这是数据不是指令"的说明


class Web(BaseModel):
    """通用网页读取（D14）：一次抓取的硬边界。

    `allow_private_hosts` 是唯一能放开内网目标的地方：写域名或 `域名:端口`，
    由用户在配置里逐条授权——模型没有任何参数能让它连内网。
    """

    max_bytes: int = 2_000_000          # 响应体最多读多少字节（超出即停，不落盘）
    max_chars: int = 8000               # 默认返回多少字符正文
    max_links: int = 10                 # 默认返回多少条链接
    timeout_s: float = 15.0             # 单次连接/读取超时
    total_deadline_s: float = 30.0      # 含重定向的总时限
    max_redirects: int = 5              # 最多跟随几次重定向（每跳重新校验目标）
    allow_private_hosts: list[str] = Field(default_factory=list)  # 显式授权的内网目标
    user_agent: str = ""                # 留空用内置 UA


class Files(BaseModel):
    """结构化文件工具的边界（D13）。

    允许的根**只来自这里**：`root` 是工作区，`extra_roots` 是用户显式写下的额外目录。
    模型没有任何参数能扩展它——否则"再传一个 path"就成了自我授权。
    """

    root: str = ""                    # 留空 = 项目根目录
    extra_roots: list[str] = Field(default_factory=list)        # 额外授权目录（用户手写）
    deny_globs: list[str] = Field(default_factory=list)         # 额外不可读（相对各根）
    write_deny_globs: list[str] = Field(default_factory=list)   # 额外不可写
    max_read_lines: int = 200         # files_read 单次默认行数
    max_list_entries: int = 200       # files_list 单次默认条目
    max_search_files: int = 2000      # 搜索最多访问多少个文件
    max_search_results: int = 80      # 搜索最多返回多少条命中
    max_search_seconds: float = 5.0   # 搜索时间上限（防止巨目录拖死回合）


class Compression(BaseModel):
    """上下文压缩（长会话防膨胀；Hermes compression_facade 最小版）。"""

    enabled: bool = True
    trigger_chars: int = 24000  # 上下文总字符量超过此值触发压缩
    keep_recent: int = 12       # 压缩时原样保留最近多少条消息
    min_messages: int = 16      # 消息总数少于此不压（得不偿失）


class Asr(BaseModel):
    """本地 ASR（faster-whisper，无字幕视频转写兜底）。"""

    model: str = "small"       # tiny/base/small/medium；small 中文质量与速度平衡
    device: str = "cpu"        # 有 NVIDIA GPU 可改 cuda
    compute_type: str = "int8"


class QQ(BaseModel):
    """QQ 通道（NapCat / OneBot v11）。"""

    enabled: bool = False
    ws_url: str = "ws://127.0.0.1:3001"
    access_token: str = ""
    allowed_users: list[int] = Field(default_factory=list)   # 私聊白名单（空=拒绝所有人）
    allowed_groups: list[int] = Field(default_factory=list)  # 群白名单（群里还需 @机器人）
    reply_delay_s: list[float] = Field(default_factory=lambda: [1.0, 3.0])  # 拟人回复延迟
    group_reply_to_bot: bool = False
    merge_window_s: float = Field(default=0.3, ge=0, le=3)
    max_pending: int = Field(default=64, ge=1, le=256)
    max_concurrent: int = Field(default=4, ge=1, le=8)
    proactive_enabled: bool = False
    owner_qq: int = Field(default=0, ge=0)
    proactive_daily_limit: int = Field(default=5, ge=0, le=50)
    quiet_start: str = "22:00"
    quiet_end: str = "08:00"
    timezone: str = "Asia/Shanghai"

    @model_validator(mode="after")
    def check_qq_policy(self):
        import datetime as dt
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("QQ timezone 必须为有效 IANA 时区") from exc
        for value in (self.quiet_start, self.quiet_end):
            parsed = dt.datetime.strptime(value, "%H:%M")
            if parsed.strftime("%H:%M") != value:
                raise ValueError("安静时段格式必须为 HH:MM")
        if len(self.reply_delay_s) != 2 or not 0 <= self.reply_delay_s[0] <= self.reply_delay_s[1] <= 30:
            raise ValueError("reply_delay_s 必须为 0–30 秒的有序上下限")
        return self


class Browser(BaseModel):
    """浏览器能力域（P3 写操作走 UI）。"""

    headless: bool = False          # 默认有头：用户能看到它在干什么（像人）
    slow_mo_ms: int = 120           # Playwright 慢放（拟人节奏 + 稳定性）
    max_writes_per_day: int = 50    # 每日写操作预算（click/type 合计）
    approval_timeout_s: float = 180.0  # 确认等待上限，超时自动拒绝


class UIFormSelectors(BaseModel):
    """用户核验过的页面控件描述；不从模型输入接受选择器或脚本。"""
    model_config = ConfigDict(extra="forbid")
    account: str
    account_attr: str = "data-mid"
    target: str
    target_attr: str = "data-id"
    editor: str
    submit: str
    records: str
    record_id_attr: str = "data-id"
    record_text: str
    record_author: str
    record_author_attr: str = "data-mid"
    challenge: str
    logged_out: str


class BilibiliWrite(BaseModel):
    enabled: bool = False
    forms: dict[str, UIFormSelectors] = Field(default_factory=dict)
    draft_ttl_s: int = Field(default=600, ge=30, le=3600)
    max_content_chars: int = Field(default=1000, ge=1, le=2000)

    @model_validator(mode="after")
    def check_forms(self):
        if set(self.forms) - {"comment", "private_message"}:
            raise ValueError("B站写表单仅支持 comment/private_message")
        for profile in self.forms.values():
            if any(not value.strip() or len(value) > 500 for value in profile.model_dump().values()):
                raise ValueError("B站表单选择器/属性不能为空或过长")
        return self


class System(BaseModel):
    """本地系统控制台（服务管理/配置）。"""

    napcat_dir: str = "D:/tools/napcat"  # NapCat Shell 目录（含 napcat.bat）


class Approval(BaseModel):
    """审批策略档（Hermes approvals.mode）。

    manual=CONFIRM 一律问人（现状默认）；smart=辅助模型先判风险，
    明显安全自动放行/明显危险直接拒/拿不准仍问人（QQ 与无人值守保持 fail-closed）。
    """

    mode: str = "manual"  # manual | smart


class MCPServerConfig(BaseModel):
    """仅用户配置可声明本地 stdio 服务；模型不能修改命令或权限。"""

    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)  # 值可用 ${ENV_NAME} 引用 .env
    cwd: str | None = None
    enabled: bool = True
    timeout_s: float = Field(default=30, ge=0.1, le=120)
    allowed_tools: list[str] | None = None  # None=全部可申请人工批准，[]=禁用全部
    max_waiting: int = Field(default=8, ge=1, le=32)
    log_bytes: int = Field(default=5242880, ge=16384, le=16777216)
    log_segments: int = Field(default=3, ge=1, le=5)
    read_only_tools: list[str] = Field(default_factory=list)  # 用户审核的无副作用工具

    @field_validator("command")
    @classmethod
    def nonblank_command(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("MCP command 必须是可执行文件；参数放 args")
        return value.strip()

    @model_validator(mode="after")
    def readonly_subset(self) -> MCPServerConfig:
        if self.allowed_tools is not None and not set(self.read_only_tools) <= set(self.allowed_tools):
            raise ValueError("MCP read_only_tools 必须包含在 allowed_tools 中")
        return self


class MCPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)

    @field_validator("servers")
    @classmethod
    def server_names(cls, value: dict[str, MCPServerConfig]) -> dict[str, MCPServerConfig]:
        import re

        if any(re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", name) is None for name in value):
            raise ValueError("MCP 服务名须为 1–40 位小写字母、数字、下划线或连字符，字母开头")
        return value


class Desktop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    allowed_apps: list[str] = Field(default_factory=list)  # 完整 exe 路径；空列表全拒
    max_nodes: int = Field(default=100, ge=1, le=300)
    timeout_s: float = Field(default=15, ge=1, le=60)
    observation_ttl_s: float = Field(default=120, ge=1, le=600)
    max_writes_per_day: int = Field(default=30, ge=0, le=500)

    @field_validator("allowed_apps")
    @classmethod
    def absolute_apps(cls, values):
        if any(not Path(value).is_absolute() for value in values):
            raise ValueError("desktop.allowed_apps 必须使用完整 exe 路径")
        return values


class Embedding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    provider: str = "local"  # local=已有 Ollama；api=LiteLLM
    model: str = ""
    revision: str = "1"  # 同名模型更新后由用户递增，避免新旧向量混用
    dimensions: int = Field(default=768, ge=1, le=4096)
    endpoint: str = "http://127.0.0.1:11434"
    api_key_env: str = ""
    send_to_cloud: bool = False
    daily_usd: float = Field(default=0, ge=0)
    input_usd_per_m: float = Field(default=0, ge=0)
    timeout_s: float = Field(default=15, ge=1, le=60)
    batch_size: int = Field(default=8, ge=1, le=32)
    max_text_chars: int = Field(default=4000, ge=1, le=16000)
    scopes: list[str] = Field(default_factory=lambda: ["private"])

    @model_validator(mode="after")
    def validate_provider(self):
        from urllib.parse import urlsplit
        if self.provider not in {"local", "api"}:
            raise ValueError("embedding.provider 必须是 local 或 api")
        url = urlsplit(self.endpoint)
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("embedding.endpoint 不能包含凭据、查询或片段")
        if self.provider == "local" and (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("本地 embedding 仅允许回环 HTTP 地址")
        if self.provider == "local" and (self.model.endswith(":cloud") or self.model.endswith("-cloud")):
            raise ValueError("本地 embedding 不能选择 Ollama 云端模型")
        if self.provider == "api" and url.scheme != "https":
            raise ValueError("API embedding 必须配置 HTTPS endpoint")
        if self.enabled and not self.model.strip():
            raise ValueError("启用 embedding 前必须配置已有模型")
        if self.enabled and self.provider == "api" and not (self.send_to_cloud and self.daily_usd > 0 and self.input_usd_per_m > 0):
            raise ValueError("API embedding 需要显式 send_to_cloud、每日预算和输入单价")
        return self


class Cron(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_running: int = Field(default=2, ge=1, le=8)


class Journal(BaseModel):
    """记忆系统的日/周/月整理（M3/M4/M5 的编排参数）。

    `*_prompt` 留空用内置默认规则（压缩规则本身要持久可改——存在配置里，
    工作台「记忆系统 → 整理设置」可编辑）。日期边界一律用机器本地时区，
    与全库 `_now()` 的口径一致。
    """

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    daily_time: str = "23:30"      # 当天整理的触发时刻（HH:MM）
    steps: int = Field(default=6, ge=1, le=12)          # 单次整理的模型步数预算
    max_catchup: int = Field(default=3, ge=1, le=10)     # 每轮最多补整理几天积压
    max_input_chars: int = Field(default=40000, ge=4000, le=200000)  # 取材截断上限
    max_note_chars: int = Field(default=6000, ge=500, le=20000)      # 单篇笔记上限
    daily_prompt: str = ""
    weekly_prompt: str = ""
    monthly_prompt: str = ""

    @model_validator(mode="after")
    def check_daily_time(self) -> "Journal":
        import datetime as dt

        parsed = dt.datetime.strptime(self.daily_time, "%H:%M")
        if parsed.strftime("%H:%M") != self.daily_time:
            raise ValueError("journal.daily_time 必须为 HH:MM")
        return self


class Transcript(BaseModel):
    """全量记忆两个模块（M1/M2）的记录边界。

    保留期 0 = 永久（默认）：全量记忆是"一字不差"的存档，用户明确要求完整保留；
    设了天数则由整理循环滚动清理日志表（不动 messages）。
    """

    model_config = ConfigDict(extra="forbid")
    max_entry_chars: int = Field(default=20000, ge=1000, le=200000)
    max_tool_args_chars: int = Field(default=4000, ge=200, le=50000)
    max_tool_result_chars: int = Field(default=4000, ge=200, le=50000)
    retention_days: int = Field(default=0, ge=0, le=3650)          # 会话全量（M2）
    dialogue_retention_days: int = Field(default=0, ge=0, le=3650)  # 对话全量（M1）


class People(BaseModel):
    """核心人物模块（M6）的限额：每条目上限与注入卡体积。"""

    model_config = ConfigDict(extra="forbid")
    person_card_chars: int = Field(default=800, ge=200, le=4000)
    archive_max_chars: int = Field(default=2000, ge=200, le=8000)
    resident_max_chars: int = Field(default=1200, ge=200, le=4000)
    event_max_chars: int = Field(default=500, ge=50, le=2000)


class AndroidConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_apps: list[str] = Field(default_factory=list, max_length=32)
    observation_ttl_s: float = Field(default=120, ge=1, le=600)
    operation_timeout_s: float = Field(default=30, ge=5, le=120)
    max_devices: int = Field(default=8, ge=1, le=16)
    preview_fps: float = Field(default=2, ge=0.2, le=2)
    preview_clients: int = Field(default=2, ge=1, le=4)
    preview_idle_s: float = Field(default=5, ge=0.2, le=30)

    @field_validator("allowed_apps")
    @classmethod
    def packages(cls, values):
        import re
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+",value) for value in values):
            raise ValueError("android.allowed_apps 只接受包名，不接受命令或 Intent")
        return values


class CommandJobsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_running: int = Field(default=2, ge=1, le=8)
    max_waiting: int = Field(default=4, ge=1, le=32)
    default_timeout_s: float = Field(default=1800, ge=1, le=7200)
    log_bytes: int = Field(default=2097152, ge=16384, le=8388608)
    retention_days: int = Field(default=7, ge=1, le=30)
    total_log_bytes: int = Field(default=67108864, ge=16777216, le=536870912)


class TerminalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_sessions: int = Field(default=4, ge=1, le=16)
    roots: list[str] = Field(default_factory=list)
    buffer_bytes: int = Field(default=1048576, ge=16384, le=8388608)
    detach_ttl_s: float = Field(default=600, ge=1, le=86400)


class AppConfig(BaseModel):
    android: AndroidConfig = Field(default_factory=AndroidConfig)
    command_jobs: CommandJobsConfig = Field(default_factory=CommandJobsConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)
    persona: Persona = Field(default_factory=Persona)
    models: Models = Field(default_factory=Models)
    llm: LLM = Field(default_factory=LLM)
    limits: Limits = Field(default_factory=Limits)
    review: Review = Field(default_factory=Review)
    files: Files = Field(default_factory=Files)
    web: Web = Field(default_factory=Web)
    vision: Vision = Field(default_factory=Vision)
    artifacts: Artifacts = Field(default_factory=Artifacts)
    memory: Memory = Field(default_factory=Memory)
    compression: Compression = Field(default_factory=Compression)
    asr: Asr = Field(default_factory=Asr)
    qq: QQ = Field(default_factory=QQ)
    browser: Browser = Field(default_factory=Browser)
    bilibili_write: BilibiliWrite = Field(default_factory=BilibiliWrite)
    system: System = Field(default_factory=System)
    approval: Approval = Field(default_factory=Approval)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    cron: Cron = Field(default_factory=Cron)
    journal: Journal = Field(default_factory=Journal)
    transcript: Transcript = Field(default_factory=Transcript)
    people: People = Field(default_factory=People)
    embedding: Embedding = Field(default_factory=Embedding)
    desktop: Desktop = Field(default_factory=Desktop)


def load_config(path: Path | None = None) -> AppConfig:
    path = path or CONFIG_PATH
    if not path.exists():
        return AppConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig(**data)


def load_qq_policy(path: Path | None = None) -> QQ:
    """每次调用都从配置源重读 QQ 策略（白名单/群名单/开关）。

    为什么不做缓存：撤销白名单必须在下一条消息、下一次投递就生效，不能等进程重启；
    读失败或字段非法时返回**全空**策略——安全配置一旦损坏，宁可谁都不许，也不沿用旧权限。
    """
    try:
        return load_config(path).qq
    except Exception:  # noqa: BLE001 —— 任何解析失败都按 fail-closed 处理
        return QQ(enabled=False, allowed_users=[], allowed_groups=[])
