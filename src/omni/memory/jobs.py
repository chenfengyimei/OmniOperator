"""定时任务模型与调度时间（纯数据 + 纯函数，不碰存储）。

从 `memory/cron.py` 拆出来的原因：存储要迁到 `memory/state.py`（SQLite 事务），
而存储层需要 `Job` 与时间计算。模型放这里，两边单向依赖，不会再绕回循环导入。

设计出处：Hermes cron 的最小版（xuexi/NOTES.md §1.6）——
调度方式二选一：`every_minutes`（周期）或 `daily_at`（每日 HH:MM，本地时区）。
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
DAILY_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
MIN_EVERY_MINUTES = 10


class CronError(ValueError):
    """任务定义不合法。"""


@dataclass
class Job:
    name: str
    prompt: str
    every_minutes: int | None = None   # 周期模式（分钟）
    daily_at: str | None = None        # 每日定时 "HH:MM"（本地时区）
    deliver_to_qq: int | None = None   # 投递给白名单 QQ 用户（可选）
    skills: list[str] = field(default_factory=list)  # 执行前注入的技能
    context_from: str | None = None    # 任务链：把源任务的最新输出注入本次 prompt
    enabled: bool = True
    next_run_at: str | None = None     # ISO 本地时间
    last_run_at: str | None = None
    last_result: str | None = None
    # 用户可见定义的版本号：只在「定义」被改写时递增，调度器推进 next_run_at 不算。
    # 前端提交时可带上它做乐观并发（旧表单不会静默覆盖后台更新）。
    revision: int = 0
    timezone: str = "local"  # local 保留旧任务机器时区；新任务可显式指定 IANA 名称
    context_max_age_minutes: int = 1440

    def validate(self) -> None:
        if not self.timezone:
            raise CronError("timezone 不能为空；使用 local 或 IANA 时区")
        if self.timezone != "local":
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
                raise CronError("未知 timezone；使用 Asia/Shanghai 等 IANA 时区") from exc
        if not isinstance(self.context_max_age_minutes, int) or not 1 <= self.context_max_age_minutes <= 10080:
            raise CronError("context_max_age_minutes 必须为 1–10080 分钟")
        if not NAME_RE.match(self.name or ""):
            raise CronError("任务名需为 3-40 位小写字母/数字/连字符（如 morning-report）")
        if not (self.prompt or "").strip():
            raise CronError("prompt 不能为空")
        has_every = self.every_minutes is not None
        has_daily = bool(self.daily_at)
        if has_every == has_daily:  # 都有或都没有
            raise CronError("every_minutes 与 daily_at 必须二选一")
        if has_every and int(self.every_minutes) < MIN_EVERY_MINUTES:
            raise CronError(f"every_minutes 最小 {MIN_EVERY_MINUTES} 分钟（防打爆）")
        if has_daily and not DAILY_RE.match(self.daily_at or ""):
            raise CronError("daily_at 需为 HH:MM（如 08:30）")
        if self.context_from is not None:
            if not NAME_RE.match(self.context_from):
                raise CronError(
                    "context_from 需为有效任务名（3-40 位小写字母/数字/连字符）"
                )
            if self.context_from == self.name:
                raise CronError("context_from 不能引用任务自己")


def compute_next_run(job: Job, from_dt: dt.datetime) -> dt.datetime:
    """下一次运行时刻（本地时区）。"""
    from_dt = from_dt.astimezone() if job.timezone == "local" else from_dt.astimezone(ZoneInfo(job.timezone))
    if job.every_minutes is not None:
        return (from_dt.astimezone(dt.timezone.utc) + dt.timedelta(minutes=int(job.every_minutes))).astimezone(from_dt.tzinfo)
    match = DAILY_RE.match(job.daily_at or "")
    if not match:
        raise CronError(f"daily_at 非法: {job.daily_at}")
    hour, minute = int(match.group(1)), int(match.group(2))
    # DST 跳过的墙钟时间当天不执行；重复时刻只取第一次，避免一天两次副作用。
    for offset in range(3):
        day = from_dt.date() + dt.timedelta(days=offset)
        candidate = dt.datetime.combine(day, dt.time(hour, minute), from_dt.tzinfo)
        normalized = candidate.astimezone(dt.timezone.utc).astimezone(from_dt.tzinfo)
        if normalized.replace(tzinfo=None) != candidate.replace(tzinfo=None):
            continue
        if candidate.timestamp() > from_dt.timestamp():
            return candidate
    raise CronError("无法计算下次有效调度时间")


def parse_iso(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


def to_iso(value: dt.datetime) -> str:
    return value.astimezone().isoformat(timespec="seconds")
