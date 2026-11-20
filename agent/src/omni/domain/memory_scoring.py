"""Pure memory-importance scoring heuristic (owner/private/emotion/promise/conflict/self/length); no I/O."""

from __future__ import annotations

import re
from typing import TypedDict

EMOTION_STRONG_RE = re.compile(
    "喜欢|想念|想你|舍不得|难过|委屈|哭|心疼|感动|害怕|孤单|抱歉|对不起|谢谢|抱抱|亲亲|陪伴|永远|最爱|在乎|重要"
)
EMOTION_MEDIUM_RE = re.compile("开心|高兴|笑死|可爱|温柔|生气|烦|郁闷|害羞|讨厌|夸|凶|累|困|饿|紧张|激动|委屈")
FIRST_RE = re.compile("第一次|初次|头一次|最早|最开始|初次见面|认识你|刚认识|纪念日|第一天")
PROMISE_RE = re.compile("答应|约定|说好|以后|下次|记得|不要忘|别忘了|发誓|保证|我会")
CONFLICT_RE = re.compile("吵架|生气|道歉|对不起|和好|误会|不理你|冷战|凶你|原谅")
SELF_RE = re.compile(r"我(?:喜欢|讨厌|想要|是|会|在|叫)|我的|wo(?:喜欢|讨厌|想要|是|会)", re.IGNORECASE)
EXCLAIM_RE = re.compile("[!！]")

SOURCE_PLATFORM = {
    "qq-owner": "qq",
    "qq-group": "qq",
    "qq-private": "qq",
    "qq-self": "qq",
    "pet-owner": "pet",
    "pet-self": "pet",
    "bili-video": "bili",
    "bili-dynamic": "bili",
    "bili-comment": "bili",
    "bili-diary": "bili",
    "manual": "shared",
    "system": "shared",
}

BASE_IMPORTANCE = 8
EMOTION_CAP = 24
MAX_TAGS = 12
BILI_SCORE_CAP = 18


class MemoryScore(TypedDict):
    importance: int
    pinned: bool
    reasons: list[str]
    emotional: int
    kind: str
    tags: list[str]
    scope: str
    platform: str


def detect_emotion_score(text: str) -> int:
    strong = len(EMOTION_STRONG_RE.findall(text))
    medium = len(EMOTION_MEDIUM_RE.findall(text))
    exclaim = 1 if EXCLAIM_RE.search(text) else 0
    return min(EMOTION_CAP, strong * 8 + medium * 3 + exclaim)


def score_memory(
    text: str,
    *,
    source: str = "system",
    is_owner: bool = False,
    is_private: bool = False,
    direct: bool = False,
    is_first: bool = False,
    score: int = 0,
    favorite: bool = False,
    anecdote: bool = False,
    manual_importance: int | None = None,
    pinned: bool = False,
    tags: tuple[str, ...] = (),
    scope: str | None = None,
) -> MemoryScore:
    emotional = detect_emotion_score(text)
    reasons: list[str] = []
    importance = BASE_IMPORTANCE
    if is_owner:
        importance += 42 if is_private else 32
        reasons.append("主人私聊" if is_private else "主人在群里说话")
    if direct:
        importance += 8
        reasons.append("直接对你说的")
    if emotional > 0:
        importance += emotional
        reasons.append(f"情绪浓度 {emotional}")
    if is_first:
        importance += 30
        reasons.append("第一次 / 最早")
    if PROMISE_RE.search(text):
        importance += 16
        reasons.append("约定 / 承诺")
    if CONFLICT_RE.search(text):
        importance += 14
        reasons.append("矛盾 / 道歉 / 和好")
    if SELF_RE.search(text):
        importance += 5
        reasons.append("关于自己")
    bili = source.startswith("bili")
    if bili:
        bonus = max(0, min(BILI_SCORE_CAP, score))
        if bonus:
            importance += bonus
            reasons.append(f"B站打分 {bonus}")
        if favorite:
            importance += 8
            reasons.append("收藏了")
        if anecdote:
            importance += 4
            reasons.append("日记里写过")
    if len(text) < 6:
        importance -= 12
        reasons.append("太短 / 信息量低")
    if manual_importance is not None:
        importance = manual_importance
        reasons.append("手动设定")

    if is_first:
        kind = "first"
    elif is_owner:
        kind = "owner"
    elif emotional >= 14:
        kind = "emotion"
    elif CONFLICT_RE.search(text):
        kind = "conflict"
    elif PROMISE_RE.search(text):
        kind = "promise"
    else:
        kind = "event"

    pinned_out = pinned or (is_first and (is_owner or importance >= 60))
    if is_first and is_owner:
        importance = 100
        pinned_out = True
    importance = int(round(max(1, min(100, importance))))

    ordered = list(dict.fromkeys(tags))
    for tag, condition in (
        ("owner", is_owner),
        ("emotion", emotional >= 8),
        ("first", is_first),
        ("bili", bili),
        ("qq", source.startswith("qq")),
    ):
        if condition and tag not in ordered:
            ordered.append(tag)
    if scope is not None:
        scope_out = scope
    elif source.startswith("qq"):
        scope_out = "qq"
    elif bili:
        scope_out = "bili"
    else:
        scope_out = "shared"
    return {
        "importance": importance,
        "pinned": pinned_out,
        "reasons": reasons,
        "emotional": emotional,
        "kind": kind,
        "tags": ordered[:MAX_TAGS],
        "scope": scope_out,
        "platform": SOURCE_PLATFORM.get(source, "shared"),
    }
