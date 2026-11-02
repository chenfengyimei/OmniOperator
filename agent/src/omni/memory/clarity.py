"""事实记忆的清晰度阶梯（纯判定层，抄 surfcore `memory-card.js` 的思路）。

为什么要有这一层：我们已有**遗忘墓碑**（用户明确说"忘掉 X"→ 永久不得复活），
但缺"**没删、只是慢慢变模糊**"这条轴。现实里人不会记得每件事，
只有反复用到的才留得清楚。少了它，事实库只会单向膨胀：三个月前随口一句
和今天刚确认过的事实，在检索里完全等价。

抄的是**判定逻辑**，不是形态（xuexi/NOTES.md §7.3 的纪律）：
- surfcore 用一张 JSON 卡片 + `importance` 分数 + 独立守护进程；
- 我们用 SQLite 一列 `clarity` + 复用既有的心跳整理循环 + 既有 `hits` 强化信号。
  两边的账本、调度、注入管线都不同，强行统一形态只会引入第二套记忆系统。

阶梯语义（`vivid` → `forgotten` 五档，与 surfcore 同序）：

| 档 | 含义 | 检索表现 |
|---|---|---|
| `vivid` | 清楚（新写入或刚被用过） | 正常召回 |
| `clear` | 还记得 | 正常召回 |
| `fuzzy` | 开始模糊 | 仍可召回，排序略微后移 |
| `trace` | 只剩印象 | 仍可召回，明显后移 |
| `forgotten` | 忘了 | 仍可召回，排到末尾并**标注为陈旧** |

**为什么 `forgotten` 也不排除**（与 surfcore 的关键分歧）：
surfcore 是"记忆卡"，`forgotten` 的记录直接从卡片里移除；我们是**可检索语料**，
`docs/MEMORY_SYSTEM.md` 的硬约定是"事实语料负责查得到"。静默丢弃一条还在库里的
事实，会让 `recall` 回答"没有找到相关记忆"——那是**谎报**（库里明明有），
比让它排到末尾并如实说"很久没被提起了"危险得多。
所以我们的阶梯只做两件事：**排序降权** + **陈旧标注**，一行都不删。
真正的删除权仍然只属于用户（墓碑机制 `forget_fact`）。

三条硬约束（抄自 surfcore 的关键设计，缺一不可）：

1. **每天最多降一格**（`apply_daily_decay`）——"渐渐模糊"而不是"一夜消失"。
   一次跳过两格会让用户觉得记忆在乱丢。
2. **复习即回升**（`review_effect`）——被再次提及/召回就升回上一档。
   这条让系统具备"越用越牢"的正反馈，而不是单向衰减。
3. **不删除**——`forgotten` 只是不再主动召回，**行还在**。
   真正的删除权只属于用户（墓碑机制，`forget_fact`）。

外加两条**每日闸门**（我们自己补的，不是抄的）：
- 衰减**当天只跑一次**（`meta.clarity_decay.day`）。整理循环每 5 分钟一轮，
  没有这道闸门，"每天最多降一格"会退化成"每 5 分钟降一格"——五分钟后
  一条老事实就滑到 `forgotten`。参考实现用 `lastDecayDate === today` 挡住
  同一件事（`memory-card.js:1565`），我们换成 meta 表实现（形态不同、闸门同义）。
- 升档**当天只升一次**（`clarity_reviewed_at`）。一轮对话里模型可能连着
  recall 同一批事实多次，若每次都升一格，一条 `forgotten` 记忆会在几分钟内
  被拉回 `vivid`——阶梯就成了"刷出来的"，失去意义。
  两条闸门方向对称：**一天最多降一格、最多升一格**。

与既有机制的关系（**并存不冲突**，各有分工）：
- 墓碑（`tombstones`）：用户明确要求 → 硬删除 + 永不可复活。
- 清晰度阶梯（本模块）：没人要求，只是**很久没被用到** → 变模糊、不再主动召回。
  阶梯**绝不写墓碑**，也**绝不物理删除行**。
- `hits` 强化（既有）：被召回过的记忆在排序里略微前置。
  阶梯决定"还进不进召回候选"，`hits` 决定"候选里排多前"，两者互补。

配置与默认值（`ClarityConfig`）全部是**显式参数**而非全局常量：
调用方（心跳整理循环）负责注入，这样本模块可以完全离线单测，
不需要读配置、不需要碰数据库、不需要起任何服务。
"""

from __future__ import annotations

import datetime as dt

# 清晰度从清楚到忘了的顺序；索引越大越模糊。
CLARITY_ORDER: tuple[str, ...] = ("vivid", "clear", "fuzzy", "trace", "forgotten")

CLARITY_VIVID = "vivid"
CLARITY_CLEAR = "clear"
CLARITY_FUZZY = "fuzzy"
CLARITY_TRACE = "trace"
CLARITY_FORGOTTEN = "forgotten"

# 不在阶梯里的值（脏数据、旧库、模型写坏）一律按"还记得"处理——
# 宁可高估记忆的清楚程度，也不要因为一个坏值把用户的记忆判死。
_DEFAULT_CLARITY = CLARITY_CLEAR


def clarity_index(clarity: str | None) -> int:
    """清晰度 → 阶梯下标；未知值按 `clear`。"""
    try:
        return CLARITY_ORDER.index(str(clarity or ""))
    except ValueError:
        return CLARITY_ORDER.index(_DEFAULT_CLARITY)


def normalize_clarity(clarity: str | None) -> str:
    """把任意输入夹到合法档位（越界或未知 → `clear`）。"""
    value = str(clarity or "")
    return value if value in CLARITY_ORDER else _DEFAULT_CLARITY


class ClarityConfig:
    """阶梯参数。默认值的取法见 `docs/MEMORY_SYSTEM.md` §清晰度阶梯。

    字段含义（全部是"天数"或"分档"，没有隐式单位）：
    - `recent_days`：写入后多少天内算"新鲜"，不参与衰减；
    - `fuzzy_after_days`：超过这个天数且没被复习过，开始变模糊；
    - `trace_after_days`：超过这个天数且没被复习过，只剩印象；
    - `forget_after_days`：超过这个天数且没被复习过，忘了（不再主动召回）；
    - `review_grace_days`：被复习后多少天内不再衰减（复习保护期）。
    """

    __slots__ = (
        "recent_days",
        "fuzzy_after_days",
        "trace_after_days",
        "forget_after_days",
        "review_grace_days",
        "enabled",
    )

    def __init__(
        self,
        *,
        recent_days: int = 7,
        fuzzy_after_days: int = 30,
        trace_after_days: int = 90,
        forget_after_days: int = 180,
        review_grace_days: int = 7,
        enabled: bool = True,
    ) -> None:
        self.recent_days = max(0, int(recent_days))
        self.fuzzy_after_days = max(0, int(fuzzy_after_days))
        self.trace_after_days = max(0, int(trace_after_days))
        self.forget_after_days = max(0, int(forget_after_days))
        self.review_grace_days = max(0, int(review_grace_days))
        self.enabled = bool(enabled)

    def as_dict(self) -> dict[str, object]:
        return {
            "recent_days": self.recent_days,
            "fuzzy_after_days": self.fuzzy_after_days,
            "trace_after_days": self.trace_after_days,
            "forget_after_days": self.forget_after_days,
            "review_grace_days": self.review_grace_days,
            "enabled": self.enabled,
        }


def _parse_day(value: str | None) -> dt.date | None:
    """解析 ISO 时间/日期；解析不了返回 None（按"没有时间信息"处理）。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _days_since(value: str | None, today: dt.date) -> float | None:
    """距今天数；无法解析返回 None（调用方据此**不衰减**，而不是当成无限旧）。"""
    day = _parse_day(value)
    if day is None:
        return None
    return float((today - day).days)


def target_clarity(
    *,
    created_at: str | None,
    last_used: str | None,
    today: dt.date,
    config: ClarityConfig | None = None,
) -> str:
    """按"这条记忆现在该有多清楚"算目标档位（**纯函数**，不读不写任何状态）。

    规则（抄 surfcore `targetClarity` 的骨架，但换成我们的信号）：
    - 从没被用过 → 用写入时间算年龄；
    - 用过 → 用**距上次被用**算年龄（近因比远因更重要：
      一条三个月前写的、但上周刚用过的记忆，应该比上个月写的更清楚）；
    - 复习保护期内一律保持 `vivid`（刚提过就忘，太反直觉）；
    - 年龄分档：`recent` → `clear`，`fuzzy_after` → `fuzzy`，
      `trace_after` → `trace`，`forget_after` → `forgotten`。

    返回值是**目标**而非直接结果——降档要一格一格走，见 `apply_daily_decay`。
    """
    cfg = config or ClarityConfig()
    if not cfg.enabled:
        return CLARITY_VIVID

    used_days = _days_since(last_used, today)
    if used_days is not None:
        # 复习保护期：刚被用过，保持清楚
        if used_days <= cfg.review_grace_days:
            return CLARITY_VIVID
        age = used_days
    else:
        age = _days_since(created_at, today)
        if age is None:
            # 连写入时间都没有 → 不做任何判断，保持原样（不降档）
            return CLARITY_VIVID
        if age <= cfg.recent_days:
            return CLARITY_VIVID

    if age >= cfg.forget_after_days:
        return CLARITY_FORGOTTEN
    if age >= cfg.trace_after_days:
        return CLARITY_TRACE
    if age >= cfg.fuzzy_after_days:
        return CLARITY_FUZZY
    return CLARITY_CLEAR


def apply_daily_decay(
    current: str | None,
    *,
    created_at: str | None,
    last_used: str | None,
    today: dt.date,
    config: ClarityConfig | None = None,
) -> str:
    """每日衰减：**最多降一格**，永不跳格（纯函数）。

    为什么限制一格：一次从 `clear` 跳到 `forgotten` 等于"一夜失忆"，
    用户会以为系统乱丢东西。surfcore 的 `applyDailyDecay` 用同一手法
    （`currentIndex + 1`，不直接跳到 target）。

    升档**不在这里做**——升档由 `review_effect` 单独触发（复习效应），
    这样"衰减"与"复习"两条语义互不干扰，各自可单测。
    """
    cfg = config or ClarityConfig()
    if not cfg.enabled:
        return normalize_clarity(current)
    target = target_clarity(
        created_at=created_at,
        last_used=last_used,
        today=today,
        config=cfg,
    )
    current_idx = clarity_index(current)
    target_idx = clarity_index(target)
    if target_idx <= current_idx:
        return normalize_clarity(current)  # 目标更清楚或一样 → 不动（升档走复习）
    return CLARITY_ORDER[current_idx + 1]


def review_effect(
    current: str | None,
    *,
    now: str | None = None,
) -> str:
    """复习效应：被再次提及/召回后升一档（**纯函数**，最多升到 `vivid`）。

    这是"越用越牢"的正反馈。没有它，阶梯就是单向衰减的系统——
    用得越多反而掉得越狠，会逼用户不敢用记忆功能。

    `now` 只是记录用的时间戳（本函数不依赖它判定），保留参数是为了让调用方
    把"这次复习发生在什么时候"与"升档"绑定成一个原子操作。
    """
    del now  # 升档与否只看当前档位，不看时间
    current_idx = clarity_index(current)
    if current_idx == 0:
        return CLARITY_ORDER[0]
    return CLARITY_ORDER[current_idx - 1]


def is_recallable(clarity: str | None) -> bool:
    """这条记忆是否**主动参与召回**。

    当前实现恒为 True——阶梯只降权不排除（见模块头"为什么 forgotten 也不排除"）。
    保留这个函数是为了给**将来**可能的"默认不召回陈旧记忆"留一个显式开关点，
    而不是现在就把排除逻辑散落进检索链各处。
    """
    del clarity
    return True
