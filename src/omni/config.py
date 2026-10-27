"""配置加载：.env 密钥 + config.yaml 主配置。"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
ENV_PATH = PROJECT_ROOT / ".env"
DATA_DIR = PROJECT_ROOT / "data"
SKILLS_DIR = PROJECT_ROOT / "skills"  # 程序性记忆：skills/<name>/SKILL.md
BILI_COOKIES_PATH = DATA_DIR / "bili_cookies.json"  # B站登录态（浏览器 Cookie，gitignore）


def load_env(env_path: Path | None = None) -> None:
    """把 .env 里的 KEY=VALUE 注入环境变量，供 LiteLLM 读取。

    优先级约定：已存在的**非空**环境变量优先（尊重外部预配置）；
    缺失或为空的变量用 .env 填充——机器里可能残留空值变量（本次踩坑：
    空的 DEEPSEEK_API_KEY 曾导致 Authorization: "Bearer " 非法）。
    """
    path = env_path or ENV_PATH
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
        "你是 OmniOperator，一个运行在用户电脑上的个人智能体。当前时间：{now}"
    )


class Models(BaseModel):
    chat: str = "deepseek/deepseek-chat"
    vision: str = "anthropic/claude-sonnet-4-5"
    local: str = "ollama/qwen2.5:7b"
    review: str = ""  # 回合后 review 用的模型；留空则跟随 chat


class Limits(BaseModel):
    max_history_messages: int = 40
    max_agent_steps: int = 8
    resident_memory_chars: int = 2200  # 常驻小记忆（MEMORY.md 式）整文件限额


class Review(BaseModel):
    """回合后异步 review（记忆自进化）的开关与预算。"""

    enabled: bool = True
    max_messages: int = 12
    steps: int = 4


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


class Browser(BaseModel):
    """浏览器能力域（P3 写操作走 UI）。"""

    headless: bool = False          # 默认有头：用户能看到它在干什么（像人）
    slow_mo_ms: int = 120           # Playwright 慢放（拟人节奏 + 稳定性）
    max_writes_per_day: int = 50    # 每日写操作预算（click/type 合计）
    approval_timeout_s: float = 180.0  # 确认等待上限，超时自动拒绝


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


class AppConfig(BaseModel):
    persona: Persona = Field(default_factory=Persona)
    models: Models = Field(default_factory=Models)
    limits: Limits = Field(default_factory=Limits)
    review: Review = Field(default_factory=Review)
    compression: Compression = Field(default_factory=Compression)
    asr: Asr = Field(default_factory=Asr)
    qq: QQ = Field(default_factory=QQ)
    browser: Browser = Field(default_factory=Browser)
    system: System = Field(default_factory=System)
    approval: Approval = Field(default_factory=Approval)
    mcp: MCPConfig = Field(default_factory=MCPConfig)


def load_config(path: Path | None = None) -> AppConfig:
    path = path or CONFIG_PATH
    if not path.exists():
        return AppConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig(**data)
