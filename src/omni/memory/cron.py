"""定时任务存储（data/cron_jobs.json）：Job 模型 + JobStore + 调度时间计算。

设计出处：Hermes cron 的最小版（xuexi/NOTES.md §1.6）——
- 文件持久化 + 原子写（tmp + os.replace）：工具进程写、心跳进程读，进程间共享；
- 至多一次语义由 core/cron.py 的调度器执行（先推进 next_run_at 再派发）；
- 调度方式二选一：every_minutes（周期）或 daily_at（每日 HH:MM，本地时区）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import DATA_DIR

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
    enabled: bool = True
    next_run_at: str | None = None     # ISO 本地时间
    last_run_at: str | None = None
    last_result: str | None = None

    def validate(self) -> None:
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


def compute_next_run(job: Job, from_dt: dt.datetime) -> dt.datetime:
    """下一次运行时刻（本地时区）。"""
    from_dt = from_dt.astimezone()
    if job.every_minutes is not None:
        return from_dt + dt.timedelta(minutes=int(job.every_minutes))
    match = DAILY_RE.match(job.daily_at or "")
    if not match:
        raise CronError(f"daily_at 非法: {job.daily_at}")
    hour, minute = int(match.group(1)), int(match.group(2))
    candidate = from_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= from_dt:
        candidate += dt.timedelta(days=1)
    return candidate


def parse_iso(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


def to_iso(value: dt.datetime) -> str:
    return value.astimezone().isoformat(timespec="seconds")


class JobStore:
    _lock = threading.Lock()  # 类级锁：工具进程与调度器并发读写同一文件

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (DATA_DIR / "cron_jobs.json")

    def list(self) -> list[Job]:
        return [Job(**raw) for raw in self._load()]

    def get(self, name: str) -> Job | None:
        for raw in self._load():
            if raw.get("name") == name:
                return Job(**raw)
        return None

    def upsert(self, job: Job, *, validate: bool = True) -> None:
        if validate:
            job.validate()
        with self._lock:
            jobs = [raw for raw in self._load() if raw.get("name") != job.name]
            jobs.append(asdict(job))
            self._save(jobs)

    def remove(self, name: str) -> bool:
        with self._lock:
            jobs = self._load()
            remaining = [raw for raw in jobs if raw.get("name") != name]
            if len(remaining) == len(jobs):
                return False
            self._save(remaining)
            return True

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _save(self, jobs: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self._path)  # 原子替换：读者永远看到完整文件
