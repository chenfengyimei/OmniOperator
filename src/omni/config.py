"""配置加载：.env 密钥 + config.yaml 主配置。"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

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
    name: str = "小欧"
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


class Review(BaseModel):
    """回合后异步 review（记忆自进化）的开关与预算。"""

    enabled: bool = True
    max_messages: int = 12
    steps: int = 4


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


class AppConfig(BaseModel):
    persona: Persona = Field(default_factory=Persona)
    models: Models = Field(default_factory=Models)
    limits: Limits = Field(default_factory=Limits)
    review: Review = Field(default_factory=Review)
    asr: Asr = Field(default_factory=Asr)
    qq: QQ = Field(default_factory=QQ)


def load_config(path: Path | None = None) -> AppConfig:
    path = path or CONFIG_PATH
    if not path.exists():
        return AppConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig(**data)
