"""工作台请求契约；路由共用，旧 web.py 继续显式转发公共模型名。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatIn(BaseModel):
    message: str
    session_id: int | None = None
    # 能力档位（D28）：不传=按配置默认（默认 auto 自动判定）。
    # 非法值由 Pydantic 直接拒绝（422），不让未定义的档位进内核。
    mode: Literal["auto", "chat", "retrieval", "agent"] | None = None


class ResolveIn(BaseModel):
    allowed: bool
    reason: str = ""


class NapCatStartIn(BaseModel):
    qq: str = ""  # 可选：快速登录的 QQ 号


class ConfigUpdateIn(BaseModel):
    updates: dict[str, dict[str, Any]]


class ServiceCleanupIn(BaseModel):
    """清理不可核验的启动记录：只删记录，绝不结束进程。"""

    name: str = ""
    pid: int | None = None
    confirm: bool = False  # 指定服务名时必须为真：用户确认过那个进程他自己处理


class SupervisorPolicyIn(BaseModel):
    """服务守护开关；缺省字段表示不改。"""

    autostart: bool | None = None
    keep_running_after_exit: bool | None = None
    restart_max_attempts: int | None = Field(default=None, ge=0, le=50)
    napcat_qq: str | None = None


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
    observation: str = ""


class AndroidElementIn(BaseModel):
    idx: int
    observation: str = ""


class AndroidKeyIn(BaseModel):
    keycode: str
    observation: str = ""


class ProfileLineIn(BaseModel):
    line_id: int
    content: str


class ProfileLineCreateIn(BaseModel):
    """工作台新增档案行（与 profile_manage add 同一条存储路径）。"""

    content: str = Field(..., min_length=1, max_length=2000)


class FactCreateIn(BaseModel):
    """工作台手动记一条事实（与 remember 工具同一条存储路径）。"""

    content: str = Field(..., min_length=1, max_length=500)


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


class SkillCreateIn(BaseModel):
    """工作台直接新建技能：与 agent 的 skill_create 共用 linter 与历史。"""

    name: str
    description: str
    content: str


class SkillArchiveIn(BaseModel):
    """归档/恢复的乐观并发：带读取时指纹，技能被别人改过就拒绝。"""

    expected_hash: str | None = None
    reason: str = ""


class SkillSettingsIn(BaseModel):
    """技能设置保存：完整配置 revision CAS + 白名单段。"""

    revision: str = Field(min_length=64, max_length=64)
    updates: dict[str, dict[str, Any]]


class SkillRollbackIn(BaseModel):
    rev: int
    expected_hash: str | None = None


class SkillAckIn(BaseModel):
    """技能变更已读标记：标到 rev（只前进）。"""

    rev: int


# ---------------- 记忆系统（M1–M6） ----------------


class PersonCreateIn(BaseModel):
    name: str
    importance: str = "known"
    note: str = ""


class PersonUpdateIn(BaseModel):
    name: str | None = None
    importance: str | None = None
    note: str | None = None


class AccountLinkIn(BaseModel):
    channel: str
    value: str
    label: str = ""


class PersonEntryIn(BaseModel):
    entry_kind: str
    content: str
    expires_at: str | None = None


class UpdatePolicyIn(BaseModel):
    channel: Literal["stable", "development"] = "stable"
    auto_check: bool = True


class UpdatePrepareIn(BaseModel):
    candidate_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class UpdateInstallIn(BaseModel):
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    confirm: bool = False


class PersonaPresetsIn(BaseModel):
    items: list[dict[str, str]]
    revision: str


class ModelKeyIn(BaseModel):
    """填 Key：`value` 为 None/空串表示清除该行，非空则替换。"""

    name: str
    value: str | None = None
