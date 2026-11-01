"""费用额度的**口径化判定层**（P5-c，抄 surfcore `token-budget.js` 的判定逻辑）。

抄的是三条**纯逻辑**，不是它的形态：

1. **口径（scope）**：参考实现把用量分成 `qq / bili / other / total` 四份，
   各自独立比额度，谁先到 80% 谁提醒、谁先到 100% 谁拦截。
   我们**不抄这组枚举**——那组名字是它自己的业务面。我们系统的自然口径是
   **通道**（`cli / web / qq / cron`，取自 `RunIdentity.channel`）：
   用户真正想控制的是"QQ 上别烧太多"，而不是"bili 这个业务面"。
   口径值来自运行时，所以这里是**开放集合** + 一个保留的 `total` 汇总口径。
2. **阶梯**：80% 提醒 / 100% 拦截（参考实现 `ALERT_RATIO = 0.8`）。
   它另有一套 80/90/100 三档，那是**记忆卡容量**告警的设定，与费用口径无关，不抄。
3. **每天只提醒一次且落盘**：参考实现的 `takeAlert` 注释写得很直白——
   "这里在**返回时**就落盘，而不是等用户点'知道了'：用户关掉页面/DSH 重启
   也不能让同一个提醒弹第二遍"。这条纪律我们照抄：**提醒状态存盘，不存内存**。

本模块只做**纯判定**，不碰 SQLite、不碰配置、不发消息：
- 判定层要在没有账本的场合也能跑（单元测试、嵌入式用法）；
- 落盘、读取、送达都在 `memory/usage.py` 与调用方。

三条不变式：

- **未知不是 0**：额度没配（`limit<=0`）= 不限，**不是**"已用 0%"。
  比例算不出来时返回 `None`，调用方不得把它当 0% 渲染成"还很宽裕"。
- **判定与处置分离**：本模块只说"到了哪一档"，不说"该怎么办"。
  提醒还是拦截、提醒给谁，由调用方决定。
- **口径是开放集合**：只有 `total` 是保留字；其余口径名原样透传，
  由调用方归一（见 `normalize_scope`）。
"""
from __future__ import annotations

from dataclasses import dataclass

# 提醒阈值：到 80% 提醒一次，到 100% 拦截。
# 抄参考实现的 `ALERT_RATIO = 0.8`。
ALERT_RATIO = 0.8

# 保留口径：所有口径的汇总。刻意不用 "total" 作普通口径名，
# 因为 "total" 在报表里天然指汇总，普通口径撞名会让读表的人误判。
TOTAL_SCOPE = "total"

# 档位
TIER_OK = "ok"          # 未到提醒线
TIER_WARN = "warn"      # 到了提醒线（≥80%），该提醒了
TIER_BLOCK = "block"    # 到 100%，该拦了


@dataclass(frozen=True)
class ScopeUsage:
    """一个口径的用量与额度。`limit_micro<=0` 表示不限。

    `used_micro` 是"已预留 + 已花费"（与账本的记账口径一致），
    不是只有已花费——在途的预留也占钱。
    """

    scope: str
    used_micro: int
    limit_micro: int

    @property
    def limited(self) -> bool:
        return self.limit_micro > 0

    @property
    def ratio(self) -> float | None:
        """已用比例。**不限时返回 None**（未知不是 0）。"""
        if not self.limited:
            return None
        return self.used_micro / self.limit_micro

    @property
    def tier(self) -> str:
        """这一档到了哪儿。

        - 不限 → 永远 `ok`（没额度就没有"到线"这回事）。
        - ≥100% → `block`。
        - ≥80% → `warn`。
        - 其余 → `ok`。
        """
        ratio = self.ratio
        if ratio is None:
            return TIER_OK
        if ratio >= 1.0:
            return TIER_BLOCK
        if ratio >= ALERT_RATIO:
            return TIER_WARN
        return TIER_OK

    @property
    def remaining_micro(self) -> int | None:
        """还能花多少。不限时返回 None（不是 0——"不限"和"用完了"是两回事）。"""
        if not self.limited:
            return None
        return max(0, self.limit_micro - self.used_micro)


def normalize_scope(scope: str) -> str:
    """口径名归一：去空白、小写、空串归到 `other`。

    为什么要有 `other` 兜底而不是空串：空串在报表里看不出是"没有口径"
    还是"漏填了"，会让人以为数据有问题。给它一个明确的名字更诚实。

    `total` 是保留口径，调用方不该拿它当普通口径用——但归一时**不拦截**，
    拦在这里会让调用方拿到一个无法区分的异常，排查更难。
    """
    text = str(scope or "").strip().lower()
    return text or "other"


def collect_tiers(
    usages: list[ScopeUsage], *, alert_ratio: float = ALERT_RATIO
) -> list[ScopeUsage]:
    """挑出"到了提醒线或拦截线"的口径，按严重度排序。

    排序规则：先拦截线后提醒线；同档按比例从高到低；再同则按口径名，
    保证输出稳定（同样的输入永远同样的顺序，便于断言与展示）。

    为什么用 `alert_ratio` 参数而不是硬编码常量：单元测试可以用更极端的
    阈值构造场景，生产走默认的 0.8。
    """
    hits: list[ScopeUsage] = []
    for usage in usages:
        ratio = usage.ratio
        if ratio is None:
            continue  # 不限 = 无档位可判
        if ratio >= 1.0 or ratio >= alert_ratio:
            hits.append(usage)
    order = {TIER_BLOCK: 0, TIER_WARN: 1}
    hits.sort(
        key=lambda u: (
            order.get(u.tier, 2),
            -(u.ratio or 0.0),
            u.scope,
        )
    )
    return hits


def describe_scope(usage: ScopeUsage) -> str:
    """人话描述一个口径的用量（给日志/接口/提醒文案用）。

    不限时只说已用多少，不说比例——没有分母的比例是编出来的。
    """
    scope = normalize_scope(usage.scope)
    used = _money(usage.used_micro)
    if not usage.limited:
        return f"{scope}：已用 {used}（未设上限）"
    ratio = usage.ratio or 0.0
    return (
        f"{scope}：{used} / {_money(usage.limit_micro)}"
        f"（{ratio * 100:.1f}%）"
    )


def describe_tiers(usages: list[ScopeUsage], *, separator: str = "；") -> str:
    """把多个口径拼成一句（参考实现的 `describeHits` 同款）。"""
    return separator.join(describe_scope(u) for u in usages)


def tier_message(usages: list[ScopeUsage], *, separator: str = "；") -> str:
    """给用户看的档位说明：区分"该提醒了"和"该拦了"。

    参考实现的 `blockMessage` 只在拦截时给一句话；我们多一档提醒文案，
    因为**提醒的价值恰恰在于"还没到拦的那一步"**。
    """
    block = [u for u in usages if u.tier == TIER_BLOCK]
    warn = [u for u in usages if u.tier == TIER_WARN]
    parts: list[str] = []
    if block:
        parts.append("已达上限：" + describe_tiers(block, separator=separator))
    if warn:
        parts.append("已用超过八成：" + describe_tiers(warn, separator=separator))
    return separator.join(parts)


def _money(micro: int) -> str:
    """微美元整数 → 美元字符串。金额一律整数微美元（项目纪律）。"""
    value = int(micro) / 1_000_000
    if value == 0:
        return "$0"
    if abs(value) < 0.01:
        return f"${value:.6f}".rstrip("0").rstrip(".")
    return f"${value:.4f}".rstrip("0").rstrip(".")
