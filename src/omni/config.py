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


def load_env(env_path: Path | None = None) -> None:
    """把 .env 里的 KEY=VALUE 注入环境变量（已存在的不覆盖），供 LiteLLM 读取。"""
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
        if key and key not in os.environ:
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


class Limits(BaseModel):
    max_history_messages: int = 40
    max_agent_steps: int = 8


class AppConfig(BaseModel):
    persona: Persona = Field(default_factory=Persona)
    models: Models = Field(default_factory=Models)
    limits: Limits = Field(default_factory=Limits)


def load_config(path: Path | None = None) -> AppConfig:
    path = path or CONFIG_PATH
    if not path.exists():
        return AppConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig(**data)
