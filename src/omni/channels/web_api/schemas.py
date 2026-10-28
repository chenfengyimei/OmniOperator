"""工作台请求契约；路由共用，旧 web.py 继续显式转发公共模型名。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ChatIn(BaseModel):
    message: str
    session_id: int | None = None


class ResolveIn(BaseModel):
    allowed: bool
    reason: str = ""


class NapCatStartIn(BaseModel):
    qq: str = ""  # 可选：快速登录的 QQ 号


class ConfigUpdateIn(BaseModel):
    updates: dict[str, dict[str, Any]]


class ModelTestIn(BaseModel):
    model: str


class MCPSchemaConfirmIn(BaseModel):
    fingerprint: str


class BiliCookiesIn(BaseModel):
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""


class CronNameIn(BaseModel):
    name: str


class CronToggleIn(BaseModel):
    name: str
    enabled: bool


class CronCreateIn(BaseModel):
    name: str
    prompt: str
    every_minutes: int | None = None
    daily_at: str | None = None
    deliver_to_qq: int | None = None
    skills: list[str] = []
    context_from: str | None = None
    timezone: str = "local"
    context_max_age_minutes: int = 1440


class CronUpdateIn(BaseModel):
    """编辑任务：只传要改的字段（None=不改）；expected_revision 做乐观并发。"""

    name: str
    prompt: str | None = None
    every_minutes: int | None = None
    daily_at: str | None = None
    deliver_to_qq: int | None = None
    skills: list[str] | None = None
    context_from: str | None = None
    enabled: bool | None = None
    expected_revision: int | None = None
    timezone: str | None = None
    context_max_age_minutes: int | None = None


class TodoToggleIn(BaseModel):
    session_id: int
    todo_id: int
    done: bool


class AndroidTapIn(BaseModel):
    x: int
    y: int
    stream_w: int
    stream_h: int
    observation: str = ''


class AndroidElementIn(BaseModel):
    idx: int
    observation: str = ''


class AndroidKeyIn(BaseModel):
    keycode: str
    observation: str = ''


class ProfileLineIn(BaseModel):
    line_id: int
    content: str


class ResidentUpdateIn(BaseModel):
    operation: str  # add | replace | remove
    id: int | None = None
    content: str = ""


class PairIn(BaseModel):
    code: str


class SkillSaveIn(BaseModel):
    """技能编辑器保存：全文 + 读取时的指纹（别人改过就 409，不覆盖）。"""

    content: str
    expected_hash: str | None = None


class SkillRollbackIn(BaseModel):
    rev: int
    expected_hash: str | None = None


class SkillAckIn(BaseModel):
    """技能变更已读标记：标到 rev（只前进）。"""

    rev: int
