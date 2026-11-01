"""费用额度的**阶梯告警**（P5-c，抄 surfcore `token-budget.js` 的判定逻辑）。

抄的只有两件事：

1. **80% 提醒 / 100% 拦截**的阶梯（参考实现 `ALERT_RATIO = 0.8`）；
2. **每天只提醒一次，且落盘**——参考实现的 `takeAlert` 注释写得很直白：
   "这里在**返回时**就落盘，而不是等用户点'知道了'：用户关掉页面/DSH 重启
   也不能让同一个提醒弹第二遍。"

第三条纪律是它最值得抄的地方：**提醒权要落盘**。网页、QQ、心跳是不同进程，
各有一份内存；进程重启内存也没了。只在内存里记"今天提醒过了"，结果就是
用户在 QQ 上被提醒一次、在网页上又被提醒一次、重启后再来一次。

**为什么不抄它的形态**：

- 它把提醒写进独立 JSON 文件（`token-alert.json`）——我们是 SQLite，
  同一份 `state.db` 里已经有额度账本，提醒标记跟着放最自然；
- 它的提醒走设置页弹窗——我们没有设置页，提醒直接落在**回答末尾**，
  用户在哪个通道说话就在哪个通道看到；
- 它的口径名写死（qq/bili/other）——我们的口径是通道，开放集合。

**职责边界**：本模块只负责"该不该提醒、提醒说什么"，
不负责发消息（那是调用方的事），也不碰模型调用。
"""
from __future__ import annotations

from ..llm.tiers import (
    TIER_BLOCK,
    TIER_WARN,
    ScopeUsage,
    collect_tiers,
    normalize_scope,
    tier_message,
)

# 提醒的落盘档位。warn = 到 80%；block = 到 100%（已经拦了，提示用）。
LEVEL_WARN = "warn"
LEVEL_BLOCK = "block"


class ScopeAlertTaker:
    """领取提醒权的门面：问账本"今天这个口径提醒过没有"。

    为什么是门面而不是直接调账本：`llm` 层不许 import `memory`
    （见 `tests/test_arch.py`），而告警逻辑住在 `core/` 里，
    通过 Protocol 注入账本最干净。
    """

    def __init__(self, ledger, *, clock=None) -> None:
        self._ledger = ledger
        self._clock = clock

    def _today(self) -> str:
        if self._clock is not None:
            return str(self._clock())
        import datetime as dt

        return dt.date.today().isoformat()

    def take(self, *, scope: str, level: str = LEVEL_WARN) -> bool:
        """领取提醒权。True = 你是今天第一次（该提醒）；False = 已提醒过。"""
        try:
            return bool(
                self._ledger.take_scope_alert(
                    day=self._today(), scope=normalize_scope(scope), level=level
                )
            )
        except Exception:  # noqa: BLE001 —— 提醒失败不该阻断对话
            # 拿不到提醒权时**不提醒**：宁可漏一次，也不要在每个进程里各弹一遍。
            return False


def scope_warning(
    usages: list[ScopeUsage], *, taker: ScopeAlertTaker | None = None
) -> str:
    """给回答追加的额度提示。空串 = 这次不用提示。

    流程与参考实现一致：

    1. 找出到线的口径（`collect_tiers`）；
    2. **只有今天还没提醒过才提醒**（`taker.take`）——落盘判定，不在内存里；
    3. 提醒内容说清楚"哪个口径、用了多少、上限多少"。

    为什么按**口径**分别领取提醒权而不是全局一次：口径之间是并列的，
    QQ 到 80% 提醒了不代表网页端也到 80%，全局一次会让后者永远收不到提醒。
    """
    hits = collect_tiers(usages)
    if not hits:
        return ""
    warn = [u for u in hits if u.tier == TIER_WARN]
    if not warn:
        # 只有到拦截线的（已经拦住了）由拦截路径自己提示，这里不重复。
        return ""
    if taker is not None:
        fresh = [u for u in warn if taker.take(scope=u.scope, level=LEVEL_WARN)]
        if not fresh:
            return ""
        warn = fresh
    return "（额度提醒：" + tier_message(warn) + "）"


def scope_block_note(usages: list[ScopeUsage]) -> str:
    """到拦截线时的说明（附在拦截提示后面）。"""
    block = [u for u in usages if u.tier == TIER_BLOCK]
    if not block:
        return ""
    return "已达上限：" + tier_message(block)
