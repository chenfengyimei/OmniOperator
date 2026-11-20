"""Pure memory decay/consolidation rules: retention scoring, clarity steps and catch-up; no I/O."""

from __future__ import annotations

from datetime import date

CLARITY_ORDER = ("vivid", "clear", "fuzzy", "trace", "forgotten")
RECENT_DAYS = 7
FUZZY_AFTER_DAYS = 7
FORGET_AFTER_DAYS = 60
MIN_OWNER_IMPORTANCE = 65
CATCHUP_MAX_DAYS = 30
RECALL_CAP = 999
TOUCH_LIMIT = 5
DEFAULT_IMPORTANCE = 50
DECAY_DAILY_HOUR = 4
DECAY_TICK_LIMIT = 8


def is_protected(importance: int, pinned: bool) -> bool:
    return pinned or importance >= MIN_OWNER_IMPORTANCE


def initial_clarity(importance: int, pinned: bool) -> str:
    return "vivid" if pinned or importance >= MIN_OWNER_IMPORTANCE else "clear"


def retention_score(
    importance: int, recall_count: int, age_days: float, since_seen_days: float, protected: bool
) -> float:
    rate = 0.12 if protected else 0.35
    score = (
        importance
        + min(12, recall_count * 2)
        + (18 if age_days <= RECENT_DAYS else 0)
        + (8 if since_seen_days <= 2 else 0)
        - min(80, max(0.0, age_days - FUZZY_AFTER_DAYS) * rate)
    )
    if protected:
        score = max(score, 55)
    if age_days <= RECENT_DAYS:
        score = max(score, 50)
    return score


def target_clarity(retention: float, pinned: bool) -> str:
    if pinned:
        return "vivid"
    if retention >= 68:
        return "vivid"
    if retention >= 46:
        return "clear"
    if retention >= 24:
        return "fuzzy"
    if retention >= 12:
        return "trace"
    return "forgotten"


def hard_forget(age_days: float, importance: int, protected: bool) -> bool:
    return age_days > FORGET_AFTER_DAYS and importance < 50 and not protected


def decay_step(current: str, target: str) -> str:
    if current not in CLARITY_ORDER or target not in CLARITY_ORDER:
        raise ValueError("Unknown clarity level")
    now, goal = CLARITY_ORDER.index(current), CLARITY_ORDER.index(target)
    if goal > now:
        return CLARITY_ORDER[now + 1]
    if goal < now:
        return CLARITY_ORDER[now - 1]
    return current


def catchup_steps(last_decay_date: str | None, today: str) -> int:
    if not last_decay_date:
        return 1
    days = (date.fromisoformat(today) - date.fromisoformat(last_decay_date)).days
    return max(1, min(CATCHUP_MAX_DAYS, days))


def should_auto_decay(last_decay_date: str | None, today: str, hour: int, daily_hour: int) -> bool:
    return hour >= daily_hour and last_decay_date != today
